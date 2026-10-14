"""
dashboard/verdicts.py — one plain sentence per backtest cell.

The gates are the harness's: whether a cell lost after costs, could be luck, rode
the stock's own drift, or failed out-of-sample is what ``strategy_fit.report`` /
``run_strategy_fit`` already decided, carried here as ``results.GATE_*`` words.
This module only (a) words each outcome for a non-expert and (b) adds the one extra
check the D1 re-score introduced — the "without its 5 best trades" concentration
test from ``strategy_fit.compare_d1_rescore`` (same ``TOP_N``).

The wording is the product here, so it is pinned by test_verdicts.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy_fit.compare_d1_rescore import TOP_N

from dashboard import results as R

NOT_TRADEABLE = "Not tradeable"
CANDIDATE = "Candidate"


@dataclass(frozen=True)
class Check:
    """One line of the 'what it was tested against' list on a detail page."""

    name: str
    passed: bool | None
    """None = could not be judged for this cell."""
    detail: str


@dataclass(frozen=True)
class Verdict:
    tradeable: bool
    """True only for a cell that cleared every check. Never means 'live-proven'."""
    headline: str
    """Short label: 'Not tradeable' / 'Candidate'."""
    sentence: str
    """The full plain verdict, e.g. 'Not tradeable — loses after costs'."""
    checks: tuple[Check, ...]


def _pct(x: float) -> str:
    return f"{x:+.2f}%"


def verdict_for(
    cell: R.ResultCell,
    avg_net_pct: float | None = None,
    avg_net_without_best_pct: float | None = None,
    n_trades: int | None = None,
) -> Verdict:
    """
    The verdict for one cell.

    ``avg_net_pct`` / ``avg_net_without_best_pct`` come from the re-derived trade
    list (scorecard.trade_stats) and are optional: a cell with no trade list (an
    event-pooled cell) is judged on the harness's gates alone.
    """
    gate = cell.gate
    cost_ok = None if gate in (R.GATE_IMPACT_UNPRICED, R.GATE_OTHER) else gate != R.GATE_COST
    if gate == R.GATE_COST:
        cost_detail = "Loses money after costs." + (
            f" Average {_pct(avg_net_pct)} per trade." if avg_net_pct is not None else ""
        )
    elif cost_ok:
        cost_detail = "Average trade is positive after costs."
    else:
        cost_detail = "Not judged."

    not_luck = None
    if gate in (R.GATE_FDR,):
        not_luck = False
    elif gate in (R.GATE_NULL, R.GATE_OOS, R.GATE_PASSED_ALL):
        not_luck = True
    beats_hold = None
    if gate == R.GATE_NULL:
        beats_hold = False
    elif gate in (R.GATE_OOS, R.GATE_PASSED_ALL):
        beats_hold = True
    later_ok = None
    if gate == R.GATE_OOS:
        later_ok = False
    elif gate == R.GATE_PASSED_ALL:
        later_ok = True

    conc_ok = None
    if avg_net_without_best_pct is not None and (n_trades or 0) > TOP_N:
        conc_ok = avg_net_without_best_pct > 0
    conc_detail = (
        f"Average without its {TOP_N} best trades: {_pct(avg_net_without_best_pct)} per trade."
        if avg_net_without_best_pct is not None
        else f"Needs more than {TOP_N} trades to judge."
    )

    checks = (
        Check("Makes money after costs", cost_ok, cost_detail),
        Check(
            "Not just luck",
            not_luck,
            "Statistically convincing after correcting for the many strategies tried."
            if not_luck
            else ("Could easily be chance." if not_luck is False else "Not reached."),
        ),
        Check(
            "Beats simply holding the stock",
            beats_hold,
            "Gains are more than the stock's own rise would give."
            if beats_hold
            else (
                "Buying the stock on any day did about as well; the signal adds little."
                if beats_hold is False
                else "Not reached."
            ),
        ),
        Check(
            "Still works on later, unseen data",
            later_ok,
            "Held up on the later 40% of the history."
            if later_ok
            else (
                "No usable test on later data." if later_ok is False else "Not reached."
            ),
        ),
        Check(f"Does not depend on its {TOP_N} best trades", conc_ok, conc_detail),
    )

    def no(why: str) -> Verdict:
        return Verdict(False, NOT_TRADEABLE, f"{NOT_TRADEABLE} — {why}", checks)

    if gate == R.GATE_COST:
        return no("loses after costs")
    if gate == R.GATE_FDR:
        return no("the profit could easily be luck")
    if gate == R.GATE_NULL:
        return no("buying the stock on any day did about as well, so the signal adds nothing")
    if gate == R.GATE_OOS:
        return no("it was not confirmed on later, unseen data")
    if gate == R.GATE_IMPACT_UNPRICED:
        return no("the cost of trading it has not been modelled yet")
    if gate == R.GATE_DECAYED:
        return no("its edge faded over time")
    if gate == R.GATE_INSUFFICIENT_N:
        return no("too few independent trades to judge")
    if gate == R.GATE_LONG_SHORT:
        return no("it needs short-selling, which a retail IDX account cannot do")
    if gate in (R.GATE_BLOCKED, R.GATE_DEFERRED):
        return no("it has not been testable yet (data or capital missing)")
    if gate != R.GATE_PASSED_ALL:
        return no("it did not reach a clean pass")
    if conc_ok is False:
        return no(f"profit disappears without its {TOP_N} best trades")
    if cell.oos_underpowered:
        return Verdict(
            False,
            CANDIDATE,
            f"{CANDIDATE}, unproven — passes every check, but too few later-period "
            "trades to be sure; needs a forward test",
            checks,
        )
    return Verdict(
        True, CANDIDATE, f"{CANDIDATE} — passes every backtest check; still needs a forward test", checks
    )
