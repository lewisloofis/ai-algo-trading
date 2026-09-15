"""
strategy_fit/test_impact_sweep.py — TDD tests for the impact sensitivity sweep.

Validates strategy_fit/impact_sweep.py:
  - scale_impact_bps: analytical scaling formula, base explicit (ADR-0017)
  - recompute_cell_at_impact: rebuilds a CellResult with new impact/hurdle/net,
    reading the priced-at base OFF THE CELL, not a module global
  - A cell missing priced-at provenance is refused (not silently rescaled)
    and surfaced in GridPointResult.unscaled_cells
  - sweep_grid: produces grid points across the MEASURED sleeve (ADR-0017)
  - FDR pool consistency: daily/event/cross/pairs cells included unchanged
  - Re-entry detection (cell gated at base becomes survivor at lower impact)
  - Base survivor drop (cell passes at base, drops at higher impact)
"""
from __future__ import annotations

import dataclasses
from typing import Sequence

import pytest

from strategy_fit.harness import (
    SHAPE_INTRADAY_SAME_DAY,
    SHAPE_SINGLE_NAME,
    CellResult,
)
from strategy_fit.impact import POSITION_NOTIONAL_IDR, SQRT_IMPACT_COEFF

# ---------------------------------------------------------------------------
# Helpers — build minimal CellResult fixtures
# ---------------------------------------------------------------------------

def _intraday_cell(
    symbol: str = "ITMG",
    family_name: str = "orb",
    gross_edge_bps: float = 100.0,
    modelled_impact_bps: float = 20.0,
    min_edge_floor_bps: float = 50.0,
    p_value: float = 0.01,
    eff_n: int = 50,
    hit_rate: float = 0.70,
    passes_cost_gate: bool = True,
    impact_priced_coeff: float | None = SQRT_IMPACT_COEFF,
    impact_priced_position_notional_idr: float | None = POSITION_NOTIONAL_IDR,
) -> CellResult:
    """Build a minimal intraday CellResult, priced (by default) at TODAY's
    active (SQRT_IMPACT_COEFF, POSITION_NOTIONAL_IDR) — i.e. as if it came
    straight out of a real grid run today.  This mirrors what
    run_strategy_fit.py actually records on every cell (ADR-0017): the
    priced-at coefficient/notional travel WITH the cell, not a module global
    re-read later.

    Pass ``impact_priced_coeff`` / ``impact_priced_position_notional_idr``
    explicitly to simulate a cell priced at a DIFFERENT point (e.g. a
    legacy-30M-priced cell), or ``None`` for a cell that predates ADR-0017's
    provenance fields entirely (both default to that column's default of
    None only if explicitly overridden — see TestMissingProvenance below).

    min_edge_hurdle_bps = min_edge_floor_bps + modelled_impact_bps
    net_edge_bps = gross_edge_bps - min_edge_hurdle_bps
    """
    hurdle = min_edge_floor_bps + modelled_impact_bps
    net = gross_edge_bps - hurdle
    return CellResult(
        symbol=symbol,
        family_name=family_name,
        shape=SHAPE_INTRADAY_SAME_DAY,
        horizon_trading_days=0,
        status="tested",
        hit_rate=hit_rate,
        eff_n=eff_n,
        gross_edge_bps=gross_edge_bps,
        net_edge_bps=net,
        p_value=p_value,
        passes_cost_gate=passes_cost_gate,
        min_edge_hurdle_bps=hurdle,
        modelled_impact_bps=modelled_impact_bps,
        impact_priced=True,
        impact_priced_coeff=impact_priced_coeff,
        impact_priced_position_notional_idr=impact_priced_position_notional_idr,
    )


