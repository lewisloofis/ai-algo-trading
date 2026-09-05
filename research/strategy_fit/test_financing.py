"""
Tests for strategy_fit.financing.

Settlement model corrected 2026-09-05: the charged window runs from the entry's
T+2 settlement to the exit's T+2 settlement, so the common shift cancels and the
cost depends on the *settlement* weekday, not the entry weekday.  See the
financing module docstring and ADR-0011 §"Financing".
"""
from datetime import date

import pytest

from lib.market_calendar import CALENDAR
from strategy_fit.financing import (
    FINANCING_BPS_PER_CAL_DAY,
    FRI,
    MON,
    SETTLEMENT_TRADING_DAYS,
    THU,
    TUE,
    WED,
    best_feasible_hurdle_bps,
    calendar_days_outstanding,
    financing_bps,
    full_pass_hurdle_bps,
    passes_gate,
)

# A Mon-Fri stretch with no IDX holidays, for date-based assertions.
_CLEAN_MONDAY = date(2026, 10, 5)


# ---------------------------------------------------------------------------
# The broker's own worked examples (source of truth for this model)
# ---------------------------------------------------------------------------

class TestBrokerWorkedExamples:
    """The two cases the broker described, asserted on real dates."""

    def test_thursday_buy_friday_sell_costs_one_day(self):
        """Buy Thu settles Mon; sell Fri settles Tue → 1 day late, 10 bps."""
        entry = _CLEAN_MONDAY.replace(day=8)          # Thursday
        assert entry.weekday() == THU
        assert CALENDAR.add_trading_days(entry, SETTLEMENT_TRADING_DAYS).weekday() == MON
        assert calendar_days_outstanding(THU, 1, entry_date=entry) == 1
        assert financing_bps(THU, 1, entry_date=entry) == pytest.approx(10.0)

    def test_wednesday_buy_thursday_sell_pays_the_weekend(self):
        """Buy Wed settles Fri; sell Thu settles Mon → Sat+Sun on top, 3 days."""
        entry = _CLEAN_MONDAY.replace(day=7)          # Wednesday
        assert entry.weekday() == WED
        assert CALENDAR.add_trading_days(entry, SETTLEMENT_TRADING_DAYS).weekday() == FRI
        assert calendar_days_outstanding(WED, 1, entry_date=entry) == 3
        assert financing_bps(WED, 1, entry_date=entry) == pytest.approx(30.0)

    def test_wednesday_is_the_only_expensive_entry_day_for_a_one_day_hold(self):
        """Every weekday but Wednesday costs exactly 1 calendar day at hold=1."""
        costs = {dow: calendar_days_outstanding(dow, 1) for dow in range(5)}
        assert costs == {MON: 1, TUE: 1, WED: 3, THU: 1, FRI: 1}


# ---------------------------------------------------------------------------
# Regression guard against the superseded model
# ---------------------------------------------------------------------------

class TestSupersededModelDoesNotReturn:
    """
    The old model anchored the window at the *entry* date and ran it for
    hold+1 trading days, which charged Thu/Fri 4 days and Wed only 2 — exactly
    inverted.  Pin the corrected answers so it cannot be re-derived.
    """

    @pytest.mark.parametrize(
        "entry_dow,hold,superseded,corrected",
        [
            (THU, 1, 4, 1),
            (FRI, 1, 4, 1),
            (MON, 1, 2, 1),
            (WED, 1, 2, 3),
            (THU, 2, 5, 2),
        ],
    )
    def test_corrected_not_superseded(self, entry_dow, hold, superseded, corrected):
        result = calendar_days_outstanding(entry_dow, hold)
        assert result == corrected
        assert result != superseded


# ---------------------------------------------------------------------------
# calendar_days_outstanding
# ---------------------------------------------------------------------------

def test_hold0_any_dow_is_zero():
    """Same-day round-trip: both legs settle on the same date → 0 cal days."""
    for dow in range(5):
        assert calendar_days_outstanding(dow, 0) == 0, f"Expected 0 for dow={dow}, hold=0"


