"""
strategy_fit/shortlist_2026_10.py -- first-candidate shortlist (research, offline).

Reproduces docs/research/2026-10-15-first-candidate-shortlist.md. Reads ONLY
local daily bars (research/data/daily/*.parquet); no network, no credentials.
Re-uses the harness's own signal functions, sequential long-only de-overlap
(``harness.deoverlap_sequential``), entry convention (default ``next_close``),
always-long null definition and tick ladder, so a row here is comparable with
the grid. It does NOT scan a new grid: the name-level rows are the cells the
2026-10-14 re-score already flagged (+ the three it killed, as controls), and
the family-level rows are the catalogue's own 6 families x 5 holds, pooled
across names (a family-level test, 30 hypotheses, Holm-corrected).

    cd research
    python3 -m strategy_fit.shortlist_2026_10 --out-dir out_shortlist

Definitions (all bps, per round trip, long-only, one position per name):
  gross      mean forward return of the sequential trades (enter close_{s+1},
             exit close_{s+1+hold})
  null       mean forward return of the SAME name/window on EVERY priced day
             (always-long); edge = gross - null (the signal's own contribution)
  cost       tick_bps(entry price) + 40 (0.15% buy + 0.25% sell, the broker
             schedule in backtest.FEE / strategy/engine/sink.go) + impact.
             Impact = 2 * coeff(ADV bucket) * vol60 * sqrt(Q / ADV60), priced
             point-in-time at the signal day (impact.TrailingImpactModel's
             estimators, vectorised); Q = 3,000,000 IDR (harness notional) for
             the "grid" columns and Q = 1 lot for the "1-lot" column.
  t(edge)    Welch t of trade returns vs all-day returns, null's effective N
             = priced days / max(hold, 1) (overlapping windows).
  pooled t   cluster-robust (by entry month) t of per-trade excess over the
             name's own null.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from screener.metrics import min_spread_bps, tick_size
from strategy_fit.daily_walk_forward import walk_forward_daily
from strategy_fit.families_event import EVENT_DETECTORS
from strategy_fit.harness import deoverlap_sequential
from strategy_fit.impact import POSITION_NOTIONAL_IDR, liquidity_bucket
from strategy_fit.long_only import long_only_mask
from strategy_fit.run_strategy_fit import HISTORY_START, SINGLE_NAME_SIGNAL_FNS

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data" / "daily"

FEE_RT_BPS = 40.0                      # 15 bps buy + 25 bps sell (backtest.FEE)
FEE_BUY, FEE_SELL = 0.0015, 0.0025
SHARES_PER_LOT = 100
HOLDS = [1, 2, 5, 20, 60]
PRE_START, PRE_END = "2015-01-01", "2023-12-31"   # selection-independent window
MIN_N = 30
ARB_PCT = 0.15                         # assumed daily auto-reject-below (stress only)
TOP_K = 5

# The cells the 2026-10-14 re-score flagged (out_d1/comparison.md) + 3 it killed.
MECHANICAL_SURVIVORS = [
    ("BIPI", "daily_momentum", 5), ("BRPT", "volume_breakout", 2),
    ("DEWA", "daily_momentum", 5), ("DSSA", "volume_breakout", 2),
    ("PTRO", "daily_momentum", 5), ("RAJA", "daily_momentum", 5),
    ("RATU", "daily_momentum", 1), ("TAPG", "daily_momentum", 5),
    ("TINS", "mean_reversion", 5), ("WIFI", "daily_momentum", 5),
]
KILLED_CONTROLS = [
    ("BNBR", "daily_momentum", 2), ("HRTA", "volume_breakout", 5),
    ("INCO", "mean_reversion", 2),
]
FAMILIES = list(SINGLE_NAME_SIGNAL_FNS) + list(EVENT_DETECTORS)


# ---------------------------------------------------------------------------
# Impact coefficient sets
# ---------------------------------------------------------------------------

def _fit(buckets: dict, per_name: dict | None) -> dict:
    """{"buckets": {bucket: coeff}, "per_name": {symbol: coeff}} -- confirmed entries only
    (impact.get_impact_coeff resolution: per-name, then ADV bucket)."""
    pn = {}
    for sym, e in (per_name or {}).items():
        if e.get("confidence") == "confirmed" and e.get("coeff") is not None:
            pn[sym] = float(e["coeff"])
    return {"buckets": buckets, "per_name": pn}


def _load_fits() -> dict[str, dict]:
    cur = json.loads((HERE / "impact_coeffs.json").read_text())
    v2 = json.loads((HERE / "impact_coeffs.CANDIDATE-2026-10-14-full-capture-v2-post0911.json").read_text())
    sp = json.loads((HERE / "impact_coeffs.CANDIDATE-2026-10-14-spread-power.json").read_text())
    b = ("high_liq", "mid_liq", "low_liq")
    return {
        # production file, exactly as get_impact_coeff resolves it (per-name, then bucket)
        "current": _fit({k: cur["buckets"][k]["coeff"] for k in b}, cur.get("per_name")),
        # 2026-10-14 pooled candidate, same resolution
        "cand_pooled": _fit({k: v2["buckets"][k]["coeff"] for k in b}, v2.get("per_name")),
        # 2026-10-14 "latest block" candidate (beta=1, buckets only): the most conservative
        "cand_latest": _fit({k: sp["no_code_change_alternative"][k] for k in b}, None),
    }


FITS = _load_fits()


def _coeff(adv: np.ndarray, fit: dict, symbol: str | None) -> np.ndarray:
    if symbol is not None and symbol in fit["per_name"]:
        return np.full(adv.shape, fit["per_name"][symbol])
    out = np.full(adv.shape, np.nan)
    ok = np.isfinite(adv) & (adv > 0)
    names = np.array([liquidity_bucket(a) if o else "" for a, o in zip(adv, ok)])
    for k, v in fit["buckets"].items():
        out[names == k] = v
    return out


def impact_bps(vol_bps: np.ndarray, adv: np.ndarray, q_idr: np.ndarray | float, fit: dict,
               symbol: str | None = None) -> np.ndarray:
    coeff = _coeff(adv, fit, symbol)
    part = np.clip(np.asarray(q_idr, dtype=float) / np.where(adv > 0, adv, np.nan), 0.0, 1.0)
    return 2.0 * coeff * vol_bps * np.sqrt(part)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bars:
    symbol: str
    df: pd.DataFrame            # date(str), open, high, low, close, volume, value, net_foreign + vol60, adv60


def load_bars(symbol: str, as_of: str) -> Bars | None:
    p = DATA_DIR / f"{symbol}.parquet"
    df = pd.read_parquet(p)
    df["date"] = df["date"].astype(str)
    for c in ("open", "close", "volume", "value", "net_foreign"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "volume"])
    df = df[df["date"] <= as_of].sort_values("date").reset_index(drop=True)   # drops the 10-15 placeholder
    if len(df) < 100:
        return None
    lr = np.log(df["close"]).diff()
    df["vol60"] = lr.rolling(59, min_periods=19).std(ddof=1) * 1e4
    turn = df["value"] if ("value" in df and (df["value"] > 0).any()) else df["close"] * df["volume"]
    df["adv60"] = turn.rolling(60, min_periods=20).median()
    return Bars(symbol, df)


def discover_universe(as_of: str) -> list[str]:
    names = []
    for p in sorted(DATA_DIR.glob("*.parquet")):
        d = pd.read_parquet(p, columns=["date", "volume"])
        d["date"] = d["date"].astype(str)
        d = d[(d["date"] <= as_of) & (d["volume"] > 0)]
        if len(d) and d["date"].iloc[-1] >= _days_before(as_of, 7):
            names.append(p.stem)
    return names


def _days_before(iso: str, n: int) -> str:
    return (pd.Timestamp(iso) - pd.Timedelta(days=n)).strftime("%Y-%m-%d")


def detect_as_of() -> str:
    """Latest date that carries real volume for a clear majority of names (drops the in-progress bar)."""
    counts: dict[str, int] = {}
    for p in DATA_DIR.glob("*.parquet"):
        d = pd.read_parquet(p, columns=["date", "volume"])
        d = d[d["volume"] > 0]
        if len(d):
            k = str(d["date"].iloc[-1])
            counts[k] = counts.get(k, 0) + 1
    return max(counts, key=lambda k: (counts[k] > 20, k))


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def _slice(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def trades_for(bars: Bars, family: str, hold: int, start: str, end: str, *, tradable_only: bool = False
               ) -> tuple[pd.DataFrame, np.ndarray]:
    """Sequential long-only trades of one cell in [start, end] + that name's always-long null series.

    Returns (trades, null_fwd) where trades has columns sdate, edate, epx, gross_bps, vol60, adv60 (at s).
    The window is sliced BEFORE signals are computed (as the harness does), so warm-up/forward windows
    never straddle the boundary.
    """
    w = _slice(bars.df, start, end)
    empty = pd.DataFrame(columns=["sdate", "edate", "epx", "gross_bps", "vol60", "adv60"])
    if len(w) < hold + 25:
        return empty, np.array([])
    if family in SINGLE_NAME_SIGNAL_FNS:
        sig = SINGLE_NAME_SIGNAL_FNS[family](w, hold=hold)
        elig = long_only_mask(sig["signal"].to_numpy(dtype=float))
        kept = deoverlap_sequential(sig, hold=hold, eligible=elig)
        null_fwd = sig["forward_return"].to_numpy(dtype=float)
        pos_of = {d: i for i, d in enumerate(w["date"])}
        idx_s = np.array([pos_of[d] for d in kept["date"]], dtype=int)
    else:
        det = EVENT_DETECTORS[family]
        ev = det(w, bars.symbol, hold)
        # null = all priced days (same entry/hold), from the single-name frame
        from strategy_fit.entry_convention import forward_return_series
        null_fwd = forward_return_series(w, hold).to_numpy(dtype=float)
        if len(ev) == 0:
            return empty, null_fwd[np.isfinite(null_fwd)]
        kept = deoverlap_sequential(
            ev, hold=hold, eligible=long_only_mask(ev["signal"].to_numpy(dtype=float)), pos_col="pos"
        )
        idx_s = kept["pos"].to_numpy(dtype=int)
    null_fwd = null_fwd[np.isfinite(null_fwd)]
    fwd = kept["forward_return"].to_numpy(dtype=float)
    idx_e = idx_s + 1
    idx_x = idx_s + 1 + hold
    ok = np.isfinite(fwd) & (idx_x < len(w))
    if tradable_only:
        vol = w["volume"].to_numpy(dtype=float)
        ok &= (vol[np.minimum(idx_e, len(w) - 1)] > 0) & (vol[np.minimum(idx_x, len(w) - 1)] > 0)
    idx_s, idx_e, fwd = idx_s[ok], idx_e[ok], fwd[ok]
    out = pd.DataFrame({
        "sdate": w["date"].to_numpy()[idx_s],
        "edate": w["date"].to_numpy()[idx_e],
        "epx": w["close"].to_numpy(dtype=float)[idx_e],
        "gross_bps": fwd * 1e4,
        "vol60": w["vol60"].to_numpy(dtype=float)[idx_s],
        "adv60": w["adv60"].to_numpy(dtype=float)[idx_s],
    })
    return out, null_fwd


def add_costs(tr: pd.DataFrame, symbol: str | None) -> pd.DataFrame:
    """cost columns, bps: spread+fee (floor), impact at harness notional per fit, impact at 1 lot."""
    t = tr.copy()
    px = t["epx"].to_numpy(dtype=float)
    t["floor_bps"] = np.array([min_spread_bps(p) for p in px]) + FEE_RT_BPS
    lot = px * SHARES_PER_LOT
    for k, fit in FITS.items():
        t[f"imp_{k}"] = impact_bps(t["vol60"].to_numpy(), t["adv60"].to_numpy(), POSITION_NOTIONAL_IDR, fit, symbol)
        # 1 lot is ~0.01-0.5% of ADV: where ADV/vol history is missing (early listing) charge 0, not NaN
        t[f"imp1_{k}"] = np.nan_to_num(impact_bps(t["vol60"].to_numpy(), t["adv60"].to_numpy(), lot, fit, symbol))
    return t


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def welch_edge_t(trade_bps: np.ndarray, null_bps: np.ndarray, hold: int) -> tuple[float, float]:
    """(edge, t) of trade mean vs all-day mean; null effective N = days / max(hold,1)."""
    n = len(trade_bps)
    if n < 3 or len(null_bps) < 10:
        return float("nan"), float("nan")
    edge = float(trade_bps.mean() - null_bps.mean())
    n_null = max(len(null_bps) / max(hold, 1), 2.0)
    se = math.sqrt(trade_bps.var(ddof=1) / n + null_bps.var(ddof=1) / n_null)
    return edge, (edge / se if se > 0 else float("nan"))


def cluster_t(values: np.ndarray, clusters: np.ndarray) -> tuple[float, float, float]:
    """mean, cluster-robust SE, t for the mean of ``values`` with cluster-summed residuals."""
    n = len(values)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    m = float(values.mean())
    u = pd.Series(values - m).groupby(clusters).sum().to_numpy()
    se = math.sqrt(float((u ** 2).sum())) / n
    return m, se, (m / se if se > 0 else float("nan"))


def norm_sf(t: float) -> float:
    return 0.5 * math.erfc(t / math.sqrt(2.0)) if np.isfinite(t) else float("nan")


def drop_top(values: np.ndarray, k: int = TOP_K) -> float:
    if len(values) <= k:
        return float("nan")
    return float(np.sort(values)[:-k].mean())


# ---------------------------------------------------------------------------
# Name-level rows
# ---------------------------------------------------------------------------

def monthly_entries(tr: pd.DataFrame, months: float) -> float:
    return len(tr) / months if months > 0 else float("nan")


def n_months(start: str, end: str) -> float:
    return (pd.Timestamp(end) - pd.Timestamp(start)).days / 30.4375


def current_state(bars: Bars, family: str, hold: int, as_of: str) -> dict:
    """Signal state as of the latest complete bar (as_of), mirroring the emitter's chain
    (anchored at HISTORY_START, sequential, long-only). Today's bar is not used."""
    w = _slice(bars.df, HISTORY_START, as_of)
    last = len(w) - 1
    if family in SINGLE_NAME_SIGNAL_FNS:
        sig = SINGLE_NAME_SIGNAL_FNS[family](w, hold=hold, drop_unpriced=False)
        elig = long_only_mask(sig["signal"].to_numpy(dtype=float))
        kept = deoverlap_sequential(sig, hold=hold, eligible=elig)
        pos_of = {d: i for i, d in enumerate(w["date"])}
        kidx = np.array([pos_of[d] for d in kept["date"]], dtype=int)
        sig_today = bool(len(sig) and sig["date"].iloc[-1] == w["date"].iloc[-1] and elig[-1])
    else:
        return {"state": "n/a (event)", "detail": ""}
    if len(kidx) == 0:
        return {"state": "flat", "detail": "no entries"}
    i = int(kidx[-1])
    exit_pos = i + 1 + hold          # session index on which the position is sold
    if i == last:
        return {"state": "ENTER next session",
                "detail": f"long signal on {w['date'].iloc[last]} close; entry close_{{s+1}}, exit {hold} sessions later"}
    if exit_pos > last + 1:
        left = exit_pos - (last + 1)
        return {"state": f"IN POSITION (exit in {left} session(s))",
                "detail": f"entered after signal {w['date'].iloc[i]}"}
    return {"state": "flat (signal fires only on a positive close; last entry "
                     f"{w['date'].iloc[i]})", "detail": ""}


