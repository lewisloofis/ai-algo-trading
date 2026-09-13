"""
strategy_fit/financing.py — ADR-0011 financing cost gate for the AI leverage sleeve.

The AI sleeve is funded by a 300M IDR trading-limit facility (ADR-0011).
Financing runs at 10 bps/calendar-day on the borrowed amount.  The financing
cost is additive to the round-trip fee gate — it is NOT a second copy of it.

Settlement model (corrected 2026-09-05)
---------------------------------------
The broker charges for the period its money is actually out, which is not the
period you hold the stock.  Both legs settle T+2 trading days:

* the **buy** settles T+2 — that is when the facility funds the purchase and
  the borrow begins;
* the **sell** settles T+2 — that is when the proceeds arrive and repay it.

The charged window is therefore ``[settle(entry), settle(exit)]``, and because
both endpoints carry the same T+2 shift it **cancels**.  What survives is not
the entry weekday but the weekday the *settlement* lands on::

    charged_calendar_days = settle(entry + hold) - settle(entry)

Consequences — the previous model got each of these wrong:

* A 1-trading-day hold normally costs **1** calendar day (10 bps), not 2-4.
* **Wednesday is the one expensive entry day** for a 1-day hold.  Wed settles
  Fri and Thu settles Mon, so the weekend falls *inside* the charged window:
  3 days (30 bps), the extra two being Sat and Sun.  Mon/Tue/Thu/Fri entries
  all cost 1 day.  The old model had this backwards — it charged Thu and Fri
  4 days each and Wed only 2.
* Same-day round-trips settle on the same date -> 0 days.  Unchanged, but it
  now falls out of the formula instead of needing a special case.
* Holidays lengthen the window exactly as weekends do; supplying *entry_date*
  picks them up for free via the market calendar.

Worked examples (the broker's own description of the fee):

    buy Thu -> settles Mon | sell Fri -> settles Tue  ->  1 day  (10 bps)
    buy Wed -> settles Fri | sell Thu -> settles Mon  ->  3 days (30 bps)
                                                          ^ Sat + Sun are the
                                                            two extra days

Net effect on the cost gate: the best-feasible hurdle falls by a flat 10 bps
for every hold >= 1 and is unchanged at hold = 0.

Note that there is no "two free days" grace here.  A sale cannot exploit the
T+2 window because the sale settles T+2 as well — the grace shifts rather than
accumulates.  The only way to hold the position and pay nothing is to repay
with owned cash by the buy's settlement date.
"""

from __future__ import annotations

from datetime import date as _date

from lib.market_calendar import CALENDAR as _CALENDAR
from strategy_fit.capital_model import ACTIVE_CAPITAL_MODEL, CapitalModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FINANCING_BPS_PER_CAL_DAY: int = 10
"""
Financing rate: 10 bps per calendar day the borrow is outstanding (ADR-0011).

.. deprecated:: ADR-0015 (2026-09-13)
    Kept for backward compatibility; equal to
    ``strategy_fit.capital_model.LEVERAGED_SLEEVE.financing_bps_per_cal_day``.
    Since leverage is now optional, the rate actually applied by
    :func:`financing_bps` and friends comes from the active (or explicitly
    passed) ``CapitalModel``, not from this constant — a model with
    ``funded_by_leverage=False`` (e.g. ``OWNED_CASH``) charges 0 regardless of
    this value.
"""

SETTLEMENT_TRADING_DAYS: int = 2
"""
IDX settlement lag in trading days, applied to *both* legs (T+2).

This shifts both endpoints of the charged window equally, so it never lengthens
the window.  It only decides which weekday the window *starts* on — which is
what makes a Wednesday entry expensive and every other weekday cheap.  See the
module docstring.
"""

# Day-of-week constants for documentation clarity.
MON: int = 0
TUE: int = 1
WED: int = 2
THU: int = 3
FRI: int = 4

_TRADING_DAYS_PER_WEEK: int = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _advance_cal_days(start_dow: int, n_trading_days: int) -> int:
    """
    Count calendar days elapsed when advancing *n_trading_days* forward from
    *start_dow* (0=Mon … 4=Fri).

    Rule: each Mon–Thu advance costs 1 calendar day; advancing past Fri
    (Fri → Mon) costs 3 calendar days (Fri, Sat, Sun → Mon).

    This correctly accounts for weekends; public holidays are out of scope
    (supply ``entry_date`` to ``calendar_days_outstanding`` for those).

    Parameters
    ----------
    start_dow : int
        Starting day-of-week (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri).
    n_trading_days : int
        Number of trading days to advance.  Must be ≥ 0.

    Returns
    -------
    int
        Total calendar days elapsed.
    """
    if n_trading_days < 0:
        raise ValueError(f"n_trading_days must be >= 0, got {n_trading_days}")
    if start_dow < 0 or start_dow > 4:
        raise ValueError(f"start_dow must be 0–4, got {start_dow}")

    cal: int = 0
    dow: int = start_dow
    for _ in range(n_trading_days):
        if dow == FRI:
            # Fri → Mon: skip Saturday and Sunday
            cal += 3
            dow = MON
        else:
            cal += 1
            dow += 1
    return cal


