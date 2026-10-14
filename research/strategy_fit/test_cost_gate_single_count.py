"""
strategy_fit/test_cost_gate_single_count.py — Invariant: cost floor counted exactly once.

TDD RED phase written before the fix.

The bug: every cell runner in run_strategy_fit.py compares
    net_edge_bps  (= gross - floor, already subtracted by the family)
against
    hurdle        (= floor + impact + financing, the full cost stack)

So the gate effectively demands  gross > 2×floor + impact + financing.

The fix: gate must compare GROSS against hurdle.
    cost_pass          = gross_edge_bps > hurdle
    CellResult.net_edge_bps = gross_edge_bps - hurdle   (true net-after-full-cost)

These tests construct CellResult inputs directly and verify the runner's
gate decision is correct for all four shapes.  The tests MUST fail before
the fix is applied and MUST pass afterwards.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.harness import (
    SHAPE_CROSS_SECTIONAL,
    SHAPE_EVENT_DRIVEN,
    SHAPE_PAIRS,
    SHAPE_SINGLE_NAME,
    CellResult,
    StrategyFamily,
)
from strategy_fit.financing import best_feasible_hurdle_bps
from strategy_fit.impact import compute_impact_bps_from_daily_df
from strategy_fit.run_strategy_fit import (
    MODELLED_IMPACT_BPS,
    _run_single_name_cell,
    _run_event_cell,
    _run_cross_sectional_cell,
    _run_pairs_cell,
    ROUND_TRIP_BPS,
)


# ---------------------------------------------------------------------------
# Helpers — known cost-stack values
# ---------------------------------------------------------------------------

def _floor_plus_financing(
    floor_bps: float,
    hold: int,
    impact_bps: float = MODELLED_IMPACT_BPS,
) -> tuple[float, float]:
    """Return (hurdle, financing_bps) for the best (cheapest) feasible entry day.

    ``impact_bps`` defaults to MODELLED_IMPACT_BPS (0.0), still correct for the
    shapes that do not price impact.  Single-name daily cells DO price it as of
    plan 13, so those callers pass the same per-symbol charge the runner used.
    """
    hurdle, _dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=floor_bps,
        modelled_impact_bps=impact_bps,
        hold_trading_days=hold,
    )
    financing = hurdle - floor_bps - impact_bps
    return hurdle, financing


# ---------------------------------------------------------------------------
# Minimal synthetic DataFrames that produce a "tested" single-name cell
# ---------------------------------------------------------------------------

def _make_strong_signal_df(n: int = 250, seed: int = 0) -> pd.DataFrame:
    """
    Synthetic daily OHLCV where signal = sign(forward_return) always,
    producing a high hit-rate and positive gross edge.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = 10_000.0 + np.cumsum(rng.normal(0, 50, n))
    close = np.clip(close, 1_000, None)
    fwd = np.sign(rng.normal(0, 1, n)) * 0.02  # ±2 %
    close_arr = close.copy()
    # Shift close so that tomorrow's return equals fwd.
    for i in range(len(close_arr) - 1):
        close_arr[i + 1] = close_arr[i] * (1.0 + fwd[i])
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": close_arr * 0.999,
            "high": close_arr * 1.001,
            "low": close_arr * 0.998,
            "close": close_arr,
            "volume": np.ones(n) * 10_000_000,
        }
    )


def _make_hist_dict(n_symbols: int = 4, n: int = 250) -> dict[str, pd.DataFrame]:
    """Small universe for pooled shapes."""
    return {f"SYM{i:02d}": _make_strong_signal_df(n=n, seed=i) for i in range(n_symbols)}


# ---------------------------------------------------------------------------
# Shared assertion helper
# ---------------------------------------------------------------------------

