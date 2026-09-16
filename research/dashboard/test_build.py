"""
End-to-end tests for the rendered page.

Covers the remaining plan-19 §8 checks:
  * field-drift — every CellResult field appears in the rendered cell detail
  * glossary completeness — a column without a definition fails the build
  * join safety — an unmapped strategy_id fails the render, loudly
  * provenance — synthetic-by-default and replay-not-live banners are present
"""
from __future__ import annotations

import dataclasses
import json
import re

import pytest

from strategy_fit.harness import CellResult
from strategy_fit.portfolio_json import emit_portfolio_json

from dashboard.build import Inputs, build_html, write_dashboard
from dashboard.conftest import make_blocked_cell, make_tested_cell
from dashboard.render import Column, table
from dashboard.strategy_map import UnmappedStrategyError


@pytest.fixture
def sidecar(tmp_path):
    def _write(cells, name="strategy_portfolio.json", is_synthetic=True, verdict="NO-GO"):
        outdir = tmp_path / name.replace(".json", "")
        path = emit_portfolio_json(
            cells=cells, outdir=str(outdir), generated_at="2026-09-16T00:00:00Z",
            is_synthetic=is_synthetic, verdict=verdict,
        )
        return path

    return _write


def _grid():
    return [
        make_tested_cell(),
        make_tested_cell(symbol="PTRO", edge_over_null_bps=10.0, min_edge_hurdle_bps=110.2),
        make_tested_cell(symbol="TINS", passes_cost_gate=False, net_edge_bps=-20.0),
        make_blocked_cell(),
    ]


class TestRendersWithoutInputs:
    def test_an_empty_page_still_renders(self):
        html = build_html(Inputs())
        assert html.startswith("<!doctype html>")
        assert "No strategy-fit sidecar supplied" in html
        assert "No post-trade store supplied" in html

    def test_it_says_what_is_missing_rather_than_showing_empty_tables(self):
        html = build_html(Inputs())
        assert "--portfolio" in html
        assert "--store" in html


