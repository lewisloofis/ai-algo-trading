"""
strategy_fit/families_indicator_rules.py -- plan 24 (classic indicator rules), Phase 1 DEVELOPMENT test.

FROZEN by plan 24 section 9 (signed 2026-10-17). Five textbook rules, signal-based exits, variable holds:

  R1 golden cross  SMA50/SMA200      entry: SMA50 crosses above SMA200   exit: crosses below          no time stop
  R2 MACD          12/26/9           entry: MACD crosses above signal    exit: crosses below          no time stop
  R3 RSI oversold  Wilder 14         entry: RSI crosses below 30         exit: RSI >= 50              20 sessions
  R4 Bollinger     SMA20 +/- 2 pop.  entry: close < lower band           exit: close >= SMA20         20 sessions
  R5 RSI2 pullback RSI2, SMA200/5    entry: RSI2 < 10 and close > SMA200 exit: close > SMA5           20 sessions

A signal is read on the close of day t; the trade happens at open_{t+1}. Exit: open_{u+1} after the first exit
signal or time stop on u > t (time stop = sessions held, counted from the entry session). One position per
name per rule. Costs, eligibility, dividends, bad bars and the IHSG comparison reuse the plan 21/22 harness
(``families_idx_structural`` / ``families_index_rebalance``).

DEVELOPMENT WINDOW ONLY: entries 2015-01-01..2022-12-31. The panel is cut at 2022-12-31 BEFORE anything is
computed, so no price dated >= 2023-01-01 is ever read; trades still open at the last development session
are closed at its open. ``guard_holdout`` raises on any later date unless
research/results/indicator_rules/HOLDOUT_AUTHORIZED exists. There is no holdout CLI.

    cd research && .venv/bin/python -m strategy_fit.families_indicator_rules --dev
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from index_events import event_time as et
from strategy_fit.families_idx_structural import (
    BAD_BAR_ABS_RETURN, DIVIDEND_WITHHOLDING_TAX, Panel, build_panel, cost_components, impact_inputs,
    locked_up_entry, recost)
from strategy_fit.families_index_rebalance import (
    BPS, CAPACITY_MULT, HOLM_ALPHA, NOTIONAL_IDR, Env, _cost, _degenerate, _fit_impact, _mean, _one_lot_cost,
    cell_metrics, gates_for, holm_adjust, make_env, regime_split)
from strategy_fit.shortlist_2026_10 import FITS, norm_sf

RESEARCH = Path(__file__).resolve().parents[1]
DAILY_DIR = RESEARCH / "data" / "daily"
OUT_DIR = RESEARCH / "results" / "indicator_rules"
HOLDOUT_FLAG = OUT_DIR / "HOLDOUT_AUTHORIZED"

DEV_START = pd.Timestamp("2015-01-01")
DEV_END = pd.Timestamp("2022-12-31")
HOLDOUT_START = pd.Timestamp("2023-01-01")
EXCL_2020 = (pd.Timestamp("2020-02-01"), pd.Timestamp("2020-12-31"))

RULES = ("R1", "R2", "R3", "R4", "R5")
TIME_STOP: dict[str, int | None] = {"R1": None, "R2": None, "R3": 20, "R4": 20, "R5": 20}
MIN_TRADES = 100
MIN_MONTHS = 24
T_MIN = 2.0
DROP_DATES = 5
RSI_OVERSOLD, RSI_EXIT = 30.0, 50.0
RSI2_ENTRY = 10.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_WINDOW, BB_STD = 20, 2.0


class HoldoutError(ValueError):
    """A date >= 2023-01-01 reached the analysis without HOLDOUT_AUTHORIZED."""


def holdout_authorized(flag: Path | None = None) -> bool:
    return Path(HOLDOUT_FLAG if flag is None else flag).exists()


def guard_holdout(dates: pd.DatetimeIndex, flag: Path | None = None) -> None:
    """Refuse any panel/signal date >= 2023-01-01 unless the authorization file exists."""
    if len(dates) and pd.DatetimeIndex(dates).max() >= HOLDOUT_START and not holdout_authorized(flag):
        raise HoldoutError(f"date >= {HOLDOUT_START.date()} refused: {HOLDOUT_FLAG} does not exist")


# ---------------------------------------------------------------------------
# Causal indicators (Series on the name's own consecutive closes)
# ---------------------------------------------------------------------------

def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW, sig: int = MACD_SIGNAL
         ) -> tuple[pd.Series, pd.Series]:
    """(MACD line, signal line); both NaN until slow + sig observations exist (26 + 9 = 35)."""
    line = ema(close, fast) - ema(close, slow)
    signal = ema(line, sig)
    full = pd.Series(np.arange(len(close)) + 1 >= slow + sig, index=close.index)
    return line.where(full), signal.where(full)


def rsi_wilder(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI: ewm(alpha=1/n, adjust=False) of gains / losses; NaN until n price changes exist."""
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    al = (-d).clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        out = np.where(al > 0, 100.0 - 100.0 / (1.0 + rs), np.where(ag > 0, 100.0, 50.0))
    return pd.Series(out, index=close.index).where(al.notna())


