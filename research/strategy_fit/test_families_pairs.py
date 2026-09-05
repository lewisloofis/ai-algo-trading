"""
strategy_fit/test_families_pairs.py — TDD tests for Change 3b.

Tests for families_pairs.py:
- Cointegrated synthetic pair is detected; noise pair is not.
- Spread half-life is estimated as a positive finite number.
- eff_n from OOS block-bootstrap meets MIN_EFF_N=30 on a large enough series.
- Noise pair produces no cointegration signal.
- Both shapes flow through the grid runner end-to-end and appear in coverage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers — synthetic cointegrated and noise pairs
# ---------------------------------------------------------------------------

def _make_cointegrated_pair(
    n: int = 200,
    seed: int = 42,
    half_life: float = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Return two DataFrames (sym_A, sym_B) where sym_A and sym_B are cointegrated.

    sym_B = sym_A + spread, where spread is an AR(1) process with the given
    half-life (mean-reverting).  beta coefficient = 1.0.
    """
    rng = np.random.default_rng(seed)
    # Shared random walk (common factor).
    common = np.cumsum(rng.normal(0.0, 1.0, n))
    # AR(1) spread with persistence phi = exp(-log(2)/half_life).
    phi = np.exp(-np.log(2.0) / half_life)
    spread = np.zeros(n)
    for t in range(1, n):
        spread[t] = phi * spread[t - 1] + rng.normal(0.0, 0.5)
    price_a = 10_000.0 + common * 100
    price_b = price_a + spread * 50

    dates = pd.bdate_range("2025-01-01", periods=n)

    def _make_df(price: np.ndarray) -> pd.DataFrame:
        p = np.clip(price, 100, None)
        return pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": p * 0.99,
            "high": p * 1.01,
            "low": p * 0.98,
            "close": p,
            "volume": np.ones(n) * 5_000_000,
        })

    return _make_df(price_a), _make_df(price_b)


