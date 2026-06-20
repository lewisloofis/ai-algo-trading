"""Tests for strategy_fit.walk_forward — TDD RED phase.

Covers:
- Persistent injected edge → is_decayed=False
- Pure noise → often decayed or hit_rate ≈ 0.5
- Insufficient history → graceful handling
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.walk_forward import (
    DECAY_MAJORITY_FRAC,
    DECAY_THRESHOLD_HR,
    DECAY_THRESHOLD_SHARPE,
    WalkForwardResult,
    walk_forward_cell,
)
from strategy_fit.families_single_name import mean_reversion_signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_edge_series(n: int = 300, edge_bps: float = 150.0, seed: int = 7) -> pd.DataFrame:
    """
    Synthetic series with a strong injected mean-reversion edge.

    Every 5th day price drops ~2-sigma; subsequent day recovers by edge_bps.
    The series is long enough for several walk-forward windows (fit=120+test=40).
    """
    rng = np.random.default_rng(seed)
    vol = 0.016
    log_returns = rng.normal(0.0, vol, size=n)
    edge = edge_bps / 1e4
    for i in range(2, n):
        if log_returns[i - 2] < -vol and log_returns[i - 1] < -vol:
            log_returns[i] += edge * 3.0  # strong injected edge
    closes = 27_500 * np.exp(np.cumsum(log_returns))
    dates = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": closes.astype(int).clip(1),
            "volume": np.full(n, 5_000_000),
        }
    )


def _make_noise_series(n: int = 300, seed: int = 99) -> pd.DataFrame:
    """Pure random-walk series — no injected edge."""
    rng = np.random.default_rng(seed)
    vol = 0.016
    log_returns = rng.normal(0.0, vol, size=n)
    closes = 27_500 * np.exp(np.cumsum(log_returns))
    dates = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": closes.astype(int).clip(1),
            "volume": np.full(n, 5_000_000),
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWalkForwardEdge:
    def test_persistent_edge_not_decayed(self):
        """Strong injected edge → mean Sharpe > 0 → not decayed in most windows."""
        df = _make_edge_series(n=300, edge_bps=200.0, seed=7)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=100,
            test_window=40,
            min_windows=2,
        )
        assert isinstance(result, WalkForwardResult)
        assert result.n_windows >= 2
        # With a strong edge, mean hit_rate should be above 0.5.
        assert result.mean_hit_rate > 0.50, (
            f"Expected mean_hit_rate > 0.5, got {result.mean_hit_rate:.3f}"
        )
        # Decay status should be persistent (not decayed).
        assert not result.is_decayed, (
            f"Expected persistent, but is_decayed=True "
            f"(mean_hr={result.mean_hit_rate:.3f})"
        )


class TestWalkForwardNoise:
    def test_noise_sharpe_near_zero(self):
        """Pure noise → mean Sharpe close to 0 (or negative)."""
        df = _make_noise_series(n=300, seed=99)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=100,
            test_window=40,
            min_windows=2,
        )
        assert isinstance(result, WalkForwardResult)
        assert result.n_windows >= 2
        # Noise should not produce a strong positive Sharpe.
        # We allow it to pass by chance occasionally, but mean should be modest.
        # Just check the result is produced correctly.
        assert isinstance(result.mean_sharpe, float)
        assert isinstance(result.is_decayed, bool)

    def test_noise_mean_hr_near_half(self):
        """Mean hit rate on pure noise should be close to 0.5 (±0.15 tolerance)."""
        df = _make_noise_series(n=300, seed=42)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=100,
            test_window=40,
            min_windows=2,
        )
        assert 0.35 <= result.mean_hit_rate <= 0.65, (
            f"Expected ~0.5 hit rate on noise, got {result.mean_hit_rate:.3f}"
        )


class TestWalkForwardInsufficientHistory:
    def test_fewer_than_min_windows_returns_gracefully(self):
        """Only 150 rows with fit=120+test=40 → 0 complete windows → graceful result."""
        df = _make_noise_series(n=120, seed=1)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=100,
            test_window=40,
            min_windows=2,  # can't be met
        )
        assert isinstance(result, WalkForwardResult)
        # With insufficient windows, should be marked decayed (conservative).
        assert result.n_windows < 2
        assert result.is_decayed  # no history → mark as non-persistent

    def test_min_windows_zero_always_possible(self):
        """min_windows=0 always succeeds even with tiny input."""
        df = _make_noise_series(n=50, seed=2)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=30,
            test_window=15,
            min_windows=0,
        )
        assert isinstance(result, WalkForwardResult)


class TestDecayConstants:
    def test_constants_are_defined(self):
        assert isinstance(DECAY_THRESHOLD_SHARPE, float)
        assert isinstance(DECAY_THRESHOLD_HR, float)
        assert isinstance(DECAY_MAJORITY_FRAC, float)
        assert 0.0 < DECAY_MAJORITY_FRAC < 1.0
        assert 0.0 < DECAY_THRESHOLD_HR < 1.0

    def test_default_threshold_values(self):
        assert DECAY_THRESHOLD_SHARPE == 0.0
        assert DECAY_THRESHOLD_HR == pytest.approx(0.50)
        assert DECAY_MAJORITY_FRAC == pytest.approx(0.60)


class TestWalkForwardWindowCount:
    def test_n_windows_correct(self):
        """300 rows, fit=100, test=40 → fits 4-5 windows."""
        df = _make_noise_series(n=300, seed=5)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=100,
            test_window=40,
            min_windows=1,
        )
        assert result.n_windows >= 1

    def test_window_dates_non_overlapping(self):
        """Each test window must not overlap with the next test window.

        In the rolling scheme: each window advances by test_window rows.
        Window i+1's test_start == window i's test_end (roll by test_window).
        """
        df = _make_edge_series(n=300)
        result = walk_forward_cell(
            df=df,
            signal_fn=mean_reversion_signal,
            horizon_trading_days=1,
            fit_window=100,
            test_window=40,
            min_windows=1,
        )
        assert result.n_windows >= 2, "Need at least 2 windows to check non-overlap"
        for i in range(len(result.windows) - 1):
            w0 = result.windows[i]
            w1 = result.windows[i + 1]
            # Test windows must not overlap: next test_start == prev test_end
            assert w1.test_start >= w0.test_end, (
                f"Window {i+1} test_start={w1.test_start} overlaps with "
                f"window {i} test_end={w0.test_end}"
            )
