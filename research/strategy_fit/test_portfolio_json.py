"""
Tests for strategy_fit.portfolio_json — the machine-readable grid sidecar.

The load-bearing test here is the round-trip (plan-19 Stage 1, §8 "field-drift
test"): every ``CellResult`` field must survive a write/read cycle
field-for-field.  It is written against ``dataclasses.fields(CellResult)``
rather than a hardcoded list, so adding a field to ``CellResult`` and forgetting
the sidecar fails this test instead of silently dropping the field from every
downstream reader.
"""
from __future__ import annotations

import dataclasses
import json
import math

import pytest

from strategy_fit.harness import (
    CellResult,
    SHAPE_INTRADAY_SAME_DAY,
    SHAPE_SINGLE_NAME,
)
from strategy_fit.impact import SQRT_IMPACT_COEFF
from strategy_fit.portfolio_json import (
    COEFF_SOURCE_GLOBAL_DEFAULT,
    COEFF_SOURCE_MEASURED,
    COEFF_SOURCE_MIXED,
    COEFF_SOURCE_UNPRICED,
    SCHEMA_VERSION,
    SIDECAR_FILENAME,
    build_sidecar,
    cells_from_sidecar,
    emit_portfolio_json,
    git_provenance,
    impact_provenance,
    load_sidecar,
    normalize_nonfinite,
)
from strategy_fit.report import emit_strategy_portfolio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _tested_cell(**overrides) -> CellResult:
    base = dict(
        symbol="ITMG",
        family_name="intraday_momentum",
        shape=SHAPE_INTRADAY_SAME_DAY,
        horizon_trading_days=0,
        status="tested",
        hit_rate=0.61,
        eff_n=48,
        gross_edge_bps=91.5,
        net_edge_bps=12.25,
        p_value=0.014,
        decay_status="persistent",
        decay_metric=0.42,
        passes_cost_gate=True,
        min_edge_hurdle_bps=79.25,
        best_entry_dow=1,
        fdr_accepted=True,
        modelled_impact_bps=18.0,
        oos_edge_bps=44.0,
        oos_hit_rate=0.58,
        oos_eff_n=17,
        oos_decay_ratio=0.48,
        oos_persistent=True,
        oos_sigma_bps=210.0,
        oos_required_eff_n=92,
        oos_underpowered=True,
        impact_priced=True,
        impact_priced_position_notional_idr=3_000_000.0,
        impact_priced_coeff=SQRT_IMPACT_COEFF,
        null_edge_bps=40.0,
        edge_over_null_bps=51.5,
    )
    base.update(overrides)
    return CellResult(**base)


def _blocked_cell(**overrides) -> CellResult:
    base = dict(
        symbol="ANTM",
        family_name="multi_month_macro",
        shape=SHAPE_SINGLE_NAME,
        horizon_trading_days=20,
        status="data_blocked",
        skip_reason="hold=20 gives ~12 obs/yr — below MIN_EFF_N=30",
    )
    base.update(overrides)
    return CellResult(**base)


def _grid() -> list[CellResult]:
    return [
        _tested_cell(),
        _tested_cell(symbol="PTRO", edge_over_null_bps=-65.1, passes_cost_gate=False),
        _blocked_cell(),
        _blocked_cell(symbol="TINS", status="insufficient_n", skip_reason="eff_n=7 < 30"),
    ]