def p_entry_in_next(tr_w1: pd.DataFrame, bars: Bars, family: str, hold: int, horizon: int = 10) -> float:
    """Empirical P(at least one sequential entry within any `horizon`-session window), last 12 months."""
    w = _slice(bars.df, _days_before(str(bars.df["date"].iloc[-1]), 365), str(bars.df["date"].iloc[-1]))
    if len(w) < 50:
        return float("nan")
    tr, _ = trades_for(bars, family, hold, str(w["date"].iloc[0]), str(w["date"].iloc[-1]))
    if len(tr) == 0:
        return 0.0
    pos = {d: i for i, d in enumerate(w["date"])}
    ent = sorted(pos[d] for d in tr["sdate"] if d in pos)
    n = len(w)
    hits = sum(any(s <= e < s + horizon for e in ent) for s in range(0, max(n - horizon, 1)))
    return hits / max(n - horizon, 1)


def name_row(bars: Bars, family: str, hold: int, as_of: str, months_w1: float) -> dict:
    start = HISTORY_START
    tr, null = trades_for(bars, family, hold, start, as_of)
    tr = add_costs(tr, bars.symbol)
    tr = tr[np.isfinite(tr["imp_current"]) & np.isfinite(tr["imp_cand_latest"])].reset_index(drop=True)  # harness: unpriced trades excluded
    last_px = float(bars.df["close"].iloc[-1])
    row: dict = {"symbol": bars.symbol, "family": family, "hold": hold, "last_close": last_px,
                 "n": len(tr), "entries_per_month": monthly_entries(tr, months_w1)}
    if len(tr) < 5:
        return row
    g = tr["gross_bps"].to_numpy()
    nul = float(null.mean() * 1e4)
    edge, t_edge = welch_edge_t(g, null * 1e4, hold)
    floor = tr["floor_bps"].to_numpy()
    row.update(gross=float(g.mean()), null=nul, edge=edge, t_edge=t_edge,
               median=float(np.median(g)), hit=float((g > 0).mean()), sd=float(g.std(ddof=1)),
               floor=float(floor.mean()))
    for k in FITS:
        c = floor + tr[f"imp_{k}"].to_numpy()
        row[f"cost_{k}"] = float(c.mean())
        row[f"net_{k}"] = float((g - c).mean())
        row[f"edgenet_{k}"] = float(edge - c.mean())
    c1 = floor + tr["imp1_cand_latest"].to_numpy()
    row["cost_1lot"] = float(c1.mean())
    row["net_1lot"] = float((g - c1).mean())
    row["edgenet_1lot"] = float(edge - c1.mean())
    c_worst = floor + tr["imp_cand_latest"].to_numpy()
    row["drop5_net_cand"] = drop_top(g - c_worst)
    row["drop5_net_1lot"] = drop_top(g - c1)
    row["drop5_gross"] = drop_top(g)
    # selection-independent OOS: same cell, 2015-2023 (name-level), tradable bars only
    pre, pre_null = trades_for(bars, family, hold, PRE_START, PRE_END, tradable_only=True)
    if len(pre) >= 5 and len(pre_null) >= 30:
        pre = add_costs(pre, bars.symbol)
        pg = pre["gross_bps"].to_numpy()
        e0, t0 = welch_edge_t(pg, pre_null * 1e4, hold)
        row.update(pre_n=len(pre), pre_gross=float(pg.mean()), pre_null=float(pre_null.mean() * 1e4),
                   pre_edge=e0, pre_t=t0,
                   pre_net_1lot=float((pg - pre["floor_bps"].to_numpy() - pre["imp1_cand_latest"].to_numpy()).mean()),
                   pre_drop5_edgenet_1lot=drop_top(pg - float(pre_null.mean() * 1e4)
                                                   - pre["floor_bps"].to_numpy() - pre["imp1_cand_latest"].to_numpy()))
    # harness walk-forward OOS (60/40 by rows) for continuity with the grid
    w = _slice(bars.df, start, as_of)
    try:
        wf = walk_forward_daily(
            df=w, signal_fn=SINGLE_NAME_SIGNAL_FNS[family] if family in SINGLE_NAME_SIGNAL_FNS else None,
            symbol=bars.symbol, min_edge_to_clear_bps=min_spread_bps(last_px) + FEE_RT_BPS,
            family_name=family, horizon_trading_days=hold,
        ) if family in SINGLE_NAME_SIGNAL_FNS else None
    except Exception:  # noqa: BLE001 - report, don't hide
        wf = None
    if wf is not None:
        row.update(wf_oos_edge=wf.oos_edge_bps, wf_oos_n=wf.oos_eff_n)
    # state, rupiah costs
    row.update(current_state(bars, family, hold, as_of))
    row["p_entry_10d"] = p_entry_in_next(tr, bars, family, hold)
    lot = last_px * SHARES_PER_LOT
    tick = tick_size(last_px)
    fee_rt = lot * (FEE_BUY + FEE_SELL)
    spread = tick * SHARES_PER_LOT
    row.update(lot_notional=lot, tick=tick, fee_rt_idr=fee_rt, spread_idr=spread,
               trip_cost_idr=fee_rt + spread,
               month_cost_idr=(fee_rt + spread) * row["entries_per_month"])
    row.update(worst_month_idr=_worst_month(tr, hold, lot, g, c1))
    row["gates"] = gates(row)
    return row


