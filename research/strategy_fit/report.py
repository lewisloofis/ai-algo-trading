"""
strategy_fit/report.py — Assemble and emit strategy_portfolio.md.

The report follows the three-state verdict convention from plans 04/05/06:
    PASS         — ≥1 BH-FDR-surviving cell with positive net edge
    INCONCLUSIVE — cells survive cost gate but none survive BH FDR correction
    NO-GO        — no cells survive the cost gate at all

Output is clearly labelled SYNTHETIC to prevent confusion with real results.

W2.1 blocking gates
--------------------
Two additional conditions are hard prerequisites for PASS, on top of the cost
gate and BH-FDR acceptance above — a gate that only annotates but cannot
change the verdict is a footnote, not a gate (plan-10 roadmap §6 W2.1):

1. **OOS power.**  A cell's oos_eff_n (when measured — see
   strategy_fit/intraday_walk_forward.py) must clear MIN_OOS_EFF_N, the hard
   not-zero floor.  Below it, the result is INCONCLUSIVE at best, never PASS.
   oos_eff_n is None (not >= 0) for shapes where no intraday OOS split exists
   at all (single_name_time_series, event_driven, cross_sectional, pairs) —
   the gate does not apply there; there is nothing measured to fail.
2. **Impact priced.**  A cell whose market impact was never priced
   (impact_priced=False — see strategy_fit/impact.py, intraday-only today)
   must be excluded from PASS, not passed with an assumed 0.0.  Do not read
   modelled_impact_bps alone to decide this — impact_priced is the explicit
   signal; a genuinely-priced 0.0 impact is not the same as never-priced.
"""

from __future__ import annotations

import datetime
import os
from typing import Sequence

from strategy_fit.harness import CellResult, SHAPE_INTRADAY_SAME_DAY
from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N

# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_NO_GO = "NO-GO"


def _clears_oos_power_gate(c: CellResult) -> bool:
    """
    True when the cell's OOS effective-N clears the hard floor (W2.1), or the
    measurement does not apply to this shape at all.

    oos_eff_n is None for shapes with no intraday OOS split (single_name_
    time_series, event_driven, cross_sectional, pairs) — the gate does not
    apply there; nothing was ever measured, so there is nothing to fail.
    oos_eff_n is a real (possibly zero) integer for intraday_same_day cells —
    those must clear MIN_OOS_EFF_N, the hard not-zero floor, to be eligible
    for PASS.  Below the floor → INCONCLUSIVE at best, never PASS.
    """
    if c.oos_eff_n is None:
        return True
    return c.oos_eff_n >= MIN_OOS_EFF_N


def _is_pass_eligible(c: CellResult) -> bool:
    """
    True iff *c* satisfies every hard prerequisite for contributing to a PASS
    verdict: cost gate, BH-FDR acceptance, positive net edge, impact priced
    (not assumed 0.0), and sufficient OOS power (see module docstring, W2.1).
    """
    return (
        c.passes_cost_gate is True
        and c.fdr_accepted is True
        and (c.net_edge_bps or 0) > 0
        and c.impact_priced is True
        and _clears_oos_power_gate(c)
    )


def compute_verdict(cells: Sequence[CellResult]) -> str:
    """
    Compute the three-state verdict over all cell results.

    PASS         : at least one cell is PASS-eligible — see _is_pass_eligible:
                   passes the cost gate, fdr_accepted=True, net_edge_bps > 0,
                   impact_priced=True, and clears the OOS power gate (W2.1).
    INCONCLUSIVE : at least one cell passes the cost gate but none are
                   PASS-eligible (FDR-rejected, impact never priced, or OOS
                   underpowered below the hard floor).
    NO-GO        : no cell passes the cost gate (all cost_gated, decayed, skipped, etc).
    """
    any_cost_gated_pass = any(
        c.passes_cost_gate is True
        for c in cells
        if c.status == "tested"
    )
    any_fdr_survivor = any(
        _is_pass_eligible(c)
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


_DOW_NAMES: dict[int, str] = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}