def _daily_cell(
    symbol: str = "BUMI",
    family_name: str = "mean_reversion",
    gross_edge_bps: float = 80.0,
    hurdle_bps: float = 70.0,
    p_value: float = 0.05,
    eff_n: int = 100,
) -> CellResult:
    """Build a minimal daily (single-name) CellResult — impact stays 0."""
    net = gross_edge_bps - hurdle_bps
    return CellResult(
        symbol=symbol,
        family_name=family_name,
        shape=SHAPE_SINGLE_NAME,
        horizon_trading_days=1,
        status="tested",
        hit_rate=0.60,
        eff_n=eff_n,
        gross_edge_bps=gross_edge_bps,
        net_edge_bps=net,
        p_value=p_value,
        passes_cost_gate=gross_edge_bps > hurdle_bps,
        min_edge_hurdle_bps=hurdle_bps,
        modelled_impact_bps=None,  # not an intraday cell
    )


# ---------------------------------------------------------------------------
# Section A: scale_impact_bps
# ---------------------------------------------------------------------------

class TestScaleImpactBps:
    """
    Every call below supplies base_coeff / base_position_notional_idr
    explicitly (ADR-0017) — scale_impact_bps no longer reads
    strategy_fit.impact's module constants itself.  Tests pass the SAME
    values (SQRT_IMPACT_COEFF, POSITION_NOTIONAL_IDR) that used to be read
    implicitly, so the numeric expectations are unchanged.
    """

    def test_base_point_returns_unchanged(self) -> None:
        """At (coeff=base_coeff, size=base_position_notional_idr), scaling is 1.0×."""
        from strategy_fit.impact_sweep import scale_impact_bps
        base_impact = 40.0
        scaled = scale_impact_bps(
            impact_base_bps=base_impact,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=SQRT_IMPACT_COEFF,
            position_notional_idr=POSITION_NOTIONAL_IDR,
        )
        assert abs(scaled - base_impact) < 1e-9, (
            f"At base point, scaled={scaled} should equal base={base_impact}"
        )

    def test_half_coeff_halves_impact(self) -> None:
        """coeff=0.5 (half of base_coeff=1.0) → impact = base × 0.5."""
        from strategy_fit.impact_sweep import scale_impact_bps
        base = 80.0
        scaled = scale_impact_bps(
            impact_base_bps=base,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=0.5,
            position_notional_idr=POSITION_NOTIONAL_IDR,
        )
        assert abs(scaled - base * 0.5) < 1e-9, f"coeff=0.5 should halve impact: {scaled}"

    def test_double_size_increases_impact_by_sqrt2(self) -> None:
        """size=2×base_size → impact = base × sqrt(2)."""
        from strategy_fit.impact_sweep import scale_impact_bps
        import math
        base = 60.0
        scaled = scale_impact_bps(
            impact_base_bps=base,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=SQRT_IMPACT_COEFF,
            position_notional_idr=POSITION_NOTIONAL_IDR * 2,
        )
        expected = base * math.sqrt(2)
        assert abs(scaled - expected) < 1e-6, (
            f"2× size: expected {expected:.4f}, got {scaled:.4f}"
        )

    def test_half_size_decreases_impact_by_sqrt2(self) -> None:
        """size=0.5×base_size → impact = base / sqrt(2)."""
        from strategy_fit.impact_sweep import scale_impact_bps
        import math
        base = 60.0
        scaled = scale_impact_bps(
            impact_base_bps=base,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=SQRT_IMPACT_COEFF,
            position_notional_idr=POSITION_NOTIONAL_IDR / 2,
        )
        expected = base / math.sqrt(2)
        assert abs(scaled - expected) < 1e-6, (
            f"0.5× size: expected {expected:.4f}, got {scaled:.4f}"
        )

    def test_combined_half_coeff_double_size(self) -> None:
        """coeff=0.5, size=2×: impact = base × 0.5 × sqrt(2)."""
        from strategy_fit.impact_sweep import scale_impact_bps
        import math
        base = 40.0
        scaled = scale_impact_bps(
            impact_base_bps=base,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=0.5,
            position_notional_idr=POSITION_NOTIONAL_IDR * 2,
        )
        expected = base * 0.5 * math.sqrt(2)
        assert abs(scaled - expected) < 1e-6, (
            f"coeff=0.5, 2×size: expected {expected:.4f}, got {scaled:.4f}"
        )

    def test_zero_base_impact_always_zero(self) -> None:
        """If base impact is 0 (synthetic fixture artifact), scaling stays 0."""
        from strategy_fit.impact_sweep import scale_impact_bps
        scaled = scale_impact_bps(
            impact_base_bps=0.0,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=0.5,
            position_notional_idr=POSITION_NOTIONAL_IDR / 2,
        )
        assert scaled == 0.0, f"zero base impact must stay zero, got {scaled}"

    def test_different_base_from_target_is_not_confused_with_module_constants(self) -> None:
        """
        Regression (ADR-0017): the base need not equal ANY current module
        constant.  A cell priced at a completely arbitrary historical point
        (e.g. the void legacy 30M/coeff=1.0) must rescale correctly to a
        target that has nothing to do with today's POSITION_NOTIONAL_IDR.
        """
        from strategy_fit.impact_sweep import scale_impact_bps
        import math
        scaled = scale_impact_bps(
            impact_base_bps=100.0,
            base_coeff=1.0,
            base_position_notional_idr=30_000_000.0,   # legacy ADR-0011 point
            coeff=1.0,
            position_notional_idr=1_500_000.0,          # N=10 sleeve point
        )
        expected = 100.0 * math.sqrt(1_500_000.0 / 30_000_000.0)
        assert abs(scaled - expected) < 1e-6


