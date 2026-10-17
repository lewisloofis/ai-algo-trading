"""
dashboard/strategy_status.py — the data behind the Strategies page: where each strategy stands, which
families we consider, which stocks are screened in, and which strategy + stock pairs look promising.

Pure functions over already-loaded inputs. Every source is optional: a missing or unreadable file gives
``None`` plus a plain reason, and the page says "not checked" with the command that produces the file.
Nothing is re-judged: gate results, edges and costs are read from ``results/shortlist/shortlist.json`` (written
by strategy_fit.shortlist_2026_10); stages come from the emitter registry's own ``status``; run state
comes from live.py. Strategy ids are joined only between the emitter registry and the heartbeat files
(both write the same id); runtime ids are never matched to research family names (strategy_map.py).
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from dashboard import strategy_words as W

SHORTLIST_REL = os.path.join("results/shortlist", "shortlist.json")
STALE_AFTER_DAYS = 7
TOP_COMBOS = 15
GATE_KEYS = ("G2", "G3", "G4", "G5", "G7")


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Shortlist:
    as_of: str
    n_names: int
    name_rows: tuple[Mapping[str, Any], ...]
    pooled: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class Universe:
    as_of: str
    eligible: tuple[str, ...]
    not_tradable: tuple[str, ...]


@dataclass(frozen=True)
class Inputs:
    shortlist: Shortlist | None
    shortlist_problem: str
    universe: Universe | None
    stored_symbols: tuple[str, ...]
    specs: Mapping[str, Any] | None
    """The emitter registry (strategy_id -> EmitterSpec); None = strategy_fit could not be imported."""
    verdicts: Any = None
    """strategy_fit.study_verdicts.Loaded; None = not loaded (see verdicts_problem)."""
    verdicts_problem: str = ""


def load_shortlist(research_dir: str) -> tuple[Shortlist | None, str]:
    path = os.path.join(research_dir, SHORTLIST_REL)
    if not os.path.exists(path):
        return None, "file not found"
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        rows = tuple(r for r in doc["name_rows"] if isinstance(r, dict))
        pooled = tuple(r for r in doc.get("pooled", ()) if isinstance(r, dict))
        return Shortlist(str(doc["as_of"]), int(doc.get("n_names") or 0), rows, pooled), ""
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"could not be read ({type(exc).__name__})"


def load_universe(daily_dir: str) -> Universe | None:
    try:
        from screener.universe_status import load_universe_status
    except Exception:  # noqa: BLE001
        return None
    st = load_universe_status(daily_dir)
    if st is None:
        return None
    return Universe(st.as_of.isoformat(), tuple(sorted(st.eligible)), tuple(sorted(st.not_tradable())))


def load_specs():
    try:
        from strategy_fit.emitter_specs import SPECS
    except Exception:  # noqa: BLE001
        return None
    return SPECS


def load_study_verdicts(research_dir: str) -> tuple[Any, str]:
    """(Loaded, "") or (None, reason): a missing module or a bad folder never crashes the build."""
    try:
        from strategy_fit.study_verdicts import load_verdicts
        return load_verdicts(os.path.join(research_dir, "results")), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"the study verdicts could not be loaded ({type(exc).__name__}: {exc})"


def gather_inputs(research_dir: str, daily_dir: str) -> Inputs:
    shortlist, problem = load_shortlist(research_dir)
    verdicts, verdicts_problem = load_study_verdicts(research_dir)
    stored = tuple(sorted(os.path.basename(p)[:-8] for p in glob.glob(os.path.join(daily_dir, "*.parquet"))))
    return Inputs(shortlist, problem, load_universe(daily_dir), stored, load_specs(), verdicts, verdicts_problem)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def gate_results(row: Mapping[str, Any]) -> dict[str, bool]:
    """{'G2': passed, ...} from the row's ``gates`` dict (keys look like 'G3_edge_net_both_fits>0')."""
    gates = row.get("gates") or {}
    return {k.split("_", 1)[0]: bool(v) for k, v in gates.items() if isinstance(k, str)}


