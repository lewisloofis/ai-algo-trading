"""
strategy_fit/families_index_rebalance.py -- plan 21 H2 (index deletions), gated test.

FROZEN by plan 21 section 9a (signed 2026-10-16). Reads ``events.csv`` and turns the
deletion events (change in {del, migrate_out}) into an event mask on the harness's
signal day s = p (the passive-trade session), so the harness enters at close_{p+1} and
exits at close_{p+1+k}, k in {5, 10, 20}. Cost, null and eligibility code are the
harness's (``families_idx_structural``) and the shortlist helpers
(``shortlist_2026_10``), unchanged.

Cells (G7 Holm denominator, fixed): {MSCI_ST, MSCI_SC, FTSE, IDX, pooled} x k = 15.

DEVELOPMENT WINDOW ONLY by default: non-freeze reviews announced <= 2021-12-31. The
holdout (G5) is behind ``--holdout`` and refuses to run unless
``research/results/index_rebalance/HOLDOUT_AUTHORIZED`` exists. ``guard_dev_events`` raises
if a non-freeze review announced >= 2022-01-01 reaches the dev analysis.

    cd research && .venv/bin/python -m strategy_fit.families_index_rebalance
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from index_events import event_time as et
from index_events.build_events import DAILY_DIR, EVENTS_CSV, RESEARCH
from strategy_fit.entry_convention import ENTRY_NEXT_CLOSE, ENTRY_NEXT_OPEN
from strategy_fit.families_idx_structural import (
    DEGENERATE_IMPACT_BPS, Panel, build_event_trades, build_panel, eligibility, fwd_total_return, recost)
from strategy_fit.shortlist_2026_10 import FITS, cluster_t, drop_top, holm, impact_bps, norm_sf

REPO = RESEARCH.parent
OUT_DIR = RESEARCH / "results/index_rebalance"
HOLDOUT_FLAG = OUT_DIR / "HOLDOUT_AUTHORIZED"
DOC_PATH = REPO / "docs" / "research" / "2026-10-16-index-rebalance-h2-dev.md"

HOLDS = (5, 10, 20)
CELLS = ("MSCI_ST", "MSCI_SC", "FTSE", "IDX", "pooled")
PROVIDER_PRIORITY = {"MSCI_ST": 0, "MSCI_SC": 1, "FTSE": 2, "IDX": 3}
DELETIONS = ("del", "migrate_out")
NOTIONAL_IDR = 3_000_000.0          # harness notional (strategy_fit.impact.POSITION_NOTIONAL_IDR)
CAPACITY_MULT = 10.0
SHARES_PER_LOT = 100
MIN_EVENTS = 30
MIN_REVIEWS = 12
T_MIN = 2.0
HOLM_ALPHA = 0.05
SMA_WINDOW = 200
BPS = 1e4
HOLDOUT_END = pd.Timestamp("2025-12-31")
GATE_FIT = "cand_latest"            # the shortlist's G3 candidate fit (most conservative); cand_pooled reported
EXCL_PRE = ("no_dates", "ticker_unresolved", "no_bars", "suspended_a_to_p", "duplicate_ticker_p")
EXCL_K = ("entry_not_traded", "bad_bars_or_missing", "not_eligible", "sequential_overlap")


# ---------------------------------------------------------------------------
# Window selection and the holdout guard
# ---------------------------------------------------------------------------

def guard_dev_events(ev: pd.DataFrame) -> None:
    """Raise if a non-freeze review announced >= 2022-01-01 is in a development frame."""
    et.guard_holdout(ev)


def guard_dev_sessions(prep: pd.DataFrame, dates: pd.DatetimeIndex) -> None:
    """Dev events must also have their passive-trade session inside the dev window (the harness's
    same-name null averages over the calendar year of the signal day)."""
    if len(prep) and (dates[prep["ip"].to_numpy(dtype=int)] > et.DEV_END).any():
        raise et.HoldoutError("a development event has its passive-trade session after 2021-12-31")


def select_window(ev: pd.DataFrame, window: str) -> pd.DataFrame:
    """'dev': reviews announced <= 2021-12-31, non-freeze. 'holdout': 2022-01-01..2025-12-31, non-freeze."""
    if window == "dev":
        dev, _ = et.split_events(ev)
        guard_dev_events(dev)
        return dev
    if window == "holdout":
        ts = et.announce_ts(ev)
        return ev[(ev["review_type"] != "freeze") & (ts >= et.HOLDOUT_START) & (ts <= HOLDOUT_END)].copy()
    raise ValueError(f"window must be 'dev' or 'holdout', got {window!r}")


def holdout_authorized(flag: Path = HOLDOUT_FLAG) -> bool:
    return Path(flag).exists()


# ---------------------------------------------------------------------------
# Environment (panel + masks + IHSG)
# ---------------------------------------------------------------------------

@dataclass
class Env:
    panel: Panel
    elig_default: pd.DataFrame
    elig_traded: pd.DataFrame
    ihsg_close: np.ndarray            # aligned to panel.dates, NaN where IHSG has no bar
    ihsg_below_sma: np.ndarray        # aligned to panel.dates: 1.0 below / 0.0 at-or-above / NaN unknown
    fwd_cache: dict = field(default_factory=dict)

    @property
    def col(self) -> dict[str, int]:
        return {s: i for i, s in enumerate(self.panel.symbols)}


def load_ihsg(path: Path | None = None) -> pd.Series:
    df = pd.read_parquet(path or DAILY_DIR / "IHSG.parquet", columns=["date", "close"])
    s = pd.Series(pd.to_numeric(df["close"], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(df["date"].astype(str))))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.where(s > 0)


def make_env(panel: Panel, ihsg: pd.Series, elig_default: pd.DataFrame | None = None) -> Env:
    elig = eligibility(panel) if elig_default is None else elig_default
    traded = (panel.volume.fillna(0) > 0)
    sma = ihsg.rolling(SMA_WINDOW, min_periods=SMA_WINDOW).mean()
    below = (ihsg < sma).astype(float).where(sma.notna())
    return Env(panel, elig, traded, ihsg.reindex(panel.dates).to_numpy(float),
               below.reindex(panel.dates).to_numpy(float))


def load_env() -> Env:
    return make_env(build_panel(et.load_hist()), load_ihsg())


# ---------------------------------------------------------------------------
# Events -> mask
# ---------------------------------------------------------------------------

def provider_key(df: pd.DataFrame) -> pd.Series:
    return pd.Series(np.where(df["provider"] == "MSCI", df["index_name"], df["provider"]), index=df.index)


def cell_events(ev: pd.DataFrame, cell: str) -> pd.DataFrame:
    """Deletions of one cell; 'pooled' = every provider (de-duplicated later on (ticker, p))."""
    d = ev[ev["change"].isin(DELETIONS)].copy()
    d["pkey"] = provider_key(d)
    if cell != "pooled":
        d = d[d["pkey"] == cell]
    d["priority"] = d["pkey"].map(PROVIDER_PRIORITY).fillna(99).astype(int)
    return d


def prepare_events(d: pd.DataFrame, env: Env) -> tuple[pd.DataFrame, dict]:
    """Apply the k-independent exclusions. -> (unique tradeable-candidate events, per-reason counts)."""
    panel = env.panel
    dates, col = panel.dates, env.col
    vol = panel.volume.to_numpy(float)
    close = panel.close.to_numpy(float)
    counts = {r: 0 for r in EXCL_PRE}
    rows = []
    for r in d.sort_values(["priority", "review_label", "event_id"]).itertuples(index=False):
        if r.announce_session == "" or r.passive_trade_session == "":
            counts["no_dates"] += 1
        elif not r.ticker or r.cross_check == "mismatch" or r.map_confidence == "low":
            counts["ticker_unresolved"] += 1
        elif r.ticker not in col or not np.isfinite(close[:, col[r.ticker]]).any():
            counts["no_bars"] += 1
        else:
            ia = int(dates.searchsorted(pd.Timestamp(r.announce_session)))
            ip = int(dates.searchsorted(pd.Timestamp(r.passive_trade_session)))
            if dates[ia] != pd.Timestamp(r.announce_session) or dates[ip] != pd.Timestamp(r.passive_trade_session):
                raise ValueError(f"{r.event_id}: session not in panel calendar (re-run index_events.build_events)")
            j = col[r.ticker]
            if et.is_suspended(vol[ia + 1: ip + 1, j]):
                counts["suspended_a_to_p"] += 1
            else:
                rows.append({"event_id": r.event_id, "ticker": r.ticker, "j": j, "ia": ia, "ip": ip,
                             "review_label": r.review_label, "announce_session": r.announce_session,
                             "priority": r.priority})
    cols = ["event_id", "ticker", "j", "ia", "ip", "review_label", "announce_session", "priority"]
    prep = pd.DataFrame(rows, columns=cols)
    uniq = prep.drop_duplicates(["ticker", "ip"], keep="first").reset_index(drop=True)  # priority-sorted already
    counts["duplicate_ticker_p"] = int(len(prep) - len(uniq))
    return uniq, counts


def event_mask(panel: Panel, prep: pd.DataFrame) -> pd.DataFrame:
    """Boolean (dates x symbols) mask with the SIGNAL at s = p, so the harness enters at close_{p+1}."""
    m = np.zeros(panel.close.shape, dtype=bool)
    if len(prep):
        m[prep["ip"].to_numpy(dtype=int), prep["j"].to_numpy(dtype=int)] = True
    return pd.DataFrame(m, index=panel.dates, columns=panel.symbols)


def _fwd(env: Env, k: int, entry: str) -> np.ndarray:
    key = (k, entry)
    if key not in env.fwd_cache:
        env.fwd_cache[key] = fwd_total_return(env.panel, k, entry).to_numpy(float)
    return env.fwd_cache[key]


# ---------------------------------------------------------------------------
# One (cell, k, entry, eligibility) evaluation
# ---------------------------------------------------------------------------

def classify_harness_exclusions(env: Env, prep: pd.DataFrame, k: int, entry: str, eligible: pd.DataFrame
                                ) -> tuple[pd.DataFrame, dict]:
    """Split ``prep`` into harness candidates and the per-reason exclusion counts (order: entry not traded,
    bad bars / missing price, not eligible)."""
    n = len(env.panel.dates)
    vol = env.panel.volume.to_numpy(float)
    fwd = _fwd(env, k, entry)
    elig = eligible.reindex_like(env.panel.close).fillna(False).to_numpy(bool)
    ip, j = prep["ip"].to_numpy(int), prep["j"].to_numpy(int)
    nxt = np.minimum(ip + 1, n - 1)
    entry_ok = (ip + 1 < n) & (np.nan_to_num(vol[nxt, j]) > 0)
    fwd_ok = np.isfinite(fwd[ip, j])
    el = elig[ip, j]
    counts = {"entry_not_traded": int((~entry_ok).sum()),
              "bad_bars_or_missing": int((entry_ok & ~fwd_ok).sum()),
              "not_eligible": int((entry_ok & fwd_ok & ~el).sum())}
    return prep[entry_ok & fwd_ok & el].reset_index(drop=True), counts


def _degenerate(T: pd.DataFrame) -> np.ndarray:
    adv, vol = T["adv"].to_numpy(float), T["vol_bps"].to_numpy(float)
    return ~np.isfinite(adv) | (adv <= 0) | ~np.isfinite(vol)


def _cost(T: pd.DataFrame, impact: np.ndarray, deg: np.ndarray, lenient: bool) -> np.ndarray:
    return T["floor_bps"].to_numpy(float) + np.where(deg, 0.0 if lenient else DEGENERATE_IMPACT_BPS, impact)


def _fit_impact(T: pd.DataFrame, fit: dict, notional: float) -> np.ndarray:
    out = np.empty(len(T))
    for i, (s, a, v) in enumerate(zip(T["symbol"], T["adv"], T["vol_bps"])):
        out[i] = impact_bps(np.array([v]), np.array([a]), notional, fit, s)[0]
    return out


def _one_lot_cost(T: pd.DataFrame, price: np.ndarray, deg: np.ndarray, lenient: bool) -> np.ndarray:
    imp = np.empty(len(T))
    for i in range(len(T)):
        imp[i] = recost(T.iloc[[i]], float(price[i]) * SHARES_PER_LOT)["impact_bps"].iloc[0]
    return _cost(T, imp, deg, lenient)


def enrich_trades(env: Env, T: pd.DataFrame, prep: pd.DataFrame, k: int, lenient: bool) -> pd.DataFrame:
    """Join trades back to events and add the cost variants, IHSG return and regime flag (bps / decimals)."""
    if T.empty:
        return T.assign(review_label=[], announce_session=[], entry_px=[], ihsg_ret=[], below_sma=[])
    key = prep.set_index(["ticker", "ip"])
    idx = pd.MultiIndex.from_arrays([T["symbol"], T["pos"]])
    meta = key.reindex(idx)
    T = T.copy()
    T["review_label"] = meta["review_label"].to_numpy()
    T["announce_session"] = meta["announce_session"].to_numpy()
    T["ia"] = meta["ia"].to_numpy(dtype=int)
    pos = T["pos"].to_numpy(int)
    close = env.panel.close.to_numpy(float)
    jj = np.array([env.col[s] for s in T["symbol"]])
    T["entry_px"] = close[pos + 1, jj]
    ih = env.ihsg_close
    T["ihsg_ret"] = ih[pos + 1 + k] / ih[pos + 1] - 1.0
    T["below_sma"] = env.ihsg_below_sma[T["ia"].to_numpy()]
    deg = _degenerate(T)
    T["degenerate_cost_inputs"] = deg
    T["cost_cur"] = _cost(T, T["impact_bps"].to_numpy(float), deg, lenient)
    for name in ("cand_latest", "cand_pooled"):
        T[f"cost_{name}"] = _cost(T, _fit_impact(T, FITS[name], NOTIONAL_IDR), deg, lenient)
    T["cost_1lot"] = _one_lot_cost(T, T["entry_px"].to_numpy(float), deg, lenient)
    imp10 = recost(T, NOTIONAL_IDR * CAPACITY_MULT)["impact_bps"].to_numpy(float)
    T["cost_10x"] = _cost(T, imp10, deg, lenient)
    T["gross_bps"] = T["gross"].to_numpy(float) * BPS
    T["null_bps"] = T["null_name"].to_numpy(float) * BPS
    T["edge_bps"] = T["gross_bps"] - T["null_bps"]
    return T


def _mean(x) -> float:
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else float("nan")


def cell_metrics(T: pd.DataFrame) -> dict:
    """Means in bps. Gate inputs for one (cell, k) trade set."""
    if T.empty:
        return {"n": 0, "reviews": 0}
    edge = T["edge_bps"].to_numpy(float)
    m, se, t = cluster_t(edge, T["review_label"].to_numpy())
    net = edge - T["cost_cur"].to_numpy(float)
    ih = T["ihsg_ret"].to_numpy(float) * BPS
    g6 = T["gross_bps"].to_numpy(float) - T["cost_cur"].to_numpy(float) - ih
    out = {
        "n": int(len(T)), "reviews": int(T["review_label"].nunique()),
        "gross": _mean(T["gross_bps"]), "null": _mean(T["null_bps"]), "edge": m, "edge_se": se, "t": t,
        "cost_cur": _mean(T["cost_cur"]), "cost_cand_latest": _mean(T["cost_cand_latest"]),
        "cost_cand_pooled": _mean(T["cost_cand_pooled"]), "cost_1lot": _mean(T["cost_1lot"]),
        "cost_10x": _mean(T["cost_10x"]),
        "net_edge_cur": _mean(net),
        "net_edge_cand_latest": _mean(edge - T["cost_cand_latest"].to_numpy(float)),
        "net_edge_cand_pooled": _mean(edge - T["cost_cand_pooled"].to_numpy(float)),
        "net_edge_1lot": _mean(edge - T["cost_1lot"].to_numpy(float)),
        "net_edge_10x": _mean(edge - T["cost_10x"].to_numpy(float)),
        "net_abs_cur": _mean(T["gross_bps"].to_numpy(float) - T["cost_cur"].to_numpy(float)),
        "ihsg_ret": _mean(ih), "n_ihsg": int(np.isfinite(ih).sum()),
        "net_minus_ihsg": _mean(g6),
        "drop5_net_edge_cur": drop_top(net),
        "n_degenerate_cost_inputs": int(T["degenerate_cost_inputs"].sum()),
    }
    return out


def _pos(x) -> bool:
    return bool(np.isfinite(x) and x > 0)


def sample_status(n: int, reviews: int) -> str:
    return "ok" if n >= MIN_EVENTS and reviews >= MIN_REVIEWS else "insufficient_n"


def gates_for(m: dict) -> dict:
    """G2, G3(a,b,c), G4, G6 for one cell's metrics (G7 is applied across cells)."""
    t = m.get("t", float("nan"))
    g = {
        "G2": bool(np.isfinite(t) and t >= T_MIN),
        "G3a_current": _pos(m.get("net_edge_cur", float("nan"))),
        "G3b_candidate": _pos(m.get(f"net_edge_{GATE_FIT}", float("nan"))),
        "G3c_1lot": _pos(m.get("net_edge_1lot", float("nan"))),
        "G4": _pos(m.get("drop5_net_edge_cur", float("nan"))),
        "G6": _pos(m.get("net_minus_ihsg", float("nan"))),
    }
    g["G3"] = g["G3a_current"] and g["G3b_candidate"] and g["G3c_1lot"]
    return g


