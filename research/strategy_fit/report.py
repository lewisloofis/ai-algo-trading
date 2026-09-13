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
3. **Signal beats an always-long null.**  A cell's edge_over_null_bps (gross
   edge minus the same de-overlapped/hold-matched/long-only population with
   the signal deleted) must clear min_edge_hurdle_bps — see the module
   docstring for _clears_null_gate.  A PTRO-shaped cell can pass gates 1 and
   2, pass the cost gate, and pass BH-FDR while its entire reported edge is
   asset drift (buy-and-hold beta) rather than signal: PTRO ran 182 → 5,400
   inside the evaluation window, so ANY long-biased rule on it looked
   brilliant.  Measured against the always-long null, the signal's own
   contribution was +45.1 bps against a 110.2 bps hurdle — it lost. A 47-name
   breadth test found the family's true mean edge was +0.1 bps (t=0.01).
"""

from __future__ import annotations

import datetime
import os
from typing import Sequence

from strategy_fit.harness import (
    SHAPE_EVENT_DRIVEN,
    SHAPE_INTRADAY_SAME_DAY,
    SHAPE_SINGLE_NAME,
    CellResult,
)
from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N

# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_NO_GO = "NO-GO"


def _clears_oos_power_gate(c: CellResult) -> bool:
    """
    True when the cell's OOS effective-N clears the hard floor (W2.1), or no
    OOS split applies to this shape at all.

    Three distinct states, and conflating the last two is a hole:

    1. **Split ran, produced an N.**  oos_eff_n is a real (possibly zero)
       integer — it must clear MIN_OOS_EFF_N to be PASS-eligible.  Below the
       floor → INCONCLUSIVE at best, never PASS.
    2. **Split was attempted and failed outright** (empty frame, a slice that
       raised, fewer than two rows).  oos_eff_n is None *and*
       oos_split_skip_reason explains why.  This must FAIL the gate: the cell
       was supposed to be validated out-of-sample and was not.  Returning
       True here would let precisely the unvalidated cells through, which is
       the failure mode the gate exists to prevent.
    3. **No split applies to this shape** (event_driven, cross_sectional,
       pairs — no OOS machinery exists for them).  Both fields are None; the
       gate does not apply, nothing was measured, so there is nothing to
       fail.  Those shapes are excluded from PASS by the impact_priced gate
       instead.

    As of plan 13 both per-symbol shapes run a split: intraday_same_day via
    intraday_walk_forward, single_name_time_series via daily_walk_forward.
    """
    if c.oos_eff_n is None:
        # State 2 vs state 3: a populated skip_reason means a split was
        # attempted for this cell and did not produce a usable OOS sample.
        return c.oos_split_skip_reason is None
    return c.oos_eff_n >= MIN_OOS_EFF_N


NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE: bool = True
"""
Governs how strict the always-long null gate (_clears_null_gate) is.

True (default): PASS requires edge_over_null_bps > min_edge_hurdle_bps — the
SIGNAL ALONE, net of whatever the underlying asset's drift alone would have
contributed, must pay for the full per-trade friction stack.

Rationale for the strict default: a strategy's gross return can include
asset drift the strategy did not forecast and — with no shorting available
to an IDX retail account (strategy_fit/long_only.py) — cannot hedge out.  A
strategy that is really just leaning on drift should be a buy-and-hold: pay
the friction hurdle ONCE, not on every one of N per-trade round trips.  A
per-trade strategy earns its keep only by producing per-trade signal value
in excess of what the drift alone already paid for — anything less is a
strictly worse trade than simply holding the name.

