"""
strategy_fit/research_log.py — what research is running or just finished, as an append-only log (plan 25).

One JSON object per line in ``runs/research_log.jsonl`` (machine-local, gitignored). The assistant
appends an event when a study starts and when it finishes; the dashboard's Strategies page shows them.

    python -m strategy_fit.research_log start --study indicator_rules --plan "plan 24" \\
        --families indicator_rules --note "running the development split"
    python -m strategy_fit.research_log finish --study indicator_rules --plan "plan 24" \\
        --families indicator_rules --outcome FAIL --note "all 5 rules FAIL development"
    python -m strategy_fit.research_log show
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from strategy_fit.study_verdicts import _STUDY_RE

SCHEMA_VERSION = 1
EVENTS: tuple[str, ...] = ("start", "finish")
OUTCOMES: tuple[str, ...] = ("PASS", "FAIL", "NO_GO", "INCONCLUSIVE", "ABANDONED")
WIB = timezone(timedelta(hours=7))
"""Jakarta time, UTC+7, no daylight saving."""
RECENT_SHOWN = 10

_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG = os.path.join(os.path.dirname(_RESEARCH_DIR), "runs", "research_log.jsonl")


@dataclass(frozen=True)
class Event:
    ts: str
    """ISO-8601, seconds precision, +07:00 offset."""
    event: str
    study: str
    plan: str
    """Where the work is planned, e.g. "plan 26" or "alpha-search 2026-10"."""
    families: tuple[str, ...]
    outcome: Optional[str]
    """Finish events only."""
    note: str
    """One line."""


@dataclass(frozen=True)
class LogState:
    events: tuple[Event, ...]
    """File order."""
    running: tuple[Event, ...]
    """The start event of each study whose latest event is a start."""
    recent: tuple[Event, ...]
    """Finish events, newest first."""
    problems: tuple[str, ...]
    """"line N: reason" for each line that could not be used."""


def _catalog_ids() -> frozenset[str]:
    from strategy_fit.catalog_meta import ALL_FAMILIES

    return frozenset(f.family_id for f in ALL_FAMILIES)


def now_ts() -> str:
    return datetime.now(WIB).isoformat(timespec="seconds")


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def problems_with(e: Event, family_ids: Iterable[str]) -> list[str]:
    out: list[str] = []
    try:
        if _parse_ts(e.ts).utcoffset() != WIB.utcoffset(None):
            out.append(f"ts {e.ts!r} must carry the +07:00 offset")
    except (TypeError, ValueError):
        out.append(f"ts {e.ts!r} is not an ISO-8601 timestamp")
    if e.event not in EVENTS:
        out.append(f"event {e.event!r} not in {EVENTS}")
    if not _STUDY_RE.match(e.study or ""):
        out.append(f"study {e.study!r} must be a single folder-style name")
    known = set(family_ids)
    for fid in e.families:
        if fid not in known:
            out.append(f"family {fid!r} is not in the family catalog (strategy_fit/catalog_meta.py)")
    if e.event == "finish" and e.outcome not in OUTCOMES:
        out.append(f"a finish needs an outcome in {OUTCOMES}, got {e.outcome!r}")
    if e.event == "start" and e.outcome is not None:
        out.append("a start must not carry an outcome")
    for name in ("plan", "note"):
        value = getattr(e, name)
        if not isinstance(value, str) or not value.strip():
            out.append(f"{name} is empty")
        elif "\n" in value or "\r" in value:
            out.append(f"{name} must be a single line")
    return out


def _to_row(e: Event) -> dict:
    row = {"schema": SCHEMA_VERSION, "ts": e.ts, "event": e.event, "study": e.study, "plan": e.plan,
           "families": list(e.families)}
    if e.outcome is not None:
        row["outcome"] = e.outcome
    row["note"] = e.note
    return row


def append(path: str, event: Event, family_ids: Optional[Iterable[str]] = None) -> None:
    """Validate, then append one line (flushed and fsynced). Invalid: ValueError, nothing written."""
    bad = problems_with(event, _catalog_ids() if family_ids is None else family_ids)
    if bad:
        raise ValueError("; ".join(bad))
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    line = json.dumps(_to_row(event), ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


_FIELDS = {"schema", "ts", "event", "study", "plan", "families", "note"}


def _from_row(row: object) -> Event:
    if not isinstance(row, dict):
        raise ValueError("not a JSON object")
    if row.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"schema is {row.get('schema')!r}, expected {SCHEMA_VERSION}")
    extra = set(row) - _FIELDS - {"outcome"}
    missing = _FIELDS - set(row)
    if missing or extra:
        raise ValueError(f"fields missing {sorted(missing)} unexpected {sorted(extra)}")
    fams = row["families"]
    if not isinstance(fams, list) or not all(isinstance(x, str) for x in fams):
        raise ValueError("'families' is not a list of strings")
    texts = {k: row[k] for k in ("ts", "event", "study", "plan", "note")}
    if not all(isinstance(v, str) for v in texts.values()):
        raise ValueError("ts, event, study, plan and note must be strings")
    outcome = row.get("outcome")
    if outcome is not None and not isinstance(outcome, str):
        raise ValueError("'outcome' is not a string")
    return Event(families=tuple(fams), outcome=outcome, **texts)


def load(path: str, family_ids: Optional[Iterable[str]] = None) -> LogState:
    """Every usable line, in file order. A bad line is reported; the rest still load. No file: empty."""
    ids = _catalog_ids() if family_ids is None else frozenset(family_ids)
    events: list[Event] = []
    problems: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return LogState((), (), (), ())
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            ev = _from_row(json.loads(line))
        except (ValueError, TypeError) as exc:
            problems.append(f"line {n}: {exc}")
            continue
        bad = problems_with(ev, ids)
        if bad:
            problems.append(f"line {n}: " + "; ".join(bad))
            continue
        events.append(ev)
    latest: dict[str, Event] = {}
    for ev in events:
        latest.pop(ev.study, None)
        latest[ev.study] = ev
    running = tuple(ev for ev in latest.values() if ev.event == "start")
    finished = [ev for ev in reversed(events) if ev.event == "finish"]
    recent = tuple(sorted(finished, key=lambda e: _parse_ts(e.ts), reverse=True))
    return LogState(tuple(events), running, recent, tuple(problems))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m strategy_fit.research_log")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("start", "a study has started"), ("finish", "a study has finished")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--log", default=DEFAULT_LOG)
        s.add_argument("--study", required=True)
        s.add_argument("--plan", required=True)
        s.add_argument("--families", required=True, help="comma-separated family ids; '' for none")
        s.add_argument("--note", required=True)
        s.add_argument("--ts", default=None, help="override the clock (tests, backfill)")
        if name == "finish":
            s.add_argument("--outcome", required=True)
    sh = sub.add_parser("show", help="print what is running and the last finished studies")
    sh.add_argument("--log", default=DEFAULT_LOG)
    return p


def _show(path: str) -> int:
    state = load(path)
    print("Running now:")
    for e in state.running:
        print(f"  {e.study}  [{e.plan}]  since {e.ts}  {e.note}")
    if not state.running:
        print("  No research is running.")
    print(f"Recently finished (last {RECENT_SHOWN}):")
    for e in state.recent[:RECENT_SHOWN]:
        print(f"  {e.ts}  {e.study}  {e.outcome}  [{e.plan}]  {e.note}")
    if not state.recent:
        print("  No research finished yet.")
    for p in state.problems:
        print(f"PROBLEM: {p}", file=sys.stderr)
    return 1 if state.problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "show":
        return _show(args.log)
    fams = tuple(x.strip() for x in args.families.split(",") if x.strip())
    ev = Event(ts=args.ts or now_ts(), event=args.cmd, study=args.study, plan=args.plan, families=fams,
               outcome=getattr(args, "outcome", None), note=args.note)
    try:
        append(args.log, ev)
    except ValueError as exc:
        print(f"not recorded: {exc}", file=sys.stderr)
        return 2
    print(f"recorded {ev.event} {ev.study} in {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
