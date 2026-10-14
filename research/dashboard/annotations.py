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


def load_annotations(path: str | None) -> dict[str, Annotation]:
    """Absent or unparseable files are no annotations, never a crashed render."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return {
            run_id: Annotation(
                run_id=run_id,
                note=str(v["note"]),
                source=str(v["source"]),
                reject_reason=v.get("reject_reason"),
            )
            for run_id, v in doc.items()
        }
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return {}
