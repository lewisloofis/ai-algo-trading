"""Merged live ledger: FIFO per symbol, own prices and fees, broker comparison, loud failures."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from dashboard import ledger as L
from dashboard import manual_trades as M
from dashboard import trades_data as T


def bot(symbol, side, lots, price, fee, when, order_id, sink="live"):
    return T.OrderLine(
        when=when, symbol=symbol, side=side, lots=lots, limit_price=int(price), strategy_id="roundtrip-goto",
        strategy_label="x", strategy_note="", run_id="r", sink_kind=sink, filled_lots=lots, fill_price=float(price),
        fees_idr=fee, realised_pnl_idr=None, status="filled", reject_reason=None, order_id=order_id)


def manual(symbol="GOTO", side="sell", lots=1, price=26, fee=6.5, day="2026-10-13", time_wib=None, **kw):
    return M.ManualTrade(date=day, time_wib=time_wib, symbol=symbol, side=side, lots=lots,
                         price_idr=float(price), fee_idr=float(fee), note="", **kw)


def at(day, hh=10):
    return datetime(*map(int, day.split("-")), hh, 0)


def test_fifo_matches_oldest_buy_first_and_nets_both_fees():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1"),
            bot("AAA", "buy", 1, 110, 3, at("2026-10-02"), "b2")]
    led = L.build_ledger(live, [manual("AAA", "sell", 1, 120, 5, "2026-10-03", "11:00")])
    # sells the 100 lot: 12000 - 5 - (10000 + 2)
    assert led.realised_pnl_idr == 12000 - 5 - 10000 - 2
    (pos,) = led.positions
    assert (pos.symbol, pos.lots, pos.avg_cost) == ("AAA", 1, 110.0)
    assert led.round_trips_closed == 1


def test_a_sell_spanning_two_buys_prorates_fees_per_lot():
    live = [bot("AAA", "buy", 1, 100, 4, at("2026-10-01"), "b1"),
            bot("AAA", "buy", 3, 110, 12, at("2026-10-02"), "b2")]
    led = L.build_ledger(live, [manual("AAA", "sell", 2, 120, 10, "2026-10-03", "11:00")])
    # lots: 1 from b1 (fee 4) + 1 from b2 (fee 12/3=4); sell fee 10 split 5/5
    assert led.realised_pnl_idr == (24000 - 10) - (10000 + 4) - (11000 + 4)
    assert led.positions[0].lots == 2 and led.positions[0].avg_cost == 110.0


def test_fees_total_counts_bot_and_manual_legs():
    led = L.build_ledger([bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1")],
                         [manual("AAA", "sell", 1, 100, 7, "2026-10-02")])
    assert led.fees_idr == 9


def test_a_manual_sell_that_would_go_negative_fails_loudly():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1")]
    with pytest.raises(L.NegativePositionError, match="AAA.*2026-10-03"):
        L.build_ledger(live, [manual("AAA", "sell", 2, 100, 1, "2026-10-03")])


def test_double_entry_of_the_same_sale_is_caught():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1")]
    twice = [manual("AAA", "sell", 1, 100, 1, "2026-10-03"), manual("AAA", "sell", 1, 100, 1, "2026-10-03")]
    with pytest.raises(L.NegativePositionError):
        L.build_ledger(live, twice)


def test_selling_a_symbol_never_bought_fails():
    with pytest.raises(L.NegativePositionError, match="ZZZ"):
        L.build_ledger([], [manual("ZZZ", "sell", 1, 100, 1, "2026-10-03")])


def test_same_day_unknown_time_sell_sorts_after_the_bot_buy():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-03", 14), "b1")]
    led = L.build_ledger(live, [manual("AAA", "sell", 1, 90, 1, "2026-10-03", None)])
    assert led.round_trips_closed == 1 and not led.positions


def test_paper_orders_never_enter_the_ledger():
    paper = bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "p", sink="paper")
    with pytest.raises(ValueError, match="paper"):
        L.build_ledger([paper], [])


def test_unfilled_bot_orders_are_ignored():
    unfilled = replace(bot("AAA", "buy", 1, 100, 0, at("2026-10-01"), "u"),
                       filled_lots=0, fill_price=None, status="not filled")
    assert L.build_ledger([unfilled], []).positions == ()


def test_broker_pnl_is_shown_with_difference_and_the_fee_reason():
    live = [bot("GOTO", "buy", 1, 27, 0, at("2026-10-13", 14), "rebalance-GOTO-2026-10-13")]
    sale = manual(fee=6.5, net_amount_idr=2593.5, broker_realised_pnl_idr=-110.55,
                  closes_order_id="rebalance-GOTO-2026-10-13")
    led = L.build_ledger(live, [sale])
    (rec,) = led.reconciliations
    assert rec.ours_idr == pytest.approx(-106.5)
    assert rec.broker_idr == -110.55
    assert rec.difference_idr == pytest.approx(-4.05)
    assert rec.implied_buy_fee_idr == pytest.approx(4.05)
    assert rec.recorded_buy_fee_idr == 0
    assert "bot store recorded buy fee 0 (broker 4.05)" in rec.reason
    assert rec.closes_order_id == "rebalance-GOTO-2026-10-13"


def test_a_matching_broker_figure_says_so():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1")]
    led = L.build_ledger(live, [manual("AAA", "sell", 1, 120, 5, "2026-10-03", broker_realised_pnl_idr=1993.0)])
    (rec,) = led.reconciliations
    assert rec.difference_idr == pytest.approx(0.0) and "agree" in rec.reason


def test_no_reconciliation_without_a_broker_figure():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1")]
    assert L.build_ledger(live, [manual("AAA", "sell", 1, 120, 5, "2026-10-03")]).reconciliations == ()


def test_closes_order_id_must_name_a_known_bot_order():
    live = [bot("AAA", "buy", 1, 100, 2, at("2026-10-01"), "b1")]
    with pytest.raises(L.UnknownOrderError, match="nope"):
        L.build_ledger(live, [manual("AAA", "sell", 1, 100, 1, "2026-10-03", closes_order_id="nope")])


def test_open_position_remembers_when_and_at_what_price_it_was_bought():
    live = [bot("GOTO", "buy", 1, 27, 0, at("2026-10-13"), "a"), bot("GOTO", "buy", 1, 28, 4, at("2026-10-14"), "b")]
    led = L.build_ledger(live, [manual(side="sell", price=26, fee=7)])
    (pos,) = led.positions
    assert pos.lots == 1 and pos.avg_cost == 28.0
    assert pos.lots_detail == (("2026-10-14", 1, 28.0),)


def test_goto_scenario_end_to_end_from_stores(make_store):
    from dashboard import store

    ns = 1_791_875_520_080_349_000  # 2026-10-13 ~14:12 WIB
    ns2 = 1_791_990_000_000_000_000  # 2026-10-14
    path = make_store(
        intents=[
            {"seq": 1, "recv_ns": ns, "strategy_id": "manual-first-order-fill", "symbol": "GOTO", "side": "buy",
             "price_idr": 27, "lots": 1, "order_id": "rebalance-GOTO-2026-10-13", "sink_kind": "live"},
            {"seq": 2, "recv_ns": ns2, "strategy_id": "roundtrip-goto", "symbol": "GOTO", "side": "buy",
             "price_idr": 28, "lots": 1, "order_id": "rt-buy", "sink_kind": "live"},
        ],
        fills=[
            {"seq": 1, "order_id": "rebalance-GOTO-2026-10-13", "fill_price_idr": 27, "fill_lots": 1, "fee_idr": 0,
             "sink_kind": "live"},
            {"seq": 2, "order_id": "rt-buy", "fill_price_idr": 28, "fill_lots": 1, "fee_idr": 4, "sink_kind": "live"},
        ],
    )
    live, _ = T.split_by_sink(T.build_orders(store.read_stores([path]), {}))
    sale = manual(fee=6.5, net_amount_idr=2593.5, broker_realised_pnl_idr=-110.55,
                  closes_order_id="rebalance-GOTO-2026-10-13")
    led = L.build_ledger(live, [sale])
    assert [(p.symbol, p.lots, p.avg_cost) for p in led.positions] == [("GOTO", 1, 28.0)]
    assert led.realised_pnl_idr == pytest.approx(-106.5)
    assert led.reconciliations[0].broker_idr == -110.55
