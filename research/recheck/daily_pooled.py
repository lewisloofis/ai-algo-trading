"""
recheck/daily_pooled.py — post-verdict trades of the pooled daily and event families (plan 26).

Reuses the shortlist's pieces unchanged: the family signal / detector functions
(``SINGLE_NAME_SIGNAL_FNS``, ``EVENT_DETECTORS``), ``harness.deoverlap_sequential``, the entry convention
(``next_close``), ``long_only_mask``, ``add_costs`` (tick + 40 bps fees + impact at the harness notional)
and the production ``impact_coeffs.json`` fit (``FITS["current"]``).

What differs from ``shortlist_2026_10.trades_for``: that function slices its [start, end] window BEFORE it
computes signals, which anchors the sequential chain in pre-verdict history. Here the signals are computed
on a frame that starts ``LOOKBACK_BARS`` rows before the first post-verdict bar (so the trailing windows are
filled), the signal rows dated on or before the verdict are then discarded, and the sequential de-overlap
chain starts fresh on the first post-verdict signal. Pre-verdict bars therefore enter only as the trailing
lookback of the signal and of vol60 / adv60.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from recheck.loaders import load_universe_bars
from strategy_fit.entry_convention import forward_return_series
from strategy_fit.families_event import EVENT_DETECTORS
from strategy_fit.harness import deoverlap_sequential
from strategy_fit.long_only import long_only_mask
from strategy_fit.run_strategy_fit import SINGLE_NAME_SIGNAL_FNS
from strategy_fit.shortlist_2026_10 import add_costs

LOOKBACK_BARS = 61
"""Trailing bars read before the first post-verdict signal: vol60 / adv60 need 60, signals at most 20."""

EMPTY = pd.DataFrame(columns=["signal_date", "entry_date", "gross_bps", "null_bps", "cost_bps", "symbol"])


def _name_trades(df: pd.DataFrame, symbol: str, detector_key: str, hold: int, start_excl: str):
    """(trades, null_fwd) for one name: post-verdict sequential long-only trades and its always-long null."""
    s0 = int(np.searchsorted(df["date"].to_numpy(dtype=str), start_excl, side="right"))
    if s0 >= len(df):
        return None, np.array([])
    w = df.iloc[max(0, s0 - LOOKBACK_BARS):].reset_index(drop=True)
    if detector_key in SINGLE_NAME_SIGNAL_FNS:
        sig = SINGLE_NAME_SIGNAL_FNS[detector_key](w, hold=hold)
        post = sig[sig["date"] > start_excl].reset_index(drop=True)
        null_fwd = post["forward_return"].to_numpy(dtype=float)
        kept = deoverlap_sequential(post, hold=hold, eligible=long_only_mask(post["signal"].to_numpy(dtype=float)))
        pos_of = {d: i for i, d in enumerate(w["date"])}
        idx_s = np.array([pos_of[d] for d in kept["date"]], dtype=int)
    else:
        fr = forward_return_series(w, hold).to_numpy(dtype=float)
        null_fwd = fr[(w["date"] > start_excl).to_numpy() & np.isfinite(fr)]
        ev = EVENT_DETECTORS[detector_key](w, symbol, hold)
        ev = ev[ev["date"] > start_excl].reset_index(drop=True)
        if ev.empty:
            return None, null_fwd
        kept = deoverlap_sequential(
            ev, hold=hold, eligible=long_only_mask(ev["signal"].to_numpy(dtype=float)), pos_col="pos")
        idx_s = kept["pos"].to_numpy(dtype=int)
    fwd = kept["forward_return"].to_numpy(dtype=float)
    idx_e, idx_x = idx_s + 1, idx_s + 1 + hold
    ok = np.isfinite(fwd) & (idx_x < len(w))
    idx_s, idx_e, fwd = idx_s[ok], idx_e[ok], fwd[ok]
    if len(idx_s) == 0:
        return None, null_fwd
    trades = pd.DataFrame({
        "sdate": w["date"].to_numpy()[idx_s],
        "edate": w["date"].to_numpy()[idx_e],
        "epx": w["close"].to_numpy(dtype=float)[idx_e],
        "gross_bps": fwd * 1e4,
        "vol60": w["vol60"].to_numpy(dtype=float)[idx_s],
        "adv60": w["adv60"].to_numpy(dtype=float)[idx_s],
    })
    return trades, null_fwd[np.isfinite(null_fwd)]


def pooled_builder(detector_key: str, hold: int) -> Callable[[str, date, date], pd.DataFrame]:
    """Records of one pooled family at one frozen hold, over every name in the universe."""

    def build(daily_dir: str, signal_start_exclusive: date, as_of: date) -> pd.DataFrame:
        start = signal_start_exclusive.isoformat()
        parts = []
        for symbol, df in load_universe_bars(daily_dir, as_of).items():
            trades, null_fwd = _name_trades(df, symbol, detector_key, hold, start)
            if trades is None or len(null_fwd) == 0:
                continue
            t = add_costs(trades, symbol)
            t = t[np.isfinite(t["imp_current"])]           # the harness drops trades with no impact history
            if t.empty:
                continue
            parts.append(pd.DataFrame({
                "signal_date": t["sdate"].to_numpy(), "entry_date": t["edate"].to_numpy(),
                "gross_bps": t["gross_bps"].to_numpy(), "null_bps": float(null_fwd.mean() * 1e4),
                "cost_bps": (t["floor_bps"] + t["imp_current"]).to_numpy(), "symbol": symbol,
            }))
        return pd.concat(parts, ignore_index=True) if parts else EMPTY.copy()

    return build
