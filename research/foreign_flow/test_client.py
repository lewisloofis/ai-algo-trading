"""Tests for foreign_flow.client: parsing and request construction. No network."""
import json
import math
import urllib.parse

import pandas as pd
import pytest

from foreign_flow import client as cl

FAKE_TOKEN = "FAKE-TOKEN-for-tests-0000"


def field(raw):
    return {"raw": raw, "formatted": "x"}


def row(date="2026-10-16", **over):
    base = {
        "date": date,
        "net_foreign": field(-868013132000), "foreign_buy": field(718861485000),
        "foreign_sell": field(1586874617000), "foreign_flow": field(-12013783232000),
        "net_lot": field(-2449181), "net_frequency": field("-62441"),
        "average_price": field(3530), "percentage_foreign_value": field(41.44),
        "percentage_domestic_value": field(58.56),
    }
    base.update(over)
    return base


def body(rows, prices=None):
    return {"data": {"historical_net": rows, "historical_price": prices or []}}


def test_parses_row_casts_string_frequency_and_joins_close():
    price = [{"date": "2026-10-16", "open": field(1), "high": field(2), "low": field(1), "close": field(3390)}]
    df = cl.parse_response(body([row()], price))
    r = df.iloc[0]
    assert r["date"] == pd.Timestamp("2026-10-16")
    assert r["net_foreign"] == -868013132000
    assert r["net_frequency"] == -62441 and str(df["net_frequency"].dtype) == "Int64"
    assert r["sb_close"] == 3390
    assert list(df.columns) == cl.COLUMNS


def test_missing_fields_become_nan_and_never_crash():
    df = cl.parse_response(body([{"date": "2026-10-15"}, row("2026-10-14", net_frequency=None,
                                                             foreign_buy={"formatted": "?"})]))
    assert len(df) == 2
    assert math.isnan(df.iloc[0]["net_foreign"]) and pd.isna(df.iloc[0]["net_frequency"])
    assert math.isnan(df.iloc[1]["foreign_buy"]) and math.isnan(df.iloc[1]["sb_close"])


def test_row_without_date_dropped_and_garbage_body_is_empty():
    assert len(cl.parse_response(body([{"net_foreign": field(1)}]))) == 0
    for bad in ({}, {"data": None}, [], {"data": {"historical_net": None}}):
        assert cl.parse_response(bad).empty


def test_empty_year_is_no_data_not_error():
    df = cl.parse_response(body([]))
    assert df.empty and list(df.columns) == cl.COLUMNS


def test_request_url_one_calendar_year(monkeypatch):
    monkeypatch.delenv("SB_API_HOST", raising=False)
    url = cl.request_url("BBCA", "2024-01-01", "2024-12-31")
    parts = urllib.parse.urlsplit(url)
    assert parts.netloc == "exodus.stockbit.com"
    assert parts.path == "/order-trade/foreign-domestic/historical"
    assert dict(urllib.parse.parse_qsl(parts.query)) == {
        "symbols": "BBCA", "market_type": "MARKET_TYPE_REGULER",
        "from": "2024-01-01", "to": "2024-12-31"}


def test_host_override(monkeypatch):
    monkeypatch.setenv("SB_API_HOST", "example.invalid")
    assert cl.request_url("A", "2024-01-01", "2024-12-31").startswith("https://example.invalid/")


def test_token_only_in_authorization_header_and_not_in_output(capsys, caplog):
    seen = {}

    def fake(url, headers):
        seen["url"], seen["headers"] = url, headers
        return json.dumps(body([row()])).encode()

    c = cl.ForeignFlowClient(FAKE_TOKEN, http_get=fake)
    c.fetch("BBCA", "2024-01-01", "2024-12-31")
    assert seen["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert FAKE_TOKEN not in seen["url"]
    assert not [v for k, v in seen["headers"].items() if k != "Authorization" and FAKE_TOKEN in str(v)]
    assert FAKE_TOKEN not in repr(c)
    out = capsys.readouterr()
    assert FAKE_TOKEN not in out.out + out.err + caplog.text


def test_non_json_raises_without_body_or_token():
    c = cl.ForeignFlowClient(FAKE_TOKEN, http_get=lambda u, h: b"<html>secret</html>")
    with pytest.raises(ValueError) as ei:
        c.fetch("BBCA", "2024-01-01", "2024-12-31")
    assert "secret" not in str(ei.value) and FAKE_TOKEN not in str(ei.value)


def test_empty_token_rejected():
    with pytest.raises(ValueError):
        cl.ForeignFlowClient("", http_get=lambda u, h: b"{}")
