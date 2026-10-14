"""
strategy_fit/test_xsec_point_in_time_impact.py — cross-sectional per-period
impact is priced point-in-time (trailing 60-bar ADV/vol ending at each
rebalance day), not from one full-history median.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit import impact
from strategy_fit.compare_d1_xsec import period_impact_stats


def _hist(n_names: int = 8, later_mult: float = 65.0) -> dict[str, pd.DataFrame]:
    out = {}
    for i in range(n_names):
        rng = np.random.default_rng(20 + i)
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
        volume = np.concatenate([np.full(150, 50_000.0), np.full(150, 50_000.0 * later_mult)])
        out[f"S{i}"] = pd.DataFrame(
            {"date": pd.bdate_range("2024-01-01", periods=300).strftime("%Y-%m-%d"), "close": close, "volume": volume}
        )
    return out


def test_per_period_charge_is_not_the_stale_full_history_number():
    hist = _hist()
    stats = period_impact_stats(hist, hold=2, entry="next_close", use_calibrated=False)
    legacy = float(np.median([impact.compute_impact_bps_from_daily_df(df, symbol=s) for s, df in hist.items()]))
    assert stats["n"] > 0
    assert stats["mean"] != pytest.approx(legacy)


def test_periods_before_enough_history_are_excluded_and_counted():
    stats = period_impact_stats(_hist(), hold=2, entry="next_close", use_calibrated=False)
    # the first ~20 rebalance days cannot be priced from trailing data
    assert stats["excluded"] > 0
    assert np.isfinite(stats["mean"])


def test_future_volume_does_not_move_the_charge_on_a_common_prefix():
    # Same first 140 bars, wildly different later volume: the charge computed on
    # the prefix of the longer history at those dates must equal the prefix-only one.
    a = _hist(later_mult=65.0)
    b = _hist(later_mult=1.0)
    for sym in a:
        ma = impact.TrailingImpactModel(a[sym], symbol=sym)
        mb = impact.TrailingImpactModel(b[sym], symbol=sym)
        d = a[sym]["date"].iloc[120]
        assert ma.impact_bps(d) == mb.impact_bps(d)
