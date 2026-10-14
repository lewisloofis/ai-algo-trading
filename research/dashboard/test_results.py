"""The report parser turns the harness's own text into typed fields; it never re-judges a cell."""
from __future__ import annotations

import os

import pytest

from dashboard import results
from dashboard.results import (
    GATE_COST,
    GATE_FDR,
    GATE_INSUFFICIENT_N,
    GATE_NULL,
    GATE_OOS,
    GATE_PASSED_ALL,
    ResultsError,
    parse_report,
)

REPORT = """# Strategy Portfolio

**Generated:** 2026-10-14T13:06:33Z
**Capital model:** `owned_cash` — no financing

## Surviving Strategies

| Symbol | Strategy | Hold (days) | Shape | Hit Rate | Net Edge (bps) | Eff-N | Best Entry Day | Friction Hurdle (bps) | Impact (bps) | Null Edge (bps) | Edge over Null (bps) | OOS Edge (bps) | OOS Eff-N | OOS Persistent | Decay Metric (Sharpe) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BIPI | daily_momentum | 5 | single_name_time_series | 0.453 | 158.5 | 75 | Mon | 112.2 | 3.7 | 108.7 | 161.9 | 444.0 | 31 | YES ⚠ (UNDERPOWERED) | 0.11 |
| TAPG | daily_momentum | 5 | single_name_time_series | 0.663 | 141.6 | 89 | Mon | 86.2 | 2.2 | 134.2 | 93.7 | 225.1 | 34 | YES | 0.28 |

## Coverage Table (All Cells)

| Symbol | Strategy | Hold | Shape | Status | Reason / Unblock |
|---|---|---|---|---|---|
| BNBR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-158.8 bps ≤ 0 (gross − hurdle, where hurdle=216.0 bps) |
| BNBR | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.063 |
| HRTA | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=-8.7 bps <= hurdle=92.4 bps (gross=164.8, null=173.5 — x) |
| HRTA | daily_momentum | 20 | single_name_time_series | tested | invalid split: neither IS nor OOS is usable |
| BNBR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |

## Methodology Notes
- not a table | row | here
"""


def test_survivors_are_read_with_the_harness_numbers():
    r = parse_report(REPORT)
    bipi = r.cell("BIPI", "daily_momentum", 5)
    assert bipi.gate == GATE_PASSED_ALL
    assert (bipi.hurdle_bps, bipi.net_edge_bps, bipi.eff_n) == (112.2, 158.5, 75)
    assert bipi.gross_edge_bps == pytest.approx(270.7)
    assert bipi.oos_underpowered is True
    assert r.cell("TAPG", "daily_momentum", 5).oos_underpowered is False


def test_coverage_reasons_map_to_gate_outcomes():
    r = parse_report(REPORT)
    assert r.cell("BNBR", "daily_momentum", 2).gate == GATE_COST
    assert r.cell("BNBR", "daily_momentum", 5).gate == GATE_FDR
    assert r.cell("HRTA", "volume_breakout", 5).gate == GATE_NULL
    assert r.cell("HRTA", "daily_momentum", 20).gate == GATE_OOS
    assert r.cell("BNBR", "daily_momentum", 60).gate == GATE_INSUFFICIENT_N


def test_numbers_inside_reason_text_are_parsed_not_recomputed():
    r = parse_report(REPORT)
    bnbr = r.cell("BNBR", "daily_momentum", 2)
    assert bnbr.hurdle_bps == 216.0 and bnbr.net_edge_bps == -158.8
    assert r.cell("BNBR", "daily_momentum", 5).p_value == 0.063
    hrta = r.cell("HRTA", "volume_breakout", 5)
    assert (hrta.gross_edge_bps, hrta.null_edge_bps) == (164.8, 173.5)


def test_symbol_hurdle_is_shared_across_a_names_cells():
    r = parse_report(REPORT)
    assert r.symbol_hurdles["BNBR"] == 216.0  # taken from its cost_gated row
    assert r.symbol_hurdles["BIPI"] == 112.2  # taken from the survivors table


def test_non_report_text_is_rejected():
    with pytest.raises(ResultsError):
        parse_report("just some markdown")


def test_only_registered_dirs_are_considered(tmp_path):
    old = tmp_path / "out_live"
    old.mkdir()
    (old / "strategy_portfolio.md").write_text(REPORT.replace("2026-10-14", "2026-09-24"))
    new = tmp_path / "out_d1" / "next_close"
    new.mkdir(parents=True)
    (new / "strategy_portfolio.md").write_text(REPORT)
    got = results.latest_report(str(tmp_path), ["out_d1/next_close"])
    assert got is not None and got.generated.startswith("2026-10-14")
    assert results.latest_report(str(tmp_path), ["out_nothing"]) is None


def test_real_corrected_run_parses_if_present():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "out_d1", "next_close", "strategy_portfolio.md")
    if not os.path.exists(path):
        pytest.skip("out_d1 not present")
    r = results.load_report(path)
    assert sum(1 for c in r.cells if c.gate == GATE_PASSED_ALL) == 10
    assert len(r.cells) > 1700
