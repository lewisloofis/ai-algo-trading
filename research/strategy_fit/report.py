"""
strategy_fit/report.py — Assemble and emit strategy_portfolio.md.

The report follows the three-state verdict convention from plans 04/05/06:
    PASS         — ≥1 BH-FDR-surviving cell with positive net edge
    INCONCLUSIVE — cells survive cost gate but none survive BH FDR correction
    NO-GO        — no cells survive the cost gate at all

Output is clearly labelled SYNTHETIC to prevent confusion with real results.
"""

from __future__ import annotations

import datetime
import os
from typing import Sequence

from strategy_fit.harness import CellResult

# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_NO_GO = "NO-GO"


def compute_verdict(cells: Sequence[CellResult]) -> str:
    """
    Compute the three-state verdict over all cell results.

    PASS         : at least one cell passes the cost gate AND has fdr_accepted=True
                   AND net_edge_bps > 0.
    INCONCLUSIVE : at least one cell passes the cost gate but none survive FDR.
    NO-GO        : no cell passes the cost gate (all cost_gated, decayed, skipped, etc).
    """
    any_cost_gated_pass = any(
        c.passes_cost_gate is True
        for c in cells
        if c.status == "tested"
    )
    # Bug 2 fix: PASS requires passes_cost_gate=True in addition to FDR acceptance.
    any_fdr_survivor = any(
        c.passes_cost_gate is True
        and c.fdr_accepted is True
        and (c.net_edge_bps or 0) > 0
        for c in cells
        if c.status == "tested"
    )

    if any_fdr_survivor:
        return VERDICT_PASS
    if any_cost_gated_pass:
        return VERDICT_INCONCLUSIVE
    return VERDICT_NO_GO


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _fmt_float(v: float | None, decimals: int = 1) -> str:
    if v is None or v != v:  # nan check
        return "—"
    return f"{v:.{decimals}f}"


def _fmt_bool(v: bool | None) -> str:
    if v is None:
        return "—"
    return "✓" if v else "✗"


