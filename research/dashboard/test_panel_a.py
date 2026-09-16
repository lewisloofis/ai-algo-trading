"""
Panel A tests — the alpha-mining funnel, coverage grid and run diff.

The load-bearing one is funnel conservation (plan-19 §8): stage counts must sum
to the catalog total, so a cell that reaches no terminal state is surfaced as a
bug rather than dropped from the denominator.
"""
from __future__ import annotations

import pytest

from strategy_fit.harness import SHAPE_SINGLE_NAME

from dashboard.catalog_map import FAMILY_TO_CATALOG, NO_CATALOG_ENTRY, UNMAPPED

from dashboard.conftest import make_blocked_cell, make_tested_cell
from dashboard.panel_a import (
    FUNNEL_STAGES,
    STAGE_COST_GATED,
    STAGE_DATA_BLOCKED,
    STAGE_DECAYED,
    STAGE_FDR_REJECTED,
    STAGE_INSUFFICIENT_N,
    STAGE_LOSES_TO_NULL,
    STAGE_PASS,
    STAGE_SKIPPED_HORIZON,
    STAGE_UNCLASSIFIED,
    build_coverage,
    build_diff,
    build_funnel,
    stage_of,
)


class TestStageAssignment:
    @pytest.mark.parametrize(
        "status, expected",
        [
            ("data_blocked", STAGE_DATA_BLOCKED),
            ("skipped_horizon", STAGE_SKIPPED_HORIZON),
            ("insufficient_n", STAGE_INSUFFICIENT_N),
            ("cost_gated", STAGE_COST_GATED),
            ("decayed", STAGE_DECAYED),
        ],
    )
    def test_non_tested_status_is_carried_through_verbatim(self, status, expected):
        assert stage_of(make_blocked_cell(status=status)) == expected

    def test_a_clean_cell_passes(self):
        assert stage_of(make_tested_cell()) == STAGE_PASS

    def test_failing_the_cost_gate(self):
        assert stage_of(make_tested_cell(passes_cost_gate=False)) == STAGE_COST_GATED

    def test_failing_fdr(self):
        assert stage_of(make_tested_cell(fdr_accepted=False)) == STAGE_FDR_REJECTED

    def test_losing_to_the_always_long_null(self):
        """PTRO's shape: strong gross edge, but the signal loses to buy-and-hold."""
        cell = make_tested_cell(
            symbol="PTRO",
            gross_edge_bps=155.3,
            null_edge_bps=110.2,
            edge_over_null_bps=45.1,
            min_edge_hurdle_bps=110.2,
        )
        assert stage_of(cell) == STAGE_LOSES_TO_NULL

    def test_unpriced_impact_does_not_reach_pass(self):
        assert stage_of(make_tested_cell(impact_priced=False)) != STAGE_PASS

    def test_an_unknown_status_is_unclassified_not_dropped(self):
        assert stage_of(make_blocked_cell(status="something_new")) == STAGE_UNCLASSIFIED


class TestFunnelConservation:
    def test_stage_counts_sum_to_the_total(self):
        cells = [
            make_tested_cell(),
            make_tested_cell(symbol="PTRO", passes_cost_gate=False),
            make_tested_cell(symbol="TINS", fdr_accepted=False),
            make_blocked_cell(),
            make_blocked_cell(symbol="BNBR", status="insufficient_n"),
        ]
        funnel = build_funnel(cells)
        assert funnel.total == 5
        assert sum(r.count for r in funnel.rows) == 5
        assert funnel.conservation_error == 0

    def test_an_unclassified_cell_is_counted_not_dropped(self):
        funnel = build_funnel([make_blocked_cell(status="brand_new_status")])
        assert funnel.conservation_error == 0
        assert funnel.count(STAGE_UNCLASSIFIED) == 1

    def test_shares_sum_to_one(self):
        cells = [make_tested_cell(), make_blocked_cell()]
        funnel = build_funnel(cells)
        assert sum(r.share for r in funnel.rows) == pytest.approx(1.0)

    def test_empty_grid_does_not_divide_by_zero(self):
        funnel = build_funnel([])
        assert funnel.total == 0
        assert all(r.share == 0.0 for r in funnel.rows)

    def test_every_stage_is_representable(self):
        assert len(set(FUNNEL_STAGES)) == len(FUNNEL_STAGES)


