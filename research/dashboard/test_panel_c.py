"""
Panel C tests — per-strategy profitability, and the four columns without which
the panel misleads (plan-19 §5).

The load-bearing ones:
  * join safety — an unmapped strategy_id fails the render (§8)
  * sink separation — replay and live PnL never share a row or a total
  * calibration exclusion — ADR-0016 §6, enforced here, not trusted to callers
"""
from __future__ import annotations

import pytest

from dashboard import panel_c, store
from dashboard.store import Attribution, RejectRow, StoreData
from dashboard.strategy_map import UnmappedStrategyError


def attribution(**overrides) -> Attribution:
    base = dict(
        run_id="r1",
        seq=1,
        symbol="AAA",
        strategy_id="orb",
        entry_recv_ns=1_000,
        exit_recv_ns=2_000,
        gross_pnl_bps=50.0,
        net_pnl_bps=20.0,
        entry_order_id="o1",
        exit_order_id="o2",
        hold_calendar_days=0.0,
        as_of_date="2026-09-16",
        sink_kind="paper",
        store_path="/tmp/run.db",
    )
    base.update(overrides)
    return Attribution(**base)


class TestJoinSafety:
    def test_an_unmapped_strategy_id_fails_the_render(self):
        """
        §8: skipping the row under-reports PnL silently; guessing a family
        compares two different strategies. Neither is better than stopping.
        """
        with pytest.raises(UnmappedStrategyError, match="mean_reversion"):
            panel_c.summarise([attribution(strategy_id="mean_reversion")])

    def test_the_error_names_the_known_ids(self):
        with pytest.raises(UnmappedStrategyError, match=r"Known ids: .*\borb\b"):
            panel_c.summarise([attribution(strategy_id="whatever")])

    def test_orb_is_not_joined_to_intraday_momentum(self):
        """
        Plan-17's warning box: the Go 'orb' fires 0.5% off the first tick, the
        Python family on a close above the 30-minute range high. Joining them
        by string would report one strategy's cost against another's edge.
        """
        row = panel_c.summarise([attribution()])[0]
        assert row.join.research_family is None
        assert row.join.definitions_match is False
        assert "30-minute" in row.join.note


class TestCalibrationExclusion:
    def test_calibration_rows_never_reach_a_pnl_total(self):
        rows = panel_c.summarise([attribution(), attribution(seq=2, strategy_id="calibration")])
        assert [r.strategy_id for r in rows] == ["orb"]
        assert rows[0].round_trips == 1

    def test_the_exclusion_is_reported_not_hidden(self):
        """"0 shown" and "0 recorded" are different facts."""
        data = StoreData(
            attributions=[attribution(), attribution(seq=2, strategy_id="calibration")]
        )
        panel = panel_c.build_panel_c(data)
        assert panel.excluded_round_trips == 1
        assert "calibration" in panel.excluded_ids

    def test_an_unmapped_id_still_fails_even_with_calibration_present(self):
        with pytest.raises(UnmappedStrategyError):
            panel_c.summarise(
                [attribution(strategy_id="calibration"), attribution(seq=2, strategy_id="ghost")]
            )


class TestSinkSeparation:
    def test_replay_and_live_are_separate_rows(self):
        rows = panel_c.summarise(
            [
                attribution(net_pnl_bps=10.0, sink_kind="paper"),
                attribution(seq=2, net_pnl_bps=90.0, sink_kind="live"),
            ]
        )
        assert len(rows) == 2
        by_sink = {r.sink_kind: r for r in rows}
        assert by_sink["paper"].mean_net_pnl_bps == 10.0
        assert by_sink["live"].mean_net_pnl_bps == 90.0
        assert by_sink["paper"].is_replay is True
        assert by_sink["live"].is_replay is False

    def test_the_panel_knows_whether_anything_is_live(self):
        paper_only = panel_c.build_panel_c(StoreData(attributions=[attribution()]))
        assert paper_only.has_live is False
        with_live = panel_c.build_panel_c(
            StoreData(attributions=[attribution(sink_kind="live")])
        )
        assert with_live.has_live is True

    def test_a_live_run_header_alone_marks_the_panel_live(self):
        from dashboard.test_panel_b import run_header

        data = StoreData(runs=[run_header(sink_kind="live")])
        assert panel_c.build_panel_c(data).has_live_runs is True
        assert panel_c.build_panel_c(StoreData(runs=[run_header(sink_kind="paper")])).has_live_runs is False


