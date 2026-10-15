"""The ONE place that decides each strategy's mode for the day.

Today it returns only PAPER or SKIP. LIVE is reserved: a future version would return it only for a
spec with status 'candidate' AND a valid arm file for that date naming the strategy. This version
parses/validates the arm file for the log but never lets it change what runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PAPER, SKIP, LIVE = "paper", "skip", "live"     # LIVE is never returned by this version
LIVE_UNSUPPORTED = "live not supported in this version"


@dataclass(frozen=True)
class Mode:
    mode: str
    reason: str


@dataclass(frozen=True)
class ArmFile:
    """runs/arm/ARM-<YYYY-MM-DD>.json: {"date": "...", "strategies": {"<id>": {"max_idr": N}}}."""
    path: Path
    present: bool
    valid: bool
    max_idr: Dict[str, int]
    problems: Tuple[str, ...] = ()


def arm_path(runs_dir: Path, day: date) -> Path:
    return Path(runs_dir) / "arm" / f"ARM-{day}.json"


def read_arm_file(runs_dir: Path, day: date) -> ArmFile:
    path = arm_path(runs_dir, day)
    if not path.exists():
        return ArmFile(path, False, False, {})
    problems: List[str] = []
    out: Dict[str, int] = {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ArmFile(path, True, False, {}, ("unreadable or not JSON",))
    if not isinstance(doc, dict) or doc.get("date") != str(day):
        problems.append(f"date field must be {day} (an arm file is valid only for its own date)")
    strategies = doc.get("strategies") if isinstance(doc, dict) else None
    if not isinstance(strategies, dict) or not strategies:
        problems.append("strategies must be a non-empty object of id -> {max_idr}")
    else:
        for sid, entry in strategies.items():
            cap = entry.get("max_idr") if isinstance(entry, dict) else None
            if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
                problems.append(f"{sid}: max_idr must be a positive integer")
            else:
                out[sid] = cap
    return ArmFile(path, True, not problems, out if not problems else {}, tuple(problems))


def decide_mode(spec, arm: Optional[ArmFile] = None) -> Mode:
    """paper | skip for one spec. The arm file is deliberately ignored in this version."""
    if spec.is_paper:
        return Mode(PAPER, "status 'paper'")
    if spec.status == "candidate":
        armed = arm is not None and arm.valid and spec.strategy_id in arm.max_idr
        return Mode(SKIP, f"status 'candidate'{' and armed' if armed else ''}: {LIVE_UNSUPPORTED}")
    return Mode(SKIP, f"status {spec.status!r} is not 'paper'")
