"""Tests for strategy_fit.financing — TDD RED phase (write first, verify fail, then implement)."""
import pytest
from strategy_fit.financing import (
    FINANCING_BPS_PER_CAL_DAY,
    SETTLEMENT_TRADING_DAYS,
    calendar_days_outstanding,
    financing_bps,
    full_pass_hurdle_bps,
    passes_gate,
    best_feasible_hurdle_bps,
)


def test_hold0_any_dow_is_zero():
    """Same-day round-trip: no overnight borrow → 0 cal days for any entry dow."""
    for dow in range(5):
        assert calendar_days_outstanding(dow, 0) == 0, f"Expected 0 for dow={dow}, hold=0"


def test_thursday_1day_hold():
    """Thu(3) entry, hold=1 → 4 cal days → 40 bps (plan-10 §3 table example)."""
    assert calendar_days_outstanding(3, 1) == 4


def test_monday_1day_hold():
    """Mon(0) entry, hold=1: Mon→Tue exit, Tue+1 settle=Wed → 2 cal days. In [1,3] range."""
    result = calendar_days_outstanding(0, 1)
    assert 1 <= result <= 3, f"Expected 1-3 cal days for Mon+1, got {result}"


def test_monday_1day_hold_exact():
    """Mon(0), hold=1: advance_cal(Mon, 1+1=2) = Mon→Tue(1) + Tue→Wed(1) = 2."""
    assert calendar_days_outstanding(0, 1) == 2


def test_friday_entry_1day_hold():
    """Fri(4), hold=1: advance_cal(Fri, 1+1=2):
       iter1: Fri→Mon = 3 cal; iter2: Mon→Tue = 1 cal → 4 cal days total."""
    assert calendar_days_outstanding(4, 1) == 4


def test_wednesday_1day_hold():
    """Wed(2), hold=1: advance_cal(Wed, 2):
       iter1: Wed→Thu = 1; iter2: Thu→Fri = 1 → 2 cal days."""
    assert calendar_days_outstanding(2, 1) == 2


def test_thursday_2day_hold():
    """Thu(3), hold=2: advance_cal(Thu, 2+1=3):
       iter1: Thu→Fri=1; iter2: Fri→Mon=3; iter3: Mon→Tue=1 → 5 cal days."""
    assert calendar_days_outstanding(3, 2) == 5


def test_financing_bps_constant():
    """FINANCING_BPS_PER_CAL_DAY must be 10."""
    assert FINANCING_BPS_PER_CAL_DAY == 10


def test_settlement_trading_days_constant():
    """SETTLEMENT_TRADING_DAYS must be 1."""
    assert SETTLEMENT_TRADING_DAYS == 1


def test_financing_bps_thursday():
    """Thu, hold=1: 4 cal days * 10 bps/day = 40 bps."""
    assert financing_bps(3, 1) == pytest.approx(40.0)


def test_financing_bps_hold0():
    """hold=0: 0 bps regardless of dow."""
    for dow in range(5):
        assert financing_bps(dow, 0) == pytest.approx(0.0)


def test_full_pass_hurdle_basic():
    """hurdle = min_edge + modelled_impact + financing.
    Thu entry, hold=1: financing=40 bps → hurdle = 10 + 5 + 40 = 55."""
    result = full_pass_hurdle_bps(
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=3,
        hold_trading_days=1,
    )
    assert result == pytest.approx(55.0)


def test_full_pass_hurdle_hold0():
    """hold=0: financing=0 → hurdle = min_edge + impact."""
    result = full_pass_hurdle_bps(
        min_edge_to_clear_bps=20.0,
        modelled_impact_bps=0.0,
        entry_dow=0,
        hold_trading_days=0,
    )
    assert result == pytest.approx(20.0)


def test_passes_gate_true():
    """Edge clearly exceeds hurdle → True."""
    assert passes_gate(
        net_edge_bps=100.0,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=0,
        hold_trading_days=1,
    ) is True


def test_passes_gate_false():
    """Edge below hurdle → False."""
    assert passes_gate(
        net_edge_bps=5.0,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=3,
        hold_trading_days=1,
    ) is False