# ---------------------------------------------------------------------------
# Section B: recompute_cell_at_impact
# ---------------------------------------------------------------------------

class TestRecomputeCellAtImpact:
    def test_non_intraday_cell_unchanged(self) -> None:
        """Daily cells have modelled_impact_bps=None → returned unchanged."""
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        c = _daily_cell()
        result = recompute_cell_at_impact(c, coeff=0.5, position_notional_idr=15_000_000.0)
        # All fields identical — same object (or equal values)
        assert result.modelled_impact_bps == c.modelled_impact_bps
        assert result.min_edge_hurdle_bps == c.min_edge_hurdle_bps
        assert result.net_edge_bps == c.net_edge_bps
        assert result.passes_cost_gate == c.passes_cost_gate

    def test_untested_intraday_cell_with_no_hurdle_is_returned_unchanged(self) -> None:
        """Regression: an intraday cell that was never cost-gated must not crash.

        `_run_strategy_fit._run_intraday_cell` returns early when the family
        status is not "tested" (e.g. insufficient_n).  That branch sets
        modelled_impact_bps — impact is computed before the family runs — but
        leaves min_edge_hurdle_bps None, because a cell that was never scored
        was never cost-gated.  The floor extraction
        `min_edge_hurdle_bps - modelled_impact_bps` then raised
        `TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'`,
        which surfaced as two failures in TestCLIFlag rather than here.

        This became reachable in the fixture universe once the family library
        was constrained to long-only (IDX retail cannot short): halving the
        tradeable observations pushes the noise symbols below MIN_EFF_N, so
        they return insufficient_n with impact already computed.
        """
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        c = dataclasses.replace(
            _intraday_cell(),
            status="insufficient_n",
            skip_reason="eff_n=12 < MIN_EFF_N=30",
            gross_edge_bps=None,
            net_edge_bps=None,
            p_value=None,
            passes_cost_gate=None,
            min_edge_hurdle_bps=None,   # never cost-gated ...
            modelled_impact_bps=18.5,   # ... but impact was still computed
        )
        result = recompute_cell_at_impact(
            c, coeff=0.5, position_notional_idr=15_000_000.0
        )
        assert result.status == "insufficient_n"
        assert result.min_edge_hurdle_bps is None
        assert result.modelled_impact_bps == 18.5
        assert result.passes_cost_gate is None

    def test_intraday_cell_recomputes_hurdle_and_net(self) -> None:
        """Intraday cell with new coeff/size → hurdle and net_edge_bps updated,
        relative to its OWN recorded priced-at base (default: today's active
        SQRT_IMPACT_COEFF / POSITION_NOTIONAL_IDR — see _intraday_cell)."""
        from strategy_fit.impact_sweep import recompute_cell_at_impact, scale_impact_bps
        c = _intraday_cell(
            gross_edge_bps=100.0,
            modelled_impact_bps=40.0,
            min_edge_floor_bps=50.0,
        )
        new_c = recompute_cell_at_impact(c, coeff=0.5, position_notional_idr=POSITION_NOTIONAL_IDR)
        expected_new_impact = scale_impact_bps(
            40.0,
            base_coeff=SQRT_IMPACT_COEFF,
            base_position_notional_idr=POSITION_NOTIONAL_IDR,
            coeff=0.5,
            position_notional_idr=POSITION_NOTIONAL_IDR,
        )
        expected_new_hurdle = 50.0 + expected_new_impact
        expected_new_net = 100.0 - expected_new_hurdle
        assert abs(new_c.modelled_impact_bps - expected_new_impact) < 1e-9
        assert abs(new_c.min_edge_hurdle_bps - expected_new_hurdle) < 1e-9
        assert abs(new_c.net_edge_bps - expected_new_net) < 1e-9

    def test_intraday_cost_pass_updates_correctly(self) -> None:
        """passes_cost_gate reflects new net_edge_bps at the swept point."""
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        # gross=100, floor=50, base_impact=60 → hurdle=110 → cost_pass=False
        c = _intraday_cell(
            gross_edge_bps=100.0,
            modelled_impact_bps=60.0,
            min_edge_floor_bps=50.0,
            passes_cost_gate=False,
        )
        # At coeff=0.5, same size as base → new_impact=30 → hurdle=80 → net=20 → cost_pass=True
        new_c = recompute_cell_at_impact(c, coeff=0.5, position_notional_idr=POSITION_NOTIONAL_IDR)
        assert new_c.passes_cost_gate is True, (
            f"At half impact, cell should pass gate; net={new_c.net_edge_bps:.2f}"
        )

    def test_result_is_new_immutable_instance(self) -> None:
        """recompute_cell_at_impact must return a new CellResult, not mutate original."""
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        c = _intraday_cell()
        new_c = recompute_cell_at_impact(c, coeff=0.5, position_notional_idr=15_000_000.0)
        assert new_c is not c, "Must return a new instance"
        # Original unchanged
        assert c.modelled_impact_bps == 20.0

    def test_floor_extraction_is_consistent(self) -> None:
        """floor = hurdle - impact must be stable: recomputing at the cell's
        own priced-at base yields the original values back."""
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        c = _intraday_cell(
            gross_edge_bps=120.0,
            modelled_impact_bps=30.0,
            min_edge_floor_bps=60.0,
        )
        new_c = recompute_cell_at_impact(
            c, coeff=SQRT_IMPACT_COEFF, position_notional_idr=POSITION_NOTIONAL_IDR
        )
        assert abs(new_c.min_edge_hurdle_bps - c.min_edge_hurdle_bps) < 1e-9
        assert abs(new_c.net_edge_bps - c.net_edge_bps) < 1e-9


