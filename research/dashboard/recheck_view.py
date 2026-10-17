"""
dashboard/recheck_view.py — the closed-family re-check (plan 26) as one evidence line per family.

``recheck.run`` writes ``research/results/recheck/<as-of>.json`` when the owner asks for a re-check. The
newest dated file is read here and each family's row on the Strategies page gains a line:

    Re-check (2026-12-01): waiting, 12 of 30
    Re-check (2026-12-01): milestone 30 evaluated, no alert
    Re-check (2026-12-01): ALERT at milestone 30 — needs a new pre-registered plan
    Re-check (2026-12-01): excluded (owner, 2026-10-17): unspent holdout overlaps post-verdict data

No file means no line. A file that cannot be used is reported as a problem ("Not checked: ..."), never as
silence. The ledger ``milestones.json`` in the same folder is not an output and is not read here.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

_DATED = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Recheck:
    as_of: str
    lines: Mapping[str, str]
    """family_id -> the text after "Re-check (<as_of>): "."""


def _line(f: dict) -> str:
    status, ev, n, nxt = f.get("status"), f.get("evaluation"), f.get("n"), f.get("next_milestone")
    if status == "waiting":
        return f"waiting, {n} of {nxt}"
    if status in ("evaluated_now", "evaluated_earlier") and isinstance(ev, dict):
        m = ev["milestone"]
        text = (f"ALERT at milestone {m} — needs a new pre-registered plan" if ev.get("alert")
                else f"milestone {m} evaluated, no alert")
        return text + (f"; waiting, {n} of {nxt}" if nxt else "")
    if status == "no_verdict":
        return str(f.get("reason") or "not checked: no readable verdict file")
    raise ValueError(f"family {f.get('family_id')!r} has an unknown status {status!r}")


def parse(doc: Any) -> Recheck:
    """ValueError when the document is not a re-check output this version understands."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"schema is not {SCHEMA_VERSION}")
    families, excluded = doc.get("families"), doc.get("excluded")
    if not isinstance(families, list) or not isinstance(excluded, list) or not isinstance(doc.get("as_of"), str):
        raise ValueError("'as_of', 'families' or 'excluded' is missing or malformed")
    lines: dict[str, str] = {}
    try:
        for f in families:
            lines[f["family_id"]] = _line(f)
        for e in excluded:
            lines[e["family_id"]] = str(e["reason"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"a family entry is malformed ({type(exc).__name__}: {exc})") from exc
    return Recheck(doc["as_of"], lines)


def load_recheck(results_dir: str) -> tuple[Recheck | None, str]:
    """(Recheck, "") for the newest ``<date>.json``; (None, "") when there is none; (None, reason) when unusable."""
    folder = os.path.join(results_dir, "recheck")
    try:
        names = sorted(n for n in os.listdir(folder) if _DATED.match(n))
    except OSError:
        return None, ""
    if not names:
        return None, ""
    newest = names[-1]
    try:
        with open(os.path.join(folder, newest), encoding="utf-8") as f:
            return parse(json.load(f)), ""
    except (OSError, ValueError, TypeError) as exc:
        return None, f"the closed-family re-check output {newest} could not be used ({type(exc).__name__}: {exc})"
