"""Process exit-code files and status-file heartbeats, shared by the paper and live watchers."""

from __future__ import annotations

import glob
import json
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from runner.config import Config
from strategy_fit.paper_stores import store_key


def rc_path(cfg: Config, day: date, sid: str) -> Path:
    return cfg.runner_dir / f"{day}-{sid}.rc.json"


def read_rc(path: Path) -> Optional[int]:
    try:
        return int(json.loads(Path(path).read_text())["rc"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def latest_status(runs_dir: Path, day: date, sid: str, sink: str = "paper") -> Optional[dict]:
    """The newest status file of today's session for ``sid`` under the given sink ('paper' | 'live')."""
    pat = str(Path(runs_dir) / f"{day}-{store_key(sid, sink)}-a[0-9]*.status.json")
    files = sorted(glob.glob(pat), key=lambda p: Path(p).stat().st_mtime)
    for path in reversed(files):
        try:
            doc = json.loads(Path(path).read_text())
            if isinstance(doc, dict):
                return doc
        except (OSError, ValueError):
            continue
    return None


def assess_heartbeat(doc: Optional[dict], now_epoch: float, stale_s: int) -> Tuple[str, Optional[float]]:
    """('none'|'running'|'stale'|'complete'|'error', age seconds). Unreadable timestamp is never 'running'."""
    if doc is None:
        return "none", None
    outcome = doc.get("outcome")
    ns = doc.get("wall_clock_ns")
    age = None
    if isinstance(ns, (int, float)) and not isinstance(ns, bool) and ns > 0:
        age = max(0.0, now_epoch - ns / 1e9)
    if outcome == "error":
        return "error", age
    if outcome != "running":
        return "complete", age
    if age is None or age > stale_s:
        return "stale", age
    return "running", age
