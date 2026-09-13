"""
strategy_fit/capital_model.py — Capital model configuration (ADR-0015).

Leverage is now OPTIONAL, not assumed.  Two properties that used to be
hard-wired module constants — the ~2-trading-day horizon ceiling
(``strategy_fit.harness.MAX_HOLD_TRADING_DAYS``) and the financing term
(``strategy_fit.financing.financing_bps``) — are both consequences of
*funding a strategy with the 300M IDR T+2 leverage facility* (ADR-0011,
amended by ADR-0014).  Neither applies to a strategy funded with owned cash.

This module makes that choice explicit and swappable via a frozen
``CapitalModel`` descriptor, rather than two independent constants that only
made sense together under one assumption.

.. important::
    **The default changed on 2026-09-13** (ADR-0015).  Before this date, the
    only capital model implemented was the leveraged sleeve, and
    ``MAX_HOLD_TRADING_DAYS=2`` / ``FINANCING_BPS_PER_CAL_DAY=10`` applied
    unconditionally.  ``ACTIVE_CAPITAL_MODEL`` now defaults to
    ``OWNED_CASH`` — no financing, no horizon ceiling.  Results produced
    under ``OWNED_CASH`` and ``LEVERAGED_SLEEVE`` are **not comparable**:
    a cell that passes under one model may be rejected or priced
    differently under the other.  Always check which model a given
    strategy_portfolio.md run used before comparing across runs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapitalModel:
    """
    Descriptor for how a strategy sleeve is funded, and what that funding
    implies for the strategy-fit harness and cost gate.

    Attributes
    ----------
    name : str
        Identifier used in skip reasons and reports (e.g. "leveraged_sleeve").
    funded_by_leverage : bool
        True if capital is drawn from the 300M IDR T+2 trading-limit facility
        (ADR-0011).  False for owned cash, which carries no tenor clock and no
        financing charge.
    max_hold_trading_days : int | None
        Capital horizon ceiling in trading days.  None means no ceiling — the
        harness never rejects a family on horizon grounds under this model.
    financing_bps_per_cal_day : int
        Financing rate in bps per calendar day the position is outstanding.
        0 for models with no financing charge (e.g. owned cash).
    """

    name: str
    funded_by_leverage: bool
    max_hold_trading_days: int | None
    financing_bps_per_cal_day: int


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

LEVERAGED_SLEEVE = CapitalModel(
    name="leveraged_sleeve",
    funded_by_leverage=True,
    max_hold_trading_days=2,
    financing_bps_per_cal_day=10,
)
"""
The ADR-0011 model: 300M IDR T+2 trading-limit facility, ~2-trading-day
horizon ceiling (T+2 sell-safe / T+4 forced liquidation), 10 bps/calendar-day
financing (settlement arithmetic corrected by ADR-0014).
"""

OWNED_CASH = CapitalModel(
    name="owned_cash",
    funded_by_leverage=False,
    max_hold_trading_days=None,
    financing_bps_per_cal_day=0,
)
"""
Owned capital: no financing charge, no tenor clock, and therefore no horizon
ceiling.  A position can be held as long as the strategy's signal calls for.
"""


# ---------------------------------------------------------------------------
# Active model
# ---------------------------------------------------------------------------

ACTIVE_CAPITAL_MODEL: CapitalModel = OWNED_CASH
"""
The capital model used by default throughout strategy_fit when no explicit
``capital_model`` is passed.

CHANGED 2026-09-13 (ADR-0015): previously the sleeve was unconditionally
funded by ``LEVERAGED_SLEEVE`` (there was no other option).  Leverage is now
optional, and the default is ``OWNED_CASH``.  Callers that specifically want
the old, leverage-funded behaviour (financing charged, ~2-day horizon
ceiling) must pass ``capital_model=LEVERAGED_SLEEVE`` explicitly.
"""
