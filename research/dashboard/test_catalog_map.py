"""
Tests for the grid-family-to-catalog join.

The load-bearing one is ``test_every_runner_family_is_mapped``: it is driven
off the runner's own family constants, so adding a family to
``run_strategy_fit.py`` without deciding what it corresponds to in the catalog
fails here rather than quietly mis-stating coverage on the page.
"""
from __future__ import annotations

import pytest

from strategy_fit.catalog_meta import ALL_FAMILIES
from strategy_fit.run_strategy_fit import (
    EVENT_FAMILY_NAMES,
    INTRADAY_FAMILIES,
    SINGLE_NAME_SIGNAL_FNS,
)

from dashboard.catalog_map import (
    FAMILY_TO_CATALOG,
    NO_CATALOG_ENTRY,
    UNMAPPED,
    catalog_id_for,
    catalog_status_for,
    is_mapped,
    unmapped_families,
    untested_catalog_families,
)


def runner_family_names() -> set[str]:
    """Every family name run_strategy_fit can put in a CellResult."""
    return (
        set(SINGLE_NAME_SIGNAL_FNS)
        | set(EVENT_FAMILY_NAMES)
        | set(INTRADAY_FAMILIES)
        | {"cross_sectional_momentum", "pairs_cointegration"}
    )


def test_every_runner_family_is_mapped():
    missing = sorted(runner_family_names() - set(FAMILY_TO_CATALOG))
    assert not missing, (
        f"runner family name(s) with no catalog_map entry: {missing}. "
        "Add a row saying which catalog family each corresponds to, or None."
    )


def test_every_mapped_target_exists_in_the_catalog():
    catalog_ids = {f.family_id for f in ALL_FAMILIES}
    bad = sorted(
        v for v in FAMILY_TO_CATALOG.values() if v is not None and v not in catalog_ids
    )
    assert not bad, f"catalog_map points at non-existent catalog family id(s): {bad}"


def test_the_map_has_no_entries_for_names_the_runner_cannot_emit():
    """A stale row is a claim about a family that no longer exists."""
    stale = sorted(set(FAMILY_TO_CATALOG) - runner_family_names())
    assert not stale, f"catalog_map has stale entries: {stale}"


@pytest.mark.parametrize(
    "runner, catalog",
    [
        ("cross_sectional_momentum", "cross_sectional"),
        ("pairs_cointegration", "pairs_stat_arb"),
        ("gap_event", "event_driven_gap"),
        ("daily_momentum", "daily_momentum"),
        ("intraday_momentum", "intraday_momentum"),
    ],
)
def test_renamed_families_join_correctly(runner, catalog):
    assert catalog_id_for(runner) == catalog


def test_orb_is_not_mapped_to_intraday_momentum():
    """
    Plan-17's conflation, refused on the research side too. The runner's ORB
    family opens on the 30-minute range; the catalog's intraday_momentum is
    the morning-to-afternoon family.
    """
    assert catalog_id_for("orb") is None
    assert is_mapped("orb"), "orb must be a considered None, not an oversight"


def test_a_family_absent_from_the_catalog_is_labelled_not_guessed():
    catalog = {f.family_id: f.catalog_status for f in ALL_FAMILIES}
    assert catalog_status_for("volume_breakout", catalog) == NO_CATALOG_ENTRY


def test_an_unknown_name_is_distinct_from_no_catalog_entry():
    """'Nobody has looked' and 'we looked, there is none' are different facts."""
    catalog = {f.family_id: f.catalog_status for f in ALL_FAMILIES}
    assert catalog_status_for("brand_new_family", catalog) == UNMAPPED
    assert unmapped_families(["brand_new_family", "orb"]) == ["brand_new_family"]


def test_catalog_status_passes_through_for_a_mapped_family():
    catalog = {f.family_id: f.catalog_status for f in ALL_FAMILIES}
    assert catalog_status_for("cross_sectional_momentum", catalog) == "testable_now"


class TestCompletenessContract:
    def test_a_renamed_family_is_not_reported_as_a_gap(self):
        """The bug this module exists to fix."""
        assert "cross_sectional" not in untested_catalog_families(["cross_sectional_momentum"])

    def test_a_genuinely_absent_family_is_reported(self):
        """obi_ofi_tick has no runner family: the order_book shape is deferred."""
        assert "obi_ofi_tick" in untested_catalog_families(list(runner_family_names()))

    def test_the_data_blocked_families_are_reported_as_absent(self):
        gaps = untested_catalog_families(list(runner_family_names()))
        for family in ("multi_week_trend", "multi_month_macro", "multi_year_carry"):
            assert family in gaps

    def test_a_full_run_leaves_only_the_known_gaps(self):
        assert untested_catalog_families(list(runner_family_names())) == [
            "multi_month_macro",
            "multi_week_trend",
            "multi_year_carry",
            "obi_ofi_tick",
        ]

    def test_an_empty_run_reports_every_catalog_family(self):
        assert len(untested_catalog_families([])) == len(ALL_FAMILIES)
