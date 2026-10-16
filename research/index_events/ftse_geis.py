"""Parse FTSE GEIS semi-annual review technical notices (Indonesia only) and
the yearly GEIS calendar PDFs into tidy CSVs.

CLI (from research/):  .venv/bin/python -m index_events.ftse_geis

Outputs (under research/data/index_events/):
  ftse_raw.csv       one row per Indonesia line in a technical notice
  ftse_calendar.csv  one row per review listed in the calendar PDFs

The input files are untrusted data: they are only parsed, never executed.
Country, section and header cells are located from the sheet content, not
from fixed positions, because layouts differ between eras.
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import subprocess
from collections import defaultdict
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = MODULE_DIR.parent / "data" / "index_events"
NOTICE_DIR = DATA_DIR / "raw" / "ftse" / "notices"
CALENDAR_DIR = DATA_DIR / "raw" / "ftse" / "calendar"
RAW_CSV = DATA_DIR / "ftse_raw.csv"
CAL_CSV = DATA_DIR / "ftse_calendar.csv"
PDFTOTEXT = "/opt/homebrew/bin/pdftotext"

TARGET_COUNTRY = "indonesia"
RAW_COLUMNS = [
    "provider", "review_label", "section", "subsection", "change", "sedol",
    "ftse_name", "country", "reason", "is_microcap_file", "source_doc", "note",
]
CAL_COLUMNS = [
    "review_label", "review_type", "data_cutoff", "announce_date", "final_date",
    "passive_trade_date", "effective_date", "source_doc", "note",
]

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
MONTH_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}

_TITLE_RE = re.compile(r"(" + "|".join(MONTHS) + r")\s+(\d{4})", re.I)
_SUBSECTION = {
    "inclusions": ("Inclusions", "add"),
    "inclusion": ("Inclusions", "add"),
    "exclusions": ("Exclusions", "del"),
    "exclusion": ("Exclusions", "del"),
    "nationality change": ("Nationality Change", "other"),
    "nationality changes": ("Nationality Change", "other"),
}
_SECTION_RE = re.compile(r"^(large|mid|small|micro)\s*cap$|^ftse\b", re.I)


# --------------------------------------------------------------------------
# Workbook reading
# --------------------------------------------------------------------------
def read_rows(path: Path) -> list[list]:
    """Return every row of the first sheet as a list of cell values."""
    if path.suffix.lower() == ".xls":
        import xlrd  # legacy binary format
        sheet = xlrd.open_workbook(str(path)).sheets()[0]
        return [sheet.row_values(i) for i in range(sheet.nrows)]
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _txt(cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        cell = int(cell)
    return str(cell).strip()


def normalize_sedol(cell) -> str:
    s = _txt(cell)
    return s.zfill(7) if s.isdigit() else s


def review_from_rows(rows: list[list]) -> tuple[int, int] | None:
    """(year, month) from the sheet title row, e.g. '... September 2026 Semi-Annual Review'."""
    for row in rows[:25]:
        cells = [_txt(c) for c in row if _txt(c)]
        if len(cells) != 1 or "review" not in cells[0].lower():
            continue
        m = _TITLE_RE.search(cells[0])
        if m:
            return int(m.group(2)), MONTH_NUM[m.group(1).lower()]
    return None


def review_from_filename(name: str) -> tuple[int, int] | None:
    m = re.search(r"(?<![A-Za-z])(Mar(?:ch)?|Sep(?:t(?:ember)?)?)[_]?(\d{4})", name, re.I)
    if not m:
        m = re.search(r"(?<![A-Za-z])(Mar(?:ch)?|Sep(?:t(?:ember)?)?)_(\d{4})", name, re.I)
    if not m:
        return None
    return int(m.group(2)), 3 if m.group(1).lower().startswith("mar") else 9


def _classify_marker(text: str):
    key = text.lstrip("*").strip().lower()
    if key in _SUBSECTION:
        return "subsection", _SUBSECTION[key]
    if _SECTION_RE.match(text.strip()):
        return "section", text.strip()
    return None, None


def _header_map(cells: list[str]) -> dict[str, int]:
    """Map logical field -> column index from a header row."""
    out: dict[str, int] = {}
    for i, c in enumerate(cells):
        k = c.lower()
        if k == "sedol":
            out["sedol"] = i
        elif k in ("company name", "name", "security name"):
            out["name"] = i
        elif k in ("country", "current country"):
            out["country"] = i
        elif k == "new country":
            out["new_country"] = i
        elif k in ("reason", "notes", "note"):
            out["reason"] = i
    return out


DEFAULT_COLS = {"sedol": 0, "name": 1, "country": 2}


def parse_notice_rows(rows, source_doc: str, review_label: str,
                      is_microcap_file: bool, country: str = TARGET_COUNTRY):
    """Yield tidy dict rows for `country` from one notice's rows."""
    section = ""
    subsection = ""
    change = ""
    cols: dict[str, int] | None = None
    for row in rows:
        cells = [_txt(c) for c in row]
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue
        if len(nonempty) == 1:
            kind, val = _classify_marker(nonempty[0])
            if kind == "subsection":
                subsection, change = val
                cols = None
            elif kind == "section":
                section, subsection, change, cols = val, "", "", None
            continue
        if cells[0].lower() == "sedol":
            cols = _header_map(cells)
            continue
        if not subsection:
            continue
        use = cols or DEFAULT_COLS
        note_parts = [] if cols else ["no_header_row"]

        def get(field):
            i = use.get(field)
            return cells[i] if i is not None and i < len(cells) else ""

        cur, new = get("country"), get("new_country")
        if country not in (cur.lower(), new.lower()):
            continue
        if new:
            note_parts.append(f"new_country={new}")
        yield {
            "provider": "FTSE", "review_label": review_label, "section": section,
            "subsection": subsection, "change": change,
            "sedol": normalize_sedol(row[use["sedol"]]) if use["sedol"] < len(row) else "",
            "ftse_name": get("name"), "country": cur, "reason": get("reason"),
            "is_microcap_file": is_microcap_file, "source_doc": source_doc,
            "note": ";".join(note_parts),
        }