# ---------------------------------------------------------------------------
# Round-trip — the field-drift guard
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_every_field_survives_the_round_trip(self, tmp_path):
        """
        Field-for-field equality, driven by dataclasses.fields(CellResult).

        A new CellResult field that the sidecar drops fails here.
        """
        cells = _grid()
        path = emit_portfolio_json(
            cells=cells,
            outdir=str(tmp_path),
            generated_at="2026-09-16T00:00:00Z",
            is_synthetic=True,
            verdict="NO-GO",
        )
        restored = cells_from_sidecar(load_sidecar(path))

        assert len(restored) == len(cells)
        field_names = [f.name for f in dataclasses.fields(CellResult)]
        assert field_names, "CellResult has no fields — test is vacuous"
        for original, back in zip(cells, restored):
            for name in field_names:
                want = normalize_nonfinite(getattr(original, name))
                got = getattr(back, name)
                assert got == want, f"field {name!r} did not round-trip"

    def test_restored_cells_compare_equal_as_dataclasses(self, tmp_path):
        cells = _grid()
        path = emit_portfolio_json(
            cells=cells, outdir=str(tmp_path), generated_at="t",
            is_synthetic=False, verdict="PASS",
        )
        assert cells_from_sidecar(load_sidecar(path)) == cells

    def test_sidecar_carries_every_cellresult_field_name(self, tmp_path):
        """The serialised dict's keys are exactly CellResult's field names."""
        path = emit_portfolio_json(
            cells=[_tested_cell()], outdir=str(tmp_path), generated_at="t",
            is_synthetic=True, verdict="NO-GO",
        )
        doc = load_sidecar(path)
        expected = {f.name for f in dataclasses.fields(CellResult)}
        assert set(doc["cells"][0]) == expected

    def test_unknown_field_fails_loudly(self, tmp_path):
        """A sidecar from a different harness version must not load silently."""
        doc = build_sidecar(
            cells=[_tested_cell()], generated_at="t", is_synthetic=True, verdict="NO-GO",
        )
        doc["cells"][0]["a_field_that_no_longer_exists"] = 1
        with pytest.raises(ValueError, match="does not define"):
            cells_from_sidecar(doc)


# ---------------------------------------------------------------------------
# Non-finite floats
# ---------------------------------------------------------------------------

class TestNonFinite:
    def test_nan_is_written_as_null_and_reads_back_as_none(self, tmp_path):
        cells = [_tested_cell(net_edge_bps=float("nan"))]
        path = emit_portfolio_json(
            cells=cells, outdir=str(tmp_path), generated_at="t",
            is_synthetic=True, verdict="NO-GO",
        )
        raw = open(path, encoding="utf-8").read()
        assert "NaN" not in raw, "sidecar must be strict JSON"
        assert cells_from_sidecar(load_sidecar(path))[0].net_edge_bps is None

    def test_infinity_is_written_as_null(self, tmp_path):
        cells = [_tested_cell(min_edge_hurdle_bps=float("inf"))]
        path = emit_portfolio_json(
            cells=cells, outdir=str(tmp_path), generated_at="t",
            is_synthetic=True, verdict="NO-GO",
        )
        assert "Infinity" not in open(path, encoding="utf-8").read()
        assert cells_from_sidecar(load_sidecar(path))[0].min_edge_hurdle_bps is None

    def test_normalize_nonfinite_recurses(self):
        got = normalize_nonfinite({"a": [1.0, float("nan")], "b": {"c": float("-inf")}})
        assert got == {"a": [1.0, None], "b": {"c": None}}

    def test_finite_floats_are_untouched(self):
        assert normalize_nonfinite(1.5) == 1.5
        assert normalize_nonfinite(0.0) == 0.0


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