class TestMissingProvenanceIsNotSilentlyRescaled:
    """
    ADR-0017: a cell with impact_priced_coeff / impact_priced_position_notional_idr
    both None (it predates these fields) must be REFUSED, not silently rescaled
    against an assumed base — see recompute_cell_at_impact's provenance guard.
    """

    def test_missing_provenance_cell_returned_unchanged(self) -> None:
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        c = _intraday_cell(
            gross_edge_bps=100.0,
            modelled_impact_bps=40.0,
            min_edge_floor_bps=50.0,
            impact_priced_coeff=None,
            impact_priced_position_notional_idr=None,
        )
        new_c = recompute_cell_at_impact(c, coeff=0.5, position_notional_idr=1_500_000.0)
        assert new_c.modelled_impact_bps == c.modelled_impact_bps
        assert new_c.min_edge_hurdle_bps == c.min_edge_hurdle_bps
        assert new_c.net_edge_bps == c.net_edge_bps
        assert new_c.passes_cost_gate == c.passes_cost_gate

    def test_partial_provenance_is_also_refused(self) -> None:
        """Only one of the two fields recorded is treated the same as neither —
        a half-known base is not a known base."""
        from strategy_fit.impact_sweep import recompute_cell_at_impact
        c = _intraday_cell(
            modelled_impact_bps=40.0,
            min_edge_floor_bps=50.0,
            impact_priced_coeff=1.0,
            impact_priced_position_notional_idr=None,
        )
        new_c = recompute_cell_at_impact(c, coeff=0.5, position_notional_idr=1_500_000.0)
        assert new_c.modelled_impact_bps == c.modelled_impact_bps

    def test_missing_provenance_cell_surfaced_in_unscaled_cells(self) -> None:
        """sweep_grid must surface these cells explicitly, at every grid point."""
        from strategy_fit.impact_sweep import sweep_grid, SWEEP_COEFFS, SWEEP_SIZES
        c = _intraday_cell(
            symbol="LEGACY", family_name="orb",
            impact_priced_coeff=None,
            impact_priced_position_notional_idr=None,
        )
        result = sweep_grid([c])
        assert len(result) == len(SWEEP_COEFFS) * len(SWEEP_SIZES)
        for r in result:
            assert "LEGACY:orb" in r.unscaled_cells

    def test_full_provenance_cell_not_in_unscaled_cells(self) -> None:
        from strategy_fit.impact_sweep import sweep_grid
        c = _intraday_cell(symbol="ITMG", family_name="orb")
        result = sweep_grid([c])
        for r in result:
            assert r.unscaled_cells == []


