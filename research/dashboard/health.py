"""
dashboard/health.py — the Overview's "is the machine working" table.

Four rows, each green / amber / red / not-checked, each with a one-line plain reason.
A row never says a bare "unknown": if a check could not run, the row says why and gives
the exact command to run (``not_checked_row``).

What is judged and by whom:
  * collector + S3 rows  — scripts/collector_healthcheck.py's own findings and exit code
  * daily price data     — the newest date inside research/data/daily/*.parquet
  * last bot run         — the newest run header in the post-trade stores
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Sequence

import pandas as pd

from dashboard import config
from dashboard.inputs import HealthOutcome
from dashboard.store import RunHeader

GREEN, AMBER, RED, NOT_CHECKED = "green", "amber", "red", "not_checked"

DAILY_REFRESH_HINT = "set -a && source .env.local && set +a && make refresh-prices   (refreshes every stored symbol; needs SB_BEARER_TOKEN)"

HEARTBEAT_STALE_S = 120
"""A live run that has not rewritten its status file for this long has stopped."""


@dataclass(frozen=True)
class HealthRow:
    name: str
    state: str
    summary: str
    """One plain sentence: what is true and what it means."""
    run_hint: str | None = None
    """For not-checked rows: the exact thing to run."""


def not_checked_row(name: str, reason: str, command: str = config.HEALTHCHECK_RUN_HINT) -> HealthRow:
    """The only way a row may say it does not know."""
    return HealthRow(name, NOT_CHECKED, f"Not checked: {reason}. Run: {command}", run_hint=command)


def wib(ns: int) -> datetime:
    """A nanosecond epoch as Jakarta wall-clock time."""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc) + timedelta(hours=config.WIB_OFFSET_HOURS)


def _age_days(day: date, today: date) -> int:
    return (today - day).days


def _freshness_state(age_days: int) -> str:
    if age_days <= config.FRESH_DAYS:
        return GREEN
    return AMBER if age_days <= config.STALE_DAYS else RED


# ---------------------------------------------------------------------------
# Collector capture + S3 freshness (from the health check)
# ---------------------------------------------------------------------------

def collector_row(outcome: HealthOutcome) -> HealthRow:
    name = "Market-data collector"
    if not outcome.checked:
        return not_checked_row(name, outcome.reason or "the health check did not run")
    doc = outcome.doc or {}
    days = doc.get("days") or []
    trading = [d for d in days if d.get("status") != "skipped_non_trading_day"]
    bad = [d for d in trading if d.get("status") != "ok"]
    exit_code = int(doc.get("exit_code", 0))
    if bad:
        last_bad = max(d.get("date", "") for d in bad)
        return HealthRow(
            name,
            RED if exit_code >= 2 else AMBER,
            f"No usable market data on {len(bad)} of the last {len(trading)} trading days "
            f"(most recent bad day: {last_bad}).",
        )
    if exit_code == 1:
        msgs = "; ".join(str(f.get("message", "")) for f in (doc.get("findings") or [])[:2])
        return HealthRow(name, AMBER, f"Recording data every day, with a warning: {msgs}")
    if exit_code >= 2:
        msgs = "; ".join(str(f.get("message", "")) for f in (doc.get("findings") or [])[:2])
        return HealthRow(name, RED, f"The collector program on the server looks out of date: {msgs}")
    return HealthRow(
        name, GREEN, f"Recorded real market data on all {len(trading)} of the last {len(trading)} trading days."
    )


def s3_row(outcome: HealthOutcome, today: date) -> HealthRow:
    name = "Market data in S3 (cloud storage)"
    if not outcome.checked:
        return not_checked_row(name, outcome.reason or "the health check did not run")
    days = (outcome.doc or {}).get("days") or []
    ok_days = sorted(d["date"] for d in days if d.get("status") == "ok")
    if not ok_days:
        return HealthRow(name, RED, "No day with usable data was found in S3 in the lookback window.")
    newest = date.fromisoformat(ok_days[-1])
    age = _age_days(newest, today)
    return HealthRow(
        name,
        _freshness_state(age),
        f"Newest good day of data is {newest.isoformat()} ({age} day{'s' if age != 1 else ''} ago).",
    )


# ---------------------------------------------------------------------------
# Daily price data (research/data/daily)
# ---------------------------------------------------------------------------

def daily_data_row(daily_dir: str, today: date) -> HealthRow:
    name = "Daily price history (used by backtests and charts)"
    files = sorted(glob.glob(os.path.join(daily_dir, "*.parquet")))
    if not files:
        return not_checked_row(
            name,
            f"no daily price files were found in {daily_dir}",
            DAILY_REFRESH_HINT,
        )
    last: dict[str, date] = {}
    for path in files:
        try:
            newest = pd.read_parquet(path, columns=["date"])["date"].astype(str).max()
            last[os.path.basename(path)[:-8]] = date.fromisoformat(newest)
        except (OSError, ValueError, KeyError):
            continue
    if not last:
        return not_checked_row(name, "the daily price files could not be read", "check research/data/daily/")
    newest_all = max(last.values())
    current = [s for s, d in last.items() if (newest_all - d).days <= config.FRESH_DAYS]
    behind = len(last) - len(current)
    age = _age_days(newest_all, today)
    if age > config.STALE_DAYS:
        return HealthRow(
            name, RED, f"Newest price on file is {newest_all.isoformat()}, {age} days ago.", DAILY_REFRESH_HINT
        )
    if behind == 0:
        return HealthRow(name, _freshness_state(age), f"All {len(last)} stocks are up to {newest_all.isoformat()}.")
    behind_dates = [d for s, d in last.items() if s not in set(current)]
    typical = max(set(behind_dates), key=behind_dates.count)
    state = RED if behind * 2 > len(last) else AMBER
    return HealthRow(
        name,
        state,
        f"Newest price is {newest_all.isoformat()}, but {behind} of {len(last)} stocks stop earlier "
        f"(most at {typical.isoformat()}). Backtests and charts for those stocks end there.",
        DAILY_REFRESH_HINT,
    )


def daily_data_newest(daily_dir: str) -> dict[str, str]:
    """Last date on file per symbol, as ISO strings (for charts that must say where data ends)."""
    out: dict[str, str] = {}
    for path in glob.glob(os.path.join(daily_dir, "*.parquet")):
        try:
            out[os.path.basename(path)[:-8]] = str(pd.read_parquet(path, columns=["date"])["date"].astype(str).max())
        except (OSError, ValueError, KeyError):
            continue
    return out


# ---------------------------------------------------------------------------
# Last bot run
# ---------------------------------------------------------------------------

def bot_row(
    runs: Sequence[RunHeader],
    status: Mapping | None,
    now: datetime,
    stale_after_days: int = 7,
) -> HealthRow:
    name = "Trading bot (last run)"
    if not runs:
        return HealthRow(name, AMBER, "The bot has not run yet: no run records were found.")
    latest = max(runs, key=lambda r: r.started_ns)
    started = wib(latest.started_ns)
    age_days = (now - datetime.fromtimestamp(latest.started_ns / 1e9, tz=timezone.utc)).days
    when = started.strftime("%Y-%m-%d %H:%M WIB")
    mode = "real-money" if latest.sink_kind == "live" else "practice (paper)"
    if latest.outcome == "error":
        return HealthRow(name, RED, f"The last run ({when}, {mode}) failed: {latest.error or 'no message'}.")
    if latest.outcome == "running":
        beat = _heartbeat_age(status, latest.run_id, now)
        if beat is not None and beat <= HEARTBEAT_STALE_S:
            return HealthRow(name, GREEN, f"Running now since {when} ({mode}).")
        return HealthRow(
            name,
            AMBER,
            f"The last run ({when}, {mode}) never recorded an ending: it probably crashed or was stopped.",
        )
    if age_days > stale_after_days:
        return HealthRow(name, AMBER, f"Last run was {when} ({mode}), {age_days} days ago.")
    return HealthRow(name, GREEN, f"Last run {when} ({mode}) finished normally.")


def _heartbeat_age(status: Mapping | None, run_id: str, now: datetime) -> float | None:
    if not status or status.get("run_id") != run_id or not status.get("wall_clock_ns"):
        return None
    return now.timestamp() - float(status["wall_clock_ns"]) / 1e9


def health_rows(
    outcome: HealthOutcome,
    daily_dir: str,
    runs: Sequence[RunHeader],
    status: Mapping | None,
    now: datetime,
) -> list[HealthRow]:
    today = (now + timedelta(hours=config.WIB_OFFSET_HOURS)).date()
    return [
        collector_row(outcome),
        s3_row(outcome, today),
        daily_data_row(daily_dir, today),
        bot_row(runs, status, now),
    ]
