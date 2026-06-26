"""
IDX holiday-aware trading calendar.

Shared across research (Python) and referenced by collector (Go embed copy).
Single source of truth: calendar/idx_holidays.json at the repo root.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import FrozenSet

_DATA_PATH = Path(__file__).parent.parent.parent / "calendar" / "idx_holidays.json"


class MarketCalendar:
    def __init__(self, holidays: FrozenSet[date], provisional: bool = False) -> None:
        self._holidays = holidays
        self.provisional = provisional

    @classmethod
    def load(cls, path: Path = _DATA_PATH) -> "MarketCalendar":
        with path.open() as f:
            raw = json.load(f)
        holidays: FrozenSet[date] = frozenset(
            date.fromisoformat(h["date"]) for h in raw["holidays"]
        )
        return cls(holidays=holidays, provisional=raw.get("provisional", True))

    def is_trading_day(self, d: date) -> bool:
        """True if d is Mon-Fri and not in the holiday set."""
        return d.weekday() < 5 and d not in self._holidays

    def next_trading_day(self, d: date) -> date:
        """Nearest trading day strictly after d."""
        candidate = d + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def prev_trading_day(self, d: date) -> date:
        """Nearest trading day strictly before d."""
        candidate = d - timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def add_trading_days(self, d: date, n: int) -> date:
        """Roll n trading days forward (n<0 backward)."""
        if n == 0:
            return d
        step = timedelta(days=1) if n > 0 else timedelta(days=-1)
        remaining = abs(n)
        current = d
        while remaining > 0:
            current += step
            if self.is_trading_day(current):
                remaining -= 1
        return current

    def trading_days_between(self, a: date, b: date) -> int:
        """Count of trading days in (a, b] — exclusive a, inclusive b."""
        if a >= b:
            return 0
        count = 0
        current = a + timedelta(days=1)
        while current <= b:
            if self.is_trading_day(current):
                count += 1
            current += timedelta(days=1)
        return count

    def settlement_calendar_days(self, entry: date, t_plus: int) -> int:
        """
        Calendar days from entry until the T+t_plus trading-day settlement date.

        settlement_date = add_trading_days(entry, t_plus)
        return (settlement_date - entry).days

        Holiday-aware financing clock (plan-14 §4 keystone).
        """
        settlement = self.add_trading_days(entry, t_plus)
        return (settlement - entry).days


# Module-level singleton loaded from the shared JSON.
CALENDAR: MarketCalendar = MarketCalendar.load()
