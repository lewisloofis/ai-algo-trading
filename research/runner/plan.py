"""Dry-run plan: what the runner WOULD do on a date. Executes nothing, reads no secret values."""

from __future__ import annotations

import shlex
from datetime import date, datetime
from typing import List, Optional

from lib.market_calendar import CALENDAR
from runner import livereport
from runner.config import (ACCESS_TOKEN_ENV, BEARER_TOKEN_ENV, TELEGRAM_CHAT_ENV, TELEGRAM_TOKEN_ENV,
                           WEBHOOK_ENV, Config, at)
from runner.guard import LiveGuardError, build_live_command
from runner.modes import LIVE as MODE_LIVE
from runner.modes import PAPER
from runner.postclose import post_close_steps
from runner.session import paper_day_argv
from runner.strategies import build_gate, decide_all


def _presence(cfg: Config, name: str) -> str:
    return f"{name}={'set' if cfg.env.get(name) else 'MISSING'}"


def _live_lines(cfg: Config, day: date, spec, mode) -> List[str]:
    """Exactly what would run live for this strategy, and why. The account number is masked."""
    sid, cap = spec.strategy_id, mode.token.max_idr
    out = [f"        LIVE {sid}: {mode.reason}"]
    try:
        cmd = build_live_command(mode.token, registry=cfg.registry, repo_root=cfg.repo_root,
                                 rebalance_at=cfg.rebalance_at, account=(cfg.env.get("SB_ACCOUNT_NO") or "").strip(),
                                 data_dir=cfg.daily_dir)
    except LiveGuardError as exc:
        return out + [f"          would be REFUSED by the guard: {exc}"]
    out += ["          1) emit today's target: " + shlex.join([cfg.python or "python", "-m", "strategy_fit.target_emitter", *cmd.emit_args]),
            f"          2) refuse unless the target would buy <= {cap:,} IDR (it estimates base_notional x sum(weights))",
            "          3) live-session (account masked): " + cmd.display(),
            f"          cap: {cap:,} IDR of BUYS this day, enforced inside strategy-run's risk layer "
            "(seeded from today's earlier stores); sells are uncapped",
            "          env: ALGO_LIVE and ALGO_CONFIRM are set for that one process only, by guard.live_child_env; "
            "the trading token reaches the supervise wrapper and live-session, never the runner's other children",
            "          no restart of any kind (--max-restarts 0, --retry forbidden); if it dies: alert, read-only recon, stop"]
    return out


def build_plan(cfg: Config, day: date, calendar=CALENDAR, now: Optional[datetime] = None) -> List[str]:
    wd = day.strftime("%a")
    if not calendar.is_trading_day(day):
        return [f"{day} ({wd}): NOT an IDX trading day. The runner does nothing and sends no alert."]
    # Simulate the morning of `day`: its own date and its 08:45 clock, so a plan for tomorrow answers
    # "would the token still be valid, would the arm file be accepted" the way the real run will.
    now = now or at(day, cfg.preflight_at)
    gate = build_gate(cfg, day, now, preflight_ok=True)
    decisions = decide_all(cfg.registry, gate)
    paper = [s for s, m in decisions if m.mode == PAPER]
    live = [(s, m) for s, m in decisions if m.mode == MODE_LIVE]
    others = [(s, m) for s, m in decisions if m.mode not in (PAPER, MODE_LIVE)]
    ids = [s.strategy_id for s in paper]
    symbols = sorted({sym for s in [*paper, *(s for s, _ in live)] for sym in s.symbols})
    lines = [f"{day} ({wd}) plan, times WIB; dry run: nothing is executed",
             f"  arm status: {livereport.arm_status(cfg, day)}"]
    lines.append(f"  {cfg.preflight_at}  preflight: trading day; previous bar ({calendar.prev_trading_day(day)}) complete for "
                 f"{', '.join(symbols) or '-'}; "
                 f"env presence ({_presence(cfg, ACCESS_TOKEN_ENV)}, {_presence(cfg, BEARER_TOKEN_ENV)}); "
                 f"token expiry (hours only); kill switch {cfg.kill_file} absent. Failure: alert, skip the day. "
                 f"A dry run does not run it: the LIVE line below assumes it passes (live-only checks, e.g. the trading token's expiry, are evaluated at that morning's {cfg.preflight_at}).")
    lines.append(f"  {cfg.preflight_at}  summary push: '{day} morning plan: {livereport.arm_status(cfg, day)}' plus one line per strategy")
    lines.append(f"  {cfg.preflight_at}+ start: build binaries, then one paper session per spec with status 'paper', "
                 "and one LIVE session per strategy shown as LIVE:")
    lines.append("        " + shlex.join(["make", "build-strategy"]))
    for s in paper:
        lines.append("        " + shlex.join(paper_day_argv(cfg, s.strategy_id, day)))
    for s, m in live:
        lines += _live_lines(cfg, day, s, m)
    for s, m in others:
        lines.append(f"        {'NOT live' if s.is_candidate else 'skip'} {s.strategy_id}: {m.reason}")
    for warning in livereport.unmanaged_positions(cfg, decisions):
        lines.append(f"        WARNING {warning}")
    lines.append(f"  until {cfg.watch_deadline}  watch: status file stale > {cfg.stale_s}s alerts; a dead PAPER process is restarted "
                 f"(max {cfg.max_restarts}) only if no order is open and before {cfg.rebalance_at} minus 60s; "
                 f"graceful stop at the deadline")
    if live:
        lines.append("        LIVE watch: every order event (submitted / partial / filled / rejected / cancelled) is pushed; "
                     "deleting the arm file stops the session; a dead live process is NEVER restarted")
    lines.append(f"  {cfg.post_close_at}  post-close:")
    for st in post_close_steps(cfg, ids):
        note = f"   [SKIPPED: {st['skip']}]" if st["skip"] else ""
        lines.append("        " + shlex.join(st["argv"]) + note)
    lines.append("        then a summary alert" + (", then a LIVE summary alert" if live or gate.arm.valid else ""))
    channels = [n for n in (TELEGRAM_TOKEN_ENV, TELEGRAM_CHAT_ENV, WEBHOOK_ENV)]
    lines.append("  alerts: log " + str(cfg.log_file) + "; push channels: " + ", ".join(_presence(cfg, n) for n in channels))
    lines.append("  live only if ALL hold: spec status 'candidate', a valid arm file for TODAY naming it, preflight ok incl. a valid "
                 "trading token, no runs/KILL; otherwise paper (paper specs) or skipped. No order is ever resubmitted")
    return lines
