"""
The dashboard's significance check and the like-for-like baseline series.

A cell passes the harness gate if its edge over the always-long null is > 0; that says nothing
about luck. The dashboard adds Welch's t (lib.stats.welch_t) of the strategy's per-trade
returns against the null's per-trade distribution, and a cell with t below lib.stats.MIN_T_STAT
cannot read as a candidate. Fixtures here have known answers.
"""
from __future__ import annotations

import numpy as np
import pytest

from dashboard import results as R
from dashboard import scorecard as S
from dashboard.verdicts import CANDIDATE, NOT_TRADEABLE, verdict_for
from lib.stats import MIN_T_STAT, welch_t
from strategy_fit.trade_export import BacktestTrade


def cell(gate, **kw):
    base = dict(symbol="XXXX", family="daily_momentum", hold=5, shape="single_name_time_series", reason="")
    base.update(kw)
    return R.ResultCell(gate=gate, **base)


def trade(i: int, ret: float) -> BacktestTrade:
    return BacktestTrade(
        signal_date=f"2025-01-{i + 1:02d}",
        entry_date=f"2025-01-{i + 2:02d}",
        exit_date=f"2025-01-{i + 3:02d}",
        entry_price=100.0,
        exit_price=100.0 * (1 + ret),
        gross_return=ret,
    )


# ---- verdict wording -------------------------------------------------------------------------


def test_passing_cell_whose_edge_is_not_significant_is_not_tradeable():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, 2.0, 0.4, 90, edge_vs_null_pct=1.3, t_stat=0.9)
    assert not v.tradeable and v.headline == NOT_TRADEABLE
    assert v.sentence == (
        "Not tradeable — beats random days by +1.30% per trade, but that could easily be luck (t = 0.9)"
    )
    sig = next(ch for ch in v.checks if "random days" in ch.name)
    assert sig.passed is False and "0.9" in sig.detail


def test_significant_edge_keeps_the_candidate_verdict():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, 2.0, 0.4, 90, edge_vs_null_pct=1.3, t_stat=2.4)
    assert v.tradeable and v.headline == CANDIDATE
    assert next(ch for ch in v.checks if "random days" in ch.name).passed is True


def test_t_exactly_at_the_bar_passes_and_just_under_fails():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    assert verdict_for(c, 2.0, 0.4, 90, edge_vs_null_pct=1.0, t_stat=MIN_T_STAT).tradeable
    assert not verdict_for(c, 2.0, 0.4, 90, edge_vs_null_pct=1.0, t_stat=MIN_T_STAT - 0.01).tradeable


def test_significance_is_not_judged_without_a_t_stat():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, 2.0, 0.4, 90)
    assert next(ch for ch in v.checks if "random days" in ch.name).passed is None
    assert v.tradeable


def test_underpowered_and_insignificant_reads_as_not_tradeable_not_unproven():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=True)
    v = verdict_for(c, 2.0, 0.4, 90, edge_vs_null_pct=1.3, t_stat=0.9)
    assert v.sentence.startswith("Not tradeable — beats random days")


def test_a_negative_edge_is_worded_as_trailing_not_beating():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, 2.0, 0.4, 90, edge_vs_null_pct=-0.5, t_stat=-0.3)
    assert "trails random days by -0.50%" in v.sentence and "beats" not in v.sentence


def test_other_gates_keep_their_own_sentence_even_with_a_t_stat():
    v = verdict_for(cell(R.GATE_COST), -1.0, -2.0, 70, edge_vs_null_pct=1.3, t_stat=0.9)
    assert v.sentence == "Not tradeable — loses after costs"


# ---- the t-stat on a trade list --------------------------------------------------------------

RETS = [0.04, -0.01, 0.03, 0.02, 0.0, 0.05]
NULL = [0.01, 0.0, 0.02, -0.01, 0.03, 0.01, 0.0, 0.02]


def test_edge_over_random_days_and_its_t_stat_use_the_null_distribution():
    trades = [trade(i, r) for i, r in enumerate(RETS)]
    st = S.trade_stats(trades, 100.0, always_long_return=float(np.mean(NULL)), null_returns=NULL, hold=1)
    assert st.edge_vs_null_pct == pytest.approx((np.mean(RETS) - np.mean(NULL)) * 100)
    # the cost is common to both sides, so it cancels out of the edge and the t
    assert st.t_stat == pytest.approx(welch_t(RETS, NULL, null_n_eff=len(NULL)))


def test_overlapping_null_trades_count_as_fewer_independent_draws():
    trades = [trade(i, r) for i, r in enumerate(RETS)]
    t1 = S.trade_stats(trades, 0, null_returns=NULL, hold=1).t_stat
    t5 = S.trade_stats(trades, 0, null_returns=NULL, hold=5).t_stat
    assert t5 < t1


def test_no_null_distribution_means_no_t_stat():
    st = S.trade_stats([trade(0, 0.01), trade(5, 0.02)], 0)
    assert st.t_stat is None and st.edge_vs_null_pct is None


# ---- the like-for-like baseline series -------------------------------------------------------


def test_baseline_is_trades_so_far_times_null_mean_after_costs():
    trades = [trade(0, 0.05), trade(5, 0.02), trade(9, 0.03)]
    strat, base, rows = S.cumulative_series(trades, cost_pct=1.0, null_mean=0.012)
    assert [v for _, v in base] == pytest.approx([0.0, 2_000.0, 4_000.0, 6_000.0])  # (1.2% - 1%) x 1M per trade
    assert [d for d, _ in base] == [d for d, _ in strat]  # same dates as the strategy line
    assert strat[0] == (trades[0].entry_date, 0.0)
    assert strat[-1][1] == pytest.approx((0.04 + 0.01 + 0.02) * 1_000_000)
    assert [r[2] for r in rows] == pytest.approx([40_000, 50_000, 70_000])


def test_baseline_gap_equals_edge_times_trades_so_far():
    trades = [trade(i * 4, r) for i, r in enumerate(RETS)]
    null_mean = float(np.mean(NULL))
    strat, base, _ = S.cumulative_series(trades, cost_pct=0.5, null_mean=null_mean)
    k = len(trades)
    gap = strat[-1][1] - base[-1][1]
    assert gap == pytest.approx(k * (np.mean(RETS) - null_mean) * 1_000_000)


def test_time_in_market_is_trade_days_over_the_span():
    assert S.in_market_pct(n_trades=3, hold=5, span_sessions=100) == pytest.approx(15.0)
    assert S.in_market_pct(0, 5, 0) is None
