"""
The wording of a verdict is the product: pin it.

Gate outcomes are the harness's (results.GATE_*); these tests only check that each
outcome is worded plainly and that the 5-best-trades check uses the harness's TOP_N.
"""
from __future__ import annotations

import os

import pytest

from dashboard import results as R
from dashboard.verdicts import CANDIDATE, NOT_TRADEABLE, verdict_for
from strategy_fit.compare_d1_rescore import TOP_N


def cell(gate, **kw):
    base = dict(symbol="XXXX", family="daily_momentum", hold=2, shape="single_name_time_series", reason="")
    base.update(kw)
    return R.ResultCell(gate=gate, **base)


def test_cost_failure_is_worded_as_losing_after_costs():
    v = verdict_for(cell(R.GATE_COST), avg_net_pct=-1.59, avg_net_without_best_pct=-2.0, n_trades=70)
    assert v.sentence == "Not tradeable — loses after costs"
    assert not v.tradeable and v.headline == NOT_TRADEABLE
    assert "-1.59%" in v.checks[0].detail


def test_survivor_without_its_best_trades_is_not_tradeable():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, avg_net_pct=1.8, avg_net_without_best_pct=-1.66, n_trades=75)
    assert v.sentence == f"Not tradeable — profit disappears without its {TOP_N} best trades"


def test_survivor_that_holds_up_is_a_candidate_never_tradeable_live_proof():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, avg_net_pct=2.0, avg_net_without_best_pct=0.4, n_trades=90)
    assert v.tradeable and v.headline == CANDIDATE
    assert "forward test" in v.sentence


def test_underpowered_survivor_is_called_unproven():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=True)
    v = verdict_for(c, avg_net_pct=2.0, avg_net_without_best_pct=0.4, n_trades=90)
    assert not v.tradeable and "unproven" in v.sentence


@pytest.mark.parametrize(
    "gate,phrase",
    [
        (R.GATE_FDR, "could easily be luck"),
        (R.GATE_NULL, "buying the stock on any day did about as well"),
        (R.GATE_OOS, "not confirmed on later, unseen data"),
        (R.GATE_IMPACT_UNPRICED, "cost of trading it has not been modelled"),
        (R.GATE_DECAYED, "faded"),
        (R.GATE_INSUFFICIENT_N, "too few independent trades"),
        (R.GATE_LONG_SHORT, "short-selling"),
    ],
)
def test_every_gate_failure_has_a_plain_sentence(gate, phrase):
    v = verdict_for(cell(gate))
    assert v.sentence.startswith("Not tradeable — ")
    assert phrase in v.sentence


def test_no_verdict_contains_jargon():
    banned = ("bps", "fdr", "null", "eff_n", "oos", "hurdle", "sharpe")
    for gate in (R.GATE_COST, R.GATE_FDR, R.GATE_NULL, R.GATE_OOS, R.GATE_PASSED_ALL, R.GATE_DECAYED):
        s = verdict_for(cell(gate, oos_underpowered=True), 1.0, 0.5, 80).sentence.lower()
        assert not any(b in s.split() for b in banned), s


def test_too_few_trades_to_trim_leaves_the_check_unjudged():
    c = cell(R.GATE_PASSED_ALL, oos_underpowered=False)
    v = verdict_for(c, avg_net_pct=1.0, avg_net_without_best_pct=None, n_trades=TOP_N)
    assert v.checks[-1].passed is None
    assert v.tradeable


def test_gate_vocabulary_is_the_harness_s():
    """The reason prefixes this dashboard keys on must still be what the harness writes."""
    here = os.path.dirname(os.path.abspath(__file__))
    src = ""
    for name in ("report.py", "run_strategy_fit.py", "daily_walk_forward.py"):
        with open(os.path.join(here, "..", "strategy_fit", name), encoding="utf-8") as f:
            src += f.read()
    for prefix, _gate in R._REASON_PREFIXES:
        assert prefix.split()[0] in src, f"harness no longer emits {prefix!r}"
