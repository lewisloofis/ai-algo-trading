"""The arm file: the operator's per-day, per-strategy permission to trade real money.

    runs/arm/ARM-<YYYY-MM-DD>.json
    {"date": "<YYYY-MM-DD>", "strategies": {"<strategy_id>": {"max_idr": <int>}}}

It is the ONLY way a strategy can go live under the runner, and it is deliberately fragile:
valid for its own date only, naming each strategy and the most rupiah it may BUY that day, with
a hard ceiling no file can exceed. ``make runner-arm`` writes it; ``make runner-disarm`` (or
deleting the file) revokes it, and the watchdog stops a live session whose arm file vanishes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from lib.market_calendar import CALENDAR
from runner.config import REPO_ROOT, WIB

# The most any arm file may name, per strategy per day. It is the sleeve's own cash (ADR-0017,
# strategy_fit/sleeve_sizing.py: 15,000,000 IDR measured 2026-09-14): Carina rejects an order
# above the sleeve's cash (C-006), so a larger cap would only be a number that lies. It is a
# separate literal on purpose, not an import: a safety ceiling must not move because a research
# preset changed. test_hard_ceiling_is_within_the_measured_sleeve ties the two together.
HARD_MAX_IDR = 15_000_000


class ArmError(ValueError):
    """The arm request or file is not acceptable. The message says why and never holds a secret."""


@dataclass(frozen=True)
class ArmFile:
    path: Path
    day: date
    present: bool
    valid: bool
    max_idr: Dict[str, int]
    problems: Tuple[str, ...] = ()


def arm_path(runs_dir: Path, day: date) -> Path:
    return Path(runs_dir) / "arm" / f"ARM-{day}.json"


def _is_amount(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= HARD_MAX_IDR


def read_arm_file(runs_dir: Path, day: date) -> ArmFile:
    """Parse and validate the arm file for ``day``. Anything off makes it invalid (fails closed)."""
    path = arm_path(runs_dir, day)
    if not os.path.lexists(path):
        return ArmFile(path, day, False, False, {})
    if path.is_symlink() or not path.is_file():
        return ArmFile(path, day, True, False, {}, ("not a regular file (symlinks are refused)",))
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ArmFile(path, day, True, False, {}, ("unreadable or not JSON",))
    problems: List[str] = []
    out: Dict[str, int] = {}
    if not isinstance(doc, dict):
        return ArmFile(path, day, True, False, {}, ("top level must be an object",))
    if doc.get("date") != str(day):
        problems.append(f"date field must be {day} (an arm file is valid only for its own date)")
    strategies = doc.get("strategies")
    if not isinstance(strategies, dict) or not strategies:
        problems.append("strategies must be a non-empty object of id -> {max_idr}")
    else:
        for sid, entry in strategies.items():
            cap = entry.get("max_idr") if isinstance(entry, dict) else None
            if not _is_amount(cap):
                problems.append(f"{sid}: max_idr must be an integer in 1..{HARD_MAX_IDR:,}")
            else:
                out[sid] = cap
    return ArmFile(path, day, True, not problems, out if not problems else {}, tuple(problems))


def _today_wib() -> date:
    return datetime.now(WIB).date()


def write_arm(runs_dir: Path, day: date, strategy_id: str, max_idr: object, *, registry=None,
              today: Optional[date] = None, calendar=CALENDAR) -> ArmFile:
    """Arm one candidate strategy for ``day``, merging into an existing valid file. Atomic."""
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    spec = registry.get(strategy_id)
    if spec is None:
        raise ArmError(f"unknown strategy {strategy_id!r}; known: {sorted(registry)}")
    if not spec.is_candidate:
        raise ArmError(f"{strategy_id} has status {spec.status!r}; only a 'candidate' spec can be armed")
    if not _is_amount(max_idr):
        raise ArmError(f"max_idr must be an integer from 1 to {HARD_MAX_IDR:,} (the sleeve's own cash), got {max_idr!r}")
    if day < (today or _today_wib()):
        raise ArmError(f"{day} is in the past")
    if not calendar.is_trading_day(day):
        raise ArmError(f"{day} is not an IDX trading day")
    existing = read_arm_file(runs_dir, day)
    merged: Dict[str, int] = {}
    if existing.present:
        if not existing.valid:
            raise ArmError(f"{existing.path.name} already exists and is invalid ({'; '.join(existing.problems)}); "
                           "run `make runner-disarm` first")
        merged.update(existing.max_idr)
    merged[strategy_id] = int(max_idr)
    path = arm_path(runs_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"date": str(day), "strategies": {sid: {"max_idr": n} for sid, n in sorted(merged.items())}}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return read_arm_file(runs_dir, day)


def disarm(runs_dir: Path, day: date) -> bool:
    """Remove the arm file for ``day``. True if there was one."""
    try:
        arm_path(runs_dir, day).unlink()
        return True
    except FileNotFoundError:
        return False


def main(argv: Optional[Sequence[str]] = None, *, runs_dir: Optional[Path] = None, registry=None,
         today: Optional[date] = None) -> int:
    """``python -m runner arm|disarm``: the operator's side of the arm file."""
    p = argparse.ArgumentParser(prog="python -m runner")
    p.add_argument("command", choices=("arm", "disarm"))
    p.add_argument("--strategy", default="")
    p.add_argument("--max-idr", default="")
    p.add_argument("--date", default="")
    args = p.parse_args(argv)
    runs_dir = Path(runs_dir) if runs_dir is not None else REPO_ROOT / "runs"
    now_day = today or _today_wib()
    try:
        day = date.fromisoformat(args.date) if args.date else now_day
    except ValueError:
        print(f"runner: --date {args.date!r} must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.command == "disarm":
        removed = disarm(runs_dir, day)
        print(f"disarmed {day}: the arm file was removed; a live session already running for it is stopped by the watchdog"
              if removed else f"nothing to disarm for {day} (no arm file)")
        return 0
    try:
        n = int(args.max_idr)
    except ValueError:
        print(f"runner: --max-idr {args.max_idr!r} must be a whole number of IDR", file=sys.stderr)
        return 2
    try:
        res = write_arm(runs_dir, day, args.strategy, n, registry=registry, today=now_day)
    except ArmError as exc:
        print(f"runner: not armed: {exc}", file=sys.stderr)
        return 2
    print(f"ARMED: {args.strategy}, max {res.max_idr[args.strategy]:,} IDR, for {day} only ({res.path})")
    print("  Live runs only if, at the 08:45 start, the spec is still 'candidate', preflight passes (incl. a valid trading token),")
    print("  and runs/KILL is absent. It buys at most the amount above, then never restarts. Revoke: make runner-disarm"
          + (f" DATE={day}" if args.date else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
