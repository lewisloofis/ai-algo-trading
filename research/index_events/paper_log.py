"""
index_events/paper_log.py -- PAPER forward log for the frozen plan 21 H2 rule.

PAPER ONLY. This module never places orders and must not import anything that can
(strategy/, target_emitter, runner/live-session, daily_store). It reads local bars and
writes one tracked CSV ledger. It never reads tokens: bars are refreshed by the user
with ``python -m screener.daily_store snapshot`` (a ready-to-run line is printed).

Frozen rule (plan 21 sections 3, 4.3, 9a): for each MSCI deletion (change del or
migrate_out) signal session p = last IDX session <= the PDF's "as of the close of"
date; buy close_{p+1}; sell close_{p+1+k}, k=20 primary (5, 10 also recorded).
Eligibility at s=p (harness), suspension rule on a+1..p, entry day must trade, cost =
harness cost at Rp 3,000,000 and at 1 lot, compared with IHSG over the same window.

Units: every *_bps / gross_k* / net_k* / ihsg_ret_k* column is basis points.

    cd research && .venv/bin/python -m index_events.paper_log fetch --review Nov26
    cd research && .venv/bin/python -m index_events.paper_log log --review MSCI-2026-11
    cd research && .venv/bin/python -m index_events.paper_log update
    cd research && .venv/bin/python -m index_events.paper_log scorecard [--json]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from index_events import INDEX_SYMBOLS, msci_lists
from index_events.build_events import msci_review_type, stock_files, strip_new
from index_events.event_time import is_suspended
from index_events.msci_ticker_map import MSCI_TICKERS
from lib.market_calendar import MarketCalendar

RESEARCH = Path(__file__).resolve().parents[1]
RAW_DIR = msci_lists.RAW_DIR
DAILY_DIR = RESEARCH / "data" / "daily"
LEDGER_PATH = RESEARCH / "paper" / "plan21_h2" / "ledger.csv"

WIB = ZoneInfo("Asia/Jakarta")
EOD_READY = time(17, 30)           # a session's bar is treated as available from 17:30 WIB
HOLDS = (5, 10, 20)
PRIMARY_K = 20
NOTIONAL_IDR = 3_000_000.0
SHARES_PER_LOT = 100
BPS = 1e4
DEV_TRADES_MSCI_ST = 23
TRADES_NEEDED_MSCI_ST = 30
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
FETCH_TIMEOUT_S = 30
MAX_PDF_BYTES = 20 * 1024 * 1024
URLS = {"st": "https://app2.msci.com/eqb/gimi/stdindex/MSCI_{mon}{yy}_STPublicList.pdf",
        "sc": "https://app2.msci.com/eqb/gimi/smallcap/MSCI_{mon}{yy}_SCPublicList.pdf"}
DELETIONS = ("del", "migrate_out")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

PLANNED_COLS = [
    "logged_at", "review_label", "review_type", "index_name", "change", "msci_name", "ticker",
    "map_confidence", "announce_date", "effective_date", "a_session", "p_session", "entry_session",
    "exit_session_k5", "exit_session_k10", "exit_session_k20", "source_doc", "source_sha256", "plan_note",
]
MUTABLE_COLS = (
    ["status", "note", "resolved_ticker", "resolved_confidence", "resolved_at", "updated_at",
     "entry_px", "adv_idr", "vol_bps", "floor_bps", "impact_bps_3m", "cost_bps", "cost_bps_1lot",
     "coeffs_sha256", "flag_locked_entry"]
    + [f"{c}_k{k}" for k in HOLDS for c in ("exit_px", "gross", "ihsg_ret", "net", "net_1lot", "net_vs_ihsg")]
)
COLUMNS = PLANNED_COLS + MUTABLE_COLS
TERMINAL_PREFIX = "excluded_"


class PaperLogError(SystemExit):
    """Operator-facing failure: message printed to stderr, exit code 2."""

    def __init__(self, msg: str):
        print(f"ERROR: {msg}", file=sys.stderr)
        super().__init__(2)


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def review_to_file_tag(review: str) -> Tuple[str, str]:
    """'Nov26' -> ('Nov', '26')."""
    m = re.fullmatch(r"([A-Z][a-z]{2})(\d{2})", review)
    if not m or m.group(1) not in MONTHS:
        raise PaperLogError(f"--review must look like Nov26, got {review!r}")
    return m.group(1), m.group(2)


def label_to_file_tag(label: str) -> str:
    """'MSCI-2026-11' -> 'Nov26'."""
    m = re.fullmatch(r"MSCI-(\d{4})-(\d{2})", label)
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise PaperLogError(f"--review must look like MSCI-2026-11, got {label!r}")
    return f"{MONTHS[int(m.group(2)) - 1]}{m.group(1)[2:]}"


def http_get(url: str, timeout: int = FETCH_TIMEOUT_S) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https MSCI hosts
        return resp.read(MAX_PDF_BYTES + 1)


def fetch_review(review: str, raw_dir: Path = RAW_DIR, getter: Callable[[str], bytes] = http_get) -> List[Path]:
    """Download both public lists; write nothing unless both are valid PDFs. Never overwrites."""
    mon, yy = review_to_file_tag(review)
    targets = {k: raw_dir / k / f"MSCI_{mon}{yy}_{k}.pdf" for k in URLS}
    existing = [p for p in targets.values() if p.exists()]
    if existing:
        raise PaperLogError("refusing to overwrite existing file(s): " + ", ".join(str(p) for p in existing))
    blobs: Dict[str, bytes] = {}
    for key, tmpl in URLS.items():
        url = tmpl.format(mon=mon, yy=yy)
        try:
            data = getter(url)
        except urllib.error.HTTPError as exc:
            raise PaperLogError(f"{url} -> HTTP {exc.code}: MSCI has probably not published {review} yet; nothing written")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaperLogError(f"{url} -> network error: {exc}; nothing written")
        if not data.startswith(b"%PDF-"):
            raise PaperLogError(f"{url} did not return a PDF (starts {data[:20]!r}): not published yet or blocked; nothing written")
        if len(data) > MAX_PDF_BYTES:
            raise PaperLogError(f"{url} larger than {MAX_PDF_BYTES} bytes; refusing")
        blobs[key] = data
    written = []
    for key, data in blobs.items():
        targets[key].parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(targets[key], data)
        written.append(targets[key])
    log = raw_dir / "fetch_log.tsv"
    if log.exists():
        with log.open("a") as fh:
            for key in URLS:
                fh.write(f"{mon}{yy}\t{key}\t200\t{URLS[key].format(mon=mon, yy=yy)}\n")
    return written


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# ledger io
# ---------------------------------------------------------------------------

def read_ledger(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise PaperLogError(f"ledger not found: {path}")
    with path.open(newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise PaperLogError(f"ledger header mismatch in {path}; expected the {len(COLUMNS)} columns of paper_log.COLUMNS")
        return [dict(r) for r in rd]


def write_ledger(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    """Write a NEW full ledger file next to the old one, then atomically rename over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="raise")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in COLUMNS})
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def init_ledger(path: Path) -> None:
    if path.exists():
        raise PaperLogError(f"ledger already exists: {path}")
    write_ledger(path, [])