@pytest.mark.parametrize(
    "entry_dow,hold,expected",
    [
        # hold=1 — Wed is the sole weekend-straddling entry
        (MON, 1, 1), (TUE, 1, 1), (WED, 1, 3), (THU, 1, 1), (FRI, 1, 1),
        # hold=2 — the expensive pair shifts to Tue/Wed
        (MON, 2, 2), (TUE, 2, 4), (WED, 2, 4), (THU, 2, 2), (FRI, 2, 2),
    ],
)
def test_calendar_days_by_dow_and_hold(entry_dow, hold, expected):
    assert calendar_days_outstanding(entry_dow, hold) == expected


def test_dow_path_matches_date_path_in_a_holiday_free_week():
    """The weekday-only path is the holiday-free specialisation of the date path."""
    from datetime import timedelta

    for offset in range(5):
        entry = _CLEAN_MONDAY + timedelta(days=offset)
        for hold in range(0, 4):
            assert calendar_days_outstanding(entry.weekday(), hold) == (
                calendar_days_outstanding(entry.weekday(), hold, entry_date=entry)
            ), f"mismatch for {entry} hold={hold}"


def test_negative_hold_rejected():
    with pytest.raises(ValueError):
        calendar_days_outstanding(MON, -1)


def test_out_of_range_dow_rejected():
    with pytest.raises(ValueError):
        calendar_days_outstanding(5, 1)


# ---------------------------------------------------------------------------
# Holiday awareness
# ---------------------------------------------------------------------------

class TestHolidayAwareness:
    """A holiday inside the charged window costs like a weekend day."""

    def test_holiday_inside_window_adds_a_day(self):
        """
        Tue 2026-08-25 is an IDX holiday.  Entry Thu 08-20 settles Mon 08-24;
        exit Fri 08-21 settles Wed 08-26 because Tue is skipped → 2 days, one
        more than the ordinary Thursday-entry cost of 1.
        """
        entry = date(2026, 8, 20)
        assert entry.weekday() == THU
        assert not CALENDAR.is_trading_day(date(2026, 8, 25))
        assert calendar_days_outstanding(THU, 1, entry_date=entry) == 2
        assert calendar_days_outstanding(THU, 1) == 1  # holiday-free baseline

    def test_holiday_shifting_both_settlements_costs_nothing_extra(self):
        """
        A holiday that moves both endpoints equally cancels, exactly as the T+2
        shift does.  Entry Mon 08-24 settles Thu 08-27; exit Wed 08-26 settles
        Fri 08-28 → still 1 day.
        """
        entry = date(2026, 8, 24)
        assert entry.weekday() == MON
        assert calendar_days_outstanding(MON, 1, entry_date=entry) == 1


# ---------------------------------------------------------------------------
# Constants and rate
# ---------------------------------------------------------------------------

def test_financing_bps_constant():
    """FINANCING_BPS_PER_CAL_DAY must be 10 (ADR-0011)."""
    assert FINANCING_BPS_PER_CAL_DAY == 10


def test_settlement_trading_days_is_t_plus_2():
    """Both legs settle T+2; the shift is applied to entry and exit alike."""
    assert SETTLEMENT_TRADING_DAYS == 2


