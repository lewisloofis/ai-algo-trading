"""Tests for multiple-testing FDR correction in strategy_fit.

Covers:
- Shuffled-label / noise-only grid → ~zero BH-corrected survivors
- Injected-edge series → at least 1 BH survivor
- Regression: cost-gate failures must not enter the FDR pool (Bug 3)
- Regression: verdict and survivor logic must require passes_cost_gate (Bugs 1 & 2)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.run_strategy_fit import apply_bh_fdr, apply_fdr_to_grid, run_grid
from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource, FIXTURE_SEED
from strategy_fit.harness import CellResult
from strategy_fit.report import compute_verdict, VERDICT_PASS, VERDICT_INCONCLUSIVE, VERDICT_NO_GO


# ---------------------------------------------------------------------------
# BH FDR unit tests
# ---------------------------------------------------------------------------

class TestBHFDR:
    def test_all_large_pvalues_rejected(self):
        """All p-values > alpha → none accepted."""
        p_values = [0.5, 0.6, 0.7, 0.8, 0.9]
        accepted = apply_bh_fdr(p_values, alpha=0.10)
        assert all(not a for a in accepted)

    def test_all_zero_pvalues_accepted(self):
        """All p-values = 0.001 → all accepted."""
        p_values = [0.001, 0.001, 0.001]
        accepted = apply_bh_fdr(p_values, alpha=0.10)
        assert all(a for a in accepted)

    def test_empty_list(self):
        """Empty input returns empty list."""
        assert apply_bh_fdr([], alpha=0.10) == []

    def test_single_small_pvalue(self):
        """Single p=0.01 at alpha=0.10 → accepted."""
        accepted = apply_bh_fdr([0.01], alpha=0.10)
        assert accepted == [True]

    def test_single_large_pvalue(self):
        """Single p=0.5 at alpha=0.10 → rejected."""
        accepted = apply_bh_fdr([0.5], alpha=0.10)
        assert accepted == [False]

    def test_mixed_pvalues(self):
        """Classic BH example: sorted p-values, some accepted some not."""
        # 5 tests, alpha=0.10: threshold for i-th (1-indexed) test = alpha*i/n
        # p = [0.001, 0.02, 0.05, 0.20, 0.50]
        # BH: compare sorted p[i] vs alpha*(i+1)/n = 0.10*(i+1)/5
        # i=0: 0.001 vs 0.02 → accept
        # i=1: 0.02  vs 0.04 → accept
        # i=2: 0.05  vs 0.06 → accept
        # i=3: 0.20  vs 0.08 → reject
        # i=4: 0.50  vs 0.10 → reject
        # BH cutoff at last accepted = index 2.  Original indices 0,1,2 accepted.
        p_values = [0.20, 0.001, 0.05, 0.50, 0.02]
        accepted = apply_bh_fdr(p_values, alpha=0.10)
        # original indices: 1 (p=0.001), 4 (p=0.02), 2 (p=0.05) accepted
        assert accepted[1] is True   # p=0.001
        assert accepted[4] is True   # p=0.02
        assert accepted[2] is True   # p=0.05
        assert accepted[0] is False  # p=0.20
        assert accepted[3] is False  # p=0.50

    def test_returns_same_length(self):
        """Output length matches input length."""
        p_values = [0.1, 0.2, 0.3]
        accepted = apply_bh_fdr(p_values, alpha=0.10)
        assert len(accepted) == len(p_values)


# ---------------------------------------------------------------------------
# Grid-level FDR test
# ---------------------------------------------------------------------------

class TestGridFDR:
    def test_noise_grid_has_few_survivors(self):
        """
        With pure noise fixtures (BUMI, DEWA are noise) and shuffled signals,
        the BH FDR should produce 0 or very few survivors.

        We test this by checking that the grid runner does not spuriously
        declare every cell significant.  With 5 symbols × ~6 family-horizon
        combos = ~30 cells, noise should give 0 accepted at alpha=0.10.
        """
        rng = np.random.default_rng(FIXTURE_SEED + 1)
        # Create 10 random p-values as a proxy for a noise grid
        p_values = rng.uniform(0.05, 0.99, size=20).tolist()
        accepted = apply_bh_fdr(p_values, alpha=0.10)
        # With uniform p-values, very few (possibly 0) should be accepted
        n_accepted = sum(accepted)
        assert n_accepted <= 5, (
            f"Expected ≤5 BH survivors on noise p-values, got {n_accepted}"
        )

    def test_injected_edge_survives_bh(self):
        """
        ITMG with strong injected edge should produce at least 1 BH survivor
        when run through the full grid.

        We mock a cell result with a very small p-value (injected edge → real signal).
        """
        # One very significant cell + many noise cells
        p_values = [0.001] + [0.5] * 19
        accepted = apply_bh_fdr(p_values, alpha=0.10)
        assert accepted[0] is True, "Significant cell should survive BH"
        n_noise_accepted = sum(accepted[1:])
        assert n_noise_accepted == 0, (
            f"Noise cells should not survive, but {n_noise_accepted} did"
        )


# ---------------------------------------------------------------------------
# Regression: Bug 3 — FDR pool must be gated by passes_cost_gate
# ---------------------------------------------------------------------------

class TestFDRPoolCostGate:
    """Bug 3 regression: apply_fdr_to_grid must exclude cost-gate failures."""

    def _make_cell(
        self,
        symbol: str,
        passes_cost_gate: bool | None,
        p_value: float | None,
        status: str = "tested",
        net_edge_bps: float = 50.0,
    ) -> CellResult:
        return CellResult(
            symbol=symbol,
            family_name="mean_reversion",
            shape="single_name_time_series",
            horizon_trading_days=1,
            status=status,
            passes_cost_gate=passes_cost_gate,
            p_value=p_value,
            net_edge_bps=net_edge_bps,
            gross_edge_bps=net_edge_bps + 20.0,
            hit_rate=0.6,
            eff_n=50,
        )

    def test_cost_gate_failing_cell_never_gets_fdr_accepted(self):
        """
        A cell with passes_cost_gate=False must not receive fdr_accepted=True,
        even when its p_value is very small.

        This is the live-run bug: ADMR mean_reversion had net_edge=12.9 bps
        vs hurdle=123.0 bps (passes_cost_gate=False) but was labelled a
        survivor because the FDR pool was not gated.
        """
        cells = [
            # Fails cost gate — p very small but must be excluded from FDR pool.
            self._make_cell("ADMR", passes_cost_gate=False, p_value=0.001, net_edge_bps=12.9),
            # Passes cost gate — also significant.
            self._make_cell("ITMG", passes_cost_gate=True, p_value=0.001, net_edge_bps=200.0),
            # Passes cost gate — not significant.
            self._make_cell("BUMI", passes_cost_gate=True, p_value=0.80, net_edge_bps=60.0),
        ]
        updated = apply_fdr_to_grid(cells)

        admr = next(c for c in updated if c.symbol == "ADMR")
        itmg = next(c for c in updated if c.symbol == "ITMG")
        bumi = next(c for c in updated if c.symbol == "BUMI")

        assert admr.fdr_accepted is not True, (
            "ADMR (passes_cost_gate=False) must not be fdr_accepted=True; "
            f"got fdr_accepted={admr.fdr_accepted}"
        )
        assert itmg.fdr_accepted is True, (
            f"ITMG (passes_cost_gate=True, p=0.001) should be fdr_accepted=True; "
            f"got fdr_accepted={itmg.fdr_accepted}"
        )
        assert bumi.fdr_accepted is not True, (
            f"BUMI (p=0.80) should not be fdr_accepted=True; got {bumi.fdr_accepted}"
        )

    def test_skipped_cell_not_in_fdr_pool(self):
        """Cells with status != 'tested' are never in the FDR pool."""
        cells = [
            CellResult(
                symbol="XXXX",
                family_name="daily_momentum",
                shape="single_name_time_series",
                horizon_trading_days=1,
                status="skipped_horizon",
                p_value=0.001,  # irrelevant but set
                passes_cost_gate=None,
            ),
            self._make_cell("ITMG", passes_cost_gate=True, p_value=0.001, net_edge_bps=200.0),
        ]
        updated = apply_fdr_to_grid(cells)
        skipped = next(c for c in updated if c.symbol == "XXXX")
        assert skipped.fdr_accepted is None, (
            f"Skipped cell should not have fdr_accepted set; got {skipped.fdr_accepted}"
        )


# ---------------------------------------------------------------------------
# Regression: Bugs 1 & 2 — verdict and survivors require passes_cost_gate
# ---------------------------------------------------------------------------

class TestVerdictAndSurvivorsCostGate:
    """Bugs 1 & 2 regression: cost gate is a hard prerequisite for survivor / PASS."""

    def _tested_cell(
        self,
        symbol: str,
        passes_cost_gate: bool,
        fdr_accepted: bool,
        net_edge_bps: float,
    ) -> CellResult:
        # impact_priced=True: these tests exercise the cost-gate/FDR
        # interaction (Bugs 1 & 2) in isolation from the W2.1 impact-priced
        # gate (test_verdict_gates.py covers that gate specifically) — a
        # single_name_time_series cell never has impact priced in production,
        # but pinning it True here keeps this fixture's PASS/INCONCLUSIVE
        # outcomes governed only by the cost-gate/FDR logic under test.
        #
        # null_edge_bps=0.0 (edge_over_null_bps == gross_edge_bps) similarly
        # isolates these tests from the always-long null gate (a plan-level
        # gate, covered on its own in test_verdict_gates.py::TestNullEdgeGate)
        # — with the signal explaining the whole gross edge, edge_over_null
        # tracks gross_edge_bps 1:1 and doesn't independently gate PASS here.
        return CellResult(
            symbol=symbol,
            family_name="mean_reversion",
            shape="single_name_time_series",
            horizon_trading_days=1,
            status="tested",
            passes_cost_gate=passes_cost_gate,
            fdr_accepted=fdr_accepted,
            net_edge_bps=net_edge_bps,
            gross_edge_bps=net_edge_bps + 20.0,
            p_value=0.001 if fdr_accepted else 0.90,
            hit_rate=0.6,
            eff_n=50,
            impact_priced=True,
            min_edge_hurdle_bps=100.0,
            null_edge_bps=0.0,
            edge_over_null_bps=net_edge_bps + 20.0,
        )

    def test_cost_gate_fail_fdr_accepted_not_survivor_and_not_pass(self):
        """
        Cell with passes_cost_gate=False, fdr_accepted=True, net_edge=12.9 bps
        must NOT appear in survivors and must NOT produce verdict=PASS.

        This is the exact live-run failure: ADMR mean_reversion.
        """
        cells = [
            # Fails cost gate — even with fdr_accepted=True this is NOT a survivor.
            self._tested_cell("ADMR", passes_cost_gate=False, fdr_accepted=True, net_edge_bps=12.9),
        ]
        verdict = compute_verdict(cells)
        # No cost-gate passers → NO-GO (not PASS, not INCONCLUSIVE).
        assert verdict == VERDICT_NO_GO, (
            f"Expected NO-GO when only cell fails cost gate, got {verdict}"
        )

    def test_cost_gate_pass_fdr_accepted_is_survivor_and_yields_pass(self):
        """
        Cell with passes_cost_gate=True, fdr_accepted=True, net_edge=200 bps
        IS a survivor and produces verdict=PASS.
        """
        cells = [
            self._tested_cell("ITMG", passes_cost_gate=True, fdr_accepted=True, net_edge_bps=200.0),
        ]
        verdict = compute_verdict(cells)
        assert verdict == VERDICT_PASS, (
            f"Expected PASS for cost-gate-passing FDR-accepted cell, got {verdict}"
        )

    def test_cost_gate_pass_fdr_rejected_is_inconclusive(self):
        """
        Cell passes cost gate but is FDR-rejected → INCONCLUSIVE (not PASS).
        """
        cells = [
            self._tested_cell("BUMI", passes_cost_gate=True, fdr_accepted=False, net_edge_bps=80.0),
        ]
        verdict = compute_verdict(cells)
        assert verdict == VERDICT_INCONCLUSIVE, (
            f"Expected INCONCLUSIVE when cost gate passes but FDR rejects, got {verdict}"
        )

    def test_mixed_cost_gate_fail_and_cost_gate_pass_no_fdr(self):
        """
        One cell fails cost gate (fdr_accepted=True, wrong scenario) and one
        passes cost gate but is FDR-rejected → INCONCLUSIVE, not PASS.
        """
        cells = [
            # Cost gate fail — should not count as FDR survivor.
            self._tested_cell("ADMR", passes_cost_gate=False, fdr_accepted=True, net_edge_bps=12.9),
            # Cost gate pass — FDR rejected.
            self._tested_cell("BUMI", passes_cost_gate=True, fdr_accepted=False, net_edge_bps=80.0),
        ]
        verdict = compute_verdict(cells)
        assert verdict == VERDICT_INCONCLUSIVE, (
            f"Expected INCONCLUSIVE (cost-gate passer exists but no FDR survivor), got {verdict}"
        )
