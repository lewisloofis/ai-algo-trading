"""
dashboard/panel_b.py — process health (plan-19 §4).

Question answered: **is the machine running, and is it recording anything
real?**

One row per pipeline stage, each with freshness, a volume counter and the last
error.  Every source already exists except the runtime heartbeat, which
plan-19 Stage 5 added as ``status.json``:

    collector capture / S3 segments   scripts/collector_healthcheck.py --json
    universe eligibility snapshot     universe/dt=*/eligibility.json in S3
    strategy runtime                  status.json (engine.StatusWriter)
    account reconciliation            strategy/account/recon.go

This module reads those artifacts; it does not run the checks.  In particular
it never calls S3 — ``collector_healthcheck.py --json`` does, and the operator
(or cron) decides when.  A dashboard that reached for the network would fail to
render exactly when the network is the thing that is broken.

Staleness is always measured against ``status.wall_clock_ns``, never against
``progress.last_event_ns``: in a replay the event clock is historical, and a
healthy replay of the 2026-06-22 session has an event age of months.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

# Alert conditions for plan-18 B7. Defined here because they are THIS plan's
# Stage 5 deliverable ("Stage 5 defines the conditions; delivery is B7's"), and
# named as constants so the routing side can import them rather than restate
# them.
STALE_HEARTBEAT_S: int = 120
"""
Seconds without a status.json rewrite before the runtime row goes red, during
session hours. Twenty-four times the default 5s write interval: long enough
that a slow disk is not an incident, short enough to notice within two minutes.
"""

ALERT_KILL_SWITCH_TRIPPED = "kill_switch_tripped"
ALERT_STALE_HEARTBEAT = "stale_heartbeat"
ALERT_RUN_CRASHED = "run_crashed"
ALERT_RUN_ERROR = "run_error"
ALERT_COLLECTOR_FAILING = "collector_failing"
ALERT_RECON_DIVERGENCE = "recon_divergence"

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"
"""
Distinct from ok/warn/fail on purpose: "nobody has told me" is not "fine". A
missing status.json means the runtime has never run, or its file was never
wired up — either way it is not a green row.
"""


@dataclass(frozen=True)
class StageRow:
    """One pipeline stage's health."""

    stage: str
    source: str
    status: str
    freshness: str
    volume: str
    detail: str


@dataclass(frozen=True)
class Alert:
    """One fired alert condition, for plan-18 B7 to route."""

    condition: str
    severity: str
    message: str


@dataclass
class PanelB:
    rows: list[StageRow] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    status_doc: Mapping[str, Any] | None = None
    healthcheck_doc: Mapping[str, Any] | None = None