def regime_split(T: pd.DataFrame) -> dict:
    """G8 (report only): IHSG below its 200-session SMA at session a."""
    out = {}
    for name, flag in (("below_sma200", 1.0), ("at_or_above_sma200", 0.0)):
        s = T[T["below_sma"] == flag] if len(T) else T
        if s.empty:
            out[name] = {"n": 0, "reviews": 0}
            continue
        edge = s["edge_bps"].to_numpy(float)
        _, _, t = cluster_t(edge, s["review_label"].to_numpy())
        out[name] = {"n": int(len(s)), "reviews": int(s["review_label"].nunique()), "gross": _mean(s["gross_bps"]),
                     "edge": _mean(edge), "t": t, "net_edge_cur": _mean(edge - s["cost_cur"].to_numpy(float)),
                     "net_minus_ihsg": _mean(s["gross_bps"].to_numpy(float) - s["cost_cur"].to_numpy(float)
                                             - s["ihsg_ret"].to_numpy(float) * BPS)}
    out["unknown_regime"] = int(T["below_sma"].isna().sum()) if len(T) else 0
    return out


def evaluate(env: Env, prep: pd.DataFrame, k: int, entry: str = ENTRY_NEXT_CLOSE, *, eligibility_mode: str = "default"
             ) -> tuple[pd.DataFrame, dict]:
    """Run the harness for one (k, entry, eligibility) -> (enriched trades, {exclusions, metrics, gates})."""
    eligible = env.elig_default if eligibility_mode == "default" else env.elig_traded
    cand, ex = classify_harness_exclusions(env, prep, k, entry, eligible)
    mask = event_mask(env.panel, cand)
    T = build_event_trades(env.panel, mask, k, entry, eligible=eligible, notional_idr=NOTIONAL_IDR, sequential=True)
    ex["sequential_overlap"] = int(len(cand) - len(T))
    ex["trades"] = int(len(T))
    T = enrich_trades(env, T, cand, k, lenient=(eligibility_mode != "default"))
    m = cell_metrics(T)
    return T, {"exclusions": ex, "metrics": m, "sample": sample_status(m["n"], m["reviews"]), "gates": gates_for(m)
               if m["n"] else {}}


