"""
recheck/xsec.py — post-verdict periods of the cross-sectional family (plan 10 D1 re-score; plan 26).

Reuses ``families_cross_sectional.compute_period_returns`` / ``select_rebalance_periods`` (1-day trailing
return rank, long-only top quintile, equal-weight universe null, ``next_close`` entry, sequential
re-rank on the exit day) and the cost recipe of ``compare_d1_xsec.rows``: median per-name floor
(one tick + 40 bps) plus the per-rebalance-day median of the trailing point-in-time impact
(``TrailingImpactModel``, calibrated coefficients), through ``best_feasible_hurdle_bps`` (financing at the
cheapest entry weekday). The full round trip is charged every period, as in the verdict.

The frame is cut at the last bar on or before the verdict (one row of lookback for the 1-day factor);
periods dated on or before the verdict are dropped and the sequential chain starts on the first
post-verdict period. Periods where no name can be priced for impact are left out and counted in ``attrs``.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from recheck.loaders import load_universe_bars
from strategy_fit import impact
from strategy_fit.entry_convention import ENTRY_NEXT_CLOSE
from strategy_fit.families_cross_sectional import compute_period_returns, select_rebalance_periods
from strategy_fit.financing import best_feasible_hurdle_bps
from strategy_fit.run_strategy_fit import ROUND_TRIP_BPS, _min_spread_bps

IMPACT_LOOKBACK_BARS = 61
"""Trailing bars the per-name impact model reads before the first post-verdict rebalance day."""


def xsec_builder(hold: int) -> Callable[[str, date, date], pd.DataFrame]:
    def build(daily_dir: str, signal_start_exclusive: date, as_of: date) -> pd.DataFrame:
        start = signal_start_exclusive.isoformat()
        bars = load_universe_bars(daily_dir, as_of)
        empty = pd.DataFrame(columns=["signal_date", "entry_date", "gross_bps", "null_bps", "cost_bps"])
        calendar = sorted({d for df in bars.values() for d in df["date"]})
        pre = [d for d in calendar if d <= start]
        if not pre or calendar[-1] <= start:
            return empty
        cutoff = pre[-1]
        hist = {s: df[df["date"] >= cutoff][["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
                for s, df in bars.items()}
        hist = {s: df for s, df in hist.items() if len(df)}
        periods = compute_period_returns(hist, hold, ENTRY_NEXT_CLOSE)
        periods = periods[periods["date"] > start].reset_index(drop=True)
        periods = select_rebalance_periods(periods, hold, ENTRY_NEXT_CLOSE)
        if periods.empty:
            return empty
        cal = sorted({d for df in hist.values() for d in df["date"]})
        median_floor = float(np.median([_min_spread_bps(float(df["close"].iloc[-1])) + ROUND_TRIP_BPS
                                        for df in bars.values()]))
        models = {}
        for s, df in bars.items():
            s0 = int(np.searchsorted(df["date"].to_numpy(dtype=str), start, side="right"))
            models[s] = impact.TrailingImpactModel(
                df.iloc[max(0, s0 - IMPACT_LOOKBACK_BARS):], symbol=s, use_calibrated=True)
        rows = []
        for r in periods.itertuples():
            vals = []
            for m in models.values():
                try:
                    vals.append(m.impact_bps(r.date))
                except impact.InsufficientImpactHistory:
                    continue
            if not vals:
                continue
            hurdle, _ = best_feasible_hurdle_bps(
                min_edge_to_clear_bps=median_floor, modelled_impact_bps=float(np.median(vals)), hold_trading_days=hold)
            rows.append((r.date, cal[int(r.pos) + 1], r.long_ret * 1e4, r.null_ret * 1e4, hurdle))
        return pd.DataFrame(rows, columns=list(empty.columns)) if rows else empty

    return build
