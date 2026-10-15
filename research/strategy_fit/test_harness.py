"""Tests for strategy_fit.harness — TDD RED phase.

Covers:
- Horizon ceiling enforcement (hold > 2 → skipped_horizon)
- Effective-N counting by shape:
    event_driven    → pooled non-overlapping events across all symbols
    single_name     → per-symbol de-overlapped rows
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.capital_model import LEVERAGED_SLEEVE, OWNED_CASH
from strategy_fit.harness import (
    MAX_HOLD_TRADING_DAYS,
    SHAPE_EVENT_DRIVEN,
    SHAPE_SINGLE_NAME,
    StrategyFamily,
    deoverlap_daily,
    effective_n_event_pooled,
    effective_n_single_name_per_symbol,
    reject_horizon_exceeded,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _make_event_df(n: int, symbol: str, hold: int = 1) -> pd.DataFrame:
    """Synthetic event DataFrame for pooled tests."""
    dates = pd.date_range("2025-01-01", periods=n * hold * 2, freq="B")[:n]
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "signal": rng.standard_normal(n),
            "forward_return": rng.standard_normal(n),
        }
    )


def _make_daily_df(n: int, hold: int = 1) -> pd.DataFrame:
    """Synthetic single-name daily DataFrame."""
    dates = pd.date_range("2025-01-01", periods=n * hold * 3, freq="B")[:n]
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        {
            "date": dates,
            "signal": rng.standard_normal(n),
            "forward_return": rng.standard_normal(n),
        }
    )


# ---------------------------------------------------------------------------
# Horizon ceiling tests
# ---------------------------------------------------------------------------

class TestHorizonCeiling:
    """
    ADR-0015 (2026-09-13): leverage is optional and the default capital model
    (OWNED_CASH) has no horizon ceiling.  Tests that exercise the ceiling
    itself pass ``capital_model=LEVERAGED_SLEEVE`` explicitly to keep pinning
    the ADR-0011 ~2-trading-day ceiling regardless of the default.
    """

    def test_max_hold_constant_is_2_under_leveraged_sleeve(self):
        assert LEVERAGED_SLEEVE.max_hold_trading_days == 2

    def test_deprecated_module_constant_reflects_active_default(self):
        """MAX_HOLD_TRADING_DAYS snapshots ACTIVE_CAPITAL_MODEL (OWNED_CASH) at import time."""
        assert MAX_HOLD_TRADING_DAYS is None

    def test_hold_3_is_rejected(self):
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=3,
        )
        result = reject_horizon_exceeded(family, capital_model=LEVERAGED_SLEEVE)
        assert result is not None
        assert result["status"] == "skipped_horizon"
        assert "capital_blocked" in result["skip_reason"]
        assert "3" in result["skip_reason"]

    def test_hold_4_is_rejected(self):
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=4,
        )
        result = reject_horizon_exceeded(family, capital_model=LEVERAGED_SLEEVE)
        assert result is not None
        assert result["status"] == "skipped_horizon"

    def test_hold_2_is_not_rejected(self):
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=2,
        )
        result = reject_horizon_exceeded(family, capital_model=LEVERAGED_SLEEVE)
        assert result is None  # None means "not rejected"

    def test_hold_1_is_not_rejected(self):
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=1,
        )
        result = reject_horizon_exceeded(family, capital_model=LEVERAGED_SLEEVE)
        assert result is None

    def test_hold_0_is_not_rejected(self):
        """Intraday / same-day hold = 0 must pass the ceiling."""
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=0,
        )
        result = reject_horizon_exceeded(family, capital_model=LEVERAGED_SLEEVE)
        assert result is None

    def test_skip_reason_mentions_max(self):
        """Skip reason must mention the leveraged sleeve's max_hold_trading_days for clarity."""
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=5,
        )
        result = reject_horizon_exceeded(family, capital_model=LEVERAGED_SLEEVE)
        assert str(LEVERAGED_SLEEVE.max_hold_trading_days) in result["skip_reason"]

    def test_owned_cash_never_rejects_regardless_of_horizon(self):
        """OWNED_CASH has no ceiling: even a 20-trading-day hold is not rejected."""
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=20,
        )
        result = reject_horizon_exceeded(family, capital_model=OWNED_CASH)
        assert result is None

    def test_owned_cash_is_the_default_without_explicit_capital_model(self):
        """No capital_model passed → falls back to ACTIVE_CAPITAL_MODEL (OWNED_CASH): no ceiling."""
        family = StrategyFamily(
            name="test_family",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=20,
        )
        result = reject_horizon_exceeded(family)
        assert result is None


# ---------------------------------------------------------------------------
# De-overlap helper
# ---------------------------------------------------------------------------