# ---------------------------------------------------------------------------
# Holm and verdicts
# ---------------------------------------------------------------------------

def holm_adjust(p_by_cell: dict[tuple[str, int], float]) -> dict[tuple[str, int], float]:
    """Holm step-down over EVERY cell in the dict; cells without a usable test must be passed in with p = 1."""
    keys = list(p_by_cell)
    adj = holm([float(p_by_cell[k]) for k in keys])
    return dict(zip(keys, adj))


def cell_p(res: dict) -> float:
    """One-sided p from the clustered G2 t; no_data / insufficient_n / NaN count as p = 1."""
    if res.get("status") != "tested":
        return 1.0
    p = norm_sf(res["metrics"].get("t", float("nan")))
    return float(p) if np.isfinite(p) else 1.0


def verdict(res: dict) -> tuple[str, list[str]]:
    if res["status"] != "tested":
        return res["status"], []
    g = res["gates"]
    failed: list[str] = []
    for name in ("G2", "G3", "G4", "G6", "G7"):
        if g.get(name, False):
            continue
        failed += [x for x in ("G3a_current", "G3b_candidate", "G3c_1lot") if not g[x]] if name == "G3" else [name]
    return ("PASS_DEV" if not failed else "FAIL"), failed


def decision(results: dict) -> dict:
    """Plan section 5: single-provider PASS_DEV = candidate; pooled-only = investigate."""
    passed = {c: [k for k in HOLDS if results[c][k]["verdict"] == "PASS_DEV"] for c in CELLS}
    single = {c: ks for c, ks in passed.items() if c != "pooled" and ks}
    if single:
        return {"outcome": "candidate", "cells": single}
    if passed["pooled"]:
        return {"outcome": "investigate", "cells": {"pooled": passed["pooled"]}}
    return {"outcome": "no_pass", "cells": {}}