def parse_notice_file(path: Path):
    """Return (rows, info) for one notice file. info carries label/mismatch data."""
    rows = read_rows(path)
    sheet_rev = review_from_rows(rows)
    file_rev = review_from_filename(path.name)
    info = {"file": path.name, "sheet": sheet_rev, "filename": file_rev,
            "mismatch": bool(sheet_rev and file_rev and sheet_rev != file_rev),
            "unparsed": sheet_rev is None}
    if sheet_rev is None:
        return [], info
    label = f"FTSE-{sheet_rev[0]}-{sheet_rev[1]:02d}"
    info["label"] = label
    micro = "microcap" in path.name.lower()
    return list(parse_notice_rows(rows, path.name, label, micro)), info


# --------------------------------------------------------------------------
# Calendar PDFs
# --------------------------------------------------------------------------
_DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", re.I)
_NA_RE = re.compile(r"\bn/a\b", re.I)
_TOKEN_RE = re.compile(_DATE_RE.pattern + r"|\bn/a\b", re.I)
REVIEW_MONTHS = [3, 6, 9, 12]
_KEYS = {
    "cutoff": re.compile(r"rebalance market|capitali[sz]ation\s+data|market\s*cap(?:italisation)?\s+data", re.I),
    "announce": re.compile(r"release of indicative (review )?(files|products)|indicative review files", re.I),
    "final": re.compile(r"release of final|final index review", re.I),
    "effective": re.compile(r"review effective", re.I),
}


def _tokens(line: str, year: int):
    """Ordered list of date|None tokens (None = n/a) found on a line."""
    out = []
    for m in _TOKEN_RE.finditer(line):
        if m.group(0).lower() == "n/a":
            out.append(None)
            continue
        y = int(m.group(3)) if m.group(3) else None
        out.append((int(m.group(1)), MONTH_NUM[m.group(2).lower()], y))
    return out


def _resolve(tok, year: int, review_month: int):
    if tok is None:
        return None, ""
    d, mo, y = tok
    note = ""
    if y is None:  # year dropped by PDF text extraction; Dec before a Mar review is prior year
        y = year - 1 if mo > review_month else year
        note = "year_inferred"
    return dt.date(y, mo, d), note


