"""Tests for the W2.1 blocking gates in strategy_fit.report.

Roadmap (docs/plans/00-roadmap-research-to-live.md §6, W2.1): the daily
harness's strategy_portfolio.md reported PASS while 9 of 11 "survivors" carried
OOS Eff-N=0 and the other 2 carried Impact=0.0 that was never actually priced.
The gates annotated but could not change the verdict — a footnote, not a gate.

This file covers the hard prerequisites for PASS, on top of the pre-existing
cost-gate + BH-FDR requirement (see test_multiple_testing.py):

1. OOS power gate — a cell's oos_eff_n (when measured) must clear
   MIN_OOS_EFF_N, the hard not-zero floor.  Below it: never PASS.
2. Impact-priced gate — a cell whose market impact was never priced
   (impact_priced=False) must be excluded from PASS, not passed with an
   assumed 0.0.
3. Always-long null gate (TestNullEdgeGate) — a cell's edge_over_null_bps
   (gross edge minus the same population with the signal deleted) must beat
   min_edge_hurdle_bps.  A PTRO daily_momentum cell passed cost gate, BH-FDR,
   impact-priced, and OOS persistence while ~62% beta (PTRO ran 182 -> 5,400
   in-sample): its signal contributed +45.1 bps against a 110.2 bps hurdle,
   losing to an always-long null of +74.3 bps/trade. A 47-name breadth test
   found the family's true mean edge was +0.1 bps (t=0.01).

All three gates are additive to (not a replacement for) the existing
cost-gate / FDR requirements.
"""
from __future__ import annotations

from strategy_fit.harness import (
    CellResult,
    SHAPE_CROSS_SECTIONAL,
    SHAPE_EVENT_DRIVEN,
    SHAPE_INTRADAY_SAME_DAY,
    SHAPE_PAIRS,
    SHAPE_SINGLE_NAME,
)
from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N
from strategy_fit.run_strategy_fit import apply_fdr_to_grid
import strategy_fit.report as report_mod
from strategy_fit.report import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_GO,
    VERDICT_PASS,
    _clears_null_gate,
    _clears_oos_power_gate,
    _is_pass_eligible,
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
        # Always-long null gate (plan): base fixture must clear this gate too,
        # same as it already clears cost/FDR/impact/OOS — gross=90.0 vs
        # null=40.0 leaves edge_over_null=50.0, comfortably over the 40.0
        # hurdle, so tests unrelated to the null gate aren't blocked by it.
        null_edge_bps=40.0,
        edge_over_null_bps=50.0,
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


class TestOosPersistenceIsBlocking:
    """plan 13: a MEASURED non-persistent OOS verdict must block PASS.

    Regression for a real defect caught on the first plan-13 live run.  The
    OOS power gate only asks whether the OOS sample is BIG ENOUGH; it never
    asked what the sample SAID.  Three cells (BREN, RATU, WIFI) cleared the
    floor with 49-60 out-of-sample observations, reported NEGATIVE OOS edges
    of -87.9 / -53.9 / -21.9 bps, and were still counted as survivors toward
    a PASS verdict — the same in-sample-only edge that killed intraday ORB,
    reproduced by the gate built to prevent it.
    """

    def test_negative_oos_edge_above_floor_blocks_pass(self):
        """The exact BREN shape: plenty of OOS data, edge did not survive it."""
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            family_name="daily_momentum",
            horizon_trading_days=2,
            oos_eff_n=49,
            oos_edge_bps=-87.9,
            oos_persistent=False,
        )
        assert _clears_oos_power_gate(c) is True, (
            "precondition: 49 observations clears the hard floor — this cell "
            "fails on the VERDICT, not on sample size"
        )
        assert _is_pass_eligible(c) is False

    def test_positive_oos_edge_above_floor_allows_pass(self):
        """The PTRO shape: edge survived out-of-sample."""
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            family_name="daily_momentum",
            horizon_trading_days=2,
            oos_eff_n=53,
            oos_edge_bps=209.5,
            oos_persistent=True,
        )
        assert _is_pass_eligible(c) is True

    def test_underpowered_but_persistent_still_allows_pass(self):
        """UNDERPOWERED is an honest annotation, not a disqualification."""
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            oos_eff_n=67,
            oos_edge_bps=106.7,
            oos_persistent=True,
            oos_underpowered=True,
        )
        assert _is_pass_eligible(c) is True

    def test_oos_persistent_none_does_not_block(self):
        """None = no split applies to this shape; the impact gate handles those."""
        c = _cell(oos_eff_n=None, oos_persistent=None)
        assert _is_pass_eligible(c) is True

    def test_verdict_downgrades_to_inconclusive_when_only_non_persistent_cells(self):
        """A grid whose only cost-gate passers failed OOS is INCONCLUSIVE, not PASS."""
        cells = [
            _cell(
                shape=SHAPE_SINGLE_NAME,
                symbol=sym,
                oos_eff_n=n,
                oos_edge_bps=edge,
                oos_persistent=False,
            )
            for sym, n, edge in (("BREN", 49, -87.9), ("RATU", 60, -53.9), ("WIFI", 59, -21.9))
        ]
        assert compute_verdict(cells) == VERDICT_INCONCLUSIVE


