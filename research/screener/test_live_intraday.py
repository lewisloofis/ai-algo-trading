"""
screener/test_live_intraday.py — Unit tests for LiveDataSource.intraday_history.

No network / no token: LiveDataSource._get is monkeypatched to return synthetic
multi-chunk intraday JSON payloads.  The chartbit intraday schema is reproduced
faithfully (newest-first rows, mixed str/int numeric types, pre-open + lunch +
session bars spanning > 90 days so the backward paging loop fires more than one
chunk).

Covered behaviours
------------------
- Paging issues correct from/to unix bounds and stitches > 1 chunk.
- Rows are deduplicated by unix_timestamp.
- Result is sorted ascending by datetime.
- Session filter removes pre-open (08:xx) and lunch (12:00–13:30) bars.
- net_foreign = foreign_buy - foreign_sell (NaN→0 when both absent).
- String-typed numerics (volume, unix_timestamp, frequency, foreign_*) coerce.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd
import pytest

from screener.screen_universe import (
    INTRADAY_MAX_WINDOW_DAYS,
    LiveDataSource,
)

# WIB is UTC+7.
_WIB_OFFSET_SECONDS = 7 * 3600


# ---------------------------------------------------------------------------
# Synthetic intraday row builder (chartbit schema)
# ---------------------------------------------------------------------------

def _unix_for(date: _dt.date, hour: int, minute: int) -> int:
    """Return the unix_timestamp (UTC seconds) for a WIB wall-clock time."""
    naive = _dt.datetime(date.year, date.month, date.day, hour, minute)
    # WIB wall-clock → UTC epoch seconds.
    epoch = _dt.datetime(1970, 1, 1)
    return int((naive - epoch).total_seconds()) - _WIB_OFFSET_SECONDS


def _row(
    date: _dt.date,
    hour: int,
    minute: int,
    *,
    symbol: str = "TEST",
    close: float = 100.0,
    with_foreign: bool = True,
) -> dict:
    """
    Build one chartbit intraday row with the documented (mixed-type) schema.

    Several numerics are STRING-typed on purpose ("volume", "unix_timestamp",
    "frequency", "foreign_*") to exercise pd.to_numeric coercion.
    """
    dt_str = f"{date.isoformat()} {hour:02d}:{minute:02d}:00"
    row: dict = {
        "close": close,
        "datetime": dt_str,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "volume": "800",                       # str
        "value": close * 800.0,
        "frequency": "1",                       # str
        "symbol": symbol,
        "unix_timestamp": str(_unix_for(date, hour, minute)),  # str
    }
    if with_foreign:
        row["foreign_buy"] = "500"             # str
        row["foreign_sell"] = "200"            # str → net_foreign = 300
    return row


def _day_rows(
    date: _dt.date,
    *,
    symbol: str = "TEST",
    with_foreign: bool = True,
) -> list[dict]:
    """
    Build one trading day of rows including pre-open, AM, lunch, and PM bars.

    Bars (WIB):
      08:45  pre-open  (must be filtered out)
      09:00  AM session
      11:59  AM session
      12:30  lunch     (must be filtered out)
      13:30  PM session
      16:15  PM session
    Rows are returned newest-first within the day (API arrives descending).
    """
    times = [(8, 45), (9, 0), (11, 59), (12, 30), (13, 30), (16, 15)]
    rows = [
        _row(date, h, m, symbol=symbol, with_foreign=with_foreign)
        for (h, m) in times
    ]
    # Newest-first.
    rows.sort(key=lambda r: int(r["unix_timestamp"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Mock _get that pages across two ~90-day chunks
# ---------------------------------------------------------------------------

class _PagingMock:
    """
    Callable replacement for LiveDataSource._get.

    Records the (from, to) bounds of each request and returns rows whose
    unix_timestamp falls inside [to, from].  The synthetic universe spans
    ~120 days so a 90-day max window forces a second page.
    """

    def __init__(self, all_rows: list[dict]) -> None:
        self._all = all_rows
        self.calls: list[tuple[int, int]] = []

    def __call__(self, url: str) -> dict:
        # Parse from=/to= out of the query string.
        from urllib.parse import urlparse, parse_qs

        q = parse_qs(urlparse(url).query)
        newer = int(q["from"][0])
        older = int(q["to"][0])
        self.calls.append((newer, older))
        rows = [
            r for r in self._all
            if older <= int(r["unix_timestamp"]) <= newer
        ]
        rows.sort(key=lambda r: int(r["unix_timestamp"]), reverse=True)
        return {"data": {"chartbit": rows}}


def _make_live_source() -> LiveDataSource:
    """Construct a LiveDataSource without needing a real token."""
    return LiveDataSource(bearer_token="fake-token-for-tests")


def _span_dates(n_days: int, end: _dt.date | None = None) -> list[_dt.date]:
    """
    Return n_days business dates ending at *end* (ascending).

    Defaults to ending "yesterday" so the synthetic series sits inside the
    intraday_history lookback window, which is anchored at the real wall clock.
    """
    if end is None:
        end = _dt.date.today() - _dt.timedelta(days=1)
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=n_days)
    return [d.date() for d in dates]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLiveIntradayHistory:
    def test_requires_token(self):
        """No SB_BEARER_TOKEN and no explicit token → EnvironmentError."""
        import os

        saved = os.environ.pop("SB_BEARER_TOKEN", None)
        try:
            with pytest.raises(EnvironmentError):
                LiveDataSource()
        finally:
            if saved is not None:
                os.environ["SB_BEARER_TOKEN"] = saved

    def test_paging_stitches_more_than_one_chunk(self, monkeypatch):
        """A > 90-day lookback must issue >= 2 paged requests and stitch them."""
        # ~120 calendar days of business days → forces a 2nd chunk at 90-day max.
        dates = _span_dates(85)  # ~120 cal days
        all_rows: list[dict] = []
        for d in dates:
            all_rows.extend(_day_rows(d))

        mock = _PagingMock(all_rows)
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", mock)

        df = ds.intraday_history("TEST", lookback_days=120)

        assert len(mock.calls) >= 2, (
            f"Expected paging (>=2 calls) for a 120-day lookback; got {len(mock.calls)}"
        )
        # Each chunk window must be <= the documented max.
        for newer, older in mock.calls:
            span_days = (newer - older) / 86400.0
            assert span_days <= INTRADAY_MAX_WINDOW_DAYS + 1, (
                f"chunk window {span_days:.1f}d exceeds max {INTRADAY_MAX_WINDOW_DAYS}d"
            )

    def test_second_chunk_bounds_walk_backward(self, monkeypatch):
        """Next chunk's `from` must walk strictly below the prior chunk's oldest bar."""
        dates = _span_dates(85)
        all_rows = [r for d in dates for r in _day_rows(d)]
        mock = _PagingMock(all_rows)
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", mock)

        ds.intraday_history("TEST", lookback_days=120)

        assert len(mock.calls) >= 2, "Expected a second page"
        # The loop sets next-chunk from = (oldest bar in prior chunk) - 1.
        first_newer, first_older = mock.calls[0]
        second_newer, _second_older = mock.calls[1]
        first_chunk_oldest_bar = min(
            int(r["unix_timestamp"])
            for r in all_rows
            if first_older <= int(r["unix_timestamp"]) <= first_newer
        )
        assert second_newer == first_chunk_oldest_bar - 1, (
            f"second chunk from={second_newer} must equal first chunk oldest bar "
            f"{first_chunk_oldest_bar} - 1"
        )
        assert second_newer < first_chunk_oldest_bar, "second chunk must walk backward"

    def test_dedupe_by_unix_timestamp(self, monkeypatch):
        """Overlapping chunks must not produce duplicate unix_timestamp rows."""
        dates = _span_dates(85)
        all_rows = [r for d in dates for r in _day_rows(d)]
        mock = _PagingMock(all_rows)
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", mock)

        df = ds.intraday_history("TEST", lookback_days=120)

        # No duplicate timestamps.
        assert df["datetime"].is_unique, "Duplicate datetime rows after stitching"

    def test_sorted_ascending(self, monkeypatch):
        """Result must be sorted ascending by datetime."""
        dates = _span_dates(40)
        all_rows = [r for d in dates for r in _day_rows(d)]
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", _PagingMock(all_rows))

        df = ds.intraday_history("TEST", lookback_days=90)

        ts = df["datetime"].tolist()
        assert ts == sorted(ts), "Rows are not sorted ascending by datetime"

    def test_session_filter_drops_preopen_and_lunch(self, monkeypatch):
        """Pre-open (08:45) and lunch (12:30) bars must be removed."""
        dates = _span_dates(40)
        all_rows = [r for d in dates for r in _day_rows(d)]
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", _PagingMock(all_rows))

        df = ds.intraday_history("TEST", lookback_days=90)

        minutes = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
        # No 08:45 (525) pre-open.
        assert not (minutes == 8 * 60 + 45).any(), "Pre-open bar leaked through"
        # No 12:30 (750) lunch bar.
        assert not (minutes == 12 * 60 + 30).any(), "Lunch bar leaked through"
        # 09:00, 11:59, 13:30, 16:15 must all survive.
        for want in (9 * 60, 11 * 60 + 59, 13 * 60 + 30, 16 * 60 + 15):
            assert (minutes == want).any(), f"Session bar {want} missing"

    def test_net_foreign_computed(self, monkeypatch):
        """net_foreign = foreign_buy - foreign_sell (500 - 200 = 300)."""
        dates = _span_dates(40)
        all_rows = [r for d in dates for r in _day_rows(d, with_foreign=True)]
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", _PagingMock(all_rows))

        df = ds.intraday_history("TEST", lookback_days=90)

        assert (df["net_foreign"] == 300.0).all(), "net_foreign != foreign_buy - foreign_sell"

    def test_net_foreign_fallback_zero_when_absent(self, monkeypatch):
        """When both foreign columns are absent, net_foreign falls back to 0."""
        dates = _span_dates(40)
        all_rows = [r for d in dates for r in _day_rows(d, with_foreign=False)]
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", _PagingMock(all_rows))

        df = ds.intraday_history("TEST", lookback_days=90)

        assert (df["net_foreign"] == 0.0).all(), "net_foreign should be 0 when foreign cols absent"

    def test_string_numerics_coerced(self, monkeypatch):
        """String-typed volume/value/etc must be coerced to numeric dtypes."""
        dates = _span_dates(40)
        all_rows = [r for d in dates for r in _day_rows(d)]
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", _PagingMock(all_rows))

        df = ds.intraday_history("TEST", lookback_days=90)

        for col in ("open", "high", "low", "close", "volume", "value", "net_foreign"):
            assert pd.api.types.is_numeric_dtype(df[col]), f"{col} not coerced to numeric"
        assert (df["volume"] == 800.0).all()

    def test_columns_contract(self, monkeypatch):
        """Returned columns must match the documented contract."""
        dates = _span_dates(40)
        all_rows = [r for d in dates for r in _day_rows(d)]
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", _PagingMock(all_rows))

        df = ds.intraday_history("TEST", lookback_days=90)

        expected = {
            "datetime", "date", "open", "high", "low",
            "close", "volume", "value", "net_foreign",
        }
        assert expected.issubset(set(df.columns)), (
            f"Missing columns: {expected - set(df.columns)}"
        )
        # datetime must be a real timestamp; date a date.
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])

    def test_empty_chunk_stops_paging(self, monkeypatch):
        """A chunk returning no rows must terminate the loop without error."""
        # Only ~20 business days of data but a 120-day lookback requested.
        dates = _span_dates(20)
        all_rows = [r for d in dates for r in _day_rows(d)]
        mock = _PagingMock(all_rows)
        ds = _make_live_source()
        monkeypatch.setattr(ds, "_get", mock)

        df = ds.intraday_history("TEST", lookback_days=120)

        # Must return the available data and not loop forever.
        assert len(df) > 0
        # All returned bars are session bars.
        minutes = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
        assert not (minutes == 8 * 60 + 45).any()
