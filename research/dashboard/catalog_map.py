"""
dashboard/catalog_map.py — the grid-family-to-catalog join, made explicit.

The strategy-fit runner and the frozen catalog use two different vocabularies
for the same idea, and nothing joins them:

    runner  (CellResult.family_name)   catalog (catalog_meta.FamilyMeta.family_id)
    cross_sectional_momentum           cross_sectional
    pairs_cointegration                pairs_stat_arb
    gap_event                          event_driven_gap

Comparing them by string equality makes the coverage grid claim that seven of
the ten catalog families were never tested, when most of them were tested under
another name — a false gap on exactly the completeness contract the panel
exists to check (plan-08 §7.3, plan-16).

So the join is a table, and three things stay visible rather than being papered
over:

* A runner family with no catalog entry (``volume_breakout``,
  ``intraday_mean_reversion``, ``volume_spike_event``,
  ``foreign_flow_spike_event``) is reported as such. It was tested; the catalog
  simply does not list it.
* ``orb`` is deliberately NOT mapped to ``intraday_momentum``. They are
  different rules (plan-17): the runner's ORB family opens on the 30-minute
  range and the catalog's ``intraday_momentum`` is the morning-to-afternoon
  family. Mapping them would repeat, inside the research side, the same
  conflation ``dashboard/strategy_map.py`` refuses on the runtime side.
* A catalog family that genuinely has no runner family at all
  (``obi_ofi_tick`` — the order_book shape is deferred, plan-10 §7.9) stays in
  the untested list, which is where it belongs.

Unlike an unmapped runtime ``strategy_id``, an unmapped family name does not
fail the render: it mis-states coverage, which is a wrong claim, but it cannot
mis-attribute money. It is rendered as a loud banner instead.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from strategy_fit.catalog_meta import ALL_FAMILIES

FAMILY_TO_CATALOG: Mapping[str, str | None] = {
    # Same name on both sides.
    "daily_momentum": "daily_momentum",
    "mean_reversion": "mean_reversion",
    "intraday_momentum": "intraday_momentum",
    # Renamed between the runner and the catalog.
    "cross_sectional_momentum": "cross_sectional",
    "pairs_cointegration": "pairs_stat_arb",
    "gap_event": "event_driven_gap",
    # Tested by the runner, absent from the catalog. None means "no catalog
    # entry", which is a fact about the catalog, not about the cell.
    "volume_breakout": None,
    "volume_spike_event": None,
    "foreign_flow_spike_event": None,
    "intraday_mean_reversion": None,
    # Deliberately unmapped: see the module docstring. ORB is not the catalog's
    # intraday_momentum, and saying so here is the point.
    "orb": None,
}

NO_CATALOG_ENTRY = "not_in_catalog"
UNMAPPED = "unmapped"
"""
A runner family name this table has never heard of. Distinct from
``not_in_catalog``, which is a considered answer; this one means nobody has
looked yet.
"""


def catalog_id_for(family_name: str) -> str | None:
    """The catalog family_id for a runner family name, or None if it has none."""
    return FAMILY_TO_CATALOG.get(family_name)


def is_mapped(family_name: str) -> bool:
    """Whether this table has an answer — including a considered 'None'."""
    return family_name in FAMILY_TO_CATALOG


def catalog_status_for(family_name: str, catalog_status: Mapping[str, str]) -> str:
    """
    What to show in the coverage grid's catalog-status column.

    Three distinct answers, never collapsed into one: the catalog's own status,
    "no catalog entry", or "nobody has mapped this name".
    """
    if not is_mapped(family_name):
        return UNMAPPED
    catalog_id = catalog_id_for(family_name)
    if catalog_id is None:
        return NO_CATALOG_ENTRY
    return catalog_status.get(catalog_id, NO_CATALOG_ENTRY)


def unmapped_families(family_names: Sequence[str]) -> list[str]:
    """Runner family names this table has no entry for."""
    return sorted({f for f in family_names if not is_mapped(f)})


def untested_catalog_families(family_names: Sequence[str]) -> list[str]:
    """
    Catalog families with no runner family in this run, AFTER mapping.

    This is the completeness contract's real answer. Before the map it
    included every renamed family and was therefore wrong.
    """
    covered = {catalog_id_for(f) for f in family_names if is_mapped(f)}
    covered.discard(None)
    return sorted(f.family_id for f in ALL_FAMILIES if f.family_id not in covered)