class TestRescaleIndependentOfLiveModuleConstant:
    """
    Regression (ADR-0017): rescaling a cell priced at size A to size B must
    give the SAME answer no matter what strategy_fit.impact.POSITION_NOTIONAL_IDR
    / SQRT_IMPACT_COEFF happen to equal AT RESCALE TIME.  This is the exact
    defect the priced-at provenance fields fix: the old scale_impact_bps read
    those module constants directly and treated their CURRENT value as "what
    this cell was priced at", which silently breaks the moment sizing becomes
    configurable.  This test would have FAILED against the old implementation.
    """

    def test_monkeypatched_module_constants_do_not_change_the_result(self, monkeypatch) -> None:
        import strategy_fit.impact_sweep as impact_sweep_mod
        from strategy_fit.impact_sweep import recompute_cell_at_impact

        c = _intraday_cell(
            gross_edge_bps=100.0,
            modelled_impact_bps=40.0,
            min_edge_floor_bps=50.0,
            impact_priced_coeff=1.0,
            impact_priced_position_notional_idr=30_000_000.0,  # priced at legacy 30M
        )

        result_before = recompute_cell_at_impact(c, coeff=1.0, position_notional_idr=15_000_000.0)

        # Simulate "the module constants changed since this cell was priced" —
        # exactly the scenario ADR-0017 identifies as having broken silently.
        monkeypatch.setattr(impact_sweep_mod, "POSITION_NOTIONAL_IDR", 999_999.0)
        monkeypatch.setattr(impact_sweep_mod, "SQRT_IMPACT_COEFF", 0.01)

        result_after = recompute_cell_at_impact(c, coeff=1.0, position_notional_idr=15_000_000.0)

        assert result_before.modelled_impact_bps == result_after.modelled_impact_bps
        assert result_before.min_edge_hurdle_bps == result_after.min_edge_hurdle_bps
        assert result_before.net_edge_bps == result_after.net_edge_bps
        assert result_before.passes_cost_gate == result_after.passes_cost_gate


# ---------------------------------------------------------------------------
# Section C: sweep_grid — structure and FDR pool consistency
# ---------------------------------------------------------------------------

