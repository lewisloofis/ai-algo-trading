"""Scorecard figures: plain arithmetic over harness-derived trades, cross-checked against the harness's own script."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dashboard import results as R
from dashboard import scorecard as S
from strategy_fit.compare_d1_rescore import TOP_N, concentration
from strategy_fit.harness import CellResult
from strategy_fit.trade_export import BacktestTrade, export_cell


def trade(i: int, ret: float) -> BacktestTrade:
    return BacktestTrade(
        signal_date=f"2025-01-{i + 1:02d}",
        entry_date=f"2025-01-{i + 2:02d}",
        exit_date=f"2025-01-{i + 3:02d}",
        entry_price=100.0,
        exit_price=100.0 * (1 + ret),
        gross_return=ret,
    )


def test_average_after_costs_in_percent_and_rupiah():
    trades = [trade(i, r) for i, r in enumerate([0.03, -0.01, 0.02, 0.0])]
    st = S.trade_stats(trades, cost_bps=100.0)  # 1% cost per trade
    assert st.n_trades == 4
    assert st.gross_avg_pct == pytest.approx(1.0)
    assert st.net_avg_pct == pytest.approx(0.0)
    assert st.net_avg_idr == pytest.approx(0.0, abs=1e-6)
    assert st.win_rate == pytest.approx(0.5)  # +2% and +1% net; 0 and -2% are not wins


def test_without_best_trades_needs_more_than_top_n_trades():
    few = [trade(i, 0.05) for i in range(TOP_N)]
    assert S.trade_stats(few, 0).net_without_best_pct is None
    many = [trade(i, 0.01) for i in range(TOP_N)] + [trade(9, 0.5)]
    st = S.trade_stats(many, 0)
    # the huge winner plus four of the 1% trades are dropped; one 1% trade remains
    assert st.net_without_best_pct == pytest.approx(1.0)
    assert st.net_without_best_pct < st.net_avg_pct


def test_holding_return_is_first_entry_to_last_exit():
    trades = [trade(0, 0.01), trade(5, 0.02)]
    closes = {trades[0].entry_date: 100.0, trades[-1].exit_date: 120.0}
    st = S.trade_stats(trades, 0, closes)
    assert st.hold_total_pct == pytest.approx(20.0)
    assert st.hold_total_idr == pytest.approx(200_000.0)
    assert S.trade_stats(trades, 0, {}).hold_total_pct is None


def test_no_trades_means_no_stats():
    assert S.trade_stats([], 50.0) is None


def test_matches_the_harness_s_own_top5_script(tmp_path):
    rng = np.random.default_rng(3)
    n = 600
    close = 1000 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, n)))
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
            "open": close,
            "close": close,
            "volume": rng.integers(1_000, 90_000, n).astype(float),
        }
    )
    df.to_parquet(tmp_path / "TEST.parquet")
    ct = export_cell("TEST", "mean_reversion", 2, data_dir=tmp_path)
    hurdle = 60.0
    mine = S.trade_stats(ct.trades, hurdle)
    cell = CellResult(
        symbol="TEST",
        family_name="mean_reversion",
        shape="single_name_time_series",
        horizon_trading_days=2,
        status="tested",
        min_edge_hurdle_bps=hurdle,
    )
    theirs = concentration(cell, str(tmp_path), None)
    assert theirs is not None
    all_net_bps, trimmed_bps, n, _ = theirs
    assert mine.net_avg_pct * 100 == pytest.approx(all_net_bps, abs=1e-6)
    assert mine.net_without_best_pct * 100 == pytest.approx(trimmed_bps, abs=1e-6)


def test_highlight_rules():
    def c(gate, sym="AAAA", fam="daily_momentum", hold=5, shape="single_name_time_series"):
        return R.ResultCell(sym, fam, hold, shape, gate, "")

    assert S.is_highlighted(c(R.GATE_PASSED_ALL))
    assert S.is_highlighted(c(R.GATE_FDR))
    assert S.is_highlighted(c(R.GATE_NULL))
    assert not S.is_highlighted(c(R.GATE_COST))
    assert not S.is_highlighted(c(R.GATE_INSUFFICIENT_N))
    assert S.is_highlighted(c(R.GATE_COST, "BNBR", "daily_momentum", 2))  # famous
    assert not S.is_highlighted(c(R.GATE_IMPACT_UNPRICED, shape="event_driven"))


def test_reproduction_check_flags_changed_data():
    trades = [trade(i, 0.02) for i in range(4)]  # gross 200 bps
    st = S.trade_stats(trades, 50.0)
    ok = R.ResultCell("A", "f", 2, "s", R.GATE_COST, "", hurdle_bps=50.0, net_edge_bps=150.0)
    bad = R.ResultCell("A", "f", 2, "s", R.GATE_COST, "", hurdle_bps=50.0, net_edge_bps=90.0)
    assert S.reproduces_report(ok, st) is True
    assert S.reproduces_report(bad, st) is False
    assert S.reproduces_report(R.ResultCell("A", "f", 2, "s", R.GATE_COST, ""), st) is None


def test_build_rows_uses_report_hurdle_and_flags_famous():
    cellr = R.ResultCell(
        "BNBR", "daily_momentum", 2, "single_name_time_series", R.GATE_COST, "cost_gated",
        hurdle_bps=100.0, net_edge_bps=-20.0,
    )
    run = R.RunResults("p", "2026-10-14T00:00:00Z", "owned_cash", (cellr,), {"BNBR": 100.0})
    trades = tuple(trade(i, 0.008) for i in range(6))
    from strategy_fit.trade_export import CellTrades

    bars = pd.DataFrame({"date": [t.entry_date for t in trades] + [trades[-1].exit_date],
                         "close": [100.0] * 7})
    ct = CellTrades("BNBR", "daily_momentum", 2, trades, bars)
    rows = S.build_rows(run, {cellr.key: ct})
    row = rows[0]
    assert row.famous and row.highlighted
    assert row.stats.net_avg_pct == pytest.approx(-0.2)
    assert row.verdict.sentence == "Not tradeable — loses after costs"
    assert row.reproduces is True  # 80 bps gross == report's -20 net + 100 cost


def test_buying_any_day_baseline_gets_the_same_cost():
    st = S.trade_stats([trade(0, 0.02)], cost_bps=50.0, always_long_return=0.01)
    assert st.any_day_avg_pct == pytest.approx(0.5)
    assert st.any_day_avg_idr == pytest.approx(5_000.0)
    assert S.trade_stats([trade(0, 0.02)], 50.0).any_day_avg_pct is None
