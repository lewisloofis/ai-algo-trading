"""
strategy_fit/test_triage_scorer.py — pytest suite for Plan 16 triage scorer.
"""

from __future__ import annotations

import re

import pytest

from strategy_fit.catalog_meta import ALL_FAMILIES
from strategy_fit.triage_scorer import (
    DataInventory,
    NameAnnotations,
    TriageResult,
    score_family,
)
from strategy_fit.families_single_name import ROUND_TRIP_BPS


_DAILY_MOMENTUM = next(f for f in ALL_FAMILIES if f.family_id == "daily_momentum")
_OBI_OFI_TICK = next(f for f in ALL_FAMILIES if f.family_id == "obi_ofi_tick")
_INTRADAY_MOMENTUM = next(f for f in ALL_FAMILIES if f.family_id == "intraday_momentum")
_MULTI_WEEK_TREND = next(f for f in ALL_FAMILIES if f.family_id == "multi_week_trend")

_STD_INV = DataInventory()
_MAX_HOLD = 2


def _itmg_like() -> NameAnnotations:
    return NameAnnotations(
        symbol="ITMG",
        min_spread_bps=9.0,
        min_edge_to_clear_bps=49.0,
        turnover_idr=145_160_000_000.0,
        vol_bps_day=88.0,
        is_edge_capped=False,
    )


def _bumi_like() -> NameAnnotations:
    return NameAnnotations(
        symbol="BUMI",
        min_spread_bps=59.5,
        min_edge_to_clear_bps=99.5,
        turnover_idr=168_990_000_000.0,
        vol_bps_day=103.0,
        is_edge_capped=True,
    )


def _noncore_name() -> NameAnnotations:
    return NameAnnotations(
        symbol="AAAA",
        min_spread_bps=30.0,
        min_edge_to_clear_bps=70.0,
        turnover_idr=50_000_000_000.0,
        vol_bps_day=100.0,
        is_edge_capped=False,
    )


def test_low_friction_testable_now() -> None:
    result = score_family(
        family=_DAILY_MOMENTUM,
        name=_itmg_like(),
        inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS,
        max_hold_days=_MAX_HOLD,
    )
    assert result.status == "testable_now", f"expected testable_now, got {result.status}"
    assert result.score > 1.0, f"expected score > 1.0, got {result.score}"


def test_high_friction_cost_gated() -> None:
    result = score_family(
        family=_OBI_OFI_TICK,
        name=_bumi_like(),
        inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS,
        max_hold_days=_MAX_HOLD,
    )
    assert result.status == "cost_gated", f"expected cost_gated, got {result.status}"


def test_data_blocked_low_n() -> None:
    thin_inv = DataInventory(
        daily_bar_trading_days=239,
        intraday_trading_days_core=60,
        intraday_trading_days_survivors=5,
        universe_size=5,
    )
    result = score_family(
        family=_INTRADAY_MOMENTUM,
        name=_noncore_name(),
        inv=thin_inv,
        fee_bps=ROUND_TRIP_BPS,
        max_hold_days=_MAX_HOLD,
    )
    assert result.status == "data_blocked", f"expected data_blocked, got {result.status}"
    rec = result.data_blocked_record
    assert rec is not None, "data_blocked_record must be populated"
    assert rec.gap_id != "", "gap_id must be non-empty"
    assert len(rec.gap_id) == 16, f"gap_id must be 16-char hex, got len={len(rec.gap_id)}"
    assert all(c in "0123456789abcdef" for c in rec.gap_id), "gap_id must be hex"
    allowed_kinds = {"length", "coverage", "granularity", "regime"}
    assert rec.kind in allowed_kinds, f"kind must be in {allowed_kinds}, got {rec.kind!r}"
    assert rec.target != "", "target must be non-empty"
    assert rec.need != "", "need must be non-empty"
    assert rec.have != "", "have must be non-empty"


def test_capital_blocked() -> None:
    result = score_family(
        family=_MULTI_WEEK_TREND,
        name=_itmg_like(),
        inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS,
        max_hold_days=_MAX_HOLD,
    )
    assert result.score == 0.0, f"expected score=0.0, got {result.score}"
    assert result.status in {"data_blocked", "cost_gated"}, (
        f"expected blocked status, got {result.status}"
    )


