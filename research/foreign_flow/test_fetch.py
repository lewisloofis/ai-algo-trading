"""Tests for foreign_flow.fetch: resume, abort, retry, atomic write. Fake client only."""
import datetime as dt
import io
import json
import urllib.error

import pandas as pd
import pytest

from foreign_flow import client as cl
from foreign_flow import fetch as fx
from screener.sb_http import SBHttpError

TOKEN = "FAKE-TOKEN-for-tests-0000"
TODAY = dt.date(2026, 10, 17)
URL = "https://exodus.stockbit.com/x?symbols=A"


def make_frame(date, net=100.0):
    return cl.normalise(pd.DataFrame([{
        "date": date, "net_foreign": net, "foreign_buy": net + 5, "foreign_sell": 5.0,
        "foreign_flow": 1.0, "net_lot": 1.0, "net_frequency": 3, "average_price": 10.0,
        "percentage_foreign_value": 1.0, "percentage_domestic_value": 99.0, "sb_close": 10.0}]))


class FakeClient:
    """Stands in for ForeignFlowClient; records (symbol, from, to); script maps (sym, year) -> action."""

    def __init__(self, script=None):
        self.calls, self.script = [], script or {}

    def fetch(self, symbol, date_from, date_to):
        self.calls.append((symbol, date_from, date_to))
        year = int(date_from[:4])
        action = self.script.get((symbol, year))
        if isinstance(action, list):
            action = action.pop(0) if action else None
        if isinstance(action, BaseException):
            raise action
        if action == "empty":
            return make_frame("2000-01-01").iloc[0:0]
        return make_frame(f"{year}-03-02")


def do_run(tmp_path, client, symbols=("A",), start=2024, end=2026, out=None, **kw):
    return fx.run(client, list(symbols), start_year=start, end_year=end, data_dir=tmp_path,
                  manifest_path=tmp_path / "_manifest.json", sleep_s=0.0, today=TODAY,
                  sleep=lambda s: None, out=out or io.StringIO(), **kw)


def test_one_call_per_year_with_calendar_year_ranges(tmp_path):
    c = FakeClient()
    do_run(tmp_path, c)
    assert c.calls == [("A", "2024-01-01", "2024-12-31"), ("A", "2025-01-01", "2025-12-31"),
                       ("A", "2026-01-01", "2026-10-17")]   # current year ends today


def test_resume_skips_completed_past_years_but_refetches_current(tmp_path):
    do_run(tmp_path, FakeClient())
    c2 = FakeClient()
    do_run(tmp_path, c2)
    assert c2.calls == [("A", "2026-01-01", "2026-10-17")]
    m = json.loads((tmp_path / "_manifest.json").read_text())
    assert m["A"] == {"2024": 1, "2025": 1, "2026": 1}


def test_empty_year_recorded_complete_with_zero_rows(tmp_path):
    do_run(tmp_path, FakeClient({("A", 2024): "empty"}))
    assert json.loads((tmp_path / "_manifest.json").read_text())["A"]["2024"] == 0
    assert fx.plan_calls(["A"], 2024, 2025, fx.load_manifest(tmp_path / "_manifest.json"), TODAY) == []


def test_parquet_sorted_deduped_across_reruns(tmp_path):
    do_run(tmp_path, FakeClient())
    do_run(tmp_path, FakeClient())   # current year refetched: same date must not duplicate
    df = pd.read_parquet(tmp_path / "A.parquet")
    assert list(df["date"]) == sorted(set(df["date"])) and len(df) == 3


def test_401_stops_run_and_keeps_manifest(tmp_path):
    c = FakeClient({("A", 2025): SBHttpError(401, URL)})
    out = io.StringIO()
    s = do_run(tmp_path, c, symbols=("A", "B"), out=out)
    assert s["aborted"] and "token expired" in s["aborted"] and "progress is saved" in s["aborted"]
    assert [x[0] for x in c.calls] == ["A", "A"]               # B never attempted
    assert json.loads((tmp_path / "_manifest.json").read_text()) == {"A": {"2024": 1}}
    assert (tmp_path / "A.parquet").exists()


def test_403_stops_with_waf_message(tmp_path):
    s = do_run(tmp_path, FakeClient({("A", 2024): SBHttpError(403, URL)}))
    assert "WAF" in s["aborted"]
    assert not (tmp_path / "_manifest.json").exists()


def test_retry_on_5xx_then_success(tmp_path):
    c = FakeClient({("A", 2024): [SBHttpError(503, URL), urllib.error.URLError("boom"), None]})
    sleeps = []
    s = fx.run(c, ["A"], start_year=2024, end_year=2024, data_dir=tmp_path,
               manifest_path=tmp_path / "_manifest.json", sleep_s=0, today=TODAY,
               sleep=sleeps.append, out=io.StringIO())
    assert s["fetched"] == 1 and not s["failures"]
    assert len(c.calls) == 3 and sleeps == [2.0, 4.0]          # exponential backoff


def test_retry_gives_up_records_failure_and_continues(tmp_path):
    c = FakeClient({("A", 2024): [SBHttpError(500, URL)] * 10})
    s = do_run(tmp_path, c, start=2024, end=2025)
    assert len(s["failures"]) == 1 and "2024" in s["failures"][0]
    assert s["fetched"] == 1
    assert "2024" not in json.loads((tmp_path / "_manifest.json").read_text())["A"]


def test_non_transient_4xx_not_retried(tmp_path):
    c = FakeClient({("A", 2024): SBHttpError(400, URL)})
    s = do_run(tmp_path, c, start=2024, end=2024)
    assert len(c.calls) == 1 and len(s["failures"]) == 1


def test_atomic_write_leaves_no_tmp_and_keeps_old_file_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "A.parquet"
    fx.merge_symbol_frame(path, make_frame("2024-03-02"))
    assert not list(tmp_path.glob("*.tmp"))
    before = path.read_bytes()

    def boom(self, p, *a, **k):
        p.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", boom)
    with pytest.raises(OSError):
        fx.merge_symbol_frame(path, make_frame("2024-03-03"))
    assert path.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


def test_manifest_not_updated_when_parquet_write_fails(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fx, "merge_symbol_frame", boom)
    with pytest.raises(OSError):
        do_run(tmp_path, FakeClient())
    assert not (tmp_path / "_manifest.json").exists()


def test_token_never_printed(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SB_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(fx, "ForeignFlowClient", lambda tok: FakeClient({("A", 2025): SBHttpError(401, URL)}))
    monkeypatch.setattr(fx, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fx, "MANIFEST_PATH", tmp_path / "_manifest.json")
    rc = fx.main(["--symbols", "A", "--start-year", "2024", "--end-year", "2026", "--sleep", "0"])
    cap = capsys.readouterr()
    assert rc == 1 and TOKEN not in cap.out + cap.err and "token expired" in cap.err
    assert (tmp_path / "A.parquet").exists()             # main() honours the patched paths, not real data


def test_missing_token_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("SB_ACCESS_TOKEN", raising=False)
    assert fx.main(["--symbols", "A"]) == 2
    assert "source ../.env.local" in capsys.readouterr().err


def test_dry_run_no_network_no_token(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("SB_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(fx, "MANIFEST_PATH", tmp_path / "_manifest.json")
    monkeypatch.setattr(fx, "ForeignFlowClient", lambda tok: pytest.fail("must not build a client"))
    assert fx.main(["--symbols", "A,B", "--start-year", "2024", "--end-year", "2025", "--dry-run"]) == 0
    assert "4 calls planned" in capsys.readouterr().out