def _settlement_dow(entry_dow: int) -> int:
    """
    Weekday on which the T+2 settlement of a trade entered on *entry_dow* lands.

    Mon→Wed, Tue→Thu, Wed→Fri, Thu→Mon, Fri→Tue.  Holiday-free; the
    ``entry_date`` path in :func:`calendar_days_outstanding` handles holidays.
    """
    if entry_dow < 0 or entry_dow > 4:
        raise ValueError(f"entry_dow must be 0–4, got {entry_dow}")
    return (entry_dow + SETTLEMENT_TRADING_DAYS) % _TRADING_DAYS_PER_WEEK


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calendar_days_outstanding(
    entry_dow: int,
    hold_trading_days: int,
    *,
    entry_date: _date | None = None,
) -> int:
    """
    Calendar days the borrowed cash is outstanding, and therefore charged.

    The borrow runs from the entry's T+2 settlement (when the facility funds
    the buy) to the exit's T+2 settlement (when the sale proceeds repay it)::

        settle(entry + hold_trading_days) - settle(entry)

    For hold=0 both settlements fall on the same date, so the result is 0.

    When *entry_date* is supplied the market calendar resolves both settlement
    dates, so IDX holidays extend the window like weekends do.  With only
    *entry_dow*, the weekend-aware day-of-week path is used.

    Parameters
    ----------
    entry_dow : int
        Day-of-week of trade entry: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri.
    hold_trading_days : int
        Number of trading days the position is held (0 = intraday/same-day).
    entry_date : date | None
        Actual calendar date of entry. When provided, enables holiday-aware
        financing calculation via the market calendar.

    Returns
    -------
    int
        Calendar days outstanding (integer ≥ 0).

    Examples
    --------
    >>> calendar_days_outstanding(MON, 0)   # same-day round-trip
    0
    >>> calendar_days_outstanding(THU, 1)   # Thu buy settles Mon, Fri sell settles Tue
    1
    >>> calendar_days_outstanding(WED, 1)   # Wed settles Fri, Thu settles Mon: +Sat +Sun
    3
    """
    if hold_trading_days < 0:
        raise ValueError(f"hold_trading_days must be >= 0, got {hold_trading_days}")
    if entry_date is not None:
        entry_settlement = _CALENDAR.add_trading_days(entry_date, SETTLEMENT_TRADING_DAYS)
        exit_settlement = _CALENDAR.add_trading_days(
            entry_date, hold_trading_days + SETTLEMENT_TRADING_DAYS
        )
        return (exit_settlement - entry_settlement).days
    return _advance_cal_days(_settlement_dow(entry_dow), hold_trading_days)


def financing_bps(
    entry_dow: int,
    hold_trading_days: int,
    *,
    entry_date: _date | None = None,
    capital_model: CapitalModel | None = None,
) -> float:
    """
    Financing cost in basis points for a given entry day-of-week and hold.

    Formula: capital_model.financing_bps_per_cal_day × calendar_days_outstanding(…)

    Returns exactly 0.0 when the capital model is not funded by leverage (or
    its rate is 0) — owned cash carries no financing charge (ADR-0015).

    Parameters
    ----------
    entry_dow : int
        Day-of-week of trade entry (0=Mon … 4=Fri).
    hold_trading_days : int
        Number of trading days the position is held.
    entry_date : date | None
        Actual calendar date of entry. When provided, enables holiday-aware
        financing calculation via the market calendar.
    capital_model : CapitalModel or None
        The capital model funding the position. Defaults to
        ``strategy_fit.capital_model.ACTIVE_CAPITAL_MODEL`` when not supplied.

    Returns
    -------
    float
        Financing cost in bps.
    """
    model = capital_model if capital_model is not None else ACTIVE_CAPITAL_MODEL
    if not model.funded_by_leverage or model.financing_bps_per_cal_day == 0:
        return 0.0
    return float(
        model.financing_bps_per_cal_day
        * calendar_days_outstanding(entry_dow, hold_trading_days, entry_date=entry_date)
    )


