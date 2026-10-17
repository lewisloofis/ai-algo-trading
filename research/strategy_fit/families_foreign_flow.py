"""
strategy_fit/families_foreign_flow.py -- plan 22 (foreign-selling rebound), Phase 2 DEVELOPMENT test.

FROZEN by plan 22 section 9 (signed 2026-10-17, incl. the data rule). Only the signal masks are new;
costs, always-long null, eligibility and gate arithmetic are the plan 21 / harness code, unchanged
(``families_idx_structural.build_event_trades``, ``families_index_rebalance`` helpers).

Signals (all use only data known after the close of day t):
  F_t  = net_foreign_t / median(value traded over t-20..t-1)
  S1   = member on t, F_t <= own trailing 5th pct AND close-to-close return on t <= -2%
  S2   = >=35 of 40 members valid on t, market sum of net_foreign <= its trailing 5th pct; buy the
         5 lowest-F valid members (ties: larger value traded first)
  C1   = mirror (F_t >= own trailing 95th pct); reported twice: F-only (C1a) and with ret >= +2% (C1b)

Data rule (section 9 row 2): a name-day that is missing, or has buy+sell > 2 x chartbit value, is *no
data*: never triggers, left out of the percentile history. Percentile = ``np.percentile`` (linear
interpolation) over the previous 250 VALID observations strictly before t, >= 120 required, trigger when
value <= (>= for C1) that percentile.

DEVELOPMENT WINDOW ONLY: signals 2017-01-01..2022-12-31. ``guard_holdout`` raises on any signal dated
>= 2023-01-01 unless research/results/foreign_flow/HOLDOUT_AUTHORIZED exists. There is no holdout CLI.

    cd research && .venv/bin/python -m strategy_fit.families_foreign_flow --dev
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from foreign_flow import DATA_DIR, UNIVERSE_CSV
from index_events import event_time as et
from strategy_fit.entry_convention import ENTRY_NEXT_CLOSE, ENTRY_NEXT_OPEN
from strategy_fit.families_idx_structural import Panel, build_event_trades, build_panel, locked_up_entry
from strategy_fit.families_index_rebalance import (
    BPS, CAPACITY_MULT, Env, HOLM_ALPHA, NOTIONAL_IDR, T_MIN, _cost, _degenerate, _fit_impact, _mean,
    _one_lot_cost, cell_metrics, classify_harness_exclusions, event_mask, gates_for, holm_adjust, load_ihsg,
    make_env, regime_split)
from strategy_fit.shortlist_2026_10 import FITS, norm_sf

RESEARCH = Path(__file__).resolve().parents[1]
OUT_DIR = RESEARCH / "results" / "foreign_flow"
HOLDOUT_FLAG = OUT_DIR / "HOLDOUT_AUTHORIZED"

DEV_START = pd.Timestamp("2017-01-01")
DEV_END = pd.Timestamp("2022-12-31")
HOLDOUT_START = pd.Timestamp("2023-01-01")
EXCL_2020 = (pd.Timestamp("2020-02-01"), pd.Timestamp("2020-12-31"))

HOLDS = (1, 3, 5)
SIGNALS = ("S1", "S2")
WINDOW = 250
MIN_HIST = 120
VALUE_WINDOW = 20
VALUE_BOUND_FACTOR = 2.0
LOW_Q, HIGH_Q = 5.0, 95.0
RET_THRESHOLD = 0.02
BAD_BAR_ABS_RETURN = 0.50
MIN_VALID_MEMBERS = 35
S2_TOP_N = 5
DROP_DATES = 5
MIN_TRADES = 100
MIN_DATES = 30


class HoldoutError(ValueError):
    """A signal dated >= 2023-01-01 reached the analysis without HOLDOUT_AUTHORIZED."""


# ---------------------------------------------------------------------------
# Signal construction (pure functions of DataFrames: date x symbol)
# ---------------------------------------------------------------------------

def trailing_percentile(x: np.ndarray, q: float, window: int = WINDOW, min_hist: int = MIN_HIST) -> np.ndarray:
    """out[i] = q-th percentile (linear) of the previous <= ``window`` entries x[:i]; NaN while i < min_hist.
    ``x`` must already be the VALID observations only."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(min_hist, min(window, n)):
        out[i] = np.percentile(x[:i], q)
    if n > window:
        out[window:] = np.percentile(sliding_window_view(x, window)[: n - window], q, axis=1)
    return out