class TestDeoverlap:
    def test_deoverlap_removes_overlapping_rows(self):
        """With hold=2, every other row is de-overlapped from daily bars."""
        df = _make_daily_df(20, hold=2)
        result = deoverlap_daily(df, hold=2)
        # First kept = index 0, next kept = index 2, etc.
        assert len(result) <= (len(df) // 2 + 1)
        assert len(result) >= 1

    def test_deoverlap_hold1_keeps_alternating(self):
        """hold=1 means every row is independent (no overlap), all kept."""
        df = _make_daily_df(10, hold=1)
        result = deoverlap_daily(df, hold=1)
        assert len(result) == 10  # non-overlapping with hold=1

    def test_deoverlap_returns_new_dataframe(self):
        """Must not mutate input."""
        df = _make_daily_df(10)
        df_copy = df.copy()
        _ = deoverlap_daily(df, hold=1)
        pd.testing.assert_frame_equal(df, df_copy)


# ---------------------------------------------------------------------------
# Effective-N: event_driven (pooled)
# ---------------------------------------------------------------------------

class TestEffNEventPooled:
    def test_pooled_across_two_symbols(self):
        """Pooled effective-N is sum of per-symbol de-overlapped events."""
        df_a = _make_event_df(20, "A", hold=1)
        df_b = _make_event_df(20, "B", hold=1)
        combined = pd.concat([df_a, df_b], ignore_index=True)
        eff_n = effective_n_event_pooled(combined, hold=1)
        # With hold=1, no overlap → 20+20=40
        assert eff_n == 40

    def test_pooled_minimum_n_respected(self):
        """Small pool below MIN_EFF_N=30 should return the actual count."""
        df = _make_event_df(10, "A", hold=1)
        eff_n = effective_n_event_pooled(df, hold=1)
        assert eff_n == 10

    def test_pooled_is_not_per_name_count(self):
        """Pooled count is NOT multiplied naively; it's sum of per-symbol de-overlap."""
        df_a = _make_event_df(15, "A", hold=2)
        df_b = _make_event_df(15, "B", hold=2)
        combined = pd.concat([df_a, df_b], ignore_index=True)
        eff_n = effective_n_event_pooled(combined, hold=2)
        # Each symbol with hold=2 de-overlaps to ~8 rows; pooled ~16
        # Must be less than 30 (not 30 from wrong per-name 30 rule)
        assert eff_n < 30  # raw pool is small
        assert eff_n >= 8  # but not zero

    def test_symbol_column_required(self):
        """Raises ValueError if 'symbol' column is missing."""
        df = _make_event_df(20, "A").drop(columns=["symbol"])
        with pytest.raises(ValueError, match="symbol"):
            effective_n_event_pooled(df, hold=1)


# ---------------------------------------------------------------------------
# Effective-N: single_name (per-symbol)
# ---------------------------------------------------------------------------

class TestEffNSingleName:
    def test_per_symbol_deoverlap(self):
        """Single-name eff_n = number of de-overlapped rows for that symbol."""
        df = _make_daily_df(30, hold=1)
        eff_n = effective_n_single_name_per_symbol(df, hold=1)
        assert eff_n == 30  # hold=1 → every row is independent

    def test_per_symbol_deoverlap_hold2(self):
        """hold=2 → every other row kept → eff_n ≈ 15."""
        df = _make_daily_df(30, hold=2)
        eff_n = effective_n_single_name_per_symbol(df, hold=2)
        assert 12 <= eff_n <= 18  # ~half of 30

    def test_per_symbol_does_not_pool(self):
        """single_name eff_n is NOT influenced by extra rows from other symbols."""
        df = _make_daily_df(30, hold=1)
        eff_n_30 = effective_n_single_name_per_symbol(df, hold=1)
        df_extra = _make_daily_df(100, hold=1)
        eff_n_100 = effective_n_single_name_per_symbol(df_extra, hold=1)
        # Per-symbol count scales with the input, not fixed at some global count
        assert eff_n_100 > eff_n_30


# ---------------------------------------------------------------------------
# Shape constants
# ---------------------------------------------------------------------------

class TestShapeConstants:
    def test_shape_names(self):
        assert SHAPE_SINGLE_NAME == "single_name_time_series"
        assert SHAPE_EVENT_DRIVEN == "event_driven"


# ---------------------------------------------------------------------------
# Grid horizon coverage (ADR-0015 / plan 18 A-2.2)
# ---------------------------------------------------------------------------

class TestGridCoversTheLongHorizonBand:
    """
    ADR-0015 lifted the ≤2-trading-day ceiling by making ``OWNED_CASH`` the
    default capital model, but the grid kept enumerating ``[1, 2]`` only — so
    the long-horizon cells stopped being *blocked* and started simply not
    existing.  The ceiling has to be absent from both places to be gone.
    """

    def test_single_name_holds_reach_the_long_band(self) -> None:
        from strategy_fit.run_strategy_fit import SINGLE_NAME_HOLDS
        assert {1, 2, 5, 20, 60} <= set(SINGLE_NAME_HOLDS)

    def test_event_holds_reach_the_long_band(self) -> None:
        from strategy_fit.run_strategy_fit import EVENT_HOLDS
        assert {1, 2, 5, 20, 60} <= set(EVENT_HOLDS)

    def test_no_grid_hold_is_rejected_by_the_active_capital_model(self) -> None:
        """Every enumerated hold must be testable under the active model."""
        from strategy_fit.harness import MAX_HOLD_TRADING_DAYS
        from strategy_fit.run_strategy_fit import SINGLE_NAME_HOLDS
        if MAX_HOLD_TRADING_DAYS is not None:
            pytest.skip("a ceiling is active; long holds are legitimately blocked")
        assert max(SINGLE_NAME_HOLDS) >= 60


class TestEmptyUniverseAborts:
    """
    An empty universe must abort, never render a verdict.

    On 2026-09-13 an expired ``SB_ACCESS_TOKEN`` made both funding endpoints
    return HTTP 401. ``screen_universe`` logged warnings, the intersection came
    back empty, and the grid ran to completion over zero symbols and wrote a
    confident ``Verdict: NO-GO`` report — over a good one. A credentials
    failure must not be able to author a research result.
    """

    def test_run_grid_raises_on_empty_universe(self) -> None:
        from strategy_fit.run_strategy_fit import EmptyUniverseError, run_grid

        class _EmptySource:
            def funding_universe(self) -> list[str]:
                return []

        with pytest.raises(EmptyUniverseError, match="zero symbols"):
            run_grid(ds=_EmptySource())

    def test_the_error_names_credentials_as_the_likely_cause(self) -> None:
        from strategy_fit.run_strategy_fit import EmptyUniverseError
        assert "SB_ACCESS_TOKEN" in EmptyUniverseError.__doc__
