"""
recheck/test_stats.py — milestone gating and the frozen alert bar (plan 26 §2), on small synthetic records.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from recheck import stats as S


def _records(nets, cost=10.0, start="2026-11-02"):
    """One record per entry date; gross = net + cost, null = 0, so edge-after-cost == net."""
    dates = pd.bdate_range(start, periods=len(nets)).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "signal_date": dates, "entry_date": dates,
        "gross_bps": np.asarray(nets, dtype=float) + cost, "null_bps": 0.0, "cost_bps": cost,
    })


# -- milestone gating ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,done,expect", [
    (0, [], None), (29, [], None), (30, [], 30), (45, [], 30), (60, [], 60),
    (45, [30], None), (60, [30], 60), (200, [], 120), (200, [30, 60], 120), (200, [30, 60, 120], None),
    (65, [], 60),          # the highest reached is evaluated; the lower one is superseded, not queued
    (65, [60], None),
])
def test_milestone_to_evaluate(n, done, expect):
    assert S.milestone_to_evaluate(n, done) == expect


@pytest.mark.parametrize("done,expect", [([], 30), ([30], 60), ([60], 120), ([30, 60], 120), ([30, 60, 120], None)])
def test_next_milestone(done, expect):
    assert S.next_milestone(done) == expect


def test_a_superseded_milestone_is_never_queued_after_a_higher_one_was_taken():
    assert S.milestone_to_evaluate(100, [60]) is None
    assert S.milestone_to_evaluate(120, [60]) == 120


# -- alert bar ----------------------------------------------------------------------------------

def test_alert_threshold_for_eleven_families_is_point_ten_over_thirty_three():
    assert S.alert_p_threshold(11) == pytest.approx(0.10 / 33)
    assert S.alert_p_threshold(15) == pytest.approx(0.10 / 45)    # the plan's worked example: 0.0022


def _ev(edge=5.0, p=1e-4, drop5=2.0):
    return S.Evaluation(n=30, edge=edge, cost=10.0, p=p, drop5=drop5, t=4.0)


def test_alert_needs_all_three_conditions():
    thr = S.alert_p_threshold(11)
    assert S.is_alert(_ev(), thr) is True
    assert S.is_alert(_ev(edge=-0.1), thr) is False
    assert S.is_alert(_ev(edge=0.0), thr) is False
    assert S.is_alert(_ev(drop5=-0.1), thr) is False
    assert S.is_alert(_ev(drop5=float("nan")), thr) is False
    assert S.is_alert(_ev(p=float("nan")), thr) is False


def test_alert_p_bar_is_strict_and_uses_the_bonferroni_threshold():
    thr = S.alert_p_threshold(11)
    assert S.is_alert(_ev(p=thr * 0.999), thr) is True
    assert S.is_alert(_ev(p=thr), thr) is False
    assert S.is_alert(_ev(p=0.0022), thr) is True       # below 0.10/33 = 0.00303
    assert S.is_alert(_ev(p=0.004), thr) is False


# -- evaluation ---------------------------------------------------------------------------------

def test_evaluate_edge_is_mean_gross_minus_null_minus_cost_per_unit():
    rec = pd.DataFrame({
        "signal_date": ["2026-11-02"] * 2 + ["2026-11-03"], "entry_date": ["2026-11-03"] * 2 + ["2026-11-04"],
        "gross_bps": [100.0, 60.0, 20.0], "null_bps": [10.0, 10.0, 10.0], "cost_bps": [30.0, 30.0, 30.0],
    })
    ev = S.evaluate(rec)
    assert ev.n == 2                                    # distinct entry dates are the unit
    assert ev.edge == pytest.approx(((100 + 60) / 2 - 10 - 30 + (20 - 10 - 30)) / 2)
    assert ev.cost == pytest.approx(30.0)


def test_evaluate_p_is_one_sided_normal_sf_of_the_hac_t_of_the_unit_series():
    nets = [10.0, -4.0, 12.0, 3.0, 8.0, 1.0, 9.0, -2.0]
    ev = S.evaluate(_records(nets))
    t = S.hac_t(np.array(nets))
    assert ev.t == pytest.approx(t)
    assert ev.p == pytest.approx(0.5 * math.erfc(t / math.sqrt(2)))


def test_hac_t_matches_a_hand_computed_newey_west_lag_one():
    # x = 1..4: mean 2.5, deviations -1.5 -.5 .5 1.5; g0 = 1.25, g1 = 0.3125, omega = g0 + 2 * 0.5 * g1 = 1.5625
    # se = sqrt(omega / n) = 0.625, t = 2.5 / 0.625
    assert S.hac_t(np.array([1.0, 2.0, 3.0, 4.0])) == pytest.approx(4.0)
    assert S.hac_t(np.array([1.0, 2.0, 3.0, 4.0]), lags=0) == pytest.approx(2.5 / math.sqrt(1.25 / 4))
    assert math.isnan(S.hac_t(np.array([1.0])))
    assert math.isnan(S.hac_t(np.array([2.0, 2.0, 2.0])))


def test_evaluate_drop5_removes_the_five_best_units():
    nets = [100, 90, 80, 70, 60, 1, 1, 1]
    assert S.evaluate(_records(nets)).drop5 == pytest.approx(1.0)
    assert math.isnan(S.evaluate(_records([5, 4, 3, 2, 1])).drop5)      # five or fewer units: cannot judge


def test_evaluate_empty_records_is_nan_with_zero_n():
    ev = S.evaluate(_records([]))
    assert ev.n == 0 and math.isnan(ev.edge) and math.isnan(ev.p)


def test_effective_n_counts_only_finite_units():
    rec = _records([1.0, 2.0, 3.0])
    rec.loc[1, "gross_bps"] = float("nan")
    assert S.effective_n(rec) == 2


def test_regime_split_is_reported_not_used():
    rec = _records([10, 10, -10, -10, 10])
    rec["regime_bear"] = [True, True, False, False, False]
    split = S.regime_split(rec)          # hold 1: one unit per date
    assert split["bear"]["n"] == 2 and split["bear"]["edge"] == pytest.approx(10.0)
    assert split["nonbear"]["n"] == 3 and split["nonbear"]["edge"] == pytest.approx(-10 / 3)
    assert S.regime_split(_records([1.0])) is None
    assert S.evaluate(rec).edge == pytest.approx(S.evaluate(rec.drop(columns="regime_bear")).edge)


# -- overlap: units are non-overlapping blocks of `hold` trading days ---------------------------------

def _cal(n=400, start="2026-01-05"):
    return list(pd.bdate_range(start, periods=n).strftime("%Y-%m-%d"))


def _daily(values, cal, cost=0.0):
    """One record per trading day: entry on cal[i], net edge values[i]."""
    n = len(values)
    return pd.DataFrame({"signal_date": cal[:n], "entry_date": cal[:n],
                         "gross_bps": np.asarray(values, float) + cost, "null_bps": 0.0, "cost_bps": cost})


def test_hold_five_with_an_entry_every_day_gives_one_unit_per_five_trading_days():
    cal = _cal()
    rec = _daily(np.ones(30), cal)
    assert S.effective_n(rec, hold=5, calendar=cal) == 6
    assert S.milestone_to_evaluate(S.effective_n(rec, hold=5, calendar=cal), []) is None      # 30 not reached


def test_hold_one_is_one_unit_per_entry_date():
    cal = _cal()
    rec = _daily(np.ones(30), cal)
    assert S.effective_n(rec, hold=1, calendar=cal) == 30 == S.effective_n(rec)


def test_blocks_follow_the_trading_calendar_not_calendar_days():
    cal = _cal()
    # entries on trading days 3,4 (block 0) and 5,9 (block 1) and 10 (block 2) for hold 5
    rec = _daily(np.zeros(11), cal).iloc[[3, 4, 5, 9, 10]]
    assert S.effective_n(rec, hold=5, calendar=cal) == 3


def test_a_block_averages_its_records_and_costs():
    cal = _cal()
    rec = pd.DataFrame({"signal_date": cal[:4], "entry_date": cal[:4], "gross_bps": [10.0, 30.0, 100.0, 100.0],
                        "null_bps": 0.0, "cost_bps": [0.0, 0.0, 20.0, 20.0]})
    ev = S.evaluate(rec, hold=2, calendar=cal)
    assert ev.n == 2
    assert ev.edge == pytest.approx((20.0 + 80.0) / 2) and ev.cost == pytest.approx(10.0)


def test_hold_above_one_needs_the_calendar():
    with pytest.raises(ValueError, match="calendar"):
        S.evaluate(_records([1.0] * 10), hold=5)


def test_hac_block_t_is_smaller_than_the_naive_per_date_t_on_overlapping_returns():
    hold, T = 5, 300
    cal = _cal(T + hold)
    noise = np.random.default_rng(11).normal(0, 50, T + hold)
    overlapped = np.array([noise[i:i + hold].sum() for i in range(T)])        # 5-day forward sums, zero true edge
    rec = _daily(overlapped, cal)
    naive_t = S.hac_t(overlapped, lags=0)
    ev = S.evaluate(rec, hold=hold, calendar=cal)
    assert ev.n == T // hold                                                  # N shrinks by ~hold
    assert abs(ev.t) < abs(naive_t)                                           # fixed seed: block-HAC is not inflated


def test_false_alert_rate_at_the_real_bar_on_overlapping_zero_edge_data_is_not_above_nominal():
    hold, T, seeds = 5, 300, 400
    cal = _cal(T + hold)
    thr = S.alert_p_threshold(11)
    p_hits = naive_hits = 0
    for seed in range(seeds):
        noise = np.random.default_rng(1000 + seed).normal(0, 50, T + hold)
        ov = np.array([noise[i:i + hold].sum() for i in range(T)])
        p_hits += S.evaluate(_daily(ov, cal), hold=hold, calendar=cal).p < thr
        from scipy import stats as sps
        naive_hits += sps.norm.sf(S.hac_t(ov, lags=0)) < thr
    # nominal 0.003 * 400 = 1.2 expected; allow sampling error (P[X >= 5 | 1.2] < 0.01)
    assert p_hits <= 4, p_hits
    assert naive_hits > p_hits               # the plain per-date t on the same data over-alerts


def test_period_families_are_one_unit_per_period_by_declaration():
    cal = _cal()
    # factor_books-style records: entries 21 trading days apart, hold-1 semantics (explicit in the spec)
    idx = list(range(0, 21 * 6, 21))
    rec = pd.DataFrame({"signal_date": [cal[i] for i in idx], "entry_date": [cal[i + 1] for i in idx],
                        "gross_bps": 5.0, "null_bps": 0.0, "cost_bps": 1.0})
    assert S.effective_n(rec, hold=1, calendar=cal) == 6
    assert S.evaluate(rec, hold=1, calendar=cal).edge == pytest.approx(4.0)
