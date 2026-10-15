"""
screener/daily_store.py — Per-symbol daily bar accrual store (parquet).

Mirrors screener/intraday_store.py, adapted for the daily grain. The daily
chartbit endpoint has no ~90-day rolling-window cap (that cap is intraday
only — see docs/plans/00-roadmap-research-to-live.md §7), so unlike the
intraday store this module is not working around a short live window; it
exists so every daily-horizon result is reproducible WITHOUT a live token,
and so a future unattended paper-trading loop has a local history it can
trust instead of a live pull that may fail or return a token error.

Public API
----------
snapshot_daily(symbols, store_dir, data_source) -> dict[str, DailyAccrualSummary]
    Pull daily history from data_source and merge-dedup into the store.

load_daily(symbol, store_dir, start=None, end=None) -> pd.DataFrame | None
    Load accrued daily history for a symbol, optionally sliced to
    [start, end] (inclusive, "YYYY-MM-DD" strings). None when absent.

coverage_for_symbol(symbol, store_dir) -> DailyCoverage
store_coverage(symbols, store_dir) -> dict[str, DailyCoverage]
    Per-symbol coverage: row count, first/last date, and trading-day gaps
    against the real IDX calendar (lib.market_calendar) — a missing bar on
    a holiday is not a gap.

CLI
---
    python -m screener.daily_store snapshot --symbols A,B,C [--out-dir data/daily]
    python -m screener.daily_store status [--symbols A,B,C] [--out-dir data/daily]

Runs daily after market close (~16:30 WIB), same cron shape as intraday_store:
    30 16 * * 1-5 cd /path/to/research && \
        python -m screener.daily_store snapshot --symbols ... >> logs/daily_store.log 2>&1

Corporate actions (the single most important correctness requirement here):
chartbit's daily bars are BACK-ADJUSTED, so a re-fetch can legitimately
rewrite a symbol's entire prior history after a split/reverse-split (see
docs/plans/12-data-gap-backlog-and-demand-driven-capture.md §5e — RAJA's
2026-07-16 1:5.26 split). The merge NEVER overwrites a stored bar silently:
any re-fetched bar whose open/high/low/close disagrees with the stored value
beyond DEFAULT_DIVERGENCE_EPSILON is logged loudly (WARNING) and appended to
an on-disk divergence_log.jsonl next to the store, before the new
(back-adjusted, authoritative) value replaces the old one.

In-progress bars: chartbit publishes a bar for the CURRENT session as soon as the
market opens (volume 0 and close = prior close at the open, a partial bar after).
Persisting it poisons every consumer (the emitter reads "yesterday" off it). The
writer therefore never persists a bar dated today (WIB) before the session is
final (DAILY_BAR_FINAL_AT, 16:15 WIB) or dated in the future, and a write also
drops such a row already in the store, so a post-close refresh heals it.

Security note: the live data path requires SB_ACCESS_TOKEN. Tests MUST mock
_build_live_data_source — never pass a real token through the test suite.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from lib.market_calendar import CALENDAR, MarketCalendar
from screener.sb_http import classify_error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Parquet engine — pyarrow is available (confirmed 18.1.0; see intraday_store).
_PARQUET_ENGINE = "pyarrow"

# Dedup key. Unlike intraday_store (keyed on "datetime"), a daily bar is
# already one row per calendar date.
_DEDUP_COL = "date"

# Anchor start date for a full-history pull. The chartbit daily endpoint has
# no rolling-window cap (unlike intraday's ~90-day cap) — it simply clamps to
# whatever history it actually holds for a symbol (observed floor: rows from
# 2024-01-02, per docs/plans/00-roadmap-research-to-live.md §7). Requesting
# an anchor this early costs nothing extra: chartbit's limit=0 returns the
# whole window in one call regardless of how far back `start` reaches.
DEFAULT_DAILY_HISTORY_START = "2000-01-01"

# Fields compared for the corporate-action divergence check. Volume is
# deliberately excluded: share-count changes move volume by the same ratio
# as a split, but open/high/low/close is what every daily-horizon calibration
# in this repo actually keys off (see plan 12 §5e).
_DIVERGENCE_CHECK_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")

# A "small epsilon": tight enough that an ordinary re-fetch of unchanged
# history (same API, same inputs) never trips it, loose enough to ignore
# float round-trip noise through parquet. Real corporate-action
# back-adjustments seen so far (RAJA's ~80% split-driven close jump) are
# orders of magnitude larger than this.
DEFAULT_DIVERGENCE_EPSILON = 0.0005  # 5 bps relative difference

_DIVERGENCE_LOG_FILENAME = "divergence_log.jsonl"

# An empty response is only believable when the store is already (nearly) current.
# If the store's last bar is more than this many trading days behind the requested
# end, an empty frame means the fetch silently failed (2026-10-14 incident).
MAX_EMPTY_STALENESS_TRADING_DAYS = 3

# Incremental refresh re-requests this many calendar days before the last stored
# bar, so recent revisions (and any back-adjustment) land in the overlap where
# _detect_divergences can see them.
INCREMENTAL_OVERLAP_DAYS = 10

# Pause between per-symbol requests during a multi-symbol snapshot. The module
# had no rate limiting before; ~241 sequential calls against the Exodus/chartbit
# API should not be a burst.
REQUEST_DELAY_SECONDS = 0.25

# --all-stored skips a symbol whose last stored bar is more than this many calendar
# days behind the store's newest bar: it has stopped trading (the delisted names kept
# for survivorship tests, or a long suspension), so a fetch only returns empty and is
# reported as a failure. --include-ended fetches them anyway. Matches the dashboard's
# config.ENDED_DAYS.
ENDED_AFTER_DAYS = 60

# A stored-symbol filename stem must look like an IDX ticker; anything else in
# the store directory is not a symbol file and is ignored.
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,8}$")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DivergenceRecord:
    """One stored bar that disagrees with its re-fetched value beyond epsilon.

    This is the signature of a corporate-action back-adjustment (or, in
    principle, a chartbit data correction) — never a silent, undetected
    overwrite. ``pct_change`` is a fraction (0.05 == 5%), not a percentage.
    """
    symbol: str
    date: str
    field: str
    old_value: float
    new_value: float
    pct_change: float


@dataclass(frozen=True)
class DailyAccrualSummary:
    """Per-symbol result from a single snapshot_daily call."""
    symbol: str
    rows_before: int
    rows_after: int
    new_rows: int
    date_range: tuple[str | None, str | None]
    divergences: tuple[DivergenceRecord, ...] = field(default_factory=tuple)
    # failed=True: the fetch raised or came back stale-empty; the store file
    # was NOT touched. ``error`` is a token-free label (e.g. "401 ...", "403 ...").
    failed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class DailyCoverage:
    """Per-symbol on-disk coverage, including trading-day gaps.

    ``gap_dates`` are IDX trading days strictly between (inclusive)
    first_date and last_date that have no stored bar. A missing weekend or
    IDX holiday is never a gap — see lib.market_calendar.
    """
    symbol: str
    row_count: int
    first_date: str | None
    last_date: str | None
    trading_days_expected: int
    trading_days_present: int
    gap_dates: tuple[str, ...]


# ---------------------------------------------------------------------------
# Core persistence helpers
# ---------------------------------------------------------------------------

def _store_path(symbol: str, store_dir: str) -> str:
    """Return the parquet file path for a symbol."""
    return os.path.join(store_dir, f"{symbol}.parquet")


def _load_existing(symbol: str, store_dir: str) -> pd.DataFrame | None:
    """
    Load the existing store for a symbol.
    Returns None when the file is absent. Does NOT raise on I/O errors
    but logs them; the caller treats a load error as if the store is absent.
    """
    path = _store_path(symbol, store_dir)
    if not os.path.exists(path):
        return None
    try:
        return pd.read_parquet(path, engine=_PARQUET_ENGINE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load store for %s (%s); treating as absent", symbol, exc)
        return None


def _normalise_daily(df: pd.DataFrame) -> pd.DataFrame:
    """
    Coerce a fresh daily_history() pull into the store's on-disk shape.

    Both FixtureDataSource.daily_history and LiveDataSource.daily_history
    return "date" as a STRING column (unlike intraday_history's "date",
    which is a real datetime.date — see the module-level note in the report
    to the caller). Normalise to datetime.date here so the on-disk store is
    consistent with intraday_store's convention and readable by
    lib.data_inventory.scan_daily_store the same way.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if _DEDUP_COL not in out.columns:
        return pd.DataFrame()
    out[_DEDUP_COL] = pd.to_datetime(out[_DEDUP_COL]).dt.date
    for col in ("open", "high", "low", "close", "volume", "value"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "close" in out.columns:
        out = out.dropna(subset=["close"])
    out = out.sort_values(_DEDUP_COL, ignore_index=True)
    return out


WIB = datetime.timezone(datetime.timedelta(hours=7))
DAILY_BAR_FINAL_AT = datetime.time(16, 15)   # IDX closes 15:50, post-close ends 16:15 WIB


def _now_wib() -> datetime.datetime:
    return datetime.datetime.now(WIB)


def drop_incomplete_session(df: pd.DataFrame, now: datetime.datetime | None = None) -> pd.DataFrame:
    """Drop bars that are not a finished session: dated after today (WIB), or dated
    today before DAILY_BAR_FINAL_AT. Past bars are untouched, including legitimate
    zero-volume bars of a suspended name. Returns a new frame."""
    if df is None or df.empty or _DEDUP_COL not in df.columns:
        return df
    now = now.astimezone(WIB) if now is not None else _now_wib()
    today = now.date()
    final = now.time() >= DAILY_BAR_FINAL_AT
    d = pd.to_datetime(df[_DEDUP_COL]).dt.date
    incomplete = (d > today) | ((d == today) & (not final))
    if not incomplete.any():
        return df
    logger.info("dropping %d in-progress/future bar(s) (%s) not final before %s WIB",
                int(incomplete.sum()), ",".join(sorted({str(x) for x in d[incomplete]})),
                DAILY_BAR_FINAL_AT.strftime("%H:%M"))
    return df[~incomplete].reset_index(drop=True)


def _merge_dedup(existing: pd.DataFrame | None, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge existing and new DataFrames, dedup by date, sort ascending.

    Keeps the row from new_df when the same date appears in both (new_df
    takes precedence — chartbit's freshly fetched, back-adjusted value is
    the authoritative one; see _detect_divergences for the loud-logging
    guard around this).
    """
    if new_df is None or new_df.empty:
        return existing if existing is not None else pd.DataFrame()

    if existing is None or existing.empty:
        merged = new_df.copy()
    else:
        merged = pd.concat([existing, new_df], ignore_index=True)

    if _DEDUP_COL not in merged.columns:
        return merged

    merged = (
        merged
        .drop_duplicates(subset=[_DEDUP_COL], keep="last")
        .sort_values(_DEDUP_COL, ignore_index=True)
    )
    return merged


def _date_range(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """Return (earliest_date_str, latest_date_str) from the stored DataFrame."""
    if df is None or df.empty or _DEDUP_COL not in df.columns:
        return (None, None)
    dates = df[_DEDUP_COL]
    return (str(dates.min()), str(dates.max()))


def _pct_diff(old: float, new: float) -> float:
    """Relative difference between old and new, safe against old == 0."""
    if old == 0:
        return 0.0 if new == 0 else float("inf")
    return abs(new - old) / abs(old)


def _detect_divergences(
    existing: pd.DataFrame | None,
    fresh: pd.DataFrame,
    symbol: str,
    epsilon: float,
) -> tuple[DivergenceRecord, ...]:
    """
    Compare dates present in BOTH existing and fresh; flag any OHLC field
    that differs by more than `epsilon` (relative).

    This is deliberately independent of _merge_dedup's "new wins" behaviour:
    the merge always keeps the fresh value, but this function is what makes
    that overwrite loud rather than silent.
    """
    if existing is None or existing.empty or fresh is None or fresh.empty:
        return ()
    if _DEDUP_COL not in existing.columns or _DEDUP_COL not in fresh.columns:
        return ()

    fields = [f for f in _DIVERGENCE_CHECK_FIELDS if f in existing.columns and f in fresh.columns]
    if not fields:
        return ()

    joined = existing[[_DEDUP_COL, *fields]].merge(
        fresh[[_DEDUP_COL, *fields]], on=_DEDUP_COL, suffixes=("_old", "_new"), how="inner"
    )
    if joined.empty:
        return ()

    records: list[DivergenceRecord] = []
    for _, row in joined.iterrows():
        for f in fields:
            old_val, new_val = row[f"{f}_old"], row[f"{f}_new"]
            if pd.isna(old_val) or pd.isna(new_val):
                continue
            pct = _pct_diff(float(old_val), float(new_val))
            if pct > epsilon:
                records.append(
                    DivergenceRecord(
                        symbol=symbol,
                        date=str(row[_DEDUP_COL]),
                        field=f,
                        old_value=float(old_val),
                        new_value=float(new_val),
                        pct_change=pct,
                    )
                )
    return tuple(records)


def _divergence_log_path(store_dir: str) -> str:
    return os.path.join(store_dir, _DIVERGENCE_LOG_FILENAME)


def _append_divergence_log(store_dir: str, divergences: tuple[DivergenceRecord, ...]) -> None:
    """Append each divergence as one JSON line to an append-only log next to the store.

    Never touches credentials; the record is price data only.
    """
    if not divergences:
        return
    detected_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    path = _divergence_log_path(store_dir)
    with open(path, "a", encoding="utf-8") as f:
        for d in divergences:
            f.write(
                json.dumps(
                    {
                        "symbol": d.symbol,
                        "date": d.date,
                        "field": d.field,
                        "old_value": d.old_value,
                        "new_value": d.new_value,
                        "pct_change": d.pct_change,
                        "detected_at": detected_at,
                    }
                )
                + "\n"
            )


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------

def _failed_summary(
    symbol: str, existing: pd.DataFrame | None, rows_before: int, error: str
) -> DailyAccrualSummary:
    dr = _date_range(existing) if existing is not None else (None, None)
    return DailyAccrualSummary(
        symbol=symbol, rows_before=rows_before, rows_after=rows_before, new_rows=0,
        date_range=dr, failed=True, error=error,
    )


def _empty_is_stale(
    existing: pd.DataFrame | None, end: str, calendar: MarketCalendar
) -> str | None:
    """Error label when an empty fetch cannot be trusted, else None."""
    if existing is None or existing.empty or _DEDUP_COL not in existing.columns:
        return "empty response and no stored history"
    last = max(existing[_DEDUP_COL])
    end_d = datetime.date.fromisoformat(end)
    if end_d <= last:
        return None
    gap = len(_trading_days_in_range(last + datetime.timedelta(days=1), end_d, calendar))
    if gap > MAX_EMPTY_STALENESS_TRADING_DAYS:
        return (
            f"empty response but store ends {last}, {gap} trading days before "
            f"requested end {end} (silent fetch failure?)"
        )
    return None


def discover_store_symbols(store_dir: str) -> list[str]:
    """Valid symbols with a ``<SYMBOL>.parquet`` file under store_dir, sorted.

    Files whose stem is not a plausible ticker are ignored (with a warning when
    they are parquet files, so a stray file is visible rather than silent).
    """
    if not os.path.isdir(store_dir):
        return []
    symbols: list[str] = []
    for fname in os.listdir(store_dir):
        stem, ext = os.path.splitext(fname)
        if ext != ".parquet":
            continue
        if _SYMBOL_RE.match(stem):
            symbols.append(stem)
        else:
            logger.warning("Ignoring non-symbol parquet file in store: %r", fname)
    return sorted(symbols)


def ended_symbols(store_dir: str, symbols: list[str]) -> list[str]:
    """Symbols whose last stored bar is more than ENDED_AFTER_DAYS behind the newest, sorted."""
    last: dict[str, datetime.date] = {}
    for symbol in symbols:
        existing = _load_existing(symbol, store_dir)
        if existing is not None and not existing.empty and _DEDUP_COL in existing.columns:
            last[symbol] = max(existing[_DEDUP_COL])
    if not last:
        return []
    cutoff = max(last.values()) - datetime.timedelta(days=ENDED_AFTER_DAYS)
    return sorted(sym for sym, d in last.items() if d < cutoff)


def incremental_start(existing: pd.DataFrame | None, full_start: str) -> str:
    """Start date for an incremental fetch: last stored date - overlap.

    Falls back to ``full_start`` when there is no stored history, and never
    reaches earlier than ``full_start``.
    """
    if existing is None or existing.empty or _DEDUP_COL not in existing.columns:
        return full_start
    last = max(existing[_DEDUP_COL])
    start = str(last - datetime.timedelta(days=INCREMENTAL_OVERLAP_DAYS))
    return max(start, full_start)


# ---------------------------------------------------------------------------
# Public snapshot API
# ---------------------------------------------------------------------------

def snapshot_daily(
    symbols: list[str] | tuple[str, ...],
    store_dir: str,
    data_source: Any,
    start: str = DEFAULT_DAILY_HISTORY_START,
    end: str | None = None,
    divergence_epsilon: float = DEFAULT_DIVERGENCE_EPSILON,
    calendar: MarketCalendar = CALENDAR,
    incremental: bool = False,
    request_delay: float = 0.0,
    on_result: Callable[[int, int, DailyAccrualSummary], None] | None = None,
) -> dict[str, DailyAccrualSummary]:
    """
    Pull daily history for each symbol and merge-dedup into the store.

    incremental:   when True, symbols that already have stored bars are fetched
                    from (last stored date - INCREMENTAL_OVERLAP_DAYS). If the
                    overlap shows a corporate-action divergence (back-adjusted
                    history no longer matches what is stored), that symbol is
                    re-fetched in FULL so the whole history stays consistent.
    request_delay: seconds slept between symbols (not after the last).
    on_result:     called as (index, total, summary) after each symbol; one
                    symbol's failure never stops the rest.

    Parameters
    ----------
    symbols:            List of ticker symbols to snapshot.
    store_dir:          Directory for per-symbol parquet files. Created if absent.
    data_source:        Any object implementing DataSource.daily_history(symbol, start, end).
    start, end:         "YYYY-MM-DD" window requested from data_source. Defaults to
                         the widest anchor (DEFAULT_DAILY_HISTORY_START) through today —
                         cheap to request wide since chartbit's daily endpoint returns
                         its whole held window in one call (limit=0), unlike intraday.
    divergence_epsilon: Relative OHLC tolerance before a re-fetched bar that disagrees
                         with a stored one is flagged as a corporate-action divergence.

    Returns
    -------
    dict mapping symbol -> DailyAccrualSummary (rows_before/after/new, date_range,
    divergences).
    """
    os.makedirs(store_dir, exist_ok=True)
    end = end or str(datetime.date.today())

    summaries: dict[str, DailyAccrualSummary] = {}

    total = len(symbols)
    for idx, symbol in enumerate(symbols, start=1):
        if idx > 1 and request_delay > 0:
            time.sleep(request_delay)
        summary = _snapshot_one(
            symbol, store_dir, data_source, start, end, divergence_epsilon, calendar, incremental
        )
        summaries[symbol] = summary
        if on_result is not None:
            on_result(idx, total, summary)

    return summaries


def _snapshot_one(
    symbol: str,
    store_dir: str,
    data_source: Any,
    start: str,
    end: str,
    divergence_epsilon: float,
    calendar: MarketCalendar,
    incremental: bool,
) -> DailyAccrualSummary:
    """Fetch + merge one symbol. Never raises for fetch problems; returns a failed summary."""
    existing = _load_existing(symbol, store_dir)
    rows_before = len(existing) if existing is not None else 0
    fetch_start = incremental_start(existing, start) if incremental else start

    try:
        raw = data_source.daily_history(symbol, start=fetch_start, end=end)
    except Exception as exc:  # noqa: BLE001
        label = classify_error(exc)
        logger.error("daily_history(%s) FAILED: %s; store left untouched", symbol, label)
        return _failed_summary(symbol, existing, rows_before, label)

    fresh = _normalise_daily(raw)

    if fresh.empty:
        stale = _empty_is_stale(existing, end, calendar)
        if stale:
            logger.error("daily_history(%s) FAILED: %s; store left untouched", symbol, stale)
            return _failed_summary(symbol, existing, rows_before, stale)

    divergences = _detect_divergences(existing, fresh, symbol, divergence_epsilon)
    if divergences and fetch_start != start:
        # Back-adjustment seen in the overlap: an incremental merge would leave
        # pre-overlap history on the old basis. Re-pull everything.
        logger.warning(
            "%s: divergence in incremental overlap; re-fetching full history from %s",
            symbol, start,
        )
        try:
            raw = data_source.daily_history(symbol, start=start, end=end)
        except Exception as exc:  # noqa: BLE001
            label = classify_error(exc)
            logger.error("daily_history(%s) full re-fetch FAILED: %s; store left untouched", symbol, label)
            return _failed_summary(symbol, existing, rows_before, f"full re-fetch after divergence: {label}")
        fresh = _normalise_daily(raw)
        if fresh.empty:
            return _failed_summary(symbol, existing, rows_before, "empty response on full re-fetch")
        divergences = _detect_divergences(existing, fresh, symbol, divergence_epsilon)
    if divergences:
        logger.warning(
            "%s: %d stored daily bar(s) disagree with the re-fetched value beyond "
            "epsilon=%.4f%% — likely a corporate-action back-adjustment (see plan 12 "
            "§5e). Overwriting with the freshly fetched value; recording to %s",
            symbol,
            len(divergences),
            divergence_epsilon * 100,
            _divergence_log_path(store_dir),
        )
        _append_divergence_log(store_dir, divergences)

    merged = drop_incomplete_session(_merge_dedup(existing, fresh))

    if not merged.empty:
        path = _store_path(symbol, store_dir)
        try:
            merged.to_parquet(path, engine=_PARQUET_ENGINE, index=False)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to write store for %s: %s", symbol, exc)
            return _failed_summary(symbol, existing, rows_before, f"write failed: {type(exc).__name__}")

    rows_after = len(merged)
    new_rows = max(0, rows_after - rows_before)
    dr = _date_range(merged)

    summary = DailyAccrualSummary(
        symbol=symbol,
        rows_before=rows_before,
        rows_after=rows_after,
        new_rows=new_rows,
        date_range=dr,
        divergences=divergences,
    )

    logger.info(
        "%s: %d → %d rows (+%d new), %s – %s%s",
        symbol,
        rows_before,
        rows_after,
        new_rows,
        dr[0],
        dr[1],
        f" [{len(divergences)} DIVERGENCE(S)]" if divergences else "",
    )
    return summary


# ---------------------------------------------------------------------------
# Public load API
# ---------------------------------------------------------------------------

def load_daily(
    symbol: str,
    store_dir: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame | None:
    """
    Load the accrued daily history for a symbol, optionally sliced to a date range.

    Parameters
    ----------
    symbol, store_dir: as elsewhere in this module.
    start, end:         Optional "YYYY-MM-DD" inclusive bounds. Either or both may
                         be omitted to leave that side of the range open.

    Returns
    -------
    pd.DataFrame or None when the store file does not exist.
    """
    df = _load_existing(symbol, store_dir)
    if df is None or df.empty or (start is None and end is None):
        return df
    if _DEDUP_COL not in df.columns:
        return df
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= df[_DEDUP_COL] >= datetime.date.fromisoformat(start)
    if end is not None:
        mask &= df[_DEDUP_COL] <= datetime.date.fromisoformat(end)
    return df[mask].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Public coverage API
# ---------------------------------------------------------------------------

def _trading_days_in_range(
    first: datetime.date, last: datetime.date, calendar: MarketCalendar
) -> list[datetime.date]:
    """Every IDX trading day in [first, last] inclusive."""
    days: list[datetime.date] = []
    current = first
    one_day = datetime.timedelta(days=1)
    while current <= last:
        if calendar.is_trading_day(current):
            days.append(current)
        current += one_day
    return days


def coverage_for_symbol(
    symbol: str,
    store_dir: str,
    calendar: MarketCalendar = CALENDAR,
) -> DailyCoverage:
    """Coverage for one symbol: row count, first/last date, trading-day gaps."""
    df = _load_existing(symbol, store_dir)
    if df is None or df.empty or _DEDUP_COL not in df.columns:
        return DailyCoverage(
            symbol=symbol,
            row_count=0,
            first_date=None,
            last_date=None,
            trading_days_expected=0,
            trading_days_present=0,
            gap_dates=(),
        )

    present_dates = set(df[_DEDUP_COL])
    first_date, last_date = min(present_dates), max(present_dates)
    expected = _trading_days_in_range(first_date, last_date, calendar)
    gaps = tuple(str(d) for d in expected if d not in present_dates)

    return DailyCoverage(
        symbol=symbol,
        row_count=len(df),
        first_date=str(first_date),
        last_date=str(last_date),
        trading_days_expected=len(expected),
        trading_days_present=len(expected) - len(gaps),
        gap_dates=gaps,
    )


def _discover_symbols(store_dir: str) -> list[str]:
    """Every symbol with a parquet file under store_dir, sorted."""
    if not os.path.isdir(store_dir):
        return []
    return sorted(
        os.path.splitext(f)[0] for f in os.listdir(store_dir) if f.endswith(".parquet")
    )


def store_coverage(
    symbols: list[str] | tuple[str, ...] | None,
    store_dir: str,
    calendar: MarketCalendar = CALENDAR,
) -> dict[str, DailyCoverage]:
    """
    Coverage for every requested symbol, or every symbol on disk when
    `symbols` is None.
    """
    if symbols is None:
        symbols = _discover_symbols(store_dir)
    return {s: coverage_for_symbol(s, store_dir, calendar=calendar) for s in symbols}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_live_data_source(bearer_token: str | None = None) -> Any:
    """
    Build a LiveDataSource from the screener package.
    The SB_ACCESS_TOKEN env var must be set. Tests patch this function.
    """
    from screener.screen_universe import LiveDataSource  # local import to keep mock seam clean
    token = bearer_token or os.environ.get("SB_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError(
            "SB_ACCESS_TOKEN environment variable is required for live snapshots.\n"
            "Set it and retry, or use --source fixture for offline testing."
        )
    return LiveDataSource(bearer_token=token)


def _progress_line(idx: int, total: int, s: DailyAccrualSummary) -> str:
    prefix = f"[{idx}/{total}] {s.symbol}"
    if s.failed:
        return f"{prefix} FAILED - {s.error}"
    div = f" [{len(s.divergences)} DIVERGENCE(S)]" if s.divergences else ""
    if s.new_rows > 0 or s.divergences:
        return f"{prefix} ok (+{s.new_rows} bars){div}"
    return f"{prefix} unchanged"


def _build_status_source(args: argparse.Namespace) -> Any:
    """EmittenClient for the universe-status step. Tests patch this. Never prints a token."""
    from screener.emitten import EmittenClient

    bearer = args.bearer_token or os.environ.get("SB_ACCESS_TOKEN", "")
    if not bearer:
        raise RuntimeError("SB_ACCESS_TOKEN is not set: the emitten classification lists need it.")
    return EmittenClient(bearer)


def _universe_status_step(args: argparse.Namespace, symbols: list[str]) -> Any | None:
    """
    Build and write universe_status.json; None (after printing why) means exit 2 - never
    refresh blind. The emitten client raises on failure by design.
    """
    from screener.universe_status import build_universe_status, write_universe_status

    try:
        status = build_universe_status(symbols, _build_status_source(args), as_of=datetime.date.today())
    except Exception as exc:  # noqa: BLE001 - any failure here must stop the refresh, with a reason
        reason = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else classify_error(exc)
        where = f" {exc.where}" if hasattr(exc, "where") else ""
        print(f"Tradable check FAILED{where}: {reason}\n"
              f"Not refreshing blind. Fix it, or pass --no-tradable-check to skip the check explicitly.")
        return None
    path = write_universe_status(status, args.store_dir)
    unknown = sum(1 for v in status.symbols.values() if v.is_tradable is None)
    print(f"Universe status: {len(status.symbols)} symbols, {len(status.eligible)} funding-eligible, "
          f"{len(status.not_tradable())} not tradable, {unknown} not on a funding list or unknown -> {path}")
    return status


def _refresh_all_stored(args: argparse.Namespace) -> int:
    """Refresh every stored symbol; returns the process exit code (1 if any failed, 2 if the
    tradable check could not run)."""
    symbols = discover_store_symbols(args.store_dir)
    if not symbols:
        print(f"No stored symbols found under {args.store_dir}; nothing to refresh "
              f"(use --symbols to seed the store).")
        return 2
    added: list[str] = []
    if args.no_tradable_check:
        print("Tradable check skipped (--no-tradable-check): refreshing without it.")
    else:
        status = _universe_status_step(args, symbols)
        if status is None:
            return 2
        untradable = [s for s in symbols if s in status.not_tradable()]
        if untradable:
            symbols = [s for s in symbols if s not in set(untradable)]
            print(f"Skipped {len(untradable)} not-tradable symbols (delisting/suspension): "
                  f"{', '.join(untradable)}")
        added = sorted(status.eligible & (status.tradable() - set(discover_store_symbols(args.store_dir))))
        if added:
            print(f"Added {len(added)} new eligible-and-tradable symbols (full history): {', '.join(added)}")
    ended = [] if args.include_ended else ended_symbols(args.store_dir, symbols)
    if ended:
        symbols = [s for s in symbols if s not in set(ended)]
        print(f"Skipped {len(ended)} ended symbols (stopped trading, likely delisted; "
              f"--include-ended to fetch them): {', '.join(ended)}")
    symbols = symbols + added
    ds = _build_live_data_source(bearer_token=args.bearer_token)
    print(f"Refreshing {len(symbols)} stored symbols from {args.store_dir} "
          f"({'full' if args.full else 'incremental'}, {REQUEST_DELAY_SECONDS}s between requests)")
    summaries = snapshot_daily(
        symbols, store_dir=args.store_dir, data_source=ds, start=args.start, end=args.end,
        incremental=not args.full, request_delay=REQUEST_DELAY_SECONDS,
        on_result=lambda i, n, s: print(_progress_line(i, n, s), flush=True),
    )
    failed = {sym: s for sym, s in summaries.items() if s.failed}
    refreshed = [s for s in summaries.values() if not s.failed and (s.new_rows > 0 or s.divergences)]
    unchanged = len(summaries) - len(failed) - len(refreshed)
    print(f"Summary: refreshed: {len(refreshed)}, unchanged: {unchanged}, failed: {len(failed)}")
    for sym, s in sorted(failed.items()):
        print(f"  FAILED {sym}: {s.error} (store untouched, last bar {s.date_range[1]})")
    diverged = [s.symbol for s in summaries.values() if s.divergences]
    if diverged:
        print(f"  Divergences (corporate-action rewrites) in: {', '.join(diverged)} "
              f"- see {_divergence_log_path(args.store_dir)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> None:
    """
    CLI entrypoint: python -m screener.daily_store {snapshot,status} [options]

    Suggested cron (daily post-close, 16:30 WIB = 09:30 UTC):
        30 9 * * 1-5 cd /path/to/research && \
            python -m screener.daily_store snapshot --symbols ... \
                >> logs/daily_store.log 2>&1
    """
    parser = argparse.ArgumentParser(
        prog="python -m screener.daily_store",
        description="Accrue daily bars into a durable local parquet store.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="Pull and merge daily bars for symbols")
    target = snap.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--symbols",
        help=(
            "Comma-separated ticker list. No default — the daily-fit survivor set "
            "is decided by backtest results, not hardcoded here."
        ),
    )
    target.add_argument(
        "--all-stored",
        action="store_true",
        help=(
            "Refresh every symbol already in --out-dir (from <SYMBOL>.parquet names). "
            "Incremental by default; per-symbol failures are summarised and exit non-zero."
        ),
    )
    snap.add_argument(
        "--full",
        action="store_true",
        help="With --all-stored: fetch full history for every symbol instead of incrementally",
    )
    snap.add_argument(
        "--include-ended",
        action="store_true",
        help=(
            f"With --all-stored: also fetch symbols whose last bar is more than "
            f"{ENDED_AFTER_DAYS} days behind the store's newest (skipped by default as delisted)"
        ),
    )
    snap.add_argument(
        "--no-tradable-check",
        action="store_true",
        help=(
            "With --all-stored: skip the universe-status step (emitten classification "
            "lists + tradeable flag). Without this flag a failure of that step "
            "exits 2 rather than refreshing blind."
        ),
    )
    snap.add_argument(
        "--out-dir",
        dest="store_dir",
        default="data/daily",
        help="Directory for parquet store files (default: data/daily)",
    )
    snap.add_argument(
        "--start",
        default=DEFAULT_DAILY_HISTORY_START,
        help=f"Requested window start, YYYY-MM-DD (default: {DEFAULT_DAILY_HISTORY_START})",
    )
    snap.add_argument(
        "--end",
        default=None,
        help="Requested window end, YYYY-MM-DD (default: today)",
    )
    snap.add_argument(
        "--bearer-token",
        default=None,
        help="SB bearer token (falls back to SB_ACCESS_TOKEN env var)",
    )

    status = sub.add_parser("status", help="Report per-symbol on-disk coverage")
    status.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated ticker list (default: every symbol found in --out-dir)",
    )
    status.add_argument(
        "--out-dir",
        dest="store_dir",
        default="data/daily",
        help="Directory for parquet store files (default: data/daily)",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.command == "snapshot":
        if args.all_stored:
            sys.exit(_refresh_all_stored(args))
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        ds = _build_live_data_source(bearer_token=args.bearer_token)
        summaries = snapshot_daily(
            symbols, store_dir=args.store_dir, data_source=ds, start=args.start, end=args.end,
            request_delay=REQUEST_DELAY_SECONDS,
        )
        for sym, s in summaries.items():
            if s.failed:
                print(f"  {sym}: FAILED - {s.error} (store untouched, last bar {s.date_range[1]})")
                continue
            note = (
                f" | {len(s.divergences)} DIVERGENCE(S) — see "
                f"{_divergence_log_path(args.store_dir)}"
                if s.divergences
                else ""
            )
            print(
                f"  {sym}: {s.rows_before} → {s.rows_after} rows "
                f"(+{s.new_rows} new) | {s.date_range[0]} – {s.date_range[1]}{note}"
            )
        failed = [sym for sym, s in summaries.items() if s.failed]
        if failed:
            print(f"SNAPSHOT FAILED for {len(failed)}/{len(summaries)} symbol(s): {', '.join(failed)}")
            sys.exit(1)
    elif args.command == "status":
        symbols = (
            [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            if args.symbols
            else None
        )
        coverage = store_coverage(symbols, store_dir=args.store_dir)
        if not coverage:
            print(f"  (no symbols found under {args.store_dir})")
        for sym, c in sorted(coverage.items()):
            gap_note = f", {len(c.gap_dates)} gap day(s): {', '.join(c.gap_dates)}" if c.gap_dates else ""
            print(
                f"  {sym}: {c.row_count} rows | {c.first_date} – {c.last_date} | "
                f"{c.trading_days_present}/{c.trading_days_expected} trading days{gap_note}"
            )


if __name__ == "__main__":
    main()
