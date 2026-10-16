"""
index_events/build_events.py — plan 21 §2.4 event table.

Reads the raw MSCI / FTSE transcriptions, resolves tickers, maps every date onto
the local IDX trading-session calendar and writes
``research/data/index_events/events.csv`` plus coverage tables.

CLI: cd research && .venv/bin/python -m index_events.build_events

Session conventions (plan §3):
  a  = last session <= announcement date (MSCI posts ~04:00 WIB next morning,
       FTSE after the IDX close)
  first_tradeable_session = next session after a
  p  = last session <= passive-trade date (MSCI: "as of the close of" the
       effective date; FTSE: the effective Friday)
  e  = next session after p
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from index_events import INDEX_SYMBOLS
from index_events.ftse_ticker_map import FTSE_TICKERS
from index_events.msci_ticker_map import MSCI_TICKERS

RESEARCH = Path(__file__).resolve().parents[1]
DATA_DIR = RESEARCH / "data" / "index_events"
DAILY_DIR = RESEARCH / "data" / "daily"
OUT_DIR = RESEARCH / "out_index_events"
EVENTS_CSV = DATA_DIR / "events.csv"

MIN_NAMES_TRADING = 10
MSCI_FREEZE_FROM = "MSCI-2026-02"
FTSE_FREEZE_FROM = "FTSE-2026-03"
SEGMENTS = ("Large", "Mid", "Small")
SEG_RANK = {"Small": 1, "Mid": 2, "Large": 3}
FTSE_SEG_SECTIONS = {"Large Cap": "Large", "Mid Cap": "Mid", "Small Cap": "Small", "Micro Cap": "Micro"}
AW_SECTIONS = {"FTSE All-World (LM)", "FTSE All-World"}
ALLCAP_SECTIONS = {"FTSE All-Cap (LMS)", "FTSE AllCap (LMS)"}

EVENT_COLUMNS = [
    "event_id", "provider", "index_name", "symbol", "change", "review_label", "review_type",
    "announce_ts_local", "announce_session", "first_tradeable_session", "passive_trade_session",
    "effective_session", "effective_date", "source_doc", "source_ref", "cross_check", "note",
    "ticker", "map_confidence", "provider_name", "from_segment", "to_segment", "source_tier",
]


# ---------------------------------------------------------------------------
# Session calendar
# ---------------------------------------------------------------------------

def stock_files(daily_dir: Path = DAILY_DIR) -> list[Path]:
    """Per-stock daily parquet files; index series (INDEX_SYMBOLS) are not stocks."""
    return [f for f in sorted(Path(daily_dir).glob("*.parquet")) if f.stem not in INDEX_SYMBOLS]


def trading_sessions(daily_dir: Path = DAILY_DIR, min_names: int = MIN_NAMES_TRADING) -> pd.DatetimeIndex:
    """Sorted union of dates on which >= min_names local files have a traded bar (volume > 0)."""
    counts: dict[str, int] = {}
    for f in stock_files(daily_dir):
        df = pd.read_parquet(f, columns=["date", "volume"])
        d = df.loc[pd.to_numeric(df["volume"], errors="coerce").fillna(0) > 0, "date"].astype(str)
        for x in set(d):
            counts[x] = counts.get(x, 0) + 1
    keep = sorted(d for d, n in counts.items() if n >= min_names)
    return pd.DatetimeIndex(pd.to_datetime(keep))


def session_on_or_before(sessions: pd.DatetimeIndex, date) -> pd.Timestamp:
    """Last session <= date; NaT if date precedes the calendar or is after its last session + 0."""
    if date is None or (isinstance(date, str) and not date.strip()) or pd.isna(date):
        return pd.NaT
    ts = pd.Timestamp(date)
    if ts > sessions[-1] or ts < sessions[0]:
        return pd.NaT
    i = sessions.searchsorted(ts, side="right") - 1
    return sessions[i]


def session_after(sessions: pd.DatetimeIndex, ts) -> pd.Timestamp:
    """Next session strictly after session ``ts``; NaT at the end of the calendar."""
    if pd.isna(ts):
        return pd.NaT
    i = sessions.searchsorted(pd.Timestamp(ts), side="right")
    return sessions[i] if i < len(sessions) else pd.NaT


def map_dates(sessions, announce_date, passive_date) -> dict:
    a = session_on_or_before(sessions, announce_date)
    p = session_on_or_before(sessions, passive_date)
    return {
        "announce_session": a,
        "first_tradeable_session": session_after(sessions, a),
        "passive_trade_session": p,
        "effective_session": session_after(sessions, p),
    }


# ---------------------------------------------------------------------------
# MSCI
# ---------------------------------------------------------------------------

def strip_new(name: str) -> str:
    return re.sub(r"\s*\(NEW\)\s*$", "", str(name)).strip()


def msci_review_type(label: str) -> str:
    if label >= MSCI_FREEZE_FROM:
        return "freeze"
    return "semi" if label[-2:] in ("05", "11") else "quarterly"


def msci_segments(index_name: str, change: str) -> tuple[str, str]:
    own, other = ("ST", "SC") if index_name == "MSCI_ST" else ("SC", "ST")
    return {"add": ("none", own), "del": (own, "none"),
            "migrate_in": (other, own), "migrate_out": (own, other)}[change]


def build_msci(raw: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for r in raw.itertuples(index=False):
        name = strip_new(r.msci_name)
        tk, conf, tnote = MSCI_TICKERS.get(name, ("", "low", "name not in MSCI_TICKERS"))
        notes = [x for x in (str(r.note) if isinstance(r.note, str) else "", tnote) if x]
        cross = "none"
        if conf == "low" or not tk:
            cross = "mismatch"
            notes.append("ticker_unresolved_or_ambiguous")
        frm, to = msci_segments(r.index_name, r.change)
        rows.append({
            "provider": "MSCI", "index_name": r.index_name, "change": r.change,
            "review_label": r.review_label, "review_type": msci_review_type(r.review_label),
            "announce_ts_local": r.announce_date, "effective_date": r.effective_date,
            **map_dates(sessions, r.announce_date, r.effective_date),
            "source_doc": r.source_doc, "source_ref": "", "cross_check": cross,
            "note": "; ".join(notes), "ticker": tk, "map_confidence": conf,
            "provider_name": name, "from_segment": frm, "to_segment": to, "source_tier": "primary",
            "_key": tk or name,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# FTSE
# ---------------------------------------------------------------------------

def classify_ftse(frm: str, to: str) -> str | None:
    """Event class from the segment pair; None = Micro-only / none->Micro move (dropped)."""
    f_in, t_in = frm in SEG_RANK, to in SEG_RANK
    if not f_in and not t_in:
        return None
    if not f_in:
        return "add"
    if not t_in:
        return "del"
    if SEG_RANK[to] == SEG_RANK[frm]:
        return None
    if frm == "Small":
        return "migrate_in"
    if to == "Small":
        return "migrate_out"
    return "other"


def ftse_moves(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One row per (review_label, sedol) with from/to segment and class.

    Returns (moves, stats). stats counts the dropped Micro-only moves.
    """
    seg = raw[raw["section"].isin(FTSE_SEG_SECTIONS)].copy()
    seg["segment"] = seg["section"].map(FTSE_SEG_SECTIONS)
    out = []
    for (rev, sedol), g in seg.groupby(["review_label", "sedol"], sort=True):
        frm_set = set(g.loc[g["change"] == "del", "segment"])
        to_set = set(g.loc[g["change"] == "add", "segment"])
        frm = next(iter(frm_set)) if len(frm_set) == 1 else ("none" if not frm_set else "multi")
        to = next(iter(to_set)) if len(to_set) == 1 else ("none" if not to_set else "multi")
        out.append({
            "review_label": rev, "sedol": sedol, "from_segment": frm, "to_segment": to,
            "ftse_name": g["ftse_name"].iloc[0], "source_doc": "|".join(sorted(set(g["source_doc"]))),
            "n_rows": len(g),
        })
    mv = pd.DataFrame(out)
    mv["change"] = [classify_ftse(f, t) for f, t in zip(mv["from_segment"], mv["to_segment"])]
    stats = {"micro_only_dropped": int(mv["change"].isna().sum())}
    return mv, stats