def failed_gate_words(row: Mapping[str, Any]) -> list[str]:
    res = gate_results(row)
    return [W.GATE_FAIL_WORDS[g] for g in GATE_KEYS if g in res and not res[g] and g in W.GATE_FAIL_WORDS]


def passed_gate_words(row: Mapping[str, Any]) -> list[str]:
    res = gate_results(row)
    return [W.GATE_PASS_WORDS[g] for g in GATE_KEYS if res.get(g)]


def n_gates_passed(row: Mapping[str, Any]) -> tuple[int, int]:
    res = gate_results(row)
    return sum(1 for g in GATE_KEYS if res.get(g)), len([g for g in GATE_KEYS if g in res])


def state_words(raw: str) -> str:
    """Plain wording of the shortlist's chain state, e.g. 'IN POSITION (exit in 2 session(s))'."""
    s = (raw or "").strip()
    low = s.lower()
    if low.startswith("in position"):
        tail = s[len("IN POSITION"):].strip()
        if tail.startswith("(") and tail.endswith(")"):
            tail = tail[1:-1]
        tail = tail.replace("session(s)", "sessions").replace("1 sessions", "1 session")
        return "In position" + (f", {tail}" if tail else "")
    if low.startswith("enter"):
        return "Signal today: enter next session"
    if low.startswith("flat"):
        return "Flat, waiting for a signal"
    return s or "unknown"


def hold_text(hold: Any) -> str:
    try:
        h = int(hold)
    except (TypeError, ValueError):
        return "?"
    return f"{h} day{'s' if h != 1 else ''}"


def family_label(family: str) -> str:
    return family.replace("_", " ").capitalize()


def shortlist_age_days(sl: Shortlist, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(sl.as_of)).days
    except ValueError:
        return None


def row_matches_spec(row: Mapping[str, Any], spec) -> bool:
    return (tuple(spec.symbols) == (row.get("symbol"),) and spec.family == row.get("family")
            and int(spec.hold) == int(row.get("hold", -1)))


# ---------------------------------------------------------------------------
# A. Where each strategy stands
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StageRow:
    stage: str
    name: str
    sub: str
    lines: tuple[str, ...]
    """Plain-text lines for the 'where it stands' cell (escaped by the renderer)."""
    paper: Any = None
    """The paper_tests.PaperRow for this strategy, if any."""


def _running(live, strategy_id: str, real_money: bool) -> bool:
    if live is None:
        return False
    return any(r.state == "online" and r.strategy_id == strategy_id and (r.mode == "real money") == real_money
               for r in live.runs)


def _shortlist_row(sl: Shortlist | None, spec):
    if sl is None:
        return None
    return next((r for r in sl.name_rows if row_matches_spec(r, spec)), None)


def _spec_sub(spec) -> str:
    return f"{', '.join(spec.symbols)} · {family_label(spec.family)} · hold {hold_text(spec.hold)}"