def gates(r: dict) -> dict:
    """Blocking gates. G1/G6 hold by construction for every row here (next-session entry; daily rebalance)."""
    pe = r.get("pre_edge")
    return {
        "G2_edge_t>=2": bool(r.get("t_edge", float("nan")) >= 2.0),
        "G3_edge_net_both_fits>0": bool(min(r.get("edgenet_current", -1), r.get("edgenet_cand_latest", -1),
                                           r.get("edgenet_1lot", -1)) > 0),
        "G4_drop5_net>0": bool(r.get("drop5_net_cand", -1) > 0),
        # independent 2015-2023 window of the SAME cell: edge>0, net>0 AND still >0 without its 5 best trades
        "G5_pre2024_edge>0_and_net>0_and_drop5>0": bool(
            pe is not None and pe == pe and pe > 0 and r.get("pre_net_1lot", -1) > 0
            and r.get("pre_drop5_edgenet_1lot", -1) > 0),
        "G7_family_level_support": False,   # no family x hold has Holm p < 0.10 (see pooled rows)
    }


def _worst_month(tr: pd.DataFrame, hold: int, lot: float, g: np.ndarray, c1: np.ndarray) -> dict:
    """Empirical worst calendar month (sum of 1-lot net P&L, IDR) and a hard stress bound."""
    pnl = lot * (g - c1) / 1e4          # 1-lot P&L re-based to TODAY's lot notional
    month = pd.Series(pnl).groupby(pd.to_datetime(tr["edate"]).dt.to_period("M").to_numpy()).sum()
    stress_trip = lot * (1 - (1 - ARB_PCT) ** hold) + lot * FEE_BUY
    return {"empirical": float(month.min()) if len(month) else float("nan"),
            "worst_trade": float(pnl.min()) if len(pnl) else float("nan"),
            "stress_trip": stress_trip, "stress_cap": lot * (1 + FEE_BUY)}


