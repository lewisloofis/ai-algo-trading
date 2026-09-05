"""Tests for the W2.1 blocking gates in strategy_fit.report.

Roadmap (docs/plans/00-roadmap-research-to-live.md §6, W2.1): the daily
harness's strategy_portfolio.md reported PASS while 9 of 11 "survivors" carried
OOS Eff-N=0 and the other 2 carried Impact=0.0 that was never actually priced.
The gates annotated but could not change the verdict — a footnote, not a gate.

This file covers the two new hard prerequisites for PASS, on top of the
pre-existing cost-gate + BH-FDR requirement (see test_multiple_testing.py):

1. OOS power gate — a cell's oos_eff_n (when measured) must clear
   MIN_OOS_EFF_N, the hard not-zero floor.  Below it: never PASS.
2. Impact-priced gate — a cell whose market impact was never priced
   (impact_priced=False) must be excluded from PASS, not passed with an
   assumed 0.0.

Both gates are additive to (not a replacement for) the existing cost-gate /
FDR requirements.
"""
from __future__ import annotations

from strategy_fit.harness import CellResult, SHAPE_INTRADAY_SAME_DAY, SHAPE_SINGLE_NAME
from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N
from strategy_fit.report import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_GO,
    VERDICT_PASS,
    compute_verdict,
    emit_strategy_portfolio,
)


def _base_kwargs(**overrides) -> dict:
    """Common fields for a cost-gate-and-FDR-passing 'tested' cell."""
    base = dict(
        symbol="ITMG",
        family_name="orb",
        shape=SHAPE_INTRADAY_SAME_DAY,
        horizon_trading_days=0,
        status="tested",
        passes_cost_gate=True,
        fdr_accepted=True,
        net_edge_bps=50.0,
        gross_edge_bps=90.0,
        p_value=0.001,
        hit_rate=0.65,
        eff_n=60,
        min_edge_hurdle_bps=40.0,
        modelled_impact_bps=20.0,
        impact_priced=True,
        oos_eff_n=20,
    )
    base.update(overrides)
    return base


def _cell(**overrides) -> CellResult:
    return CellResult(**_base_kwargs(**overrides))


# ---------------------------------------------------------------------------
# Gate 1 — OOS effective-N below the hard floor
# ---------------------------------------------------------------------------

class TestOosEffNGate:
    def test_oos_eff_n_zero_blocks_pass(self):
        """The exact live-run bug: oos_eff_n=0 must never yield PASS."""
        cells = [_cell(oos_eff_n=0)]
        assert compute_verdict(cells) != VERDICT_PASS
        assert compute_verdict(cells) == VERDICT_INCONCLUSIVE

    def test_oos_eff_n_below_floor_blocks_pass(self):
        cells = [_cell(oos_eff_n=MIN_OOS_EFF_N - 1)]
        assert compute_verdict(cells) == VERDICT_INCONCLUSIVE

    def test_oos_eff_n_at_floor_allows_pass(self):
        """Clearing the floor exactly (>=) must be sufficient, not >."""
        cells = [_cell(oos_eff_n=MIN_OOS_EFF_N)]
        assert compute_verdict(cells) == VERDICT_PASS

    def test_oos_eff_n_above_floor_allows_pass(self):
        cells = [_cell(oos_eff_n=MIN_OOS_EFF_N + 10)]
        assert compute_verdict(cells) == VERDICT_PASS

    def test_oos_eff_n_none_does_not_block_pass(self):
        """oos_eff_n=None means 'not measured for this shape' — gate doesn't apply.

        Daily / event / cross-sectional / pairs shapes never run an intraday
        OOS split, so oos_eff_n is always None for them; that must not be
        conflated with oos_eff_n=0 (measured, and thin).
        """
        cells = [_cell(
            shape=SHAPE_SINGLE_NAME, horizon_trading_days=1, oos_eff_n=None,
        )]
        assert compute_verdict(cells) == VERDICT_PASS

    def test_below_floor_cell_still_appears_as_cost_gate_passer(self, tmp_path):
        """Excluded from PASS ≠ silently dropped — must show up annotated."""
        cells = [_cell(oos_eff_n=0)]
        path = emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = open(path).read()
        assert "INCONCLUSIVE" in content
        assert "*No cells survived the full gate and FDR correction.*" in content
        assert "oos_underfloor" in content


