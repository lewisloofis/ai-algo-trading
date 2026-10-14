"""
dashboard/annotations.py — operator-written notes keyed by run_id.

For facts the post-trade store cannot hold (a broker rejection recorded before
commit a641a2a, the cause of a run that never wrote its end row). Rendered
verbatim and labelled operator-written: this file is a human's claim, not a
measurement, and the page never blends it into a number (plan-19 D2).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "annotations.json")


@dataclass(frozen=True)
class Annotation:
    run_id: str
    note: str
    source: str
    """Where the operator got it — a runbook section, a commit — so it can be checked."""
    reject_reason: str | None = None
    """A broker rejection that arrived after submit but left no order_reject row."""


DECISION_KEY = "_current_decision"
TIMELINE_KEY = "_timeline"


@dataclass(frozen=True)
class Decision:
    """The one-line 'what are we doing with real money' statement shown at the top of Overview."""

    headline: str
    reason: str
    as_of: str
    source: str


@dataclass(frozen=True)
class TimelineEvent:
    date: str
    note: str
    source: str


def _load_doc(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def load_annotations(path: str | None) -> dict[str, Annotation]:
    """
    Run-keyed notes. Absent or unparseable files are no annotations, never a crashed render.

    Keys starting with ``_`` are reserved for the page-level entries (decision, timeline).
    """
    try:
        return {
            run_id: Annotation(
                run_id=run_id,
                note=str(v["note"]),
                source=str(v["source"]),
                reject_reason=v.get("reject_reason"),
            )
            for run_id, v in _load_doc(path).items()
            if not run_id.startswith("_")
        }
    except (KeyError, AttributeError, TypeError):
        return {}


def load_decision(path: str | None) -> Decision | None:
    """The current decision, or None if the file does not carry one (the page then says so)."""
    v = _load_doc(path).get(DECISION_KEY)
    try:
        return Decision(
            headline=str(v["headline"]),
            reason=str(v["reason"]),
            as_of=str(v["as_of"]),
            source=str(v["source"]),
        )
    except (KeyError, TypeError):
        return None


def load_timeline(path: str | None) -> list[TimelineEvent]:
    """Page-level events plus run notes (dated from their run_id), oldest first."""
    doc = _load_doc(path)
    events: list[TimelineEvent] = []
    for e in doc.get(TIMELINE_KEY) or []:
        try:
            events.append(TimelineEvent(str(e["date"]), str(e["note"]), str(e["source"])))
        except (KeyError, TypeError):
            continue
    for run_id, a in load_annotations(path).items():
        stamp = run_id[:8]
        if len(stamp) == 8 and stamp.isdigit():
            events.append(
                TimelineEvent(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", a.note, a.source)
            )
    return sorted(events, key=lambda e: e.date)
