"""The daily store never persists the current session's in-progress bar (volume 0 at
the open, close = prior close; partial bar intraday) and heals one already stored."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pandas as pd

from screener import daily_store as ds

WIB = ds.WIB


def _bar(day: str, close: float, volume: int) -> dict:
    return {"date": day, "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "value": close * volume, "net_foreign": 0}


def _source(rows: list[dict]) -> MagicMock:
    src = MagicMock()
    src.daily_history.side_effect = lambda symbol, **kw: pd.DataFrame(rows)
    return src


def _at(hhmm: str, day: str = "2026-10-15") -> datetime.datetime:
    h, m = map(int, hhmm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return datetime.datetime(y, mo, d, h, m, tzinfo=WIB)


def _stored_dates(tmp_path, sym="WIFI") -> list[str]:
    return [str(d) for d in pd.read_parquet(tmp_path / f"{sym}.parquet")["date"]]


ROWS = [_bar("2026-10-13", 1330, 19_525_900), _bar("2026-10-14", 1370, 27_561_000),
        _bar("2026-10-15", 1370, 0)]          # the placeholder: volume 0, close = prior close


def test_open_market_placeholder_is_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("10:41"))
    summaries = ds.snapshot_daily(["WIFI"], str(tmp_path), _source(ROWS), start="2026-10-01", end="2026-10-15")
    assert _stored_dates(tmp_path) == ["2026-10-13", "2026-10-14"]
    assert summaries["WIFI"].date_range[1] == "2026-10-14"


def test_partial_intraday_bar_with_volume_is_not_persisted_either(tmp_path, monkeypatch):
    rows = ROWS[:2] + [_bar("2026-10-15", 1385, 4_000_000)]
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("13:00"))
    ds.snapshot_daily(["WIFI"], str(tmp_path), _source(rows), start="2026-10-01", end="2026-10-15")
    assert _stored_dates(tmp_path)[-1] == "2026-10-14"


def test_final_bar_is_persisted_after_the_session_is_final(tmp_path, monkeypatch):
    rows = ROWS[:2] + [_bar("2026-10-15", 1385, 30_000_000)]
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("16:20"))
    ds.snapshot_daily(["WIFI"], str(tmp_path), _source(rows), start="2026-10-01", end="2026-10-15")
    assert _stored_dates(tmp_path)[-1] == "2026-10-15"


def test_not_final_just_before_the_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("16:14"))
    ds.snapshot_daily(["WIFI"], str(tmp_path), _source(ROWS), start="2026-10-01", end="2026-10-15")
    assert _stored_dates(tmp_path)[-1] == "2026-10-14"


def test_future_dated_bar_is_never_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("20:00"))
    rows = ROWS[:2] + [_bar("2026-10-16", 1370, 0)]
    ds.snapshot_daily(["WIFI"], str(tmp_path), _source(rows), start="2026-10-01", end="2026-10-16")
    assert _stored_dates(tmp_path) == ["2026-10-13", "2026-10-14"]


def test_a_placeholder_already_in_the_store_is_healed_by_the_next_write(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("16:30"))
    pd.DataFrame(ROWS).assign(date=lambda d: pd.to_datetime(d["date"]).dt.date).to_parquet(tmp_path / "WIFI.parquet")
    ds.snapshot_daily(["WIFI"], str(tmp_path), _source(ROWS[:2] + [_bar("2026-10-15", 1385, 30_000_000)]),
                      start="2026-10-01", end="2026-10-15")
    got = pd.read_parquet(tmp_path / "WIFI.parquet")
    last = got.iloc[-1]
    assert str(last["date"]) == "2026-10-15" and last["volume"] == 30_000_000 and last["close"] == 1385


def test_past_zero_volume_bars_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "_now_wib", lambda: _at("16:30", "2026-10-16"))
    rows = [_bar("2026-10-13", 1330, 0), _bar("2026-10-14", 1330, 0)]      # a suspended name
    ds.snapshot_daily(["WIFI"], str(tmp_path), _source(rows), start="2026-10-01", end="2026-10-14")
    assert _stored_dates(tmp_path) == ["2026-10-13", "2026-10-14"]


def test_drop_incomplete_session_is_pure(monkeypatch):
    df = pd.DataFrame([{"date": datetime.date(2026, 10, 15), "close": 1.0, "volume": 0}])
    out = ds.drop_incomplete_session(df, _at("09:05"))
    assert len(df) == 1 and out.empty
