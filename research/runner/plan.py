"""Dry-run plan: what the runner WOULD do on a date. Executes nothing, reads no secret values."""

from __future__ import annotations

import shlex
from datetime import date
from typing import List

from lib.market_calendar import CALENDAR
from runner.config import (ACCESS_TOKEN_ENV, BEARER_TOKEN_ENV, TELEGRAM_CHAT_ENV, TELEGRAM_TOKEN_ENV,
                           WEBHOOK_ENV, Config)
from runner.postclose import post_close_steps
from runner.session import paper_day_argv
from runner.strategies import select


def _presence(cfg: Config, name: str) -> str:
    return f"{name}={'set' if cfg.env.get(name) else 'MISSING'}"


def build_plan(cfg: Config, day: date, calendar=CALENDAR) -> List[str]:
    wd = day.strftime("%a")
    if not calendar.is_trading_day(day):
        return [f"{day} ({wd}): NOT an IDX trading day. The runner does nothing and sends no alert."]
    paper, other = select(cfg.registry)
    ids = [s.strategy_id for s in paper]
    lines = [f"{day} ({wd}) plan, times WIB; dry run: nothing is executed"]
    lines.append(f"  {cfg.preflight_at}  preflight: trading day; previous bar ({calendar.prev_trading_day(day)}) complete for "
                 f"{', '.join(sorted({sym for s in paper for sym in s.symbols})) or '-'}; "
                 f"env presence ({_presence(cfg, ACCESS_TOKEN_ENV)}, {_presence(cfg, BEARER_TOKEN_ENV)}); "
                 f"token expiry (hours only); kill switch {cfg.kill_file} absent. Failure: alert, skip the day.")
    lines.append(f"  {cfg.preflight_at}+ start: build binaries, then one paper session per spec with status 'paper':")
    lines.append("        " + shlex.join(["make", "build-strategy"]))
    for s in paper:
        lines.append("        " + shlex.join(paper_day_argv(cfg, s.strategy_id, day)))
    for sid, reason in other:
        lines.append(f"        skip {sid}: {reason}")
    lines.append(f"  until {cfg.watch_deadline}  watch: status file stale > {cfg.stale_s}s alerts; a dead paper process is restarted "
                 f"(max {cfg.max_restarts}) only if no order is open and before {cfg.rebalance_at} minus 60s; "
                 f"graceful stop at the deadline")
    lines.append(f"  {cfg.post_close_at}  post-close:")
    for st in post_close_steps(cfg, ids):
        note = f"   [SKIPPED: {st['skip']}]" if st["skip"] else ""
        lines.append("        " + shlex.join(st["argv"]) + note)
    lines.append("        then a summary alert")
    channels = [n for n in (TELEGRAM_TOKEN_ENV, TELEGRAM_CHAT_ENV, WEBHOOK_ENV)]
    lines.append("  alerts: log " + str(cfg.log_file) + "; push channels: " + ", ".join(_presence(cfg, n) for n in channels))
    lines.append("  never live: paper specs only, ALGO_LIVE/ALGO_CONFIRM stripped, no order is ever resubmitted")
    return lines
