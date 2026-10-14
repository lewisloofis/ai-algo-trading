"""Trades data: the safety guarantees (loud unmapped ids, no calibration, live never summed with paper)."""
from __future__ import annotations

import pytest

from dashboard import store, trades_data as T
from dashboard.annotations import Annotation
from dashboard.strategy_map import UnmappedStrategyError

LIVE_ID = "roundtrip-goto"
NS = 1_791_990_000_000_000_000  # 2026-10-14 ~ 15:40 WIB


def read(make_store, **kw):
    return store.read_stores([make_store(**kw)])


def one_buy_and_sell(make_store):
    return read(
        make_store,
        intents=[
            {"seq": 1, "recv_ns": NS, "strategy_id": LIVE_ID, "symbol": "GOTO", "side": "buy", "price_idr": 28,
             "lots": 2, "order_id": "b", "sink_kind": "live"},
            {"seq": 2, "recv_ns": NS + 10**12, "strategy_id": LIVE_ID, "symbol": "GOTO", "side": "sell",
             "price_idr": 30, "lots": 2, "order_id": "s", "sink_kind": "live"},
        ],
        fills=[
            {"seq": 1, "order_id": "b", "fill_price_idr": 28, "fill_lots": 2, "fee_idr": 4, "sink_kind": "live"},
            {"seq": 2, "order_id": "s", "fill_price_idr": 30, "fill_lots": 2, "fee_idr": 5, "net_pnl_idr": 291,
             "sink_kind": "live"},
        ],
    )


def test_intents_and_fills_are_joined_newest_first(make_store):
    lines = T.build_orders(one_buy_and_sell(make_store), {})
    assert [x.side for x in lines] == ["sell", "buy"]
    buy = lines[1]
    assert (buy.filled_lots, buy.fill_price, buy.fees_idr, buy.status) == (2, 28.0, 4, "filled")
    assert buy.realised_pnl_idr is None
    assert lines[0].realised_pnl_idr == 291
    assert buy.strategy_label.startswith("Order-path test")


def test_an_order_with_no_fill_is_not_filled(make_store):
    data = read(make_store, intents=[{"seq": 1, "strategy_id": LIVE_ID, "sink_kind": "live", "order_id": "x"}])
    (line,) = T.build_orders(data, {})
    assert line.status == "not filled" and not line.filled and line.fill_price is None


def test_a_recorded_rejection_is_explained_in_plain_words(make_store):
    data = read(
        make_store,
        intents=[{"seq": 1, "strategy_id": LIVE_ID, "sink_kind": "live", "order_id": "x"}],
        rejects=[{"seq": 1, "strategy_id": LIVE_ID, "sink_kind": "live", "order_id": "x",
                  "reason": "[C-006] insufficient trading balance"}],
    )
    (line,) = T.build_orders(data, {})
    assert line.status == "rejected"
    assert line.reject_reason == "The broker refused it: not enough cash available to trade."


def test_a_rejection_that_predates_the_reject_table_comes_from_the_annotation(make_store):
    data = read(
        make_store,
        intents=[{"seq": 1, "strategy_id": LIVE_ID, "sink_kind": "live", "order_id": "x", "run_id": "r9"}],
    )
    note = Annotation("r9", "n", "s", reject_reason="[C-006] insufficient trading balance")
    (line,) = T.build_orders(data, {"r9": note})
    assert line.status == "rejected" and "not enough cash" in line.reject_reason


def test_an_unmapped_strategy_id_fails_the_build_loudly(make_store):
    data = read(make_store, intents=[{"seq": 1, "strategy_id": "mystery-algo", "order_id": "x"}])
    with pytest.raises(UnmappedStrategyError, match="mystery-algo"):
        T.build_orders(data, {})


def test_an_unmapped_id_on_a_reject_row_also_fails(make_store):
    data = read(make_store, rejects=[{"seq": 1, "strategy_id": "mystery-algo"}])
    with pytest.raises(UnmappedStrategyError):
        T.build_orders(data, {})