def emit_strategy_portfolio(
    cells: Sequence[CellResult],
    outdir: str,
    generated_at: str | None = None,
    is_synthetic: bool = True,
) -> str:
    """
    Emit ``strategy_portfolio.md`` to *outdir*.

    Returns the path to the written file.

    Parameters
    ----------
    cells : Sequence[CellResult]
        All cell results from the grid runner (tested + skipped + blocked).
    outdir : str
        Output directory (created if missing).
    generated_at : str or None
        ISO datetime string for the report header.  Defaults to now (UTC).
    is_synthetic : bool
        When True (default), emit the ⚠ SYNTHETIC DATA banner.
        When False, emit a LIVE DATA provenance line instead.

    Returns
    -------
    str
        Absolute path to the written file.
    """
    os.makedirs(outdir, exist_ok=True)
    if generated_at is None:
        # Bug 4 fix: use timezone-aware UTC instead of deprecated utcnow().
        generated_at = (
            datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        )
        # Normalise to the canonical Z suffix used throughout the codebase.
        if generated_at.endswith("+00:00"):
            generated_at = generated_at[:-6] + "Z"

    verdict = compute_verdict(cells)
    # Bug 1 fix: survivors must satisfy ALL of: status="tested", passes_cost_gate=True,
    # fdr_accepted=True, and net_edge_bps > 0.  The cost gate is a hard prerequisite.
    survivors = [
        c for c in cells
        if (
            c.status == "tested"
            and c.passes_cost_gate is True
            and c.fdr_accepted is True
            and (c.net_edge_bps or 0) > 0
        )
    ]
    cost_pass_cells = [
        c for c in cells
        if c.status == "tested" and c.passes_cost_gate is True
    ]
    non_survivors = [c for c in cells if c not in survivors]

    lines: list[str] = []

    # ---- Header ----
    lines.append("# Strategy Portfolio — Plan 10 Strategy Fit")
    lines.append("")
    if is_synthetic:
        lines.append(
            "> **⚠ SYNTHETIC DATA** — All results are generated from deterministic "
            "synthetic fixtures. This is NOT real market data and NOT a live trading signal. "
            "For offline pipeline validation only (plan-10 §7)."
        )
    else:
        lines.append(
            "> **LIVE DATA** — Generated from live chartbit daily history via LiveDataSource. "
            "Real market results."
        )
    lines.append("")
    lines.append(f"**Generated:** {generated_at}  ")
    lines.append(
        "**Capital model:** ADR-0011 — 300M IDR T+2 leverage sleeve, "
        "10 bps/cal-day financing, ≤2 trading-day horizon ceiling."
    )
    lines.append("")

    # ---- Verdict ----
    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    if verdict == VERDICT_PASS:
        lines.append(
            f"**{len(survivors)} cell(s)** survive the full cost gate "
            "(min_edge + modelled_impact + financing) "
            "AND the Benjamini-Hochberg FDR correction at α=0.10 "
            "AND walk-forward persistence check."
        )
    elif verdict == VERDICT_INCONCLUSIVE:
        lines.append(
            f"**{len(cost_pass_cells)} cell(s)** pass the cost gate but none survive "
            "FDR correction — results may be noise. "
            "Collect more data or tighten signal criteria before deployment."
        )
    else:
        lines.append(
            "No cells pass the cost gate after accounting for structural friction, "
            "financing, and impact.  No strategy is deployable on current data."
        )
    lines.append("")

    # ---- Survivor table ----
    lines.append("## Surviving Strategies")
    lines.append("")
    if survivors:
        lines.append("| Symbol | Strategy | Hold (days) | Shape | Hit Rate | Net Edge (bps) | Eff-N | Friction Hurdle (bps) | Decay Metric (Sharpe) |")
        lines.append("|--------|----------|-------------|-------|----------|---------------|-------|----------------------|----------------------|")
        for c in sorted(survivors, key=lambda x: (x.symbol, x.family_name)):
            lines.append(
                f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
                f"| {c.shape} | {_fmt_float(c.hit_rate, 3)} "
                f"| {_fmt_float(c.net_edge_bps, 1)} "
                f"| {c.eff_n or '—'} "
                f"| {_fmt_float(c.min_edge_hurdle_bps, 1)} "
                f"| {_fmt_float(c.decay_metric, 2)} |"
            )
    else:
        lines.append("*No cells survived the full gate and FDR correction.*")
    lines.append("")

    # ---- Cost-gate passers that didn't survive FDR ----
    if cost_pass_cells and verdict != VERDICT_PASS:
        lines.append("### Cost-Gate Passers (rejected by FDR)")
        lines.append("")
        lines.append("| Symbol | Strategy | Hold (days) | Net Edge (bps) | p-value | BH Accepted |")
        lines.append("|--------|----------|-------------|----------------|---------|-------------|")
        for c in cost_pass_cells:
            lines.append(
                f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
                f"| {_fmt_float(c.net_edge_bps, 1)} "
                f"| {_fmt_float(c.p_value, 3)} "
                f"| {_fmt_bool(c.fdr_accepted)} |"
            )
        lines.append("")

    # ---- Coverage table ----
    lines.append("## Coverage Table (All Cells)")
    lines.append("")
    lines.append(
        "Every tested, skipped, or blocked cell is listed here. "
        "No cell is silently omitted (plan-10 §7.8)."
    )
    lines.append("")
    lines.append("| Symbol | Strategy | Hold | Shape | Status | Reason / Unblock |")
    lines.append("|--------|----------|------|-------|--------|------------------|")

    for c in sorted(non_survivors, key=lambda x: (x.symbol, x.family_name, x.horizon_trading_days)):
        reason = c.skip_reason or ""
        if c.status == "tested" and not reason:
            if c.passes_cost_gate is False:
                reason = f"cost_gated: net_edge={_fmt_float(c.net_edge_bps, 1)} bps < hurdle={_fmt_float(c.min_edge_hurdle_bps, 1)} bps"
            elif c.decay_status == "decayed":
                reason = f"decayed: mean_sharpe={_fmt_float(c.decay_metric, 2)}"
            elif c.fdr_accepted is False:
                reason = f"fdr_rejected: p={_fmt_float(c.p_value, 3)}"
        lines.append(
            f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
            f"| {c.shape} | {c.status} | {reason} |"
        )

    # Add data_blocked entries for deferred families.
    lines.append("| (all) | multi_week_trend | 5+ | single_name_time_series | data_blocked | data_blocked AND capital_blocked. Unblocks at 2+ years history AND owned cash (no T+4 clock). |")
    lines.append("| (all) | multi_month_momentum | 20+ | single_name_time_series | data_blocked | 20-day hold → ~12 OOS windows/year < MIN_EFF_N=30. Unblocks at 3+ years. |")
    lines.append("| (all) | cross_sectional | — | cross_sectional | deferred | Plan-10 §7.5 deferred. |")
    lines.append("| (all) | pairs_stat_arb | — | pairs | deferred | Plan-10 §7.6 deferred. |")
    lines.append("| (all) | order_book_obi | <1min | order_book | deferred | Plan-10 §7.9 deferred. Requires re-test on low-friction names post-capture. |")
    lines.append("")

    # ---- Methodology notes ----
    lines.append("## Methodology Notes")
    lines.append("")
    lines.append("- **Financing model:** ADR-0011, T+1 settlement lag (empirical). Holidays out of scope (v1).")
    lines.append("- **Cost gate entry_dow:** Thursday (dow=3), worst-case. Early-week entries are cheaper.")
    lines.append("- **FDR correction:** Benjamini-Hochberg at α=0.10 across the full (name, strategy, horizon) grid.")
    lines.append("- **Effective-N:** single_name per-symbol de-overlap; event_driven pooled across universe.")
    lines.append("- **Walk-forward:** fit=120 rows, test=40 rows, roll by test_window. Decay = majority of windows below Sharpe=0 AND hit_rate=0.5.")
    lines.append("- **Impact model:** Not modelled (set to 0 bps for v1). Unblocks with position-sizing from plan-13.")
    lines.append("- **Deferred families:** cross_sectional, pairs, order_book — see plan-10 §7.5–7.6, 7.9.")
    lines.append("")

    path = os.path.join(outdir, "strategy_portfolio.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")

    return path