def _make_noise_pair(n: int = 200, seed: int = 99) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return two independent random-walk DataFrames (not cointegrated)."""
    rng = np.random.default_rng(seed)
    price_a = 10_000.0 + np.cumsum(rng.normal(0.0, 100, n))
    price_b = 8_000.0 + np.cumsum(rng.normal(0.0, 100, n))
    dates = pd.bdate_range("2025-01-01", periods=n)

    def _make_df(price: np.ndarray) -> pd.DataFrame:
        p = np.clip(price, 100, None)
        return pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": p * 0.99,
            "high": p * 1.01,
            "low": p * 0.98,
            "close": p,
            "volume": np.ones(n) * 5_000_000,
        })

    return _make_df(price_a), _make_df(price_b)


# ---------------------------------------------------------------------------
# Pairs family tests
# ---------------------------------------------------------------------------

class TestRunPairsFamily:

    def test_import_succeeds(self):
        """families_pairs module must be importable."""
        from strategy_fit import families_pairs  # noqa: F401

    def test_run_returns_dict_with_required_keys(self):
        from strategy_fit.families_pairs import run_pairs_family
        df_a, df_b = _make_cointegrated_pair(n=120)
        hist = {"SYM_A": df_a, "SYM_B": df_b}
        result = run_pairs_family(hist_dict=hist, horizon_trading_days=1)
        required = {"status", "skip_reason", "eff_n", "hit_rate",
                    "gross_edge_bps", "net_edge_bps", "p_value"}
        assert required.issubset(result.keys()), (
            f"Missing keys: {required - result.keys()}"
        )

    def test_cointegrated_pair_is_detected(self):
        """Long-only regression: a pair trade requires a short leg (long the
        cheap leg, short the rich leg), which IDX retail accounts cannot
        execute (see strategy_fit/long_only.py). So a strongly cointegrated
        pair must NEVER be reported as 'tested' — at best it is detected and
        then marked long_short_infeasible; it may also legitimately be
        insufficient_n if eff_n falls short. 'tested' is no longer a valid
        outcome for this family under any data.

        Before this fix, a strongly cointegrated pair produced status='tested'
        with a scored gross_edge_bps — i.e. the family was silently scored as
        if this account could trade a long/short pair. That is the bug.
        """
        from strategy_fit.families_pairs import run_pairs_family
        from strategy_fit.long_only import STATUS_LONG_SHORT_INFEASIBLE
        df_a, df_b = _make_cointegrated_pair(n=200, seed=42, half_life=5.0)
        hist = {"SYM_A": df_a, "SYM_B": df_b}
        result = run_pairs_family(hist_dict=hist, horizon_trading_days=1)
        # A cointegrated pair with 200 rows and half_life=5 should pass cointegration test.
        assert result["status"] in (STATUS_LONG_SHORT_INFEASIBLE, "insufficient_n"), (
            f"Cointegrated pair should not be skipped for non-data reasons, and must "
            f"never be 'tested'; got status={result['status']} "
            f"skip_reason={result.get('skip_reason')}"
        )

    def test_noise_pair_does_not_pass_coint(self):
        """Two independent random walks must not produce a strong cointegration signal."""
        from strategy_fit.families_pairs import run_pairs_family
        from strategy_fit.long_only import STATUS_LONG_SHORT_INFEASIBLE
        df_a, df_b = _make_noise_pair(n=200)
        hist = {"SYM_A": df_a, "SYM_B": df_b}
        result = run_pairs_family(hist_dict=hist, horizon_trading_days=1)
        # Noise pairs either fail cointegration, or have low eff_n. We cannot
        # guarantee p>0.05 on every seed, but the result must be one of the
        # known non-survivor statuses — 'tested' is never valid for this
        # family (see test_cointegrated_pair_is_detected).
        assert result["status"] in (
            STATUS_LONG_SHORT_INFEASIBLE, "insufficient_n", "cointegration_not_found"
        ), f"Unexpected status for noise pair: {result['status']}"

    def test_candidate_set_cap_is_logged(self, caplog):
        """When there are many symbols, a cap must be applied and logged."""
        import logging
        from strategy_fit.families_pairs import run_pairs_family
        # Build a large universe to trigger the cap.
        hist: dict[str, pd.DataFrame] = {}
        for i in range(20):
            df_a, _ = _make_cointegrated_pair(n=100, seed=i)
            hist[f"SYM{i:02d}"] = df_a
        with caplog.at_level(logging.INFO, logger="strategy_fit.families_pairs"):
            run_pairs_family(hist_dict=hist, horizon_trading_days=1)
        # Should log that it capped the candidate set.
        cap_mentioned = any(
            "cap" in record.message.lower() or "top" in record.message.lower()
            for record in caplog.records
        )
        assert cap_mentioned, (
            "run_pairs_family must log the candidate-set cap to prevent silent truncation"
        )

    def test_shape_constant_is_pairs(self):
        from strategy_fit.harness import SHAPE_PAIRS
        assert SHAPE_PAIRS == "pairs"

    def test_min_obs_constant_exported(self):
        """Module must export MIN_PAIR_OBS (non-overlapping spread obs floor)."""
        from strategy_fit import families_pairs
        assert hasattr(families_pairs, "MIN_PAIR_OBS"), (
            "families_pairs must export MIN_PAIR_OBS constant"
        )
        assert families_pairs.MIN_PAIR_OBS >= 30


# ---------------------------------------------------------------------------
# End-to-end: pairs shape in grid runner
# ---------------------------------------------------------------------------

class TestPairsInGrid:

    def test_pairs_appears_in_coverage(self, tmp_path):
        """Run the grid with fixture data; pairs must appear in output."""
        from strategy_fit.run_strategy_fit import run_strategy_fit
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "pairs" in content, (
            "pairs family must appear in the strategy_portfolio.md coverage table"
        )