def test_financing_bps_hold0():
    """hold=0: 0 bps regardless of dow."""
    for dow in range(5):
        assert financing_bps(dow, 0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# full_pass_hurdle_bps / passes_gate
# ---------------------------------------------------------------------------

def test_full_pass_hurdle_basic():
    """hurdle = min_edge + modelled_impact + financing.
    Wed entry, hold=1: financing=30 bps → hurdle = 10 + 5 + 30 = 45."""
    result = full_pass_hurdle_bps(
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=WED,
        hold_trading_days=1,
    )
    assert result == pytest.approx(45.0)


def test_full_pass_hurdle_hold0():
    """hold=0: financing=0 → hurdle = min_edge + impact."""
    result = full_pass_hurdle_bps(
        min_edge_to_clear_bps=20.0,
        modelled_impact_bps=0.0,
        entry_dow=MON,
        hold_trading_days=0,
    )
    assert result == pytest.approx(20.0)


def test_passes_gate_true():
    """Edge clearly exceeds hurdle → True."""
    assert passes_gate(
        net_edge_bps=100.0,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=MON,
        hold_trading_days=1,
    ) is True


def test_passes_gate_false():
    """Edge below hurdle → False."""
    assert passes_gate(
        net_edge_bps=5.0,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=WED,
        hold_trading_days=1,
    ) is False


def test_passes_gate_dow_changes_outcome():
    """
    Same edge, Mon (10 bps financing) vs Wed (30 bps) changes the outcome.
    edge=30, min=10, impact=5:
      Mon: hurdle = 10+5+10 = 25 → 30 > 25 → True
      Wed: hurdle = 10+5+30 = 45 → 30 < 45 → False
    """
    edge = 30.0
    passes_mon = passes_gate(
        net_edge_bps=edge,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=MON,
        hold_trading_days=1,
    )
    passes_wed = passes_gate(
        net_edge_bps=edge,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=WED,
        hold_trading_days=1,
    )
    assert passes_mon is True
    assert passes_wed is False


# ---------------------------------------------------------------------------
# best_feasible_hurdle_bps — cheapest-entry-day cost gate
# ---------------------------------------------------------------------------

class TestBestFeasibleHurdle:
    """Tests for best_feasible_hurdle_bps: cheapest-entry-day cost gate."""

    def test_returns_tuple_of_hurdle_and_dow(self):
        """Return type is (float, int)."""
        hurdle, dow = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=10.0,
            modelled_impact_bps=5.0,
            hold_trading_days=1,
        )
        assert isinstance(hurdle, float)
        assert isinstance(dow, int)
        assert 0 <= dow <= 4

    def test_same_day_hold_is_zero_financing_any_dow(self):
        """hold=0: financing=0 for every dow, hurdle = min_edge + impact."""
        hurdle, dow = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=20.0,
            modelled_impact_bps=5.0,
            hold_trading_days=0,
        )
        assert hurdle == pytest.approx(25.0)
        assert 0 <= dow <= 4

    def test_cheapest_entry_for_hold1_is_one_calendar_day(self):
        """Mon/Tue/Thu/Fri all cost 1 cal day at hold=1; Wed costs 3."""
        hurdle, dow = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=10.0,
            modelled_impact_bps=0.0,
            hold_trading_days=1,
        )
        assert hurdle == pytest.approx(20.0)   # 10 floor + 10 financing
        assert dow == MON                      # tie broken by lowest index

    def test_wednesday_is_the_day_best_feasible_avoids(self):
        """The chosen hurdle is strictly below the Wednesday hurdle at hold=1."""
        hurdle, best_dow = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=10.0,
            modelled_impact_bps=5.0,
            hold_trading_days=1,
        )
        wed_hurdle = full_pass_hurdle_bps(10.0, 5.0, entry_dow=WED, hold_trading_days=1)
        assert hurdle < wed_hurdle
        assert best_dow != WED

    def test_cell_that_clears_monday_but_not_wednesday_passes(self):
        """Regression: edge=30 passes the Mon hurdle=25 but not Wed's 45."""
        edge = 30.0
        hurdle, _ = best_feasible_hurdle_bps(10.0, 5.0, hold_trading_days=1)
        assert edge > hurdle, f"edge={edge} should exceed best-feasible hurdle={hurdle}"

    def test_best_entry_dow_is_recorded(self):
        """best_entry_dow is in 0..4 and is not None."""
        _, dow = best_feasible_hurdle_bps(10.0, 0.0, hold_trading_days=2)
        assert dow is not None
        assert 0 <= dow <= 4

    def test_all_days_tie_when_no_financing(self):
        """hold=0: all days have 0 financing; returned hurdle equals min_edge+impact."""
        hurdle, _ = best_feasible_hurdle_bps(50.0, 10.0, hold_trading_days=0)
        assert hurdle == pytest.approx(60.0)

    @pytest.mark.parametrize("hold,expected_financing", [(0, 0.0), (1, 10.0), (2, 20.0), (3, 30.0)])
    def test_best_feasible_financing_is_ten_bps_per_held_day(self, hold, expected_financing):
        """
        With the entry day chosen freely, the weekend is always avoidable: the
        best-feasible financing is exactly 10 bps per trading day held.
        """
        hurdle, _ = best_feasible_hurdle_bps(0.0, 0.0, hold_trading_days=hold)
        assert hurdle == pytest.approx(expected_financing)