def ftse_view_check(moves: pd.DataFrame, raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compare each move with the All-World (LM) / All-Cap (LMS) index views.

    Adds columns ``view_check`` (ok|mismatch|none) and ``view_note``.
    Also returns orphan view rows (view row with no matching segment move).
    """
    def sets(sections):
        v = raw[raw["section"].isin(sections)]
        return ({(r, s) for r, s, c in zip(v["review_label"], v["sedol"], v["change"]) if c == "add"},
                {(r, s) for r, s, c in zip(v["review_label"], v["sedol"], v["change"]) if c == "del"},
                set(v["review_label"]))
    aw_in, aw_out, aw_revs = sets(AW_SECTIONS)
    ac_in, ac_out, ac_revs = sets(ALLCAP_SECTIONS)
    checks, notes = [], []
    for r in moves.itertuples(index=False):
        key = (r.review_label, r.sedol)
        f, t = r.from_segment, r.to_segment
        exp = {
            "AW_in": (t in ("Large", "Mid")) and f not in ("Large", "Mid"),
            "AW_out": (f in ("Large", "Mid")) and t not in ("Large", "Mid"),
            "AC_in": (t in SEG_RANK) and f not in SEG_RANK,
            "AC_out": (f in SEG_RANK) and t not in SEG_RANK,
        }
        obs = {"AW_in": key in aw_in, "AW_out": key in aw_out, "AC_in": key in ac_in, "AC_out": key in ac_out}
        present = {"AW_in": r.review_label in aw_revs, "AW_out": r.review_label in aw_revs,
                   "AC_in": r.review_label in ac_revs, "AC_out": r.review_label in ac_revs}
        bad = [k for k in exp if present[k] and exp[k] != obs[k]]
        if not any(present.values()):
            checks.append("none"); notes.append("")
        elif bad:
            checks.append("mismatch"); notes.append("view_disagrees:" + ",".join(bad))
        else:
            checks.append("ok"); notes.append("")
    moves = moves.assign(view_check=checks, view_note=notes)
    keys = {(a, b) for a, b in zip(moves["review_label"], moves["sedol"])}
    orphans = []
    for name, sec in (("All-World", AW_SECTIONS), ("All-Cap", ALLCAP_SECTIONS)):
        v = raw[raw["section"].isin(sec)]
        for r in v.itertuples(index=False):
            if (r.review_label, r.sedol) not in keys:
                orphans.append({"view": name, "review_label": r.review_label, "sedol": r.sedol,
                                "change": r.change, "ftse_name": r.ftse_name})
    return moves, pd.DataFrame(orphans)


def ftse_third_friday(year: int, month: int) -> pd.Timestamp:
    d = pd.Timestamp(year=year, month=month, day=1)
    first_fri = d + pd.Timedelta(days=(4 - d.weekday()) % 7)
    return first_fri + pd.Timedelta(days=14)


def check_ftse_rule(cal: pd.DataFrame, min_years: int = 4) -> dict:
    """Empirically check FTSE's date rule on semi-annual calendar rows.

    passive rule: passive trade date = third Friday of the review month.
    announce rule: announce = passive - K days with one constant K.
    A rule is 'confirmed' only if it holds on rows spanning >= min_years calendar years.
    """
    semi = cal[cal["review_type"] == "semi"].copy()
    semi["passive"] = pd.to_datetime(semi["passive_trade_date"])
    semi["ann"] = pd.to_datetime(semi["announce_date"])
    semi["year"] = semi["passive"].dt.year
    semi["offset"] = (semi["passive"] - semi["ann"]).dt.days
    passive_ok = semi["passive"] == [ftse_third_friday(p.year, p.month) for p in semi["passive"]]
    res = {"passive_rows_ok": int(passive_ok.sum()), "semi_rows": len(semi),
           "passive_years_ok": int(semi.loc[passive_ok, "year"].nunique()),
           "offset_by_row": dict(zip(semi["review_label"], semi["offset"]))}
    mode = semi["offset"].mode().iloc[0]
    # longest run of consecutive (chronological) rows sharing one offset, ending at the earliest rows
    semi = semi.sort_values("passive")
    first = semi["offset"].iloc[0]
    run = semi[(semi["offset"].ne(first)).cumsum() == 0]
    res.update({"early_offset": int(first), "early_offset_rows": len(run),
                "early_offset_years": int(run["year"].nunique()), "modal_offset": int(mode)})
    res["passive_rule_confirmed"] = bool(passive_ok.all() and res["passive_years_ok"] >= min_years)
    res["announce_rule_confirmed"] = bool(res["early_offset_years"] >= min_years)
    return res


def infer_ftse_2015(cal: pd.DataFrame, labels: list[str]) -> tuple[dict, dict]:
    """Dates for review labels missing from the calendar, only if the rule is confirmed."""
    rule = check_ftse_rule(cal)
    out = {}
    if rule["passive_rule_confirmed"] and rule["announce_rule_confirmed"]:
        for lab in labels:
            y, m = int(lab[5:9]), int(lab[10:12])
            passive = ftse_third_friday(y, m)
            out[lab] = {"announce_date": (passive - pd.Timedelta(days=rule["early_offset"])).date().isoformat(),
                        "passive_trade_date": passive.date().isoformat()}
    return out, rule


def build_ftse(raw: pd.DataFrame, cal: pd.DataFrame, sessions: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    moves, stats = ftse_moves(raw)
    moves, orphans = ftse_view_check(moves, raw)
    stats["orphans"] = orphans
    calx = cal.set_index("review_label")
    missing = sorted(set(moves["review_label"]) - set(calx.index))
    inferred, rule = infer_ftse_2015(cal, missing)
    stats["rule"] = rule
    stats["missing_calendar_labels"] = missing
    stats["inferred_labels"] = sorted(inferred)
    stats["disagreements"] = moves[moves["view_check"] == "mismatch"]
    stats["other_dropped_count"] = int((moves["change"] == "other").sum())
    rows = []
    for r in moves[moves["change"].notna()].itertuples(index=False):
        lab = r.review_label
        notes = []
        if lab in calx.index:
            c = calx.loc[lab]
            ann, pas, eff = c["announce_date"], c["passive_trade_date"], c["effective_date"]
            rtype = c["review_type"]
        elif lab in inferred:
            ann, pas = inferred[lab]["announce_date"], inferred[lab]["passive_trade_date"]
            eff, rtype = "", "semi"
            notes.append("dates_inferred_from_rule")
        else:
            ann = pas = eff = np.nan
            rtype = "semi"
            notes.append("dates_missing_no_calendar_row")
        if lab >= FTSE_FREEZE_FROM:
            rtype = "freeze"
        tk, conf = FTSE_TICKERS.get(r.sedol, ("", "low"))
        cross = r.view_check
        if r.view_note:
            notes.append(r.view_note)
        if conf == "low" or not tk:
            cross = "mismatch"
            notes.append("ticker_unresolved_or_ambiguous")
        if r.change == "other":
            notes.append("mid_large_move")
        if "multi" in (r.from_segment, r.to_segment):
            notes.append("multiple_segments_in_one_side")
        rows.append({
            "provider": "FTSE", "index_name": "FTSE_GEIS", "change": r.change, "review_label": lab,
            "review_type": rtype, "announce_ts_local": ann, "effective_date": eff,
            **map_dates(sessions, ann, pas), "source_doc": r.source_doc, "source_ref": f"sedol:{r.sedol}",
            "cross_check": cross, "note": "; ".join(notes), "ticker": tk, "map_confidence": conf,
            "provider_name": r.ftse_name, "from_segment": r.from_segment, "to_segment": r.to_segment,
            "source_tier": "primary", "_key": tk or r.sedol,
        })
    return pd.DataFrame(rows), stats


# ---------------------------------------------------------------------------
# Assemble + coverage
# ---------------------------------------------------------------------------

def finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["symbol"] = df["ticker"]
    df["event_id"] = (df["review_label"] + ":" + df["index_name"] + ":" + df["_key"].astype(str)
                      + ":" + df["change"])
    if df["event_id"].duplicated().any():
        dup = df.loc[df["event_id"].duplicated(keep=False), "event_id"].unique()[:5]
        raise ValueError(f"duplicate event_id, e.g. {list(dup)}")
    if (df["source_doc"].fillna("") == "").any():
        raise ValueError("event row without source_doc")
    for c in ("announce_session", "first_tradeable_session", "passive_trade_session", "effective_session"):
        df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d").fillna("")
    return df[EVENT_COLUMNS].sort_values(["provider", "review_label", "index_name", "change", "event_id"])


def local_tickers(daily_dir: Path = DAILY_DIR) -> set[str]:
    return {p.stem for p in stock_files(daily_dir)}


def coverage_tables(ev: pd.DataFrame, have: set[str]) -> dict[str, pd.DataFrame]:
    ev = ev.copy()
    ev["year"] = ev["review_label"].str[-7:-3]
    cov = (ev.groupby(["provider", "index_name", "change", "year"]).size().unstack("year", fill_value=0))
    cov["total"] = cov.sum(axis=1)
    rev = ev.groupby(["provider", "index_name"])["review_label"].nunique().rename("n_reviews").to_frame()
    has_tk = ev["ticker"].fillna("") != ""
    nobars = ev[has_tk & ~ev["ticker"].isin(have)]
    miss = (nobars.groupby(["provider", "ticker"]).agg(
        events=("event_id", "size"),
        changes=("change", lambda s: ",".join(sorted(set(s)))),
        first_review=("review_label", "min"), last_review=("review_label", "max")).reset_index())
    unresolved = ev[~has_tk | (ev["cross_check"] == "mismatch") & (ev["map_confidence"] == "low")]
    by_change_missing = (nobars.groupby(["provider", "index_name", "change"]).size().rename("events_no_bars"))
    return {"coverage": cov, "reviews": rev, "missing_bars": miss, "unresolved": unresolved,
            "missing_by_change": by_change_missing.to_frame()}


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sessions = trading_sessions()
    print(f"sessions: {len(sessions)} ({sessions[0].date()} .. {sessions[-1].date()})")
    msci_raw = pd.read_csv(DATA_DIR / "msci_raw.csv", dtype=str, keep_default_na=False)
    ftse_raw = pd.read_csv(DATA_DIR / "ftse_raw.csv", dtype={"sedol": str})
    cal = pd.read_csv(DATA_DIR / "ftse_calendar.csv", dtype=str, keep_default_na=False)
    msci = build_msci(msci_raw, sessions)
    ftse, fstats = build_ftse(ftse_raw, cal, sessions)
    ev = finalize(pd.concat([msci, ftse], ignore_index=True))
    ev.to_csv(EVENTS_CSV, index=False)
    print(f"wrote {EVENTS_CSV} rows={len(ev)}")

    print("\n== FTSE ==")
    print(f"Micro-only / none->Micro moves dropped: {fstats['micro_only_dropped']}")
    print(f"Mid<->Large 'other' moves (kept, tagged): {fstats['other_dropped_count']}")
    rule = fstats["rule"]
    print(f"FTSE rule check: {rule}")
    print(f"labels without calendar row: {fstats['missing_calendar_labels']}; inferred: {fstats['inferred_labels']}")
    dis = fstats["disagreements"]
    print(f"index-view disagreements: {len(dis)}")
    if len(dis):
        print(dis[["review_label", "sedol", "ftse_name", "from_segment", "to_segment", "change", "view_note"]].to_string())
    orph = fstats["orphans"]
    print(f"index-view rows with no segment move: {len(orph)}")
    if len(orph):
        print(orph.to_string())

    tabs = coverage_tables(ev, local_tickers())
    for k, t in tabs.items():
        t.to_csv(OUT_DIR / f"coverage_{k}.csv")
    pd.set_option("display.width", 250, "display.max_columns", 50, "display.max_rows", 500)
    print("\n== coverage: events by provider x index x change x review year ==")
    print(tabs["coverage"].to_string())
    print(tabs["reviews"].to_string())
    miss = tabs["missing_bars"]
    print(f"\nevents whose ticker has no local bars: {int(miss['events'].sum()) if len(miss) else 0} "
          f"({len(miss)} tickers)")
    print(miss.to_string())
    print(f"\nunresolved/low-confidence tickers: {len(tabs['unresolved'])}")
    nodate = ev[ev["announce_session"] == ""]
    print(f"events without session dates: {len(nodate)} ({sorted(nodate['review_label'].unique())})")


if __name__ == "__main__":
    main()
