"""universe_status.json: build from classification fakes, atomic write, tolerant load."""
from __future__ import annotations

import datetime
import json
import urllib.error

import pytest

from screener import universe_status as us
from screener.emitten import SUBSECTOR_DAY_TRADE, SUBSECTOR_TRADING_LIMIT, CompanyRecord, ClassificationError
from screener.sb_http import SBHttpError

AS_OF = datetime.date(2026, 10, 15)


def rec(symbol, tradeable=True, status="Active", notations=(), uma=False):
    return CompanyRecord(symbol, tradeable, status, tuple(notations), uma)


class FakeEmitten:
    def __init__(self, day_trade=(), trading_limit=()):
        keep = lambda r: r if isinstance(r, Exception) else tuple(r)  # noqa: E731
        self._rows = {SUBSECTOR_DAY_TRADE: keep(day_trade), SUBSECTOR_TRADING_LIMIT: keep(trading_limit)}
        self.calls: list[str] = []

    def fetch_classification(self, subsector_id):
        self.calls.append(subsector_id)
        row = self._rows[subsector_id]
        if isinstance(row, Exception):
            raise row
        return row


def _build(stored, emitten):
    return us.build_universe_status(stored, emitten, as_of=AS_OF)


def test_covers_stored_union_eligible_and_makes_exactly_two_calls():
    em = FakeEmitten([rec("BOTH"), rec("NEW"), rec("DT")], [rec("BOTH"), rec("NEW"), rec("TL", False)])
    st = _build(["OLD", "BOTH", "TL"], em)
    assert set(st.symbols) == {"OLD", "BOTH", "NEW", "TL"}  # DT: neither stored nor eligible
    assert sorted(em.calls) == sorted([SUBSECTOR_DAY_TRADE, SUBSECTOR_TRADING_LIMIT])
    assert st.symbols["BOTH"] == us.SymbolStatus(True, True, True, "Active", ())
    assert st.symbols["TL"].is_tradable is False and st.symbols["TL"].in_trading_limit
    assert not st.symbols["TL"].in_day_trade
    assert st.eligible == {"BOTH", "NEW"}


def test_stored_symbol_on_neither_list_is_null_not_false():
    st = _build(["OLD"], FakeEmitten([rec("A")], [rec("A")]))
    assert st.symbols["OLD"] == us.SymbolStatus(False, False, None, None, ())


def test_lists_disagree_false_wins_and_missing_flag_is_unknown():
    st = _build([], FakeEmitten([rec("A", True), rec("B", None), rec("C", True)],
                                [rec("A", False), rec("B", True), rec("C", True)]))
    assert st.symbols["A"].is_tradable is False
    assert st.symbols["B"].is_tradable is None
    assert st.symbols["C"].is_tradable is True
    assert st.not_tradable() == {"A"} and st.tradable() == {"C"}


def test_records_company_status_and_notations():
    st = _build([], FakeEmitten([rec("A", True, "Suspended", ["X", "E"])], [rec("A", True, "Active")]))
    assert st.symbols["A"].company_status == "Suspended" and st.symbols["A"].notations == ("X", "E")


def test_write_is_atomic_and_round_trips(tmp_path):
    st = _build(["A"], FakeEmitten([rec("A", notations=["X"])], [rec("A")]))
    path = us.write_universe_status(st, str(tmp_path))
    assert path.endswith("universe_status.json")
    assert [p.name for p in tmp_path.iterdir()] == ["universe_status.json"]
    doc = json.loads((tmp_path / "universe_status.json").read_text())
    assert doc["schema_version"] == 2 == us.SCHEMA_VERSION and doc["as_of"] == "2026-10-15"
    assert doc["symbols"]["A"] == {"in_day_trade": True, "in_trading_limit": True, "is_tradable": True,
                                   "company_status": "Active", "notations": ["X"]}
    assert us.load_universe_status(str(tmp_path)) == st


def test_load_missing_corrupt_or_other_schema_is_none(tmp_path):
    assert us.load_universe_status(str(tmp_path)) is None
    (tmp_path / "universe_status.json").write_text("{nope")
    assert us.load_universe_status(str(tmp_path)) is None
    (tmp_path / "universe_status.json").write_text(json.dumps(
        {"schema_version": 1, "as_of": "2026-10-15", "symbols": {}}))
    assert us.load_universe_status(str(tmp_path)) is None


@pytest.mark.parametrize("exc,reason", [
    (urllib.error.URLError("timed out"), "URLError (timed out)"),
    (SBHttpError(403, "https://x/y?z=1"), "403 Forbidden - possible WAF/User-Agent block (not a token problem)"),
    (ClassificationError("classification response is paginated (2 pages); pagination not implemented"),
     "classification response is paginated (2 pages); pagination not implemented"),
])
def test_emitten_failure_is_labelled_with_step_and_host(exc, reason):
    with pytest.raises(us.UniverseStatusError) as ei:
        _build([], FakeEmitten(exc, []))
    assert "emitten classification" in ei.value.where and "exodus" in ei.value.where
    assert str(ei.value) == reason