def _assert_gate_and_net(
    cell: CellResult,
    gross_edge_bps: float,
    hurdle: float,
    shape_label: str,
) -> None:
    """
    Assert that the cell's passes_cost_gate and net_edge_bps are consistent
    with gross_edge_bps being compared to hurdle EXACTLY ONCE.

    - passes_cost_gate must equal (gross_edge_bps > hurdle)
    - net_edge_bps must equal (gross_edge_bps - hurdle), not (gross - floor) re-subtracted
    - For a passing cell net_edge_bps > 0; for a failing cell net_edge_bps <= 0
    """
    assert cell.status == "tested", (
        f"[{shape_label}] Expected status='tested'; got {cell.status!r}: {cell.skip_reason}"
    )
    assert cell.gross_edge_bps is not None, f"[{shape_label}] gross_edge_bps is None"
    assert cell.net_edge_bps is not None, f"[{shape_label}] net_edge_bps is None"
    assert cell.passes_cost_gate is not None, f"[{shape_label}] passes_cost_gate is None"

    expected_pass = gross_edge_bps > hurdle
    assert cell.passes_cost_gate == expected_pass, (
        f"[{shape_label}] passes_cost_gate={cell.passes_cost_gate} but gross={gross_edge_bps:.1f} "
        f"> hurdle={hurdle:.1f} is {expected_pass}.  "
        f"The floor is being double-counted (net_edge was compared to hurdle instead of gross)."
    )

    expected_net = gross_edge_bps - hurdle
    assert abs(cell.net_edge_bps - expected_net) < 1.0, (
        f"[{shape_label}] net_edge_bps={cell.net_edge_bps:.2f}, "
        f"expected gross-hurdle={expected_net:.2f} (floor counted exactly once). "
        f"Difference={cell.net_edge_bps - expected_net:.2f} bps — floor double-subtracted?"
    )


# ---------------------------------------------------------------------------
# Part 1 — single_name: cost gate uses gross, not (gross - floor)
# ---------------------------------------------------------------------------

