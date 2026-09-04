"""pytest suite for lib/data_inventory.py.

Everything runs against tmp_path fixtures — synthetic parquet files and a
synthetic segments/ tree. No network I/O, and the real research/data/ store
is never touched.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from lib.data_inventory import (
    InventoryReport,
    SegmentSession,
    SourceCapability,
    SourceCoverage,
    SymbolCoverage,
    build_inventory,
    daily_bar_source_capability,
    scan_daily_store,
    scan_intraday_store,
    scan_segments,
    symbol_coverage,
)


def _write_bar_parquet(path: Path, dates: list[str], bars_per_day: int = 1) -> None:
    """Write a minimal intraday-bar parquet file with a `date` column.

    Each entry in `dates` gets `bars_per_day` rows, mimicking multiple
    1-minute bars per trading day.
    """
    rows = [d for d in dates for _ in range(bars_per_day)]
    table = pa.table({"date": pa.array([date.fromisoformat(d) for d in rows], type=pa.date32())})
    pq.write_table(table, path)


def _write_segment(
    wl_dir: Path,
    seg_name: str,
    watchlist_id: str,
    symbols: list[str],
    channels: list[str],
    total_data_records: int = 0,
    total_control_records: int = 0,
    start: str = "2026-06-22T01:31:02.230937Z",
    end: str = "2026-06-22T01:59:56.777814Z",
    write_manifest: bool = True,
    manifest_body: str | None = None,
) -> None:
    """Write a fake `<seg_name>.binpb.zst` + matching manifest under wl_dir.

    Pass `write_manifest=False` to simulate a segment with no manifest at
    all, or `manifest_body` to write deliberately malformed JSON.
    """
    wl_dir.mkdir(parents=True, exist_ok=True)
    (wl_dir / f"{seg_name}.binpb.zst").write_bytes(b"\x00")

    if not write_manifest:
        return

    if manifest_body is not None:
        (wl_dir / f"{seg_name}.manifest.json").write_text(manifest_body)
        return

    manifest = {
        "schema_version": 1,
        "collector_version": "0.1.0",
        "watchlist_id": watchlist_id,
        "symbols": symbols,
        "channels": channels,
        "segment_start_utc": start,
        "segment_end_utc": end,
        "total_data_records": total_data_records,
        "total_control_records": total_control_records,
    }
    (wl_dir / f"{seg_name}.manifest.json").write_text(json.dumps(manifest))


# ---------------------------------------------------------------------------
# scan_intraday_store
# ---------------------------------------------------------------------------

def test_scan_intraday_store_missing_dir_returns_empty(tmp_path: Path) -> None:
    result = scan_intraday_store(tmp_path / "does-not-exist")
    assert result == ()


def test_scan_intraday_store_empty_dir_returns_empty(tmp_path: Path) -> None:
    result = scan_intraday_store(tmp_path)
    assert result == ()


def test_scan_intraday_store_single_symbol(tmp_path: Path) -> None:
    _write_bar_parquet(
        tmp_path / "PTRO.parquet",
        ["2026-03-25", "2026-03-26", "2026-03-27"],
        bars_per_day=5,
    )

    result = scan_intraday_store(tmp_path)

    assert len(result) == 1
    cov = result[0]
    assert cov.symbol == "PTRO"
    assert cov.granularity == "1min_bar"
    assert cov.first_date == date(2026, 3, 25)
    assert cov.last_date == date(2026, 3, 27)
    assert cov.distinct_trading_days == 3
    assert cov.row_count == 15  # 3 days * 5 bars/day
    assert cov.source_file.endswith("PTRO.parquet")


def test_scan_intraday_store_distinct_day_counting_ignores_duplicate_rows(tmp_path: Path) -> None:
    # 108 bars on the same single day must count as 1 distinct trading day,
    # not 108 — this is the exact bug class the old hand-written prior masked.
    _write_bar_parquet(tmp_path / "BRPT.parquet", ["2026-06-08"] * 108)

    result = scan_intraday_store(tmp_path)

    assert result[0].distinct_trading_days == 1
    assert result[0].row_count == 108


def test_scan_intraday_store_mixed_ranges_across_symbols(tmp_path: Path) -> None:
    # Mirrors the real store: 4 core names with a longer history, 3 names
    # seeded later with a shorter one.
    _write_bar_parquet(tmp_path / "PTRO.parquet", ["2026-03-25", "2026-06-08", "2026-09-04"])
    _write_bar_parquet(tmp_path / "BREN.parquet", ["2026-06-08", "2026-09-04"])

    result = scan_intraday_store(tmp_path)
    by_symbol = {cov.symbol: cov for cov in result}

    assert by_symbol["PTRO"].distinct_trading_days == 3
    assert by_symbol["PTRO"].first_date == date(2026, 3, 25)
    assert by_symbol["BREN"].distinct_trading_days == 2
    assert by_symbol["BREN"].first_date == date(2026, 6, 8)
    assert by_symbol["BREN"].last_date == date(2026, 9, 4)


def test_scan_intraday_store_ignores_non_parquet_files(tmp_path: Path) -> None:
    _write_bar_parquet(tmp_path / "PTRO.parquet", ["2026-03-25"])
    (tmp_path / "notes.txt").write_text("not a parquet file")

    result = scan_intraday_store(tmp_path)

    assert len(result) == 1
    assert result[0].symbol == "PTRO"


# ---------------------------------------------------------------------------
# scan_segments
# ---------------------------------------------------------------------------

def test_scan_segments_missing_dir_returns_empty(tmp_path: Path) -> None:
    result = scan_segments(tmp_path / "does-not-exist")
    assert result == ()


def test_scan_segments_empty_dir_returns_empty(tmp_path: Path) -> None:
    result = scan_segments(tmp_path)
    assert result == ()


def test_scan_segments_one_session_per_date_watchlist_pair(tmp_path: Path) -> None:
    wl_dir = tmp_path / "dt=2026-06-22" / "plan10-survivors"
    _write_segment(
        wl_dir, "seg-000001", "plan10-survivors",
        symbols=["PTRO", "BREN"], channels=["runningTradeBatch", "orderBook"],
        total_data_records=758, total_control_records=5,
        start="2026-06-22T01:31:02.230937Z", end="2026-06-22T01:59:56.777814Z",
    )
    _write_segment(
        wl_dir, "seg-000002", "plan10-survivors",
        symbols=["PTRO", "BREN"], channels=["runningTradeBatch", "orderBook"],
        total_data_records=90569, total_control_records=3,
        start="2026-06-22T02:00:00.939474Z", end="2026-06-22T02:59:59.876293Z",
    )

    result = scan_segments(tmp_path)

    assert len(result) == 1
    session = result[0]
    assert session.date == date(2026, 6, 22)
    assert session.watchlist_id == "plan10-survivors"
    assert session.symbols == ("BREN", "PTRO")
    assert session.channels == ("orderBook", "runningTradeBatch")
    assert session.total_data_records == 758 + 90569
    assert session.total_control_records == 5 + 3
    assert session.manifest_count == 2
    assert session.session_start_utc == "2026-06-22T01:31:02.230937Z"
    assert session.session_end_utc == "2026-06-22T02:59:59.876293Z"
    assert session.unknown_coverage is False


def test_scan_segments_multiple_watchlists_are_independent(tmp_path: Path) -> None:
    day1 = tmp_path / "dt=2026-06-17"
    _write_segment(day1 / "default", "seg-000001", "default", symbols=["BUMI"], channels=["orderBook", "liveprice"])
    _write_segment(
        day1 / "lq45-mining", "seg-000001", "lq45-mining",
        symbols=["ADRO", "PTBA"], channels=["runningTradeBatch", "orderBook"],
    )

    result = scan_segments(tmp_path)
    by_watchlist = {s.watchlist_id: s for s in result}

    assert set(by_watchlist) == {"default", "lq45-mining"}
    assert by_watchlist["default"].symbols == ("BUMI",)
    assert by_watchlist["default"].channels == ("liveprice", "orderBook")
    assert by_watchlist["lq45-mining"].symbols == ("ADRO", "PTBA")


def test_scan_segments_multiple_dates_same_watchlist_are_separate_sessions(tmp_path: Path) -> None:
    _write_segment(tmp_path / "dt=2026-06-18" / "mining-sample", "seg-1", "mining-sample", symbols=["ITMG"], channels=["runningTradeBatch"])
    _write_segment(tmp_path / "dt=2026-06-19" / "mining-sample", "seg-1", "mining-sample", symbols=["ITMG"], channels=["runningTradeBatch"])

    result = scan_segments(tmp_path)

    assert len(result) == 2
    assert {s.date for s in result} == {date(2026, 6, 18), date(2026, 6, 19)}
    assert all(s.watchlist_id == "mining-sample" for s in result)


def test_scan_segments_skips_unparseable_date_dir(tmp_path: Path) -> None:
    _write_segment(tmp_path / "dt=not-a-date" / "default", "seg-1", "default", symbols=["X"], channels=["runningTradeBatch"])
    _write_segment(tmp_path / "dt=2026-06-17" / "default", "seg-1", "default", symbols=["Y"], channels=["runningTradeBatch"])

    result = scan_segments(tmp_path)

    assert len(result) == 1
    assert result[0].symbols == ("Y",)


def test_scan_segments_skips_non_dt_dirs_and_stray_files(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_text("")
    wl_dir = tmp_path / "dt=2026-06-17" / "default"
    _write_segment(wl_dir, "seg-1", "default", symbols=["Y"], channels=["runningTradeBatch"])
    (wl_dir / ".DS_Store").write_text("")

    result = scan_segments(tmp_path)

    assert len(result) == 1
    assert result[0].symbols == ("Y",)


def test_scan_segments_no_segment_files_is_unknown_coverage(tmp_path: Path) -> None:
    wl_dir = tmp_path / "dt=2026-06-17" / "empty-watchlist"
    wl_dir.mkdir(parents=True)

    result = scan_segments(tmp_path)

    assert len(result) == 1
    session = result[0]
    assert session.unknown_coverage is True
    assert session.symbols == ()
    assert session.manifest_count == 0


def test_scan_segments_missing_manifest_is_unknown_coverage(tmp_path: Path) -> None:
    wl_dir = tmp_path / "dt=2026-06-17" / "default"
    _write_segment(wl_dir, "seg-good", "default", symbols=["Y"], channels=["runningTradeBatch"], total_data_records=10)
    _write_segment(wl_dir, "seg-orphan", "default", symbols=["Z"], channels=["runningTradeBatch"], write_manifest=False)

    result = scan_segments(tmp_path)

    assert len(result) == 1
    session = result[0]
    assert session.unknown_coverage is True
    # Only the readable manifest's data counts toward the totals/symbols.
    assert session.symbols == ("Y",)
    assert session.total_data_records == 10
    assert session.manifest_count == 1


def test_scan_segments_malformed_manifest_is_unknown_coverage(tmp_path: Path) -> None:
    wl_dir = tmp_path / "dt=2026-06-17" / "default"
    _write_segment(wl_dir, "seg-good", "default", symbols=["Y"], channels=["runningTradeBatch"], total_data_records=42)
    _write_segment(wl_dir, "seg-bad", "default", symbols=[], channels=[], manifest_body="{not valid json")

    result = scan_segments(tmp_path)

    assert len(result) == 1
    session = result[0]
    assert session.unknown_coverage is True
    assert session.total_data_records == 42
    assert session.manifest_count == 1


# ---------------------------------------------------------------------------
# symbol_coverage — the channel-gating fix
# ---------------------------------------------------------------------------

def test_symbol_coverage_requires_the_named_channel() -> None:
    # The real bug this exists to catch: 2026-06-17 `default` captured BUMI
    # with orderBook/liveprice only — no runningTradeBatch. That day must
    # NOT count as trade coverage for BUMI.
    no_trades_session = SegmentSession(
        date=date(2026, 6, 17),
        watchlist_id="default",
        symbols=("BUMI",),
        channels=("orderBook", "liveprice"),
        session_start_utc=None,
        session_end_utc=None,
        total_data_records=100,
        total_control_records=1,
        manifest_count=1,
        unknown_coverage=False,
    )

    result = symbol_coverage([no_trades_session], required_channel="runningTradeBatch")

    assert result == ()


def test_symbol_coverage_counts_when_channel_present() -> None:
    session = SegmentSession(
        date=date(2026, 6, 22),
        watchlist_id="plan10-survivors",
        symbols=("PTRO", "BREN"),
        channels=("runningTradeBatch", "orderBook"),
        session_start_utc=None,
        session_end_utc=None,
        total_data_records=100,
        total_control_records=1,
        manifest_count=1,
        unknown_coverage=False,
    )

    result = symbol_coverage([session], required_channel="runningTradeBatch")
    by_symbol = {cov.symbol: cov for cov in result}

    assert set(by_symbol) == {"PTRO", "BREN"}
    assert by_symbol["PTRO"].dates == (date(2026, 6, 22),)
    assert by_symbol["PTRO"].distinct_trading_days == 1


def test_symbol_coverage_mixed_sessions_directory_scan_would_have_overcounted(tmp_path: Path) -> None:
    # PTRO appears on disk across 3 different watchlists' directories, but
    # only 2 of those sessions actually carry runningTradeBatch for PTRO —
    # a directory-name-only scan would have reported 3.
    _write_segment(
        tmp_path / "dt=2026-06-17" / "no-trades", "seg-1", "no-trades",
        symbols=["PTRO"], channels=["orderBook", "liveprice"],
    )
    _write_segment(
        tmp_path / "dt=2026-06-22" / "plan10-survivors", "seg-1", "plan10-survivors",
        symbols=["PTRO"], channels=["runningTradeBatch", "orderBook"],
    )
    _write_segment(
        tmp_path / "dt=2026-06-23" / "plan10-survivors", "seg-1", "plan10-survivors",
        symbols=["PTRO"], channels=["runningTradeBatch", "orderBook"],
    )

    sessions = scan_segments(tmp_path)
    coverage = symbol_coverage(sessions)
    by_symbol = {cov.symbol: cov for cov in coverage}

    assert by_symbol["PTRO"].distinct_trading_days == 2
    assert by_symbol["PTRO"].dates == (date(2026, 6, 22), date(2026, 6, 23))


def test_symbol_coverage_unknown_coverage_session_never_counts(tmp_path: Path) -> None:
    wl_dir = tmp_path / "dt=2026-06-17" / "default"
    _write_segment(wl_dir, "seg-orphan", "default", symbols=["X"], channels=["runningTradeBatch"], write_manifest=False)

    sessions = scan_segments(tmp_path)
    assert sessions[0].unknown_coverage is True

    coverage = symbol_coverage(sessions)
    assert coverage == ()


def test_symbol_coverage_default_required_channel_is_running_trade_batch() -> None:
    session = SegmentSession(
        date=date(2026, 6, 22),
        watchlist_id="plan10-survivors",
        symbols=("PTRO",),
        channels=("runningTradeBatch",),
        session_start_utc=None,
        session_end_utc=None,
        total_data_records=1,
        total_control_records=0,
        manifest_count=1,
        unknown_coverage=False,
    )
    assert symbol_coverage([session]) != ()  # uses the default parameter value


# ---------------------------------------------------------------------------
# scan_daily_store — no on-disk store exists yet; this must report that
# honestly rather than fabricate a number.
# ---------------------------------------------------------------------------

def test_scan_daily_store_missing_dir_returns_empty(tmp_path: Path) -> None:
    result = scan_daily_store(tmp_path / "does-not-exist")
    assert result == ()


def test_scan_daily_store_empty_dir_returns_empty(tmp_path: Path) -> None:
    result = scan_daily_store(tmp_path)
    assert result == ()


def test_scan_daily_store_reads_real_files_if_they_ever_appear(tmp_path: Path) -> None:
    # Not the current state of the repo, but scan_daily_store must be ready
    # to report real coverage the moment a daily-bar store is added.
    _write_bar_parquet(tmp_path / "PTRO.parquet", ["2025-01-02", "2026-09-04"])

    result = scan_daily_store(tmp_path)

    assert len(result) == 1
    assert result[0].symbol == "PTRO"
    assert result[0].granularity == "daily_bar"
    assert result[0].distinct_trading_days == 2


# ---------------------------------------------------------------------------
# daily_bar_source_capability — a fetchable-from-source fact, deliberately
# separate from on-disk coverage (correction: this must NOT be derived from
# the intraday store, which is governed by a different, much shorter limit).
# ---------------------------------------------------------------------------

def test_daily_bar_source_capability_is_not_a_source_coverage() -> None:
    cap = daily_bar_source_capability(today=date(2026, 9, 4))
    assert isinstance(cap, SourceCapability)
    assert cap.source == "chartbit_daily"
    assert cap.granularity == "daily_bar"


def test_daily_bar_source_capability_uses_the_real_lookback_constant() -> None:
    from screener.screen_universe import DEFAULT_LOOKBACK_DAYS

    cap = daily_bar_source_capability(today=date(2026, 9, 4))
    assert cap.lookback_calendar_days == DEFAULT_LOOKBACK_DAYS


def test_daily_bar_source_capability_estimated_trading_days_uses_real_calendar() -> None:
    from lib.market_calendar import CALENDAR
    from screener.screen_universe import DEFAULT_LOOKBACK_DAYS

    today = date(2026, 9, 4)
    cap = daily_bar_source_capability(today=today)

    expected = CALENDAR.trading_days_between(today - timedelta(days=DEFAULT_LOOKBACK_DAYS), today)
    assert cap.estimated_trading_days == expected
    # Roughly a year's worth of trading days, NOT the ~62-108 day depth of
    # the on-disk intraday store — that was the exact bug this replaces.
    assert cap.estimated_trading_days > 200


def test_daily_bar_source_capability_defaults_to_today() -> None:
    # Just needs to not raise and to return a plausible value when `today`
    # is omitted.
    cap = daily_bar_source_capability()
    assert cap.estimated_trading_days > 0


# ---------------------------------------------------------------------------
# build_inventory / staleness_days
# ---------------------------------------------------------------------------

def test_build_inventory_combines_both_sources(tmp_path: Path) -> None:
    intraday_dir = tmp_path / "intraday"
    intraday_dir.mkdir()
    _write_bar_parquet(intraday_dir / "PTRO.parquet", ["2026-09-01", "2026-09-04"])

    segments_dir = tmp_path / "segments"
    _write_segment(
        segments_dir / "dt=2026-09-04" / "plan10-survivors", "seg-1", "plan10-survivors",
        symbols=["PTRO"], channels=["runningTradeBatch"], total_data_records=500,
    )

    report = build_inventory(intraday_store_dir=intraday_dir, segments_root=segments_dir)

    assert len(report.intraday_bars) == 1
    assert len(report.tick_segments) == 1
    assert report.symbol_trade_coverage()[0].symbol == "PTRO"


def test_build_inventory_missing_dirs_returns_empty_report(tmp_path: Path) -> None:
    report = build_inventory(
        intraday_store_dir=tmp_path / "no-intraday",
        segments_root=tmp_path / "no-segments",
        daily_store_dir=tmp_path / "no-daily",
    )
    # On-disk coverage is empty for every store...
    assert report.intraday_bars == ()
    assert report.tick_segments == ()
    assert report.daily_bars_on_disk == ()
    # ...but daily_bar_capability is a SOURCE fact, independent of any of
    # these directories existing, so it is still populated.
    assert report.daily_bar_capability is not None
    assert report.daily_bar_capability.source == "chartbit_daily"


def test_staleness_days_computes_days_since_last_date() -> None:
    report = InventoryReport(
        intraday_bars=(
            SourceCoverage(
                symbol="PTRO",
                granularity="1min_bar",
                first_date=date(2026, 3, 25),
                last_date=date(2026, 9, 4),
                distinct_trading_days=108,
                row_count=1000,
                source_file="PTRO.parquet",
            ),
        ),
    )

    result = report.staleness_days(today=date(2026, 9, 11))

    assert result == {"PTRO": 7}


def test_staleness_days_omits_sources_with_no_data() -> None:
    report = InventoryReport(
        intraday_bars=(
            SourceCoverage(
                symbol="EMPTY",
                granularity="1min_bar",
                first_date=None,
                last_date=None,
                distinct_trading_days=0,
                row_count=0,
                source_file="EMPTY.parquet",
            ),
        ),
    )

    result = report.staleness_days(today=date(2026, 9, 11))

    assert result == {}


def test_staleness_days_zero_when_fresh_today() -> None:
    report = InventoryReport(
        intraday_bars=(
            SourceCoverage(
                symbol="PTRO",
                granularity="1min_bar",
                first_date=date(2026, 6, 22),
                last_date=date(2026, 9, 4),
                distinct_trading_days=8,
                row_count=999,
                source_file="PTRO.parquet",
            ),
        ),
    )

    result = report.staleness_days(today=date(2026, 9, 4))

    assert result == {"PTRO": 0}