# ---------------------------------------------------------------------------
# Gate 3 — always-long null: the signal must beat a "signal ignored" null
# ---------------------------------------------------------------------------

class TestNullEdgeGate:
    """A cell's edge_over_null_bps (gross edge minus the same de-overlapped,
    hold-matched, long-only population with the signal deleted) must clear
    min_edge_hurdle_bps, or it cannot reach PASS regardless of every other
    gate.

    Regression for a real defect: a PTRO daily_momentum cell passed the cost
    gate, BH-FDR, impact-priced, and OOS persistence gates while its return
    was ~62% beta (PTRO ran 182 -> 5,400 inside the evaluation window, so any
    long-biased rule looked brilliant). Measured against an always-long null,
    the signal itself contributed +45.1 bps against a 110.2 bps hurdle — it
    lost to buy-and-hold. A 47-name breadth test found the family's true mean
    edge was +0.1 bps (t=0.01).
    """

    def test_ptro_regression_loses_to_null_blocks_pass(self):
        """The exact PTRO numbers. Every other gate in the system passes
        this cell — it is the whole point of the regression."""
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            family_name="daily_momentum",
            horizon_trading_days=2,
            gross_edge_bps=119.4,
            null_edge_bps=74.3,
            edge_over_null_bps=45.1,
            min_edge_hurdle_bps=110.2,
            net_edge_bps=9.2,
            passes_cost_gate=True,
            fdr_accepted=True,
            impact_priced=True,
            oos_eff_n=53,
            oos_persistent=True,
        )
        assert c.passes_cost_gate is True
        assert c.fdr_accepted is True
        assert (c.net_edge_bps or 0) > 0
        assert c.impact_priced is True
        assert _clears_oos_power_gate(c) is True
        assert c.oos_persistent is not False
        assert _is_pass_eligible(c) is False, (
            "PTRO clears every other gate — the null gate alone must exclude it"
        )

    def test_signal_that_clears_null_and_hurdle_is_pass_eligible(self):
        """edge_over_null_bps > hurdle: the signal itself pays for the
        friction — PASS-eligible."""
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            family_name="daily_momentum",
            horizon_trading_days=2,
            gross_edge_bps=250.0,
            null_edge_bps=50.0,
            edge_over_null_bps=200.0,
            min_edge_hurdle_bps=110.2,
            net_edge_bps=139.8,
            oos_eff_n=53,
            oos_persistent=True,
        )
        assert _is_pass_eligible(c) is True

    def test_edge_over_null_none_on_applicable_shape_blocks(self):
        """None on a shape the gate applies to means 'never measured', not
        'not applicable' — it must BLOCK, never silently pass. Inverse of
        _clears_oos_power_gate's None-means-not-applicable case."""
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=2,
            null_edge_bps=None,
            edge_over_null_bps=None,
            oos_eff_n=53,
            oos_persistent=True,
        )
        assert _clears_null_gate(c) is False
        assert _is_pass_eligible(c) is False

    def test_edge_over_null_none_on_cross_sectional_shape_is_exempt(self):
        """cross_sectional is structurally long/short (long_short_infeasible)
        — no long-only null is computable, so None here means 'not
        applicable', not 'unmeasured'."""
        c = _cell(
            shape=SHAPE_CROSS_SECTIONAL,
            horizon_trading_days=1,
            null_edge_bps=None,
            edge_over_null_bps=None,
            oos_eff_n=None,
            oos_persistent=None,
        )
        assert _clears_null_gate(c) is True
        assert _is_pass_eligible(c) is True

    def test_edge_over_null_none_on_pairs_shape_is_exempt(self):
        c = _cell(
            shape=SHAPE_PAIRS,
            horizon_trading_days=1,
            null_edge_bps=None,
            edge_over_null_bps=None,
            oos_eff_n=None,
            oos_persistent=None,
        )
        assert _clears_null_gate(c) is True
        assert _is_pass_eligible(c) is True

    def test_event_driven_shape_is_in_gate_scope_not_exempt(self):
        """event_driven DOES have a computable null (families_event.py pools
        forward_return over all events regardless of sign) — unlike
        cross_sectional/pairs it is not exempt, so an unmeasured None here
        must still block."""
        c = _cell(
            shape=SHAPE_EVENT_DRIVEN,
            symbol="(pooled)",
            horizon_trading_days=1,
            null_edge_bps=None,
            edge_over_null_bps=None,
            oos_eff_n=None,
            oos_persistent=None,
        )
        assert _clears_null_gate(c) is False
        assert _is_pass_eligible(c) is False

    def test_relaxed_setting_allows_edge_over_null_between_zero_and_hurdle(self, monkeypatch):
        """NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE=False relaxes the bar to
        edge_over_null_bps > 0 — the PTRO cell would pass under this
        (more permissive, non-default) setting."""
        monkeypatch.setattr(report_mod, "NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE", False)
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            family_name="daily_momentum",
            horizon_trading_days=2,
            gross_edge_bps=119.4,
            null_edge_bps=74.3,
            edge_over_null_bps=45.1,  # < hurdle=110.2, but > 0
            min_edge_hurdle_bps=110.2,
            net_edge_bps=9.2,
            oos_eff_n=53,
            oos_persistent=True,
        )
        assert _is_pass_eligible(c) is True

    def test_relaxed_setting_still_blocks_non_positive_edge_over_null(self, monkeypatch):
        """Even the relaxed setting requires edge_over_null_bps > 0 — a
        signal that is net NEGATIVE relative to the null is never PASS."""
        monkeypatch.setattr(report_mod, "NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE", False)
        c = _cell(
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=2,
            gross_edge_bps=95.0,
            null_edge_bps=100.0,
            edge_over_null_bps=-5.0,
            min_edge_hurdle_bps=40.0,
            net_edge_bps=55.0,
            oos_eff_n=53,
            oos_persistent=True,
        )
        assert _is_pass_eligible(c) is False

    def test_verdict_downgrades_to_inconclusive_when_only_null_losing_cells(self):
        """A grid whose only cost-gate passer loses to the null is
        INCONCLUSIVE, not PASS — the PTRO shape at the verdict level."""
        cells = [
            _cell(
                shape=SHAPE_SINGLE_NAME,
                symbol="PTRO",
                family_name="daily_momentum",
                horizon_trading_days=2,
                gross_edge_bps=119.4,
                null_edge_bps=74.3,
                edge_over_null_bps=45.1,
                min_edge_hurdle_bps=110.2,
                net_edge_bps=9.2,
                oos_eff_n=53,
                oos_persistent=True,
            ),
        ]
        assert compute_verdict(cells) == VERDICT_INCONCLUSIVE

    def test_null_gate_reason_appears_in_report_as_loses_to_null(self, tmp_path):
        """Excluded from PASS ≠ silently dropped — the report must show a
        reader-facing reason without requiring recomputation."""
        cells = [
            _cell(
                shape=SHAPE_SINGLE_NAME,
                symbol="PTRO",
                family_name="daily_momentum",
                horizon_trading_days=2,
                gross_edge_bps=119.4,
                null_edge_bps=74.3,
                edge_over_null_bps=45.1,
                min_edge_hurdle_bps=110.2,
                net_edge_bps=9.2,
                oos_eff_n=53,
                oos_persistent=True,
            ),
        ]
        path = emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = open(path).read()
        assert "INCONCLUSIVE" in content
        assert "loses_to_null" in content


