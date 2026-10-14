"""
dashboard/build.py — assemble the three panels into one static HTML page.

``make dashboard`` calls this. It reads artifacts and writes one file; it opens
no sockets, starts no server, and holds no state between runs (plan-19 D1, D6).
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from strategy_fit.harness import CellResult
from strategy_fit.portfolio_json import (
    SIDECAR_FILENAME,
    cells_from_sidecar,
    load_sidecar,
)

from dashboard.annotations import Annotation, load_annotations
from dashboard import panel_a, panel_b, panel_c, store
from dashboard.render import (
    Column,
    bar,
    banner,
    esc,
    glossary_section,
    num,
    page,
    pill,
    table,
)
from dashboard.strategy_map import STRATEGY_JOINS

TITLE = "Trading system — mining, health, profitability"


@dataclass
class Inputs:
    """Everything the page can be built from. All of it optional."""

    portfolio_json: str | None = None
    previous_portfolio_json: str | None = None
    store_globs: list[str] = field(default_factory=list)
    status_json: str | None = None
    healthcheck_json: str | None = None
    eligibility_json: str | None = None
    annotations_json: str | None = None
    now_s: float | None = None


# ---------------------------------------------------------------------------
# Panel A
# ---------------------------------------------------------------------------

_A_FUNNEL_COLS = (
    Column("panel_a.funnel_stage", "Stage"),
    Column("cell.status", "What it means", glossary=True),
    Column("panel_a.funnel_stage", "Cells", numeric=True),
    Column("panel_a.funnel_stage", "Share", numeric=True),
    Column("", "", glossary=False),
)

_A_COVERAGE_COLS = (
    Column("cell.family_name", "Family"),
    Column("cell.horizon_trading_days", "Hold (td)", numeric=True),
    Column("cell.status", "Catalog status"),
    Column("panel_a.funnel_stage", "Dominant stage"),
    Column("cell.eff_n", "Tested / total", numeric=True),
)

_A_DIFF_COLS = (
    Column("cell.symbol", "Symbol"),
    Column("cell.family_name", "Family"),
    Column("cell.horizon_trading_days", "Hold", numeric=True),
    Column("panel_a.funnel_stage", "Was"),
    Column("panel_a.funnel_stage", "Now"),
    Column("cell.net_edge_bps", "Net edge was", numeric=True),
    Column("cell.net_edge_bps", "Net edge now", numeric=True),
    Column("cell.net_edge_bps", "Δ bps", numeric=True),
)

_A_DETAIL_COLS = (
    Column("cell.symbol", "Field", glossary=False),
    Column("cell.symbol", "Value", glossary=False),
)


def render_panel_a(
    cells: Sequence[CellResult],
    doc: Mapping[str, Any] | None,
    previous: Sequence[CellResult] | None,
) -> tuple[str, set[str]]:
    """Panel A: the alpha mining process. Returns (html, glossary keys used)."""
    used: set[str] = {c.key for c in _A_FUNNEL_COLS + _A_COVERAGE_COLS + _A_DIFF_COLS if c.glossary}
    out = ['<h2 id="panel-a">A · Alpha mining — where candidates die</h2>']

    if not cells:
        out.append(
            '<p class="empty">No strategy-fit sidecar supplied. Run the grid, then pass '
            f"--portfolio path/to/{SIDECAR_FILENAME}.</p>"
        )
        return "\n".join(out), used

    out.append(_provenance_banners(doc))

    funnel = panel_a.build_funnel(cells)
    out.append("<h3>Funnel</h3>")
    out.append(
        f'<p class="sub">{funnel.total:,} catalog cells. Stages are exhaustive and '
        "mutually exclusive — every cell reaches exactly one.</p>"
    )
    if funnel.conservation_error != 0:
        out.append(
            banner(
                "fail",
                f"<strong>Funnel conservation failure:</strong> stage counts differ from the "
                f"cell total by {funnel.conservation_error}. A cell reached no terminal state; "
                "that is a bug in the grid, surfaced here rather than dropped.",
            )
        )
    unclassified = funnel.count(panel_a.STAGE_UNCLASSIFIED)
    if unclassified:
        out.append(
            banner(
                "fail",
                f"<strong>{unclassified} cell(s) are UNCLASSIFIED</strong> — a status the funnel "
                "does not recognise. Investigate before trusting any count on this page.",
            )
        )

    rows = []
    for r in funnel.rows:
        if r.count == 0 and r.stage == panel_a.STAGE_UNCLASSIFIED:
            continue
        kind = "pass" if r.stage == panel_a.STAGE_PASS else ("dead" if r.count else "")
        rows.append(
            [
                f"<code>{esc(r.stage)}</code>",
                esc(r.label),
                num(r.count),
                f"{r.share * 100:.1f}%",
                bar(r.share, kind),
            ]
        )
    out.append(table(_A_FUNNEL_COLS, rows))

    coverage = panel_a.build_coverage(cells)
    out.append("<h3>Coverage grid — family × horizon</h3>")
    out.append(
        '<p class="sub">The completeness contract of plan-08 §7.3 and plan-16, rendered: '
        "<em>no family is silently skipped</em> becomes visible rather than asserted. "
        "Catalog status is what the catalog says; dominant stage is what this run did.</p>"
    )
    if coverage.unmapped_families:
        out.append(
            banner(
                "fail",
                "<strong>Runner families with no entry in dashboard/catalog_map.py:</strong> "
                + ", ".join(f"<code>{esc(f)}</code>" for f in coverage.unmapped_families)
                + " — their coverage claim is unverified. Add a row saying which catalog "
                "family each corresponds to, or that it corresponds to none.",
            )
        )
    if coverage.untested_families:
        out.append(
            banner(
                "warn",
                "<strong>Families in the catalog with no cell in this run:</strong> "
                + ", ".join(f"<code>{esc(f)}</code>" for f in coverage.untested_families)
                + " — compared after mapping runner family names to catalog ids, "
                "so a renamed family does not show up here as a gap.",
            )
        )
    out.append(
        table(
            _A_COVERAGE_COLS,
            [
                [
                    esc(c.family),
                    num(c.hold),
                    esc(c.catalog_status),
                    f"<code>{esc(c.dominant_stage)}</code>",
                    f"{c.tested:,} / {c.total:,}",
                ]
                for c in coverage.cells
            ],
        )
    )

    out.append("<h3>Run diff</h3>")
    if previous is None:
        out.append(
            '<p class="empty">No previous sidecar to compare against. Pass --previous, or keep '
            "the prior run's strategy_portfolio.json beside the current one.</p>"
        )
    else:
        diffs = panel_a.build_diff(previous, cells)
        out.append(
            f'<p class="sub">{len(diffs):,} cell(s) changed, biggest move first. This is the view '
            "that would have caught BNBR flipping −113.1 → +119.5 bps, the ×100 lot-size defect, "
            "and the volatility-estimator flip — each was a change in a value nothing was watching.</p>"
        )
        out.append(
            table(
                _A_DIFF_COLS,
                [
                    [
                        esc(d.symbol),
                        esc(d.family_name),
                        num(d.hold),
                        f"<code>{esc(d.was_stage or '—')}</code>",
                        f"<code>{esc(d.now_stage or '—')}</code>",
                        num(d.was_net_edge_bps),
                        num(d.now_net_edge_bps),
                        num(d.edge_delta_bps),
                    ]
                    for d in diffs[:200]
                ],
                empty="No cell changed stage or net edge between the two runs.",
            )
        )

    out.append(_cell_detail(cells))
    out.append(_thresholds_footer())
    return "\n".join(out), used


def _cell_detail(cells: Sequence[CellResult]) -> str:
    """
    Every field of a few notable cells, labelled from its own docstring.

    Field-drift guard (plan-19 §8): the row list is driven by
    ``dataclasses.fields(CellResult)``, so a new field appears here without
    anyone remembering to add it.
    """
    import dataclasses

    notable = _notable_cells(cells)
    if not notable:
        return ""
    out = ["<h3>Cell detail</h3>", '<p class="sub">Every field, from its own docstring.</p>']
    for label, cell in notable:
        rows = [
            [
                f'<a href="#{esc("gl-cell-" + f.name)}"><code>{esc(f.name)}</code></a>',
                f"<code>{esc(getattr(cell, f.name))}</code>",
            ]
            for f in dataclasses.fields(CellResult)
        ]
        out.append(
            f"<details><summary>{esc(label)}: {esc(cell.symbol)} · "
            f"{esc(cell.family_name)} · hold {cell.horizon_trading_days}</summary>"
            + table(_A_DETAIL_COLS, rows)
            + "</details>"
        )
    return "\n".join(out)


def _notable_cells(cells: Sequence[CellResult]) -> list[tuple[str, CellResult]]:
    """The cells worth expanding: a PASS if there is one, the best and worst tested."""
    tested = [c for c in cells if c.status == "tested" and c.net_edge_bps is not None]
    out: list[tuple[str, CellResult]] = []
    passes = [c for c in cells if panel_a.stage_of(c) == panel_a.STAGE_PASS]
    if passes:
        out.append(("PASS", passes[0]))
    if tested:
        out.append(("Best net edge", max(tested, key=lambda c: c.net_edge_bps)))
        out.append(("Worst net edge", min(tested, key=lambda c: c.net_edge_bps)))
    elif cells:
        out.append(("First cell", cells[0]))
    return out


def _provenance_banners(doc: Mapping[str, Any] | None) -> str:
    """
    Always-visible provenance (plan-19 D4) — banners, never dismissible filters.

    ``is_synthetic`` defaults to True in ``emit_strategy_portfolio``, so a
    missing flag is treated as synthetic. A report is fixture data unless it
    says otherwise.
    """
    if doc is None:
        return banner("synthetic", "<strong>⚠ No provenance available</strong> — treat as synthetic.")
    out: list[str] = []
    if doc.get("is_synthetic", True):
        out.append(
            banner(
                "synthetic",
                "<strong>⚠ SYNTHETIC DATA</strong> — generated from deterministic fixtures. "
                "Not real market data and not a trading signal. "
                "(<code>is_synthetic</code> defaults to true: a report is synthetic unless told otherwise.)",
            )
        )
    else:
        out.append(
            banner("live", "<strong>LIVE DATA</strong> — generated from live chartbit daily history.")
        )

    prov = doc.get("provenance") or {}
    impact = prov.get("impact") or {}
    source = impact.get("coeff_source")
    if source == "unpriced":
        out.append(
            banner(
                "warn",
                "<strong>Impact never priced</strong> for any cell in this run "
                "(<code>impact_priced=false</code> everywhere). That is not the same as "
                "impact being zero.",
            )
        )
    elif source == "mixed":
        out.append(
            banner(
                "warn",
                "<strong>Mixed impact coefficients</strong> — some cells used the calibrated "
                "per-name value and some the global default. Do not average across them.",
            )
        )
    elif source == "measured":
        out.append(
            banner(
                "info",
                "Impact priced with <strong>calibrated per-name coefficients</strong>. Those were "
                "calibrated from tick fills and are <em>borrowed</em> for daily cells; a daily "
                "hurdle shown here carries that borrowing. Impact also moves with the ADV window "
                "and the volatility estimator — PTRO's ranged 3.9 → 33.6 bps by ADV window and its "
                "coefficient moved 3.9× by estimator choice.",
            )
        )

    git = prov.get("git") or {}
    if git.get("dirty"):
        out.append(
            banner(
                "warn",
                f"<strong>Produced from a dirty tree</strong> at <code>{esc((git.get('sha') or '')[:12])}</code> "
                "— the SHA does not fully describe the code that made these numbers.",
            )
        )
    return "\n".join(out)


def _thresholds_footer() -> str:
    t = panel_a.thresholds_in_force()
    items = "".join(
        f"<li><code>{esc(k)}</code> = <code>{esc(v)}</code></li>" for k, v in t.items()
    )
    return (
        "<h3>Thresholds in force</h3>"
        '<p class="sub">Read live from the modules that own them at render time. '
        "This page defines none of them (plan-19 §2 D2).</p>"
        f"<ul>{items}</ul>"
    )


# ---------------------------------------------------------------------------
# Panel B
# ---------------------------------------------------------------------------

_B_COLS = (
    Column("status.outcome", "Stage", glossary=False),
    Column("status.run_id", "Source", glossary=False),
    Column("status.outcome", "State", glossary=False),
    Column("panel_b.heartbeat_age_s", "Freshness"),
    Column("progress.events_seen", "Volume"),
    Column("status.error", "Detail"),
)

_B_RISK_COLS = (
    Column("risk.kill_switch_tripped", "Kill switch"),
    Column("risk.trading_day", "Trading day"),
    Column("risk.day_notional_idr", "Day notional (IDR)", numeric=True),
    Column("risk.day_notional_cap_idr", "Day cap (IDR)", numeric=True),
    Column("risk.gross_exposure_idr", "Gross exposure (IDR)", numeric=True),
    Column("risk.open_reservations", "Open reservations", numeric=True),
)


def render_panel_b(panel: panel_b.PanelB) -> tuple[str, set[str]]:
    used = {c.key for c in _B_COLS + _B_RISK_COLS if c.glossary}
    out = ['<h2 id="panel-b">B · Process health — is the machine running?</h2>']

    if panel.alerts:
        for a in panel.alerts:
            out.append(
                banner(
                    a.severity,
                    f"<strong>{esc(a.condition)}</strong> — {esc(a.message)}",
                )
            )
    out.append(
        '<p class="sub">Alert <em>conditions</em> are defined here (plan-19 Stage 5); '
        "routing them to a human is plan-18 B7's job, not this page's.</p>"
    )
    out.append(
        table(
            _B_COLS,
            [
                [
                    esc(r.stage),
                    f'<span class="mono">{esc(r.source)}</span>',
                    pill(r.status),
                    esc(r.freshness),
                    esc(r.volume),
                    esc(r.detail),
                ]
                for r in panel.rows
            ],
        )
    )

    risk = (panel.status_doc or {}).get("risk")
    out.append("<h3>Risk state</h3>")
    if not isinstance(risk, dict):
        out.append(
            banner(
                "info",
                "<code>risk: null</code> — this run had no RiskSink wired in, so the pre-trade "
                "state was not reported. Null means <em>not reported</em>, never <em>no limits "
                "in force</em>.",
            )
        )
    else:
        out.append(
            table(
                _B_RISK_COLS,
                [
                    [
                        pill("fail" if risk.get("kill_switch_tripped") else "ok",
                             "TRIPPED" if risk.get("kill_switch_tripped") else "clear"),
                        esc(risk.get("trading_day") or "—"),
                        num(risk.get("day_notional_idr"), 0),
                        num(risk.get("day_notional_cap_idr"), 0),
                        num(risk.get("gross_exposure_idr"), 0),
                        num(risk.get("open_reservations")),
                    ]
                ],
            )
        )
        positions = risk.get("positions_idr") or {}
        if positions:
            out.append(
                "<p>Positions: "
                + ", ".join(
                    f"<code>{esc(sym)}</code> {num(v, 0)}" for sym, v in sorted(positions.items())
                )
                + "</p>"
            )
    return "\n".join(out), used


# ---------------------------------------------------------------------------
# Panel C
# ---------------------------------------------------------------------------

_C_COLS = (
    Column("attribution.strategy_id", "Strategy"),
    Column("order_intent.sink_kind", "Sink"),
    Column("counters.round_trips", "Round-trips", numeric=True),
    Column("attribution.symbol", "Symbols", numeric=True),
    Column("attribution.gross_pnl_bps", "Mean gross bps", numeric=True),
    Column("panel_c.mean_net_pnl_bps", "Mean net bps", numeric=True),
    Column("panel_c.fee_slippage_gap_bps", "Fee gap bps", numeric=True),
    Column("panel_c.win_rate", "Win rate", numeric=True),
    Column("attribution.hold_calendar_days", "Mean hold (cal d)", numeric=True),
    Column("panel_c.required_effective_n", "Required N", numeric=True),
)

_C_RISK_COLS = (
    Column("panel_c.mean_net_pnl_bps", "Strategy", glossary=False),
    Column("attribution.net_pnl_bps", "Worst trade bps", numeric=True),
    Column("attribution.net_pnl_bps", "Best trade bps", numeric=True),
    Column("attribution.net_pnl_bps", "Max drawdown bps", numeric=True),
    Column("attribution.as_of_date", "First"),
    Column("attribution.as_of_date", "Last"),
    Column("attribution.run_id", "Runs", numeric=True),
)

_C_FUNNEL_COLS = (
    Column("decision.strategy_id", "Strategy"),
    Column("decision.run_id", "Run"),
    Column("decision.event_kind", "Decisions", numeric=True),
    Column("decision.signal_value", "Signals", numeric=True),
    Column("order_intent.order_id", "Intents", numeric=True),
    Column("fill.fill_lots", "Fills", numeric=True),
    Column("order_intent.order_id", "Submitted−filled", numeric=True),
    Column("order_reject.order_id", "Rejected", numeric=True),
)

_C_REJECT_COLS = (
    Column("order_reject.strategy_id", "Strategy"),
    Column("order_reject.sink_kind", "Sink"),
    Column("order_reject.reason", "Reason"),
    Column("order_reject.order_id", "Count", numeric=True),
)


def render_panel_c(
    panel: panel_c.PanelC,
    runs: Sequence[store.RunHeader],
    annotations: Mapping[str, Annotation] | None = None,
) -> tuple[str, set[str]]:
    annotations = annotations or {}
    used = {c.key for c in _C_COLS + _C_RISK_COLS + _C_FUNNEL_COLS + _C_REJECT_COLS if c.glossary}
    out = ['<h2 id="panel-c">C · Per-strategy profitability — what did each algo earn?</h2>']

    if not panel.rows and not panel.funnels and not runs:
        out.append(
            '<p class="empty">No post-trade store supplied, or no rows in it. '
            "Pass --store with a path or glob.</p>"
        )
        return "\n".join(out), used

    if not panel.rows and not panel.funnels:
        # Runs exist but produced no decisions or round-trips. That is a
        # result — a strategy that never fired — not an absence of data, so
        # the run table below still renders.
        out.append(
            '<p class="empty">Runs were recorded but produced no decisions and no '
            "round-trips: the strategy never fired.</p>"
        )
        out.append(_runs_table(runs, panel.funnels, annotations))
        return "\n".join(out), used

    if panel.has_live or panel.has_live_runs:
        out.append(
            banner(
                "info",
                "<strong>Live rows are real-money fills</strong> (sink <code>live</code>). "
                "Replay/paper and live rows are grouped separately and are never summed.",
            )
        )
    else:
        out.append(
            banner(
                "replay",
                "<strong>Every figure below is replay or paper.</strong> No run in the supplied "
                "stores used a live sink, so no number here has been paid for. Replay PnL and "
                "live PnL are grouped separately and are never summed.",
            )
        )
    if panel.excluded_round_trips:
        out.append(
            banner(
                "info",
                f"{panel.excluded_round_trips} round-trip(s) excluded: reserved strategy id(s) "
                + ", ".join(f"<code>{esc(i)}</code>" for i in panel.excluded_ids)
                + " are calibration traffic (ADR-0016 §6) and are never reported as performance.",
            )
        )

    out.append(
        table(
            _C_COLS,
            [
                [
                    f"<code>{esc(r.strategy_id)}</code>",
                    pill("ok" if not r.is_replay else "warn", r.sink_kind),
                    num(r.round_trips),
                    num(r.symbols),
                    num(r.mean_gross_pnl_bps),
                    num(r.mean_net_pnl_bps),
                    num(r.fee_slippage_gap_bps),
                    f"{r.win_rate * 100:.0f}%",
                    num(r.mean_hold_calendar_days, 2),
                    _required_n_cell(r),
                ]
                for r in panel.rows
            ],
        )
    )

    out.append("<h3>Believability</h3>")
    for r in panel.rows:
        join = r.join
        if join.research_family is None:
            out.append(
                banner(
                    "warn",
                    f"<strong><code>{esc(r.strategy_id)}</code> has no research counterpart.</strong> "
                    + esc(join.note)
                    + " No always-long null is computable from the post-trade store either: the "
                    "runtime records what it traded, not what buying unconditionally would have "
                    "earned. Treat the mean net bps above as an execution result, not as evidence "
                    "of an edge.",
                )
            )
        elif not join.definitions_match:
            out.append(
                banner(
                    "fail",
                    f"<strong><code>{esc(r.strategy_id)}</code> ≠ <code>{esc(join.research_family)}</code>.</strong> "
                    + esc(join.note),
                )
            )

    out.append(
        table(
            _C_RISK_COLS,
            [
                [
                    f"<code>{esc(r.strategy_id)}</code> <span class='mono'>{esc(r.sink_kind)}</span>",
                    num(r.worst_net_pnl_bps),
                    num(r.best_net_pnl_bps),
                    num(r.max_drawdown_bps),
                    esc(r.first_as_of_date or "—"),
                    esc(r.last_as_of_date or "—"),
                    num(len(r.run_ids)),
                ]
                for r in panel.rows
            ],
        )
    )

    out.append("<h3>Decision funnel — signal to fill</h3>")
    out.append(
        '<p class="sub">Each drop-off is a distinct failure mode that is otherwise invisible: '
        "a strategy that never fired looks identical to one that fired and was gated, and to one "
        "that was admitted and never filled.</p>"
    )
    out.append(
        table(
            _C_FUNNEL_COLS,
            [
                [
                    f"<code>{esc(f.strategy_id)}</code>",
                    f"<span class='mono'>{esc(store.run_label(f.run_id))}</span>",
                    num(f.decisions),
                    num(f.signals),
                    num(f.intents),
                    num(f.fills),
                    num(f.intents_unfilled),
                    _rejected_cell(f, annotations.get(f.run_id)),
                ]
                for f in panel.funnels
            ],
        )
    )

    if panel.rejects:
        out.append("<h3>Rejections</h3>")
        out.append(
            '<p class="sub">Every intent a risk gate, the market gate, or the broker refused — '
            "grouped by reason, so a rejection storm is visible as a count, not just a smaller "
            "fill total.</p>"
        )
        out.append(
            table(
                _C_REJECT_COLS,
                [
                    [
                        f"<code>{esc(r.strategy_id)}</code>",
                        pill("ok" if r.sink_kind == "live" else "warn", r.sink_kind),
                        esc(r.reason),
                        num(r.count),
                    ]
                    for r in panel.rejects
                ],
            )
        )

    out.append(_runs_table(runs, panel.funnels, annotations))
    return "\n".join(out), used


def _required_n_cell(r: panel_c.StrategyPnL) -> str:
    if r.required_effective_n is None:
        return "—"
    flag = "fail" if r.underpowered else "ok"
    return f"{r.required_effective_n:,} " + pill(flag, f"have {r.round_trips:,}")


_RUN_COLS = (
    Column("run.run_id", "Run"),
    Column("run.outcome", "Outcome"),
    Column("run.source", "Source"),
    Column("run.fill_mode", "Fill mode"),
    Column("run.strategy_id", "Strategy"),
    Column("run.input_spec", "Input"),
    Column("run.binary_hash", "Binary"),
    # The run table's counter columns are RunRow.Counters, so the definition
    # that owns them is engine.Counters — not a `run.*` field of its own.
    Column("counters.round_trips", "Round-trips", numeric=True),
    Column("", "Note", glossary=False),
)


_OPERATOR_TAG = "<span class='mono'>operator-written</span>"


def _rejected_cell(f: store.DecisionFunnel, ann: Annotation | None) -> str:
    if not f.rejects_recorded:
        return "n/a (no order_reject table)"
    cell = num(f.rejects)
    if f.rejects == 0 and ann is not None and ann.reject_reason:
        cell += (
            f"<br>0 recorded; {_OPERATOR_TAG}: <code>{esc(ann.reject_reason)}</code> "
            f"<span class='mono'>({esc(ann.source)})</span>"
        )
    return cell


def _run_note(
    r: store.RunHeader, funnels: Sequence[store.DecisionFunnel], ann: Annotation | None
) -> str:
    parts = []
    if r.crashed:
        mine = [f for f in funnels if f.run_id == r.run_id]
        decisions = sum(f.decisions for f in mine)
        intents = sum(f.intents for f in mine)
        parts.append(
            f"no end row; {decisions:,} decisions, {intents:,} intents recorded"
        )
    if r.error:
        parts.append(esc(r.error))
    if ann is not None:
        parts.append(f"{_OPERATOR_TAG}: {esc(ann.note)} <span class='mono'>({esc(ann.source)})</span>")
    return "<br>".join(parts) or "—"


def _runs_table(
    runs: Sequence[store.RunHeader],
    funnels: Sequence[store.DecisionFunnel] = (),
    annotations: Mapping[str, Annotation] | None = None,
) -> str:
    if not runs:
        return ""
    annotations = annotations or {}
    rows = []
    for r in sorted(runs, key=lambda r: r.started_ns, reverse=True):
        state = {"complete": "ok", "error": "fail", "running": "warn"}.get(r.outcome, "unknown")
        label = r.outcome + (" (no end row)" if r.crashed else "")
        rows.append(
            [
                f"<span class='mono'>{esc(r.run_id)}</span>",
                pill(state, label),
                esc(r.source),
                esc(r.fill_mode),
                f"<code>{esc(r.strategy_id)}</code>",
                f"<span class='mono'>{esc(r.input_spec)}</span>",
                f"<code>{esc(r.binary_hash[:12] or '—')}</code>",
                num(r.round_trips),
                _run_note(r, funnels, annotations.get(r.run_id)),
            ]
        )
    return "<h3>Runs</h3>" + table(_RUN_COLS, rows)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _load_cells(path: str | None) -> tuple[list[CellResult], Mapping[str, Any] | None]:
    if not path or not os.path.exists(path):
        return [], None
    doc = load_sidecar(path)
    return cells_from_sidecar(doc), doc


def build_html(inputs: Inputs) -> str:
    """Render the whole page. Every input is optional; missing ones say so."""
    cells, doc = _load_cells(inputs.portfolio_json)
    previous, _ = _load_cells(inputs.previous_portfolio_json)

    data = store.read_stores(inputs.store_globs) if inputs.store_globs else store.StoreData()
    c_panel = panel_c.build_panel_c(data)
    b_panel = panel_b.build_panel_b(
        status_path=inputs.status_json,
        healthcheck_path=inputs.healthcheck_json,
        eligibility_path=inputs.eligibility_json,
        runs=data.runs,
        now_s=inputs.now_s,
    )

    a_html, a_keys = render_panel_a(cells, doc, previous if inputs.previous_portfolio_json else None)
    b_html, b_keys = render_panel_b(b_panel)
    annotations = load_annotations(inputs.annotations_json)
    shown = {r.run_id for r in data.runs} | {f.run_id for f in data.funnels}
    c_html, c_keys = render_panel_c(
        c_panel, data.runs, {k: v for k, v in annotations.items() if k in shown}
    )

    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = [
        f"<h1>{esc(TITLE)}</h1>",
        f'<p class="sub">Generated {esc(generated)} · '
        '<a href="#panel-a">mining</a> · <a href="#panel-b">health</a> · '
        '<a href="#panel-c">profitability</a> · <a href="#glossary">glossary</a></p>',
    ]
    if data.errors:
        header.append(
            banner(
                "fail",
                "<strong>Store read errors:</strong> "
                + "; ".join(esc(e) for e in data.errors)
                + " — these files contributed no rows, so every total below is short by "
                "an unknown amount.",
            )
        )

    detail_keys = {f"cell.{f}" for f in _cellresult_field_names()}
    glossary_keys = a_keys | b_keys | c_keys | detail_keys

    footer = (
        "Static page, regenerated by <code>make dashboard</code>. No server, no metrics "
        "daemon, no JavaScript (plan-19 D1/D6). Mapped strategy ids: "
        + ", ".join(f"<code>{esc(s)}</code>" for s in sorted(STRATEGY_JOINS))
        + " — an unmapped id fails the render rather than being guessed at."
    )
    return page(TITLE, header + [a_html, b_html, c_html, glossary_section(glossary_keys)], footer)


def _cellresult_field_names() -> list[str]:
    import dataclasses

    return [f.name for f in dataclasses.fields(CellResult)]


def write_dashboard(inputs: Inputs, out_path: str) -> str:
    """Render and write the page; return its path."""
    html_text = build_html(inputs)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return out_path
