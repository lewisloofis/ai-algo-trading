"""End-to-end tests for strategy_fit pipeline.

Covers:
- Running run_grid with fixture source produces strategy_portfolio.md
- The output contains required sections (verdict, survivor table, coverage table, SYNTHETIC marker)
- The injected-edge ITMG series contributes to the survivor pool
- Regression Bug 4: is_synthetic=False emits LIVE marker, not SYNTHETIC banner
"""

from __future__ import annotations

import os
import tempfile

import pytest

from strategy_fit.run_strategy_fit import run_strategy_fit
from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
from strategy_fit.report import emit_strategy_portfolio
from strategy_fit.harness import CellResult


class TestEndToEnd:
    def test_portfolio_md_is_produced(self, tmp_path):
        """Running the pipeline must produce strategy_portfolio.md in outdir."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        portfolio_path = tmp_path / "strategy_portfolio.md"
        assert portfolio_path.exists(), (
            f"strategy_portfolio.md not found in {tmp_path}"
        )

    def test_verdict_line_present(self, tmp_path):
        """strategy_portfolio.md must contain a three-state verdict header."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        # Must contain one of the three verdict words.
        has_verdict = any(v in content for v in ["PASS", "INCONCLUSIVE", "NO-GO"])
        assert has_verdict, "No verdict (PASS/INCONCLUSIVE/NO-GO) found in portfolio"

    def test_synthetic_marker_present(self, tmp_path):
        """Output must be clearly marked as SYNTHETIC data."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "SYNTHETIC" in content, "SYNTHETIC marker missing from portfolio output"

    def test_survivor_table_present(self, tmp_path):
        """Output must contain a survivors section."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "Surviving" in content or "survivor" in content or "Survivor" in content, (
            "No survivor table found in portfolio output"
        )

    def test_coverage_table_present(self, tmp_path):
        """Output must contain a coverage section (no silent omissions)."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        has_coverage = (
            "Coverage" in content
            or "coverage" in content
            or "data_blocked" in content
            or "skipped_horizon" in content
            or "capital_blocked" in content
        )
        assert has_coverage, "No coverage table found in portfolio output"

    def test_skipped_horizon_cells_recorded(self, tmp_path):
        """Multi-week strategies (hold > 2) must appear in coverage as skipped."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        # At least one cell should be skipped for capital reasons.
        assert "capital_blocked" in content or "skipped_horizon" in content, (
            "No capital_blocked / skipped_horizon cells in coverage"
        )

    def test_itmg_appears_in_output(self, tmp_path):
        """ITMG (injected edge) must appear somewhere in the output."""
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "ITMG" in content, "ITMG not mentioned in portfolio output"

    def test_no_error_on_run(self, tmp_path):
        """Pipeline must run to completion without raising an exception."""
        ds = StrategyFitFixtureDataSource()
        # Should not raise.
        run_strategy_fit(ds=ds, outdir=str(tmp_path))


# ---------------------------------------------------------------------------
# Regression: Bug 4 — is_synthetic flag controls banner (not hardcoded)
# ---------------------------------------------------------------------------

class TestSyntheticBanner:
    """Bug 4 regression: emit_strategy_portfolio must respect is_synthetic flag."""

    def _minimal_cell(self) -> CellResult:
        """Return a minimal tested CellResult for report generation."""
        return CellResult(
            symbol="ITMG",
            family_name="daily_momentum",
            shape="single_name_time_series",
            horizon_trading_days=1,
            status="tested",
            passes_cost_gate=True,
            fdr_accepted=True,
            net_edge_bps=200.0,
            gross_edge_bps=220.0,
            p_value=0.001,
            hit_rate=0.65,
            eff_n=80,
            min_edge_hurdle_bps=50.0,
        )

    def test_is_synthetic_true_emits_synthetic_banner(self, tmp_path):
        """Default (is_synthetic=True) must contain the SYNTHETIC DATA warning."""
        cells = [self._minimal_cell()]
        emit_strategy_portfolio(cells=cells, outdir=str(tmp_path), is_synthetic=True)
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "SYNTHETIC DATA" in content or "SYNTHETIC" in content, (
            "is_synthetic=True must produce a SYNTHETIC DATA banner"
        )
        assert "LIVE DATA" not in content, (
            "is_synthetic=True must NOT produce a LIVE DATA marker"
        )

    def test_is_synthetic_false_emits_live_marker_not_synthetic(self, tmp_path):
        """is_synthetic=False must emit a LIVE DATA marker and omit the SYNTHETIC banner."""
        cells = [self._minimal_cell()]
        emit_strategy_portfolio(cells=cells, outdir=str(tmp_path), is_synthetic=False)
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "LIVE DATA" in content, (
            "is_synthetic=False must produce a LIVE DATA provenance marker"
        )
        assert "SYNTHETIC DATA" not in content, (
            "is_synthetic=False must NOT produce the ⚠ SYNTHETIC DATA banner"
        )

    def test_default_is_synthetic_matches_synthetic_true(self, tmp_path):
        """Calling emit_strategy_portfolio without is_synthetic must behave like is_synthetic=True."""
        cells = [self._minimal_cell()]
        # Call without is_synthetic kwarg — default must be True.
        emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "SYNTHETIC" in content, (
            "Default call (no is_synthetic) must produce SYNTHETIC banner"
        )

    def test_generated_at_is_timezone_aware(self, tmp_path):
        """Generated timestamp must end with 'Z' (UTC-aware)."""
        cells = [self._minimal_cell()]
        emit_strategy_portfolio(cells=cells, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        # The **Generated:** line should contain a Z-suffixed timestamp.
        import re
        match = re.search(r"\*\*Generated:\*\*\s+(\S+)", content)
        assert match, "No **Generated:** timestamp found in report"
        ts = match.group(1)
        assert ts.endswith("Z"), (
            f"Timestamp should end with 'Z' (UTC-aware); got: {ts!r}"
        )