def full_pass_hurdle_bps(
    min_edge_to_clear_bps: float,
    modelled_impact_bps: float,
    entry_dow: int,
    hold_trading_days: int,
    *,
    capital_model: CapitalModel | None = None,
) -> float:
    """
    Full cost hurdle a strategy's net edge must exceed to pass the gate.

    hurdle = min_edge_to_clear_bps + modelled_impact_bps + financing_bps(…)

    The 40 bps round-trip fee is already baked into ``min_edge_to_clear_bps``
    (plan-09 annotation: min_spread_bps + ROUND_TRIP_BPS).  Financing is the
    additional ADR-0011 term and must not be double-counted with the fee.
    Under a capital model with no leverage (e.g. ``OWNED_CASH``, ADR-0015),
    financing contributes exactly 0.0 and the hurdle is min_edge + impact.

    Parameters
    ----------
    min_edge_to_clear_bps : float
        Structural friction floor for this name from plan-09 (one-tick spread
        + 40 bps round-trip fee).
    modelled_impact_bps : float
        Estimated market-impact / slippage for this strategy's position size.
    entry_dow : int
        Day-of-week of entry (0=Mon … 4=Fri).
    hold_trading_days : int
        Position hold in trading days.
    capital_model : CapitalModel or None
        The capital model funding the position. Defaults to
        ``strategy_fit.capital_model.ACTIVE_CAPITAL_MODEL`` when not supplied.

    Returns
    -------
    float
        Full cost hurdle in bps.
    """
    return min_edge_to_clear_bps + modelled_impact_bps + financing_bps(
        entry_dow, hold_trading_days, capital_model=capital_model
    )


def best_feasible_hurdle_bps(
    min_edge_to_clear_bps: float,
    modelled_impact_bps: float,
    hold_trading_days: int,
    *,
    capital_model: CapitalModel | None = None,
) -> tuple[float, int]:
    """
    Return the minimum cost hurdle achievable by choosing the cheapest entry day.

    The allocator CHOOSES when to enter — so deployability should be tested at
    the best (cheapest financing) feasible entry day, not the worst case.  Under
    the corrected settlement model the worst case is a **Wednesday** entry on a
    1-day hold (its charged window straddles the weekend); for a 1-day hold the
    other four weekdays all tie at the cheapest 1 calendar day.
    For same-day (hold=0) financing is 0 regardless of entry day.

    Iterates over all weekdays (Mon=0 … Fri=4), computes ``full_pass_hurdle_bps``
    at each, and returns the minimum with the corresponding day-of-week.  Ties
    are broken by the lowest dow index (Mon wins over Tue when equal).

    Holidays are not modelled here — the sweep is over weekdays, not dates, so
    it reports the hurdle in an ordinary holiday-free week.  A cell entered the
    week of an IDX holiday pays more; use ``financing_bps(..., entry_date=...)``
    when a concrete date is known.

    Parameters
    ----------
    min_edge_to_clear_bps : float
        Structural friction floor (one-tick spread + ROUND_TRIP_BPS).
    modelled_impact_bps : float
        Estimated market-impact / slippage.
    hold_trading_days : int
        Position hold in trading days (0 = intraday/same-day).
    capital_model : CapitalModel or None
        The capital model funding the position. Defaults to
        ``strategy_fit.capital_model.ACTIVE_CAPITAL_MODEL`` when not supplied.
        Under a model with no leverage, financing is 0 at every entry day, so
        all five weekdays tie and ``best_dow`` is Monday (index 0).

    Returns
    -------
    tuple[float, int]
        ``(min_hurdle_bps, best_entry_dow)`` where ``best_entry_dow`` is 0–4.
    """
    best_hurdle: float = float("inf")
    best_dow: int = 0
    for dow in range(5):
        hurdle = full_pass_hurdle_bps(
            min_edge_to_clear_bps=min_edge_to_clear_bps,
            modelled_impact_bps=modelled_impact_bps,
            entry_dow=dow,
            hold_trading_days=hold_trading_days,
            capital_model=capital_model,
        )
        if hurdle < best_hurdle:
            best_hurdle = hurdle
            best_dow = dow
    return best_hurdle, best_dow


def passes_gate(
    net_edge_bps: float,
    min_edge_to_clear_bps: float,
    modelled_impact_bps: float,
    entry_dow: int,
    hold_trading_days: int,
    *,
    capital_model: CapitalModel | None = None,
) -> bool:
    """
    Return True if ``net_edge_bps`` strictly exceeds the full pass hurdle.

    The pass test is: net_edge_per_trade > 0 after the full cost stack
    (plan-10 §2 locked decision).

    Parameters
    ----------
    net_edge_bps : float
        Estimated gross edge per trade in bps (e.g. mean forward return × 1e4).
    min_edge_to_clear_bps : float
        Structural friction floor (one-tick spread + ROUND_TRIP_BPS).
    modelled_impact_bps : float
        Estimated impact / slippage.
    entry_dow : int
        Day-of-week of entry.
    hold_trading_days : int
        Position hold in trading days.
    capital_model : CapitalModel or None
        The capital model funding the position. Defaults to
        ``strategy_fit.capital_model.ACTIVE_CAPITAL_MODEL`` when not supplied.

    Returns
    -------
    bool
        True iff net_edge_bps > full_pass_hurdle_bps(…).
    """
    hurdle = full_pass_hurdle_bps(
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        modelled_impact_bps=modelled_impact_bps,
        entry_dow=entry_dow,
        hold_trading_days=hold_trading_days,
        capital_model=capital_model,
    )
    return net_edge_bps > hurdle