# ---------------------------------------------------------------------------
# Family-level (pooled) rows
# ---------------------------------------------------------------------------

def pooled_row(all_bars: list[Bars], family: str, hold: int, start: str, end: str, tradable_only: bool) -> dict:
    parts, nulls = [], {}
    for b in all_bars:
        tr, null = trades_for(b, family, hold, start, end, tradable_only=tradable_only)
        if len(null) < 30:
            continue
        nulls[b.symbol] = float(null.mean() * 1e4)
        if len(tr):
            tr = add_costs(tr, b.symbol).assign(symbol=b.symbol, null=nulls[b.symbol])
            if start == HISTORY_START:     # harness: trades with no impact history are excluded
                tr = tr[np.isfinite(tr["imp_current"]) & np.isfinite(tr["imp_cand_latest"])]
            else:                           # pre-window: fees+spread dominate; unpriced impact charged 0
                tr = tr.assign(**{c: np.nan_to_num(tr[c]) for c in tr.columns if c.startswith("imp_")})
            parts.append(tr)
    row = {"family": family, "hold": hold, "window": f"{start}..{end}", "names": len(nulls)}
    if not parts:
        row["n"] = 0
        return row
    t = pd.concat(parts, ignore_index=True)
    row["n"] = len(t)
    row["names_with_trades"] = int(t["symbol"].nunique())
    g = t["gross_bps"].to_numpy()
    ex = g - t["null"].to_numpy()
    cl = pd.to_datetime(t["edate"]).dt.to_period("M").astype(str).to_numpy()
    m_ex, se_ex, t_ex = cluster_t(ex, cl)
    cost1 = t["floor_bps"].to_numpy() + t["imp1_cand_latest"].to_numpy()
    cost_c = t["floor_bps"].to_numpy() + t["imp_current"].to_numpy()
    cost_l = t["floor_bps"].to_numpy() + t["imp_cand_latest"].to_numpy()
    row.update(gross=float(g.mean()), null=float(t["null"].mean()), edge=m_ex, se=se_ex, t_edge=t_ex,
               p_one_sided=norm_sf(t_ex), cost_1lot=float(cost1.mean()),
               cost_current=float(cost_c.mean()), cost_cand=float(cost_l.mean()),
               net_current=float((g - cost_c).mean()), net_cand=float((g - cost_l).mean()),
               edgenet_current=float((ex - cost_c).mean()), edgenet_cand=float((ex - cost_l).mean()),
               edgenet_1lot=float((ex - cost1).mean()),
               drop5_edgenet_cand=drop_top(ex - cost_l),
               frac_names_pos=float(pd.Series(ex).groupby(t["symbol"].to_numpy()).mean().gt(0).mean()))
    m_n, se_n, t_n = cluster_t(ex - cost_l, cl)
    row.update(t_edgenet_cand=t_n)
    return row