def parse_calendar_text(text: str, year: int, source_doc: str):
    """Parse pdftotext -layout output of one yearly GEIS calendar."""
    lines = text.splitlines()
    # schedule region: from first '<year>' schedule heading to the next heading
    start = next((i for i, l in enumerate(lines)
                  if re.search(r"review\s+schedule\s+" + str(year), l, re.I)
                  and i > 8), 0)
    region = list(range(start, min(len(lines), start + 140)))
    found: dict[str, list[tuple]] = {}
    for key, rx in _KEYS.items():
        for i in region:
            if not rx.search(lines[i]):
                continue
            need = 4
            for off in (0, 1, -1, 2, -2, 3, -3):
                j = i + off
                if j < 0 or j >= len(lines):
                    continue
                toks = _tokens(lines[j], year)
                if len(toks) == need and any(t is not None for t in toks) \
                        and (key != "cutoff" or True):
                    found[key] = toks
                    break
            if key in found:
                break
    rows = []
    for idx, month in enumerate(REVIEW_MONTHS):
        rec = {"review_label": f"FTSE-{year}-{month:02d}",
               "review_type": "semi" if month in (3, 9) else "quarterly",
               "source_doc": source_doc}
        notes = []
        for key, col in (("cutoff", "data_cutoff"), ("announce", "announce_date"),
                         ("final", "final_date"), ("effective", "effective_date")):
            toks = found.get(key)
            d, n = _resolve(toks[idx], year, month) if toks else (None, "")
            rec[col] = d.isoformat() if d else ""
            if n:
                notes.append(f"{col}:{n}")
            if toks is None:
                notes.append(f"{key}_row_missing")
        eff = rec["effective_date"]
        rec["passive_trade_date"] = ""
        if eff:
            e = dt.date.fromisoformat(eff)
            if e.weekday() == 0:
                rec["passive_trade_date"] = (e - dt.timedelta(days=3)).isoformat()
            else:
                notes.append("effective_not_monday")
        rec["note"] = ";".join(notes)
        rows.append(rec)
    return rows


def parse_calendar_pdf(path: Path):
    year = int(re.search(r"(\d{4})", path.stem).group(1))
    text = subprocess.run([PDFTOTEXT, "-layout", str(path), "-"], check=True,
                          capture_output=True, text=True).stdout
    return parse_calendar_text(text, year, path.name)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def section_kind(section: str) -> str:
    s = section.lower().replace("-", "").replace(" ", "")
    for seg, k in (("large", "L"), ("mid", "M"), ("small", "S"), ("micro", "U")):
        if s.startswith(seg):  # check first: "smallcap" contains "allcap"
            return k
    if "allworld" in s:
        return "AW"
    if "allcap" in s:
        return "AC"
    if "totalcap" in s:
        return "TC"
    return "?"


_VIEW_SEGMENTS = {"AW": "LM", "AC": "LMS", "TC": "LMSU"}


def consistency_violations(raw_rows: list[dict]) -> list[str]:
    """Index-view adds/dels must be explained by a segment add/del."""
    by = defaultdict(set)
    for r in raw_rows:
        if r["is_microcap_file"] in (True, "True") or r["change"] not in ("add", "del"):
            continue
        by[(r["review_label"], section_kind(r["section"]), r["change"])].add(r["sedol"])
    out = []
    labels = sorted({k[0] for k in by})
    for lab in labels:
        for view, segs in _VIEW_SEGMENTS.items():
            for ch in ("add", "del"):
                allowed = set().union(*(by.get((lab, s, ch), set()) for s in segs))
                extra = by.get((lab, view, ch), set()) - allowed
                if extra:
                    out.append(f"{lab} {view} {ch}: {sorted(extra)} not in {segs} {ch}")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _write(path: Path, cols: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    all_rows, infos = [], []
    for p in sorted(NOTICE_DIR.iterdir()):
        if p.suffix.lower() not in (".xls", ".xlsx") or p.name.startswith("~"):
            continue
        rows, info = parse_notice_file(p)
        all_rows.extend(rows)
        infos.append(info)
    all_rows.sort(key=lambda r: (r["review_label"], r["is_microcap_file"],
                                 r["source_doc"]))
    cal_rows = []
    for p in sorted(CALENDAR_DIR.glob("geis-*.pdf")):
        cal_rows.extend(parse_calendar_pdf(p))
    _write(RAW_CSV, RAW_COLUMNS, all_rows)
    _write(CAL_CSV, CAL_COLUMNS, cal_rows)

    print(f"wrote {RAW_CSV} ({len(all_rows)} rows), {CAL_CSV} ({len(cal_rows)} rows)")
    for i in infos:
        flag = "MISMATCH" if i["mismatch"] else ("UNPARSED" if i["unparsed"] else "ok")
        print(f"  {flag:8} sheet={i['sheet']} file={i['filename']} {i['file']}")
    cal_labels = {r["review_label"] for r in cal_rows}
    missing = sorted({r["review_label"] for r in all_rows} - cal_labels)
    print("reviews with no calendar row:", missing)
    for v in consistency_violations(all_rows):
        print("INCONSISTENT", v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