def _paper_lines(p, running: bool, fail_words: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    if p is None:
        out.append("Paper results could not be read.")
    elif p.error is not None:
        out.append(f"Could not read paper results: {p.error}")
    elif not p.started:
        out.append(f"Not started yet: 0 days run, 0 / {p.min_trips} round trips.")
    else:
        hold = ", ".join(f"{n} lot{'s' if n != 1 else ''} {s}" for s, n in p.holding) or "flat"
        out.append(f"{p.days_run} day(s) run, {p.n_trips} / {p.min_trips} round trips, holding: {hold}.")
        out.append(f"Verdict: {p.verdict}.")
    if running:
        out.insert(0, "Running now.")
    if fail_words:
        out.append("Still failing in the backtest: " + "; ".join(fail_words) + ".")
    return tuple(out)


def stage_rows(specs: Mapping[str, Any] | None, paper_rows: Sequence | None, live, sl: Shortlist | None) -> list[StageRow]:
    """One row per strategy with an operational definition, plus the shortlist names that need more study."""
    if specs is None:
        return []
    paper_by_id = {p.strategy_id: p for p in (paper_rows or ())}
    out: list[StageRow] = []
    for spec in specs.values():
        status = spec.status
        if status not in ("candidate", "paper", "retired"):
            continue  # 'example' entries are test fixtures, not strategies
        note = getattr(spec, "status_note", "") or ""
        p = paper_by_id.get(spec.strategy_id)
        row = _shortlist_row(sl, spec)
        fail = failed_gate_words(row) if row else []
        if status == "candidate":
            on = _running(live, spec.strategy_id, True)
            lines = ("Running with real money now." if on else "Not running. Cleared the bar for a live test; not yet started.",)
            out.append(StageRow("live" if on else "live_candidate", spec.strategy_id, _spec_sub(spec), lines + _paper_lines(p, False, []), p))
        elif status == "paper":
            on = _running(live, spec.strategy_id, False)
            out.append(StageRow("paper", spec.strategy_id, _spec_sub(spec), _paper_lines(p, on, fail), p))
        else:
            out.append(StageRow("retired", spec.strategy_id, _spec_sub(spec), (note or "Retired.",), p))
    out.extend(_study_rows(specs, sl))
    return sorted(out, key=lambda r: W.STAGE_ORDER.index(r.stage))


def _study_rows(specs: Mapping[str, Any], sl: Shortlist | None) -> list[StageRow]:
    if sl is None:
        return []
    defined = list(specs.values())
    rows = [r for r in sl.name_rows
            if n_gates_passed(r)[0] >= 1 and not any(row_matches_spec(r, s) for s in defined)]
    rows.sort(key=lambda r: (-n_gates_passed(r)[0], -float(r.get("edgenet_1lot") or -1e9)))
    out = []
    for r in rows:
        fails = failed_gate_words(r)
        lines = ("Close, but not good enough yet. Fails: " + "; ".join(fails) + ".",)
        out.append(StageRow("study", f"{r.get('symbol')} {family_label(str(r.get('family')))}",
                            f"hold {hold_text(r.get('hold'))} · no running definition yet", lines))
    return out


def stage_counts(rows: Sequence[StageRow]) -> dict[str, int]:
    return {s: sum(1 for r in rows if r.stage == s) for s in W.STAGE_ORDER}


# ---------------------------------------------------------------------------
# D. Promising combinations
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Combo:
    symbol: str
    family: str
    hold: int
    n_trades: int
    edge_after_costs_bps: float
    drop5_ok: bool
    drop5_bps: float | None
    oos_edge_bps: float | None
    oos_n: int | None
    gates_passed: int
    gates_total: int
    gates_words: tuple[str, ...]
    state: str
    lot_notional: float | None
    trip_cost: float | None
    month_cost: float | None
    on_paper: bool


def _combo(r: Mapping[str, Any], specs) -> Combo:
    res = gate_results(r)
    k, total = n_gates_passed(r)
    return Combo(
        str(r.get("symbol")), str(r.get("family")), int(r.get("hold", 0)), int(r.get("n") or 0),
        float(r["edgenet_1lot"]), bool(res.get("G4")), r.get("drop5_net_1lot"),
        r.get("wf_oos_edge"), r.get("wf_oos_n"), k, total, tuple(passed_gate_words(r)),
        state_words(str(r.get("state", ""))), r.get("lot_notional"), r.get("trip_cost_idr"),
        r.get("month_cost_idr"),
        any(s.status == "paper" and row_matches_spec(r, s) for s in (specs or {}).values()),
    )


def promising_combos(sl: Shortlist | None, specs) -> list[Combo]:
    """Rows whose edge over just holding is positive after one-lot costs, best first."""
    if sl is None:
        return []
    rows = [r for r in sl.name_rows if isinstance(r.get("edgenet_1lot"), (int, float)) and r["edgenet_1lot"] > 0]
    combos = [_combo(r, specs) for r in rows]
    return sorted(combos, key=lambda c: (-c.gates_passed, -c.edge_after_costs_bps))


# ---------------------------------------------------------------------------
# Recent runs (history that used to sit on the Overview)
# ---------------------------------------------------------------------------

RUN_HISTORY_DAYS = 14


def recent_runs(summary) -> list:
    """Runs that are not online, newest first (live.collect already orders by recency)."""
    return [r for r in (summary.runs if summary else ()) if r.state != "online"]