def test_calibration_orders_are_excluded_not_mapped(make_store):
    data = read(
        make_store,
        intents=[{"seq": 1, "strategy_id": "calibration", "order_id": "c", "sink_kind": "live"},
                 {"seq": 2, "strategy_id": LIVE_ID, "order_id": "x", "sink_kind": "live"}],
        fills=[{"seq": 1, "order_id": "c", "net_pnl_idr": 99999, "sink_kind": "live"}],
    )
    lines = T.build_orders(data, {})
    assert [x.strategy_id for x in lines] == [LIVE_ID]
    assert T.live_summary(lines).realised_pnl_idr == 0


def test_live_and_paper_are_split_and_never_summed(make_store):
    data = read(
        make_store,
        intents=[
            {"seq": 1, "strategy_id": LIVE_ID, "order_id": "l", "sink_kind": "live", "symbol": "GOTO", "lots": 1},
            {"seq": 2, "strategy_id": "manual-first-order-shadow", "order_id": "p", "sink_kind": "paper",
             "symbol": "BBCA", "lots": 1},
        ],
        fills=[
            {"seq": 1, "order_id": "l", "fill_price_idr": 28, "fee_idr": 4, "net_pnl_idr": 50, "sink_kind": "live"},
            {"seq": 2, "order_id": "p", "fill_price_idr": 5925, "fee_idr": 889, "net_pnl_idr": 7777,
             "sink_kind": "paper"},
        ],
    )
    live, paper = T.split_by_sink(T.build_orders(data, {}))
    assert [x.symbol for x in live] == ["GOTO"] and [x.symbol for x in paper] == ["BBCA"]
    s = T.live_summary(live)
    assert (s.realised_pnl_idr, s.fees_idr) == (50, 4)  # nothing from the paper fill
    with pytest.raises(ValueError, match="never be summed"):
        T.live_summary(live + paper)


def test_open_positions_are_net_live_lots_with_average_cost(make_store):
    data = read(
        make_store,
        intents=[
            {"seq": 1, "strategy_id": LIVE_ID, "order_id": "a", "sink_kind": "live", "symbol": "GOTO", "lots": 1},
            {"seq": 2, "strategy_id": LIVE_ID, "order_id": "b", "sink_kind": "live", "symbol": "GOTO", "lots": 1},
            {"seq": 3, "strategy_id": LIVE_ID, "order_id": "c", "sink_kind": "live", "symbol": "BBCA", "lots": 1},
            {"seq": 4, "strategy_id": LIVE_ID, "order_id": "d", "sink_kind": "live", "symbol": "BBCA", "lots": 1,
             "side": "sell"},
        ],
        fills=[
            {"seq": 1, "order_id": "a", "fill_price_idr": 27, "sink_kind": "live"},
            {"seq": 2, "order_id": "b", "fill_price_idr": 28, "sink_kind": "live"},
            {"seq": 3, "order_id": "c", "fill_price_idr": 5000, "sink_kind": "live"},
            {"seq": 4, "order_id": "d", "fill_price_idr": 5100, "sink_kind": "live"},
        ],
    )
    live, _ = T.split_by_sink(T.build_orders(data, {}))
    (pos,) = T.live_summary(live).positions
    assert (pos.symbol, pos.lots, pos.avg_cost, pos.cost_idr) == ("GOTO", 2, 27.5, 5500.0)


@pytest.mark.parametrize(
    "raw,phrase",
    [
        ("[C-006] insufficient trading balance", "not enough cash"),
        ("paper sink: no book observed for symbol", "No price quote"),
        ("risk: session order cap reached", "safety limits blocked it (session order cap reached)"),
        ("something new", "Refused: something new"),
    ],
)
def test_plain_reasons(raw, phrase):
    assert phrase in T.plain_reason(raw)


def test_store_reads_intents_and_fills(make_store):
    data = one_buy_and_sell(make_store)
    assert len(data.intents) == 2 and len(data.fills) == 2
    assert data.fills[1].net_pnl_idr == 291