class TestNullEdgeFieldsSurviveFdrReplace:
    """apply_fdr_to_grid rebuilds each CellResult via dataclasses.replace
    specifically so newly-added fields can't be silently dropped (see the
    comment on apply_fdr_to_grid in run_strategy_fit.py — a field-by-field
    rebuild caused exactly that bug before). null_edge_bps / edge_over_null_bps
    must survive that round-trip."""

    def test_null_fields_survive_apply_fdr_to_grid(self):
        cell = CellResult(
            symbol="PTRO",
            family_name="daily_momentum",
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=2,
            status="tested",
            passes_cost_gate=True,
            p_value=0.02,
            net_edge_bps=9.2,
            gross_edge_bps=119.4,
            null_edge_bps=74.3,
            edge_over_null_bps=45.1,
        )
        updated = apply_fdr_to_grid([cell])
        assert updated[0].null_edge_bps == 74.3
        assert updated[0].edge_over_null_bps == 45.1
        # fdr_accepted must actually have been (re)populated by replace(),
        # proving this cell really went through the rebuild path.
        assert updated[0].fdr_accepted is True

    def test_none_null_fields_also_survive_apply_fdr_to_grid(self):
        """The default (None, None) round-trips too — dataclasses.replace
        must not silently coerce a missing measurement into a wrong value."""
        cell = CellResult(
            symbol="XCS",
            family_name="cross_sectional_momentum",
            shape=SHAPE_CROSS_SECTIONAL,
            horizon_trading_days=1,
            status="tested",
            passes_cost_gate=True,
            p_value=0.02,
            net_edge_bps=9.2,
            gross_edge_bps=119.4,
        )
        updated = apply_fdr_to_grid([cell])
        assert updated[0].null_edge_bps is None
        assert updated[0].edge_over_null_bps is None
        assert updated[0].fdr_accepted is True