def assert_planned_unchanged(old: Sequence[Dict[str, str]], new: Sequence[Dict[str, str]]) -> None:
    if len(old) != len(new):
        raise AssertionError("row count changed")
    for o, n in zip(old, new):
        for c in PLANNED_COLS:
            if o[c] != n[c]:
                raise AssertionError(f"planned column {c} changed for {o['review_label']} {o['msci_name']}")


# ---------------------------------------------------------------------------
# session planning
# ---------------------------------------------------------------------------

def session_on_or_before(cal: MarketCalendar, d: date) -> date:
    return d if cal.is_trading_day(d) else cal.prev_trading_day(d)


def calendar_covered_year(holidays_path: Optional[Path] = None) -> int:
    """Latest calendar year that the holiday file lists at least one holiday for."""
    from lib import market_calendar as mc
    with (holidays_path or mc._DATA_PATH).open() as fh:
        return max(int(h["date"][:4]) for h in json.load(fh)["holidays"])


def with_fixed_holidays(cal: MarketCalendar, covered_year: int, years: int = 3) -> MarketCalendar:
    """Add the fixed-date New Year closure (Jan 1) for years the holiday file does not cover yet.

    Only Jan 1 is certain; the rest of an uncovered year is unverified and flagged in plan_note.
    """
    extra = frozenset(date(covered_year + i, 1, 1) for i in range(1, years + 1))
    return MarketCalendar(holidays=cal._holidays | extra, provisional=cal.provisional)