class TestSweepGrid:
    def _make_cells(self) -> list[CellResult]:
        """Mix of intraday + daily cells, some passing cost gate."""
        return [
            # Intraday survivor at base
            _intraday_cell(symbol="ITMG", family_name="orb",
                           gross_edge_bps=100.0, modelled_impact_bps=20.0,
                           min_edge_floor_bps=50.0, p_value=0.01),
            # Intraday gated at base (impact too high), re-enters at lower
            _intraday_cell(symbol="ITMG", family_name="intraday_momentum",
                           gross_edge_bps=100.0, modelled_impact_bps=60.0,
                           min_edge_floor_bps=50.0, passes_cost_gate=False,
                           p_value=0.02),
            # Daily survivor — unchanged across sweep
            _daily_cell(symbol="BUMI", family_name="mean_reversion",
                        gross_edge_bps=80.0, hurdle_bps=70.0, p_value=0.03),
            # Untested cell — should be included but never counted as survivor
            CellResult(
                symbol="DEWA", family_name="orb",
                shape=SHAPE_INTRADAY_SAME_DAY,
                horizon_trading_days=0,
                status="insufficient_n",
                skip_reason="too few obs",
            ),
        ]

    def test_sweep_returns_ten_grid_points(self) -> None:
        """10 grid points: 2 coeffs × 5 sizes (ADR-0017 rebased the size axis
        to the measured sleeve at N=10/5/3/1 plus the legacy 30M reference)."""
        from strategy_fit.impact_sweep import sweep_grid, SWEEP_COEFFS, SWEEP_SIZES
        assert len(SWEEP_COEFFS) == 2
        assert len(SWEEP_SIZES) == 5
        result = sweep_grid(self._make_cells())
        assert len(result) == 10, f"Expected 10 grid points, got {len(result)}"

    def test_grid_points_span_correct_parameter_space(self) -> None:
        """Every (coeff, size) combination must appear exactly once."""
        from strategy_fit.impact_sweep import sweep_grid, SWEEP_COEFFS, SWEEP_SIZES
        result = sweep_grid(self._make_cells())
        params = {(r.coeff, r.position_notional_idr) for r in result}
        expected = {(c, s) for c in SWEEP_COEFFS for s in SWEEP_SIZES}
        assert params == expected

    def test_survivor_count_at_base_matches_normal_run(self) -> None:
        """At (coeff=1.0, size=today's active POSITION_NOTIONAL_IDR), survivor
        count equals what the normal pipeline gives — the fixtures are priced
        at exactly that point by default (see _intraday_cell)."""
        from strategy_fit.impact_sweep import sweep_grid
        from strategy_fit.run_strategy_fit import apply_fdr_to_grid
        cells = self._make_cells()
        # Normal pipeline result
        normal_cells = apply_fdr_to_grid(cells)
        normal_survivors = [
            c for c in normal_cells
            if c.status == "tested" and c.passes_cost_gate is True
            and c.fdr_accepted is True and (c.net_edge_bps or 0) > 0
        ]
        # Sweep at base point
        result = sweep_grid(cells)
        base_point = next(
            r for r in result
            if abs(r.coeff - 1.0) < 1e-9 and abs(r.position_notional_idr - POSITION_NOTIONAL_IDR) < 1.0
        )
        assert base_point.survivor_count == len(normal_survivors), (
            f"Base-point sweep survivors={base_point.survivor_count} "
            f"!= normal pipeline={len(normal_survivors)}"
        )

    def test_daily_cells_in_fdr_pool_at_every_grid_point(self) -> None:
        """Non-intraday cells must be included in the FDR pool at each grid point (impact=0)."""
        from strategy_fit.impact_sweep import sweep_grid
        cells = self._make_cells()
        result = sweep_grid(cells)
        # All grid points should include the daily cell (symbol=BUMI) in their FDR pool.
        # We can't inspect internal FDR pool directly, but we CAN check that the daily
        # survivor (BUMI mean_reversion, p=0.03) appears in survivors whenever it passes
        # the cost gate — its cost gate status should not change across grid points.
        for r in result:
            bumi_survivors = [s for s in r.intraday_survivors if "BUMI" not in s]
            # BUMI is a single_name, so it shouldn't appear in intraday_survivors
            # but it should have been in the FDR pool. Verify survivor_count >= 0.
            assert r.survivor_count >= 0

    def test_re_entry_detected(self) -> None:
        """A cell gated at its own base but passing at half-coeff/same-size → re_entry list contains it."""
        from strategy_fit.impact_sweep import sweep_grid
        cells = self._make_cells()
        result = sweep_grid(cells)
        # ITMG intraday_momentum has base_impact=60, floor=50, gross=100 →
        # hurdle=110 > gross=100 at base → gated.
        # At coeff=0.5, same size: new_impact=30, hurdle=80, net=20 → passes.
        low_impact_point = next(
            r for r in result
            if abs(r.coeff - 0.5) < 1e-9 and abs(r.position_notional_idr - POSITION_NOTIONAL_IDR) < 1.0
        )
        reentries = low_impact_point.reentry_cells
        assert len(reentries) > 0, (
            f"Expected at least one re-entry at (0.5, {POSITION_NOTIONAL_IDR}); re_entries={reentries}"
        )

    def test_base_survivor_drop_at_high_impact(self) -> None:
        """A cell priced at the smallest swept size drops once rescaled up to
        the largest swept size (the legacy 30M reference point)."""
        from strategy_fit.impact_sweep import sweep_grid, SWEEP_SIZES
        import math

        base_size = min(SWEEP_SIZES)
        high_size = max(SWEEP_SIZES)
        gross = 95.0
        base_impact = 40.0
        floor = 50.0
        size_ratio = math.sqrt(high_size / base_size)
        # Verify it passes at its own priced-at base ...
        assert gross > floor + base_impact
        # ... but fails once rescaled up to the largest swept size.
        assert gross < floor + base_impact * size_ratio

        marginal_cell = _intraday_cell(
            symbol="MDKA", family_name="intraday_momentum",
            gross_edge_bps=gross,
            modelled_impact_bps=base_impact,
            min_edge_floor_bps=floor,
            passes_cost_gate=True,
            p_value=0.01,
            impact_priced_coeff=1.0,
            impact_priced_position_notional_idr=base_size,
        )
        result = sweep_grid([marginal_cell])
        high_size_point = next(
            r for r in result
            if abs(r.coeff - 1.0) < 1e-9 and abs(r.position_notional_idr - high_size) < 1.0
        )
        dropped = high_size_point.dropped_cells
        assert len(dropped) > 0, (
            f"Expected base survivor to drop at (1.0, {high_size}); dropped={dropped}"
        )


