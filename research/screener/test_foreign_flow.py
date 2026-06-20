"""
screener/test_foreign_flow.py — TDD tests for foreign-flow extraction (Change 2).

Tests that:
1. FixtureDataSource returns net_foreign column (via updated fixtures).
2. StrategyFitFixtureDataSource returns net_foreign column.
3. foreign_flow_spike_event detector uses net_foreign (not foreignflow).
4. Synthetic net_foreign spikes are detected; flat series produces no events.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# StrategyFitFixtureDataSource emits net_foreign
# ---------------------------------------------------------------------------

class TestStrategyFitFixtureNetForeign:
    """The offline fixture DataSource must emit net_foreign column."""

    def test_daily_history_has_net_foreign_column(self):
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df = ds.daily_history("ITMG", "2025-01-01", "2026-12-31")
        assert "net_foreign" in df.columns, (
            "StrategyFitFixtureDataSource.daily_history must return net_foreign column"
        )

    def test_net_foreign_is_numeric(self):
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df = ds.daily_history("ITMG", "2025-01-01", "2026-12-31")
        assert pd.api.types.is_numeric_dtype(df["net_foreign"]), (
            "net_foreign column must be numeric"
        )

    def test_net_foreign_is_deterministic(self):
        """Two calls with same symbol must return identical net_foreign."""
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df1 = ds.daily_history("BUMI", "2025-01-01", "2026-12-31")
        df2 = ds.daily_history("BUMI", "2025-01-01", "2026-12-31")
        pd.testing.assert_series_equal(df1["net_foreign"], df2["net_foreign"])

    def test_all_symbols_have_net_foreign(self):
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource, UNIVERSE_SYMBOLS
        ds = StrategyFitFixtureDataSource()
        for sym in UNIVERSE_SYMBOLS:
            df = ds.daily_history(sym, "2025-01-01", "2026-12-31")
            assert "net_foreign" in df.columns, (
                f"{sym} missing net_foreign column"
            )


# ---------------------------------------------------------------------------
# foreign_flow_spike_event detector uses net_foreign column
# ---------------------------------------------------------------------------

def _make_df_with_net_foreign(
    n: int = 60,
    spike_indices: list[int] | None = None,
    spike_magnitude: float = 1e9,
) -> pd.DataFrame:
    """
    Build a synthetic OHLCV + net_foreign DataFrame.

    spike_indices: rows where net_foreign is set to spike_magnitude.
    All other rows have net_foreign = 0.
    """
    rng = np.random.default_rng(42)
    close = 10_000.0 + rng.normal(0, 100, size=n).cumsum()
    close = np.clip(close, 1_000, None)
    volume = (5_000_000 + rng.normal(0, 500_000, size=n)).astype(int)
    net_foreign = np.zeros(n)
    if spike_indices:
        for idx in spike_indices:
            net_foreign[idx] = spike_magnitude
    dates = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": volume,
            "net_foreign": net_foreign,
        }
    )


class TestForeignFlowSpikeDetector:
    """foreign_flow_spike_event must use net_foreign column."""

    def test_spike_in_net_foreign_triggers_event(self):
        """A strong spike in net_foreign at known indices must produce events."""
        from strategy_fit.families_event import _detect_foreign_flow_spike_events
        # Place spikes well separated so de-overlap doesn't eat them all.
        spike_indices = [25, 50]
        df = _make_df_with_net_foreign(n=80, spike_indices=spike_indices, spike_magnitude=5e9)
        events = _detect_foreign_flow_spike_events(df, symbol="TEST", hold=1)
        assert len(events) >= 1, (
            "Spikes in net_foreign must produce at least 1 detected event"
        )

    def test_flat_net_foreign_produces_no_events(self):
        """A perfectly flat (zero) net_foreign must not trigger any events."""
        from strategy_fit.families_event import _detect_foreign_flow_spike_events
        df = _make_df_with_net_foreign(n=60, spike_indices=None)
        events = _detect_foreign_flow_spike_events(df, symbol="TEST", hold=1)
        # Flat series: std=0 → z-score undefined → no events.
        assert len(events) == 0, (
            "Flat net_foreign (all zeros) must produce no events"
        )

    def test_uses_net_foreign_not_foreignflow(self):
        """Detector must read net_foreign, not the old foreignflow column."""
        from strategy_fit.families_event import _detect_foreign_flow_spike_events
        # Build df WITHOUT net_foreign — must return empty DataFrame.
        n = 60
        rng = np.random.default_rng(0)
        df_no_col = pd.DataFrame({
            "date": pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
            "close": 10_000 + rng.normal(0, 100, n).cumsum(),
            "volume": np.ones(n) * 5_000_000,
            "foreignflow": rng.normal(0, 1e8, n),  # old column, should be ignored
        })
        events = _detect_foreign_flow_spike_events(df_no_col, symbol="TEST", hold=1)
        assert len(events) == 0, (
            "Detector must not use old 'foreignflow' column; must return empty when net_foreign absent"
        )

    def test_pooled_event_family_detects_net_foreign_spikes(self):
        """run_event_family with injected net_foreign spikes must reach MIN_EFF_N."""
        from strategy_fit.families_event import run_event_family
        # Build multiple symbols each with strong spikes, enough for MIN_EFF_N=30.
        hist_dict: dict[str, pd.DataFrame] = {}
        for sym_idx in range(5):
            spike_idx = list(range(5, 75, 8))  # ~8 spikes spread across 80 rows
            df = _make_df_with_net_foreign(n=80, spike_indices=spike_idx, spike_magnitude=5e9)
            hist_dict[f"SYM{sym_idx}"] = df
        result = run_event_family(
            hist_dict=hist_dict,
            event_detector_name="foreign_flow_spike_event",
            horizon_trading_days=1,
        )
        assert result["status"] == "tested", (
            f"Expected status='tested' with injected spikes, got: {result['status']} "
            f"({result.get('skip_reason')})"
        )
        assert (result["eff_n"] or 0) >= 30, (
            f"Expected eff_n >= 30, got {result['eff_n']}"
        )
