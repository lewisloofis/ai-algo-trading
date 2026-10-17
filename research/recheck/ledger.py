"""
recheck/ledger.py — milestone ledger (plan 26 §4): a milestone is evaluated once, ever.

``research/results/recheck/milestones.json`` maps family_id -> a list of
``{milestone, as_of, edge, p, drop5, alert}`` entries (plus ``n`` and ``cost`` for the reader).
A second run at the same N reports the stored entry instead of evaluating again.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

FILENAME = "milestones.json"


def load(path: str) -> dict[str, list[dict]]:
    """{} when the file is absent. An unreadable ledger raises ValueError: it is never silently reset,
    because an empty ledger would let every milestone be looked at again."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{os.path.basename(path)} could not be read ({type(exc).__name__}: {exc})") from exc
    if not isinstance(doc, dict) or not all(
        isinstance(v, list) and all(isinstance(e, dict) and "milestone" in e for e in v) for v in doc.values()
    ):
        raise ValueError(f"{os.path.basename(path)} is not a {{family: [entries]}} ledger")
    return doc


def done(ledger: dict[str, list[dict]], family_id: str) -> list[int]:
    return sorted(int(e["milestone"]) for e in ledger.get(family_id, []))


def latest(ledger: dict[str, list[dict]], family_id: str) -> Optional[dict]:
    entries = ledger.get(family_id, [])
    return max(entries, key=lambda e: int(e["milestone"])) if entries else None


def append(path: str, family_id: str, entry: dict) -> None:
    """Add one entry; ValueError if that milestone is already there. Atomic replace."""
    ledger = load(path)
    if int(entry["milestone"]) in done(ledger, family_id):
        raise ValueError(f"{family_id} milestone {entry['milestone']} is already in the ledger")
    ledger.setdefault(family_id, []).append(entry)
    ledger[family_id].sort(key=lambda e: int(e["milestone"]))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".milestones-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