class TestPanelARendering:
    def test_the_funnel_appears_with_its_counts(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        assert "4 catalog cells" in html
        assert "cost_gated" in html
        assert "loses_to_null" in html

    def test_field_drift_every_cellresult_field_is_rendered(self, sidecar):
        """
        §8: a CellResult field absent from the rendered cell detail fails.
        Driven by dataclasses.fields, so a new field is covered automatically.
        """
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        missing = [
            f.name for f in dataclasses.fields(CellResult)
            if f'<code>{f.name}</code>' not in html
        ]
        assert not missing, f"field(s) never rendered in cell detail: {missing}"

    def test_every_rendered_field_links_to_its_glossary_entry(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        for f in dataclasses.fields(CellResult):
            assert f'id="gl-cell-{f.name}"' in html, f"no glossary anchor for {f.name}"

    def test_thresholds_are_shown_with_their_names(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        assert "DECAY_THRESHOLD_SHARPE" in html
        assert "MIN_OOS_EFF_N" in html

    def test_untested_catalog_families_are_named(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        assert "no cell in this run" in html
        assert "obi_ofi_tick" in html


class TestProvenanceBanners:
    def test_synthetic_is_the_default_and_is_a_banner(self, sidecar):
        """D4: a report is synthetic unless told otherwise, and it is not dismissible."""
        html = build_html(Inputs(portfolio_json=sidecar(_grid(), is_synthetic=True)))
        assert "SYNTHETIC DATA" in html
        assert "defaults to true" in html

    def test_live_data_says_so(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid(), is_synthetic=False)))
        assert "LIVE DATA" in html
        assert "SYNTHETIC DATA" not in html

    def test_borrowed_coefficients_warning_survives_into_the_panel(self, sidecar):
        """§7: report.py's warning must survive into any panel showing a hurdle."""
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        assert "borrowed" in html
        assert "ADV window" in html

    def test_unpriced_impact_is_not_reported_as_zero(self, sidecar):
        cells = [make_tested_cell(impact_priced=False, impact_priced_coeff=None)]
        html = build_html(Inputs(portfolio_json=sidecar(cells)))
        assert "Impact never priced" in html
        assert "not the same as" in html


class TestRunDiffRendering:
    def test_a_changed_cell_shows_its_delta(self, sidecar, tmp_path):
        prev = sidecar([make_tested_cell(symbol="BNBR", net_edge_bps=-113.1)], name="prev.json")
        now = sidecar([make_tested_cell(symbol="BNBR", net_edge_bps=119.5)], name="now.json")
        html = build_html(Inputs(portfolio_json=now, previous_portfolio_json=prev))
        assert "BNBR" in html
        assert "232.6" in html

    def test_no_previous_sidecar_says_so(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        assert "No previous sidecar" in html


class TestPanelCRendering:
    def test_replay_banner_is_present_when_nothing_is_live(self, make_store):
        path = make_store(
            intents=[{"seq": 1, "order_id": "o1"}],
            attributions=[{"seq": 2, "entry_order_id": "o1", "net_pnl_bps": -5.0}],
        )
        html = build_html(Inputs(store_globs=[path]))
        assert "Every figure below is replay or paper" in html
        assert "plan-18 B4" in html

    def test_an_unmapped_strategy_id_fails_the_render(self, make_store):
        path = make_store(
            intents=[{"seq": 1, "order_id": "o1", "strategy_id": "ghost"}],
            attributions=[{"seq": 2, "entry_order_id": "o1", "strategy_id": "ghost"}],
        )
        with pytest.raises(UnmappedStrategyError, match="ghost"):
            build_html(Inputs(store_globs=[path]))

    def test_orb_renders_its_no_research_counterpart_warning(self, make_store):
        path = make_store(
            intents=[{"seq": 1, "order_id": "o1"}],
            attributions=[{"seq": 2, "entry_order_id": "o1"}],
        )
        html = build_html(Inputs(store_globs=[path]))
        assert "has no research counterpart" in html
        assert "30-minute opening-range high" in html

    def test_the_run_table_flags_a_crashed_run(self, make_store):
        path = make_store(runs=[{"run_id": "r1", "outcome": "running", "ended_ns": None}])
        html = build_html(Inputs(store_globs=[path]))
        assert "died?" in html

    def test_store_read_errors_are_surfaced(self, tmp_path):
        bad = tmp_path / "bad.db"
        bad.write_text("not a database")
        html = build_html(Inputs(store_globs=[str(bad)]))
        assert "Store read errors" in html
        assert "short by an unknown amount" in html


class TestPanelBRendering:
    def test_a_missing_status_file_is_unknown_not_ok(self, tmp_path):
        html = build_html(Inputs())
        assert "No status.json found" in html
        assert "unobserved one" in html

    def test_a_tripped_kill_switch_is_an_alert(self, tmp_path):
        status = tmp_path / "status.json"
        status.write_text(json.dumps({
            "schema_version": 1, "run_id": "r1", "wall_clock_ns": 10**18,
            "written_at": "2026-09-16T00:00:00Z", "source": "live", "sink_kind": "paper",
            "strategy_id": "orb", "input_spec": "AAA", "outcome": "running",
            "progress": {"events_seen": 5, "last_event_ns": 10**18,
                          "last_event_as_of_date": "2026-09-16",
                          "counters": {"orders_attempted": 1, "fills": 1,
                                       "total_fees_idr": 10, "round_trips": 0, "net_pnl_bps": 0.0}},
            "risk": {"kill_switch_tripped": True, "kill_switch_reason": "file present",
                      "trading_day": "2026-09-16", "day_notional_idr": 1.0,
                      "day_notional_cap_idr": 2.0, "gross_exposure_idr": 1.0,
                      "gross_exposure_cap_idr": 5.0, "positions_idr": {"AAA": 1.0},
                      "open_reservations": 0},
        }))
        html = build_html(Inputs(status_json=str(status), now_s=10**18 / 1e9))
        assert "kill_switch_tripped" in html
        assert "KILL SWITCH TRIPPED" in html

    def test_null_risk_reads_as_not_reported(self, tmp_path):
        status = tmp_path / "status.json"
        status.write_text(json.dumps({
            "schema_version": 1, "run_id": "r1", "wall_clock_ns": 10**18,
            "source": "replay", "sink_kind": "paper", "strategy_id": "orb",
            "input_spec": "x", "outcome": "complete",
            "progress": {"events_seen": 0, "last_event_ns": 0,
                          "last_event_as_of_date": "", "counters": {}},
            "risk": None,
        }))
        html = build_html(Inputs(status_json=str(status), now_s=10**18 / 1e9))
        assert "never <em>no limits in force</em>" in html

    def test_a_stale_heartbeat_fires_an_alert(self, tmp_path):
        status = tmp_path / "status.json"
        status.write_text(json.dumps({
            "schema_version": 1, "run_id": "r1", "wall_clock_ns": 10**18,
            "source": "live", "sink_kind": "paper", "strategy_id": "orb",
            "input_spec": "x", "outcome": "running",
            "progress": {"events_seen": 1, "last_event_ns": 10**18,
                          "last_event_as_of_date": "2026-09-16", "counters": {}},
            "risk": None,
        }))
        # Ten minutes of wall clock later.
        html = build_html(Inputs(status_json=str(status), now_s=10**18 / 1e9 + 600))
        assert "stale_heartbeat" in html


class TestGlossaryCompleteness:
    def test_a_column_without_a_definition_fails_the_build(self):
        """§8: this is the check, exercised directly."""
        with pytest.raises(KeyError, match="no.such.field"):
            table([Column("no.such.field", "Invented")], [["x"]])

    def test_a_presentational_column_needs_no_entry(self):
        html = table([Column("", "Bar", glossary=False)], [["<span></span>"]])
        assert "Bar" in html

    def test_the_glossary_section_is_rendered(self, sidecar):
        html = build_html(Inputs(portfolio_json=sidecar(_grid())))
        assert 'id="glossary"' in html
        assert "never hand-written here" in html


class TestOutput:
    def test_the_page_is_self_contained(self, sidecar, tmp_path):
        out = tmp_path / "nested" / "dash.html"
        path = write_dashboard(Inputs(portfolio_json=sidecar(_grid())), str(out))
        html = open(path, encoding="utf-8").read()
        assert "<script" not in html, "D6: the page must need no JavaScript"
        assert re.search(r"<link[^>]+href", html) is None, "no external stylesheet"
        assert "<style>" in html

    def test_cli_returns_2_on_an_unmapped_strategy(self, make_store, tmp_path, capsys):
        from dashboard.__main__ import main

        path = make_store(
            intents=[{"seq": 1, "order_id": "o1", "strategy_id": "ghost"}],
            attributions=[{"seq": 2, "entry_order_id": "o1", "strategy_id": "ghost"}],
        )
        code = main(["--store", path, "--out", str(tmp_path / "x.html")])
        assert code == 2
        assert "ghost" in capsys.readouterr().err

    def test_cli_writes_the_page(self, sidecar, tmp_path, capsys):
        from dashboard.__main__ import main

        out = tmp_path / "dash.html"
        assert main(["--portfolio", sidecar(_grid()), "--out", str(out)]) == 0
        assert out.exists()
        assert "wrote" in capsys.readouterr().out