class TestCoverage:
    def test_catalog_status_is_distinct_from_run_status(self):
        """
        The catalog's opinion of a family and what this run did with it are
        different questions and must not be collapsed into one column.
        """
        cov = build_coverage([make_blocked_cell()])
        cell = cov.cells[0]
        assert cell.catalog_status == "testable_now", "the catalog says this is testable"
        assert cell.dominant_stage == STAGE_DATA_BLOCKED, "this run could not test it"

    def test_families_absent_from_the_run_are_named(self):
        """Plan-08 §7.3's completeness contract, made checkable."""
        cov = build_coverage([make_tested_cell()])
        assert "daily_momentum" in cov.untested_families
        assert "intraday_momentum" not in cov.untested_families

    def test_a_full_runner_grid_leaves_only_the_known_gaps(self):
        """
        Everything the runner can emit still leaves the three data-blocked
        families and obi_ofi_tick (order_book is deferred, plan-10 §7.9). It is
        NOT the empty list, and claiming it were would be the false-completeness
        this panel exists to prevent.
        """
        cells = [make_tested_cell(family_name=name) for name in FAMILY_TO_CATALOG]
        assert build_coverage(cells).untested_families == [
            "multi_month_macro",
            "multi_week_trend",
            "multi_year_carry",
            "obi_ofi_tick",
        ]

    def test_a_family_the_map_has_never_heard_of_is_flagged(self):
        cov = build_coverage([make_tested_cell(family_name="experimental_thing")])
        assert cov.cells[0].catalog_status == UNMAPPED
        assert cov.unmapped_families == ["experimental_thing"]

    def test_a_family_deliberately_absent_from_the_catalog_is_labelled_so(self):
        """`volume_breakout` is tested but has no catalog entry — a considered
        answer, distinct from 'nobody has looked'."""
        cov = build_coverage([make_tested_cell(family_name="volume_breakout")])
        assert cov.cells[0].catalog_status == NO_CATALOG_ENTRY
        assert cov.unmapped_families == []


class TestRunDiff:
    def test_a_sign_flip_is_reported_with_its_magnitude(self):
        """The BNBR case: −113.1 → +119.5 bps, found late and by accident."""
        was = [make_tested_cell(symbol="BNBR", net_edge_bps=-113.1, passes_cost_gate=False)]
        now = [make_tested_cell(symbol="BNBR", net_edge_bps=119.5)]
        diffs = build_diff(was, now)
        assert len(diffs) == 1
        d = diffs[0]
        assert d.symbol == "BNBR"
        assert d.edge_delta_bps == pytest.approx(232.6)
        assert d.stage_changed

    def test_unchanged_cells_are_not_reported(self):
        cells = [make_tested_cell()]
        assert build_diff(cells, cells) == []

    def test_a_disappeared_cell_is_a_change(self):
        diffs = build_diff([make_tested_cell()], [])
        assert len(diffs) == 1
        assert diffs[0].now_stage is None

    def test_a_new_cell_is_a_change(self):
        diffs = build_diff([], [make_tested_cell()])
        assert len(diffs) == 1
        assert diffs[0].was_stage is None

    def test_sorted_by_magnitude_with_appearances_first(self):
        was = [
            make_tested_cell(symbol="AAA", net_edge_bps=10.0),
            make_tested_cell(symbol="BBB", net_edge_bps=10.0),
        ]
        now = [
            make_tested_cell(symbol="AAA", net_edge_bps=11.0),
            make_tested_cell(symbol="BBB", net_edge_bps=99.0),
            make_tested_cell(symbol="CCC", net_edge_bps=5.0),
        ]
        order = [d.symbol for d in build_diff(was, now)]
        assert order == ["CCC", "BBB", "AAA"]

    def test_a_stage_change_with_no_edge_change_still_reports(self):
        was = [make_tested_cell(net_edge_bps=40.0, fdr_accepted=True)]
        now = [make_tested_cell(net_edge_bps=40.0, fdr_accepted=False)]
        diffs = build_diff(was, now)
        assert len(diffs) == 1
        assert diffs[0].stage_changed
        assert diffs[0].edge_delta_bps == 0.0

    def test_cells_are_matched_on_symbol_family_and_hold(self):
        """A different hold is a different cell, not a change to the same one."""
        was = [make_tested_cell(horizon_trading_days=0, net_edge_bps=10.0)]
        now = [make_tested_cell(horizon_trading_days=1, net_edge_bps=10.0, shape=SHAPE_SINGLE_NAME)]
        diffs = build_diff(was, now)
        assert len(diffs) == 2
