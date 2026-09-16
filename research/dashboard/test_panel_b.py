"""
Panel B tests — process health.

The contract-shaped one is ``test_reads_the_real_healthcheck_key_set``: the keys
are extracted from ``scripts/collector_healthcheck.py``'s own
``_result_to_dict``, so renaming a key there fails here rather than silently
rendering a blank row on the page.
"""
from __future__ import annotations

import ast
import json
import os
import re

import pytest

from dashboard.panel_b import (
    ALERT_COLLECTOR_FAILING,
    ALERT_KILL_SWITCH_TRIPPED,
    ALERT_RUN_CRASHED,
    ALERT_RUN_ERROR,
    ALERT_STALE_HEARTBEAT,
    STALE_HEARTBEAT_S,
    STATUS_FAIL,
    STATUS_OK,
    STATUS_UNKNOWN,
    STATUS_WARN,
    build_panel_b,
    collector_rows,
    load_json,
    runtime_row,
    store_rows,
)
from dashboard.store import RunHeader

HEALTHCHECK_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "collector_healthcheck.py",
)

NOW_S = 1_800_000_000.0
NOW_NS = int(NOW_S * 1e9)


def healthcheck_keys() -> list[str]:
    """The top-level keys collector_healthcheck.py --json actually emits."""
    src = open(HEALTHCHECK_PY, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_result_to_dict":
            ret = next(n for n in ast.walk(node) if isinstance(n, ast.Return))
            return [k.value for k in ret.value.keys]
    raise LookupError("_result_to_dict not found")


def status_doc(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "run_id": "20260916T023552Z-06055d6b",
        "wall_clock_ns": NOW_NS,
        "written_at": "2026-09-16T00:00:00Z",
        "source": "replay",
        "sink_kind": "paper",
        "strategy_id": "orb",
        "input_spec": "segments/*",
        "outcome": "running",
        "progress": {
            "events_seen": 413_094,
            "last_event_ns": 1_782_120_299_520_208_219,
            "last_event_as_of_date": "2026-06-22",
            "counters": {"orders_attempted": 10, "fills": 10, "total_fees_idr": 6162,
                          "round_trips": 5, "net_pnl_bps": -320.32},
        },
        "risk": None,
    }
    base.update(overrides)
    return base


def run_header(**overrides) -> RunHeader:
    base = dict(
        run_id="r1", started_ns=NOW_NS, ended_ns=NOW_NS, outcome="complete", error="",
        source="replay", pacing="fast", fill_mode="taker", sink_kind="paper",
        strategy_id="orb", input_spec="segments/*", binary_hash="abc",
        orders_attempted=1, fills=1, total_fees_idr=1, round_trips=1, net_pnl_bps=0.0,
        store_path="/tmp/run.db",
    )
    base.update(overrides)
    return RunHeader(**base)


class TestHealthcheckContract:
    def test_reads_the_real_healthcheck_key_set(self):
        """Every key Panel B reads must be one the health check actually emits."""
        keys = set(healthcheck_keys())
        for needed in (
            "generated_at", "bucket", "artifact_key", "binary_freshness",
            "lookback_days", "days", "findings", "exit_code",
        ):
            assert needed in keys, f"Panel B reads {needed!r}, which the health check no longer emits"

    def test_exit_code_is_the_verdict_not_a_re_derivation(self):
        assert collector_rows({"exit_code": 0, "days": [], "findings": []})[0][0].status == STATUS_OK
        assert collector_rows({"exit_code": 1, "days": [], "findings": []})[0][0].status == STATUS_WARN
        assert collector_rows({"exit_code": 2, "days": [], "findings": []})[0][0].status == STATUS_FAIL

    def test_a_failing_check_raises_an_alert(self):
        _, alerts = collector_rows({"exit_code": 2, "days": [], "findings": [{"message": "stale"}]})
        assert [a.condition for a in alerts] == [ALERT_COLLECTOR_FAILING]

    def test_missing_healthcheck_is_unknown_not_ok(self):
        rows, alerts = collector_rows(None)
        assert [r.status for r in rows] == [STATUS_UNKNOWN, STATUS_UNKNOWN]
        assert alerts == []
        assert "--healthcheck" in rows[0].detail

    def test_both_collector_stages_appear_even_with_no_data(self):
        """A stage that vanishes reads as 'not part of the system'."""
        rows, _ = collector_rows(None)
        assert [r.stage for r in rows] == ["Collector capture", "S3 segments / binary"]

    def test_day_counts_are_summarised(self):
        rows, _ = collector_rows({
            "exit_code": 0, "lookback_days": 5, "findings": [],
            "days": [{"status": "ok"}, {"status": "ok"}, {"status": "no_segments"}],
        })
        assert "2/3 days OK" in rows[0].volume


class TestRuntimeRow:
    def test_a_missing_status_file_is_unknown(self):
        row, alerts = runtime_row(None, NOW_S)
        assert row.status == STATUS_UNKNOWN
        assert alerts == []

    def test_a_fresh_running_heartbeat_is_ok(self):
        row, alerts = runtime_row(status_doc(), NOW_S)
        assert row.status == STATUS_OK
        assert alerts == []

    def test_a_stale_heartbeat_fires(self):
        row, alerts = runtime_row(status_doc(), NOW_S + STALE_HEARTBEAT_S + 1)
        assert row.status == STATUS_FAIL
        assert [a.condition for a in alerts] == [ALERT_STALE_HEARTBEAT]

    def test_the_staleness_boundary_is_not_off_by_one(self):
        _, alerts = runtime_row(status_doc(), NOW_S + STALE_HEARTBEAT_S)
        assert alerts == [], "exactly at the threshold is not yet stale"

    def test_a_completed_run_is_never_stale(self):
        """A finished replay's heartbeat stops; that is not a failure."""
        row, alerts = runtime_row(status_doc(outcome="complete"), NOW_S + 10 * STALE_HEARTBEAT_S)
        assert row.status == STATUS_OK
        assert alerts == []

    def test_staleness_is_measured_against_wall_clock_not_event_time(self):
        """
        The replay's last event is from 2026-06-22, months before 'now'. Judging
        liveness by event time would call every healthy replay dead.
        """
        doc = status_doc()
        assert doc["progress"]["last_event_ns"] < doc["wall_clock_ns"]
        row, alerts = runtime_row(doc, NOW_S)
        assert row.status == STATUS_OK
        assert alerts == []

    def test_an_errored_run_fires(self):
        row, alerts = runtime_row(status_doc(outcome="error", error="feed died"), NOW_S)
        assert row.status == STATUS_FAIL
        assert [a.condition for a in alerts] == [ALERT_RUN_ERROR]

    def test_a_tripped_kill_switch_fires_even_on_a_fresh_run(self):
        doc = status_doc(risk={"kill_switch_tripped": True, "kill_switch_reason": "file present"})
        row, alerts = runtime_row(doc, NOW_S)
        assert row.status == STATUS_FAIL
        assert ALERT_KILL_SWITCH_TRIPPED in [a.condition for a in alerts]

    def test_null_risk_is_called_out_as_not_reported(self):
        row, _ = runtime_row(status_doc(risk=None), NOW_S)
        assert "never 'no limits in force'" in row.detail

    def test_volume_reports_events_fills_and_round_trips(self):
        row, _ = runtime_row(status_doc(), NOW_S)
        assert "413,094 events" in row.volume
        assert "10 fills" in row.volume
        assert "5 round-trips" in row.volume


class TestStoreRow:
    def test_no_runs_is_unknown(self):
        row, alerts = store_rows([])
        assert row.status == STATUS_UNKNOWN
        assert alerts == []

    def test_a_header_left_at_running_is_a_crashed_run(self):
        row, alerts = store_rows([run_header(outcome="running", ended_ns=None)])
        assert row.status == STATUS_WARN
        assert [a.condition for a in alerts] == [ALERT_RUN_CRASHED]
        assert "died before finishing" in row.detail

    def test_an_errored_run_is_a_failure(self):
        row, _ = store_rows([run_header(outcome="error", error="boom")])
        assert row.status == STATUS_FAIL

    def test_clean_runs_are_ok(self):
        row, alerts = store_rows([run_header(), run_header(run_id="r2")])
        assert row.status == STATUS_OK
        assert alerts == []
        assert "2 run(s)" in row.volume


class TestAssembly:
    def test_every_stage_gets_a_row(self, tmp_path):
        panel = build_panel_b(now_s=NOW_S)
        stages = [r.stage for r in panel.rows]
        assert stages == [
            "Collector capture",
            "S3 segments / binary",
            "Universe eligibility",
            "Strategy runtime",
            "Post-trade store",
            "Account reconciliation",
        ]

    def test_reconciliation_says_why_it_has_nothing_to_show(self):
        panel = build_panel_b(now_s=NOW_S)
        recon = next(r for r in panel.rows if r.stage == "Account reconciliation")
        assert recon.status == STATUS_UNKNOWN
        assert "does not persist its output" in recon.detail

    def test_an_unparseable_artifact_is_treated_as_absent(self, tmp_path):
        broken = tmp_path / "status.json"
        broken.write_text("{ not json")
        assert load_json(str(broken)) is None
        panel = build_panel_b(status_path=str(broken), now_s=NOW_S)
        runtime = next(r for r in panel.rows if r.stage == "Strategy runtime")
        assert runtime.status == STATUS_UNKNOWN

    def test_it_reads_a_real_status_file_written_by_the_go_writer(self, tmp_path):
        """Round-trips the exact document engine.StatusWriter emits."""
        path = tmp_path / "status.json"
        path.write_text(json.dumps(status_doc(outcome="complete")))
        panel = build_panel_b(status_path=str(path), now_s=NOW_S)
        runtime = next(r for r in panel.rows if r.stage == "Strategy runtime")
        assert runtime.status == STATUS_OK
        assert "20260916T023552Z-06055d6b" in runtime.source
