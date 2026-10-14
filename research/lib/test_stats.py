"""pytest suite for lib/stats.py — required_effective_n (plan-10 W2.1 power calc)."""
from __future__ import annotations

import math

from lib.stats import REQUIRED_N_UNRESOLVABLE, required_effective_n


# ---------------------------------------------------------------------------
# Worked example from the roadmap (plan-10 §6 W2.1)
# ---------------------------------------------------------------------------

class TestWorkedExample:
    def test_20bps_edge_150bps_sigma_z2_is_225(self) -> None:
        """20 bps edge at z=2 against a 150 bps per-trade spread needs 225."""
        n = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0, z=2.0)
        assert n == 225, f"Expected 225 (roadmap worked example), got {n}"

    def test_default_z_is_2(self) -> None:
        """Calling without z must match the explicit z=2.0 worked example."""
        n_default = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0)
        n_explicit = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0, z=2.0)
        assert n_default == n_explicit == 225


# ---------------------------------------------------------------------------
# Monotonicity
# ---------------------------------------------------------------------------

class TestMonotonicity:
    def test_larger_edge_needs_fewer_samples(self) -> None:
        """Doubling the target edge quarters the required N (inverse square)."""
        n_small_edge = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0)
        n_large_edge = required_effective_n(target_edge_bps=40.0, per_trade_sigma_bps=150.0)
        assert n_large_edge < n_small_edge
        assert abs(n_large_edge - n_small_edge / 4) <= 1

    def test_larger_sigma_needs_more_samples(self) -> None:
        """Doubling the per-trade sigma quadruples the required N."""
        n_low_sigma = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=75.0)
        n_high_sigma = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0)
        assert n_high_sigma > n_low_sigma
        # Inverse-square relationship, modulo ceil() rounding on each side.
        assert abs(n_high_sigma - n_low_sigma * 4) <= 5

    def test_higher_z_needs_more_samples(self) -> None:
        """A stricter confidence requirement (higher z) needs more samples."""
        n_z1 = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0, z=1.0)
        n_z3 = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0, z=3.0)
        assert n_z3 > n_z1


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------

class TestDegenerateInputs:
    def test_zero_edge_returns_unresolvable_sentinel(self) -> None:
        n = required_effective_n(target_edge_bps=0.0, per_trade_sigma_bps=150.0)
        assert n == REQUIRED_N_UNRESOLVABLE

    def test_negative_edge_returns_unresolvable_sentinel(self) -> None:
        n = required_effective_n(target_edge_bps=-10.0, per_trade_sigma_bps=150.0)
        assert n == REQUIRED_N_UNRESOLVABLE

    def test_nan_edge_returns_unresolvable_sentinel(self) -> None:
        n = required_effective_n(target_edge_bps=math.nan, per_trade_sigma_bps=150.0)
        assert n == REQUIRED_N_UNRESOLVABLE

    def test_none_edge_returns_unresolvable_sentinel(self) -> None:
        n = required_effective_n(target_edge_bps=None, per_trade_sigma_bps=150.0)  # type: ignore[arg-type]
        assert n == REQUIRED_N_UNRESOLVABLE

    def test_zero_sigma_returns_theoretical_minimum(self) -> None:
        """No dispersion at all → any single observation resolves the edge."""
        n = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=0.0)
        assert n == 1

    def test_negative_sigma_returns_theoretical_minimum(self) -> None:
        n = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=-5.0)
        assert n == 1

    def test_result_is_always_at_least_1(self) -> None:
        n = required_effective_n(target_edge_bps=1e6, per_trade_sigma_bps=1e-6)
        assert n >= 1

    def test_result_is_int(self) -> None:
        n = required_effective_n(target_edge_bps=20.0, per_trade_sigma_bps=150.0)
        assert isinstance(n, int)


# ---------------------------------------------------------------------------
# Welch's t: strategy trades vs the always-long (null) trade distribution
# ---------------------------------------------------------------------------

import pytest

from lib.stats import MIN_T_STAT, welch_t


class TestWelchT:
    def test_known_answer(self) -> None:
        # a: mean 3, var 2.5, n 5; b: mean 2, var 1, n 3 -> se = sqrt(0.5 + 1/3)
        t = welch_t([1, 2, 3, 4, 5], [1, 2, 3])
        assert t == pytest.approx(1.0 / math.sqrt(0.5 + 1.0 / 3.0))

    def test_null_effective_n_widens_the_error(self) -> None:
        # overlapping null trades are not independent: fewer effective draws -> smaller t
        t_full = welch_t([1, 2, 3, 4, 5], [1, 2, 3])
        t_eff = welch_t([1, 2, 3, 4, 5], [1, 2, 3], null_n_eff=1.5)
        assert t_eff == pytest.approx(1.0 / math.sqrt(0.5 + 1.0 / 1.5))
        assert t_eff < t_full

    def test_sign_follows_the_edge(self) -> None:
        assert welch_t([0, 1, 2], [3, 4, 5]) < 0

    def test_undefined_cases_are_nan(self) -> None:
        assert math.isnan(welch_t([1.0], [1, 2, 3]))        # one trade: no variance
        assert math.isnan(welch_t([1, 1, 1], [2, 2, 2]))    # zero variance both sides
        assert math.isnan(welch_t([1, 2, 3], []))

    def test_threshold_is_two(self) -> None:
        assert MIN_T_STAT == 2.0