class TestAggregation:
    def test_mean_net_matches_the_engine_counter_arithmetic(self):
        """
        Deliberately the same definition as engine.finalize's
        Counters.NetPnlBps: sum of net bps / round-trips. A Go test
        (TestAttributionAverageMatchesCounters) pins the other side.
        """
        rows = panel_c.summarise(
            [
                attribution(net_pnl_bps=10.0),
                attribution(seq=2, net_pnl_bps=-30.0),
                attribution(seq=3, net_pnl_bps=20.0),
            ]
        )
        assert rows[0].mean_net_pnl_bps == pytest.approx(0.0)
        assert rows[0].round_trips == 3

    def test_fee_gap_is_gross_minus_net(self):
        rows = panel_c.summarise([attribution(gross_pnl_bps=50.0, net_pnl_bps=20.0)])
        assert rows[0].fee_slippage_gap_bps == pytest.approx(30.0)

    def test_win_rate_counts_strictly_positive_trades(self):
        rows = panel_c.summarise(
            [
                attribution(net_pnl_bps=1.0),
                attribution(seq=2, net_pnl_bps=0.0),
                attribution(seq=3, net_pnl_bps=-1.0),
                attribution(seq=4, net_pnl_bps=2.0),
            ]
        )
        assert rows[0].win_rate == pytest.approx(0.5)

    def test_max_drawdown_is_peak_to_trough_of_the_cumulative_curve(self):
        rows = panel_c.summarise(
            [
                attribution(seq=1, exit_recv_ns=1, net_pnl_bps=100.0),
                attribution(seq=2, exit_recv_ns=2, net_pnl_bps=-40.0),
                attribution(seq=3, exit_recv_ns=3, net_pnl_bps=-30.0),
                attribution(seq=4, exit_recv_ns=4, net_pnl_bps=50.0),
            ]
        )
        assert rows[0].max_drawdown_bps == pytest.approx(70.0)

    def test_drawdown_ordering_follows_exit_time_not_insertion(self):
        rows = panel_c.summarise(
            [
                attribution(seq=1, exit_recv_ns=9, net_pnl_bps=-50.0),
                attribution(seq=2, exit_recv_ns=1, net_pnl_bps=100.0),
            ]
        )
        assert rows[0].max_drawdown_bps == pytest.approx(50.0)

    def test_date_range_uses_the_wib_day_key(self):
        rows = panel_c.summarise(
            [
                attribution(seq=1, as_of_date="2026-09-16"),
                attribution(seq=2, as_of_date="2026-06-22"),
            ]
        )
        assert rows[0].first_as_of_date == "2026-06-22"
        assert rows[0].last_as_of_date == "2026-09-16"


class TestDeployabilityScreen:
    def test_required_n_is_none_when_the_edge_is_non_positive(self):
        """Nothing to resolve — and substituting a default would invent a threshold."""
        rows = panel_c.summarise(
            [attribution(net_pnl_bps=-10.0), attribution(seq=2, net_pnl_bps=-20.0)]
        )
        assert rows[0].required_effective_n is None
        assert rows[0].underpowered is None

    def test_required_n_is_none_with_a_single_trade(self):
        rows = panel_c.summarise([attribution()])
        assert rows[0].per_trade_sigma_bps is None
        assert rows[0].required_effective_n is None

    def test_a_thin_result_is_flagged_underpowered(self):
        """A small edge against a wide dispersion needs years to be falsifiable."""
        rows = panel_c.summarise(
            [attribution(seq=i, net_pnl_bps=v) for i, v in enumerate([5.0, -150.0, 160.0, 2.0], 1)]
        )
        row = rows[0]
        assert row.required_effective_n is not None
        assert row.required_effective_n > row.round_trips
        assert row.underpowered is True

    def test_it_agrees_with_lib_stats(self):
        import statistics

        from lib.stats import required_effective_n

        values = [5.0, -150.0, 160.0, 2.0]
        rows = panel_c.summarise(
            [attribution(seq=i, net_pnl_bps=v) for i, v in enumerate(values, 1)]
        )
        assert rows[0].required_effective_n == required_effective_n(
            target_edge_bps=statistics.fmean(values),
            per_trade_sigma_bps=statistics.stdev(values),
            z=panel_c.POWER_Z,
        )


class TestEmptyStore:
    def test_no_rows_is_not_an_error(self):
        panel = panel_c.build_panel_c(StoreData())
        assert panel.rows == []
        assert panel.excluded_round_trips == 0
        assert panel.rejects == []


def reject(**overrides) -> RejectRow:
    base = dict(
        run_id="r1",
        seq=1,
        recv_ns=1_000,
        strategy_id="orb",
        symbol="AAA",
        side="buy",
        price_idr=1000,
        lots=1,
        order_id="o1",
        reason="risk: cap exceeded",
        sink_kind="paper",
        store_path="/tmp/run.db",
    )
    base.update(overrides)
    return RejectRow(**base)


class TestRejections:
    """Task 3: the SQLite audit trail for rejections, surfaced on Panel C."""

    def test_no_rejects_is_an_empty_list_not_an_error(self):
        panel = panel_c.build_panel_c(StoreData(rejects=[]))
        assert panel.rejects == []

    def test_rejects_are_grouped_by_strategy_sink_and_reason(self):
        rows = [
            reject(seq=1, reason="risk: cap exceeded"),
            reject(seq=2, reason="risk: cap exceeded"),
            reject(seq=3, reason="risk: market state unknown"),
            reject(seq=4, strategy_id="rebalance", sink_kind="live", reason="risk: cap exceeded"),
        ]
        summary = panel_c.summarise_rejects(rows)
        by_key = {(s.strategy_id, s.sink_kind, s.reason): s.count for s in summary}
        assert by_key[("orb", "paper", "risk: cap exceeded")] == 2
        assert by_key[("orb", "paper", "risk: market state unknown")] == 1
        assert by_key[("rebalance", "live", "risk: cap exceeded")] == 1

    def test_calibration_rejects_are_excluded_like_everything_else(self):
        rows = [reject(strategy_id="calibration"), reject(seq=2, strategy_id="orb")]
        summary = panel_c.summarise_rejects(rows)
        assert [s.strategy_id for s in summary] == ["orb"]

    def test_build_panel_c_includes_rejects(self):
        data = StoreData(rejects=[reject()])
        panel = panel_c.build_panel_c(data)
        assert len(panel.rejects) == 1
        assert panel.rejects[0].count == 1