# ---------------------------------------------------------------------------
# Gate 2 — impact never priced
# ---------------------------------------------------------------------------

class TestImpactPricedGate:
    def test_impact_not_priced_blocks_pass(self):
        """The other live-run bug: a daily cell with impact_priced=False
        (impact assumed 0, never measured) must never yield PASS."""
        cells = [_cell(
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=1,
            oos_eff_n=None,
            impact_priced=False,
            modelled_impact_bps=None,
        )]
        assert compute_verdict(cells) == VERDICT_INCONCLUSIVE

    def test_impact_priced_true_allows_pass(self):
        cells = [_cell(
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=1,
            oos_eff_n=None,
            impact_priced=True,
        )]
        assert compute_verdict(cells) == VERDICT_PASS

    def test_impact_priced_default_is_false(self):
        """Default must be the conservative 'not priced' state."""
        c = CellResult(
            symbol="X", family_name="y", shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=1, status="tested",
        )
        assert c.impact_priced is False

    def test_unpriced_cell_excluded_from_survivor_table_but_annotated(self, tmp_path):
        cells = [_cell(
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=1,
            oos_eff_n=None,
            impact_priced=False,
            modelled_impact_bps=None,
        )]
        path = emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = open(path).read()
        assert "*No cells survived the full gate and FDR correction.*" in content
        assert "impact_not_priced" in content

    def test_zero_priced_impact_is_not_the_same_as_not_priced(self):
        """A genuinely-priced 0.0 impact (impact_priced=True) must PASS —
        do not conflate 'priced at zero' with 'never priced'."""
        cells = [_cell(modelled_impact_bps=0.0, impact_priced=True)]
        assert compute_verdict(cells) == VERDICT_PASS


# ---------------------------------------------------------------------------
# Combined: reproduces the exact strategy_portfolio.md bug (11 "survivors")
# ---------------------------------------------------------------------------

class TestCombinedRealisticBugScenario:
    def test_nine_thin_intraday_and_two_unpriced_daily_yields_inconclusive_not_pass(self):
        """
        Reproduces research/out_live/strategy_portfolio.md at HEAD: 9 intraday
        cells with oos_eff_n=0 and 2 daily cells with impact never priced, all
        otherwise passing cost gate + FDR.  Before W2.1 this was PASS; after,
        it must be INCONCLUSIVE (cost-gate passers exist, none are eligible).
        """
        intraday_cells = [
            _cell(symbol=f"SYM{i}", oos_eff_n=0)
            for i in range(9)
        ]
        daily_cells = [
            _cell(
                symbol=f"DLY{i}",
                shape=SHAPE_SINGLE_NAME,
                horizon_trading_days=2,
                oos_eff_n=None,
                impact_priced=False,
                modelled_impact_bps=None,
            )
            for i in range(2)
        ]
        cells = intraday_cells + daily_cells
        assert compute_verdict(cells) == VERDICT_INCONCLUSIVE

    def test_one_genuinely_eligible_cell_among_the_noise_yields_pass(self):
        """A single properly-powered, impact-priced cell among the noise is
        enough to flip the verdict back to PASS — the gates block bad
        evidence, they don't just always force INCONCLUSIVE."""
        cells = [
            _cell(symbol=f"SYM{i}", oos_eff_n=0)
            for i in range(9)
        ] + [_cell(symbol="GOOD", oos_eff_n=30)]
        assert compute_verdict(cells) == VERDICT_PASS

    def test_all_cost_gate_failures_yields_no_go_regardless_of_new_gates(self):
        """NO-GO must still be reachable — new gates don't mask a total failure."""
        cells = [_cell(passes_cost_gate=False, net_edge_bps=-10.0)]
        assert compute_verdict(cells) == VERDICT_NO_GO
