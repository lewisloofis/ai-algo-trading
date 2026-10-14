"""
dashboard/strategy_map.py — the runtime-to-research join, made explicit.

Plan-19 §7's first trap, and the only one that can make the dashboard report
one strategy's realised cost against another's modelled edge:

    The Go strategy ``orb`` fires on any liveprice tick 0.5% above the day's
    first tick.  The Python family ``intraday_momentum`` fires on a 1-minute
    bar close above the 30-minute opening-range high.

Those are different rules with the same informal name (plan-17's warning box).
Joining ``attribution.strategy_id`` to ``CellResult.family_name`` by string
equality would silently compare them.  So the join is a table, every entry
states whether the two definitions actually match, and an id that is not in
the table raises instead of rendering.

Adding a strategy means adding a row here.  That is the intended friction: the
alternative is a panel that quietly invents a comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StrategyJoin:
    """How one runtime strategy id relates to the research catalog."""

    strategy_id: str
    """The id the Go runtime writes into decision/order_intent/attribution."""
    research_family: str | None
    """
    The ``CellResult.family_name`` whose modelled edge may be compared against
    this strategy's realised PnL, or None when no family tests the same rule.
    """
    definitions_match: bool
    """
    True only when the runtime rule and the research family's rule are the
    SAME rule.  False means the two may be shown side by side for orientation
    but their numbers must never be differenced, summed, or called a backtest
    comparison.
    """
    note: str
    """Why — in one sentence, for the banner the panel renders."""


# The join table. Keep it short and keep it honest.
STRATEGY_JOINS: Mapping[str, StrategyJoin] = {
    "orb": StrategyJoin(
        strategy_id="orb",
        research_family=None,
        definitions_match=False,
        note=(
            "The Go 'orb' strategy enters on any liveprice tick 0.5% above the day's "
            "first tick and exits at 15:49 WIB. The research family "
            "'intraday_momentum' enters on a 1-minute bar close above the 30-minute "
            "opening-range high. Same informal name, different rule (plan-17): no "
            "research family currently tests what this strategy does, so no modelled "
            "edge is shown against it."
        ),
    ),
    **{
        sid: StrategyJoin(
            strategy_id=sid,
            research_family=None,
            definitions_match=False,
            note=(
                "Plan-18 B4 live-order infrastructure test (runbook "
                "first-live-order.md): orders placed to prove the order path, "
                "not to earn. No research family; no modelled edge is shown."
            ),
        )
        for sid in (
            "manual-first-order-fill",
            "manual-first-order-shadow",
            "manual-limit-probe",
            "roundtrip-goto",
        )
    },
}

# Reserved ids that must never be reported as strategy performance.
EXCLUDED_STRATEGY_IDS: frozenset[str] = frozenset({"calibration"})
"""
ADR-0016 §6 reserves ``calibration`` for fill-calibration traffic: orders
placed to measure the market, not to make money.  Panel C drops these rows
itself rather than trusting the caller to have filtered them — a calibration
round-trip in a PnL total is a wrong number that looks like a right one.
"""


class UnmappedStrategyError(KeyError):
    """
    Raised when the store holds a strategy_id the join table does not know.

    Deliberately fatal to a render (plan-19 §8 "join safety"). A dashboard that
    skips the unknown row under-reports PnL silently; one that guesses a family
    compares two different strategies. Neither is better than stopping.
    """


def join_for(strategy_id: str) -> StrategyJoin:
    """The join row for *strategy_id*, or raise UnmappedStrategyError."""
    try:
        return STRATEGY_JOINS[strategy_id]
    except KeyError:
        raise UnmappedStrategyError(
            f"strategy_id {strategy_id!r} has no entry in dashboard/strategy_map.py. "
            "Add one — stating whether its rule matches a research family's rule — "
            "rather than letting the panel guess. Known ids: "
            + ", ".join(sorted(STRATEGY_JOINS))
        ) from None


def require_all_mapped(strategy_ids: "list[str] | set[str]") -> None:
    """Raise unless every id (excluding the reserved ones) is in the table."""
    unknown = sorted(
        s for s in set(strategy_ids)
        if s not in STRATEGY_JOINS and s not in EXCLUDED_STRATEGY_IDS
    )
    if unknown:
        raise UnmappedStrategyError(
            "unmapped strategy_id(s) in the post-trade store: "
            + ", ".join(unknown)
            + " — add them to dashboard/strategy_map.py. Known ids: "
            + ", ".join(sorted(STRATEGY_JOINS))
        )


def is_excluded(strategy_id: str) -> bool:
    """Whether this id is reserved and must not be reported as performance."""
    return strategy_id in EXCLUDED_STRATEGY_IDS