def bollinger(close: pd.Series, n: int = BB_WINDOW, k: float = BB_STD) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(lower, middle, upper) with the POPULATION standard deviation (ddof=0)."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def crosses_above(a: pd.Series, b: pd.Series) -> pd.Series:
    """<= on t-1 and > on t; False wherever either value is missing."""
    return ((a.shift(1) <= b.shift(1)) & (a > b)).fillna(False).astype(bool)


def crosses_below(a: pd.Series, b: pd.Series) -> pd.Series:
    """>= on t-1 and < on t."""
    return ((a.shift(1) >= b.shift(1)) & (a < b)).fillna(False).astype(bool)


def rule_signals(rule: str, close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """(entry, exit) boolean Series on ``close``'s own index; every value at t uses closes <= t only."""
    f = {"R1": _r1, "R2": _r2, "R3": _r3, "R4": _r4, "R5": _r5}.get(rule)
    if f is None:
        raise ValueError(f"unknown rule {rule!r}")
    entry, exit_ = f(close)
    return entry.fillna(False).astype(bool), exit_.fillna(False).astype(bool)


def _r1(c):
    fast, slow = sma(c, 50), sma(c, 200)
    return crosses_above(fast, slow), crosses_below(fast, slow)


def _r2(c):
    line, sig = macd(c)
    return crosses_above(line, sig), crosses_below(line, sig)


def _r3(c):
    r = rsi_wilder(c, 14)
    return (r.shift(1) >= RSI_OVERSOLD) & (r < RSI_OVERSOLD), r >= RSI_EXIT


def _r4(c):
    lower, mid, _ = bollinger(c)
    return c < lower, c >= mid


def _r5(c):
    return (rsi_wilder(c, 2) < RSI2_ENTRY) & (c > sma(c, 200)), c > sma(c, 5)


def panel_signals(close: pd.DataFrame, rule: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Signals on the panel calendar. Indicators run on each name's own consecutive closes (NaN days skipped)."""
    entry = pd.DataFrame(False, index=close.index, columns=close.columns)
    exit_ = entry.copy()
    for s in close.columns:
        c = close[s].dropna()
        if len(c) < 3:
            continue
        e, x = rule_signals(rule, c)
        entry.loc[c.index, s] = e.to_numpy()
        exit_.loc[c.index, s] = x.to_numpy()
    return entry, exit_


# ---------------------------------------------------------------------------
# Book: numpy view of the (already dev-truncated) panel
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Book:
    dates: pd.DatetimeIndex
    symbols: list[str]
    open: np.ndarray
    close: np.ndarray
    tradable: np.ndarray       # volume > 0 and open > 0
    net_div: np.ndarray        # dividend x (1 - withholding), 0 where none
    div_cum: np.ndarray
    bad_cum: np.ndarray        # cumulative count of |1d close return| > BAD_BAR_ABS_RETURN
    elig: np.ndarray
    locked: np.ndarray
    adv: np.ndarray
    vol_bps: np.ndarray
    last: int                  # index of the last development session

    @property
    def n(self) -> int:
        return len(self.dates)


def build_book(env: Env, flag: Path | None = None) -> Book:
    panel = env.panel
    guard_holdout(panel.dates, flag)
    nd = (panel.dividend.fillna(0.0) * (1.0 - DIVIDEND_WITHHOLDING_TAX)).to_numpy(float)
    bad = (panel.close.pct_change(fill_method=None).abs() > BAD_BAR_ABS_RETURN).to_numpy(float)
    op = panel.open.to_numpy(float)
    adv, vol = impact_inputs(panel)
    return Book(
        dates=panel.dates, symbols=panel.symbols, open=op, close=panel.close.to_numpy(float),
        tradable=(panel.volume.fillna(0).to_numpy(float) > 0) & (np.nan_to_num(op) > 0),
        net_div=nd, div_cum=np.cumsum(nd, axis=0), bad_cum=np.cumsum(np.nan_to_num(bad), axis=0),
        elig=env.elig_default.reindex_like(panel.close).fillna(False).to_numpy(bool),
        locked=locked_up_entry(panel).to_numpy(bool), adv=adv.to_numpy(float), vol_bps=vol.to_numpy(float),
        last=len(panel.dates) - 1)


# ---------------------------------------------------------------------------
# Variable-hold trade builder
# ---------------------------------------------------------------------------

DIAG_KEYS = ("signals", "overlap_ignored", "not_eligible", "locked_upper_limit", "entry_not_traded",
             "zero_hold", "bad_bar_void", "trades", "closed_at_dev_end", "exit_signal", "exit_time_stop")


def exit_position(book: Book, j: int, e: int, exit_idx: np.ndarray, time_stop: int | None
                  ) -> tuple[int, str] | None:
    """(exit open position, reason) for a position entered at open position ``e``; None if unusable.

    First exit signal u >= e (u > t) or time stop u = e + time_stop - 1; exit at open_{u+1}, the next traded
    open if that one has no trade; past the last development session -> its open (or the last traded open
    at or before it)."""
    k = int(np.searchsorted(exit_idx, e))
    u_sig = int(exit_idx[k]) if k < len(exit_idx) else None
    u_ts = e + time_stop - 1 if time_stop else None
    cands = [(u, r) for u, r in ((u_sig, "exit_signal"), (u_ts, "exit_time_stop")) if u is not None]
    if cands:
        u, reason = min(cands, key=lambda p: p[0])
        x = u + 1
        while x <= book.last and not book.tradable[x, j]:
            x += 1
        if x <= book.last:
            return x, reason
    x = book.last
    while x > e and not book.tradable[x, j]:
        x -= 1
    return (x, "dev_end") if x > e else None


def _try_trade(book: Book, j: int, t: int, e: int, exit_idx, time_stop, diag: dict):
    if not book.elig[t, j]:
        diag["not_eligible"] += 1
    elif book.locked[t, j]:
        diag["locked_upper_limit"] += 1
    elif not book.tradable[e, j]:
        diag["entry_not_traded"] += 1
    else:
        return exit_position(book, j, e, exit_idx, time_stop) or (None, "zero_hold")
    return None, None


def build_trades(book: Book, entry: np.ndarray, exit_: np.ndarray, time_stop: int | None,
                 start_pos: int = 0) -> tuple[pd.DataFrame, dict]:
    """One row per executed trade. ``entry`` / ``exit_`` are (dates x names) boolean signal masks."""
    diag = dict.fromkeys(DIAG_KEYS, 0)
    rows = []
    for j in range(len(book.symbols)):
        exit_idx = np.flatnonzero(exit_[:, j])
        busy_x = -1
        for t in np.flatnonzero(entry[:, j]):
            e = int(t) + 1
            if e < max(start_pos, 1) or e > book.last:
                continue
            diag["signals"] += 1
            if e < busy_x:
                diag["overlap_ignored"] += 1
                continue
            x, reason = _try_trade(book, j, int(t), e, exit_idx, time_stop, diag)
            if x is None:
                diag["zero_hold"] += reason == "zero_hold"
                continue
            busy_x = x
            if book.bad_cum[x, j] - book.bad_cum[e - 1, j] > 0:
                diag["bad_bar_void"] += 1
                continue
            diag[reason] = diag.get(reason, 0) + 1
            diag["trades"] += 1
            diag["closed_at_dev_end"] += reason == "dev_end"
            gross = (book.open[x, j] + book.div_cum[x, j] - book.div_cum[e, j]) / book.open[e, j] - 1.0
            rows.append((book.symbols[j], j, int(t), e, x, reason, gross))
    cols = ["symbol", "j", "pos", "e", "x", "exit_reason", "gross"]
    T = pd.DataFrame(rows, columns=cols)
    T["hold"] = T["x"] - T["e"]
    return T.sort_values(["e", "symbol"]).reset_index(drop=True), diag


# ---------------------------------------------------------------------------
# Null, costs, enrichment
# ---------------------------------------------------------------------------

def daily_open_returns(book: Book) -> np.ndarray:
    """r[d] = total return open_d -> open_{d+1} (dividend on d+1 net of tax); NaN if either price or a bar is bad."""
    r = np.full(book.open.shape, np.nan)
    r[:-1] = (book.open[1:] + book.net_div[1:]) / book.open[:-1] - 1.0
    bad_day = np.diff(book.bad_cum, axis=0, prepend=0.0) > 0            # bar d is bad
    bad = bad_day.copy()
    bad[:-1] |= bad_day[1:]
    return np.where(bad | ~np.isfinite(r), np.nan, r)


def name_year_null(book: Book) -> pd.DataFrame:
    """Mean daily open->open total return per (year of d, name) over sessions d whose PRIOR close was eligible
    (the eligibility state an entry at open_d would have had). Index = year, columns = symbols."""
    r = daily_open_returns(book)
    prior = np.vstack([np.zeros((1, r.shape[1]), bool), book.elig[:-1]])
    r = np.where(prior, r, np.nan)
    df = pd.DataFrame(r, index=book.dates, columns=book.symbols)
    return df.groupby(df.index.year).mean()


def score_trades(env: Env, book: Book, raw: pd.DataFrame, ihsg_open: np.ndarray) -> pd.DataFrame:
    """Gross / null / edge, the cost variants, IHSG return over the identical open->open window, regime flag."""
    if raw.empty:
        return raw
    T = raw.copy()
    j, t, e, x = (T[c].to_numpy(int) for c in ("j", "pos", "e", "x"))
    T["entry_date"] = book.dates[e]
    T["exit_date"] = book.dates[x]
    T["review_label"] = T["entry_date"].dt.strftime("%Y-%m")            # cluster = entry calendar month
    T["date_label"] = T["entry_date"].dt.strftime("%Y-%m-%d")
    null_tab = name_year_null(book)
    per_day = np.array([null_tab.at[y, s] if y in null_tab.index else np.nan
                        for y, s in zip(T["entry_date"].dt.year, T["symbol"])])
    T["entry_px"] = book.open[e, j]
    T["div_added"] = (book.div_cum[x, j] - book.div_cum[e, j])
    floor, impact, coeff = cost_components(T["entry_px"].to_numpy(float), book.adv[t, j], book.vol_bps[t, j],
                                           NOTIONAL_IDR, T["symbol"].tolist())
    T["adv"], T["vol_bps"] = book.adv[t, j], book.vol_bps[t, j]
    T["floor_bps"], T["impact_bps"], T["coeff"], T["cost_bps"] = floor, impact, coeff, floor + impact
    T["ihsg_ret"] = ihsg_open[x] / ihsg_open[e] - 1.0
    T["below_sma"] = env.ihsg_below_sma[t]
    deg = _degenerate(T)
    T["degenerate_cost_inputs"] = deg
    T["cost_cur"] = _cost(T, T["impact_bps"].to_numpy(float), deg, False)
    for name in ("cand_latest", "cand_pooled"):
        T[f"cost_{name}"] = _cost(T, _fit_impact(T, FITS[name], NOTIONAL_IDR), deg, False)
    T["cost_1lot"] = _one_lot_cost(T, T["entry_px"].to_numpy(float), deg, False)
    T["cost_10x"] = _cost(T, recost(T, NOTIONAL_IDR * CAPACITY_MULT)["impact_bps"].to_numpy(float), deg, False)
    T["gross_bps"] = T["gross"].to_numpy(float) * BPS
    T["null_bps"] = T["hold"].to_numpy(float) * per_day * BPS
    T["edge_bps"] = T["gross_bps"] - T["null_bps"]
    return T


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def drop_best_dates(T: pd.DataFrame, n: int = DROP_DATES, use_gross: bool = False) -> float:
    """Mean net over trades after removing every trade entered on the ``n`` entry dates with the highest per-date
    mean net (net = edge - cost, as plan 22; ``use_gross`` = gross - cost). NaN when there are <= n dates."""
    if T.empty:
        return float("nan")
    net = (T["gross_bps"] if use_gross else T["edge_bps"]) - T["cost_cur"]
    per_date = net.groupby(T["date_label"]).mean()
    if len(per_date) <= n:
        return float("nan")
    drop = set(per_date.nlargest(n).index)
    return _mean(net[~T["date_label"].isin(drop)])


def g9_metrics(T: pd.DataFrame) -> dict:
    """Development result without entries dated 2020-02-01..2020-12-31."""
    if T.empty:
        return {"n": 0, "dates": 0, "n_removed": 0, "edge": float("nan"), "net": float("nan"),
                "net_minus_ihsg": float("nan"), "net_gross_basis": float("nan"), "pass": False}
    d = pd.to_datetime(T["entry_date"])
    sub = T[~((d >= EXCL_2020[0]) & (d <= EXCL_2020[1]))]
    out = {"n": int(len(sub)), "dates": int(sub["review_label"].nunique()), "n_removed": int(len(T) - len(sub))}
    if sub.empty:
        return {**out, "edge": float("nan"), "net": float("nan"), "net_minus_ihsg": float("nan"),
                "net_gross_basis": float("nan"), "pass": False}
    cost = sub["cost_cur"].to_numpy(float)
    e = _mean(sub["edge_bps"])
    net = _mean(sub["edge_bps"].to_numpy(float) - cost)
    nmi = _mean(sub["gross_bps"].to_numpy(float) - cost - sub["ihsg_ret"].to_numpy(float) * BPS)
    return {**out, "edge": e, "net": net, "net_minus_ihsg": nmi, "net_gross_basis": _mean(sub["gross_bps"] - cost),
            "pass": bool(e > 0 and net > 0 and np.isfinite(nmi) and nmi > 0)}


def sample_status(n: int, months: int) -> str:
    return "ok" if n >= MIN_TRADES and months >= MIN_MONTHS else "insufficient_n"


def hold_stats(T: pd.DataFrame) -> dict:
    if T.empty:
        return {}
    cost = T["cost_cur"].to_numpy(float)
    return {"mean_hold": float(T["hold"].mean()), "median_hold": float(T["hold"].median()),
            "win_rate_gross": float((T["gross_bps"] > 0).mean()),
            "win_rate_net": float((T["gross_bps"].to_numpy(float) - cost > 0).mean()),
            "net_gross_basis": _mean(T["gross_bps"].to_numpy(float) - cost),
            "drop5_net_gross_basis": drop_best_dates(T, use_gross=True)}


def evaluate_rule(env: Env, book: Book, ihsg_open: np.ndarray, rule: str, flag: Path | None = None
                  ) -> tuple[pd.DataFrame, dict]:
    guard_holdout(book.dates, flag)
    entry, exit_ = panel_signals(env.panel.close, rule)
    start = int(book.dates.searchsorted(DEV_START))
    raw, diag = build_trades(book, entry.to_numpy(bool), exit_.to_numpy(bool), TIME_STOP[rule], start)
    T = score_trades(env, book, raw, ihsg_open)
    ok = T[np.isfinite(T["null_bps"])] if len(T) else T
    diag["no_null"] = int(len(T) - len(ok))
    T = ok.reset_index(drop=True)
    if T.empty:
        return T, {"diag": diag, "metrics": {"n": 0, "reviews": 0}, "sample": "insufficient_n", "gates": {},
                   "g9": g9_metrics(T), "regime_g8": {}, "hold": {}}
    m = cell_metrics(T)
    m["drop5_net_edge_cur"] = drop_best_dates(T)
    g = gates_for(m)
    g9 = g9_metrics(T)
    g["G9"] = g9["pass"]
    return T, {"diag": diag, "metrics": m, "sample": sample_status(m["n"], m["reviews"]), "gates": g, "g9": g9,
               "regime_g8": regime_split(T), "hold": hold_stats(T)}


# ---------------------------------------------------------------------------
# Holm, verdicts, section 5
# ---------------------------------------------------------------------------

def cell_p(res: dict) -> float:
    if res.get("status") != "tested":
        return 1.0
    p = norm_sf(res["metrics"].get("t", float("nan")))
    return float(p) if np.isfinite(p) else 1.0


def apply_holm(results: dict) -> dict:
    """G7 over exactly the 5 cells; insufficient_n counts as p = 1."""
    adj = holm_adjust({r: cell_p(results[r]) for r in RULES})
    for r in RULES:
        res = results[r]
        res["p_one_sided"] = cell_p(res)
        res["p_holm"] = adj[r]
        if res["status"] == "tested":
            res["gates"] = {**res["gates"], "G7": bool(np.isfinite(res["p_holm"]) and res["p_holm"] < HOLM_ALPHA)}
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


def decision(results: dict) -> dict:
    passed = [r for r in RULES if results[r]["verdict"] == "PASS_DEV"]
    if passed:
        return {"branch": "pass_dev_then_holdout", "cells": passed}
    return {"branch": "nothing_passes_dev_close_holdout_unspent", "cells": []}


# ---------------------------------------------------------------------------
# Data loading and the whole run
# ---------------------------------------------------------------------------

def truncate_hist(hist: dict[str, pd.DataFrame], end: pd.Timestamp = DEV_END) -> dict[str, pd.DataFrame]:
    """Drop every row dated after ``end`` BEFORE any computation."""
    out = {}
    for s, df in hist.items():
        keep = pd.to_datetime(df["date"].astype(str)) <= end
        if keep.any():
            out[s] = df[keep.to_numpy()]
    return out


def ihsg_frames(raw: pd.DataFrame, end: pd.Timestamp = DEV_END) -> tuple[pd.Series, pd.Series]:
    """(close, open) of IHSG up to ``end``, indexed by date."""
    idx = pd.DatetimeIndex(pd.to_datetime(raw["date"].astype(str)))
    out = []
    for c in ("close", "open"):
        s = pd.Series(pd.to_numeric(raw[c], errors="coerce").to_numpy(), index=idx)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        out.append(s[s.index <= end].where(lambda v: v > 0))
    return out[0], out[1]


def build_env(hist: dict[str, pd.DataFrame], ihsg_raw: pd.DataFrame, end: pd.Timestamp = DEV_END,
              flag: Path | None = None) -> tuple[Env, np.ndarray]:
    """(Env, IHSG open aligned to the panel) built from data dated <= ``end`` only."""
    guard_holdout(pd.DatetimeIndex([end]), flag)
    close, opn = ihsg_frames(ihsg_raw, end)
    env = make_env(build_panel(truncate_hist(hist, end)), close)
    return env, opn.reindex(env.panel.dates).to_numpy(float)


def run_dev(env: Env, ihsg_open: np.ndarray, flag: Path | None = None, n_draws: int = 200, seed: int = 24) -> dict:
    from strategy_fit import families_indicator_rules_views as views
    book = build_book(env, flag)
    results: dict = {}
    trades = []
    for rule in RULES:
        T, info = evaluate_rule(env, book, ihsg_open, rule, flag)
        results[rule] = {"status": "tested" if info["sample"] == "ok" else "insufficient_n", **info}
        if len(T):
            trades.append(T.assign(rule=rule))
            results[rule]["c_rand"] = views.c_rand(book, T, n_draws, seed)
            results[rule]["portfolio"] = views.portfolio_view(env, book, T, ihsg_open)
    apply_holm(results)
    for r in RULES:
        results[r]["verdict"], results[r]["failed_gates"] = verdict(results[r])
    return {"window": "dev", "entry_window": [str(DEV_START.date()), str(DEV_END.date())], "holdout_run": False,
            "last_dev_session": str(book.dates[book.last].date()), "n_cells": len(RULES), "cells": results,
            "decision": decision(results), "_trades": trades}


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


def jsonable(out: dict) -> dict:
    return {k: v for k, v in out.items() if k != "_trades"}


def load_inputs() -> tuple[Env, np.ndarray]:
    return build_env(et.load_hist(DAILY_DIR), pd.read_parquet(DAILY_DIR / "IHSG.parquet"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dev", action="store_true", required=True,
                    help="plan 24 development window (entries 2015-01-01..2022-12-31). There is no holdout option.")
    ap.parse_args(argv)
    from strategy_fit import families_indicator_rules_report as rep
    env, ihsg_open = load_inputs()
    out = run_dev(env, ihsg_open)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "dev.json").write_text(json.dumps(jsonable(out), indent=1, default=_json_default))
    if out["_trades"]:
        pd.concat(out["_trades"], ignore_index=True).drop(columns=["j"]).to_csv(
            OUT_DIR / "dev_trades.csv", index=False)
    text = rep.render(out)
    (OUT_DIR / "dev.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
