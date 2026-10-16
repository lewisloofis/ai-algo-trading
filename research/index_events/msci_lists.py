"""Parse MSCI quarterly-review "Public List" PDFs into tidy Indonesia rows.

Layout (identical 2014-2026 in the files on disk, verified by the summary-table
reconciliation in `reconcile`): a dateline ("Geneva, August 13, 2014"), a sentence
"... will take place as of the close of August 29, 2014.", a per-country summary
table, then per-country blocks:

    MSCI INDONESIA INDEX
    Additions        Deletions
    NAME A           NAME B
                     NAME C

Columns are separated by 2+ spaces; a line that starts with whitespace holds a
deletion only. "None" is a placeholder, not a security. "(NEW)" is kept in
msci_name verbatim and mirrored in `note`.

The lists carry no explicit migration marker. A name that is an addition in one
index and a deletion in the other index of the same review is tagged
migrate_in / migrate_out (see `tag_migrations`), with the evidence in `note`.

CLI: cd research && .venv/bin/python -m index_events.msci_lists
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

COUNTRY = "INDONESIA"
RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "index_events" / "raw" / "msci"
OUT_CSV = Path(__file__).resolve().parents[1] / "data" / "index_events" / "msci_raw.csv"
INDEX_NAMES = {"st": "MSCI_ST", "sc": "MSCI_SC"}
COLUMNS = [
    "provider", "index_name", "review_label", "announce_date", "effective_date",
    "section", "change", "msci_name", "source_doc", "note",
]

_DATE = r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})"
_DATELINE_RE = re.compile(r"^\s*[A-Z][A-Za-z .]+,\s+" + _DATE + r"\s*$", re.M)
_EFFECTIVE_RE = re.compile(r"as\s+of\s+the\s+close\s+of\s+" + _DATE, re.I)
_BLOCK_HEAD_RE = re.compile(r"^\s*MSCI\s+(.+?)\s+INDEX\s*$")
_REGION_RE = re.compile(r"^(ASIA PACIFIC|AMERICAS|EUROPE, MIDDLE EAST AND AFRICA)$")
_SUMMARY_RE = re.compile(r"^\s*([A-Z][A-Z .&']+?)\s{2,}(\d+)\s+(\d+)\s*$")
_SPLIT_RE = re.compile(r"\s{2,}")
_FOOTER_RE = re.compile(r"^\s*Page\s+\d+\b")


class ParseError(ValueError):
    """Raised when a mandatory field cannot be parsed; callers must not guess."""


@dataclass(frozen=True)
class Row:
    provider: str
    index_name: str
    review_label: str
    announce_date: str
    effective_date: str
    section: str
    change: str
    msci_name: str
    source_doc: str
    note: str


def _to_iso(month: str, day: str, year: str) -> str:
    try:
        return datetime.strptime(f"{month} {day} {year}", "%B %d %Y").date().isoformat()
    except ValueError as exc:
        raise ParseError(f"bad date {month} {day} {year}") from exc


def parse_announce_date(text: str) -> str:
    m = _DATELINE_RE.search(text[:2000])
    if not m:
        raise ParseError("dateline not found")
    return _to_iso(*m.groups())


def parse_effective_date(text: str) -> str:
    m = _EFFECTIVE_RE.search(text)
    if not m:
        raise ParseError("'as of the close of' date not found")
    return _to_iso(*m.groups())


def review_label(announce_iso: str) -> str:
    return f"MSCI-{announce_iso[:4]}-{announce_iso[5:7]}"


def country_summary(text: str, country: str = COUNTRY) -> Tuple[int, int]:
    """(added, deleted) from the per-country summary table; (0, 0) if absent."""
    for line in text.splitlines():
        if _BLOCK_HEAD_RE.match(line):
            break
        m = _SUMMARY_RE.match(line)
        if m and m.group(1).strip() == country:
            return int(m.group(2)), int(m.group(3))
    return 0, 0


def extract_country_block(text: str, country: str = COUNTRY) -> List[str]:
    """Lines of the country block, page footers/blank lines/repeated headers dropped.

    A block continues across a page break; it ends at the next country heading,
    a region heading, or the disclaimer page.
    """
    lines = text.replace("\f", "\n").splitlines()
    start = None
    for i, line in enumerate(lines):
        m = _BLOCK_HEAD_RE.match(line)
        if m and m.group(1).strip() == country:
            start = i + 1
            break
    if start is None:
        return []
    block: List[str] = []
    for line in lines[start:]:
        s = line.strip()
        if _BLOCK_HEAD_RE.match(line) or s.startswith("Notice and Disclaimer"):
            break
        if _REGION_RE.match(s):
            break  # region heading such as "EUROPE, MIDDLE EAST AND AFRICA"
        if not s or _FOOTER_RE.match(line):
            continue
        block.append(line.rstrip())
    return block


def parse_block(block: Sequence[str]) -> Dict[str, List[str]]:
    """Split a country block into {'ADDITIONS': [...], 'DELETIONS': [...]}.

    The section key is the heading text upper-cased ("Additions"/"Deletions" as
    printed). Raises ParseError on a line we cannot place.
    """
    out: Dict[str, List[str]] = {"Additions": [], "Deletions": []}
    seen_header = False
    for line in block:
        if re.match(r"^\s*Additions\s+Deletions\s*$", line):
            seen_header = True  # also swallows a header repeated after a page break
            continue
        if not seen_header:
            raise ParseError(f"line before Additions/Deletions header: {line!r}")
        indented = line[0] in " \t"
        parts = _SPLIT_RE.split(line.strip())
        if len(parts) > 2:
            raise ParseError(f"cannot split columns: {line!r}")
        if indented and len(parts) == 1:
            cells = [(None, parts[0])]
        elif indented:
            raise ParseError(f"indented line with two cells: {line!r}")
        elif len(parts) == 2:
            cells = [("Additions", parts[0]), ("Deletions", parts[1])]
        else:
            cells = [("Additions", parts[0])]
        for sec, name in cells:
            if name.upper() == "NONE":
                continue
            out["Deletions" if sec is None else sec].append(name)
    return out


def parse_text(text: str, source_doc: str, index_key: str) -> List[Row]:
    announce = parse_announce_date(text)
    effective = parse_effective_date(text)
    label = review_label(announce)
    sections = parse_block(extract_country_block(text))
    rows: List[Row] = []
    for section, names in sections.items():
        for name in names:
            rows.append(Row(
                provider="MSCI", index_name=INDEX_NAMES[index_key], review_label=label,
                announce_date=announce, effective_date=effective, section=section,
                change="add" if section == "Additions" else "del", msci_name=name,
                source_doc=source_doc, note="(NEW) flag in list" if "(NEW)" in name else "",
            ))
    return rows


def tag_migrations(rows: Sequence[Row]) -> List[Row]:
    """Tag cross-index moves within one review (exact name match), immutably."""
    key = lambda r: (r.review_label, r.msci_name)
    adds = {key(r): r for r in rows if r.change == "add"}
    dels = {key(r): r for r in rows if r.change == "del"}
    out: List[Row] = []
    for r in rows:
        other = (dels if r.change == "add" else adds).get(key(r))
        if other is not None and other.index_name != r.index_name:
            if r.change == "add":
                new, note = "migrate_in", f"inferred: same name deleted from {other.index_name} in same review"
            else:
                new, note = "migrate_out", f"inferred: same name added to {other.index_name} in same review"
            out.append(Row(**{**asdict(r), "change": new, "note": "; ".join(x for x in (r.note, note) if x)}))
        else:
            out.append(r)
    return out


def pdf_to_text(path: Path) -> str:
    res = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return res.stdout


def _find_pdftotext() -> None:
    import os, shutil
    if shutil.which("pdftotext") is None:
        os.environ["PATH"] += os.pathsep + "/opt/homebrew/bin"


def run(raw_dir: Path = RAW_DIR) -> Tuple[List[Row], List[Tuple[str, str]], List[dict]]:
    """Return (rows, failures[(file, reason)], per_file summary records)."""
    _find_pdftotext()
    rows: List[Row] = []
    failures: List[Tuple[str, str]] = []
    per_file: List[dict] = []
    for key in ("st", "sc"):
        for pdf in sorted((raw_dir / key).glob("*.pdf")):
            try:
                text = pdf_to_text(pdf)
                parsed = parse_text(text, pdf.name, key)
            except (ParseError, subprocess.CalledProcessError) as exc:
                failures.append((pdf.name, str(exc)))
                continue
            rows.extend(parsed)
            per_file.append({
                "file": pdf.name, "index": INDEX_NAMES[key],
                "label": parsed[0].review_label if parsed else review_label(parse_announce_date(text)),
                "adds": sum(r.change == "add" for r in parsed),
                "dels": sum(r.change == "del" for r in parsed),
                "summary": country_summary(text),
            })
    return tag_migrations(rows), failures, per_file


def write_csv(rows: Sequence[Row], path: Path = OUT_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: (r.announce_date, r.index_name, r.section, r.msci_name))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in ordered:
            w.writerow(asdict(r))


def main() -> int:
    rows, failures, per_file = run()
    write_csv(rows)
    print(f"wrote {len(rows)} rows -> {OUT_CSV}")
    mism = [p for p in per_file if (p["adds"], p["dels"]) != p["summary"]]
    print(f"files parsed: {len(per_file)}; failures: {len(failures)}; summary-table mismatches: {len(mism)}")
    for f, why in failures:
        print(f"  UNPARSED {f}: {why}")
    for p in mism:
        print(f"  MISMATCH {p['file']}: parsed {(p['adds'], p['dels'])} vs summary {p['summary']}")
    for idx in INDEX_NAMES.values():
        sub = [r for r in rows if r.index_name == idx]
        by = lambda c: sum(r.change == c for r in sub)
        print(idx, {c: by(c) for c in ("add", "del", "migrate_in", "migrate_out")},
              "reviews", len({r.review_label for r in sub}))
    return 1 if failures or mism else 0


if __name__ == "__main__":
    sys.exit(main())
