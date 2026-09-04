"""
screener/tick_bars.py — Per-symbol tick-derived bar store (parquet), built by
invoking the Go `sbcap bars` subcommand over captured protobuf segments.

This is the missing edge between captured ticks (`segments/dt=YYYY-MM-DD/
<watchlist_id>/seg-*.binpb.zst`) and the research substrate: without it, the
53 captured sessions in S3 / locally are unreachable from strategy research.
`sbcap bars` (collector/cmd/sbcap/bars.go) does the actual replay + bucketing;
this module discovers segment files, shells out per (symbol, day), and
accrues the resulting bars into a parquet store using the same append-merge
convention as screener/intraday_store.py.

The first eight `sbcap bars` CSV columns (datetime, date, open, high, low,
close, volume, value) are deliberately identical to the chartbit intraday
schema so the two substrates are directly comparable; the remainder
(trades, buy_vol, sell_vol, buy_trades, sell_trades, untagged_vol,
untagged_trades, vwap) are tick-only fields that chartbit cannot provide.
The CSV header is parsed dynamically (pandas infers it from the header row;
nothing here hardcodes the column list) because bars.go's schema is not
frozen — it has already changed once to add untagged_vol/untagged_trades.

By default `sbcap bars` includes EVERY trade, tagged or not: untagged trades
(no aggressor side) cover the pre-opening auction, scattered mid-session
negotiated/crossing prints, and the entire 16:00-16:16 post-trading session,
which can exceed 10% of a symbol's daily volume (measured PTRO 2026-06-22).

TWO STORES — DO NOT COLLAPSE THEM BACK INTO ONE
------------------------------------------------
This module builds two DIFFERENT substrates, not one store with an optional
filter, because one cannot be derived from the other after the fact:

  - Full-tape store (no flag; conventionally `data/tick_bars/`): every trade,
    tagged and untagged. This is the correct input for anything measured in
    shares or rupiah where the negotiated/crossing prints are real traded
    quantity — volume, participation, turnover, impact calibration. Dropping
    untagged trades here would understate volume, which is the bug this
    module's `--include-auction` predecessor had.

  - Tagged-only store (`--tagged-only`; conventionally `data/tick_bars_tagged/`):
    untagged trades dropped entirely, at BUILD TIME. **This is the default
    input for any signal whose trigger is a price level — ORB, momentum,
    breakout, or anything else keyed off open/high/low/close.** Untagged
    trades are negotiated/crossing prints that a resting order in the
    continuous book could never interact with. If one sets a bar's high or
    low, a price-keyed strategy is trading against a level the account could
    never have transacted at — a backtest-poisoning bug in the same failure
    class as the short leg removed in commit c62eba8 (a signal computed
    against prices the account cannot actually transact at).

  Why build-time, not a query-time filter: subtracting `untagged_vol` from
  `volume` recovers tagged volume after the fact, but there is no way to back
  an untagged trade's price back out of a bar's `open`/`high`/`low`/`close`
  once it has been folded in. The choice has to be made before the bar is
  built, so both stores must be built, from scratch, independently — never
  try to derive the tagged-only store from the full-tape store, or the two
  will drift apart (or worse, look derivable when they are not).

  Use `build_both_stores` (or the `build-both` CLI subcommand) rather than
  two separate `build` invocations whenever both stores are wanted, so they
  cannot go out of sync with each other for the same symbol/day.

A whole-day volume diff of the full-tape store against chartbit (which has no
post-trading window) will disagree for that reason, not an ingestion bug.
A continuous-session (09:00-15:59 WIB) diff of the full-tape store against
chartbit will ALSO disagree more than expected, because mid-session untagged
prints fall inside that window too — restrict such comparisons to the
tagged-only store, which reproduces chartbit's continuous-session view.

Public API
----------
build_bars(symbols, segments_root, store_dir, interval, sbcap_path,
           from_date, to_date, tagged_only) -> dict[str, AccrualSummary | BuildError]
    Discover segments per symbol/day, shell out to `sbcap bars`, and
    append-merge the parsed bars into `<store_dir>/<SYMBOL>.parquet`.
    `tagged_only=True` passes `--tagged-only` through to sbcap.

build_both_stores(symbols, segments_root, store_dir, tagged_store_dir, ...)
    -> {"full": {...}, "tagged": {...}}
    Runs build_bars twice — full tape into `store_dir`, tagged-only into
    `tagged_store_dir` — so the two stores are always built together.

load_tick_bars(symbol, store_dir) -> pd.DataFrame | None
    Load the full accrued bar history for a symbol from a given store
    directory; None when absent. Pass whichever store's directory you need
    (full-tape or tagged-only) — this function does not care which it is.

CLI
---
    python -m screener.tick_bars build --symbols PTRO,BRPT \
        --store-dir data/tick_bars [--tagged-only] [--segments-root segments] \
        [--interval 1m] [--from-date 2026-06-17] [--to-date 2026-06-26] \
        [--sbcap-path bin/sbcap]

    python -m screener.tick_bars build-both --symbols PTRO,BRPT \
        --store-dir data/tick_bars --tagged-store-dir data/tick_bars_tagged \
        [--segments-root segments] [--interval 1m] \
        [--from-date 2026-06-17] [--to-date 2026-06-26] [--sbcap-path bin/sbcap]

Failure contract
----------------
A per-symbol `sbcap` failure (non-zero exit) is reported as a `BuildError`
in the returned mapping — it is never silently folded into a zero-row
`AccrualSummary`. The `build` CLI exits non-zero when EVERY requested symbol
fails; `build-both` exits non-zero when EVERY symbol fails for EITHER store.
Both always print one explicit line per symbol distinguishing "no new data"
(sbcap ran fine, nothing new to accrue) from "errored" (sbcap failed).
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Parquet engine — pyarrow is available (confirmed 25.0.1).
_PARQUET_ENGINE = "pyarrow"

# Dedup key: the sbcap bars CSV "datetime" column (bar start time, WIB wall clock).
_DEDUP_COL = "datetime"

# Bar width passed to `sbcap bars --interval`; matches the chartbit intraday grid.
_DEFAULT_INTERVAL = "1m"

# Default location of the built sbcap binary (see Makefile `make build`).
_DEFAULT_SBCAP_PATH = "bin/sbcap"

_DATE_DIR_RE = re.compile(r"^dt=(\d{4}-\d{2}-\d{2})$")
_SEGMENT_SUFFIX = ".binpb.zst"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AccrualSummary:
    """Per-symbol result from a successful build_bars() pass."""
    symbol: str
    rows_before: int
    rows_after: int
    new_rows: int
    date_range: tuple[str | None, str | None]


@dataclass(frozen=True)
class BuildError:
    """Per-symbol result when `sbcap bars` failed for one or more days."""
    symbol: str
    message: str


class SbcapError(RuntimeError):
    """Raised when an `sbcap bars` invocation exits non-zero."""


# ---------------------------------------------------------------------------
# Segment discovery
# ---------------------------------------------------------------------------

def _discover_dates(
    segments_root: str,
    from_date: str | None,
    to_date: str | None,
) -> list[str]:
    """
    Return sorted ISO date strings for `dt=YYYY-MM-DD` directories under
    segments_root, optionally filtered to [from_date, to_date] inclusive.
    """
    if not os.path.isdir(segments_root):
        return []

    dates: list[str] = []
    for name in os.listdir(segments_root):
        match = _DATE_DIR_RE.match(name)
        if not match:
            continue
        date_str = match.group(1)
        if from_date and date_str < from_date:
            continue
        if to_date and date_str > to_date:
            continue
        dates.append(date_str)

    return sorted(dates)


def _discover_segments_for_date(segments_root: str, date_str: str) -> list[str]:
    """
    Return every segment file for one date, across ALL watchlist_id
    directories under `dt=<date_str>/`. A widened watchlist forks the
    directory name (e.g. "default" + "lq45-mining" on the same date), so
    both must be read to get the full tape for that session.
    """
    date_dir = os.path.join(segments_root, f"dt={date_str}")
    if not os.path.isdir(date_dir):
        return []

    paths: list[str] = []
    for watchlist_id in sorted(os.listdir(date_dir)):
        wl_dir = os.path.join(date_dir, watchlist_id)
        if not os.path.isdir(wl_dir):
            continue  # skip stray files, e.g. .DS_Store
        for fname in sorted(os.listdir(wl_dir)):
            if fname.endswith(_SEGMENT_SUFFIX):
                paths.append(os.path.join(wl_dir, fname))

    return paths


# ---------------------------------------------------------------------------
# sbcap invocation + CSV parsing
# ---------------------------------------------------------------------------

def _run_sbcap_bars(
    segment_paths: list[str],
    symbol: str,
    interval: str,
    sbcap_path: str,
    tagged_only: bool = False,
) -> pd.DataFrame:
    """
    Invoke `sbcap bars <segment_paths>... --symbol <symbol> --interval
    <interval> [--tagged-only]` and parse its stdout CSV.

    `tagged_only=True` drops untagged (negotiated/crossing) trades at the
    sbcap level — see the module docstring for why this must happen at
    build time rather than as a post-hoc filter on the full-tape store.

    Raises SbcapError (with sbcap's stderr) on a non-zero exit. Never uses
    shell=True — argv is passed as a list.
    """
    argv = [
        sbcap_path,
        "bars",
        *segment_paths,
        "--symbol",
        symbol,
        "--interval",
        interval,
    ]
    if tagged_only:
        argv.append("--tagged-only")
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SbcapError(
            f"sbcap bars exited {proc.returncode} for symbol={symbol} "
            f"({len(segment_paths)} segment file(s)): {proc.stderr.strip()}"
        )
    return _parse_bars_csv(proc.stdout)


def _parse_bars_csv(csv_text: str) -> pd.DataFrame:
    """Parse one `sbcap bars` CSV payload into a typed DataFrame."""
    df = pd.read_csv(io.StringIO(csv_text), parse_dates=["datetime"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


# ---------------------------------------------------------------------------
# Store persistence (mirrors screener/intraday_store.py conventions)
# ---------------------------------------------------------------------------

def _store_path(symbol: str, store_dir: str) -> str:
    return os.path.join(store_dir, f"{symbol}.parquet")


def _load_existing(symbol: str, store_dir: str) -> pd.DataFrame | None:
    """Load the existing store for a symbol; None when absent or unreadable."""
    path = _store_path(symbol, store_dir)
    if not os.path.exists(path):
        return None
    try:
        return pd.read_parquet(path, engine=_PARQUET_ENGINE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load tick-bar store for %s (%s); treating as absent", symbol, exc)
        return None


def _merge_dedup(existing: pd.DataFrame | None, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge existing and newly-built bars, dedup by datetime, sort ascending.
    Keeps the newest build's row for a duplicated timestamp (new_df wins).
    """
    if new_df is None or new_df.empty:
        return existing if existing is not None else pd.DataFrame()

    if existing is None or existing.empty:
        merged = new_df.copy()
    else:
        merged = pd.concat([existing, new_df], ignore_index=True)

    if _DEDUP_COL not in merged.columns:
        return merged

    return (
        merged
        .drop_duplicates(subset=[_DEDUP_COL], keep="last")
        .sort_values(_DEDUP_COL, ignore_index=True)
    )


def _date_range(df: pd.DataFrame) -> tuple[str | None, str | None]:
    if df is None or df.empty or _DEDUP_COL not in df.columns:
        return (None, None)
    dts = pd.to_datetime(df[_DEDUP_COL])
    return (str(dts.min().date()), str(dts.max().date()))


def load_tick_bars(symbol: str, store_dir: str) -> pd.DataFrame | None:
    """Load the full accrued tick-bar history for a symbol; None when absent."""
    return _load_existing(symbol, store_dir)


# ---------------------------------------------------------------------------
# Per-symbol build
# ---------------------------------------------------------------------------

def _build_symbol(
    symbol: str,
    dates: list[str],
    segments_root: str,
    store_dir: str,
    interval: str,
    sbcap_path: str,
    tagged_only: bool = False,
) -> AccrualSummary:
    """
    Build bars for one symbol across `dates`, one day at a time, and
    append-merge into its parquet store. Raises SbcapError if any day's
    `sbcap bars` invocation fails — the caller (build_bars) decides whether
    that sinks the whole symbol.
    """
    existing = _load_existing(symbol, store_dir)
    rows_before = len(existing) if existing is not None else 0

    day_frames: list[pd.DataFrame] = []
    for date_str in dates:
        segment_paths = _discover_segments_for_date(segments_root, date_str)
        if not segment_paths:
            continue
        day_df = _run_sbcap_bars(segment_paths, symbol, interval, sbcap_path, tagged_only=tagged_only)
        if not day_df.empty:
            day_frames.append(day_df)

    new_df = pd.concat(day_frames, ignore_index=True) if day_frames else pd.DataFrame()
    merged = _merge_dedup(existing, new_df)

    if not merged.empty:
        path = _store_path(symbol, store_dir)
        os.makedirs(store_dir, exist_ok=True)
        merged.to_parquet(path, engine=_PARQUET_ENGINE, index=False)

    rows_after = len(merged)
    new_rows = max(0, rows_after - rows_before)
    dr = _date_range(merged)

    logger.info(
        "%s: %d -> %d rows (+%d new), %s - %s",
        symbol, rows_before, rows_after, new_rows, dr[0], dr[1],
    )

    return AccrualSummary(
        symbol=symbol,
        rows_before=rows_before,
        rows_after=rows_after,
        new_rows=new_rows,
        date_range=dr,
    )


# ---------------------------------------------------------------------------
# Public multi-symbol API
# ---------------------------------------------------------------------------

def build_bars(
    symbols: list[str] | tuple[str, ...],
    segments_root: str,
    store_dir: str,
    interval: str = _DEFAULT_INTERVAL,
    sbcap_path: str = _DEFAULT_SBCAP_PATH,
    from_date: str | None = None,
    to_date: str | None = None,
    tagged_only: bool = False,
) -> dict[str, AccrualSummary | BuildError]:
    """
    Build tick bars for each symbol over the discovered segment days and
    append-merge into `<store_dir>/<SYMBOL>.parquet`.

    Parameters
    ----------
    symbols:        Ticker symbols to build.
    segments_root:  Root of the `dt=YYYY-MM-DD/<watchlist_id>/seg-*.binpb.zst`
                     tree (e.g. "segments").
    store_dir:      Directory for per-symbol parquet files. Created if absent.
                     Caller's responsibility to point this at a directory
                     matching `tagged_only` (see module docstring) — this
                     function does not rename or namespace the directory for
                     you, so building both variants into the same store_dir
                     will overwrite one with the other.
    interval:       Bar width passed to `sbcap bars --interval` (default "1m").
    sbcap_path:     Path to the built sbcap binary (default "bin/sbcap").
    from_date, to_date:
                     Optional inclusive ISO date bounds ("YYYY-MM-DD") on
                     which segment days to read.
    tagged_only:    When True, passes `--tagged-only` to sbcap, dropping
                     untagged (negotiated/crossing) trades — book-accessible
                     prices only. See the module docstring: this changes
                     open/high/low/close, not just volume, and cannot be
                     recovered from a full-tape build after the fact.

    Returns
    -------
    dict mapping symbol -> AccrualSummary on success, or BuildError when
    `sbcap bars` failed for that symbol. Every requested symbol is present
    in the mapping — a symbol with nothing new is still reported, never
    silently dropped.
    """
    os.makedirs(store_dir, exist_ok=True)
    dates = _discover_dates(segments_root, from_date, to_date)

    results: dict[str, AccrualSummary | BuildError] = {}
    for symbol in symbols:
        try:
            results[symbol] = _build_symbol(
                symbol, dates, segments_root, store_dir, interval, sbcap_path,
                tagged_only=tagged_only,
            )
        except SbcapError as exc:
            logger.error("Failed to build tick bars for %s: %s", symbol, exc)
            results[symbol] = BuildError(symbol=symbol, message=str(exc))

    return results


def build_both_stores(
    symbols: list[str] | tuple[str, ...],
    segments_root: str,
    store_dir: str,
    tagged_store_dir: str,
    interval: str = _DEFAULT_INTERVAL,
    sbcap_path: str = _DEFAULT_SBCAP_PATH,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, dict[str, AccrualSummary | BuildError]]:
    """
    Build the full-tape store and the tagged-only store together, so they
    are always built from the same segment set and cannot drift out of sync
    (see the module docstring for why one cannot be derived from the other).

    Parameters
    ----------
    store_dir:         Full-tape store directory (no sbcap flag).
    tagged_store_dir:  Tagged-only store directory (`sbcap bars --tagged-only`).
    All other parameters are as in build_bars.

    Returns
    -------
    {"full": {symbol: AccrualSummary | BuildError, ...},
     "tagged": {symbol: AccrualSummary | BuildError, ...}}
    """
    return {
        "full": build_bars(
            symbols, segments_root, store_dir, interval=interval, sbcap_path=sbcap_path,
            from_date=from_date, to_date=to_date, tagged_only=False,
        ),
        "tagged": build_bars(
            symbols, segments_root, tagged_store_dir, interval=interval, sbcap_path=sbcap_path,
            from_date=from_date, to_date=to_date, tagged_only=True,
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _require_symbols(raw: str) -> list[str]:
    symbols = _parse_symbols(raw)
    if not symbols:
        print("ERROR: no symbols provided", file=sys.stderr)
        sys.exit(1)
    return symbols


def _print_build_summary(
    symbols: list[str],
    results: dict[str, AccrualSummary | BuildError],
    label: str | None = None,
) -> int:
    """Print one line per symbol; return the number that errored."""
    prefix = f"[{label}] " if label else ""
    num_failed = 0
    for sym in symbols:
        result = results[sym]
        if isinstance(result, BuildError):
            num_failed += 1
            print(f"  {prefix}{sym}: ERRORED - {result.message}")
        elif result.new_rows == 0:
            print(
                f"  {prefix}{sym}: no new data ({result.rows_after} rows total) "
                f"| {result.date_range[0]} - {result.date_range[1]}"
            )
        else:
            print(
                f"  {prefix}{sym}: {result.rows_before} -> {result.rows_after} rows "
                f"(+{result.new_rows} new) | {result.date_range[0]} - {result.date_range[1]}"
            )
    return num_failed


def _add_common_build_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbols", required=True, help="Comma-separated ticker list, e.g. PTRO,BRPT")
    p.add_argument(
        "--segments-root",
        default="segments",
        help="Root of the dt=YYYY-MM-DD/<watchlist_id>/ segment tree (default: segments)",
    )
    p.add_argument(
        "--interval",
        default=_DEFAULT_INTERVAL,
        help="Bar width passed to sbcap bars --interval (default: 1m)",
    )
    p.add_argument(
        "--sbcap-path",
        default=_DEFAULT_SBCAP_PATH,
        help=f"Path to the built sbcap binary (default: {_DEFAULT_SBCAP_PATH})",
    )
    p.add_argument("--from-date", default=None, help="Inclusive start date YYYY-MM-DD")
    p.add_argument("--to-date", default=None, help="Inclusive end date YYYY-MM-DD")


def main(argv: list[str] | None = None) -> None:
    """
    CLI entrypoint:
        python -m screener.tick_bars build [--tagged-only] [options]
        python -m screener.tick_bars build-both [options]

    `build` writes ONE store at --store-dir; pass --tagged-only for the
    tagged-only substrate and point --store-dir at a SEPARATE directory
    (e.g. data/tick_bars_tagged) — see the module docstring for why this is
    a different substrate, not a filtered view.

    `build-both` builds the full-tape store and the tagged-only store in one
    invocation, guaranteeing they cover the same symbol/day set.

    Exit code is non-zero only when EVERY requested symbol failed to build
    (an sbcap error) for `build`, or for at least one of the two stores for
    `build-both` — a symbol with no new data is a successful, reported
    outcome, not a failure.
    """
    parser = argparse.ArgumentParser(
        prog="python -m screener.tick_bars",
        description="Build per-symbol tick bars from captured segments into local parquet store(s).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="Build/accrue tick bars for symbols into ONE store")
    _add_common_build_args(build_p)
    build_p.add_argument(
        "--store-dir",
        default="data/tick_bars",
        help="Directory for parquet store files (default: data/tick_bars, the full-tape store)",
    )
    build_p.add_argument(
        "--tagged-only",
        action="store_true",
        default=False,
        help=(
            "Drop untagged (negotiated/crossing) trades — book-accessible prices only. "
            "Point --store-dir at a separate directory (e.g. data/tick_bars_tagged) when "
            "using this; see the module docstring."
        ),
    )

    both_p = sub.add_parser(
        "build-both",
        help="Build the full-tape store AND the tagged-only store together, so they cannot drift apart",
    )
    _add_common_build_args(both_p)
    both_p.add_argument(
        "--store-dir",
        default="data/tick_bars",
        help="Directory for the full-tape store (default: data/tick_bars)",
    )
    both_p.add_argument(
        "--tagged-store-dir",
        default="data/tick_bars_tagged",
        help="Directory for the tagged-only store (default: data/tick_bars_tagged)",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.command == "build":
        symbols = _require_symbols(args.symbols)
        results = build_bars(
            symbols,
            segments_root=args.segments_root,
            store_dir=args.store_dir,
            interval=args.interval,
            sbcap_path=args.sbcap_path,
            from_date=args.from_date,
            to_date=args.to_date,
            tagged_only=args.tagged_only,
        )
        num_failed = _print_build_summary(symbols, results)
        print(f"Done: {len(symbols) - num_failed}/{len(symbols)} symbol(s) built successfully")
        if symbols and num_failed == len(symbols):
            print(f"ERROR: all {len(symbols)} symbol(s) failed to build", file=sys.stderr)
            sys.exit(1)

    elif args.command == "build-both":
        symbols = _require_symbols(args.symbols)
        both = build_both_stores(
            symbols,
            segments_root=args.segments_root,
            store_dir=args.store_dir,
            tagged_store_dir=args.tagged_store_dir,
            interval=args.interval,
            sbcap_path=args.sbcap_path,
            from_date=args.from_date,
            to_date=args.to_date,
        )
        full_failed = _print_build_summary(symbols, both["full"], label="full")
        tagged_failed = _print_build_summary(symbols, both["tagged"], label="tagged")
        print(
            f"Done: full {len(symbols) - full_failed}/{len(symbols)}, "
            f"tagged {len(symbols) - tagged_failed}/{len(symbols)} symbol(s) built successfully"
        )
        if symbols and (full_failed == len(symbols) or tagged_failed == len(symbols)):
            print(
                f"ERROR: all {len(symbols)} symbol(s) failed to build for at least one store",
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