False: PASS requires only edge_over_null_bps > 0 (the existing
net_edge_bps > 0 gate — computed against the SAME hurdle, on gross edge, not
edge-over-null — still applies unconditionally; this flag relaxes only the
null gate's own bar).
"""

NULL_GATE_APPLICABLE_SHAPES: frozenset[str] = frozenset(
    {SHAPE_SINGLE_NAME, SHAPE_EVENT_DRIVEN, SHAPE_INTRADAY_SAME_DAY}
)
"""
Shapes for which an always-long null is computable and the gate below
applies.  cross_sectional and pairs are structurally long/short (see
strategy_fit/long_only.py: long_short_infeasible_result) — there is no
long-only null to compare a decile-spread or cointegrated-pair strategy
against, so those shapes always carry edge_over_null_bps=None and the gate
must SKIP them, not block them.  See _clears_null_gate for why a None on a
shape IN this set is the opposite case: a bug, not an exemption.
"""


def _clears_null_gate(c: CellResult) -> bool:
    """
    True when *c*'s signal contributes edge beyond an always-long null that
    ignores the signal entirely (same de-overlap, same hold, same long-only
    constraint, signal filter removed — see strategy_fit/harness.py's
    CellResult.null_edge_bps), or the gate does not apply to this shape.

    Mirrors _clears_oos_power_gate's None-handling, deliberately inverted:

    - Shape NOT in NULL_GATE_APPLICABLE_SHAPES (cross_sectional, pairs):
      no null is computable for a structurally long/short family — the gate
      does not apply, there is nothing to fail.  Returns True.
    - Shape IN NULL_GATE_APPLICABLE_SHAPES but edge_over_null_bps is None:
      this shape's family runner is EXPECTED to compute a null for every
      tested cell (see families_single_name.py, families_intraday.py,
      families_event.py).  A None here means the computation was never run
      or silently failed — a bug, not "not applicable" — and must BLOCK,
      never silently pass.  This is the inverse of _clears_oos_power_gate's
      state 3 (None means "no split applies"): there, None on a shape
      outside the gate's domain is fine; here, None on a shape INSIDE the
      gate's domain is not.
    - Otherwise: compare edge_over_null_bps against the configured bar (see
      NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE).
    """
    if c.shape not in NULL_GATE_APPLICABLE_SHAPES:
        return True
    if c.edge_over_null_bps is None:
        return False
    if NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE:
        return c.edge_over_null_bps > (c.min_edge_hurdle_bps or 0.0)
    return c.edge_over_null_bps > 0


def _null_gate_reason(c: CellResult) -> str:
    """Human-readable explanation for why *c* failed _clears_null_gate.

    Only meaningful to call when _clears_null_gate(c) is False and the shape
    is in NULL_GATE_APPLICABLE_SHAPES — callers are expected to check that
    first (see report table construction below).
    """
    if c.edge_over_null_bps is None:
        return (
            f"null_gate_unmeasured: edge_over_null_bps is None for an "
            f"applicable shape ({c.shape}) — this is a bug in the family "
            "runner, not a 'does not apply' case; treated as a block"
        )
    gross_str = _fmt_float(c.gross_edge_bps, 1)
    null_str = _fmt_float(c.null_edge_bps, 1)
    over_str = _fmt_float(c.edge_over_null_bps, 1)
    if NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE:
        hurdle_str = _fmt_float(c.min_edge_hurdle_bps, 1)
        return (
            f"loses_to_null: edge_over_null={over_str} bps <= hurdle={hurdle_str} bps "
            f"(gross={gross_str}, null={null_str} — signal does not pay for its own "
            "friction; the reported edge is asset drift, not signal)"
        )
    return (
        f"loses_to_null: edge_over_null={over_str} bps <= 0 "
        f"(gross={gross_str}, null={null_str})"
    )


def _is_pass_eligible(c: CellResult) -> bool:
    """
    True iff *c* satisfies every hard prerequisite for contributing to a PASS
    verdict: cost gate, BH-FDR acceptance, positive net edge, impact priced
    (not assumed 0.0), sufficient OOS power, a NON-FALSE out-of-sample
    persistence verdict (see module docstring, W2.1 and plan 13), and —
    where an always-long null is computable for this shape — a signal that
    clears that null (see _clears_null_gate and the module docstring gate 3).

    On ``oos_persistent is not False`` — the last condition, added in plan 13
    after it fired on real data.  ``_clears_oos_power_gate`` only asks whether
    the OOS sample is BIG ENOUGH; it never asks what the OOS sample SAID.  A
    cell could therefore clear the floor with 50-60 observations, report a
    measured NEGATIVE out-of-sample edge, and still be counted a survivor —
    which is precisely the in-sample-only edge that killed the intraday ORB
    family, reproduced by the very gate built to catch it.  Three of the six
    survivors in the first plan-13 live run were exactly this (BREN -87.9,
    RATU -53.9, WIFI -21.9 bps OOS).

    ``is not False`` rather than ``is True`` is deliberate: None means no OOS
    split applies to this shape (event_driven and the long_short_infeasible
    shapes), which the impact_priced gate already excludes and which this gate
    must not double-judge.  False means a split ran and the edge did not
    survive it — that blocks.
    """
    return (
        c.passes_cost_gate is True
        and c.fdr_accepted is True
        and (c.net_edge_bps or 0) > 0
        and c.impact_priced is True
        and _clears_oos_power_gate(c)
        and c.oos_persistent is not False
        and _clears_null_gate(c)
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
        "10 bps/cal-day financing (ADR-0014 settlement clock), "
        "≤2 trading-day horizon ceiling."
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
            "genuinely priced (impact_priced=True), clear the OOS not-zero floor "
            "(MIN_OOS_EFF_N — W2.1; below-floor cells cannot reach PASS), and — where an "
            "always-long null is computable for their shape — beat that null by more than "
            "min_edge_hurdle_bps (the signal, not asset drift, pays for its own friction; "
            "see Methodology Notes). "
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
            "| Null Edge (bps) | Edge over Null (bps) "
            "| OOS Edge (bps) | OOS Eff-N | OOS Persistent | Decay Metric (Sharpe) |"
        )
        lines.append(
            "|--------|----------|-------------|-------|----------|---------------|-------"
            "|----------------|----------------------|-------------|"
            "-----------------|------------------------|"
            "----------------|-----------|----------------|----------------------|"
        )
        for c in sorted(survivors, key=lambda x: (x.symbol, x.family_name)):
            # W2.1: read impact_priced, not shape — a genuinely-priced 0.0
            # impact must not look the same as "impact never priced" (the
            # latter can no longer reach this table at all, since it fails
            # _is_pass_eligible, but the explicit flag is the correct signal
            # regardless of that invariant).
            impact_str = _fmt_float(c.modelled_impact_bps, 1) if c.impact_priced else "not priced"
            # plan 13: BOTH per-symbol shapes now run an OOS split (intraday via
            # intraday_walk_forward, daily via daily_walk_forward), so the OOS
            # columns can no longer be keyed on shape == intraday.  Key them on
            # whether a split was actually run for this cell: an attempted split
            # populates oos_eff_n, or oos_split_skip_reason when it failed.
            # Shapes with no split machinery at all (event_driven, and the
            # long_short_infeasible cross_sectional / pairs) still read "n/a".
            has_oos = c.oos_eff_n is not None or c.oos_split_skip_reason is not None
            oos_edge_str = _fmt_float(c.oos_edge_bps, 1) if has_oos else "—"
            oos_n_str = str(c.oos_eff_n) if c.oos_eff_n is not None else "—"
            if has_oos:
                if c.oos_persistent is True:
                    oos_persist_str = "YES ⚠ (UNDERPOWERED)" if c.oos_underpowered else "YES"
                elif c.oos_persistent is False:
                    # Say WHY it failed.  A blanket "thin-N" is wrong and was
                    # actively misleading on the first plan-13 live run, where
                    # cells with 49-60 OOS observations failed on a measured
                    # NEGATIVE out-of-sample edge, not on sample size.
                    if c.oos_edge_bps is not None and c.oos_edge_bps <= 0:
                        oos_persist_str = f"NO ⚠ (OOS edge {c.oos_edge_bps:+.1f})"
                    elif c.oos_eff_n is not None and c.oos_eff_n < MIN_OOS_EFF_N:
                        oos_persist_str = f"NO ⚠ (thin-N: {c.oos_eff_n})"
                    else:
                        oos_persist_str = "NO ⚠"
                else:
                    oos_persist_str = "—"
            else:
                oos_persist_str = "n/a"
            # Null-edge gate (plan): only in NULL_GATE_APPLICABLE_SHAPES has a
            # null been computed at all; other shapes (cross_sectional,
            # pairs) read "n/a" rather than "—" (which is reserved for "not
            # yet reported" elsewhere) so a reader can tell the two apart.
            null_str = (
                _fmt_float(c.null_edge_bps, 1)
                if c.shape in NULL_GATE_APPLICABLE_SHAPES
                else "n/a"
            )
            edge_over_null_str = (
                _fmt_float(c.edge_over_null_bps, 1)
                if c.shape in NULL_GATE_APPLICABLE_SHAPES
                else "n/a"
            )
            lines.append(
                f"| {c.symbol} | {c.family_name} | {c.horizon_trading_days} "
                f"| {c.shape} | {_fmt_float(c.hit_rate, 3)} "
                f"| {_fmt_float(c.net_edge_bps, 1)} "
                f"| {c.eff_n or '—'} "
                f"| {_fmt_dow(c.best_entry_dow)} "
                f"| {_fmt_float(c.min_edge_hurdle_bps, 1)} "
                f"| {impact_str} "
                f"| {null_str} "
                f"| {edge_over_null_str} "
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
            elif not _clears_null_gate(c):
                # Clears cost gate, FDR, impact-priced, and OOS power — but
                # the signal itself does not beat an always-long null.  Say
                # so explicitly (PTRO-shaped): a reader must be able to see
                # "this lost to buy-and-hold" without recomputing anything.
                reason = _null_gate_reason(c)
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
                # net_edge_bps is ALREADY gross − hurdle, so the gate test is
                # "> 0", not "> hurdle".  Phrase it that way: the old wording
                # ("net_edge=X < hurdle=Y") invited reading X as a gross edge
                # and comparing it against Y a second time.
                reason = (
                    f"cost_gated: net_after_cost={_fmt_float(c.net_edge_bps, 1)} bps ≤ 0 "
                    f"(gross − hurdle, where hurdle={_fmt_float(c.min_edge_hurdle_bps, 1)} bps)"
                )
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
            elif not _clears_null_gate(c):
                reason = _null_gate_reason(c)
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
    lines.append("- **Financing model:** ADR-0011 rate (10 bps/calendar-day) on the ADR-0014 settlement clock: charged over `[settle(entry), settle(exit)]` with both legs at T+2, so the cost is 10 bps per trading day held except where the settlement window straddles a weekend (Wed entry on a 1-day hold: 30 bps). Holiday-aware when an entry date is supplied; the weekday sweep below assumes a holiday-free week.")
    lines.append("- **Cost gate entry_dow:** Best-feasible (cheapest) weekday chosen by `best_feasible_hurdle_bps`. The allocator controls entry timing — deployability is tested at the cheapest day, not the worst case (which is Wednesday for a 1-day hold — see ADR-0014, not Thursday as ADR-0011 stated).")
    lines.append("- **FDR correction:** Benjamini-Hochberg at α=0.10 across the full (name, strategy, horizon) grid.")
    lines.append("- **Effective-N:** single_name per-symbol de-overlap; event_driven pooled across universe.")
    lines.append("- **Walk-forward:** fit=120 rows, test=40 rows, roll by test_window. Decay = majority of windows below Sharpe=0 AND hit_rate=0.5.")
    lines.append(
        "- **Impact model (intraday + daily single-name, plan 13):** Square-root Almgren law (round-trip): "
        "2 × SQRT_IMPACT_COEFF(1.0) × daily_vol_bps × sqrt(participation_rate), "
        "where participation = POSITION_NOTIONAL_IDR(30M IDR) / ADV. "
        "Charged at both entry and exit; one-tick spread already in min_edge_to_clear_bps (no double-count). "
        "Daily single-name cells are priced from daily bars (plan 13), using the SAME formula: the "
        "square-root law prices ONE execution and the 2x factor is the entry leg plus the exit leg, so "
        "a 2-day hold does not double impact — only how daily_vol_bps and ADV are ESTIMATED differs. "
        "Event / cross-sectional / pairs cells still never price impact (`impact_priced=False`). "
        "**Coefficients are BORROWED for daily cells:** impact_coeffs.json was calibrated (plan 15) from "
        "tick fills on INTRADAY executions for 7 large-caps; names outside that sample fall through to "
        "global_default=1.0, the conservative high-friction end. See impact.DAILY_COEFF_IS_BORROWED_NOTE. "
        "**W2.1: impact_priced=True is a hard prerequisite for PASS** — a cell whose impact was "
        "never priced is excluded from Surviving Strategies, not passed with an assumed 0.0 "
        "(see Cost-Gate Passers / Coverage Table for the `impact_not_priced` reason). "
        "See strategy_fit/impact.py."
    )
    lines.append(
        "- **OOS persistence and power (W2.1; extended to daily cells in plan 13):** Single 60/40 "
        "chronological IS/OOS split — on trading dates for intraday cells, on raw daily bars (split "
        "BEFORE the signal is computed, so no lookback or forward-return window can straddle the "
        "boundary) for daily single-name cells. is_persistent=True iff OOS edge > 0 AND OOS eff_n ≥ "
        "MIN_OOS_EFF_N=10 — MIN_OOS_EFF_N is a hard, not-zero floor, not a power calculation. "
        "**A False persistence verdict is now BLOCKING for PASS (plan 13).** Previously the floor was "
        "checked but the verdict itself was only annotated, so a cell with 50+ OOS observations and a "
        "measured NEGATIVE out-of-sample edge could still be reported as a survivor — the exact "
        "in-sample-only failure that killed the intraday ORB family. "
        "**Below this floor a cell is excluded from PASS** (INCONCLUSIVE at best), shown as "
        "`oos_underfloor` in Cost-Gate Passers / Coverage Table. "
        "Separately, `oos_required_eff_n` (lib.stats.required_effective_n: N ≥ (z·σ/edge)², z=2) "
        "asks whether oos_eff_n is enough to *trust* the specific observed edge given its "
        "per-trade dispersion — a cell can clear the hard floor and still be `oos_underpowered`, "
        "shown as YES ⚠ (UNDERPOWERED) in the survivor table. This is an expected, common, honest "
        "verdict given ~55 trading days of history, not evidence the pipeline is broken. "
        "See strategy_fit/intraday_walk_forward.py."
    )
    lines.append(
        "- **Always-long null gate:** for single_name_time_series, event_driven, and "
        "intraday_same_day cells, the family runner ALSO scores the same de-overlapped, "
        "long-only, hold-matched population with the signal deleted (`null_edge_bps` — "
        "buy every slot, direction ignored). `edge_over_null_bps = gross_edge_bps − "
        "null_edge_bps` is the edge attributable to the signal itself, net of whatever "
        "the underlying asset's drift alone contributed. **A cell whose signal does not "
        "clear `min_edge_hurdle_bps` on edge_over_null_bps is excluded from PASS**, shown "
        "as `loses_to_null` in Cost-Gate Passers / Coverage Table — even when its "
        "gross/net edge, cost gate, FDR, impact, and OOS persistence all pass. This gate "
        "exists because a strongly-trending name (e.g. PTRO, up ~30x in-sample) makes any "
        "long-biased rule look brilliant on gross edge alone: measured against its own "
        "always-long null, PTRO's signal contributed +45.1 bps against a 110.2 bps hurdle "
        "— it lost to buy-and-hold. cross_sectional and pairs cells have no long-only null "
        "(structurally long/short — long_short_infeasible) and are exempt; a cell IN scope "
        "with `edge_over_null_bps=None` is treated as a measurement failure and blocked, "
        "never silently passed. See strategy_fit/report.py:_clears_null_gate and "
        "NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE."
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
