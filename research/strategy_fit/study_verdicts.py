"""
strategy_fit/study_verdicts.py — study verdicts as small committed files (plan 25).

A verdict is the decision recorded after reading a study's numbers: one family, one outcome, the
date it was decided, and where the evidence is. Each study keeps its verdicts next to its output in
``research/results/<study>/verdict.json``; the triage report and the dashboard read them from there.

    python -m strategy_fit.study_verdicts record --study indicator_rules --family indicator_rules \\
        --outcome FAIL --decided 2026-10-17 --source "plan 24 §10.1" \\
        --doc docs/plans/24-classic-indicator-rules.md --summary "all 5 rules FAIL development"
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import date
from typing import Iterable, Optional

SCHEMA_VERSION = 1
FILENAME = "verdict.json"
OUTCOMES: tuple[str, ...] = ("PASS", "FAIL", "NO_GO", "INCONCLUSIVE")
UNTESTED = "untested"

_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RESULTS_DIR = os.path.join(_RESEARCH_DIR, "results")
DEFAULT_REPO_ROOT = os.path.dirname(_RESEARCH_DIR)

_STUDY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Verdict:
    family_id: str
    outcome: str
    decided: str
    """ISO date (YYYY-MM-DD) the verdict was decided."""
    source: str
    """Short citation, e.g. "plan 24 §10.1"."""
    doc: str
    """Repo-relative path to the document holding the evidence."""
    summary: str
    """One line with the deciding numbers."""


@dataclass(frozen=True)
class Loaded:
    verdicts: dict[str, Verdict]
    """family_id -> the latest verdict for it."""
    problems: tuple[str, ...]

    def outcome(self, family_id: str) -> str:
        v = self.verdicts.get(family_id)
        return v.outcome if v else UNTESTED


def _catalog_ids() -> frozenset[str]:
    from strategy_fit.catalog_meta import ALL_FAMILIES

    return frozenset(f.family_id for f in ALL_FAMILIES)


def problems_with(v: Verdict, family_ids: Iterable[str], repo_root: str) -> list[str]:
    out: list[str] = []
    if v.outcome not in OUTCOMES:
        out.append(f"outcome {v.outcome!r} not in {OUTCOMES}")
    if v.family_id not in set(family_ids):
        out.append(f"family {v.family_id!r} is not in the family catalog (strategy_fit/catalog_meta.py)")
    try:
        date.fromisoformat(v.decided)
    except (TypeError, ValueError):
        out.append(f"decided {v.decided!r} is not a YYYY-MM-DD date")
    norm = os.path.normpath(v.doc or "")
    if not v.doc or os.path.isabs(v.doc) or norm.startswith(".."):
        out.append(f"doc {v.doc!r} must be a repo-relative path")
    elif not os.path.exists(os.path.join(repo_root, norm)):
        out.append(f"doc {v.doc!r} does not exist")
    for name in ("source", "summary"):
        if not str(getattr(v, name) or "").strip():
            out.append(f"{name} is empty")
    return out


def _read_file(path: str) -> list[Verdict]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"schema is {doc.get('schema') if isinstance(doc, dict) else '?'}, expected {SCHEMA_VERSION}")
    names = {f.name for f in fields(Verdict)}
    rows = doc.get("verdicts")
    if not isinstance(rows, list):
        raise ValueError("'verdicts' is not a list")
    out = []
    for r in rows:
        if not isinstance(r, dict) or set(r) != names:
            raise ValueError(f"a verdict needs exactly the fields {sorted(names)}")
        out.append(Verdict(**{k: str(r[k]) for k in names}))
    return out


def load_verdicts(
    results_dir: str = DEFAULT_RESULTS_DIR,
    family_ids: Optional[Iterable[str]] = None,
    repo_root: str = DEFAULT_REPO_ROOT,
) -> Loaded:
    """Every ``<results_dir>/*/verdict.json``. Latest ``decided`` wins per family; a same-day
    disagreement drops the family and is reported. A bad file is reported; the rest still load."""
    ids = _catalog_ids() if family_ids is None else frozenset(family_ids)
    problems: list[str] = []
    by_family: dict[str, list[tuple[str, Verdict]]] = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "*", FILENAME))):
        rel = os.path.relpath(path, results_dir)
        try:
            verdicts = _read_file(path)
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"{rel}: could not be read ({type(exc).__name__}: {exc})")
            continue
        for v in verdicts:
            bad = problems_with(v, ids, repo_root)
            if bad:
                problems.append(f"{rel}: {v.family_id}: " + "; ".join(bad))
                continue
            by_family.setdefault(v.family_id, []).append((rel, v))
    chosen: dict[str, Verdict] = {}
    for fid, entries in by_family.items():
        latest = max(v.decided for _, v in entries)
        top = [(rel, v) for rel, v in entries if v.decided == latest]
        if len({v.outcome for _, v in top}) > 1:
            where = ", ".join(f"{rel}={v.outcome}" for rel, v in top)
            problems.append(f"{fid}: conflicting verdicts decided the same day {latest} ({where})")
            continue
        chosen[fid] = top[0][1]
    return Loaded(chosen, tuple(problems))


def record(
    results_dir: str,
    study: str,
    verdict: Verdict,
    family_ids: Optional[Iterable[str]] = None,
    repo_root: str = DEFAULT_REPO_ROOT,
) -> str:
    """Validate, then upsert ``verdict`` (by family) into ``<results_dir>/<study>/verdict.json``."""
    if not _STUDY_RE.match(study or ""):
        raise ValueError(f"study {study!r} must be a single folder name under research/results/")
    bad = problems_with(verdict, _catalog_ids() if family_ids is None else family_ids, repo_root)
    if bad:
        raise ValueError("; ".join(bad))
    path = os.path.join(results_dir, study, FILENAME)
    existing = _read_file(path) if os.path.exists(path) else []
    merged = [verdict if v.family_id == verdict.family_id else v for v in existing]
    if verdict.family_id not in {v.family_id for v in existing}:
        merged.append(verdict)
    body = json.dumps({"schema": SCHEMA_VERSION, "verdicts": [asdict(v) for v in merged]},
                      indent=2, ensure_ascii=False) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".verdict-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m strategy_fit.study_verdicts")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="record (or replace) one family's verdict for a study")
    r.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR)
    r.add_argument("--repo-root", default=DEFAULT_REPO_ROOT)
    r.add_argument("--study", required=True, help="folder under research/results/")
    for name in ("family", "outcome", "decided", "source", "doc", "summary"):
        r.add_argument(f"--{name}", required=True)
    sub.add_parser("show", help="print every family's current outcome and any problems")
    return p


def main(argv: Optional[list[str]] = None, family_ids: Optional[Iterable[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "show":
        loaded = load_verdicts(family_ids=family_ids)
        for fid in sorted(_catalog_ids() if family_ids is None else family_ids):
            v = loaded.verdicts.get(fid)
            print(f"{fid:28s} {loaded.outcome(fid):13s} {v.decided + '  ' + v.source if v else ''}")
        for p in loaded.problems:
            print(f"PROBLEM: {p}", file=sys.stderr)
        return 1 if loaded.problems else 0
    v = Verdict(args.family, args.outcome, args.decided, args.source, args.doc, args.summary)
    try:
        path = record(args.results_dir, args.study, v, family_ids=family_ids, repo_root=args.repo_root)
    except ValueError as exc:
        print(f"not recorded: {exc}", file=sys.stderr)
        return 2
    print(f"recorded {v.family_id} = {v.outcome} in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