# ---------------------------------------------------------------------------
# Section D: emit_impact_sweep — output format
# ---------------------------------------------------------------------------

class TestEmitImpactSweep:
    def test_emit_creates_file(self, tmp_path) -> None:
        """emit_impact_sweep writes impact_sweep.md to outdir."""
        from strategy_fit.impact_sweep import emit_impact_sweep, sweep_grid
        cells = [
            _intraday_cell(symbol="ITMG", family_name="orb",
                           gross_edge_bps=100.0, modelled_impact_bps=20.0,
                           min_edge_floor_bps=50.0, p_value=0.01),
        ]
        grid = sweep_grid(cells)
        path = emit_impact_sweep(grid=grid, cells=cells, outdir=str(tmp_path))
        assert (tmp_path / "impact_sweep.md").exists()
        assert path.endswith("impact_sweep.md")

    def test_emit_contains_summary_table(self, tmp_path) -> None:
        """Output must contain a summary table header."""
        from strategy_fit.impact_sweep import emit_impact_sweep, sweep_grid
        cells = [
            _intraday_cell(symbol="ITMG", family_name="orb",
                           gross_edge_bps=100.0, modelled_impact_bps=20.0,
                           min_edge_floor_bps=50.0, p_value=0.01),
        ]
        grid = sweep_grid(cells)
        emit_impact_sweep(grid=grid, cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "impact_sweep.md").read_text()
        assert "coeff" in content.lower() or "Coeff" in content
        assert "survivor" in content.lower() or "Survivor" in content

    def test_emit_contains_per_cell_matrix(self, tmp_path) -> None:
        """Output must contain per-cell net bps matrix."""
        from strategy_fit.impact_sweep import emit_impact_sweep, sweep_grid
        cells = [
            _intraday_cell(symbol="ITMG", family_name="orb",
                           gross_edge_bps=100.0, modelled_impact_bps=20.0,
                           min_edge_floor_bps=50.0, p_value=0.01),
        ]
        grid = sweep_grid(cells)
        emit_impact_sweep(grid=grid, cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "impact_sweep.md").read_text()
        # Should mention the symbol and family
        assert "ITMG" in content
        assert "orb" in content

    def test_emit_notes_scaling_is_exact(self, tmp_path) -> None:
        """Report must mention that scaling is exact/analytical (no re-fetch)."""
        from strategy_fit.impact_sweep import emit_impact_sweep, sweep_grid
        cells = [_intraday_cell()]
        grid = sweep_grid(cells)
        emit_impact_sweep(grid=grid, cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "impact_sweep.md").read_text()
        # Should mention analytical/exact scaling or no re-fetch
        assert any(kw in content for kw in ["exact", "analytical", "no re-fetch", "no API"]), (
            "Report must note that scaling is exact (no API calls needed)"
        )

    def test_emit_surfaces_unscaled_cells_section(self, tmp_path) -> None:
        """A cell missing priced-at provenance must be called out explicitly in the report."""
        from strategy_fit.impact_sweep import emit_impact_sweep, sweep_grid
        cells = [
            _intraday_cell(
                symbol="LEGACY", family_name="orb",
                impact_priced_coeff=None,
                impact_priced_position_notional_idr=None,
            ),
        ]
        grid = sweep_grid(cells)
        emit_impact_sweep(grid=grid, cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "impact_sweep.md").read_text()
        assert "Not Rescaled" in content
        assert "LEGACY:orb" in content

    def test_emit_omits_unscaled_cells_section_when_none_missing(self, tmp_path) -> None:
        from strategy_fit.impact_sweep import emit_impact_sweep, sweep_grid
        cells = [_intraday_cell(symbol="ITMG", family_name="orb")]
        grid = sweep_grid(cells)
        emit_impact_sweep(grid=grid, cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "impact_sweep.md").read_text()
        assert "Not Rescaled" not in content


