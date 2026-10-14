"""Manual-trade ledger file: strict validation at load; a missing file is simply no manual trades."""
from __future__ import annotations

import json
import os

import pytest

from dashboard import manual_trades as M

GOOD = {
    "date": "2026-10-13", "time_wib": None, "symbol": "GOTO", "side": "sell", "lots": 1,
    "price_idr": 26, "fee_idr": 7, "net_amount_idr": 2594, "broker_realised_pnl_idr": -110.56,
    "closes_order_id": "rebalance-GOTO-2026-10-13", "note": "by hand",
}


def write(tmp_path, trades, **top):
    p = tmp_path / "manual_trades.json"
    p.write_text(json.dumps({"trades": trades, **top}))
    return str(p)


def load_one(tmp_path, **over):
    return M.load_manual_trades(write(tmp_path, [{**GOOD, **over}]))


def test_missing_file_means_no_manual_trades(tmp_path):
    assert M.load_manual_trades(str(tmp_path / "nope.json")) == []


def test_a_good_entry_loads_every_field(tmp_path):
    (t,) = load_one(tmp_path)
    assert (t.date, t.time_wib, t.symbol, t.side, t.lots, t.price_idr, t.fee_idr) == (
        "2026-10-13", None, "GOTO", "sell", 1, 26.0, 7.0)
    assert t.net_amount_idr == 2594 and t.broker_realised_pnl_idr == -110.56
    assert t.closes_order_id == "rebalance-GOTO-2026-10-13" and t.note == "by hand"
    assert t.notional_idr == 2600.0


def test_optional_fields_may_be_absent(tmp_path):
    bare = {k: v for k, v in GOOD.items()
            if k not in ("net_amount_idr", "broker_realised_pnl_idr", "closes_order_id")}
    (t,) = M.load_manual_trades(write(tmp_path, [bare]))
    assert t.net_amount_idr is None and t.broker_realised_pnl_idr is None and t.closes_order_id is None


def test_time_of_day_is_validated_when_given(tmp_path):
    assert load_one(tmp_path, time_wib="14:05")[0].time_wib == "14:05"
    with pytest.raises(M.ManualTradesError, match="time_wib"):
        load_one(tmp_path, time_wib="25:00")


def test_an_empty_trades_list_is_fine(tmp_path):
    assert M.load_manual_trades(write(tmp_path, [])) == []


def test_the_schema_key_is_ignored(tmp_path):
    assert M.load_manual_trades(write(tmp_path, [GOOD], _schema={"x": 1}))[0].symbol == "GOTO"


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"colour": "red"}, "unknown field"),
        ({"side": "short"}, "side"),
        ({"side": "BUY"}, "side"),
        ({"lots": 0}, "lots"),
        ({"lots": -1}, "lots"),
        ({"lots": 1.5}, "lots"),
        ({"lots": True}, "lots"),
        ({"price_idr": 0}, "price_idr"),
        ({"price_idr": -5}, "price_idr"),
        ({"price_idr": "26"}, "price_idr"),
        ({"fee_idr": -1}, "fee_idr"),
        ({"date": "2026-13-01"}, "date"),
        ({"date": "13/10/2026"}, "date"),
        ({"date": None}, "date"),
        ({"symbol": ""}, "symbol"),
        ({"symbol": "goto"}, "symbol"),
        ({"note": 5}, "note"),
        ({"closes_order_id": ""}, "closes_order_id"),
    ],
)
def test_bad_values_fail_with_a_clear_message(tmp_path, over, needle):
    with pytest.raises(M.ManualTradesError, match=needle) as e:
        load_one(tmp_path, **over)
    assert "trade #1" in str(e.value)


def test_a_missing_required_field_fails(tmp_path):
    bad = {k: v for k, v in GOOD.items() if k != "price_idr"}
    with pytest.raises(M.ManualTradesError, match="missing.*price_idr"):
        M.load_manual_trades(write(tmp_path, [bad]))


def test_malformed_json_and_wrong_shape_fail(tmp_path):
    p = tmp_path / "manual_trades.json"
    p.write_text("{not json")
    with pytest.raises(M.ManualTradesError, match="not valid JSON"):
        M.load_manual_trades(str(p))
    p.write_text(json.dumps([GOOD]))
    with pytest.raises(M.ManualTradesError, match="trades"):
        M.load_manual_trades(str(p))
    p.write_text(json.dumps({"trades": [], "extra": 1}))
    with pytest.raises(M.ManualTradesError, match="unknown"):
        M.load_manual_trades(str(p))


def test_the_checked_in_example_is_itself_valid():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_trades.example.json")
    (t,) = M.load_manual_trades(path)
    assert t.symbol == "GOTO" and t.side == "sell"
