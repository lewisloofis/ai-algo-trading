#!/usr/bin/env python3
"""
Reads calendar/deadlines.yaml and prints items due within the lead window,
then reports data-freshness for the real on-disk data stores.

Usage:
    python calendar/check_deadlines.py [--lead N]

Exit code 1 if any deadline is DUE SOON/OVERDUE or any data source is
STALE/DATA LOST.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path
_DEADLINES_PATH = Path(__file__).parent / "deadlines.yaml"
_REPO_ROOT = Path(__file__).parent.parent
_RESEARCH_DIR = _REPO_ROOT / "research"

# How many calendar days may pass since the intraday (chartbit) store's last
# data point before it is flagged STALE. See W1.2
# (docs/plans/00-roadmap-research-to-live.md §6 Wave 1).
INTRADAY_STALENESS_THRESHOLD_DAYS: int = 7

# The chartbit intraday endpoint serves a rolling ~90-day window. A store
# staler than this has not just gone quiet — it has already lost data
# permanently, since chartbit does not backfill past its window.
CHARTBIT_ROLLING_WINDOW_DAYS: int = 90

# How many calendar days may pass since a tick-segment symbol's last
# confirmed trade coverage before it is flagged STALE. Separate constant
# from the intraday one on purpose: the local `segments/` checkout is a
# partial mirror by design (S3 holds the full, authoritative history, and
# typically only a handful of recent sessions are pulled down locally), so
# by default staleness here is reported for visibility but does NOT fail
# the exit code — see `--segments-authoritative`.
TICK_SEGMENTS_STALENESS_THRESHOLD_DAYS: int = 7


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import]
        with path.open() as f:
            return yaml.safe_load(f)
    except ImportError:
        return _parse_yaml_simple(path)


def _parse_yaml_simple(path: Path) -> dict:
    """Minimal YAML parser covering only the deadlines.yaml schema."""
    lines = path.read_text().splitlines()
    result: dict = {"deadlines": []}
    current: dict | None = None
    desc_lines: list[str] = []
    in_desc = False

    def _flush_desc() -> None:
        nonlocal desc_lines, in_desc
        if current is not None and desc_lines:
            current["desc"] = " ".join(desc_lines).strip()
        desc_lines = []
        in_desc = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "deadlines:":
            continue

        if stripped.startswith("- id:"):
            _flush_desc()
            if current is not None:
                result["deadlines"].append(current)
            current = {"id": stripped[len("- id:"):].strip()}
            in_desc = False
            continue

        if current is None:
            continue

        if stripped.startswith("due:"):
            _flush_desc()
            current["due"] = stripped[len("due:"):].strip().strip('"')
        elif stripped.startswith("lead_days:"):
            _flush_desc()
            current["lead_days"] = int(stripped[len("lead_days:"):].strip())
        elif stripped.startswith("desc:"):
            _flush_desc()
            in_desc = True
            rest = stripped[len("desc:"):].strip().lstrip(">").strip()
            if rest:
                desc_lines.append(rest)
        elif in_desc:
            desc_lines.append(stripped)

    _flush_desc()
    if current is not None:
        result["deadlines"].append(current)

    return result


def _check(lead_days: int) -> int:
    data = _load_yaml(_DEADLINES_PATH)
    today = datetime.date.today()
    any_alert = False

    for item in data.get("deadlines", []):
        item_id = item.get("id", "?")
        due_str = item.get("due", "")
        item_lead = int(item.get("lead_days", lead_days))
        desc = item.get("desc", "").strip()

        try:
            due = datetime.date.fromisoformat(due_str)
        except ValueError:
            print(f"  SKIP  {item_id}: unparseable due date {due_str!r}")
            continue

        days_until = (due - today).days

        if days_until < 0:
            status = "OVERDUE"
            any_alert = True
        elif days_until <= item_lead:
            status = "DUE SOON"
            any_alert = True
        else:
            status = "OK"

        print(f"  {status:<9} {item_id}  (due {due_str}, {days_until}d away)")
        if status != "OK":
            first_line = desc.split(".")[0] if desc else ""
            if first_line:
                print(f"             {first_line}.")

    return 1 if any_alert else 0


def _freshness_line(
    symbol: str,
    granularity: str,
    last_date: datetime.date | None,
    distinct_trading_days: int,
    today: datetime.date,
    is_chartbit: bool,
) -> bool:
    """Print one data-freshness line; return True if it should raise an alert."""
    if last_date is None:
        print(f"  SKIP      {symbol} ({granularity}): no data on disk")
        return False

    stale_days = (today - last_date).days
    threshold = INTRADAY_STALENESS_THRESHOLD_DAYS if is_chartbit else TICK_SEGMENTS_STALENESS_THRESHOLD_DAYS

    if is_chartbit and stale_days > CHARTBIT_ROLLING_WINDOW_DAYS:
        status = "DATA LOST"
        any_alert = True
    elif stale_days > threshold:
        status = "STALE"
        any_alert = True
    else:
        status = "OK"
        any_alert = False

    print(
        f"  {status:<9} {symbol} ({granularity}): last={last_date} "
        f"({stale_days}d ago, {distinct_trading_days} trading days on file)"
    )
    if status == "DATA LOST":
        print(
            f"             chartbit's rolling ~{CHARTBIT_ROLLING_WINDOW_DAYS}-day window has "
            "already dropped data older than this point — the gap is PERMANENT; "
            "re-pulling now cannot recover it."
        )
    return any_alert


def _check_freshness(segments_root: Path | None = None, segments_authoritative: bool = False) -> bool:
    """Report last-data-date and flag staleness for each real data source.

    Imports `lib.data_inventory` (research/lib/data_inventory.py) lazily and
    degrades gracefully — a SKIP line, not a crash — if it or its pyarrow
    dependency is unavailable in the current interpreter. Never imports
    pandas. Returns True if any source should raise an alert.

    Tick-segment freshness is reported per symbol via
    `InventoryReport.symbol_trade_coverage()`, which only counts a session
    toward a symbol when that session's manifest actually names the
    `runningTradeBatch` channel for it — a watchlist directory existing on
    disk is not proof a symbol has usable trade data (see
    lib.data_inventory.symbol_coverage).

    `segments_root`/`segments_authoritative`: the default `segments/` tree
    is a PARTIAL local checkout by design — S3 holds the full, continuously
    updated history, and only a handful of recent sessions are typically
    pulled down locally. Tick-segment staleness is therefore reported (for
    visibility) but does not affect the return value unless the caller
    passes `segments_authoritative=True` — set that only once `segments_root`
    points at a copy that is actually kept in sync with S3.
    """
    if str(_RESEARCH_DIR) not in sys.path:
        sys.path.insert(0, str(_RESEARCH_DIR))

    try:
        from lib.data_inventory import DEFAULT_REQUIRED_CHANNEL, build_inventory
    except ImportError as exc:
        print(f"\nData freshness:\n  SKIP      data-freshness check unavailable: {exc}")
        return False

    try:
        build_kwargs = {}
        if segments_root is not None:
            build_kwargs["segments_root"] = segments_root
        report = build_inventory(**build_kwargs)
    except ImportError as exc:
        # scan_intraday_store imports pyarrow lazily; missing it degrades
        # this check instead of crashing the whole deadline run.
        print(f"\nData freshness:\n  SKIP      data-freshness check unavailable: {exc}")
        return False

    print("\nData freshness:")
    today = datetime.date.today()
    any_alert = False

    if not report.intraday_bars:
        print("  SKIP      intraday bar store: no parquet files found on disk")
    for cov in report.intraday_bars:
        any_alert = _freshness_line(
            cov.symbol, cov.granularity, cov.last_date, cov.distinct_trading_days, today, is_chartbit=True
        ) or any_alert

    unknown_sessions = [s for s in report.tick_segments if s.unknown_coverage]
    for session in unknown_sessions:
        print(
            f"  WARN      {session.watchlist_id} on {session.date}: unknown coverage "
            "(missing or unparseable manifest) — not counted as confirmed data"
        )
    any_alert = any_alert or bool(unknown_sessions)

    trade_coverage = report.symbol_trade_coverage()
    if not report.tick_segments:
        print("  SKIP      tick segments: no captured segments found on disk")
    elif not trade_coverage:
        print(
            f"  STALE     tick segments: no session on disk carries the "
            f"{DEFAULT_REQUIRED_CHANNEL!r} channel — zero symbols have confirmed trade coverage"
        )
        any_alert = any_alert or segments_authoritative
    else:
        if not segments_authoritative:
            print(
                "  NOTE      segments/ is a partial local checkout — S3 is authoritative; "
                "STALE below is expected and does NOT fail this check "
                "(pass --segments-authoritative once --segments-root is an S3-synced copy)"
            )
        for cov in trade_coverage:
            is_alert = _freshness_line(
                cov.symbol,
                f"tick_trade[{cov.required_channel}]",
                cov.last_date,
                cov.distinct_trading_days,
                today,
                is_chartbit=False,
            )
            if segments_authoritative:
                any_alert = any_alert or is_alert

    return any_alert


def main() -> None:
    parser = argparse.ArgumentParser(description="Check operational deadlines.")
    parser.add_argument("--lead", type=int, default=30, help="Alert window in days (default: 30)")
    parser.add_argument(
        "--segments-root",
        type=Path,
        default=None,
        help=(
            "Override the tick-segments directory to scan (default: repo segments/, "
            "a partial local checkout). Point this at an S3-synced mirror for a check "
            "that can meaningfully fail on staleness."
        ),
    )
    parser.add_argument(
        "--segments-authoritative",
        action="store_true",
        help=(
            "Treat tick-segment staleness as a hard failure. Use only when "
            "--segments-root points at the S3-authoritative copy, not a partial local "
            "checkout — otherwise this exits 1 on a healthy repo."
        ),
    )
    args = parser.parse_args()
    deadlines_alert = _check(args.lead) != 0
    freshness_alert = _check_freshness(
        segments_root=args.segments_root, segments_authoritative=args.segments_authoritative
    )
    sys.exit(1 if (deadlines_alert or freshness_alert) else 0)


if __name__ == "__main__":
    main()
