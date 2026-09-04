"""
lib/data_inventory.py — filesystem-derived inventory of real market data.

Built for plan 12 §8.4 / roadmap W1.2. Scans the on-disk stores this repo
actually has and reports what is *there*, not what a plan once assumed would
be there:

  - the intraday 1-minute bar store: research/data/intraday/<SYMBOL>.parquet
  - captured tick segments: segments/dt=YYYY-MM-DD/<watchlist_id>/*.manifest.json
  - a daily-bar store: research/data/daily/<SYMBOL>.parquet — does not exist
    yet (`scan_daily_store` reports the honest `()` today), so this module
    also exposes `daily_bar_source_capability`, a SEPARATE fact about what
    the chartbit daily endpoint can supply on demand. On-disk coverage and
    fetchable-from-source capability are never merged into one number —
    they are governed by different limits (see `SourceCapability`) and are
    reported as distinct types.

No network I/O. Segment coverage is read from the small JSON manifests
written alongside each capture — never from decoding the captured protobuf
payloads, which would be far too slow to run as a routine check.

A directory name alone is NOT proof of coverage: a watchlist can be on disk
for a given date while subscribed to channels that carry no trade data at
all (see `symbol_coverage`'s docstring for the real example that motivated
this). Every fact this module reports about *what a session actually
contains* — symbols, channels, record counts — is read from its manifests,
and a missing or unreadable manifest is recorded as unknown coverage, with a
logged warning, rather than silently treated as either "covered" or
"absent". A missing directory is not an error: it means zero coverage from
that source, so every scan function returns an empty result rather than
raising.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_RESEARCH_ROOT: Path = Path(__file__).resolve().parent.parent
_REPO_ROOT: Path = _RESEARCH_ROOT.parent

DEFAULT_INTRADAY_STORE_DIR: Path = _RESEARCH_ROOT / "data" / "intraday"
DEFAULT_DAILY_STORE_DIR: Path = _RESEARCH_ROOT / "data" / "daily"
DEFAULT_SEGMENTS_ROOT: Path = _REPO_ROOT / "segments"

_SEGMENT_SUFFIX = ".binpb.zst"
_MANIFEST_SUFFIX = ".manifest.json"

# The channel `sbcap`/the bars builder actually needs. A watchlist_id can be
# on disk for a date while subscribed only to e.g. orderBook/liveprice —
# that date carries zero trade data for every symbol in it.
DEFAULT_REQUIRED_CHANNEL = "runningTradeBatch"


@dataclass(frozen=True)
class SourceCoverage:
    """Coverage of one intraday-bar symbol.

    ``symbol`` is the ticker and ``source_file`` is the parquet file it was
    read from.
    """

    symbol: str
    granularity: str
    first_date: Optional[date]
    last_date: Optional[date]
    distinct_trading_days: int
    row_count: int
    source_file: str


@dataclass(frozen=True)
class SegmentSession:
    """One capture session: every segment under one ``dt=.../<watchlist_id>/``.

    Built entirely from that directory's manifest files, never from decoding
    the captured protobuf. ``symbols`` and ``channels`` are the union across
    every manifest found; ``session_start_utc``/``session_end_utc`` are the
    earliest start and latest end; the two record counts are summed.

    ``unknown_coverage`` is True if any segment in the directory had a
    missing or unparseable manifest — in that case the numeric fields are a
    floor (from whatever manifests *could* be read), not a confirmed total.
    A directory with zero segment files at all is also unknown coverage,
    with every field empty/zero.
    """

    date: date
    watchlist_id: str
    symbols: tuple[str, ...]
    channels: tuple[str, ...]
    session_start_utc: Optional[str]
    session_end_utc: Optional[str]
    total_data_records: int
    total_control_records: int
    manifest_count: int
    unknown_coverage: bool


@dataclass(frozen=True)
class SymbolCoverage:
    """Per-symbol trading-day coverage derived from `SegmentSession` records.

    A session's date counts toward a symbol only if the symbol appears in
    that session's ``symbols`` AND ``required_channel`` appears in that
    session's ``channels``. This is the fix for a real miscount: the
    2026-06-17 ``default`` watchlist captured BUMI with channels
    ``orderBook``/``liveprice`` only — no ``runningTradeBatch`` — so that day
    is on disk but carries zero trade data for BUMI. Directory-name-only
    scanning cannot see this; reading the manifest's ``channels`` can.
    """

    symbol: str
    required_channel: str
    dates: tuple[date, ...]
    first_date: Optional[date]
    last_date: Optional[date]
    distinct_trading_days: int


@dataclass(frozen=True)
class SourceCapability:
    """What a live data SOURCE can supply on demand — not what is on disk.

    Deliberately a different type from `SourceCoverage`: on-disk coverage
    and fetchable-from-source capability are different facts, governed by
    different limits (e.g. the chartbit daily endpoint takes `limit=0` for
    its whole lookback window in one call; the intraday endpoint pages in
    <=90-day chunks and is why the on-disk intraday store is shallow). This
    module never collapses the two into one number.
    """

    source: str
    granularity: str
    lookback_calendar_days: int
    estimated_trading_days: int
    basis: str


@dataclass(frozen=True)
class InventoryReport:
    """Aggregate result of a full inventory scan across all known sources."""

    intraday_bars: tuple[SourceCoverage, ...] = ()
    tick_segments: tuple[SegmentSession, ...] = ()
    daily_bars_on_disk: tuple[SourceCoverage, ...] = ()
    daily_bar_capability: Optional[SourceCapability] = None

    def symbol_trade_coverage(
        self, required_channel: str = DEFAULT_REQUIRED_CHANNEL
    ) -> tuple[SymbolCoverage, ...]:
        """Per-symbol coverage from `tick_segments`, gated on a channel."""
        return symbol_coverage(self.tick_segments, required_channel=required_channel)

    def staleness_days(self, today: date) -> dict[str, int]:
        """Days since the most recent bar, keyed by intraday-store symbol.

        Covers `intraday_bars` only. Tick-segment freshness needs channel
        gating to mean anything (see `symbol_coverage`), so use
        `symbol_trade_coverage()` for that instead of this dict.

        A source with no data at all (``last_date is None``) is omitted
        rather than scored as infinitely stale — its absence from this dict
        is itself the signal that it holds nothing.
        """
        return {
            src.symbol: (today - src.last_date).days
            for src in self.intraday_bars
            if src.last_date is not None
        }


def _scan_bar_store(store_dir: Path, granularity: str) -> tuple[SourceCoverage, ...]:
    """Shared implementation behind `scan_intraday_store` and `scan_daily_store`.

    Scans ``*.parquet`` files in ``store_dir``, one file per symbol. Only the
    ``date`` column is read from each file (a cheap columnar projection — no
    need to load OHLCV). Returns ``()`` if ``store_dir`` does not exist or
    holds no parquet files.
    """
    store_dir = Path(store_dir)
    if not store_dir.is_dir():
        return ()

    import pyarrow.parquet as pq

    coverages: list[SourceCoverage] = []
    for path in sorted(store_dir.glob("*.parquet")):
        symbol = path.stem
        table = pq.read_table(path, columns=["date"])
        dates = table.column("date").to_pylist()
        if not dates:
            coverages.append(
                SourceCoverage(
                    symbol=symbol,
                    granularity=granularity,
                    first_date=None,
                    last_date=None,
                    distinct_trading_days=0,
                    row_count=0,
                    source_file=str(path),
                )
            )
            continue

        distinct_dates = sorted(set(dates))
        coverages.append(
            SourceCoverage(
                symbol=symbol,
                granularity=granularity,
                first_date=distinct_dates[0],
                last_date=distinct_dates[-1],
                distinct_trading_days=len(distinct_dates),
                row_count=len(dates),
                source_file=str(path),
            )
        )
    return tuple(coverages)


def scan_intraday_store(
    store_dir: Path = DEFAULT_INTRADAY_STORE_DIR,
) -> tuple[SourceCoverage, ...]:
    """Scan the intraday 1-minute bar store. See `_scan_bar_store`."""
    return _scan_bar_store(store_dir, granularity="1min_bar")


def scan_daily_store(
    store_dir: Path = DEFAULT_DAILY_STORE_DIR,
) -> tuple[SourceCoverage, ...]:
    """Scan a real on-disk daily-bar store, if one ever exists.

    There is currently no such store in this repo — daily bars are fetched
    on demand from the chartbit daily endpoint (see
    `daily_bar_source_capability`), never cached to disk. This scans
    ``store_dir`` in the same shape as `scan_intraday_store` anyway, so it
    starts reporting real coverage the moment such a store is added, rather
    than needing a rewrite. Returns ``()`` today — honestly, not fabricated.
    """
    return _scan_bar_store(store_dir, granularity="daily_bar")


def daily_bar_source_capability(today: Optional[date] = None) -> SourceCapability:
    """Estimate how many trading days of daily bars chartbit can supply.

    There is no on-disk daily-bar store to measure (see `scan_daily_store`).
    The chartbit daily endpoint takes ``limit=0`` and returns its whole
    configured lookback window
    (``screener.screen_universe.DEFAULT_LOOKBACK_DAYS`` calendar days) in
    ONE call; unlike the intraday endpoint it has no ~90-day paging
    ceiling — that ceiling is what makes the on-disk intraday store shallow,
    and it does not apply here. ``estimated_trading_days`` converts the
    calendar-day lookback to trading days via the real IDX trading calendar
    (`lib.market_calendar`), not a hardcoded ratio.

    Imports `screener.screen_universe` lazily (it pulls in pandas) — raises
    `ImportError` if that is not available in the current interpreter, the
    same convention `scan_intraday_store` uses for pyarrow.
    """
    from screener.screen_universe import DEFAULT_LOOKBACK_DAYS

    from lib.market_calendar import CALENDAR

    as_of = today if today is not None else date.today()
    start = as_of - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    estimated_trading_days = CALENDAR.trading_days_between(start, as_of)

    return SourceCapability(
        source="chartbit_daily",
        granularity="daily_bar",
        lookback_calendar_days=DEFAULT_LOOKBACK_DAYS,
        estimated_trading_days=estimated_trading_days,
        basis=(
            f"IDX trading-calendar count over the trailing {DEFAULT_LOOKBACK_DAYS} calendar "
            f"days ({start} to {as_of}); chartbit's daily endpoint returns this whole window "
            "in one call (limit=0), unlike the intraday endpoint's ~90-day paging ceiling."
        ),
    )


def _scan_session(day: date, wl_dir: Path) -> SegmentSession:
    """Build one `SegmentSession` from every manifest under ``wl_dir``."""
    watchlist_id = wl_dir.name
    symbols: set[str] = set()
    channels: set[str] = set()
    starts: list[str] = []
    ends: list[str] = []
    total_data_records = 0
    total_control_records = 0
    manifest_count = 0
    unknown_coverage = False

    segment_files = sorted(wl_dir.glob(f"*{_SEGMENT_SUFFIX}"))
    if not segment_files:
        logger.warning("no segment files found under %s; treating as unknown coverage", wl_dir)
        return SegmentSession(
            date=day,
            watchlist_id=watchlist_id,
            symbols=(),
            channels=(),
            session_start_utc=None,
            session_end_utc=None,
            total_data_records=0,
            total_control_records=0,
            manifest_count=0,
            unknown_coverage=True,
        )

    for seg_path in segment_files:
        name = seg_path.name
        stem = name[: -len(_SEGMENT_SUFFIX)] if name.endswith(_SEGMENT_SUFFIX) else seg_path.stem
        manifest_path = seg_path.parent / f"{stem}{_MANIFEST_SUFFIX}"

        if not manifest_path.is_file():
            logger.warning("missing manifest for segment %s; treating as unknown coverage", seg_path)
            unknown_coverage = True
            continue

        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("unreadable/malformed manifest %s; treating as unknown coverage", manifest_path)
            unknown_coverage = True
            continue

        manifest_count += 1
        symbols.update(manifest.get("symbols", []))
        channels.update(manifest.get("channels", []))
        start = manifest.get("segment_start_utc")
        end = manifest.get("segment_end_utc")
        if start:
            starts.append(start)
        if end:
            ends.append(end)
        total_data_records += int(manifest.get("total_data_records", 0))
        total_control_records += int(manifest.get("total_control_records", 0))

    return SegmentSession(
        date=day,
        watchlist_id=watchlist_id,
        symbols=tuple(sorted(symbols)),
        channels=tuple(sorted(channels)),
        # ISO-8601 UTC timestamps sort correctly lexicographically as long as
        # they share the same fixed-width date/time prefix, which every
        # manifest in one session does — good enough for min/max here.
        session_start_utc=min(starts) if starts else None,
        session_end_utc=max(ends) if ends else None,
        total_data_records=total_data_records,
        total_control_records=total_control_records,
        manifest_count=manifest_count,
        unknown_coverage=unknown_coverage,
    )


def scan_segments(
    segments_root: Path = DEFAULT_SEGMENTS_ROOT,
) -> tuple[SegmentSession, ...]:
    """Scan ``segments/dt=YYYY-MM-DD/<watchlist_id>/`` directories.

    Returns one `SegmentSession` per (date, watchlist_id) pair found, built
    from that directory's manifest files. Returns ``()`` if ``segments_root``
    does not exist.
    """
    segments_root = Path(segments_root)
    if not segments_root.is_dir():
        return ()

    sessions: list[SegmentSession] = []

    for dt_dir in sorted(segments_root.iterdir()):
        if not dt_dir.is_dir() or not dt_dir.name.startswith("dt="):
            continue
        try:
            day = date.fromisoformat(dt_dir.name[len("dt=") :])
        except ValueError:
            logger.warning("skipping unparseable segment date dir: %s", dt_dir)
            continue

        for wl_dir in sorted(dt_dir.iterdir()):
            if not wl_dir.is_dir():
                continue
            sessions.append(_scan_session(day, wl_dir))

    return tuple(sessions)


def symbol_coverage(
    sessions: Iterable[SegmentSession],
    required_channel: str = DEFAULT_REQUIRED_CHANNEL,
) -> tuple[SymbolCoverage, ...]:
    """Per-symbol trading-day coverage, gated on ``required_channel``.

    A session's date counts toward a symbol only if the symbol is in that
    session's ``symbols`` AND ``required_channel`` is in that session's
    ``channels``. A session with ``unknown_coverage`` True has empty
    ``symbols``/``channels`` for whatever it couldn't read, so it never
    silently counts as coverage for anything.
    """
    by_symbol: dict[str, set[date]] = {}
    for session in sessions:
        if required_channel not in session.channels:
            continue
        for symbol in session.symbols:
            by_symbol.setdefault(symbol, set()).add(session.date)

    coverages: list[SymbolCoverage] = []
    for symbol, dates_set in sorted(by_symbol.items()):
        dates = tuple(sorted(dates_set))
        coverages.append(
            SymbolCoverage(
                symbol=symbol,
                required_channel=required_channel,
                dates=dates,
                first_date=dates[0] if dates else None,
                last_date=dates[-1] if dates else None,
                distinct_trading_days=len(dates),
            )
        )
    return tuple(coverages)


def build_inventory(
    intraday_store_dir: Path = DEFAULT_INTRADAY_STORE_DIR,
    segments_root: Path = DEFAULT_SEGMENTS_ROOT,
    daily_store_dir: Path = DEFAULT_DAILY_STORE_DIR,
    today: Optional[date] = None,
) -> InventoryReport:
    """Scan every known real data source and return one combined report.

    `daily_bar_capability` degrades to `None` (with a logged warning)
    rather than raising if `screener.screen_universe` is not importable —
    unlike `scan_intraday_store`'s pyarrow dependency, whose absence is
    fatal to reading bars at all, being unable to state the daily-bar
    source-capability estimate should not take down the rest of the scan.
    """
    try:
        daily_capability = daily_bar_source_capability(today=today)
    except ImportError as exc:
        logger.warning("could not measure daily-bar source capability: %s", exc)
        daily_capability = None

    return InventoryReport(
        intraday_bars=scan_intraday_store(intraday_store_dir),
        tick_segments=scan_segments(segments_root),
        daily_bars_on_disk=scan_daily_store(daily_store_dir),
        daily_bar_capability=daily_capability,
    )