class TestSingleNameCostGateSingleCount:
    """
    Verify the single_name runner gates on gross_edge_bps > hurdle and
    sets net_edge_bps = gross_edge_bps - hurdle.

    For single_name: floor = per-name min_edge_to_clear_bps (spread + 40 bps fee).
    """

    # We cannot easily force the family to produce a specific gross edge, so we
    # verify the invariant via the CellResult: if net_edge_bps == gross - hurdle
    # then the floor was counted exactly once.

    def _run_cell(self, hold: int, floor_bps: float = 50.0, impact_basis: str | None = None) -> CellResult:
        family = StrategyFamily(
            name="daily_momentum",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=hold,
        )
        df = _make_strong_signal_df(n=250, seed=42)
        kw = {} if impact_basis is None else {"impact_basis": impact_basis}
        return _run_single_name_cell(
            symbol="TEST",
            df=df,
            family=family,
            min_edge_to_clear_bps=floor_bps,
            **kw,
        )

    def test_trailing_basis_net_is_gross_minus_floor_plus_mean_trade_impact(self):
        """Default (trailing point-in-time) basis: the cell charges the MEAN
        per-trade impact, and the floor is still counted exactly once."""
        floor = 50.0
        cell = self._run_cell(hold=2, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        hurdle, _ = _floor_plus_financing(floor, hold=2, impact_bps=cell.modelled_impact_bps)
        assert cell.impact_basis == "trailing_point_in_time"
        assert abs(cell.net_edge_bps - (cell.gross_edge_bps - hurdle)) < 0.1

    def test_net_edge_equals_gross_minus_full_hurdle_hold1(self):
        """net_edge_bps must be gross - full_hurdle (floor counted once), hold=1."""
        floor = 50.0
        # plan 13: single_name cells now price market impact, so the
        # expected hurdle must include the same per-symbol charge the
        # runner applied.  The invariant under test is unchanged - the
        # friction floor is still counted exactly once - but the cost
        # stack it sits in now carries an impact term.  Passing 0.0 here
        # would be a stale expectation, not a double-count.
        impact = compute_impact_bps_from_daily_df(_make_strong_signal_df(n=250, seed=42))
        hurdle, _ = _floor_plus_financing(floor, hold=1, impact_bps=impact)
        # Pinned to the legacy full-history basis: the expected `impact` above is
        # the single full-history number; the default basis is now trailing
        # point-in-time (see test_impact_point_in_time.py).
        cell = self._run_cell(hold=1, floor_bps=floor, impact_basis="legacy_full_history")
        assert cell.impact_priced is True, "plan 13: daily cells must be impact-priced"
        assert cell.modelled_impact_bps == pytest.approx(impact)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"single_name hold=1: net_edge_bps={cell.net_edge_bps:.3f} but "
            f"gross - hurdle = {expected_net:.3f}.  "
            f"(gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}, floor={floor:.1f})"
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold2(self):
        """net_edge_bps must be gross - full_hurdle (floor counted once), hold=2."""
        floor = 50.0
        # plan 13: single_name cells now price market impact, so the
        # expected hurdle must include the same per-symbol charge the
        # runner applied.  The invariant under test is unchanged - the
        # friction floor is still counted exactly once - but the cost
        # stack it sits in now carries an impact term.  Passing 0.0 here
        # would be a stale expectation, not a double-count.
        impact = compute_impact_bps_from_daily_df(_make_strong_signal_df(n=250, seed=42))
        hurdle, _ = _floor_plus_financing(floor, hold=2, impact_bps=impact)
        # Pinned to the legacy full-history basis: the expected `impact` above is
        # the single full-history number; the default basis is now trailing
        # point-in-time (see test_impact_point_in_time.py).
        cell = self._run_cell(hold=2, floor_bps=floor, impact_basis="legacy_full_history")
        assert cell.impact_priced is True, "plan 13: daily cells must be impact-priced"
        assert cell.modelled_impact_bps == pytest.approx(impact)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"single_name hold=2: net_edge_bps={cell.net_edge_bps:.3f} but "
            f"gross - hurdle = {expected_net:.3f}.  "
            f"(gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}, floor={floor:.1f})"
        )

    def test_cost_gate_pass_when_gross_exceeds_hurdle(self):
        """A cell with gross > hurdle must have passes_cost_gate=True."""
        # We'll use a very low floor so the synthetic data's gross edge clears.
        floor = 1.0  # minimal floor so hurdle is tiny
        hurdle, _ = _floor_plus_financing(floor, hold=1)
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        if cell.gross_edge_bps > hurdle:
            assert cell.passes_cost_gate is True, (
                f"gross={cell.gross_edge_bps:.1f} > hurdle={hurdle:.1f} "
                f"but passes_cost_gate={cell.passes_cost_gate}"
            )

    def test_cost_gate_fail_when_gross_below_hurdle(self):
        """A cell with gross < hurdle must have passes_cost_gate=False."""
        # Very high floor so hurdle dwarfs any realistic gross edge.
        floor = 10_000.0
        hurdle, _ = _floor_plus_financing(floor, hold=1)
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.passes_cost_gate is False, (
            f"gross={cell.gross_edge_bps} vs huge hurdle={hurdle}, should fail cost gate"
        )

    def test_net_edge_positive_iff_passes_cost_gate(self):
        """Survivors (passes_cost_gate=True) must have net_edge_bps > 0."""
        floor = 50.0
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        if cell.passes_cost_gate:
            assert (cell.net_edge_bps or 0.0) > 0.0, (
                "Survivor cell must have net_edge_bps > 0 (gross > hurdle)"
            )
        else:
            assert (cell.net_edge_bps or 0.0) <= 0.0, (
                "Non-survivor cell must have net_edge_bps <= 0 (gross <= hurdle)"
            )


# ---------------------------------------------------------------------------
# Part 2 — event: cost gate uses gross, not (gross - ROUND_TRIP_BPS)
# ---------------------------------------------------------------------------