# ---------------------------------------------------------------------------
# Report header must describe the model that actually ran
# ---------------------------------------------------------------------------

class TestCapitalModelHeaderIsNotHardcoded:
    """
    The header was a fixed string naming ADR-0011's leveraged sleeve, printed
    regardless of the active model.  After ADR-0015 made ``OWNED_CASH`` the
    default, every report claimed a 10 bps/cal-day financing charge and a
    ≤2-day ceiling that the run had not applied — misdescribing its own cost
    model in the direction that makes hurdles look higher than they were.
    """

    @staticmethod
    def _header(model) -> str:
        from strategy_fit.report import render_capital_model_line
        return render_capital_model_line(model)

    def test_owned_cash_header_states_no_financing_and_no_ceiling(self) -> None:
        from strategy_fit.capital_model import OWNED_CASH
        line = self._header(OWNED_CASH)
        assert "owned_cash" in line
        assert "ADR-0015" in line
        assert "no financing" in line.lower()
        assert "no horizon ceiling" in line.lower()
        assert "ADR-0011" not in line

    def test_leveraged_sleeve_header_states_its_charge_and_ceiling(self) -> None:
        from strategy_fit.capital_model import LEVERAGED_SLEEVE
        line = self._header(LEVERAGED_SLEEVE)
        assert "leveraged_sleeve" in line
        assert "ADR-0011" in line
        assert "10 bps" in line
        assert "2 trading-day" in line

    def test_the_two_models_never_render_the_same_line(self) -> None:
        from strategy_fit.capital_model import LEVERAGED_SLEEVE, OWNED_CASH
        assert self._header(OWNED_CASH) != self._header(LEVERAGED_SLEEVE)


# ---------------------------------------------------------------------------
# Impact-methodology prose must describe the active sizing, not a hardcoded
# constant (ADR-0017; same class of defect as TestCapitalModelHeaderIsNotHardcoded
# above and the ADR-0011 capital-model line found in plan 18 §4.4)
# ---------------------------------------------------------------------------

class TestImpactMethodologyProseIsNotHardcoded:
    """
    strategy_fit/report.py:615 used to emit the literal string
    "where participation = POSITION_NOTIONAL_IDR(30M IDR) / ADV" — a hardcoded
    figure that stopped being true the moment ADR-0017 rebased the per-name
    notional to the measured 15M sleeve.  This is the regression test that
    would have caught it: it changes ACTIVE_SLEEVE_SIZING and asserts the
    report's prose follows, rather than asserting a fixed string.
    """

    def test_prose_states_the_active_notional_not_a_hardcoded_one(self, tmp_path) -> None:
        cells = [_cell()]
        path = emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = open(path).read()
        from strategy_fit.impact import POSITION_NOTIONAL_IDR
        assert f"{int(POSITION_NOTIONAL_IDR):,}" in content
        assert "30M IDR) / ADV" not in content, (
            "report must not emit the old hardcoded '30M IDR' participation line"
        )

    def test_prose_follows_active_sleeve_sizing_when_changed(self, tmp_path, monkeypatch) -> None:
        """Change ACTIVE_SLEEVE_SIZING (as ADR-0017's Open Questions anticipates
        happening once concurrent-name count is settled) and confirm the
        report's prose follows it, proving it is read live rather than
        memoized from import time."""
        import strategy_fit.report as report_mod
        from strategy_fit.sleeve_sizing import SLEEVE_N1

        monkeypatch.setattr(report_mod, "ACTIVE_SLEEVE_SIZING", SLEEVE_N1)
        monkeypatch.setattr(report_mod, "POSITION_NOTIONAL_IDR", SLEEVE_N1.per_name_notional_idr)

        cells = [_cell()]
        path = emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = open(path).read()
        assert f"{int(SLEEVE_N1.per_name_notional_idr):,}" in content
        assert SLEEVE_N1.name in content

    def test_prose_names_the_active_preset(self, tmp_path) -> None:
        from strategy_fit.sleeve_sizing import ACTIVE_SLEEVE_SIZING
        cells = [_cell()]
        path = emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = open(path).read()
        assert ACTIVE_SLEEVE_SIZING.name in content
