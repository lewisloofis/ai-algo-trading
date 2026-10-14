"""
strategy_fit/test_families_cross_sectional.py — TDD tests for Change 3a.

Tests for families_cross_sectional.py:
- run_cross_sectional_family with injected cross-sectional signal produces
  positive ICIR and ≥30 independent periods.
- A noisy (random) signal produces near-zero ICIR (not necessarily flagged as
  no-events, but statistically uninformative).
- The family flows end-to-end through run_grid with fixture DataSource and
  appears in the coverage table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cross_sectional_hist(
    n_symbols: int = 10,
    n_days: int = 60,
    signal_strength: float = 0.6,
    seed: int = 0,
) -> dict[str, pd.DataFrame]:
    """
    Build a synthetic hist_dict where each symbol's return on day t+1 is
    correlated with its rank on day t (cross-sectional momentum).

    signal_strength: 1.0 = perfect rank-correlation; 0.0 = pure noise.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-01", periods=n_days + 1)
    symbols = [f"SYM{i:02d}" for i in range(n_symbols)]

    # Base random returns for all symbols across days.
    base = rng.normal(0.0, 0.02, size=(n_symbols, n_days + 1))
    # Injected rank signal: on each day, sort by base and shift-up the ranked returns.
    # This creates cross-sectional predictability.
    close_matrix = np.ones((n_symbols, n_days + 1)) * 10_000.0
    for t in range(1, n_days + 1):
        noise = rng.normal(0.0, 0.02, size=n_symbols)
        ranks = np.argsort(close_matrix[:, t - 1])[::-1]  # rank by prior-day close
        # High-rank (top-close) symbols get higher returns.
        rank_signal = (ranks / (n_symbols - 1) - 0.5) * 0.04  # ±2%
        ret = signal_strength * rank_signal + (1 - signal_strength) * noise
        close_matrix[:, t] = close_matrix[:, t - 1] * (1.0 + ret)

    hist_dict: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        closes = close_matrix[i]
        hist_dict[sym] = pd.DataFrame(
            {
                "date": dates.strftime("%Y-%m-%d"),
                "open": closes * 0.99,
                "high": closes * 1.01,
                "low": closes * 0.98,
                "close": closes,
                "volume": np.ones(n_days + 1) * 5_000_000,
            }
        )
    return hist_dict


# ---------------------------------------------------------------------------
# Cross-sectional family tests
# ---------------------------------------------------------------------------

class TestRunCrossSectionalFamily:

    def test_import_succeeds(self):
        """families_cross_sectional module must be importable."""
        from strategy_fit import families_cross_sectional  # noqa: F401

    def test_run_returns_dict_with_required_keys(self):
        from strategy_fit.families_cross_sectional import run_cross_sectional_family
        hist = _make_cross_sectional_hist(n_symbols=10, n_days=60, signal_strength=0.7)
        result = run_cross_sectional_family(hist_dict=hist, horizon_trading_days=1)
        required = {"status", "skip_reason", "eff_n", "ic", "ic_tstat", "icir", "p_value",
                    "hit_rate", "gross_edge_bps", "net_edge_bps"}
        assert required.issubset(result.keys()), (
            f"Missing keys: {required - result.keys()}"
        )

    def test_injected_signal_produces_positive_icir(self):
        """Long-only regression: even a strong injected cross-sectional signal
        must NEVER be reported as 'tested' — cross-sectional is a decile
        long/short spread, which IDX retail accounts cannot execute (see
        strategy_fit/long_only.py). It must always come back
        long_short_infeasible, with the scored metrics (ic/ic_tstat/icir)
        nulled out, regardless of how strong the underlying signal is.

        Before this fix, a strong signal produced status='tested' with a
        positive ICIR — i.e. the family was silently scored as if this
        account could trade a long/short spread. That is the bug.
        """
        from strategy_fit.families_cross_sectional import run_cross_sectional_family
        from strategy_fit.long_only import STATUS_LONG_SHORT_INFEASIBLE
        hist = _make_cross_sectional_hist(n_symbols=10, n_days=80, signal_strength=0.8)
        result = run_cross_sectional_family(hist_dict=hist, horizon_trading_days=1)
        assert result["status"] == STATUS_LONG_SHORT_INFEASIBLE, (
            f"Expected status='{STATUS_LONG_SHORT_INFEASIBLE}', got {result['status']}: "
            f"{result.get('skip_reason')}"
        )
        assert result["ic"] is None and result["ic_tstat"] is None and result["icir"] is None, (
            "long_short_infeasible must null out the scored metrics, got "
            f"ic={result['ic']} ic_tstat={result['ic_tstat']} icir={result['icir']}"
        )
        assert "short" in (result["skip_reason"] or "").lower(), (
            "skip_reason must explain the short-leg infeasibility"
        )

    def test_injected_signal_reaches_min_eff_n(self):
        """With 120 sessions and hold=1, the periodic rebalance (rank on s,
        hold s+1..s+2, re-rank on the exit day) yields ~59 periods, eff_n >= 30.

        D1 note: this fixture used n_days=60 when every signal day counted as an
        independent period; the live-feasible rebalance takes every 2nd day for
        hold=1, so 60 sessions give ~29 periods and the family correctly
        reports insufficient_n.

        eff_n is a period count, not a scored P&L number, so it is preserved
        even though the family is long_short_infeasible (never 'tested').
        """
        from strategy_fit.families_cross_sectional import run_cross_sectional_family
        from strategy_fit.long_only import STATUS_LONG_SHORT_INFEASIBLE
        hist = _make_cross_sectional_hist(n_symbols=8, n_days=120, signal_strength=0.7)
        result = run_cross_sectional_family(hist_dict=hist, horizon_trading_days=1)
        assert result["status"] == STATUS_LONG_SHORT_INFEASIBLE
        assert (result["eff_n"] or 0) >= 30, (
            f"Expected eff_n >= 30, got {result['eff_n']}"
        )

    def test_insufficient_symbols_returns_insufficient_n(self):
        """With fewer than 3 symbols, IC cannot be computed meaningfully."""
        from strategy_fit.families_cross_sectional import run_cross_sectional_family
        hist = _make_cross_sectional_hist(n_symbols=2, n_days=40, signal_strength=0.8)
        result = run_cross_sectional_family(hist_dict=hist, horizon_trading_days=1)
        # Either insufficient_n OR tested with very low eff_n.
        if result["status"] == "insufficient_n":
            pass  # correct
        else:
            assert (result["eff_n"] or 0) < 30

    def test_shape_is_cross_sectional(self):
        """Shape constant must be 'cross_sectional'."""
        from strategy_fit.harness import SHAPE_CROSS_SECTIONAL
        assert SHAPE_CROSS_SECTIONAL == "cross_sectional"

    def test_min_symbols_per_period(self):
        """Family must document the minimum symbols required per period."""
        from strategy_fit import families_cross_sectional
        assert hasattr(families_cross_sectional, "MIN_SYMBOLS_PER_PERIOD"), (
            "families_cross_sectional must export MIN_SYMBOLS_PER_PERIOD constant"
        )


# ---------------------------------------------------------------------------
# End-to-end: cross_sectional shape in grid runner
# ---------------------------------------------------------------------------

class TestCrossSectionalInGrid:

    def test_cross_sectional_appears_in_coverage(self, tmp_path):
        """Run the grid with fixture data; cross_sectional must appear in output."""
        from strategy_fit.run_strategy_fit import run_strategy_fit
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "cross_sectional" in content, (
            "cross_sectional family must appear in the strategy_portfolio.md coverage table"
        )
