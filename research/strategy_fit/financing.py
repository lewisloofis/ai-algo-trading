"""
strategy_fit/financing.py — ADR-0011 financing cost gate for the AI leverage sleeve.

The AI sleeve is funded by a 300M IDR T+2 trading-limit facility (ADR-0011).
Financing runs at 10 bps/calendar-day on the borrowed amount.  The financing
cost is additive to the round-trip fee gate — it is NOT a second copy of it.

Settlement model
----------------------------------
* Settlement lag: SETTLEMENT_TRADING_DAYS = 1 trading day after exit
  (empirically derived to match plan-10 §3 table examples).
* Hold=0 (same-day round-trip): borrow completes within the trading session;
  no overnight carry → 0 calendar days outstanding.
* For hold > 0: when entry_date is provided, calendar_days_outstanding
  delegates to CALENDAR.settlement_calendar_days (holiday-aware). When only
  entry_dow is provided, the DOW-based _advance_cal_days helper is used
  (weekends only, no holidays).

Verified against plan-10 §3 table:
    hold=0, any dow  → 0 cal days → 0 bps          ✓
    Thu(3), hold=1   → 4 cal days → 40 bps          ✓
    Mon(0), hold=1   → 2 cal days → 20 bps (≈1–3)  ✓
    Fri(4), hold=1   → 4 cal days → 40 bps          ✓
"""

from __future__ import annotations

from datetime import date as _date

from lib.market_calendar import CALENDAR as _CALENDAR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FINANCING_BPS_PER_CAL_DAY: int = 10
"""Financing rate: 10 bps per calendar day the borrow is outstanding (ADR-0011)."""

SETTLEMENT_TRADING_DAYS: int = 1
"""
Number of trading days of settlement lag after exit.

Empirically: advancing (hold + 1) trading days from entry matches plan-10 §3
table examples (Thu entry, 1-day hold → Mon settle → 4 cal days).  This is
distinct from the full IDX T+2 business-day settlement and should be revisited
if the broker's exact settlement clock is confirmed.  Holidays are out of scope
for v1.
"""

# Day-of-week constants for documentation clarity.
MON: int = 0
TUE: int = 1
WED: int = 2
THU: int = 3
FRI: int = 4


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _advance_cal_days(start_dow: int, n_trading_days: int) -> int:
    """
    Count calendar days elapsed when advancing *n_trading_days* forward from
    *start_dow* (0=Mon … 4=Fri).

    Rule: each Mon–Thu advance costs 1 calendar day; advancing past Fri
    (Fri → Mon) costs 3 calendar days (Fri, Sat, Sun → Mon).

    This correctly accounts for weekends; public holidays are out of scope (v1).

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
    Calendar days the borrow is outstanding from entry through settlement.

    For hold=0 (same-day round-trip), the borrow completes within the trading
    session and no overnight carry is incurred → returns 0.

    For hold > 0, when *entry_date* is supplied the calculation delegates to
    ``CALENDAR.settlement_calendar_days`` (holiday-aware). When only
    *entry_dow* is provided, the DOW-based ``_advance_cal_days`` helper is
    used (weekends only).

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
    >>> calendar_days_outstanding(0, 0)   # Mon, same-day
    0
    >>> calendar_days_outstanding(3, 1)   # Thu entry, 1-day hold → 40 bps
    4
    >>> calendar_days_outstanding(0, 1)   # Mon entry, 1-day hold
    2
    """
    if hold_trading_days == 0:
        return 0
    if entry_date is not None:
        return _CALENDAR.settlement_calendar_days(
            entry_date, hold_trading_days + SETTLEMENT_TRADING_DAYS
        )
    return _advance_cal_days(entry_dow, hold_trading_days + SETTLEMENT_TRADING_DAYS)


def financing_bps(
    entry_dow: int,
    hold_trading_days: int,
    *,
    entry_date: _date | None = None,
) -> float:
    """
    Financing cost in basis points for a given entry day-of-week and hold.

    Formula: FINANCING_BPS_PER_CAL_DAY × calendar_days_outstanding(…)

    Parameters
    ----------
    entry_dow : int
        Day-of-week of trade entry (0=Mon … 4=Fri).
    hold_trading_days : int
        Number of trading days the position is held.
    entry_date : date | None
        Actual calendar date of entry. When provided, enables holiday-aware
        financing calculation via the market calendar.

    Returns
    -------
    float
        Financing cost in bps.
    """
    return float(
        FINANCING_BPS_PER_CAL_DAY
        * calendar_days_outstanding(entry_dow, hold_trading_days, entry_date=entry_date)
    )


def full_pass_hurdle_bps(
    min_edge_to_clear_bps: float,
    modelled_impact_bps: float,
    entry_dow: int,
    hold_trading_days: int,
) -> float:
    """
    Full cost hurdle a strategy's net edge must exceed to pass the gate.

    hurdle = min_edge_to_clear_bps + modelled_impact_bps + financing_bps(…)

    The 40 bps round-trip fee is already baked into ``min_edge_to_clear_bps``
    (plan-09 annotation: min_spread_bps + ROUND_TRIP_BPS).  Financing is the
    additional ADR-0011 term and must not be double-counted with the fee.

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

    Returns
    -------
    float
        Full cost hurdle in bps.
    """
    return min_edge_to_clear_bps + modelled_impact_bps + financing_bps(entry_dow, hold_trading_days)


def best_feasible_hurdle_bps(
    min_edge_to_clear_bps: float,
    modelled_impact_bps: float,
    hold_trading_days: int,
) -> tuple[float, int]:
    """
    Return the minimum cost hurdle achievable by choosing the cheapest entry day.

    The allocator CHOOSES when to enter — so deployability should be tested at
    the best (cheapest financing) feasible entry day, not the worst-case Thursday.
    For same-day (hold=0) financing is 0 regardless of entry day.

    Iterates over all weekdays (Mon=0 … Fri=4), computes ``full_pass_hurdle_bps``
    at each, and returns the minimum with the corresponding day-of-week.  Ties
    are broken by the lowest dow index (Mon wins over Tue when equal).

    Parameters
    ----------
    min_edge_to_clear_bps : float
        Structural friction floor (one-tick spread + ROUND_TRIP_BPS).
    modelled_impact_bps : float
        Estimated market-impact / slippage.
    hold_trading_days : int
        Position hold in trading days (0 = intraday/same-day).

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
    )
    return net_edge_bps > hurdle