# ---------------------------------------------------------------------------
# Whole run
# ---------------------------------------------------------------------------

def run_window(ev: pd.DataFrame, env: Env, window: str = "dev", cells: tuple[str, ...] = CELLS) -> dict:
    """The given cells (default all 15), every sensitivity. ``ev`` must already be the window's events.

    A subset leaves the other cells uncomputed (plan 21 §9: the MSCI_ST holdout amendment
    keeps the rest of the holdout unseen). Holm is then over the subset only and is not a gate.
    """
    if window == "dev":
        guard_dev_events(ev)
    unknown = set(cells) - set(CELLS)
    if unknown:
        raise ValueError(f"unknown cells {sorted(unknown)}; choose from {CELLS}")
    results: dict = {c: {} for c in cells}
    trades_out: list[pd.DataFrame] = []
    for cell in cells:
        d = cell_events(ev, cell)
        if d.empty:
            for k in HOLDS:
                results[cell][k] = {"status": "no_data", "raw_events": 0,
                                    "reason": f"no {cell} deletion events in events.csv for the {window} window"}
            continue
        prep, pre = prepare_events(d, env)
        if window == "dev":
            guard_dev_sessions(prep, env.panel.dates)
        base = {"raw_events": int(len(d)), "raw_reviews": int(d["review_label"].nunique()), "pre_exclusions": pre,
                "candidate_events": int(len(prep))}
        for k in HOLDS:
            T, main = evaluate(env, prep, k)
            _, opn = evaluate(env, prep, k, ENTRY_NEXT_OPEN)
            T_off, off = evaluate(env, prep, k, eligibility_mode="traded_only")
            status = "tested" if main["sample"] == "ok" else "insufficient_n"
            res = {**base, "status": status, **main, "sens_open_entry": opn, "sens_eligibility_traded_only": off,
                   "regime_g8": regime_split(T) if len(T) else {}}
            results[cell][k] = res
            if len(T):
                trades_out.append(T.assign(cell=cell, k=k))
    adj = holm_adjust({(c, k): cell_p(results[c][k]) for c in cells for k in HOLDS})
    for c in cells:
        for k in HOLDS:
            r = results[c][k]
            r["p_one_sided"] = cell_p(r)
            r["p_holm"] = adj[(c, k)]
            if r["status"] == "tested":
                r["gates"] = {**r["gates"], "G7": bool(np.isfinite(r["p_holm"]) and r["p_holm"] < HOLM_ALPHA)}
            r["verdict"], r["failed_gates"] = verdict(r)
    return {"window": window, "cells": results, "decision": decision(results) if cells == CELLS else None,
            "n_cells": len(cells) * len(HOLDS), "_trades": trades_out}


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def jsonable(out: dict) -> dict:
    cells = {c: {str(k): v for k, v in by_k.items()} for c, by_k in out["cells"].items()}
    return {**{k: v for k, v in out.items() if k not in ("_trades", "cells")}, "cells": cells}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--holdout", action="store_true",
                    help="plan 21 G5 holdout. Refuses to run unless research/results/index_rebalance/HOLDOUT_AUTHORIZED exists.")
    ap.add_argument("--cells", default=",".join(CELLS),
                    help="comma-separated subset of cells to compute (default all); others stay uncomputed")
    args = ap.parse_args(argv)
    cells = tuple(c.strip() for c in args.cells.split(",") if c.strip())
    window = "holdout" if args.holdout else "dev"
    if args.holdout and not holdout_authorized():
        print(f"REFUSED: holdout run needs {HOLDOUT_FLAG} (owner authorization). Nothing was computed.", file=sys.stderr)
        return 2
    from strategy_fit import h2_report
    ev = pd.read_csv(EVENTS_CSV, dtype=str, keep_default_na=False)
    sel = select_window(ev, window)
    env = load_env()
    out = run_window(sel, env, window, cells)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = ("h2_dev" if window == "dev" else "h2_holdout") + ("" if cells == CELLS else "_" + "_".join(cells).lower())
    (OUT_DIR / f"{tag}.json").write_text(json.dumps(jsonable(out), indent=1, default=_json_default))
    if out["_trades"]:
        pd.concat(out["_trades"], ignore_index=True).to_csv(OUT_DIR / f"{tag}_trades.csv", index=False)
    if cells != CELLS:
        text = subset_summary(out)
        (OUT_DIR / f"{tag}.md").write_text(text)
        print(text)
        return 0
    (OUT_DIR / f"{tag}.md").write_text(h2_report.render_full(out))
    if window == "dev":
        DOC_PATH.write_text(h2_report.render_doc(out))
    print(h2_report.render_full(out))
    return 0


SUMMARY_KEYS = ("n", "reviews", "gross", "null", "edge", "t", "net_edge_cur", f"net_edge_{GATE_FIT}",
                "net_edge_1lot", "net_minus_ihsg")


def subset_summary(out: dict) -> str:
    """Plain per-cell, per-hold lines for a --cells run (the full renderer assumes all 15 cells)."""
    lines = [f"# H2 {out['window']} run, cells: {', '.join(out['cells'])} (bps)", ""]
    for c, by_k in out["cells"].items():
        for k, r in by_k.items():
            m = r.get("metrics", {})
            vals = "  ".join(f"{key}={m[key]:.1f}" if isinstance(m.get(key), float) else f"{key}={m.get(key)}"
                             for key in SUMMARY_KEYS)
            lines.append(f"{c} k={k} [{r['status']}] {vals}")
            lines.append(f"    gates={r.get('gates')} drop_top5={m.get('drop5_net_edge_cur')}")
            lines.append(f"    exclusions={r.get('exclusions')}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
