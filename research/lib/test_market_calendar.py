"""pytest suite for market_calendar.py."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from lib.market_calendar import CALENDAR, MarketCalendar

_DATA_PATH = Path(__file__).parent.parent.parent / "calendar" / "idx_holidays.json"
_VALID_KINDS = {"public_holiday", "collective_leave", "exchange_closure"}


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def _raw() -> dict:
    with _DATA_PATH.open() as f:
        return json.load(f)


def test_holiday_count() -> None:
    raw = _raw()
    assert len(raw["holidays"]) == 22


def test_no_weekend_holidays() -> None:
    raw = _raw()
    for h in raw["holidays"]:
        d = date.fromisoformat(h["date"])
        assert d.weekday() < 5, f"{h['date']} falls on a weekend"


def test_no_duplicate_dates() -> None:
    raw = _raw()
    dates = [h["date"] for h in raw["holidays"]]
    assert len(dates) == len(set(dates)), "Duplicate holiday dates found"


def test_valid_kinds() -> None:
    raw = _raw()
    for h in raw["holidays"]:
        assert h["kind"] in _VALID_KINDS, f"Unknown kind {h['kind']!r} on {h['date']}"


def test_not_provisional() -> None:
    raw = _raw()
    assert raw["provisional"] is False


# ---------------------------------------------------------------------------
# Trading-day count
# ---------------------------------------------------------------------------

def test_trading_day_count_2026() -> None:
    cal = CALENDAR
    d = date(2026, 1, 1)
    count = 0
    while d.year == 2026:
        if cal.is_trading_day(d):
            count += 1
        d += timedelta(days=1)
    assert count == 239


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("d,expected", [
    (date(2026, 1, 1), False),   # Thu, Tahun Baru
    (date(2026, 1, 2), True),    # Fri
    (date(2026, 1, 3), False),   # Sat
    (date(2026, 1, 4), False),   # Sun
    (date(2026, 1, 5), True),    # Mon
    (date(2026, 6, 1), False),   # Mon, Hari Lahir Pancasila
])
def test_is_trading_day(d: date, expected: bool) -> None:
    assert CALENDAR.is_trading_day(d) is expected


# ---------------------------------------------------------------------------
# next_trading_day
# ---------------------------------------------------------------------------

def test_next_trading_day_skips_new_year() -> None:
    # next_trading_day(2025-12-31) → 2026-01-02 (Jan 1 is a holiday)
    result = CALENDAR.next_trading_day(date(2025, 12, 31))
    assert result == date(2026, 1, 2)


def test_next_trading_day_nyepi_cluster() -> None:
    # next_trading_day(2026-03-19) → 2026-03-25
    # Mar 20 (Fri) = collective_leave → skip
    # Mar 21 (Sat) = weekend → skip
    # Mar 22 (Sun) = weekend → skip
    # Mar 23 (Mon) = collective_leave → skip
    # Mar 24 (Tue) = collective_leave → skip
    # Mar 25 (Wed) = trading day ✓
    result = CALENDAR.next_trading_day(date(2026, 3, 19))
    assert result == date(2026, 3, 25)


# ---------------------------------------------------------------------------
# add_trading_days
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start,n,expected", [
    (date(2026, 1, 8), 0, date(2026, 1, 8)),    # no change
    (date(2026, 1, 8), 1, date(2026, 1, 9)),    # Thu → Fri
    (date(2026, 1, 8), 2, date(2026, 1, 12)),   # Thu → Fri → Mon (4 cal days)
    (date(2026, 1, 8), -1, date(2026, 1, 7)),   # Thu → Wed
])
def test_add_trading_days(start: date, n: int, expected: date) -> None:
    assert CALENDAR.add_trading_days(start, n) == expected


# ---------------------------------------------------------------------------
# trading_days_between
# ---------------------------------------------------------------------------

def test_trading_days_between_same_date() -> None:
    assert CALENDAR.trading_days_between(date(2026, 1, 1), date(2026, 1, 1)) == 0


def test_trading_days_between_across_new_year() -> None:
    # (Jan 1, Jan 5]: Jan 2 (Fri) + Jan 5 (Mon) = 2
    # Jan 1 excluded, Jan 3-4 weekend
    result = CALENDAR.trading_days_between(date(2026, 1, 1), date(2026, 1, 5))
    assert result == 2


# ---------------------------------------------------------------------------
# settlement_calendar_days
# ---------------------------------------------------------------------------

def test_settlement_calendar_days_normal_weekend() -> None:
    # Entry = 2026-01-08 (Thu), t_plus = 2
    # T+1 = Jan 9 (Fri), T+2 = Jan 12 (Mon)
    # (Jan 12 - Jan 8).days = 4
    result = CALENDAR.settlement_calendar_days(date(2026, 1, 8), 2)
    assert result == 4


def test_settlement_calendar_days_holiday() -> None:
    # Entry = 2026-05-29 (Fri), t_plus = 2
    # May 30 (Sat) → skip, May 31 (Sun) → skip, Jun 1 (Mon, Pancasila) → skip
    # Jun 2 (Tue) → T+1, Jun 3 (Wed) → T+2
    # (Jun 3 - May 29).days = 5
    result = CALENDAR.settlement_calendar_days(date(2026, 5, 29), 2)
    assert result == 5


def test_settlement_calendar_days_zero_hold() -> None:
    # settlement_calendar_days(any_date, 0) → 0
    result = CALENDAR.settlement_calendar_days(date(2026, 3, 15), 0)
    assert result == 0