def holm(pvals: list[float]) -> list[float]:
    """Holm step-down adjusted p-values (NaN-safe)."""
    idx = [i for i, p in enumerate(pvals) if np.isfinite(p)]
    order = sorted(idx, key=lambda i: pvals[i])
    m = len(order)
    adj = [float("nan")] * len(pvals)
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (m - r) * pvals[i]))
        adj[i] = run
    return adj


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fresh_signal_counts(all_bars: list[Bars], as_of: str) -> dict:
    """How many names have a fresh long signal on the latest complete close (single-name families)."""
    out = {}
    for fam, fn in SINGLE_NAME_SIGNAL_FNS.items():
        c = 0
        for b in all_bars:
            w = _slice(b.df, HISTORY_START, as_of)
            sig = fn(w, hold=1, drop_unpriced=False)
            if len(sig) and sig["date"].iloc[-1] == as_of and sig["signal"].iloc[-1] > 0:
                c += 1
        out[fam] = c
    return out


def _f(x, nd=1) -> str:
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.{nd}f}"


def render_markdown(out: dict) -> str:
    L: list[str] = []
    L.append(f"as_of (last complete bar) = {out['as_of']}, {out['n_names']} names\n")
    L.append("### Name-level rows (bps per round trip unless stated)\n")
    L.append("| cell | grp | n | /mo | gross | null | edge | t | cost cur/cand/1lot | edge-net cur/cand/1lot | net cand | drop5 net | pre-24 n/edge/t | pre net 1lot / drop5 | wf OOS edge/n | gates | state | P(entry<=10d) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in out["name_rows"]:
        g = r.get("gates", {})
        L.append(
            f"| {r['symbol']} {r['family']} h{r['hold']} | {r['group']} | {r['n']} | {_f(r.get('entries_per_month'))} | "
            f"{_f(r.get('gross'))} | {_f(r.get('null'))} | {_f(r.get('edge'))} | {_f(r.get('t_edge'), 2)} | "
            f"{_f(r.get('cost_current'))}/{_f(r.get('cost_cand_latest'))}/{_f(r.get('cost_1lot'))} | "
            f"{_f(r.get('edgenet_current'))}/{_f(r.get('edgenet_cand_latest'))}/{_f(r.get('edgenet_1lot'))} | "
            f"{_f(r.get('net_cand_latest'))} | {_f(r.get('drop5_net_cand'))} | "
            f"{r.get('pre_n', '-')}/{_f(r.get('pre_edge'))}/{_f(r.get('pre_t'), 2)} | "
            f"{_f(r.get('pre_net_1lot'))} / {_f(r.get('pre_drop5_edgenet_1lot'))} | "
            f"{_f(r.get('wf_oos_edge'))}/{r.get('wf_oos_n', '-')} | {sum(g.values())}/5 | {r.get('state')} | {_f(r.get('p_entry_10d'), 2)} |")
    L.append("\n### Run cost, 1 lot (IDR)\n")
    L.append("| cell | last close | lot notional | tick | fee RT | spread (1 tick) | cost/trip | trips/mo | cost/mo | worst trade (bt, today's lot) | worst month (bt) | stress month cap |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in out["name_rows"]:
        w = r.get("worst_month_idr", {})
        L.append(
            f"| {r['symbol']} {r['family']} h{r['hold']} | {_f(r['last_close'], 0)} | {_f(r.get('lot_notional'), 0)} | {r.get('tick', '-')} | "
            f"{_f(r.get('fee_rt_idr'), 0)} | {_f(r.get('spread_idr'), 0)} | {_f(r.get('trip_cost_idr'), 0)} | "
            f"{_f(r.get('entries_per_month'))} | {_f(r.get('month_cost_idr'), 0)} | {_f(w.get('worst_trade'), 0)} | "
            f"{_f(w.get('empirical'), 0)} | {_f(w.get('stress_cap'), 0)} |")
    if "pooled" in out:
        L.append("\n### Family-level (pooled over names), 2024-01-01..as_of; cost at 1 lot; Holm over 30 tests\n")
        L.append("| family | hold | trades | gross | null | edge | cluster t | Holm p | cost 1lot | edge-net 1lot | edge-net cand@3M | drop5 edge-net | frac names edge>0 | 2015-23: n / edge / t / edge-net 1lot |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        w1 = [r for r in out["pooled"] if r["window"].startswith(HISTORY_START)]
        w0 = {(r["family"], r["hold"]): r for r in out["pooled"] if r["window"].startswith(PRE_START)}
        for r in w1:
            o = w0.get((r["family"], r["hold"]), {})
            L.append(
                f"| {r['family']} | {r['hold']} | {r.get('n', 0)} | {_f(r.get('gross'))} | {_f(r.get('null'))} | {_f(r.get('edge'))} | "
                f"{_f(r.get('t_edge'), 2)} | {_f(r.get('p_holm_30'), 2)} | {_f(r.get('cost_1lot'))} | {_f(r.get('edgenet_1lot'))} | "
                f"{_f(r.get('edgenet_cand'))} | {_f(r.get('drop5_edgenet_cand'))} | {_f(r.get('frac_names_pos'), 2)} | "
                f"{o.get('n', '-')} / {_f(o.get('edge'))} / {_f(o.get('t_edge'), 2)} / {_f(o.get('edgenet_1lot'))} |")
        L.append(f"\nFresh long signals on the latest close (hold-1 frame, any chain state): {out.get('fresh_signals')}")
        L.append(f"Single-name cells in the universe grid (names x holds x families) = {out.get('grid_cells_single_name')}")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="out_shortlist")
    ap.add_argument("--as-of", default=None, help="last COMPLETE bar date (default: detected)")
    ap.add_argument("--skip-pooled", action="store_true")
    args = ap.parse_args(argv)

    as_of = args.as_of or detect_as_of()
    universe = discover_universe(as_of)
    bars = {s: b for s in universe if (b := load_bars(s, as_of)) is not None}
    all_bars = list(bars.values())
    months_w1 = n_months(HISTORY_START, as_of)
    print(f"as_of={as_of} universe={len(all_bars)} names; W1 months={months_w1:.1f}")

    out: dict = {"as_of": as_of, "n_names": len(all_bars), "fits": FITS,
                 "harness_notional_idr": POSITION_NOTIONAL_IDR}

    rows = []
    for sym, fam, hold in MECHANICAL_SURVIVORS + KILLED_CONTROLS:
        if sym not in bars:
            print("missing", sym)
            continue
        r = name_row(bars[sym], fam, hold, as_of, months_w1)
        r["group"] = "survivor" if (sym, fam, hold) in MECHANICAL_SURVIVORS else "killed_control"
        rows.append(r)
        print(f"{sym:5s} {fam:16s} h{hold:<2d} n={r['n']:3d} gross={r.get('gross', float('nan')):7.1f} "
              f"edge={r.get('edge', float('nan')):7.1f} t={r.get('t_edge', float('nan')):5.2f} "
              f"d5net={r.get('drop5_net_cand', float('nan')):7.1f} pre_edge={r.get('pre_edge', float('nan')):7.1f} "
              f"state={r.get('state')}")
    out["name_rows"] = rows

    if not args.skip_pooled:
        pooled = []
        for fam in FAMILIES:
            for hold in HOLDS:
                for (a, b_, tradable) in ((HISTORY_START, as_of, False), (PRE_START, PRE_END, True)):
                    r = pooled_row(all_bars, fam, hold, a, b_, tradable)
                    pooled.append(r)
                print(f"pooled {fam} h{hold}")
        w1 = [r for r in pooled if r["window"].startswith(HISTORY_START)]
        adj = holm([r.get("p_one_sided", float("nan")) for r in w1])
        for r, a in zip(w1, adj):
            r["p_holm_30"] = a
        out["pooled"] = pooled
        out["fresh_signals"] = fresh_signal_counts(all_bars, as_of)

        # multiple-testing context for the name-level picks: size of the grid they came from
        grid_cells = len(all_bars) * len(HOLDS) * len(SINGLE_NAME_SIGNAL_FNS)
        out["grid_cells_single_name"] = grid_cells
        for r in rows:
            t = r.get("t_edge", float("nan"))
            r["expected_cells_ge_t_by_chance"] = grid_cells * norm_sf(t) if np.isfinite(t) else float("nan")

    od = Path(args.out_dir)
    od.mkdir(parents=True, exist_ok=True)
    (od / "shortlist.json").write_text(json.dumps(out, indent=1, default=float))
    (od / "shortlist.md").write_text(render_markdown(out))
    print("wrote", od / "shortlist.json")


if __name__ == "__main__":
    main()
