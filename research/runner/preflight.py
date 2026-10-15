"""08:45 preflight. Env checks are presence-only; token expiry is reported as hours, never a value."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from lib.market_calendar import CALENDAR
from runner import token_expiry
from runner.config import ACCESS_TOKEN_ENV, BEARER_TOKEN_ENV, Config
from runner.strategies import select

FAIL, WARN, OK = "fail", "warn", "ok"


@dataclass(frozen=True)
class Check:
    name: str
    level: str          # ok | warn | fail
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    day: str
    trading_day: bool
    checks: Tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return self.trading_day and not any(c.level == FAIL for c in self.checks)

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if c.level == FAIL]

    @property
    def warnings(self) -> List[Check]:
        return [c for c in self.checks if c.level == WARN]


def _default_load_daily(symbol: str, store_dir: str):
    from screener.daily_store import load_daily
    return load_daily(symbol, store_dir)


def check_prev_bar(symbol: str, prev_day: date, daily_dir: Path,
                   load: Callable = _default_load_daily) -> Check:
    name = f"daily bar {symbol}"
    df = load(symbol, str(daily_dir))
    if df is None or len(df) == 0:
        return Check(name, FAIL, f"no stored daily bars for {symbol} (run `make refresh-prices`)")
    last = df.iloc[-1]
    last_date = last["date"]
    last_date = last_date.date() if hasattr(last_date, "date") and not isinstance(last_date, date) else last_date
    if last_date < prev_day:
        return Check(name, FAIL, f"last bar {last_date} is older than the previous session {prev_day}; refresh-prices did not run")
    if last_date > prev_day:
        return Check(name, FAIL, f"last bar {last_date} is after the previous session {prev_day} (an in-progress bar was stored)")
    if not float(last.get("volume", 0) or 0) > 0:
        return Check(name, FAIL, f"bar {last_date} has zero volume (placeholder, not a complete session)")
    return Check(name, OK, f"{symbol} complete through {last_date}")


def _token_check(name: str, cfg: Config, now_epoch: float, min_hours: float, required: bool) -> Check:
    value = cfg.env.get(name)
    if not value:
        return Check(name, FAIL if required else WARN, f"{name} is not set")
    left = token_expiry.seconds_left(value, now_epoch)
    msg = token_expiry.describe(name, value, now_epoch)
    if left is None:
        return Check(name, WARN, msg)
    if left < min_hours * 3600:
        return Check(name, FAIL if required else WARN, f"{msg} (need >= {min_hours:g}h)")
    return Check(name, OK, msg)


def run_preflight(cfg: Config, day: date, now_epoch: float, calendar=CALENDAR,
                  load: Callable = _default_load_daily, which=shutil.which) -> PreflightResult:
    if not calendar.is_trading_day(day):
        return PreflightResult(str(day), False, (Check("calendar", OK, f"{day} is not an IDX trading day"),))
    checks: List[Check] = [Check("calendar", OK, f"{day} is an IDX trading day")]
    if getattr(calendar, "provisional", False):
        checks.append(Check("calendar", WARN, "holiday calendar is provisional"))

    paper, _other = select(cfg.registry)
    if not paper:
        checks.append(Check("strategies", FAIL, "no spec with status 'paper'"))
    prev = calendar.prev_trading_day(day)
    for symbol in sorted({s for spec in paper for s in spec.symbols}):
        checks.append(check_prev_bar(symbol, prev, cfg.daily_dir, load))

    checks.append(_token_check(ACCESS_TOKEN_ENV, cfg, now_epoch, cfg.min_access_token_hours, required=True))
    checks.append(_token_check(BEARER_TOKEN_ENV, cfg, now_epoch, 8.0, required=False))

    try:
        killed = os.path.lexists(cfg.kill_file)
        checks.append(Check("kill switch", FAIL if killed else OK,
                            f"{cfg.kill_file} present; remove it to run" if killed else "kill-switch file absent"))
    except OSError:
        checks.append(Check("kill switch", FAIL, f"{cfg.kill_file} unreadable (fail closed)"))

    bins = [cfg.repo_root / "bin" / "live-session", cfg.repo_root / "bin" / "strategy-run"]
    if not all(b.exists() for b in bins) and not which("go"):
        checks.append(Check("binaries", FAIL, "bin/live-session or bin/strategy-run missing and `go` not on PATH"))
    else:
        checks.append(Check("binaries", OK, "strategy binaries present or buildable"))
    return PreflightResult(str(day), True, tuple(checks))


def format_result(res: PreflightResult) -> str:
    if not res.trading_day:
        return f"{res.day}: not a trading day, nothing to do"
    lines = [f"preflight {res.day}: {'OK' if res.ok else 'FAILED'}"]
    lines += [f"  [{c.level}] {c.name}: {c.detail}" for c in res.checks]
    return "\n".join(lines)