def load_json(path: str | None) -> Mapping[str, Any] | None:
    """Read a JSON artifact, or None if it is absent or unreadable."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _age_s(ns: int | None, now_s: float) -> float | None:
    if not ns:
        return None
    return now_s - ns / 1e9


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


def runtime_row(status: Mapping[str, Any] | None, now_s: float) -> tuple[StageRow, list[Alert]]:
    """The strategy-runtime row, from status.json."""
    if status is None:
        return (
            StageRow(
                stage="Strategy runtime",
                source="status.json (engine.StatusWriter)",
                status=STATUS_UNKNOWN,
                freshness="—",
                volume="—",
                detail=(
                    "No status.json found. Either strategy-run has never run with "
                    "--status, or the path given to the dashboard is wrong. This is "
                    "not a healthy runtime; it is an unobserved one."
                ),
            ),
            [],
        )

    alerts: list[Alert] = []
    age = _age_s(status.get("wall_clock_ns"), now_s)
    outcome = status.get("outcome", "unknown")
    progress = status.get("progress") or {}
    counters = progress.get("counters") or {}
    risk = status.get("risk")

    state = STATUS_OK
    details: list[str] = []

    if outcome == "error":
        state = STATUS_FAIL
        details.append(f"run ended with an error: {status.get('error', '')}")
        alerts.append(
            Alert(ALERT_RUN_ERROR, STATUS_FAIL, f"run {status.get('run_id')} ended with an error")
        )
    elif outcome == "running" and age is not None and age > STALE_HEARTBEAT_S:
        state = STATUS_FAIL
        details.append(
            f"heartbeat is {_fmt_age(age)} but the run still claims to be running "
            f"— stale beyond {STALE_HEARTBEAT_S}s"
        )
        alerts.append(
            Alert(
                ALERT_STALE_HEARTBEAT,
                STATUS_FAIL,
                f"status.json for run {status.get('run_id')} is {age:.0f}s old while marked running",
            )
        )
    elif outcome == "complete":
        state = STATUS_OK
        details.append("run completed cleanly")

    if isinstance(risk, dict) and risk.get("kill_switch_tripped"):
        state = STATUS_FAIL
        reason = risk.get("kill_switch_reason", "")
        details.append(f"KILL SWITCH TRIPPED: {reason}")
        alerts.append(Alert(ALERT_KILL_SWITCH_TRIPPED, STATUS_FAIL, f"kill switch tripped: {reason}"))
    elif risk is None:
        details.append(
            "no RiskSink wired into this run — 'risk: null' means not reported, "
            "never 'no limits in force'"
        )

    volume = (
        f"{progress.get('events_seen', 0):,} events · "
        f"{counters.get('fills', 0)} fills · "
        f"{counters.get('round_trips', 0)} round-trips"
    )
    return (
        StageRow(
            stage="Strategy runtime",
            source=f"status.json · run {status.get('run_id', '?')} · {status.get('source', '?')}",
            status=state,
            freshness=_fmt_age(age),
            volume=volume,
            detail="; ".join(details) or "running",
        ),
        alerts,
    )


def collector_rows(doc: Mapping[str, Any] | None) -> tuple[list[StageRow], list[Alert]]:
    """
    The collector and S3 rows, from ``collector_healthcheck.py --json``.

    The health check's own exit code is the verdict — 0 clear, 1 warnings,
    2 failures — and it is reused rather than re-derived here.
    """
    if doc is None:
        # Both rows still appear. A stage that vanishes from the table when its
        # source is missing reads as "not part of the system"; a stage marked
        # unknown reads as "nobody has checked", which is the true state.
        missing = (
            "No health-check JSON supplied. Run "
            "`python scripts/collector_healthcheck.py --json > health.json` "
            "and pass it with --healthcheck."
        )
        return (
            [
                StageRow(
                    stage="Collector capture",
                    source="scripts/collector_healthcheck.py --json",
                    status=STATUS_UNKNOWN,
                    freshness="—",
                    volume="—",
                    detail=missing,
                ),
                StageRow(
                    stage="S3 segments / binary",
                    source="scripts/collector_healthcheck.py --json",
                    status=STATUS_UNKNOWN,
                    freshness="—",
                    volume="—",
                    detail=missing,
                ),
            ],
            [],
        )

    exit_code = doc.get("exit_code", 0)
    state = {0: STATUS_OK, 1: STATUS_WARN}.get(exit_code, STATUS_FAIL)
    days = doc.get("days") or []
    ok_days = sum(1 for d in days if d.get("status") == "ok")
    problems = [d for d in days if d.get("status") not in ("ok", "skipped_non_trading_day")]
    findings = doc.get("findings") or []

    alerts: list[Alert] = []
    if exit_code >= 2:
        alerts.append(
            Alert(ALERT_COLLECTOR_FAILING, STATUS_FAIL, f"collector health check failed ({len(findings)} finding(s))")
        )

    rows = [
        StageRow(
            stage="Collector capture",
            source="scripts/collector_healthcheck.py --json",
            status=state,
            freshness=str(doc.get("generated_at", "—")),
            volume=f"{ok_days}/{len(days)} days OK over a {doc.get('lookback_days', '?')}-day lookback",
            detail=(
                "; ".join(str(f.get("message", f)) for f in findings[:3])
                or (f"{len(problems)} problem day(s)" if problems else "clear")
            ),
        ),
        StageRow(
            stage="S3 segments / binary",
            source=f"s3://{doc.get('bucket', '?')}",
            status=state,
            freshness=str((doc.get("binary_freshness") or {}).get("last_modified", "—")),
            volume=str(doc.get("artifact_key", "—")),
            detail=str((doc.get("binary_freshness") or {}).get("message", "")),
        ),
    ]
    return rows, alerts


def eligibility_row(path: str | None) -> StageRow:
    """
    The universe eligibility snapshot row.

    These objects have accrued since 2026-09-13 and nothing has read one back.
    Rendering it is most of the point.
    """
    doc = load_json(path)
    if doc is None:
        return StageRow(
            stage="Universe eligibility",
            source="universe/dt=*/eligibility.json",
            status=STATUS_UNKNOWN,
            freshness="—",
            volume="—",
            detail="No eligibility snapshot supplied (--eligibility).",
        )
    names = doc.get("eligible") or doc.get("symbols") or []
    return StageRow(
        stage="Universe eligibility",
        source=str(path),
        status=STATUS_OK,
        freshness=str(doc.get("as_of") or doc.get("dt") or "—"),
        volume=f"{len(names)} eligible name(s)",
        detail=str(doc.get("note", "")),
    )


def store_rows(runs: "list[Any]") -> tuple[StageRow, list[Alert]]:
    """
    The post-trade store row: how many runs, and did any of them crash.

    A run header still marked "running" long after the fact is a process that
    died — the reason BeginRun writes the header at start rather than at exit.
    """
    alerts: list[Alert] = []
    if not runs:
        return (
            StageRow(
                stage="Post-trade store",
                source="strategy/engine/store.go",
                status=STATUS_UNKNOWN,
                freshness="—",
                volume="no runs recorded",
                detail="No `run` rows. Either no run has been recorded since plan-19 Stage 3, or no store was supplied.",
            ),
            alerts,
        )

    crashed = [r for r in runs if getattr(r, "crashed", False)]
    errored = [r for r in runs if getattr(r, "outcome", "") == "error"]
    state = STATUS_OK
    details: list[str] = []
    if crashed:
        state = STATUS_WARN
        details.append(f"{len(crashed)} run(s) still marked 'running' — died before finishing")
        alerts.append(
            Alert(ALERT_RUN_CRASHED, STATUS_WARN, f"{len(crashed)} run header(s) left at 'running'")
        )
    if errored:
        state = STATUS_FAIL
        details.append(f"{len(errored)} run(s) ended with an error")

    latest = max(runs, key=lambda r: r.started_ns)
    return (
        StageRow(
            stage="Post-trade store",
            source=str(latest.store_path),
            status=state,
            freshness=_fmt_age(_age_s(latest.started_ns, time.time())),
            volume=f"{len(runs)} run(s)",
            detail="; ".join(details) or "all runs accounted for",
        ),
        alerts,
    )


def build_panel_b(
    status_path: str | None = None,
    healthcheck_path: str | None = None,
    eligibility_path: str | None = None,
    runs: "list[Any] | None" = None,
    now_s: float | None = None,
) -> PanelB:
    """Assemble Panel B from whichever artifacts were supplied."""
    now_s = time.time() if now_s is None else now_s
    status = load_json(status_path)
    health = load_json(healthcheck_path)

    panel = PanelB(status_doc=status, healthcheck_doc=health)

    collector, collector_alerts = collector_rows(health)
    panel.rows.extend(collector)
    panel.alerts.extend(collector_alerts)

    panel.rows.append(eligibility_row(eligibility_path))

    runtime, runtime_alerts = runtime_row(status, now_s)
    panel.rows.append(runtime)
    panel.alerts.extend(runtime_alerts)

    store, store_alerts = store_rows(runs or [])
    panel.rows.append(store)
    panel.alerts.extend(store_alerts)

    panel.rows.append(
        StageRow(
            stage="Account reconciliation",
            source="strategy/account/recon.go",
            status=STATUS_UNKNOWN,
            freshness="—",
            volume="—",
            detail=(
                "recon runs but does not persist its output, so there is nothing to "
                "render. Persisting it is the remaining Panel B gap "
                f"(alert condition: {ALERT_RECON_DIVERGENCE})."
            ),
        )
    )
    return panel