@dataclass
class Signals:
    F: pd.DataFrame
    valid: pd.DataFrame
    s1: pd.DataFrame
    s2: pd.DataFrame
    c1a: pd.DataFrame
    c1b: pd.DataFrame
    s2_day: pd.Series            # market-wide S2 trigger per date
    diag: dict


def flow_ratio(net_foreign: pd.DataFrame, value: pd.DataFrame, window: int = VALUE_WINDOW) -> pd.DataFrame:
    """F_t = net_foreign_t / median(value over t-window..t-1) (today's value is NOT in the denominator)."""
    med = value.shift(1).rolling(window, min_periods=window).median()
    return net_foreign / med.where(med > 0)


def valid_mask(net_foreign: pd.DataFrame, buy_plus_sell: pd.DataFrame, value: pd.DataFrame,
               F: pd.DataFrame) -> pd.DataFrame:
    """Data rule: row present, finite F, chartbit value known and buy+sell <= 2 x value."""
    ok = (net_foreign.notna() & buy_plus_sell.notna() & value.notna() & (value > 0)
          & (buy_plus_sell <= VALUE_BOUND_FACTOR * value) & np.isfinite(F))
    return ok.fillna(False).astype(bool)


def _name_triggers(F: pd.DataFrame, valid: pd.DataFrame, q: float, low: bool, window: int, min_hist: int
                   ) -> pd.DataFrame:
    """Per name: F_t beyond its own trailing percentile of prior VALID F; invalid days never trigger."""
    out = np.zeros(F.shape, dtype=bool)
    Fv_all, v_all = F.to_numpy(float), valid.to_numpy(bool)
    for j in range(F.shape[1]):
        idx = np.flatnonzero(v_all[:, j])
        if len(idx) <= min_hist:
            continue
        x = Fv_all[idx, j]
        thr = trailing_percentile(x, q, window, min_hist)
        with np.errstate(invalid="ignore"):
            hit = (x <= thr) if low else (x >= thr)
        out[idx, j] = hit & np.isfinite(thr)
    return pd.DataFrame(out, index=F.index, columns=F.columns)


def build_signals(close: pd.DataFrame, value: pd.DataFrame, net_foreign: pd.DataFrame,
                  buy_plus_sell: pd.DataFrame, member: pd.DataFrame, *, window: int = WINDOW,
                  min_hist: int = MIN_HIST, min_valid_members: int = MIN_VALID_MEMBERS,
                  top_n: int = S2_TOP_N) -> Signals:
    """All masks on the frames' own date axis. Every value at row t uses rows <= t only."""
    member = member.reindex_like(close).fillna(False).astype(bool)
    F = flow_ratio(net_foreign, value)
    valid = valid_mask(net_foreign, buy_plus_sell, value, F)
    ret = close.pct_change(fill_method=None)
    sane = ret.abs() <= BAD_BAR_ABS_RETURN
    low = _name_triggers(F, valid, LOW_Q, True, window, min_hist)
    high = _name_triggers(F, valid, HIGH_Q, False, window, min_hist)
    s1 = low & member & sane & (ret <= -RET_THRESHOLD)
    c1a = high & member
    c1b = high & member & sane & (ret >= RET_THRESHOLD)

    mv = valid & member
    n_valid = mv.sum(axis=1)
    qualifies = (n_valid >= min_valid_members).to_numpy()
    msum = net_foreign.where(mv).sum(axis=1).to_numpy(float)
    q_idx = np.flatnonzero(qualifies)
    thr = np.full(len(close), np.nan)
    if len(q_idx):
        thr[q_idx] = trailing_percentile(msum[q_idx], LOW_Q, window, min_hist)
    with np.errstate(invalid="ignore"):
        day_hit = qualifies & np.isfinite(thr) & (msum <= thr)
    s2 = np.zeros(close.shape, dtype=bool)
    Fa, Va, mva = F.to_numpy(float), value.to_numpy(float), mv.to_numpy(bool)
    for i in np.flatnonzero(day_hit):
        cols = np.flatnonzero(mva[i])
        order = np.lexsort((-np.nan_to_num(Va[i, cols]), Fa[i, cols]))   # F asc, then value desc
        s2[i, cols[order[:top_n]]] = True
    diag = {"s2_qualifying_sessions": int(qualifies.sum()),
            "s2_sessions_below_min_valid": int(((n_valid > 0) & ~qualifies).sum()),
            "s2_trigger_days": int(day_hit.sum())}
    return Signals(F, valid, s1, pd.DataFrame(s2, index=close.index, columns=close.columns), c1a, c1b,
                   pd.Series(day_hit, index=close.index), diag)


