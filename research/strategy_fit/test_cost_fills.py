"""cost_fills: join fills to intents, live/paper separation, broker fees from recon, broker-only imports."""

from __future__ import annotations

from strategy_fit import cost_fills as cf
from strategy_fit.cost_report_fixtures import write_broker_trades, write_cost_store, write_recon


def _row(**kw):
    base = dict(day="2026-01-05", symbol="AAAA", side="buy", decision=1000, fill=1000, lots=1, fee=1.5, sink="live")
    base.update(kw)
    return base


def test_store_fill_carries_decision_price_and_sink(tmp_path):
    write_cost_store(tmp_path / "2026-01-05-a.db", [_row(decision=1000, fill=1005, side="buy")])
    fills, cov = cf.load_store_fills(tmp_path)
    assert len(fills) == 1
    f = fills[0]
    assert (f.source, f.origin, f.symbol, f.side, f.lots, f.day) == ("live", "store", "AAAA", "buy", 1, "2026-01-05")
    assert f.decision_price_idr == 1000 and f.fill_price_idr == 1005 and f.arrival_mid_idr is None
    assert f.shares == 100 and f.notional_idr == 100_500


def test_live_and_paper_fills_are_kept_apart(tmp_path):
    write_cost_store(tmp_path / "2026-01-05-a.db", [_row(sink="live"), _row(sink="paper", symbol="BBBB")])
    fills, _ = cf.load_store_fills(tmp_path)
    assert {(f.source, f.symbol) for f in fills} == {("live", "AAAA"), ("paper", "BBBB")}


def test_excluded_and_unreadable_stores_are_skipped_and_reported(tmp_path):
    write_cost_store(tmp_path / "bad-name.db", [_row()])
    write_cost_store(tmp_path / "ok.db", [_row(symbol="BBBB")])
    (tmp_path / "junk.db").write_text("not sqlite")
    fills, cov = cf.load_store_fills(tmp_path, exclude=frozenset({"bad-name.db"}))
    assert [f.symbol for f in fills] == ["BBBB"]
    assert cov["unreadable_stores"] == ["junk.db"] and cov["excluded_stores"] == ["bad-name.db"]


def test_coverage_counts_unfilled_intents_and_rejects(tmp_path):
    write_cost_store(tmp_path / "x.db", [_row()], rejects=2, intents_only=3)
    _, cov = cf.load_store_fills(tmp_path)
    assert cov["live"] == {"intents": 4, "fills": 1, "rejects": 2}


def test_broker_fee_from_recon_overrides_the_store_fee(tmp_path):
    write_cost_store(tmp_path / "x.db", [_row(order_id="ord-1", fee=0.0)])
    write_recon(tmp_path, "2026-01-06", [{"store_order_id": "ord-1", "broker_id": "B1", "broker_fee_idr": 150.0,
                                          "store_fee_idr": 0.0}], [])
    fills, _ = cf.load_store_fills(tmp_path)
    merged, cov = cf.attach_broker_data(tmp_path, fills)
    (f,) = merged
    assert f.fee_idr == 150.0 and f.fee_source == "broker"
    assert cov["fills_with_broker_fee"] == 1


def test_fee_falls_back_to_store_when_the_broker_has_not_listed_it(tmp_path):
    write_cost_store(tmp_path / "x.db", [_row(order_id="ord-1", fee=150.0)])
    fills, _ = cf.load_store_fills(tmp_path)
    merged, _ = cf.attach_broker_data(tmp_path, fills)
    assert merged[0].fee_idr == 150.0 and merged[0].fee_source == "store"


def test_broker_only_trades_become_live_fills_without_a_decision_price(tmp_path):
    write_recon(tmp_path, "2026-01-06", [], [{"broker_id": "B9", "symbol": "AAAA", "side": "sell", "date": "2026-01-05",
                                              "time_wib": "10:00:00", "lots": 1, "price_idr": 990, "fee_idr": 247.5}])
    merged, cov = cf.attach_broker_data(tmp_path, [])
    (f,) = merged
    assert (f.source, f.origin, f.decision_price_idr, f.fee_source) == ("live", "broker_import", None, "broker")
    assert f.fee_idr == 247.5 and f.day == "2026-01-05" and cov["broker_only_fills"] == 1


def test_broker_import_file_and_recon_do_not_double_count(tmp_path):
    trade = {"broker_id": "B9", "symbol": "AAAA", "side": "sell", "date": "2026-01-05", "time_wib": "10:00:00",
             "lots": 1, "price_idr": 990, "fee_idr": 247.5}
    write_recon(tmp_path, "2026-01-06", [], [trade])
    write_broker_trades(tmp_path, [dict(trade, source="broker-import")])
    merged, _ = cf.attach_broker_data(tmp_path, [])
    assert len(merged) == 1


def test_broker_only_trade_already_matched_to_a_store_fill_is_not_added_again(tmp_path):
    write_cost_store(tmp_path / "x.db", [_row(order_id="ord-1")])
    write_recon(tmp_path, "2026-01-06",
                [{"store_order_id": "ord-1", "broker_id": "B1", "broker_fee_idr": 1.5, "store_fee_idr": 1.5}], [])
    write_broker_trades(tmp_path, [{"broker_id": "B1", "symbol": "AAAA", "side": "buy", "date": "2026-01-05",
                                    "time_wib": "09:30:00", "lots": 1, "price_idr": 1000, "fee_idr": 1.5}])
    fills, _ = cf.load_store_fills(tmp_path)
    merged, _ = cf.attach_broker_data(tmp_path, fills)
    assert len(merged) == 1


def test_missing_recon_and_import_files_are_fine(tmp_path):
    merged, cov = cf.attach_broker_data(tmp_path, [])
    assert merged == [] and cov["recon_files"] == 0
