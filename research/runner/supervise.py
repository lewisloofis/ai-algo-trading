"""Tiny wrapper that runs ONE guarded command and records its exit code.

    python -m runner.supervise --rc-file F -- <argv...>                       (paper / make)
    python -m runner.supervise --rc-file F --live-grant G -- <live argv...>   (the live door)

The watchdog (a different process from `start`) can then tell "exited with 0" from "killed" by
the presence and content of the rc file. SIGINT/SIGTERM are forwarded to the child.

The paper path passes the argv through ``guard.validate_argv`` and so can never start
live-session. The live path never trusts its caller: it re-reads the grant, redoes the live
decision from the arm file, kill file, clock and environment as they are NOW
(``guard.reauthorize_live``), requires the argv to equal the authorised command, emits today's
target, refuses a target that would buy more than the armed cap, and only then starts
live-session with ALGO_LIVE/ALGO_CONFIRM set for that one process. It never restarts it."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from runner.guard import LiveGuardError, live_child_env, reauthorize_live, validate_argv

RC_REFUSED = 126
RC_TARGET_TOO_BIG = 7
RC_EMIT_FAILED_FLOOR = 1


def target_buy_notional(path: Path) -> float:
    """What a target would buy from flat, in IDR: base_notional_idr * sum(weights). ValueError if unreadable."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        base = float(doc["base_notional_idr"])
        weights = [float(t["weight"]) for t in doc["targets"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"target unreadable ({type(exc).__name__})") from exc
    if base < 0 or any(w < 0 for w in weights):
        raise ValueError("target has a negative size")
    return base * sum(weights)


def _default_emit(args: Sequence[str], registry) -> int:
    from strategy_fit import target_emitter as te
    return te.main(list(args), registry=registry)


def main(argv: Optional[Sequence[str]] = None, popen=subprocess.Popen, *, registry=None,
         env: Optional[Mapping[str, str]] = None, emit: Callable[[Sequence[str], object], int] = _default_emit,
         today=None, now_epoch: Optional[float] = None) -> int:
    p = argparse.ArgumentParser(prog="runner.supervise")
    p.add_argument("--rc-file", required=True)
    p.add_argument("--live-grant", default="")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if args.live_grant:
        return _run_live(args, cmd, popen, registry, os.environ if env is None else env, emit, today, now_epoch)
    try:
        validate_argv(cmd)
    except LiveGuardError as exc:
        print(f"supervise: refused: {exc}", file=sys.stderr)
        _write_rc(args.rc_file, RC_REFUSED)
        return RC_REFUSED
    child = popen(cmd)
    return _wait(child, args.rc_file)


def _run_live(args, cmd, popen, registry, env, emit, today, now_epoch) -> int:
    try:
        grant = json.loads(Path(args.live_grant).read_text(encoding="utf-8"))
        live = reauthorize_live(grant, cmd, env, registry, today=today, now_epoch=now_epoch)
    except (OSError, ValueError, LiveGuardError) as exc:
        print(f"supervise: LIVE refused: {exc}", file=sys.stderr)
        _write_rc(args.rc_file, RC_REFUSED)
        return RC_REFUSED
    print(f"supervise: LIVE authorised for {live.strategy_id}, max {live.max_idr:,} IDR; emitting today's target")
    sys.stdout.flush()
    rc = emit(live.emit_args, registry)
    if rc != 0:
        print(f"supervise: target emit failed (exit {rc}); live-session not started", file=sys.stderr)
        _write_rc(args.rc_file, rc or RC_EMIT_FAILED_FLOOR)
        return rc or RC_EMIT_FAILED_FLOOR
    try:
        need = target_buy_notional(live.target)
    except ValueError as exc:
        print(f"supervise: LIVE refused: {exc}; live-session not started", file=sys.stderr)
        _write_rc(args.rc_file, RC_REFUSED)
        return RC_REFUSED
    if need > live.max_idr:
        print(f"supervise: LIVE refused: the target would buy about {need:,.0f} IDR but the armed cap is "
              f"{live.max_idr:,} IDR; nothing was submitted", file=sys.stderr)
        _write_rc(args.rc_file, RC_TARGET_TOO_BIG)
        return RC_TARGET_TOO_BIG
    try:      # the target step takes time: the arm file and kill file are re-checked immediately before the start
        child_env = live_child_env(live, env, registry, today=today)
    except LiveGuardError as exc:
        print(f"supervise: LIVE refused at the last moment: {exc}", file=sys.stderr)
        _write_rc(args.rc_file, RC_REFUSED)
        return RC_REFUSED
    child = popen(list(live.argv), env=child_env)
    return _wait(child, args.rc_file)


def _wait(child, rc_file: str) -> int:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda s, _f, c=child: c.send_signal(s))
    rc = child.wait()
    _write_rc(rc_file, rc)
    return rc


def _write_rc(path: str, rc: int) -> None:
    tmp = Path(path + ".tmp")
    tmp.write_text(json.dumps({"rc": rc, "finished_at": time.time()}))
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
