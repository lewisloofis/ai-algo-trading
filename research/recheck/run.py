"""
recheck/run.py — the closed-family re-check (plan 26): post-verdict window, milestones, alert bar, output.

    cd research && python -m recheck.run --as-of 2026-12-01      # or: make recheck [AS_OF=YYYY-MM-DD]

For each spec in ``recheck.specs.SPECS`` it builds the family's records for signals strictly after the
family's verdict ``decided`` date (up to ``--as-of``), counts effective N, and:

  * N below the next unevaluated milestone (30 / 60 / 120): prints "waiting, N of M" and decides nothing;
  * otherwise evaluates the highest milestone reached that is not already in the ledger, once, and stores it
    in ``results/recheck/milestones.json``; a second run at the same N reports the stored entry.

An ALERT needs edge over the null after current cost > 0, one-sided p < 0.10 / (3 * K), and drop-top-5 > 0.
An alert is a request for a new pre-registered plan, never a verdict. Writes ``<as-of>.md`` and ``<as-of>.json``
next to the ledger. Reads only local daily parquet files; no network, no credentials, no orders.

Exit codes: 0 ok; 2 refused (as-of in the future, stale daily data, unreadable ledger, bad arguments).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

from recheck import ledger as L
from recheck import loaders
from recheck import specs as SP
from recheck import stats as ST
from strategy_fit.research_log import WIB
from strategy_fit.study_verdicts import DEFAULT_RESULTS_DIR, Loaded, load_verdicts

SCHEMA_VERSION = 1
STALE_DAYS = 10
DEFAULT_DAILY_DIR = os.path.join(os.path.dirname(DEFAULT_RESULTS_DIR), "data", "daily")
DEFAULT_OUT_DIR = os.path.join(DEFAULT_RESULTS_DIR, "recheck")


class Refused(Exception):
    """The run must not start; main() prints the message and exits 2."""


def _num(x: Any) -> Optional[float]:
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)


def impact_coeffs_info() -> dict:
    """Which impact coefficient file this run priced with (the production file every spec resolves through)."""
    from strategy_fit import impact

    path = impact._COEFFS_PATH
    try:
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        sha = "unreadable"
    return {"file": os.path.basename(path), "sha256": sha}


def check_inputs(as_of: date, daily_dir: str, today: date) -> None:
    if as_of > today:
        raise Refused(f"--as-of {as_of} is in the future (today is {today}); nothing after today can be re-checked")
    latest = loaders.latest_bar_date(daily_dir)
    if latest is None:
        raise Refused(f"no daily bars found in {daily_dir}; run: make refresh-prices")
    if latest < as_of - timedelta(days=STALE_DAYS):
        raise Refused(
            f"daily data is stale: the latest bar is {latest}, more than {STALE_DAYS} days before --as-of {as_of}. "
            "Run: make refresh-prices")


def _evaluation_entry(milestone: int, as_of: date, ev: ST.Evaluation, alert: bool) -> dict:
    return {"milestone": milestone, "as_of": as_of.isoformat(), "edge": _num(ev.edge), "p": _num(ev.p),
            "drop5": _num(ev.drop5), "alert": bool(alert), "n": ev.n, "cost": _num(ev.cost)}


def _family_entry(spec: SP.RecheckSpec, verdict, n: int, ledger_before: dict, milestone: Optional[int],
                  entry: Optional[dict], regime: Optional[dict]) -> dict:
    done_after = sorted(set(L.done(ledger_before, spec.family_id)) | ({milestone} if milestone else set()))
    stored = entry if entry else L.latest(ledger_before, spec.family_id)
    status = "evaluated_now" if entry else ("evaluated_earlier" if stored else "waiting")
    return {
        "family_id": spec.family_id, "verdict_date": verdict.decided, "verdict_outcome": verdict.outcome,
        "source": spec.source, "hold": spec.hold, "entry": spec.entry, "unit": spec.unit,
        "n": n, "next_milestone": ST.next_milestone(done_after), "status": status,
        "evaluation": stored, "regime_split": regime if entry else None,
    }


def run_family(spec: SP.RecheckSpec, verdict, daily_dir: str, as_of: date, ledger_path: str, p_threshold: float,
               calendar: Sequence[str]) -> dict:
    ledger_before = L.load(ledger_path)
    records = spec.build(daily_dir, date.fromisoformat(verdict.decided), as_of)
    n = ST.effective_n(records, spec.unit_hold, calendar)
    milestone = ST.milestone_to_evaluate(n, L.done(ledger_before, spec.family_id))
    entry = regime = None
    if milestone is not None:
        ev = ST.evaluate(records, spec.unit_hold, calendar)
        entry = _evaluation_entry(milestone, as_of, ev, ST.is_alert(ev, p_threshold))
        regime = ST.regime_split(records, spec.unit_hold, calendar)
        L.append(ledger_path, spec.family_id, entry)
    return _family_entry(spec, verdict, n, ledger_before, milestone, entry, regime)


def _notes(specs: Sequence[SP.RecheckSpec], daily_dir: str, as_of: date) -> list[str]:
    notes = []
    if any(s.family_id == "cross_sectional" for s in specs):
        n = len(loaders.load_universe_bars(daily_dir, as_of))
        notes.append("cross_sectional universe differs from the verdict's (S.txt not in repo): "
                     f"traded-within-{loaders.UNIVERSE_RECENCY_DAYS}-days, {n} names")
    return notes


def recheck(as_of: date, daily_dir: str, out_dir: str, verdicts: Loaded, specs: Sequence[SP.RecheckSpec] = SP.SPECS,
            excluded: Optional[dict[str, str]] = None, catalog: Optional[Iterable[str]] = None,
            k: int = SP.K, calendar: Optional[Sequence[str]] = None) -> dict:
    """Run every spec, update the ledger, write ``<as-of>.json`` / ``.md``; return the report document."""
    excluded = SP.EXCLUDED if excluded is None else excluded
    ledger_path = os.path.join(out_dir, L.FILENAME)
    L.load(ledger_path)                       # an unreadable ledger stops the run before anything is evaluated
    threshold = ST.alert_p_threshold(k)
    if calendar is None:
        calendar = loaders.trading_calendar(daily_dir, as_of)
    families, spec_ids = [], {s.family_id for s in specs}
    for spec in specs:
        verdict = verdicts.verdicts.get(spec.family_id)
        if verdict is None:
            families.append({"family_id": spec.family_id, "verdict_date": None, "status": "no_verdict",
                             "reason": "not checked: no readable verdict file for this family",
                             "n": None, "next_milestone": None, "evaluation": None})
            continue
        families.append(run_family(spec, verdict, daily_dir, as_of, ledger_path, threshold, calendar))
    not_rechecked = [{"family_id": f, "reason": r} for f, r in excluded.items() if f not in spec_ids]
    listed = spec_ids | set(excluded)
    for f in sorted(set(catalog or ()) - listed):
        not_rechecked.append({"family_id": f, "reason": SP.UNTESTED_NOTE})
    doc = {
        "schema": SCHEMA_VERSION, "as_of": as_of.isoformat(), "k": k, "n_looks": ST.N_LOOKS,
        "alpha_family": ST.ALPHA_FAMILY, "alert_p_threshold": threshold, "milestones": list(ST.MILESTONES),
        "impact_coeffs": impact_coeffs_info(), "notes": _notes(specs, daily_dir, as_of), "families": families,
        "excluded": not_rechecked,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{as_of.isoformat()}.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    with open(os.path.join(out_dir, f"{as_of.isoformat()}.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(doc))
    return doc


def _fmt(x: Optional[float], nd: int = 1) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def _p_fmt(p: Optional[float]) -> str:
    return "-" if p is None else (f"{p:.4f}" if p >= 0.0001 else f"{p:.1e}")


def _row(f: dict) -> str:
    fid = f["family_id"]
    if f["status"] == "no_verdict":
        return f"| {fid} | - | {f['reason']} | - | - | - | - | - | - |"
    ev = f["evaluation"]
    nxt = f["next_milestone"]
    progress = f"{f['n']} of {nxt}" if nxt else f"{f['n']} (all looks used)"
    if f["status"] == "waiting":
        status = f"waiting, {progress}"
        return f"| {fid} | {f['verdict_date']} | {status} | {progress} | - | - | - | - | - |"
    stamp = "evaluated now" if f["status"] == "evaluated_now" else "stored result"
    status = f"milestone {ev['milestone']} ({stamp}" + (f"; waiting for {nxt}" if nxt else "") + ")"
    return (f"| {fid} | {f['verdict_date']} | {status} | {progress} | {_fmt(ev['edge'])} | {_fmt(ev['cost'])} | "
            f"{_p_fmt(ev['p'])} | {_fmt(ev['drop5'])} | {'**ALERT**' if ev['alert'] else 'no'} |")


def render_markdown(doc: dict) -> str:
    coeffs = doc["impact_coeffs"]
    lines = [
        f"# Closed-family re-check, as of {doc['as_of']}", "",
        f"Alert bar: edge over the always-long null after current cost > 0, one-sided p < "
        f"{doc['alpha_family']:.2f} / ({doc['n_looks']} x {doc['k']}) = {doc['alert_p_threshold']:.5f}, "
        f"and drop-top-5 > 0. Milestones at N = {', '.join(map(str, doc['milestones']))} independent observations. "
        "An alert only earns a new pre-registered plan; it is not a verdict.",
        f"Impact coefficients: `{coeffs['file']}` (sha256 {coeffs['sha256'][:12]}). Edge, cost and drop-5 are bps per "
        "observation unit (a non-overlapping block of `hold` trading days, or one period); p is Newey-West HAC.", "",
        "| family | verdict date | status | N vs next milestone | edge (bps) | cost (bps) | p | drop-5 (bps) | ALERT |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row(f) for f in doc["families"]]
    lines += [f"\nNote: {n}" for n in doc.get("notes", [])]
    splits = [(f["family_id"], f["regime_split"]) for f in doc["families"] if f.get("regime_split")]
    if splits:
        lines += ["", "Regime split at evaluation (reported, never used as a filter):"]
        for fid, s in splits:
            lines.append(f"* {fid}: bear n={s['bear']['n']} edge={_fmt(_num(s['bear']['edge']))}; "
                         f"non-bear n={s['nonbear']['n']} edge={_fmt(_num(s['nonbear']['edge']))}")
    lines += ["", "## Not re-checked", "", "| family | reason |", "|---|---|"]
    lines += [f"| {e['family_id']} | {e['reason']} |" for e in doc["excluded"]]
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m recheck.run", description=__doc__.split("\n\n")[0])
    p.add_argument("--as-of", required=True, type=date.fromisoformat, help="YYYY-MM-DD, not in the future")
    p.add_argument("--daily-dir", default=DEFAULT_DAILY_DIR)
    p.add_argument("--results-dir", default=DEFAULT_RESULTS_DIR, help="where the study verdict files live")
    p.add_argument("--out-dir", default=None, help="default: <results-dir>/recheck")
    return p


def main(argv: Optional[list[str]] = None, today: Optional[date] = None, specs: Sequence[SP.RecheckSpec] = SP.SPECS,
         verdicts: Optional[Loaded] = None, excluded: Optional[dict[str, str]] = None,
         catalog: Optional[Iterable[str]] = None, k: int = SP.K, calendar: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    today = today or datetime.now(WIB).date()
    out_dir = args.out_dir or os.path.join(args.results_dir, "recheck")
    try:
        check_inputs(args.as_of, args.daily_dir, today)
        if verdicts is None:
            verdicts = load_verdicts(args.results_dir)
            for problem in verdicts.problems:
                print(f"PROBLEM: {problem}", file=sys.stderr)
        if catalog is None:
            from strategy_fit.catalog_meta import ALL_FAMILIES

            catalog = [m.family_id for m in ALL_FAMILIES]
        doc = recheck(args.as_of, args.daily_dir, out_dir, verdicts, specs, excluded, catalog, k, calendar)
    except (Refused, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(render_markdown(doc))
    print(f"wrote {os.path.join(out_dir, args.as_of.isoformat())}.md / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
