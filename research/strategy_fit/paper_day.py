"""
strategy_fit/paper_day.py — one command per trading day for a PAPER forward test.

    make paper-day STRATEGY=wifi_momo_h5 [AS_OF=YYYY-MM-DD] [REBALANCE_AT=09:30]

On an IDX trading day: (1) emit today's target from bars strictly before today,
(2) run ``live-session --sink paper --source live`` (live prices, simulated fills,
market-data token only). On a non-trading day: print why and exit 0.

Paper only: a spec that is not ``paper`` is refused (exit 5), the child's
environment has ALGO_LIVE / ALGO_CONFIRM / SB_TRADING_ACCESS_TOKEN removed, and
the command is built by target_emitter.live_session_argv, which refuses a
candidate. The only secret read is the *presence* of SB_ACCESS_TOKEN.

Exit codes: 0 ok / non-trading day, 2 data or setup error, 3 no SB_ACCESS_TOKEN,
5 not a paper spec, 6 too late for REBALANCE_AT (live-session needs 60s lead),
else live-session's own code (0 clean, 1 problem, 2 refused/crashed).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from lib.market_calendar import CALENDAR
from strategy_fit import target_emitter as te
from strategy_fit.emitter_specs import SPECS
from strategy_fit.paper_stores import store_glob

ACCESS_TOKEN_ENV = "SB_ACCESS_TOKEN"
STRIPPED_ENV = ("ALGO_LIVE", "ALGO_CONFIRM", "SB_TRADING_ACCESS_TOKEN")
MIN_LEAD = timedelta(seconds=60)          # live-session --min-lead default
EXIT_TOO_LATE = te.EXIT_BAD_WINDOW


def paper_env(env: Mapping[str, str]) -> dict[str, str]:
    """The child environment: market data token only, nothing that could arm or trade."""
    return {k: v for k, v in env.items() if k not in STRIPPED_ENV}


def latest_start(as_of: date, rebalance_at: str) -> datetime:
    """Start strictly BEFORE this instant (live-session: rebalance-at must be > now + min-lead)."""
    t = te.parse_hhmm(rebalance_at)
    return datetime(as_of.year, as_of.month, as_of.day, t.hour, t.minute, tzinfo=te.WIB) - MIN_LEAD


def run(
    argv: Optional[Sequence[str]] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
    now: Optional[Callable[[], datetime]] = None,
    runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
    registry=None,
    repo_root: Path = te.REPO_ROOT,
    live_session_bin: Optional[Path] = None,
) -> int:
    p = argparse.ArgumentParser(prog="strategy_fit.paper_day", description=__doc__.split("\n")[1])
    p.add_argument("--strategy", required=True)
    p.add_argument("--as-of", default="", help="trading day, YYYY-MM-DD (default: today, WIB)")
    p.add_argument("--rebalance-at", default=f"{te.DEFAULT_WINDOW.rebalance_at:%H:%M}")
    p.add_argument("--runs-dir", default=str(repo_root / "runs"))
    p.add_argument("--data-dir", default=str(te.DEFAULT_DATA_DIR))
    p.add_argument("--equity-idr", type=int, default=te.PAPER_EQUITY_IDR)
    args = p.parse_args(argv)

    env = os.environ if env is None else env
    now_wib = (now or (lambda: datetime.now(te.WIB)))()
    registry = SPECS if registry is None else registry
    if args.strategy not in registry:
        print(f"paper-day: unknown strategy {args.strategy!r}; known: {sorted(registry)}", file=sys.stderr)
        return te.EXIT_DATA_ERROR
    spec = registry[args.strategy]
    if not spec.is_paper:
        print(f"paper-day: {spec.strategy_id} has status {spec.status!r}; paper-day runs only status "
              "'paper' specs (a live candidate goes through emit-target and live-session by hand)",
              file=sys.stderr)
        return te.EXIT_RETIRED

    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else now_wib.date()
    except ValueError:
        print(f"paper-day: --as-of {args.as_of!r} must be YYYY-MM-DD", file=sys.stderr)
        return te.EXIT_DATA_ERROR
    if not CALENDAR.is_trading_day(as_of):
        print(f"paper-day: {as_of} is not an IDX trading day; nothing to do ({spec.strategy_id}).")
        return 0

    if not env.get(ACCESS_TOKEN_ENV):
        print(f"paper-day: {ACCESS_TOKEN_ENV} is not set in the environment (market data only; "
              "no trading token is needed or used)", file=sys.stderr)
        return te.EXIT_NO_TOKEN

    try:
        window = te.RebalanceWindow(rebalance_at=te.parse_hhmm(args.rebalance_at))
    except te.BadWindowError as exc:
        print(f"paper-day: {exc}", file=sys.stderr)
        return te.EXIT_BAD_WINDOW
    if as_of == now_wib.date():
        deadline = latest_start(as_of, args.rebalance_at)
        if now_wib >= deadline:
            print(f"paper-day: too late. It is {now_wib:%H:%M:%S} WIB; live-session needs the run to start "
                  f"at least 60s before REBALANCE_AT={args.rebalance_at}, i.e. before {deadline:%H:%M:%S} WIB. "
                  "Pick a later REBALANCE_AT (inside 09:00-11:30) or skip today.", file=sys.stderr)
            return EXIT_TOO_LATE

    ls_bin = live_session_bin or te.LIVE_SESSION_BIN
    runs_dir = Path(args.runs_dir)
    for b in (ls_bin, Path(ls_bin).with_name("strategy-run")):
        if not Path(b).exists():
            print(f"paper-day: {b} not found; run `make build-strategy`", file=sys.stderr)
            return te.EXIT_DATA_ERROR

    out = runs_dir / f"target_{spec.strategy_id}_{as_of}.json"
    rc = te.main(
        ["emit", "--strategy", spec.strategy_id, "--as-of", as_of.isoformat(), "--out", str(out),
         "--data-dir", args.data_dir, "--rebalance-at", args.rebalance_at,
         "--runs-dir", str(runs_dir), "--equity-idr", str(args.equity_idr)],
        registry=registry,
    )
    if rc != 0:
        return rc

    cmd = te.live_session_argv(spec, out, window, args.equity_idr, runs_dir)
    cmd[0] = str(ls_bin)
    print(f"paper-day: running live-session (paper sink, live prices) for {spec.strategy_id} {as_of}")
    sys.stdout.flush()
    rc = runner(cmd, env=paper_env(env), cwd=str(repo_root)).returncode

    print()
    print("paper-day results")
    print(f"  target     {out}  (+ {te.sidecar_path(out).name})")
    print(f"  stores     {store_glob(runs_dir, spec.strategy_id)}")
    print(f"  scorecard  make paper-scorecard STRATEGY={spec.strategy_id}")
    print(f"  live-session exit {rc} (0 clean, 1 problem, 2 refused/crashed)")
    return rc


if __name__ == "__main__":
    sys.exit(run())
