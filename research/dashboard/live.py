"""
dashboard/live.py — which bot sessions are running right now, from their heartbeat files.

``strategy-run`` rewrites ``runs/<store>.status.json`` every few seconds. This module reads every
such file (health.py only reads the newest one) and sorts each into one state:

  online    the file says "running" AND it was rewritten within HEARTBEAT_STALE_S seconds
  stopped   the file says "running" but has not been rewritten that recently: the program died
            or was killed without writing an ending
  finished  the run ended normally
  error     the run ended with an error

Pure: no network, no clock of its own (``now`` is passed in). The result is JSON-able and carries
display-ready text, so the static page and the live-updating page show exactly the same words.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from dashboard import config
from dashboard.health import HEARTBEAT_STALE_S

ONLINE, STOPPED, FINISHED, ERROR = "online", "stopped", "finished", "error"

RECENT_DAYS = 3
"""Runs that are not online are listed only if their last heartbeat is this recent."""

_LABEL = {ONLINE: "Online", STOPPED: "Stopped without ending", FINISHED: "Finished", ERROR: "Failed"}
_TONE = {ONLINE: "green", STOPPED: "amber", FINISHED: "grey", ERROR: "red"}


@dataclass(frozen=True)
class LiveRun:
    file: str
    strategy_id: str
    mode: str
    """'real money' (sink live) or 'practice (paper)'."""
    source: str
    state: str
    state_label: str
    tone: str
    heartbeat_age_s: float | None
    heartbeat_text: str
    written_wib: str
    events_seen: int | None
    positions_text: str
    day_notional_idr: float | None
    notional_text: str
    kill_switch: bool
    kill_text: str
    error: str


@dataclass(frozen=True)
class LiveSummary:
    as_of_wib: str
    online_count: int
    runs: tuple[LiveRun, ...]
    notes: tuple[str, ...]
    """Files that could not be read, one plain sentence each."""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["runs"] = [asdict(r) for r in self.runs]
        d["notes"] = list(self.notes)
        return d


def _to_wib(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) + timedelta(hours=config.WIB_OFFSET_HOURS)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _written_at(doc: Mapping) -> datetime | None:
    ns = doc.get("wall_clock_ns")
    if _is_number(ns) and ns > 0:
        return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
    text = doc.get("written_at")
    if isinstance(text, str) and text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def age_text(age_s: float | None) -> str:
    if age_s is None:
        return "unknown"
    s = int(age_s)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m ago"
    return f"{s // 86400}d ago"


def classify(doc: Mapping, now: datetime) -> tuple[str, float | None]:
    """(state, heartbeat age in seconds). A missing timestamp can never be 'online'."""
    written = _written_at(doc)
    age = None if written is None else max(0.0, (now - written).total_seconds())
    outcome = doc.get("outcome")
    if outcome == "running":
        return (ONLINE if age is not None and age <= HEARTBEAT_STALE_S else STOPPED), age
    return (ERROR if outcome == "error" else FINISHED), age


def _positions_text(risk: Mapping) -> str:
    pos = risk.get("positions_idr")
    if not isinstance(pos, dict):
        return "–"
    held = {s: v for s, v in pos.items() if _is_number(v) and v}
    if not held:
        return "None"
    return ", ".join(f"{s} Rp {round(v):,}" for s, v in sorted(held.items()))


def _to_run(path: str, doc: Mapping, now: datetime) -> LiveRun:
    state, age = classify(doc, now)
    written = _written_at(doc)
    risk = doc.get("risk") if isinstance(doc.get("risk"), dict) else {}
    progress = doc.get("progress") if isinstance(doc.get("progress"), dict) else {}
    events = progress.get("events_seen")
    notional = risk.get("day_notional_idr")
    notional = float(notional) if _is_number(notional) else None
    tripped = bool(risk.get("kill_switch_tripped"))
    reason = str(risk.get("kill_switch_reason") or "")
    return LiveRun(
        file=os.path.basename(path),
        strategy_id=str(doc.get("strategy_id") or "unknown"),
        mode="real money" if doc.get("sink_kind") == "live" else "practice (paper)",
        source=str(doc.get("source") or "unknown"),
        state=state,
        state_label=_LABEL[state],
        tone=_TONE[state],
        heartbeat_age_s=age,
        heartbeat_text=age_text(age),
        written_wib=_to_wib(written).strftime("%Y-%m-%d %H:%M:%S WIB") if written else "unknown",
        events_seen=int(events) if _is_number(events) else None,
        positions_text=_positions_text(risk),
        day_notional_idr=notional,
        notional_text="–" if notional is None else f"Rp {round(notional):,}",
        kill_switch=tripped,
        kill_text=("TRIPPED" + (f": {reason}" if reason else "")) if tripped else "off",
        error=str(doc.get("error") or ""),
    )


def collect(runs_dir: str, now: datetime, recent_days: int = RECENT_DAYS) -> LiveSummary:
    """Read every runs/*status*.json. Unreadable ones become a note; they never raise."""
    found: list[tuple[datetime, LiveRun]] = []
    notes: list[str] = []
    cutoff = now - timedelta(days=recent_days)
    never = datetime.min.replace(tzinfo=timezone.utc)
    for path in sorted(glob.glob(os.path.join(runs_dir, "*status*.json"))):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as exc:
            notes.append(f"{name} could not be read ({type(exc).__name__}) and was skipped.")
            continue
        if not isinstance(doc, dict):
            notes.append(f"{name} is not a status file and was skipped.")
            continue
        run = _to_run(path, doc, now)
        written = _written_at(doc)
        if run.state != ONLINE and (written is None or written < cutoff):
            continue
        found.append((written or never, run))
    found.sort(key=lambda t: (t[1].state != ONLINE, -t[0].timestamp()))
    ordered = tuple(r for _w, r in found)
    return LiveSummary(
        as_of_wib=_to_wib(now).strftime("%Y-%m-%d %H:%M:%S WIB"),
        online_count=sum(1 for r in ordered if r.state == ONLINE),
        runs=ordered,
        notes=tuple(notes),
    )
