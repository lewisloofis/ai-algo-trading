"""
lib/calendar_reconcile.py — Ground-truth reconciliation validator (plan-14 §7.9).

Pulls ~1yr of chartbit daily bars for a set of liquid IDX names, takes the
union of dates that have bars as the "observed IDX trading calendar", then diffs
it against what market_calendar.py predicts for the same date range.

Two finding categories:
  calendar_says_trading_market_silent
      Our calendar predicts a trading day but chartbit has no bar for ANY of
      the probe symbols.  Possible causes: a missing holiday (or ad-hoc
      exchange closure) that IDX announced after the file was published.

  calendar_says_holiday_market_traded
      Our calendar predicts a holiday (or weekend) but chartbit has a bar for
      at least one probe symbol on that date.  Possible cause: a wrong date in
      the file (transcription error).

Zero findings = PASS.  Any findings = list with date and category.

Usage (from the research/ directory)::

    python -m lib.calendar_reconcile --symbols BBRI,BBCA,TLKM
    python -m lib.calendar_reconcile --symbols BBRI,BBCA,TLKM --start 2025-01-01 --end 2026-06-27
    python -m lib.calendar_reconcile --symbols BBRI,BBCA,TLKM --json

Requirements:
    SB_BEARER_TOKEN env var must be set (same as screen_universe.py LiveDataSource).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from lib.market_calendar import CALENDAR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS: list[str] = ["BBRI", "BBCA", "TLKM"]
"""
Default probe symbols.  These are among the most liquid names on IDX and
almost never have data gaps — making them the best ground truth for whether
the exchange was open on a given day.
"""

# How many calendar days back to fetch (≈ 1 year of trading history).
DEFAULT_LOOKBACK_DAYS: int = 400

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

Category = Literal[
    "calendar_says_trading_market_silent",
    "calendar_says_holiday_market_traded",
]


@dataclass(frozen=True)
class CalendarDiff:
    """One divergence between the predicted and observed IDX trading calendar."""

    date: date
    category: Category
    detail: str


# ---------------------------------------------------------------------------
# Core reconciliation logic
# ---------------------------------------------------------------------------

def _fetch_observed_dates(
    symbols: list[str],
    start: str,
    end: str,
) -> set[date]:
    """
    Pull daily bars from chartbit for each symbol and return the union of dates
    that have at least one bar as the "observed IDX trading calendar".

    Parameters
    ----------
    symbols : list[str]
        Probe symbols (e.g. ["BBRI", "BBCA", "TLKM"]).
    start, end : str
        Date range in YYYY-MM-DD format.  start is the older bound, end the newer.

    Returns
    -------
    set[date]
        Dates on which chartbit returned a bar for at least one probe symbol.
    """
    # Import here so the module loads cleanly even without the token (e.g. in
    # pure-calendar unit tests).
    try:
        from screener.screen_universe import LiveDataSource
    except ImportError as exc:
        raise ImportError(
            "screener.screen_universe.LiveDataSource is required for live data fetching. "
            f"Original error: {exc}"
        ) from exc

    token = os.environ.get("SB_BEARER_TOKEN")
    if not token:
        raise EnvironmentError(
            "SB_BEARER_TOKEN env var must be set to fetch live data from chartbit.\n"
            "Obtain a bearer token via the Stockbit auth flow (see docs/auth/) and "
            "export it before running this script:\n\n"
            "    export SB_BEARER_TOKEN=<your_token>"
        )

    ds = LiveDataSource(bearer_token=token)
    observed: set[date] = set()

    for symbol in symbols:
        logger.info("Fetching daily bars for %s (%s → %s) …", symbol, start, end)
        try:
            df = ds.daily_history(symbol, start, end)
        except Exception as exc:
            logger.warning("Failed to fetch history for %s: %s", symbol, exc)
            continue
        if df.empty:
            logger.warning("No bars returned for %s", symbol)
            continue
        for d_str in df["date"].astype(str):
            try:
                observed.add(date.fromisoformat(d_str))
            except ValueError:
                logger.warning("Could not parse date %r from %s bars", d_str, symbol)
        logger.info("  → %d bars, %d unique dates", len(df), len(observed))

    return observed


def reconcile(
    symbols: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[CalendarDiff]:
    """
    Run the ground-truth reconciliation.

    Parameters
    ----------
    symbols : list[str] or None
        Probe symbols.  Defaults to DEFAULT_SYMBOLS (BBRI, BBCA, TLKM).
    start : str or None
        Start date (YYYY-MM-DD).  Defaults to ``today - lookback_days``.
    end : str or None
        End date (YYYY-MM-DD).  Defaults to today.
    lookback_days : int
        Calendar days back from today when start is not provided.

    Returns
    -------
    list[CalendarDiff]
        Sorted list of divergences.  Empty list = PASS.
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    today = date.today()
    end_date = date.fromisoformat(end) if end else today
    start_date = (
        date.fromisoformat(start)
        if start
        else end_date - timedelta(days=lookback_days)
    )

    logger.info(
        "Reconciling %s → %s against %d probe symbols: %s",
        start_date, end_date, len(symbols), symbols,
    )

    # Observed calendar from chartbit (union of dates with bars).
    observed = _fetch_observed_dates(
        symbols=symbols,
        start=start_date.isoformat(),
        end=end_date.isoformat(),
    )
    logger.info("Observed %d unique trading dates in chartbit data", len(observed))

    # Predicted calendar from market_calendar.py.
    predicted_trading: set[date] = set()
    d = start_date
    while d <= end_date:
        if CALENDAR.is_trading_day(d):
            predicted_trading.add(d)
        d += timedelta(days=1)

    logger.info("Calendar predicts %d trading days in this range", len(predicted_trading))

    diffs: list[CalendarDiff] = []

    # Category 1: calendar says trading but chartbit is silent.
    # A single probe date being absent from ALL symbols' data suggests the
    # exchange was actually closed (missing holiday or ad-hoc closure).
    silent_days = predicted_trading - observed
    for d in sorted(silent_days):
        diffs.append(CalendarDiff(
            date=d,
            category="calendar_says_trading_market_silent",
            detail=(
                f"Calendar predicts trading on {d} but none of {symbols} "
                "returned a bar from chartbit"
            ),
        ))

    # Category 2: calendar says holiday/weekend but chartbit has a bar.
    # This indicates a wrong date in the file (transcription error) because
    # chartbit actually served data on a day we labeled non-trading.
    extra_days = observed - predicted_trading
    for d in sorted(extra_days):
        # Distinguish weekends from holidays for the detail message.
        if d.weekday() >= 5:
            detail = (
                f"Chartbit has bars on {d} ({d.strftime('%A')}) "
                "but the weekmask marks weekends as non-trading"
            )
        else:
            detail = (
                f"Chartbit has bars on {d} but market_calendar marks it as "
                "a holiday — possible wrong date in idx_holidays.json"
            )
        diffs.append(CalendarDiff(
            date=d,
            category="calendar_says_holiday_market_traded",
            detail=detail,
        ))

    return sorted(diffs, key=lambda x: x.date)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit_report(diffs: list[CalendarDiff], start: date, end: date, symbols: list[str]) -> None:
    """Print a human-readable reconciliation report to stdout."""
    print(f"\nCalendar reconciliation: {start} → {end}")
    print(f"Probe symbols: {', '.join(symbols)}")
    print("-" * 60)
    if not diffs:
        print("PASS — zero divergences between market_calendar.py and chartbit")
        return

    print(f"FAIL — {len(diffs)} divergence(s) found:\n")
    silent = [d for d in diffs if d.category == "calendar_says_trading_market_silent"]
    traded = [d for d in diffs if d.category == "calendar_says_holiday_market_traded"]

    if silent:
        print(f"  calendar_says_trading_market_silent ({len(silent)}):")
        for diff in silent:
            print(f"    {diff.date}  — {diff.detail}")
        print()

    if traded:
        print(f"  calendar_says_holiday_market_traded ({len(traded)}):")
        for diff in traded:
            print(f"    {diff.date}  — {diff.detail}")
        print()

    print("Action: update calendar/idx_holidays.json to resolve divergences.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Ground-truth reconciliation: diff market_calendar.py predictions "
            "against observed chartbit daily bars for liquid IDX names."
        )
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help=(
            f"Comma-separated probe symbols (default: {','.join(DEFAULT_SYMBOLS)}). "
            "Use the most liquid names for reliable ground truth."
        ),
    )
    parser.add_argument(
        "--start",
        default=None,
        help="Start date YYYY-MM-DD (default: today minus --lookback-days).",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date YYYY-MM-DD (default: today).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Calendar days to look back when --start is omitted (default: {DEFAULT_LOOKBACK_DAYS}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output instead of the human-readable report.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    today = date.today()
    end_date = date.fromisoformat(args.end) if args.end else today
    start_date = (
        date.fromisoformat(args.start)
        if args.start
        else end_date - timedelta(days=args.lookback_days)
    )

    try:
        diffs = reconcile(
            symbols=symbols,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
    except EnvironmentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        logger.error("Reconciliation failed: %s", exc, exc_info=True)
        sys.exit(1)

    if args.json:
        output = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "symbols": symbols,
            "pass": len(diffs) == 0,
            "diffs": [
                {
                    "date": d.date.isoformat(),
                    "category": d.category,
                    "detail": d.detail,
                }
                for d in diffs
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        _emit_report(diffs, start_date, end_date, symbols)

    # Exit 1 when divergences exist so the script is CI-friendly.
    sys.exit(0 if not diffs else 1)


if __name__ == "__main__":
    main()
