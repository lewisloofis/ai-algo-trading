"""Entrypoint: python -m runner <preflight|start|watch|post-close|morning|all|arm|disarm> [--date D] [--dry-run].

A scheduler/supervisor around the existing commands (paper-day, refresh-prices, recon,
paper-scorecard, dashboard). It goes live only for a 'candidate' strategy the operator armed for
today (`arm` / `disarm`, see runner.arm and docs/runbooks/unattended-runner.md); everything else
is paper or skipped."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace
from datetime import date
from typing import Callable, List, Mapping, Optional

from lib.market_calendar import CALENDAR
from runner import arm as arm_mod
from runner import livereport
from runner import preflight as pf
from runner.alerts import ALERT, SUMMARY, WARN, Alerter
from runner.config import Clock, Config, WIB, at
from runner.envfile import load_env_file
from runner.executor import Executor
from runner.plan import build_plan
from runner.postclose import post_close
from runner.session import start_sessions, watch_sessions
from runner.state import load_state, save_state
from runner.strategies import build_gate, decide_all

SUBCOMMANDS = ("preflight", "start", "watch", "post-close", "morning", "all", "arm", "disarm")


def run_preflight_phase(cfg: Config, day: date, clock: Clock, alerter: Alerter) -> bool:
    """Run preflight, record it, alert on failure. True = go."""
    res = pf.run_preflight(cfg, day, clock.now().timestamp())
    text = pf.format_result(res)
    alerter.log(text)
    print(text)
    sp = cfg.state_path(day)
    state = load_state(sp, str(day))
    state["preflight"] = {"ok": res.ok, "trading_day": res.trading_day, "at": clock.now().isoformat(timespec="seconds")}
    save_state(sp, state)
    if not res.trading_day:
        return False
    if not res.ok:
        alerter.send(ALERT, f"{day} preflight FAILED, skipping the day",
                     "\n".join([f"{c.name}: {c.detail}" for c in res.failures]
                               + [f"arm: {livereport.arm_status(cfg, day)} (nothing goes live on a failed preflight)"]))
        return False
    if res.warnings:
        alerter.send(WARN, f"{day} preflight warnings", "\n".join(f"{c.name}: {c.detail}" for c in res.warnings))
    return True


def _preflight_ok(cfg: Config, day: date, clock: Clock, alerter: Alerter) -> bool:
    prior = load_state(cfg.state_path(day), str(day)).get("preflight")
    if prior is not None:
        return bool(prior["ok"])
    return run_preflight_phase(cfg, day, clock, alerter)


def send_morning_summary(cfg: Config, day: date, clock: Clock, alerter: Alerter, preflight_ok: bool) -> None:
    """The 08:45 push: 'ARMED: <id>, max <n> IDR' or 'not armed', and what each strategy will do."""
    gate = build_gate(cfg, day, clock.now(), preflight_ok, load_state(cfg.state_path(day), str(day)))
    title, body = livereport.morning_summary(cfg, day, decide_all(cfg.registry, gate))
    alerter.send(SUMMARY, title, body)


def wait_until(clock: Clock, target, step_s: float = 60.0) -> None:
    """Sleep (never spin) until the target time."""
    while True:
        remaining = (target - clock.now()).total_seconds()
        if remaining <= 0:
            return
        clock.sleep(min(remaining, step_s))


def dispatch(cmd: str, cfg: Config, day: date, clock: Clock, ex: Executor, alerter: Alerter, force: bool = False) -> int:
    if cmd == "preflight":
        run_preflight_phase(cfg, day, clock, alerter)
        return 0 if (load_state(cfg.state_path(day), str(day))["preflight"]["ok"]
                     or not CALENDAR.is_trading_day(day)) else 1
    if cmd == "start":
        if not CALENDAR.is_trading_day(day):
            alerter.log(f"start {day}: not a trading day")
            return 0
        if not _preflight_ok(cfg, day, clock, alerter):
            return 1
        return start_sessions(cfg, day, clock, ex, alerter, preflight_ok=True)
    if cmd == "watch":
        return watch_sessions(cfg, day, clock, ex, alerter) if CALENDAR.is_trading_day(day) else 0
    if cmd == "post-close":
        return post_close(cfg, day, clock, ex, alerter, force=force)
    if cmd in ("all", "morning"):      # morning = all without the post-close wait (what launchd runs at 08:45)
        rc = 0
        if CALENDAR.is_trading_day(day):
            ok = run_preflight_phase(cfg, day, clock, alerter)
            send_morning_summary(cfg, day, clock, alerter, ok)
            if ok:
                rc |= start_sessions(cfg, day, clock, ex, alerter, preflight_ok=True)
                rc |= watch_sessions(cfg, day, clock, ex, alerter)
            else:
                rc = 1
            if cmd == "all":
                wait_until(clock, at(day, cfg.post_close_at))
                rc |= post_close(cfg, day, clock, ex, alerter, force=force)
        else:
            alerter.log(f"{cmd} {day}: not a trading day, nothing to do")
        return rc
    raise ValueError(cmd)


def main(argv: Optional[List[str]] = None, *, env: Optional[Mapping[str, str]] = None, clock: Optional[Clock] = None,
         executor: Optional[Executor] = None, alerter: Optional[Alerter] = None, cfg: Optional[Config] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m runner", description=__doc__.split("\n")[0])
    p.add_argument("command", choices=SUBCOMMANDS)
    p.add_argument("--date", default="", help="trading day YYYY-MM-DD (default: today, WIB)")
    p.add_argument("--dry-run", action="store_true", help="print the plan for the date and execute nothing")
    p.add_argument("--force", action="store_true", help="post-close: rerun even if already done today")
    p.add_argument("--no-env-file", action="store_true", help="do not load .env.local")
    p.add_argument("--strategy", default="", help="arm: the candidate strategy id")
    p.add_argument("--max-idr", default="", help="arm: the most IDR it may BUY that day (integer)")
    args = p.parse_args(argv)
    if args.command in ("arm", "disarm"):     # operator side of the arm file: no env file, no secrets
        return arm_mod.main([args.command, "--strategy", args.strategy, "--max-idr", args.max_idr,
                             "--date", args.date],
                            runs_dir=cfg.runs_dir if cfg is not None else None,
                            registry=cfg.registry if cfg is not None else None)

    clock = clock or Clock()
    try:
        day = date.fromisoformat(args.date) if args.date else clock.now().astimezone(WIB).date()
    except ValueError:
        print(f"runner: --date {args.date!r} must be YYYY-MM-DD", file=sys.stderr)
        return 2

    if cfg is None:
        cfg = Config(env=dict(env if env is not None else os.environ), python=sys.executable)
    elif not cfg.python:
        cfg = replace(cfg, python=sys.executable)
    mutable_env = dict(cfg.env)
    if not args.no_env_file:
        _added, problems = load_env_file(cfg.repo_root / ".env.local", mutable_env)
        cfg = replace(cfg, env=mutable_env)
        for msg in problems:
            print(f"runner: {msg}", file=sys.stderr)
    if args.dry_run:
        print("\n".join(build_plan(cfg, day)))
        return 0
    alerter = alerter or Alerter(cfg.log_file, cfg.env)
    ex = executor or Executor(registry=cfg.registry)
    try:
        return dispatch(args.command, cfg, day, clock, ex, alerter, force=args.force)
    except KeyboardInterrupt:
        alerter.send(WARN, f"{day} runner {args.command} interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001 - last line of defence: say so, never a traceback with env in it
        alerter.send(ALERT, f"{day} runner {args.command} crashed ({type(exc).__name__})", "see runs/runner/runner.log")
        alerter.log(f"crash detail: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