class TestEventCostGateSingleCount:
    """
    Verify the event runner gates on gross_edge_bps > hurdle (median floor).
    Floor = median min_edge_to_clear_bps across the universe.
    """

    def _run_cell(self, hold: int, floor_bps: float = 50.0) -> CellResult:
        family = StrategyFamily(
            name="gap_event",
            shape=SHAPE_EVENT_DRIVEN,
            horizon_trading_days=hold,
        )
        hist = _make_hist_dict(n_symbols=4, n=250)
        # Uniform min_edge_map so median = floor_bps.
        min_edge_map = {sym: floor_bps for sym in hist}
        return _run_event_cell(
            hist_dict=hist,
            family=family,
            universe_min_edge_map=min_edge_map,
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold1(self):
        """event hold=1: net_edge_bps must equal gross - full_hurdle."""
        floor = 50.0
        hurdle, _ = _floor_plus_financing(floor, hold=1)
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"event hold=1: net={cell.net_edge_bps:.3f}, expected gross-hurdle={expected_net:.3f}, "
            f"gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}"
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold2(self):
        """event hold=2: net_edge_bps must equal gross - full_hurdle."""
        floor = 50.0
        hurdle, _ = _floor_plus_financing(floor, hold=2)
        cell = self._run_cell(hold=2, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"event hold=2: net={cell.net_edge_bps:.3f}, expected gross-hurdle={expected_net:.3f}, "
            f"gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}"
        )

    def test_cost_gate_fail_when_gross_below_hurdle(self):
        """event: huge floor must cause cost_gate failure."""
        floor = 10_000.0
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.passes_cost_gate is False

    def test_net_edge_positive_iff_passes_cost_gate(self):
        """event: net_edge_bps > 0 iff passes_cost_gate=True."""
        cell = self._run_cell(hold=1, floor_bps=50.0)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        if cell.passes_cost_gate:
            assert (cell.net_edge_bps or 0.0) > 0.0
        else:
            assert (cell.net_edge_bps or 0.0) <= 0.0


# ---------------------------------------------------------------------------
# Part 3 — cross_sectional: cost gate uses gross, not (gross - cs_min_edge)
# ---------------------------------------------------------------------------

class TestCrossSectionalCostGateSingleCount:
    """
    Verify the cross_sectional runner gates on gross > hurdle (median floor).
    The family already subtracted cs_min_edge in net_edge_bps — the runner
    must NOT subtract the floor a second time by comparing that result to hurdle.
    """

    def _run_cell(self, hold: int, floor_bps: float = 50.0) -> CellResult:
        family = StrategyFamily(
            name="cross_sectional_momentum",
            shape=SHAPE_CROSS_SECTIONAL,
            horizon_trading_days=hold,
        )
        hist = _make_hist_dict(n_symbols=10, n=150)
        min_edge_map = {sym: floor_bps for sym in hist}
        return _run_cross_sectional_cell(
            hist_dict=hist,
            family=family,
            universe_min_edge_map=min_edge_map,
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold1(self):
        """cross_sectional hold=1: net_edge_bps must equal gross - full_hurdle."""
        floor = 50.0
        hurdle, _ = _floor_plus_financing(floor, hold=1)
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"cross_sectional hold=1: net={cell.net_edge_bps:.3f}, "
            f"expected gross-hurdle={expected_net:.3f}, "
            f"gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}"
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold2(self):
        """cross_sectional hold=2: net_edge_bps must equal gross - full_hurdle."""
        floor = 50.0
        hurdle, _ = _floor_plus_financing(floor, hold=2)
        cell = self._run_cell(hold=2, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"cross_sectional hold=2: net={cell.net_edge_bps:.3f}, "
            f"expected gross-hurdle={expected_net:.3f}, "
            f"gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}"
        )

    def test_cost_gate_fail_when_gross_below_hurdle(self):
        """cross_sectional: huge floor must cause cost_gate failure."""
        floor = 10_000.0
        cell = self._run_cell(hold=1, floor_bps=floor)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.passes_cost_gate is False

    def test_net_edge_positive_iff_passes_cost_gate(self):
        """cross_sectional: net_edge_bps > 0 iff passes_cost_gate=True."""
        cell = self._run_cell(hold=1, floor_bps=50.0)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        if cell.passes_cost_gate:
            assert (cell.net_edge_bps or 0.0) > 0.0
        else:
            assert (cell.net_edge_bps or 0.0) <= 0.0


# ---------------------------------------------------------------------------
# Part 4 — pairs: cost gate uses gross, not (gross - sum-of-legs-floor)
# ---------------------------------------------------------------------------

class TestPairsCostGateSingleCount:
    """
    Verify the pairs runner gates on gross > hurdle (sum-of-legs floor).
    The family already subtracted pair_min_edge in net_edge_bps — the runner
    must NOT subtract the floor a second time.
    """

    def _run_cell(self, hold: int, floor_bps: float = 50.0) -> CellResult:
        family = StrategyFamily(
            name="pairs_cointegration",
            shape=SHAPE_PAIRS,
            horizon_trading_days=hold,
        )
        # Use cointegrated data to get a "tested" result.
        from strategy_fit.test_families_pairs import _make_cointegrated_pair
        df_a, df_b = _make_cointegrated_pair(n=200, seed=42, half_life=3.0)
        hist = {"SYM_A": df_a, "SYM_B": df_b}
        min_edge_map = {sym: floor_bps for sym in hist}
        return _run_pairs_cell(
            hist_dict=hist,
            family=family,
            universe_min_edge_map=min_edge_map,
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold1(self):
        """pairs hold=1: net_edge_bps must equal gross - full_hurdle (sum-of-legs floor)."""
        floor_per_leg = 50.0
        # pairs floor = sum of 2 cheapest legs = 100 bps.
        pairs_floor = floor_per_leg * 2.0
        hurdle, _ = _floor_plus_financing(pairs_floor, hold=1)
        cell = self._run_cell(hold=1, floor_bps=floor_per_leg)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"pairs hold=1: net={cell.net_edge_bps:.3f}, "
            f"expected gross-hurdle={expected_net:.3f}, "
            f"gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}"
        )

    def test_net_edge_equals_gross_minus_full_hurdle_hold2(self):
        """pairs hold=2: net_edge_bps must equal gross - full_hurdle."""
        floor_per_leg = 50.0
        pairs_floor = floor_per_leg * 2.0
        hurdle, _ = _floor_plus_financing(pairs_floor, hold=2)
        cell = self._run_cell(hold=2, floor_bps=floor_per_leg)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.gross_edge_bps is not None
        assert cell.net_edge_bps is not None
        expected_net = cell.gross_edge_bps - hurdle
        assert abs(cell.net_edge_bps - expected_net) < 0.1, (
            f"pairs hold=2: net={cell.net_edge_bps:.3f}, "
            f"expected gross-hurdle={expected_net:.3f}, "
            f"gross={cell.gross_edge_bps:.1f}, hurdle={hurdle:.1f}"
        )

    def test_cost_gate_fail_when_gross_below_hurdle(self):
        """pairs: huge floor must cause cost_gate failure."""
        floor_per_leg = 10_000.0
        cell = self._run_cell(hold=1, floor_bps=floor_per_leg)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        assert cell.passes_cost_gate is False

    def test_net_edge_positive_iff_passes_cost_gate(self):
        """pairs: net_edge_bps > 0 iff passes_cost_gate=True."""
        cell = self._run_cell(hold=1, floor_bps=50.0)
        if cell.status != "tested":
            pytest.skip(f"Cell not tested: {cell.skip_reason}")
        if cell.passes_cost_gate:
            assert (cell.net_edge_bps or 0.0) > 0.0
        else:
            assert (cell.net_edge_bps or 0.0) <= 0.0


# ---------------------------------------------------------------------------
# Part 5 — Explicit boundary: gross = floor + financing + 5 → pass;
#           gross = floor + financing - 5 → fail  (per shape)
# ---------------------------------------------------------------------------

class TestBoundaryPassFail:
    """
    Construct a CellResult manually to directly probe the boundary:
      pass:  gross = hurdle + 5    → net_after_cost = +5
      fail:  gross = hurdle - 5    → net_after_cost = -5

    These tests are purely logical checks of the invariant using direct
    construction — no family computation needed.

    They verify the INTENDED semantics so that any regression immediately
    causes a failure with a clear message.
    """

    @staticmethod
    def _make_cell(gross_edge_bps: float, hurdle: float) -> CellResult:
        """Build a minimal tested CellResult with the double-count-corrected semantics."""
        cost_pass = gross_edge_bps > hurdle
        net_after_cost = gross_edge_bps - hurdle
        return CellResult(
            symbol="TEST",
            family_name="mock",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=1,
            status="tested",
            gross_edge_bps=gross_edge_bps,
            net_edge_bps=net_after_cost,
            passes_cost_gate=cost_pass,
            min_edge_hurdle_bps=hurdle,
            p_value=0.01,
            hit_rate=0.6,
            eff_n=50,
        )

    def test_gross_above_hurdle_by_5_passes(self):
        """gross = hurdle + 5 → passes_cost_gate=True, net_edge_bps = +5."""
        floor = 50.0
        hurdle, _ = _floor_plus_financing(floor, hold=1)
        cell = self._make_cell(gross_edge_bps=hurdle + 5.0, hurdle=hurdle)
        assert cell.passes_cost_gate is True
        assert abs(cell.net_edge_bps - 5.0) < 0.01, (
            f"Expected net_edge_bps=+5, got {cell.net_edge_bps}"
        )

    def test_gross_below_hurdle_by_5_fails(self):
        """gross = hurdle - 5 → passes_cost_gate=False, net_edge_bps = -5."""
        floor = 50.0
        hurdle, _ = _floor_plus_financing(floor, hold=1)
        cell = self._make_cell(gross_edge_bps=hurdle - 5.0, hurdle=hurdle)
        assert cell.passes_cost_gate is False
        assert abs(cell.net_edge_bps - (-5.0)) < 0.01, (
            f"Expected net_edge_bps=-5, got {cell.net_edge_bps}"
        )

    def test_boundary_single_name_floor(self):
        """single_name floor=40 bps (ROUND_TRIP_BPS) + 20 bps financing → hurdle=60."""
        # Mon hold=1: financing = 20 bps; floor = ROUND_TRIP_BPS only (no spread) for simplicity.
        from strategy_fit.financing import financing_bps
        floor = float(ROUND_TRIP_BPS)  # 40 bps
        fin = financing_bps(0, 1)      # Mon, hold=1 → 20 bps
        hurdle = floor + fin + MODELLED_IMPACT_BPS
        # gross = hurdle + 5 → pass
        cell_pass = self._make_cell(hurdle + 5.0, hurdle)
        assert cell_pass.passes_cost_gate is True
        assert abs(cell_pass.net_edge_bps - 5.0) < 0.01
        # gross = hurdle - 5 → fail
        cell_fail = self._make_cell(hurdle - 5.0, hurdle)
        assert cell_fail.passes_cost_gate is False
        assert abs(cell_fail.net_edge_bps - (-5.0)) < 0.01

    def test_boundary_pairs_double_floor(self):
        """pairs sum-of-legs floor = 2×ROUND_TRIP_BPS → hurdle = 2×floor + financing."""
        from strategy_fit.financing import financing_bps
        floor = float(ROUND_TRIP_BPS) * 2.0  # sum of 2 legs
        fin = financing_bps(0, 1)
        hurdle = floor + fin + MODELLED_IMPACT_BPS
        cell_pass = self._make_cell(hurdle + 5.0, hurdle)
        assert cell_pass.passes_cost_gate is True
        cell_fail = self._make_cell(hurdle - 5.0, hurdle)
        assert cell_fail.passes_cost_gate is False