def _fmt_dow(v: int | None) -> str:
    """Format a day-of-week integer (0=Mon … 4=Fri) as a short name."""
    if v is None:
        return "—"
    return _DOW_NAMES.get(v, str(v))


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
    # W2.1: also impact_priced=True and OOS power (see _is_pass_eligible) — a
    # cell that is only excluded by one of these two additional gates must
    # still show up as a cost-gate passer below, not silently vanish.
    survivors = [
        c for c in cells
        if c.status == "tested" and _is_pass_eligible(c)
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
            "(min_edge + modelled_impact + financing at best-feasible entry day), "
            "the Benjamini-Hochberg FDR correction at α=0.10, have their market impact "
            "genuinely priced (impact_priced=True), and clear the OOS not-zero floor "
            "(MIN_OOS_EFF_N — W2.1; below-floor cells cannot reach PASS). "
            "Whether a cell that clears that floor is also UNDERPOWERED relative to the "
            "power-derived required-N is reported per cell (\"OOS Persistent\" column) "
            "for human review — see Methodology Notes."
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
        lines.append(
            "| Symbol | Strategy | Hold (days) | Shape | Hit Rate | Net Edge (bps) | Eff-N "
            "| Best Entry Day | Friction Hurdle (bps) | Impact (bps) "
            "| OOS Edge (bps) | OOS Eff-N | OOS Persistent | Decay Metric (Sharpe) |"
        )
        lines.append(
            "|--------|----------|-------------|-------|----------|---------------|-------"
            "|----------------|----------------------|-------------|"
            "----------------|-----------|----------------|----------------------|"
        )
        for c in sorted(survivors, key=lambda x: (x.symbol, x.family_name)):
            is_intraday = c.shape == SHAPE_INTRADAY_SAME_DAY
            # W2.1: read impact_priced, not shape — a genuinely-priced 0.0
            # impact must not look the same as "impact never priced" (the
            # latter can no longer reach this table at all, since it fails
            # _is_pass_eligible, but the explicit flag is the correct signal
            # regardless of that invariant).
            impact_str = _fmt_float(c.modelled_impact_bps, 1) if c.impact_priced else "not priced"
            oos_edge_str = _fmt_float(c.oos_edge_bps, 1) if is_intraday else "—"
            oos_n_str = str(c.oos_eff_n) if is_intraday and c.oos_eff_n is not None else "—"
            if is_intraday:
                if c.oos_persistent is True:
                    oos_persist_str = "YES ⚠ (UNDERPOWERED)" if c.oos_underpowered else "YES"
                elif c.oos_persistent is False:
                    oos_persist_str = "NO ⚠ (thin-N)"
                else:
                    oos_persist_str = "—"
            else:
                oos_persist_str = "n/a"
            lines.append(
                f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
                f"| {c.shape} | {_fmt_float(c.hit_rate, 3)} "
                f"| {_fmt_float(c.net_edge_bps, 1)} "
                f"| {c.eff_n or '—'} "
                f"| {_fmt_dow(c.best_entry_dow)} "
                f"| {_fmt_float(c.min_edge_hurdle_bps, 1)} "
                f"| {impact_str} "
                f"| {oos_edge_str} "
                f"| {oos_n_str} "
                f"| {oos_persist_str} "
                f"| {_fmt_float(c.decay_metric, 2)} |"
            )
    else:
        lines.append("*No cells survived the full gate and FDR correction.*")
    lines.append("")

    # ---- Cost-gate passers that are not PASS-eligible ----
    # W2.1: a cell can clear the cost gate and FDR and still be excluded from
    # PASS by the impact-priced or OOS-power gates — the Reason column makes
    # that explicit instead of leaving these cells looking like plain FDR
    # rejections.
    if cost_pass_cells and verdict != VERDICT_PASS:
        lines.append("### Cost-Gate Passers (not PASS-eligible)")
        lines.append("")
        lines.append(
            "| Symbol | Strategy | Hold (days) | Net Edge (bps) | p-value | BH Accepted | Reason |"
        )
        lines.append(
            "|--------|----------|-------------|----------------|---------|-------------|--------|"
        )
        for c in cost_pass_cells:
            if c.fdr_accepted is not True:
                reason = "fdr_rejected"
            elif c.impact_priced is not True:
                reason = "impact_not_priced (excluded pending plan-13 impact model for this shape)"
            elif not _clears_oos_power_gate(c):
                reason = c.oos_split_skip_reason or (
                    f"oos_underfloor: oos_eff_n={c.oos_eff_n} < MIN_OOS_EFF_N={MIN_OOS_EFF_N}"
                )
            elif (c.net_edge_bps or 0) <= 0:
                reason = "net_edge_not_positive"
            elif c.oos_split_skip_reason:
                # Clears the hard floor and every other gate, but the intraday
                # walk-forward split itself found a problem (e.g. OOS edge
                # non-positive) — surface it rather than discarding it.
                reason = f"oos_split_note: {c.oos_split_skip_reason}"
            else:
                reason = "—"
            lines.append(
                f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
                f"| {_fmt_float(c.net_edge_bps, 1)} "
                f"| {_fmt_float(c.p_value, 3)} "
                f"| {_fmt_bool(c.fdr_accepted)} "
                f"| {reason} |"
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
            elif c.impact_priced is not True:
                reason = "impact_not_priced (excluded from PASS pending plan-13 impact model for this shape)"
            elif not _clears_oos_power_gate(c):
                reason = c.oos_split_skip_reason or (
                    f"oos_underfloor: oos_eff_n={c.oos_eff_n} < MIN_OOS_EFF_N={MIN_OOS_EFF_N}"
                )
            elif c.oos_split_skip_reason:
                reason = f"oos_split_note: {c.oos_split_skip_reason}"
        lines.append(
            f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
            f"| {c.shape} | {c.status} | {reason} |"
        )

    # Add data_blocked entries for capital-blocked or deferred families.
    lines.append("| (all) | multi_week_trend | 5+ | single_name_time_series | data_blocked | data_blocked AND capital_blocked. Unblocks at 2+ years history AND owned cash (no T+4 clock). |")
    lines.append("| (all) | multi_month_momentum | 20+ | single_name_time_series | data_blocked | 20-day hold → ~12 OOS windows/year < MIN_EFF_N=30. Unblocks at 3+ years. |")
    lines.append("| (all) | order_book_obi | <1min | order_book | deferred | Plan-10 §7.9 deferred. Requires re-test on low-friction names post-capture. |")
    lines.append("")

    # ---- Methodology notes ----
    lines.append("## Methodology Notes")
    lines.append("")
    lines.append("- **Financing model:** ADR-0011, T+1 settlement lag (empirical). Holidays out of scope (v1).")
    lines.append("- **Cost gate entry_dow:** Best-feasible (cheapest) weekday chosen by `best_feasible_hurdle_bps`. The allocator controls entry timing — deployability is tested at the cheapest day, not worst-case Thursday.")
    lines.append("- **FDR correction:** Benjamini-Hochberg at α=0.10 across the full (name, strategy, horizon) grid.")
    lines.append("- **Effective-N:** single_name per-symbol de-overlap; event_driven pooled across universe.")
    lines.append("- **Walk-forward:** fit=120 rows, test=40 rows, roll by test_window. Decay = majority of windows below Sharpe=0 AND hit_rate=0.5.")
    lines.append(
        "- **Impact model (intraday):** Square-root Almgren law (round-trip): "
        "2 × SQRT_IMPACT_COEFF(1.0) × daily_vol_bps × sqrt(participation_rate), "
        "where participation = POSITION_NOTIONAL_IDR(30M IDR) / ADV. "
        "Charged at both entry and exit; one-tick spread already in min_edge_to_clear_bps (no double-count). "
        "Daily / event / cross-sectional / pairs cells never price impact (`impact_priced=False`) — "
        "plan-13 will extend the model to those shapes. "
        "**W2.1: impact_priced=True is a hard prerequisite for PASS** — a cell whose impact was "
        "never priced is excluded from Surviving Strategies, not passed with an assumed 0.0 "
        "(see Cost-Gate Passers / Coverage Table for the `impact_not_priced` reason). "
        "See strategy_fit/impact.py."
    )
    lines.append(
        "- **Intraday OOS persistence and power (W2.1):** Single 60/40 chronological IS/OOS split "
        "on trading dates. is_persistent=True iff OOS edge > 0 AND OOS eff_n ≥ MIN_OOS_EFF_N=10 — "
        "MIN_OOS_EFF_N is a hard, not-zero floor, not a power calculation. "
        "**Below this floor a cell is excluded from PASS** (INCONCLUSIVE at best), shown as "
        "`oos_underfloor` in Cost-Gate Passers / Coverage Table. "
        "Separately, `oos_required_eff_n` (lib.stats.required_effective_n: N ≥ (z·σ/edge)², z=2) "
        "asks whether oos_eff_n is enough to *trust* the specific observed edge given its "
        "per-trade dispersion — a cell can clear the hard floor and still be `oos_underpowered`, "
        "shown as YES ⚠ (UNDERPOWERED) in the survivor table. This is an expected, common, honest "
        "verdict given ~55 trading days of history, not evidence the pipeline is broken. "
        "See strategy_fit/intraday_walk_forward.py."
    )
    lines.append("- **Cross-sectional family:** daily Spearman IC between trailing-return rank and forward return; eff_n = independent cross-sectional periods. Cost floor: median min_edge_to_clear_bps (market-neutral L/S; see families_cross_sectional.py).")
    lines.append("- **Pairs family:** Engle-Granger cointegration on the top-15 most-liquid names; spread half-life estimated via AR(1); OOS block-bootstrap on non-overlapping spread observations. Cost floor: sum of both legs' min_edge (each leg is a separate order).")
    lines.append("- **Intraday same-day families:** ORB (30-min opening range), intraday momentum (morning-to-afternoon), VWAP mean-reversion. hold=0 → financing=0, hurdle = min_edge + impact. One obs per trading day (no de-overlap needed).")
    lines.append("- **Deferred families:** order_book — see plan-10 §7.9. (cross_sectional and pairs are now testable.)")
    lines.append("")

    path = os.path.join(outdir, "strategy_portfolio.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        f.write("\n")

    return path
