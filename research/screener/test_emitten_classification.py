"""emitten classification endpoint: parse, pagination guard, and the symbol-set wrappers."""
from __future__ import annotations

import pytest

from screener import emitten as em
from screener.sb_http import SBHttpError


def _co(symbol, tradeable=1, **extra):
    row = {"symbol": symbol, "name": symbol.title(), "company_id": "1", "company_status": "Active",
           "notations": [], "uma": False, "last": "100", "avgvolume": "5"}
    if tradeable is not None:
        row["tradeable"] = tradeable
    return {**row, **extra}


def _body(*groups, total_page=1, total_data=None):
    return {"data": [
        {"id": 1, "level": 3, "companies": g,
         "pagination": {"next_page": 0, "prev_page": 0, "current_page": 1, "total_page": total_page,
                        "data_per_page": 500, "total_data_in_page": len(g),
                        "total_data": len(g) if total_data is None else total_data}}
        for g in groups], "message": "ok"}


def _client(monkeypatch, body, urls=None):
    client = em.EmittenClient("x")

    def fake_get(url):
        if urls is not None:
            urls.append(url)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(client, "_get", fake_get)
    return client


def test_parse_maps_fields_and_flattens_groups():
    recs = em.parse_classification(_body(
        [_co("aali", 1, notations=["X"], uma=True)], [_co("BBCA", 0, company_status="Suspended")]))
    assert recs[0] == em.CompanyRecord("AALI", True, "Active", ("X",), True)
    assert recs[1].symbol == "BBCA" and recs[1].tradeable is False and recs[1].company_status == "Suspended"


@pytest.mark.parametrize("raw", [None, 2, "1", True, "yes"])
def test_tradeable_missing_or_not_0_1_is_none(raw):
    rec = em.parse_classification(_body([_co("AALI", raw)]))[0]
    assert rec.tradeable is None


def test_rows_without_symbol_are_skipped():
    assert [r.symbol for r in em.parse_classification(_body([_co(""), _co("AALI")]))] == ["AALI"]


def test_multiple_pages_raises_clearly():
    with pytest.raises(em.ClassificationError, match=r"paginated \(3 pages\); pagination not implemented"):
        em.parse_classification(_body([_co("A")], total_page=3))


def test_total_data_mismatch_raises():
    with pytest.raises(em.ClassificationError, match="pagination not implemented"):
        em.parse_classification(_body([_co("A")], total_data=200))


def test_missing_pagination_is_accepted():
    body = {"data": [{"companies": [_co("A")]}]}
    assert [r.symbol for r in em.parse_classification(body)] == ["A"]


def test_fetch_classification_uses_one_id_and_parent_88(monkeypatch):
    urls: list[str] = []
    client = _client(monkeypatch, _body([_co("A")]), urls)
    assert [r.symbol for r in client.fetch_classification(em.SUBSECTOR_TRADING_LIMIT)] == ["A"]
    assert urls == [f"{em.CLASSIFICATION_URL}?ids=1000005139&parent_id=88"]


def test_fetch_subsector_is_the_symbol_set_including_untradeable(monkeypatch):
    client = _client(monkeypatch, _body([_co("A"), _co("B", 0)]))
    assert client.fetch_day_trade() == {"A", "B"}
    assert client.fetch_trading_limit() == {"A", "B"}


def test_http_failure_and_pagination_raise_through_fetch(monkeypatch):
    with pytest.raises(SBHttpError):
        _client(monkeypatch, SBHttpError(500, "https://x/y")).fetch_day_trade()
    with pytest.raises(em.ClassificationError):
        _client(monkeypatch, _body([_co("A")], total_page=2)).fetch_trading_limit()


@pytest.mark.parametrize("flags,want", [
    ([True, True], True), ([True, False], False), ([None, False], False),
    ([True, None], None), ([None], None), ([False], False),
])
def test_merge_tradeable_false_wins_unknown_is_not_true(flags, want):
    assert em.merge_tradeable(flags) is want