def plan_sessions(announce: str, effective: str, cal: MarketCalendar, covered_year: int) -> Dict[str, str]:
    """a, p, entry and k exits as ISO dates. Refuses a provisional calendar; flags uncovered years."""
    if cal.provisional:
        raise PaperLogError("market calendar is provisional (calendar/idx_holidays.json): refusing to plan sessions")
    cal = with_fixed_holidays(cal, covered_year)
    a = session_on_or_before(cal, date.fromisoformat(announce))
    p = session_on_or_before(cal, date.fromisoformat(effective))
    entry = cal.next_trading_day(p)
    out = {"a_session": a.isoformat(), "p_session": p.isoformat(), "entry_session": entry.isoformat()}
    notes = []
    for k in HOLDS:
        ex = cal.add_trading_days(entry, k)
        out[f"exit_session_k{k}"] = ex.isoformat()
        if ex.year > covered_year:
            notes.append(f"k{k} exit {ex.isoformat()} is in {ex.year}, beyond the holiday calendar (covers <= {covered_year}; only Jan 1 assumed closed): unverified")
    if entry.year > covered_year:
        notes.append(f"entry {entry.isoformat()} beyond holiday calendar")
    out["plan_note"] = "; ".join(notes)
    return out


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def resolve_ticker(msci_name: str) -> Tuple[str, str, str]:
    """(ticker, confidence, note); ('', 'low', ...) when unresolved. Low confidence counts as unmapped (plan 9a)."""
    tk, conf, note = MSCI_TICKERS.get(strip_new(msci_name), ("", "low", "name not in MSCI_TICKERS"))
    if conf == "low" or not tk:
        return "", "low", note or "low-confidence map"
    return tk, conf, note


def deletion_rows(review: str, raw_dir: Path) -> List[Tuple[msci_lists.Row, Path]]:
    """Indonesia deletions (del + inferred migrate_out) for the review, parsed with msci_lists."""
    tag = label_to_file_tag(review)
    msci_lists._find_pdftotext()
    parsed: List[msci_lists.Row] = []
    paths: Dict[str, Path] = {}
    for key in ("st", "sc"):
        pdf = raw_dir / key / f"MSCI_{tag}_{key}.pdf"
        if not pdf.exists():
            raise PaperLogError(f"missing {pdf}; run: fetch --review {tag}")
        paths[msci_lists.INDEX_NAMES[key]] = pdf
        text = msci_lists.pdf_to_text(pdf)
        try:
            rows = msci_lists.parse_text(text, pdf.name, key)
        except msci_lists.ParseError as exc:
            raise PaperLogError(f"cannot parse {pdf.name}: {exc}")
        got = (sum(r.change == "add" for r in rows), sum(r.change == "del" for r in rows))
        if got != msci_lists.country_summary(text):
            raise PaperLogError(f"{pdf.name}: parsed {got} != summary table {msci_lists.country_summary(text)}")
        parsed.extend(rows)
    tagged = msci_lists.tag_migrations(parsed)
    wrong = {r.review_label for r in tagged} - {review}
    if wrong:
        raise PaperLogError(f"PDF dateline gives review {sorted(wrong)}, not {review}")
    return [(r, paths[r.index_name]) for r in tagged if r.change in DELETIONS]


def build_log_rows(review: str, raw_dir: Path, cal: MarketCalendar, covered_year: int,
                   logged_at: datetime, *, allow_late: bool, today_wib: date) -> List[Dict[str, str]]:
    dels = deletion_rows(review, raw_dir)
    rows: List[Dict[str, str]] = []
    seen: set = set()
    for r, pdf in sorted(dels, key=lambda x: (x[0].index_name, x[0].msci_name)):
        plan = plan_sessions(r.announce_date, r.effective_date, cal, covered_year)
        if date.fromisoformat(plan["entry_session"]) <= today_wib:
            if not allow_late:
                raise PaperLogError(
                    f"entry session {plan['entry_session']} is not in the future: the log must be written before outcomes exist "
                    "(use --allow-late only for dry runs; the rows are tagged logged_late)")
            plan["plan_note"] = "; ".join(x for x in (plan["plan_note"], "logged_late") if x)
        tk, conf, tnote = resolve_ticker(r.msci_name)
        status = "planned"
        note = ""
        if not tk:
            status, note = "unmapped", f"add {strip_new(r.msci_name)!r} to msci_ticker_map.MSCI_TICKERS before p: {tnote}"
        elif tk in seen:
            status, note = "excluded_duplicate_ticker", "same ticker already logged in this review"
        seen.add(tk) if tk else None
        rows.append({
            "logged_at": logged_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "review_label": r.review_label,
            "review_type": msci_review_type(r.review_label), "index_name": r.index_name, "change": r.change,
            "msci_name": strip_new(r.msci_name), "ticker": tk, "map_confidence": conf,
            "announce_date": r.announce_date, "effective_date": r.effective_date, **plan,
            "source_doc": pdf.name, "source_sha256": sha256_file(pdf), "status": status, "note": note,
        })
    return rows