def test_score_monotone() -> None:
    low_spread = NameAnnotations(
        symbol="ITMG",
        min_spread_bps=9.0,
        min_edge_to_clear_bps=49.0,
        turnover_idr=145_160_000_000.0,
        vol_bps_day=88.0,
        is_edge_capped=False,
    )
    high_spread = NameAnnotations(
        symbol="DEWA",
        min_spread_bps=188.7,
        min_edge_to_clear_bps=228.7,
        turnover_idr=26_000_000_000.0,
        vol_bps_day=203.0,
        is_edge_capped=True,
    )
    r_low = score_family(
        family=_DAILY_MOMENTUM,
        name=low_spread,
        inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS,
        max_hold_days=_MAX_HOLD,
    )
    r_high = score_family(
        family=_DAILY_MOMENTUM,
        name=high_spread,
        inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS,
        max_hold_days=_MAX_HOLD,
    )
    assert r_low.score > r_high.score, (
        f"lower spread name should score higher: "
        f"{r_low.score:.3f} vs {r_high.score:.3f}"
    )


def test_no_redeclared_fee() -> None:
    import pathlib

    root = pathlib.Path(__file__).parent
    for fname in ("triage_scorer.py", "triage.py"):
        src = (root / fname).read_text(encoding="utf-8")
        assert "0.0015" not in src, f"{fname} must not hardcode buy fee 0.0015"
        assert "0.0025" not in src, f"{fname} must not hardcode sell fee 0.0025"
        # Reject bare literal 40 (as int or float) that would re-declare the constant.
        # Allow "40." only if it appears as part of a bps value like "40.0" in strings.
        # The safe check: no standalone `= 40` or `= 40.0` assignment patterns.
        # We check for any `40` that looks like a standalone fee constant assignment.
        assert not re.search(r"\b40\.0\b\s*#.*fee", src, re.IGNORECASE), (
            f"{fname} must not redeclare fee as 40.0"
        )


# --- test outcomes come from the committed verdict files (plan 25) ---

from strategy_fit.study_verdicts import Loaded, Verdict, load_verdicts


def test_obi_ofi_tick_outcome_is_no_go_via_verdicts() -> None:
    loaded = load_verdicts()
    assert loaded.outcome("obi_ofi_tick") == "NO_GO"
    assert "plan 06" in loaded.verdicts["obi_ofi_tick"].source


def test_render_shows_test_outcome_column() -> None:
    from strategy_fit.triage import _render_markdown

    testable = score_family(
        family=_DAILY_MOMENTUM, name=_itmg_like(), inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS, max_hold_days=_MAX_HOLD,
    )
    blocked = score_family(
        family=_OBI_OFI_TICK, name=_bumi_like(), inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS, max_hold_days=_MAX_HOLD,
    )
    ids = {"daily_momentum", "obi_ofi_tick"}
    verdicts = Loaded(
        {"obi_ofi_tick": Verdict("obi_ofi_tick", "NO_GO", "2026-06-19", "plan 06 (plans 04, 05)",
                                 "docs/plans/06-fill-simulator-and-backtest.md", "every cell negative")},
        ("x/verdict.json: could not be read (ValueError: bad)",),
    )
    md = _render_markdown([testable], [blocked], ids, ids, n_names=2, verdicts=verdicts)

    assert "| Status | Test outcome |" in md
    assert "| Family | Status | Test outcome | Unblock condition |" in md
    assert "| daily_momentum | single_name_time_series | ITMG |" in md
    assert "| testable_now | untested |" in md
    assert "| obi_ofi_tick | cost_gated | NO_GO |" in md
    assert "## Section 3 — Test outcomes" in md
    assert "| obi_ofi_tick | NO_GO | 2026-06-19 | plan 06 (plans 04, 05) | every cell negative |" in md
    assert "| daily_momentum | untested | — | — | — |" in md
    assert "PROBLEM: x/verdict.json: could not be read" in md


_POST_TRIAGE = [f for f in ALL_FAMILIES if f.catalog_status == "post_triage"]


def test_families_tested_after_triage_are_catalogued() -> None:
    ids = {f.family_id for f in _POST_TRIAGE}
    assert {
        "volume_breakout", "volume_spike_event", "foreign_flow_spike_event",
        "foreign_selling_rebound", "index_rebalance_deletion", "indicator_rules",
        "factor_books", "limit_move_events", "dividend_events", "calendar_effects",
    } <= ids
    assert len({f.family_id for f in ALL_FAMILIES}) == len(ALL_FAMILIES), "duplicate family_id"


def test_post_triage_families_carry_a_tested_outcome() -> None:
    loaded = load_verdicts()
    for fam in _POST_TRIAGE:
        assert loaded.outcome(fam.family_id) != "untested", f"{fam.family_id}: post_triage means it was tested"


def test_post_triage_family_is_not_scored_by_triage() -> None:
    result = score_family(
        family=_POST_TRIAGE[0], name=_itmg_like(), inv=_STD_INV,
        fee_bps=ROUND_TRIP_BPS, max_hold_days=_MAX_HOLD,
    )
    assert result.status == "post_triage"
    assert result.score == 0.0
    assert result.data_blocked_record is None