def test_passes_gate_dow_changes_outcome():
    """Same edge, Thu (40 bps financing) vs Mon (20 bps financing) changes outcome.
    edge=40, min=10, impact=5:
      Mon: hurdle = 10+5+20 = 35 → 40 > 35 → True
      Thu: hurdle = 10+5+40 = 55 → 40 < 55 → False
    """
    edge = 40.0
    passes_mon = passes_gate(
        net_edge_bps=edge,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=0,
        hold_trading_days=1,
    )
    passes_thu = passes_gate(
        net_edge_bps=edge,
        min_edge_to_clear_bps=10.0,
        modelled_impact_bps=5.0,
        entry_dow=3,
        hold_trading_days=1,
    )
    assert passes_mon is True   # 40 > 35
    assert passes_thu is False  # 40 < 55


# ---------------------------------------------------------------------------
# best_feasible_hurdle_bps — Change 1 tests (TDD RED → GREEN)
# ---------------------------------------------------------------------------

class TestBestFeasibleHurdle:
    """Tests for best_feasible_hurdle_bps: cheapest-entry-day cost gate."""

    def test_returns_tuple_of_hurdle_and_dow(self):
        """Return type is (float, int)."""
        result = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=10.0,
            modelled_impact_bps=5.0,
            hold_trading_days=1,
        )
        hurdle, dow = result
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
        # All days tie at the same hurdle; best_entry_dow is any valid weekday.
        assert 0 <= dow <= 4

    def test_cheapest_entry_is_monday_or_wednesday_for_hold1(self):
        """For hold=1, Mon and Wed both give 2 cal days (cheapest).
        Best dow should be Mon=0 or Wed=2 (the tie-breaker is min index → Mon)."""
        hurdle, dow = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=10.0,
            modelled_impact_bps=0.0,
            hold_trading_days=1,
        )
        # Mon: 2 cal days → financing=20; hurdle=30
        # Tue: 2 cal days → financing=20; hurdle=30
        # Wed: 2 cal days → financing=20; hurdle=30
        # Thu: 4 cal days → financing=40; hurdle=50
        # Fri: 4 cal days → financing=40; hurdle=50
        assert hurdle == pytest.approx(30.0)   # cheapest hurdle
        assert dow in (0, 1, 2)                # Mon/Tue/Wed all tie

    def test_monday_cheaper_than_thursday_for_hold1(self):
        """With hold=1, Mon hurdle < Thu hurdle.
        edge=40, min=10, impact=5:
          Mon hurdle = 10+5+20=35; Thu hurdle = 10+5+40=55.
          best_feasible returns the Mon-level hurdle."""
        hurdle, best_dow = best_feasible_hurdle_bps(
            min_edge_to_clear_bps=10.0,
            modelled_impact_bps=5.0,
            hold_trading_days=1,
        )
        # Best hurdle must be well below the Thursday hurdle.
        thu_hurdle = full_pass_hurdle_bps(10.0, 5.0, entry_dow=3, hold_trading_days=1)
        assert hurdle < thu_hurdle

    def test_cell_that_clears_monday_but_not_thursday_now_passes(self):
        """Regression: edge=40 passes Mon hurdle=35 but not Thu hurdle=55.
        With best_feasible, this cell now passes the gate."""
        edge = 40.0
        min_edge = 10.0
        impact = 5.0
        hurdle, _ = best_feasible_hurdle_bps(min_edge, impact, hold_trading_days=1)
        assert edge > hurdle, (
            f"edge={edge} should exceed best-feasible hurdle={hurdle}"
        )

    def test_best_entry_dow_is_recorded(self):
        """best_entry_dow is in 0..4 and is not None."""
        _, dow = best_feasible_hurdle_bps(10.0, 0.0, hold_trading_days=2)
        assert dow is not None
        assert 0 <= dow <= 4

    def test_all_days_tie_when_no_financing(self):
        """hold=0: all days have 0 financing; returned hurdle equals min_edge+impact."""
        hurdle, _ = best_feasible_hurdle_bps(50.0, 10.0, hold_trading_days=0)
        assert hurdle == pytest.approx(60.0)