def cmd_log(args) -> int:
    ledger = Path(args.ledger)
    old = read_ledger(ledger)
    if any(r["review_label"] == args.review for r in old):
        raise PaperLogError(f"ledger already has rows for {args.review}: append-only, no overwrite")
    cal = MarketCalendar.load()
    today = datetime.now(WIB).date()
    new = build_log_rows(args.review, Path(args.raw_dir), cal, calendar_covered_year(), now_utc(),
                         allow_late=args.allow_late, today_wib=today)
    if not new:
        print(f"{args.review}: no Indonesia deletions in the lists; nothing appended")
        return 0
    write_ledger(ledger, old + new)
    print(f"appended {len(new)} rows for {args.review} -> {ledger}")
    for r in new:
        print(f"  {r['index_name']:8} {r['change']:11} {r['msci_name']:28} {r['ticker'] or '-':5} {r['status']:10} "
              f"p={r['p_session']} entry={r['entry_session']} k5={r['exit_session_k5']} "
              f"k10={r['exit_session_k10']} k20={r['exit_session_k20']}")
    warn = {r["plan_note"] for r in new if r["plan_note"]}
    for w in sorted(warn):
        print(f"WARNING plan_note: {w}", file=sys.stderr)
    unm = [r for r in new if r["status"] == "unmapped"]
    if unm:
        print("\n!!! UNMAPPED NAMES (add to research/index_events/msci_ticker_map.py MSCI_TICKERS, then run `update`; "
              "must be done before the entry close or the row becomes excluded_resolved_late):", file=sys.stderr)
        for r in unm:
            print(f"  !!! {r['index_name']}: {r['msci_name']}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# update: evaluation environment
# ---------------------------------------------------------------------------

@dataclass
class Env:
    """Bars + harness arrays needed to score a paper row. Built once per `update`."""
    panel: object
    eligible: object
    adv: object
    vol: object
    lock_next: object
    ihsg: "object"
    last_bar: Dict[str, "object"]
    coeffs_sha: str
    fwd_cache: dict = field(default_factory=dict)


def _harness():
    """Lazy import of the harness (cost / eligibility / forward return code is reused unchanged)."""
    from strategy_fit import families_idx_structural as h
    from strategy_fit import impact as imp
    return h, imp


def make_env(hist: Dict[str, "object"], ihsg: "object") -> Env:
    import pandas as pd
    h, imp = _harness()
    panel = h.build_panel(hist)
    adv, vol = h.impact_inputs(panel)
    last_bar = {s: pd.Timestamp(str(df["date"].astype(str).max())) for s, df in hist.items()}
    try:
        sha = hashlib.sha256(Path(imp._COEFFS_PATH).read_bytes()).hexdigest()[:12]
    except OSError:
        sha = "absent"
    return Env(panel, h.eligibility(panel), adv, vol, h.locked_up_entry(panel), ihsg, last_bar, sha)


def load_env(daily_dir: Path = DAILY_DIR) -> Env:
    import pandas as pd
    hist = {p.stem: pd.read_parquet(p) for p in stock_files(daily_dir)}
    ih = pd.read_parquet(daily_dir / "IHSG.parquet", columns=["date", "close"])
    ihsg = pd.Series(pd.to_numeric(ih["close"], errors="coerce").to_numpy(),
                     index=pd.DatetimeIndex(pd.to_datetime(ih["date"].astype(str)))).sort_index()
    ihsg = ihsg[~ihsg.index.duplicated(keep="last")]
    return make_env(hist, ihsg.where(ihsg > 0))


def session_available(session: str, asof: datetime) -> bool:
    """True once that session's close bar can exist (strictly before asof's WIB date, or after 17:30 WIB on it)."""
    local = asof.astimezone(WIB)
    s = date.fromisoformat(session)
    return s < local.date() or (s == local.date() and local.time() >= EOD_READY)


def eff_ticker(row: Dict[str, str]) -> str:
    return row["ticker"] or row["resolved_ticker"]


def is_terminal(row: Dict[str, str]) -> bool:
    return row["status"] == "closed" or row["status"].startswith(TERMINAL_PREFIX)


def _f(x: float, nd: int = 4) -> str:
    return "" if x is None or x != x else f"{x:.{nd}f}"


def _fwd(env: Env, k: int):
    if k not in env.fwd_cache:
        h, _ = _harness()
        env.fwd_cache[k] = h.fwd_total_return(env.panel, k)
    return env.fwd_cache[k]


class Need:
    """Collects tickers whose bars block progress."""

    def __init__(self):
        self.symbols: Dict[str, str] = {}

    def add(self, sym: str, why: str) -> None:
        self.symbols.setdefault(sym, why)


def _exclude(upd: Dict[str, str], reason: str, note: str = "") -> Dict[str, str]:
    upd["status"] = f"{TERMINAL_PREFIX}{reason}"
    if note:
        upd["note"] = note
    return upd


def evaluate_row(row: Dict[str, str], env: Env, asof: datetime, need: Need) -> Dict[str, str]:
    """Return the mutable-column updates for one row (empty dict = nothing to do yet)."""
    import numpy as np
    import pandas as pd
    h, _ = _harness()
    upd: Dict[str, str] = {}
    tk = eff_ticker(row)
    if row["status"] not in ("planned", "open") or not tk:
        return upd
    panel, dates = env.panel, env.panel.dates
    p, entry = pd.Timestamp(row["p_session"]), pd.Timestamp(row["entry_session"])

    def have_bars(session: pd.Timestamp) -> bool:
        if tk not in panel.symbols:
            need.add(tk, "missing")
            return False
        if env.last_bar[tk] < session:
            need.add(tk, "stale")
            return False
        return True

    if row["status"] == "planned":
        if not session_available(row["p_session"], asof) or not have_bars(p):
            return upd
        if p not in dates:
            return _exclude(upd, "session_missing", f"p {row['p_session']} not in bar calendar") if dates.max() > p else upd
        j, ip = panel.symbols.index(tk), dates.get_loc(p)
        a = pd.Timestamp(row["a_session"])
        if a not in dates:
            return _exclude(upd, "session_missing", f"a {row['a_session']} not in bar calendar")
        ia = dates.get_loc(a)
        if is_suspended(panel.volume.to_numpy(float)[ia + 1: ip + 1, j]):
            return _exclude(upd, "suspended_a_to_p")
        if not bool(env.eligible.iloc[ip, j]):
            return _exclude(upd, "not_eligible", "harness eligibility false at s=p")
        if not session_available(row["entry_session"], asof) or not have_bars(entry):
            return upd
        if entry not in dates:
            return _exclude(upd, "session_missing", f"entry {row['entry_session']} not in bar calendar") if dates.max() > entry else upd
        ie = dates.get_loc(entry)
        px = float(panel.close.to_numpy(float)[ie, j])
        if not panel.volume.to_numpy(float)[ie, j] > 0 or not px > 0:
            return _exclude(upd, "entry_not_traded")
        adv_v, vol_v = float(env.adv.to_numpy(float)[ip, j]), float(env.vol.to_numpy(float)[ip, j])
        fl, im3, _ = h.cost_components(np.array([px]), np.array([adv_v]), np.array([vol_v]), NOTIONAL_IDR, [tk])
        _, im1, _ = h.cost_components(np.array([px]), np.array([adv_v]), np.array([vol_v]), px * SHARES_PER_LOT, [tk])
        upd.update({
            "status": "open", "entry_px": _f(px, 2), "adv_idr": _f(adv_v, 0), "vol_bps": _f(vol_v, 2),
            "floor_bps": _f(float(fl[0]), 2), "impact_bps_3m": _f(float(im3[0]), 2),
            "cost_bps": _f(float(fl[0] + im3[0]), 2), "cost_bps_1lot": _f(float(fl[0] + im1[0]), 2),
            "coeffs_sha256": env.coeffs_sha, "flag_locked_entry": str(int(bool(env.lock_next.iloc[ip, j]))),
        })
    if row["entry_px"] == "" and "entry_px" not in upd:
        return upd
    cur = {**row, **upd}
    cost3, cost1 = float(cur["cost_bps"]), float(cur["cost_bps_1lot"])
    j, ip, ie = panel.symbols.index(tk), dates.get_loc(p), dates.get_loc(entry)
    for k in HOLDS:
        if cur[f"exit_px_k{k}"] != "":
            continue
        ex = pd.Timestamp(row[f"exit_session_k{k}"])
        if not session_available(row[f"exit_session_k{k}"], asof):
            continue
        ok_ihsg = env.ihsg.index.max() >= ex and entry in env.ihsg.index
        if not ok_ihsg:
            need.add("IHSG", "stale")
        if not have_bars(ex) or not ok_ihsg:
            continue
        if ex not in dates:
            if dates.max() > ex:
                upd[f"exit_px_k{k}"] = "NA"
                upd["note"] = f"exit k{k} {row[f'exit_session_k{k}']} not in bar calendar"
            continue
        gross = float(_fwd(env, k).iloc[ip, j]) * BPS
        px_out = float(panel.close.to_numpy(float)[dates.get_loc(ex), j])
        ih = float(env.ihsg.loc[ex] / env.ihsg.loc[entry] - 1.0) * BPS
        upd[f"exit_px_k{k}"] = _f(px_out, 2)
        if gross != gross:
            upd["note"] = "; ".join(x for x in (upd.get("note", cur["note"]), f"gross_k{k} invalid (bad bar or missing price)") if x)
            if k == PRIMARY_K:
                upd["status"] = f"{TERMINAL_PREFIX}bad_bars_or_missing"
            continue
        upd.update({f"gross_k{k}": _f(gross, 2), f"ihsg_ret_k{k}": _f(ih, 2),
                    f"net_k{k}": _f(gross - cost3, 2), f"net_1lot_k{k}": _f(gross - cost1, 2),
                    f"net_vs_ihsg_k{k}": _f(gross - cost3 - ih, 2)})
        if k == PRIMARY_K:
            upd["status"] = "closed"
    return upd


def resolve_unmapped(row: Dict[str, str], asof: datetime) -> Dict[str, str]:
    """An `unmapped` row whose name has since been mapped: record the resolution without touching planned columns."""
    if row["status"] != "unmapped":
        return {}
    tk, conf, _ = resolve_ticker(row["msci_name"])
    if not tk:
        return {}
    stamp = asof.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    upd = {"resolved_ticker": tk, "resolved_confidence": conf, "resolved_at": stamp}
    if session_available(row["entry_session"], asof):
        return {**upd, "status": "excluded_resolved_late", "note": "mapped only after the entry close; could not have been bought"}
    return {**upd, "status": "planned", "note": ""}


def update_rows(rows: Sequence[Dict[str, str]], env: Optional[Env], asof: datetime,
                env_loader: Optional[Callable[[], Env]] = None) -> Tuple[List[Dict[str, str]], Need]:
    """Pure function: new rows (planned columns copied untouched) and the tickers needing fresh bars."""
    need = Need()
    out: List[Dict[str, str]] = []
    stamp = asof.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for row in rows:
        new = dict(row)
        if not is_terminal(new):
            new.update(resolve_unmapped(new, asof))
            if env is None and env_loader is not None and new["status"] in ("planned", "open"):
                env = env_loader()
            if env is not None:
                new.update(evaluate_row(new, env, asof, need))
        if new != row:
            new["updated_at"] = stamp
        out.append(new)
    assert_planned_unchanged(rows, out)
    return out, need


def snapshot_line(need: Need) -> str:
    syms = ",".join(sorted(need.symbols))
    return f"cd research && python -m screener.daily_store snapshot --symbols {syms}"


def cmd_update(args) -> int:
    ledger = Path(args.ledger)
    rows = read_ledger(ledger)
    asof = now_utc()
    daily = Path(args.daily_dir)
    new, need = update_rows(rows, None, asof, env_loader=lambda: load_env(daily))
    changed = sum(a != b for a, b in zip(rows, new))
    if changed:
        write_ledger(ledger, new)
    print(f"{changed} row(s) updated -> {ledger}")
    counts: Dict[str, int] = {}
    for r in new:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("status counts:", dict(sorted(counts.items())))
    for r in new:
        if r["status"] == "unmapped":
            print(f"!!! UNMAPPED: {r['index_name']} {r['msci_name']} (add to msci_ticker_map.MSCI_TICKERS)", file=sys.stderr)
    if need.symbols:
        print("\nBars needed (missing/stale): " + ", ".join(f"{s}({w})" for s, w in sorted(need.symbols.items())))
        print("Run with your own token in the environment, then re-run `update`:")
        print("  " + snapshot_line(need))
    return 0


# ---------------------------------------------------------------------------
# scorecard
# ---------------------------------------------------------------------------

def _mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def scorecard(rows: Sequence[Dict[str, str]]) -> Dict[str, object]:
    closed = [r for r in rows if r["status"] == "closed"]
    per: Dict[str, Dict[str, object]] = {}
    groups = {"ALL": closed}
    for name in sorted({r["index_name"] for r in rows}):
        groups[name] = [r for r in closed if r["index_name"] == name]
    for name, grp in groups.items():
        net = [float(r["net_k20"]) for r in grp]
        vs = [float(r["net_vs_ihsg_k20"]) for r in grp]
        per[name] = {
            "closed_trips": len(grp), "mean_net_k20_bps": _mean(net), "mean_net_vs_ihsg_k20_bps": _mean(vs),
            "share_net_k20_gt0": (sum(x > 0 for x in net) / len(net)) if net else None,
            "share_net_vs_ihsg_k20_gt0": (sum(x > 0 for x in vs) / len(vs)) if vs else None,
            "mean_net_1lot_k20_bps": _mean([float(r["net_1lot_k20"]) for r in grp]),
        }
    st = len(groups.get("MSCI_ST", []))
    status: Dict[str, int] = {}
    for r in rows:
        status[r["status"]] = status.get(r["status"], 0) + 1
    return {
        "per_index": per, "status_counts": dict(sorted(status.items())),
        "msci_st_progress": {"development": DEV_TRADES_MSCI_ST, "paper": st, "total": DEV_TRADES_MSCI_ST + st,
                             "needed": TRADES_NEEDED_MSCI_ST,
                             "line": f"development {DEV_TRADES_MSCI_ST} + paper {st} = {DEV_TRADES_MSCI_ST + st} "
                                     f"of {TRADES_NEEDED_MSCI_ST} trades needed (plan 21 §10)"},
    }


def cmd_scorecard(args) -> int:
    sc = scorecard(read_ledger(Path(args.ledger)))
    if args.json:
        print(json.dumps(sc, indent=2))
        return 0
    fmt = lambda v, p=False: "n/a" if v is None else (f"{v * 100:.0f}%" if p else f"{v:+.0f}")
    print(f"{'index':9} {'closed':>6} {'mean net_k20':>13} {'mean net-IHSG':>14} {'share>0':>8}")
    for name, s in sc["per_index"].items():
        print(f"{name:9} {s['closed_trips']:>6} {fmt(s['mean_net_k20_bps']):>13} "
              f"{fmt(s['mean_net_vs_ihsg_k20_bps']):>14} {fmt(s['share_net_k20_gt0'], True):>8}")
    print("(bps, Rp 3M cost; share>0 is on net_k20)")
    print("status:", sc["status_counts"])
    print(sc["msci_st_progress"]["line"])
    return 0


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

def cmd_fetch(args) -> int:
    for p in fetch_review(args.review, Path(args.raw_dir)):
        print(f"saved {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="index_events.paper_log", description="Plan 21 H2 paper forward log (no orders).")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--ledger", default=str(LEDGER_PATH))
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download the two MSCI public-list PDFs (never overwrites)")
    f.add_argument("--review", required=True, help="e.g. Nov26")
    f.add_argument("--raw-dir", default=str(RAW_DIR))
    f.set_defaults(fn=cmd_fetch)
    lg = sub.add_parser("log", parents=[common], help="append planned rows for a review (append-only)")
    lg.add_argument("--review", required=True, help="e.g. MSCI-2026-11")
    lg.add_argument("--raw-dir", default=str(RAW_DIR))
    lg.add_argument("--allow-late", action="store_true", help="dry runs only: permit a review whose entry is already past")
    lg.set_defaults(fn=cmd_log)
    up = sub.add_parser("update", parents=[common], help="score open rows from local bars")
    up.add_argument("--daily-dir", default=str(DAILY_DIR))
    up.set_defaults(fn=cmd_update)
    sc = sub.add_parser("scorecard", parents=[common], help="closed-trip summary")
    sc.add_argument("--json", action="store_true")
    sc.set_defaults(fn=cmd_scorecard)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