def restrict_dates(mask: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    keep = (mask.index >= start) & (mask.index <= end)
    return mask.where(pd.Series(keep, index=mask.index), False, axis=0).astype(bool)


def holdout_authorized(flag: Path | None = None) -> bool:
    return Path(HOLDOUT_FLAG if flag is None else flag).exists()


def guard_holdout(mask: pd.DataFrame, flag: Path | None = None) -> None:
    """Refuse any signal dated >= 2023-01-01 unless the authorization file exists."""
    late = mask.index >= HOLDOUT_START
    if late.any() and bool(mask.loc[late].to_numpy().any()) and not holdout_authorized(flag):
        raise HoldoutError(f"signal dated >= {HOLDOUT_START.date()} refused: {HOLDOUT_FLAG} does not exist")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_universe_membership(dates: pd.DatetimeIndex, symbols: list[str], path: Path = UNIVERSE_CSV
                             ) -> pd.DataFrame:
    """Member on session d = in the top 40 of the latest month-end strictly before d."""
    uni = pd.read_csv(path)
    uni["month_end"] = pd.to_datetime(uni["month_end"])
    ends = np.array(sorted(uni["month_end"].unique()), dtype="datetime64[ns]")
    col = {s: i for i, s in enumerate(symbols)}
    by_end = {e: [col[s] for s in g["symbol"] if s in col] for e, g in uni.groupby("month_end")}
    m = np.zeros((len(dates), len(symbols)), dtype=bool)
    pos = np.searchsorted(ends, dates.to_numpy("datetime64[ns]"), side="left") - 1   # latest end < d
    for i, p in enumerate(pos):
        if p >= 0:
            m[i, by_end[pd.Timestamp(ends[p])]] = True
    return pd.DataFrame(m, index=dates, columns=symbols)


def load_foreign_wide(dates: pd.DatetimeIndex, symbols: list[str], data_dir: Path = DATA_DIR
                      ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(net_foreign, buy_plus_sell) aligned to the panel; NaN where there is no foreign row."""
    net, gross = {}, {}
    for s in symbols:
        p = Path(data_dir) / f"{s}.parquet"
        if not p.exists():
            continue
        f = pd.read_parquet(p, columns=["date", "net_foreign", "foreign_buy", "foreign_sell"])
        f.index = pd.DatetimeIndex(pd.to_datetime(f["date"]))
        f = f[~f.index.duplicated(keep="last")]
        net[s] = f["net_foreign"].astype(float)
        gross[s] = (f["foreign_buy"] + f["foreign_sell"]).astype(float)
    cols = list(net)
    nf = pd.DataFrame(net).reindex(index=dates, columns=symbols)
    bs = pd.DataFrame(gross).reindex(index=dates, columns=symbols)
    return nf, bs


def load_ihsg_open(dates: pd.DatetimeIndex) -> np.ndarray:
    df = pd.read_parquet(RESEARCH / "data" / "daily" / "IHSG.parquet", columns=["date", "open"])
    s = pd.Series(pd.to_numeric(df["open"], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(df["date"].astype(str))))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.where(s > 0).reindex(dates).to_numpy(float)


def load_env() -> Env:
    return make_env(build_panel(et.load_hist()), load_ihsg())


def dev_signals(env: Env, data_dir: Path = DATA_DIR, universe_csv: Path = UNIVERSE_CSV) -> Signals:
    """Signals from data dated <= 2022-12-31 only (later rows are never read), masks cut to the dev window."""
    panel = env.panel
    keep = panel.dates <= DEV_END
    dates = panel.dates[keep]
    syms = panel.symbols
    nf, bs = load_foreign_wide(dates, syms, data_dir)
    member = load_universe_membership(dates, syms, universe_csv)
    sig = build_signals(panel.close.loc[keep], panel.value_idr.loc[keep], nf, bs, member)
    return sig


def full_mask(panel: Panel, mask: pd.DataFrame) -> pd.DataFrame:
    """Embed a dev-truncated mask into the full panel calendar (False after 2022-12-31) and cut to the window."""
    m = mask.reindex(index=panel.dates, columns=panel.symbols).fillna(False).astype(bool)
    return restrict_dates(m, DEV_START, DEV_END)


# ---------------------------------------------------------------------------
# Evaluation of one mask
# ---------------------------------------------------------------------------

def _prep(mask: pd.DataFrame) -> pd.DataFrame:
    ip, j = np.nonzero(mask.to_numpy(bool))
    return pd.DataFrame({"ip": ip, "j": j, "ticker": [mask.columns[x] for x in j]})


def enrich(env: Env, ihsg_open: np.ndarray, T: pd.DataFrame, k: int, entry: str) -> pd.DataFrame:
    """Cost variants, IHSG return over the identical window, regime flag; cluster label = signal date."""
    T = T.copy()
    T["review_label"] = T["date"].dt.strftime("%Y-%m-%d")
    pos = T["pos"].to_numpy(int)
    close = env.panel.close.to_numpy(float)
    jj = np.array([env.col[s] for s in T["symbol"]])
    T["entry_px"] = close[pos + 1, jj]                 # close is only used for the 1-lot size (as plan 21)
    ih = ihsg_open if entry == ENTRY_NEXT_OPEN else env.ihsg_close
    T["ihsg_ret"] = ih[pos + 1 + k] / ih[pos + 1] - 1.0
    T["below_sma"] = env.ihsg_below_sma[pos]
    deg = _degenerate(T)
    T["degenerate_cost_inputs"] = deg
    T["cost_cur"] = _cost(T, T["impact_bps"].to_numpy(float), deg, False)
    for name in ("cand_latest", "cand_pooled"):
        T[f"cost_{name}"] = _cost(T, _fit_impact(T, FITS[name], NOTIONAL_IDR), deg, False)
    T["cost_1lot"] = _one_lot_cost(T, T["entry_px"].to_numpy(float), deg, False)
    from strategy_fit.families_idx_structural import recost
    T["cost_10x"] = _cost(T, recost(T, NOTIONAL_IDR * CAPACITY_MULT)["impact_bps"].to_numpy(float), deg, False)
    T["gross_bps"] = T["gross"].to_numpy(float) * BPS
    T["null_bps"] = T["null_name"].to_numpy(float) * BPS
    T["edge_bps"] = T["gross_bps"] - T["null_bps"]
    return T


def drop_best_dates(T: pd.DataFrame, n: int = DROP_DATES) -> float:
    """Mean net (edge - cost_cur) over trades after removing every trade on the ``n`` signal dates with the
    highest per-date mean net. NaN when there are <= n dates."""
    if T.empty:
        return float("nan")
    net = T["edge_bps"] - T["cost_cur"]
    per_date = net.groupby(T["review_label"]).mean()
    if len(per_date) <= n:
        return float("nan")
    drop = set(per_date.nlargest(n).index)
    return _mean(net[~T["review_label"].isin(drop)])


def g9_metrics(T: pd.DataFrame) -> dict:
    """Development result without signals dated 2020-02-01..2020-12-31."""
    d = pd.to_datetime(T["date"]) if len(T) else pd.Series([], dtype="datetime64[ns]")
    sub = T[~((d >= EXCL_2020[0]) & (d <= EXCL_2020[1]))] if len(T) else T
    out = {"n": int(len(sub)), "dates": int(sub["review_label"].nunique()) if len(sub) else 0,
           "n_removed": int(len(T) - len(sub))}
    if sub.empty:
        return {**out, "edge": float("nan"), "net": float("nan"), "net_minus_ihsg": float("nan"), "pass": False}
    edge, cost = sub["edge_bps"].to_numpy(float), sub["cost_cur"].to_numpy(float)
    net = _mean(edge - cost)
    nmi = _mean(sub["gross_bps"].to_numpy(float) - cost - sub["ihsg_ret"].to_numpy(float) * BPS)
    e = _mean(edge)
    return {**out, "edge": e, "net": net, "net_minus_ihsg": nmi,
            "pass": bool(e > 0 and net > 0 and np.isfinite(nmi) and nmi > 0)}


def sample_status(n: int, dates: int) -> str:
    return "ok" if n >= MIN_TRADES and dates >= MIN_DATES else "insufficient_n"


def evaluate(env: Env, ihsg_open: np.ndarray, mask: pd.DataFrame, k: int, entry: str,
             flag: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Harness run for one (mask, k, entry) -> (enriched trades, {exclusions, metrics, gates, g9, regime})."""
    guard_holdout(mask, flag)
    prep = _prep(mask)
    cand, ex = classify_harness_exclusions(env, prep, k, entry, env.elig_default)
    ex["signals"] = int(len(prep))
    ex["locked_upper_limit"] = int(locked_up_entry(env.panel).to_numpy()[cand["ip"], cand["j"]].sum()) if len(cand) else 0
    T = build_event_trades(env.panel, event_mask(env.panel, cand), k, entry, eligible=env.elig_default,
                           notional_idr=NOTIONAL_IDR, sequential=True, skip_locked=True)
    ex["trades"] = int(len(T))
    ex["sequential_overlap"] = int(len(cand) - ex["locked_upper_limit"] - len(T))
    if T.empty:
        return T, {"exclusions": ex, "metrics": {"n": 0, "reviews": 0}, "sample": "insufficient_n", "gates": {},
                   "g9": g9_metrics(T), "regime_g8": {}}
    T = enrich(env, ihsg_open, T, k, entry)
    m = cell_metrics(T)
    m["drop5_net_edge_cur"] = drop_best_dates(T)           # 5 best signal DATES (plan G4), not 5 best trades
    g = gates_for(m)
    g9 = g9_metrics(T)
    g["G9"] = g9["pass"]
    return T, {"exclusions": ex, "metrics": m, "sample": sample_status(m["n"], m["reviews"]), "gates": g,
               "g9": g9, "regime_g8": regime_split(T)}


# ---------------------------------------------------------------------------
# Holm, verdicts, section 5
# ---------------------------------------------------------------------------

def cell_p(res: dict) -> float:
    if res.get("status") != "tested":
        return 1.0
    p = norm_sf(res["metrics"].get("t", float("nan")))
    return float(p) if np.isfinite(p) else 1.0


def apply_holm(results: dict) -> dict:
    """G7 over exactly the 6 cells (signal x k); insufficient_n counts as p = 1. Mutates ``results`` entries
    in place only by adding keys (they are this module's own dicts)."""
    keys = [(s, k) for s in SIGNALS for k in HOLDS]
    adj = holm_adjust({key: cell_p(results[key[0]][key[1]]) for key in keys})
    for s, k in keys:
        r = results[s][k]
        r["p_one_sided"] = cell_p(r)
        r["p_holm"] = adj[(s, k)]
        if r["status"] == "tested":
            r["gates"] = {**r["gates"], "G7": bool(np.isfinite(r["p_holm"]) and r["p_holm"] < HOLM_ALPHA)}
    return results


def verdict(res: dict) -> tuple[str, list[str]]:
    if res["status"] != "tested":
        return res["status"], []
    g = res["gates"]
    failed: list[str] = []
    for name in ("G2", "G3", "G4", "G6", "G7", "G9"):
        if g.get(name, False):
            continue
        failed += [x for x in ("G3a_current", "G3b_candidate", "G3c_1lot") if not g[x]] if name == "G3" else [name]
    return ("PASS_DEV" if not failed else "FAIL"), failed


def decision(results: dict, c1: dict) -> dict:
    """Plan section 5, applied mechanically to the 6 cells. ``c1_matches`` = the C1 variant at the same k has
    mean edge > 0 and clustered t >= 2 (operational reading; the plan does not define 'matches')."""
    passed = [(s, k) for s in SIGNALS for k in HOLDS if results[s][k]["verdict"] == "PASS_DEV"]
    only_g9 = [(s, k) for s in SIGNALS for k in HOLDS if results[s][k]["failed_gates"] == ["G9"]]
    if passed:
        matches = {f"{s}_k{k}": {v: bool(c1[v][k]["metrics"].get("n") and c1[v][k]["metrics"]["edge"] > 0
                                         and c1[v][k]["metrics"]["t"] >= T_MIN) for v in c1}
                   for s, k in passed}
        return {"branch": "pass_dev_then_holdout", "cells": [f"{s}_k{k}" for s, k in passed], "c1_matches": matches}
    if only_g9:
        return {"branch": "only_G9_fails_close", "cells": [f"{s}_k{k}" for s, k in only_g9]}
    return {"branch": "nothing_passes_dev_close_holdout_unspent", "cells": []}


# ---------------------------------------------------------------------------
# Whole run
# ---------------------------------------------------------------------------

def _scored(T: pd.DataFrame, info: dict) -> dict:
    status = "tested" if info["sample"] == "ok" else "insufficient_n"
    return {"status": status, **info}


def run_dev(env: Env, sig: Signals, ihsg_open: np.ndarray, flag: Path | None = None) -> dict:
    panel = env.panel
    masks = {"S1": full_mask(panel, sig.s1), "S2": full_mask(panel, sig.s2)}
    c1_masks = {"C1a_F_only": full_mask(panel, sig.c1a), "C1b_F_and_ret": full_mask(panel, sig.c1b)}
    results: dict = {s: {} for s in SIGNALS}
    trades: list[pd.DataFrame] = []
    for s in SIGNALS:
        for k in HOLDS:
            T, info = evaluate(env, ihsg_open, masks[s], k, ENTRY_NEXT_OPEN, flag)
            res = _scored(T, info)
            _, cl = evaluate(env, ihsg_open, masks[s], k, ENTRY_NEXT_CLOSE, flag)
            res["sens_close_entry"] = cl
            results[s][k] = res
            if len(T):
                trades.append(T.assign(signal=s, k=k, entry=ENTRY_NEXT_OPEN))
    apply_holm(results)
    for s in SIGNALS:
        for k in HOLDS:
            r = results[s][k]
            r["verdict"], r["failed_gates"] = verdict(r)
    c1: dict = {v: {} for v in c1_masks}
    for v, mk in c1_masks.items():
        for k in HOLDS:
            T, info = evaluate(env, ihsg_open, mk, k, ENTRY_NEXT_OPEN, flag)
            c1[v][k] = _scored(T, info)
    return {"window": "dev", "signal_window": [str(DEV_START.date()), str(DEV_END.date())],
            "holdout_run": False, "cells": results, "c1": c1, "decision": decision(results, c1),
            "signal_diag": {**sig.diag, **signal_counts(sig)}, "n_cells": 6, "_trades": trades}


def signal_counts(sig: Signals) -> dict:
    dev = lambda m: m.loc[(m.index >= DEV_START) & (m.index <= DEV_END)]  # noqa: E731
    out = {}
    for name in ("s1", "s2", "c1a", "c1b"):
        m = dev(getattr(sig, name))
        out[f"{name}_name_days"] = int(m.to_numpy().sum())
        out[f"{name}_dates"] = int(m.any(axis=1).sum())
    return out


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def jsonable(out: dict) -> dict:
    def conv(by):
        return {c: {str(k): v for k, v in bk.items()} for c, bk in by.items()}
    return {**{k: v for k, v in out.items() if k not in ("_trades", "cells", "c1")},
            "cells": conv(out["cells"]), "c1": conv(out["c1"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dev", action="store_true", required=True,
                    help="plan 22 development window (signals 2017-01-01..2022-12-31). There is no holdout option.")
    ap.parse_args(argv)
    from strategy_fit import families_foreign_flow_report as rep
    env = load_env()
    sig = dev_signals(env)
    out = run_dev(env, sig, load_ihsg_open(env.panel.dates))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "dev.json").write_text(json.dumps(jsonable(out), indent=1, default=_json_default))
    if out["_trades"]:
        pd.concat(out["_trades"], ignore_index=True).to_csv(OUT_DIR / "dev_trades.csv", index=False)
    text = rep.render(out)
    (OUT_DIR / "dev.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