class TestProvenance:
    def test_universe_holds_and_families_are_derived_from_cells(self):
        doc = build_sidecar(
            cells=_grid(), generated_at="t", is_synthetic=True, verdict="NO-GO",
        )
        prov = doc["provenance"]
        assert prov["universe"]["symbols"] == ["ANTM", "ITMG", "PTRO", "TINS"]
        assert prov["universe"]["size"] == 4
        assert prov["holds_trading_days"] == [0, 20]
        assert prov["families"] == ["intraday_momentum", "multi_month_macro"]

    def test_git_provenance_never_raises_outside_a_checkout(self, tmp_path):
        got = git_provenance(repo_root=str(tmp_path))
        assert set(got) == {"sha", "branch", "dirty", "error"}
        # Either it answered (tmp_path inside some checkout) or it recorded why.
        assert got["sha"] is not None or got["error"]

    def test_git_provenance_records_the_repo_sha(self):
        got = git_provenance()
        if got["error"]:
            pytest.skip(f"git unavailable: {got['error']}")
        assert got["sha"] and len(got["sha"]) == 40
        assert isinstance(got["dirty"], bool)

    def test_impact_source_unpriced_when_nothing_was_priced(self):
        got = impact_provenance([_blocked_cell()])
        assert got["coeff_source"] == COEFF_SOURCE_UNPRICED
        assert got["priced_cells"] == 0
        assert got["unpriced_cells"] == 1

    def test_impact_source_global_default(self):
        got = impact_provenance([_tested_cell(impact_priced_coeff=SQRT_IMPACT_COEFF)])
        assert got["coeff_source"] == COEFF_SOURCE_GLOBAL_DEFAULT

    def test_impact_source_measured(self):
        got = impact_provenance([_tested_cell(impact_priced_coeff=SQRT_IMPACT_COEFF * 0.3)])
        assert got["coeff_source"] == COEFF_SOURCE_MEASURED

    def test_impact_source_mixed_is_reported_not_averaged(self):
        """A run with both calibrated and default coefficients must say so."""
        got = impact_provenance([
            _tested_cell(impact_priced_coeff=SQRT_IMPACT_COEFF),
            _tested_cell(symbol="PTRO", impact_priced_coeff=SQRT_IMPACT_COEFF * 0.3),
        ])
        assert got["coeff_source"] == COEFF_SOURCE_MIXED
        assert len(got["distinct_coeffs"]) == 2

    def test_sidecar_does_not_copy_thresholds(self):
        """
        D2: thresholds are imported from the module that owns them, never
        snapshotted into the sidecar where they would go stale.
        """
        doc = build_sidecar(
            cells=_grid(), generated_at="t", is_synthetic=True, verdict="NO-GO",
        )
        # Scoped to the provenance block on purpose: a cell's own skip_reason
        # may legitimately quote a threshold ("eff_n=7 < MIN_EFF_N=30") — that
        # is the grid's recorded explanation, not a second definition.
        prov = json.dumps(doc["provenance"])
        for banned in (
            "DECAY_THRESHOLD_SHARPE", "DECAY_THRESHOLD_HR", "DECAY_MAJORITY_FRAC",
            "MIN_OOS_EFF_N", "MIN_EFF_N",
        ):
            assert banned not in prov, f"threshold {banned} must not be copied into the sidecar"
        assert set(doc["provenance"]) == {
            "git", "universe", "families", "shapes", "holds_trading_days",
            "impact", "capital_model", "sleeve_sizing",
        }


# ---------------------------------------------------------------------------
# Integration with the markdown emitter
# ---------------------------------------------------------------------------

class TestEmittedAlongsideMarkdown:
    def test_emit_strategy_portfolio_writes_the_sidecar(self, tmp_path):
        md = emit_strategy_portfolio(cells=_grid(), outdir=str(tmp_path))
        assert md.endswith("strategy_portfolio.md")
        sidecar = tmp_path / SIDECAR_FILENAME
        assert sidecar.exists()
        doc = load_sidecar(str(sidecar))
        assert doc["schema_version"] == SCHEMA_VERSION
        assert doc["cell_count"] == 4

    def test_markdown_and_sidecar_agree_on_provenance(self, tmp_path):
        """One run, two files: generated_at, is_synthetic and verdict must match."""
        md = emit_strategy_portfolio(
            cells=_grid(), outdir=str(tmp_path),
            generated_at="2026-09-16T01:02:03Z", is_synthetic=False,
        )
        doc = load_sidecar(str(tmp_path / SIDECAR_FILENAME))
        md_text = open(md, encoding="utf-8").read()
        assert doc["generated_at"] == "2026-09-16T01:02:03Z"
        assert "2026-09-16T01:02:03Z" in md_text
        assert doc["is_synthetic"] is False
        assert "LIVE DATA" in md_text
        assert f"## Verdict: {doc['verdict']}" in md_text

    def test_is_synthetic_defaults_to_true_in_the_sidecar_too(self, tmp_path):
        """D4: synthetic unless told otherwise, on both artifacts."""
        emit_strategy_portfolio(cells=_grid(), outdir=str(tmp_path))
        assert load_sidecar(str(tmp_path / SIDECAR_FILENAME))["is_synthetic"] is True
