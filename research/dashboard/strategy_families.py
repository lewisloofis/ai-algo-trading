"""
dashboard/strategy_families.py — "Strategy families we consider": one row per catalog family with our
current result in plain words, derived from the backtest cells and the shortlist (nothing invented).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dashboard import strategy_words as W
from dashboard.strategy_status import Shortlist, hold_text, n_gates_passed

@dataclass(frozen=True)
class FamilyRow:
    family_id: str
    bet: str
    rule: str
    horizon: str
    status: str
    status_note: str
    headline: str
    tone: str
    evidence: tuple[str, ...]
    test_outcome: str = "untested"


_OUTCOME_HEADLINES = {
    "PASS": ("Passed its test", "green"),
    "FAIL": ("Failed its test", "red"),
    "NO_GO": ("Failed its test (no-go)", "red"),
    "INCONCLUSIVE": ("Inconclusive: needs more data", "amber"),
}


def _horizon(meta) -> str:
    return f"{meta.horizon_label} (typical hold {meta.char_hold_days} day{'s' if meta.char_hold_days != 1 else ''})" \
        if meta.char_hold_days else meta.horizon_label


_SCORED_GATES = ("passed_all_gates", "cost_gated", "fdr_rejected", "loses_to_null", "oos_not_persistent",
                 "impact_not_priced")


def _backtest_evidence(words: W.FamilyWords, run_cells) -> tuple[str | None, int]:
    """(sentence, number of scored cases); None when the run has no case of this family."""
    cells = [c for c in (run_cells or ()) if c.family in words.run_families]
    if not cells:
        return None, 0
    scored = [c for c in cells if c.gate in _SCORED_GATES]
    passed = sum(1 for c in scored if c.gate == "passed_all_gates")
    if not scored:
        return f"Backtest: {len(cells):,} case(s) listed, none scored yet.", 0
    return (f"Backtest: {len(scored):,} case(s) scored, {passed} cleared the cost and significance gates "
            "(before the extra checks on the shortlist)."), len(scored)


def _pooled_evidence(words: W.FamilyWords, sl: Shortlist | None) -> tuple[str | None, float | None]:
    rows = [p for p in (sl.pooled if sl else ()) if p.get("family") in words.pooled_families
            and p.get("edgenet_1lot") is not None]
    if not rows:
        return None, None
    best = max(rows, key=lambda p: p["edgenet_1lot"])
    p_best = min((p.get("p_holm_30") for p in rows if p.get("p_holm_30") is not None), default=None)
    text = (f"All {best.get('names', '?')} stocks pooled: best after-cost edge {best['edgenet_1lot']:+.0f} bps "
            f"(hold {hold_text(best.get('hold'))})")
    if p_best is not None:
        text += f", best adjusted p-value {p_best:.2f} (needs under 0.10)"
    return text + ".", float(best["edgenet_1lot"])


def _family_row(fid: str, words: W.FamilyWords, horizon: str, status: str, note: str,
                run_cells, sl: Shortlist | None, specs, verdict=None) -> FamilyRow:
    outcome = verdict.outcome if verdict else "untested"
    ev: list[str] = [f"Test ({verdict.decided}, {verdict.source}): {verdict.summary}"] if verdict else []
    bt, n_scored = _backtest_evidence(words, run_cells)
    pooled, best_net = _pooled_evidence(words, sl)
    for x in (bt, pooled):
        if x:
            ev.append(x)
    spec_list = [s for s in (specs or {}).values() if s.family in words.pooled_families + (fid,)]
    n_live = sum(1 for s in spec_list if s.status == "candidate")
    n_paper = sum(1 for s in spec_list if s.status == "paper")
    names = 0
    if sl:
        names = sum(1 for r in sl.name_rows if r.get("family") in words.pooled_families and n_gates_passed(r)[0] >= 1)
    if names:
        ev.append(f"{names} individual stock(s) pass at least one shortlist check.")
    if n_live:
        head, tone = f"{n_live} candidate(s) for live trading", "green"
    elif n_paper:
        head, tone = f"{n_paper} name(s) on paper", "amber"
    elif outcome in _OUTCOME_HEADLINES:
        head, tone = _OUTCOME_HEADLINES[outcome]
    elif status != "testable_now":
        head, tone = "Not testable yet", "grey"
    elif n_scored == 0 and pooled is None:
        head, tone = "Not scored in the latest run", "grey"
    elif best_net is not None and best_net > 0:
        head, tone = "Some promise, not proven", "amber"
    else:
        head, tone = "Nothing passes after costs", "red"
    return FamilyRow(fid, words.bet, words.rule, horizon, status, note, head, tone, tuple(ev), outcome)


def family_rows(run_cells, sl: Shortlist | None, specs, verdicts=None) -> list[FamilyRow]:
    """``verdicts`` is a study_verdicts.Loaded (or None: every family is untested)."""
    decided = verdicts.verdicts if verdicts else {}
    try:
        from strategy_fit.catalog_meta import ALL_FAMILIES
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in ALL_FAMILIES:
        words = W.FAMILY_WORDS.get(m.family_id, W.FamilyWords("(no description yet)", "(no description yet)"))
        out.append(_family_row(m.family_id, words, _horizon(m), m.catalog_status, m.deferred_note, run_cells, sl,
                               specs, decided.get(m.family_id)))
    return out