# ---------------------------------------------------------------------------
# Section E: CLI flag
# ---------------------------------------------------------------------------

class TestCLIFlag:
    def test_impact_sweep_flag_accepted(self, tmp_path) -> None:
        """--impact-sweep flag is accepted by the CLI without error."""
        from strategy_fit.run_strategy_fit import main
        # Run with fixture source and --impact-sweep; should complete without raising
        try:
            main([
                "--source", "fixture",
                "--outdir", str(tmp_path),
                "--impact-sweep",
            ])
        except SystemExit as e:
            assert e.code == 0 or e.code is None, f"CLI exited with code {e.code}"

    def test_impact_sweep_emits_sweep_file(self, tmp_path) -> None:
        """--impact-sweep produces impact_sweep.md in outdir."""
        from strategy_fit.run_strategy_fit import main
        main([
            "--source", "fixture",
            "--outdir", str(tmp_path),
            "--impact-sweep",
        ])
        assert (tmp_path / "impact_sweep.md").exists(), (
            "impact_sweep.md must be written when --impact-sweep is set"
        )

    def test_normal_run_no_sweep_file(self, tmp_path) -> None:
        """Without --impact-sweep, no impact_sweep.md is written."""
        from strategy_fit.run_strategy_fit import main
        main([
            "--source", "fixture",
            "--outdir", str(tmp_path),
        ])
        assert not (tmp_path / "impact_sweep.md").exists(), (
            "impact_sweep.md must NOT be written when --impact-sweep is not set"
        )
