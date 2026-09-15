"""
strategy_fit/impact_sweep.py — Impact sensitivity sweep for intraday cells.

Purpose
-------
The square-root impact model has two free parameters: the Almgren coefficient
(SQRT_IMPACT_COEFF) and the per-name position notional (POSITION_NOTIONAL_IDR).
Both are v1 assumptions; the notional in particular is now derived from the
sleeve's MEASURED equity rather than assumed (ADR-0017, see
strategy_fit/sleeve_sizing.py) — but "measured equity" still leaves the
concurrent-name count an open portfolio-construction question, so the
coefficient AND the position size both remain worth sweeping.  This module
sweeps a small grid of (coeff, position_notional) values to show how
sensitive the intraday survivor count and net edges are to those assumptions.

Analytical scaling
------------------
For any intraday cell, the impact it actually reported was computed as:

    impact_base = 2 × coeff_priced_at × daily_vol_bps × sqrt(notional_priced_at / adv)

where ``coeff_priced_at`` and ``notional_priced_at`` are whatever the cell
was ACTUALLY priced with — recorded on the cell itself as
``CellResult.impact_priced_coeff`` / ``impact_priced_position_notional_idr``
(ADR-0017; see the harness.py docstring for why).  At a new (coeff', size'):

    impact' = 2 × coeff' × daily_vol_bps × sqrt(size' / adv)
            = impact_base × (coeff' / coeff_priced_at) × sqrt(size' / notional_priced_at)

This is EXACT for the sqrt model — daily_vol_bps and adv are fixed per symbol
and already embedded in impact_base.  No API calls or DataFrame re-reads are
needed.  The sweep therefore runs purely from the CellResult objects produced
by one normal grid run.

.. important::
    **Fixed 2026-09-14 (ADR-0017).**  Before this date, ``scale_impact_bps``
    read ``strategy_fit.impact.SQRT_IMPACT_COEFF`` /
    ``strategy_fit.impact.POSITION_NOTIONAL_IDR`` — the CURRENT MODULE
    CONSTANTS — and treated them as "the point this cell was priced at".
    That is only true when the constants have not changed since the cell was
    priced.  It was an implicit coupling that happened to hold while sizing
    was a hardcoded literal, and broke silently the moment sizing became
    configurable (which is exactly what ADR-0017 makes it).  The fix: the
    priced-at coefficient and notional now travel WITH the cell
    (``CellResult.impact_priced_coeff`` / ``impact_priced_position_notional_idr``),
    and every rescale reads THOSE, never a module global.  A cell that
    predates these fields (both None) is NOT silently assumed to have been
    priced at today's constants — see ``recompute_cell_at_impact`` and the
    "Cells Not Rescaled" section of the emitted report.

Grid
----
SWEEP_COEFFS : {0.5, 1.0}    (conservative vs half-coefficient; unchanged)
SWEEP_SIZES  : the MEASURED sleeve (ADR-0017) at N ∈ {10, 5, 3, 1} concurrent
               names — {1.5M, 3M, 5M, 15M} IDR — plus 30M retained and
               labelled as the SUPERSEDED ADR-0011 reference point, so this
               run's numbers stay legible against every historical
               strategy_portfolio.md / impact_sweep.md.
10 grid points total: 2 × 5.  See strategy_fit/sleeve_sizing.py for the
presets these sizes are drawn from, and for why the concurrent-name count
itself is an open question this sweep exists to make visible, not assume.

Scope
-----
Every cell that actually PRICED impact is rescaled: intraday (hold=0,
shape=intraday_same_day) cells and — since plan 13 — daily single-name
cells, which are priced from daily bars by the same square-root law.  Only
cells that never price impact at all (event / cross-sectional / pairs,
modelled_impact_bps=None) pass through unchanged.

This is load-bearing for reading the summary table: the per-cell matrix
below shows INTRADAY cells only, while the Survivors column counts every
shape.  The two can disagree completely — a run in which every intraday
cell is gated can still report dozens of survivors, all of them daily.

Survivors at each grid point
-----------------------------
Survivors' = status=="tested" AND cost_pass' AND fdr_accepted AND net'>0.

FDR is re-run at each grid point by calling the existing `apply_fdr_to_grid`
from run_strategy_fit.py — no BH reimplementation.

Always-long null gate is NOT re-evaluated here
-----------------------------------------------
ADR-0017's Consequences section is explicit that "the always-long null gate
gets harder to clear, not easier": null_edge_bps is a property of the
underlying asset's drift, not of our order size, so cheaper impact can revive
a cell's cost-gate pass without that cell having earned anything against the
null.  This sweep recomputes cost-gate / FDR status only; a re-entry here is
NOT a claim that the cell would also clear the null gate — a reader must
check that separately (see strategy_fit/report.py's null-gate columns on the
base run, and re-run it against a recomputed cell if the null gate itself
needs re-checking at a new size — it does not depend on position notional,
so the base run's null-gate verdict for a given cell is unaffected by this
sweep and can be read directly off the base CellResult).
"""

from __future__ import annotations

import dataclasses
import math
import os
from dataclasses import dataclass
from typing import Sequence

from strategy_fit.harness import SHAPE_INTRADAY_SAME_DAY, CellResult
from strategy_fit.impact import POSITION_NOTIONAL_IDR, SQRT_IMPACT_COEFF
from strategy_fit.sleeve_sizing import (
    ACTIVE_SLEEVE_SIZING,
    LEGACY_ADR0011,
    SLEEVE_N1,
    SLEEVE_N3,
    SLEEVE_N5,
    SLEEVE_N10,
)

# ---------------------------------------------------------------------------
# Sweep grid constants
# ---------------------------------------------------------------------------

SWEEP_COEFFS: tuple[float, ...] = (0.5, 1.0)
"""
Two coefficient values to sweep:
  0.5 — lower end of the Almgren empirical range (more optimistic)
  1.0 — SQRT_IMPACT_COEFF base value (conservative, plan-10 assumption)
"""

SWEEP_SIZES: tuple[float, ...] = (
    SLEEVE_N10.per_name_notional_idr,       # 1,500,000 IDR  (N=10)
    SLEEVE_N5.per_name_notional_idr,        # 3,000,000 IDR  (N=5, ACTIVE_SLEEVE_SIZING)
    SLEEVE_N3.per_name_notional_idr,        # 5,000,000 IDR  (N=3)
    SLEEVE_N1.per_name_notional_idr,        # 15,000,000 IDR (N=1, whole measured sleeve)
    LEGACY_ADR0011.per_name_notional_idr,   # 30,000,000 IDR — VOID, retained as reference
)
"""
Five position-notional values to sweep, rebased to the MEASURED sleeve
(ADR-0017) instead of multiples of a void constant:

  1,500,000 IDR  — N=10 concurrent names
  3,000,000 IDR  — N=5  concurrent names (ACTIVE_SLEEVE_SIZING default)
  5,000,000 IDR  — N=3  concurrent names
  15,000,000 IDR — N=1  concurrent name  (the WHOLE measured 15M sleeve)
  30,000,000 IDR — ADR-0011's SUPERSEDED reference point (2x the entire
                   sleeve).  Retained ONLY so this run's numbers stay legible
                   against every historical strategy_portfolio.md /
                   impact_sweep.md run, which were priced against it — not a
                   candidate sizing.  See strategy_fit/sleeve_sizing.py.

CHANGED 2026-09-14 (ADR-0017): previously {15M, 30M, 60M} — half/base/double
of the void POSITION_NOTIONAL_IDR=30M constant.  That framing stopped making
sense once 30M was shown to be twice the sleeve's entire measured equity: at
that base, "half" (15M) was already the whole account concentrated in one
name, and "double" (60M) was unfundable by construction.  The new grid
sweeps sizes that correspond to an actual number of concurrent positions in
the real 15M account instead.
"""

SWEEP_SIZE_LABELS: dict[float, str] = {
    SLEEVE_N10.per_name_notional_idr: "N=10",
    SLEEVE_N5.per_name_notional_idr: "N=5 (active)",
    SLEEVE_N3.per_name_notional_idr: "N=3",
    SLEEVE_N1.per_name_notional_idr: "N=1 (whole sleeve)",
    LEGACY_ADR0011.per_name_notional_idr: "ADR-0011 (superseded)",
}
"""Concurrent-name-count label for each swept size — the decision-relevant
axis for a reader (ADR-0017 asks for sizes to be read as portfolio
construction choices, not bare IDR figures)."""


def _size_label(position_notional_idr: float) -> str:
    """Concurrent-name label for a swept size; falls back to the raw IDR figure for an unlisted size."""
    for size, label in SWEEP_SIZE_LABELS.items():
        if abs(size - position_notional_idr) < 1.0:
            return label
    return f"{position_notional_idr:,.0f} IDR"


def _fmt_size_m(position_notional_idr: float) -> str:
    """Format an IDR notional in millions, e.g. 1_500_000 -> '1.5M', 30_000_000 -> '30M'."""
    return f"{position_notional_idr / 1e6:g}M"


# ---------------------------------------------------------------------------
# Scaling formula
# ---------------------------------------------------------------------------

def scale_impact_bps(
    impact_base_bps: float,
    base_coeff: float,
    base_position_notional_idr: float,
    coeff: float,
    position_notional_idr: float,
) -> float:
    """
    Rescale a base impact to a new (coeff, position_notional) point.

    Formula (exact for the sqrt model, no re-fetch needed):

        impact' = impact_base × (coeff / base_coeff) × sqrt(size' / base_position_notional_idr)

    ``base_coeff`` / ``base_position_notional_idr`` are NOT read from a
    module global — they must be the values the cell was ACTUALLY priced at
    (``CellResult.impact_priced_coeff`` / ``impact_priced_position_notional_idr``,
    ADR-0017).  ``recompute_cell_at_impact`` reads them off the cell itself
    before calling this function.  See the module docstring's "important"
    note for why this is a fix, not a style choice: reading a module
    constant here was only correct when that constant hadn't changed since
    the cell was priced.

    Parameters
    ----------
    impact_base_bps : float
        The cell's modelled_impact_bps at its own priced-at point.
    base_coeff : float
        The Almgren coefficient the cell was actually priced with.
    base_position_notional_idr : float
        The per-name notional (IDR) the cell was actually priced at.
    coeff : float
        New Almgren coefficient (e.g. 0.5 or 1.0).
    position_notional_idr : float
        New per-name position notional in IDR.

    Returns
    -------
    float
        Rescaled round-trip impact in basis points.
    """
    coeff_ratio = coeff / base_coeff
    size_ratio = position_notional_idr / base_position_notional_idr
    return impact_base_bps * coeff_ratio * math.sqrt(size_ratio)


# ---------------------------------------------------------------------------
# Cell recompute
# ---------------------------------------------------------------------------

def _missing_impact_provenance(cell: CellResult) -> bool:
    """
    True iff *cell* is an intraday, tested, impact-priced cell that SHOULD be
    rescaled at a swept grid point but carries no record of what it was
    actually priced at (``impact_priced_coeff`` / ``impact_priced_position_notional_idr``
    both None) — i.e. it predates ADR-0017's provenance fields.
    """
    return (
        cell.shape == SHAPE_INTRADAY_SAME_DAY
        and cell.status == "tested"
        and cell.modelled_impact_bps is not None
        and cell.min_edge_hurdle_bps is not None
        and (cell.impact_priced_coeff is None or cell.impact_priced_position_notional_idr is None)
    )


def recompute_cell_at_impact(
    cell: CellResult,
    coeff: float,
    position_notional_idr: float,
) -> CellResult:
    """
    Return a new CellResult with impact, hurdle, net_edge, and cost_pass
    recomputed at (coeff, position_notional_idr).

    Non-intraday cells (modelled_impact_bps is None → impact=0) are returned
    unchanged (same values, new instance via dataclasses.replace).

    The structural friction floor is extracted as:
        floor = cell.min_edge_hurdle_bps - cell.modelled_impact_bps

    For intraday hold=0 cells, financing=0, so the full hurdle is floor + impact.
    This extraction is valid for any intraday cell where the hurdle was set by
    best_feasible_hurdle_bps(..., hold_trading_days=0).

    Parameters
    ----------
    cell : CellResult
        Original cell result from the base grid run.
    coeff : float
        New Almgren coefficient for the swept point.
    position_notional_idr : float
        New position notional in IDR for the swept point.

    Returns
    -------
    CellResult
        New frozen CellResult with updated modelled_impact_bps, min_edge_hurdle_bps,
        net_edge_bps, and passes_cost_gate.  All other fields are preserved.
    """
    # Non-intraday cells have no impact model → return unchanged (new instance).
    if cell.modelled_impact_bps is None:
        return dataclasses.replace(cell)

    # A cell that was never cost-gated has no hurdle to re-derive a floor from.
    # This is reachable and is NOT the same condition as the check above:
    # _run_intraday_cell's early return for status != "tested" sets
    # modelled_impact_bps (a real float, impact is computed before the family
    # runs) but leaves min_edge_hurdle_bps None, because a cell that was never
    # scored was never cost-gated. The subtraction below would then raise
    # TypeError: unsupported operand type(s) for -: 'NoneType' and 'float'.
    # Sweeping is meaningless for such a cell anyway — there is no gross edge to
    # re-test against a new hurdle — so return it unchanged, exactly as for a
    # non-intraday cell.
    if cell.min_edge_hurdle_bps is None:
        return dataclasses.replace(cell)

    # ADR-0017: the priced-at base must come from the cell itself, never a
    # module global (see the module docstring's "important" note — this is
    # the fix for a real provenance defect, not defensive style).  A cell
    # computed before impact_priced_coeff / impact_priced_position_notional_idr
    # existed carries None here.  Refuse to rescale it rather than silently
    # assume it was priced at today's constants: the caller (sweep_grid)
    # surfaces these cells explicitly as "not rescaled" instead of mixing an
    # unknown-base number into a grid point's survivor count.
    if cell.impact_priced_coeff is None or cell.impact_priced_position_notional_idr is None:
        return dataclasses.replace(cell)

    # Rescale impact analytically, relative to what this cell was ACTUALLY priced at.
    new_impact = scale_impact_bps(
        impact_base_bps=cell.modelled_impact_bps,
        base_coeff=cell.impact_priced_coeff,
        base_position_notional_idr=cell.impact_priced_position_notional_idr,
        coeff=coeff,
        position_notional_idr=position_notional_idr,
    )

    # Reconstruct floor from the stored hurdle and base impact.
    floor = cell.min_edge_hurdle_bps - cell.modelled_impact_bps

    new_hurdle = floor + new_impact
    gross = cell.gross_edge_bps or 0.0
    new_net = gross - new_hurdle
    new_cost_pass = gross > new_hurdle

    return dataclasses.replace(
        cell,
        modelled_impact_bps=new_impact,
        min_edge_hurdle_bps=new_hurdle,
        net_edge_bps=new_net,
        passes_cost_gate=new_cost_pass,
        # Clear FDR acceptance — it will be recomputed by apply_fdr_to_grid.
        fdr_accepted=None,
    )


# ---------------------------------------------------------------------------
# Grid-point result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GridPointResult:
    """
    Survivor summary at one (coeff, position_notional_idr) grid point.

    Attributes
    ----------
    coeff : float
        Almgren coefficient at this grid point.
    position_notional_idr : float
        Per-name position notional in IDR at this grid point.
    survivor_count : int
        Number of cells that are status=="tested", pass the cost gate, pass
        FDR, and have net_edge_bps > 0.
    intraday_survivors : list[str]
        Short descriptors for each intraday survivor: "SYMBOL:family(+NNbps)".
    all_survivors : list[CellResult]
        Full CellResult objects for all survivors (intraday + daily + other shapes).
    reentry_cells : list[str]
        Intraday cells that were cost-gated at the base point (their own
        priced-at point — see CellResult.impact_priced_coeff /
        impact_priced_position_notional_idr) but pass at this grid point.
    dropped_cells : list[str]
        Intraday cells that passed the cost gate at the base point but fail here.
    unscaled_cells : list[str]
        Intraday, tested, impact-priced cells that predate ADR-0017's
        provenance fields (impact_priced_coeff / impact_priced_position_notional_idr
        both None) and were therefore NOT rescaled at this grid point — they
        appear at every grid point with their ORIGINAL impact/hurdle/net
        values, not a value computed against an assumed base. Same list at
        every grid point (it is a property of the input cells, not of the
        swept point). Empty for a run where every cell carries provenance.
    """

    coeff: float
    position_notional_idr: float
    survivor_count: int
    intraday_survivors: list[str]
    all_survivors: list[CellResult]
    reentry_cells: list[str]
    dropped_cells: list[str]
    unscaled_cells: list[str]


# ---------------------------------------------------------------------------
# Main sweep function
# ---------------------------------------------------------------------------

def sweep_grid(
    cells: Sequence[CellResult],
    sweep_coeffs: Sequence[float] = SWEEP_COEFFS,
    sweep_sizes: Sequence[float] = SWEEP_SIZES,
) -> list[GridPointResult]:
    """
    Sweep the grid of (coeff, position_notional) values and compute survivors.

    For each grid point:
    1. Recompute each intraday cell's impact/hurdle/net at the new (coeff, size),
       relative to what it was ACTUALLY priced at (ADR-0017).  Non-intraday
       cells pass through unchanged, as do cells missing priced-at provenance.
    2. Re-run BH-FDR via apply_fdr_to_grid (same function as production — ensures
       the FDR pool is identical to the production pipeline).
    3. Collect survivors and detect re-entries / drops vs. each cell's own
       base-point ``passes_cost_gate`` (recorded on the cell already — this
       does not assume a single shared base point across cells).

    Parameters
    ----------
    cells : Sequence[CellResult]
        Cell results from the base grid run (fdr_accepted may be set or None).
    sweep_coeffs : Sequence[float]
        Coefficients to sweep (default: SWEEP_COEFFS).
    sweep_sizes : Sequence[float]
        Position notionals to sweep (default: SWEEP_SIZES).

    Returns
    -------
    list[GridPointResult]
        One GridPointResult per grid point, in (coeff, size) iteration order.
    """
    from strategy_fit.run_strategy_fit import apply_fdr_to_grid  # avoid circular at module level

    # Determine base-point cost-gate status for re-entry / drop detection.
    # Each cell's OWN recorded passes_cost_gate IS its base-point status —
    # whatever (coeff, notional) it was actually priced at when the base
    # grid ran — so this needs no module-global "base point" of its own.
    _base_pass: dict[tuple[str, str], bool] = {
        (c.symbol, c.family_name): bool(c.passes_cost_gate)
        for c in cells
        if c.shape == SHAPE_INTRADAY_SAME_DAY and c.status == "tested"
    }

    # Cells that should be rescaled but carry no priced-at provenance
    # (ADR-0017) — same list at every grid point; computed once.
    unscaled_descs: list[str] = sorted(
        f"{c.symbol}:{c.family_name}" for c in cells if _missing_impact_provenance(c)
    )

    results: list[GridPointResult] = []

    for coeff in sweep_coeffs:
        for size in sweep_sizes:
            # 1. Recompute all cells at this grid point.
            recomputed = [recompute_cell_at_impact(c, coeff=coeff, position_notional_idr=size) for c in cells]

            # 2. Re-run FDR on the recomputed list.
            recomputed_fdr = apply_fdr_to_grid(recomputed)

            # 3. Collect survivors.
            survivors = [
                c for c in recomputed_fdr
                if (
                    c.status == "tested"
                    and c.passes_cost_gate is True
                    and c.fdr_accepted is True
                    and (c.net_edge_bps or 0.0) > 0
                )
            ]

            intraday_survivor_descs = [
                f"{c.symbol}:{c.family_name}(+{c.net_edge_bps:.0f}bps)"
                for c in survivors
                if c.shape == SHAPE_INTRADAY_SAME_DAY
            ]

            # 4. Re-entry: was gated at base, passes here.
            reentry_descs: list[str] = []
            dropped_descs: list[str] = []
            for c in recomputed_fdr:
                if c.shape != SHAPE_INTRADAY_SAME_DAY or c.status != "tested":
                    continue
                key = (c.symbol, c.family_name)
                was_passing = _base_pass.get(key, False)
                now_passing = bool(c.passes_cost_gate)
                desc = f"{c.symbol}:{c.family_name}"
                if not was_passing and now_passing:
                    reentry_descs.append(desc)
                if was_passing and not now_passing:
                    dropped_descs.append(desc)

            results.append(
                GridPointResult(
                    coeff=coeff,
                    position_notional_idr=size,
                    survivor_count=len(survivors),
                    intraday_survivors=intraday_survivor_descs,
                    all_survivors=survivors,
                    reentry_cells=reentry_descs,
                    dropped_cells=dropped_descs,
                    unscaled_cells=unscaled_descs,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Net-bps matrix helpers
# ---------------------------------------------------------------------------

def _net_bps_matrix(
    cells: Sequence[CellResult],
    grid: Sequence[GridPointResult],
    sweep_coeffs: Sequence[float] = SWEEP_COEFFS,
    sweep_sizes: Sequence[float] = SWEEP_SIZES,
) -> dict[tuple[str, str], dict[tuple[float, float], float | None]]:
    """
    Build a per-cell matrix of net_edge_bps at each grid point.

    Returns a dict keyed by (symbol, family_name) → {(coeff, size): net_bps}.
    Only intraday cells with status=="tested" are included.
    """
    intraday_keys: list[tuple[str, str]] = [
        (c.symbol, c.family_name)
        for c in cells
        if c.shape == SHAPE_INTRADAY_SAME_DAY and c.status == "tested"
    ]
    # Deduplicate while preserving order
    seen: set[tuple[str, str]] = set()
    unique_keys: list[tuple[str, str]] = []
    for k in intraday_keys:
        if k not in seen:
            seen.add(k)
            unique_keys.append(k)

    matrix: dict[tuple[str, str], dict[tuple[float, float], float | None]] = {
        k: {} for k in unique_keys
    }

    for coeff in sweep_coeffs:
        for size in sweep_sizes:
            recomputed = [recompute_cell_at_impact(c, coeff=coeff, position_notional_idr=size) for c in cells]
            for rc in recomputed:
                key = (rc.symbol, rc.family_name)
                if key in matrix:
                    matrix[key][(coeff, size)] = rc.net_edge_bps

    return matrix


# ---------------------------------------------------------------------------
# Report emitter
# ---------------------------------------------------------------------------

def emit_impact_sweep(
    grid: Sequence[GridPointResult],
    cells: Sequence[CellResult],
    outdir: str,
    sweep_coeffs: Sequence[float] = SWEEP_COEFFS,
    sweep_sizes: Sequence[float] = SWEEP_SIZES,
) -> str:
    """
    Write impact_sweep.md to outdir and return its path.

    Content:
    (a) Summary table — one row per (coeff, size) with survivor count and
        comma-separated list of intraday survivors with net bps.
    (b) Per-intraday-cell net-bps matrix across all grid points.
    (c) Annotations for re-entries (new survivor at lower impact) and drops
        (base survivor lost at higher impact).
    (d) Cells not rescaled (missing priced-at provenance), when any exist.
    (e) Methodology note explaining the exact analytical scaling.

    Parameters
    ----------
    grid : Sequence[GridPointResult]
        Output of sweep_grid().
    cells : Sequence[CellResult]
        Original base-run cells (used for the per-cell matrix).
    outdir : str
        Output directory (created if missing).
    sweep_coeffs, sweep_sizes : Sequence[float]
        Grid parameters (must match those used for sweep_grid).

    Returns
    -------
    str
        Absolute path to the written file.
    """
    os.makedirs(outdir, exist_ok=True)

    lines: list[str] = []

    lines.append("# Impact Sensitivity Sweep — Plan 10 Intraday Cells")
    lines.append("")
    lines.append(
        "> **Analytical sweep** — scaling is exact for the square-root model: no re-fetch, "
        "no API calls.  Sizes are the MEASURED sleeve (ADR-0017), not multiples of the "
        "void ADR-0011 30M constant.  See Methodology section below."
    )
    lines.append("")
    size_descs = ", ".join(f"{_fmt_size_m(s)} ({_size_label(s)})" for s in sweep_sizes)
    lines.append(
        "Grid: SQRT_IMPACT_COEFF ∈ {" + ", ".join(str(c) for c in sweep_coeffs) + "} × "
        f"position notional ∈ {{{size_descs}}} IDR "
        f"({len(sweep_coeffs) * len(sweep_sizes)} points)."
    )
    lines.append("")
    lines.append(
        f"This run's own priced-at base (before sweeping): "
        f"SQRT_IMPACT_COEFF={SQRT_IMPACT_COEFF}, "
        f"POSITION_NOTIONAL_IDR={int(POSITION_NOTIONAL_IDR):,} IDR "
        f"(`{ACTIVE_SLEEVE_SIZING.name}`, N={ACTIVE_SLEEVE_SIZING.concurrent_names})."
    )
    lines.append("")

    # ---- Cells not rescaled (missing provenance) — surfaced up front ----
    if grid and grid[0].unscaled_cells:
        lines.append("## Cells Not Rescaled (missing priced-at provenance)")
        lines.append("")
        lines.append(
            "These intraday cells predate `impact_priced_coeff` / "
            "`impact_priced_position_notional_idr` (ADR-0017) — the coefficient/notional "
            "they were actually priced at is not recorded, so rescaling them would have "
            "to ASSUME a base that might not be true.  They are held at their ORIGINAL "
            "impact/hurdle/net values at every grid point below rather than silently "
            "rescaled against an assumed base.  Re-run the base grid to populate "
            "provenance for these cells before trusting their sweep behaviour."
        )
        lines.append("")
        for desc in grid[0].unscaled_cells:
            lines.append(f"- {desc}")
        lines.append("")

    # ---- (a) Summary table ----
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Coeff | Position (IDR) | Concurrent Names | Survivors | Intraday Survivors | Re-entries | Drops |")
    lines.append("|-------|---------------|-------------------|-----------|-------------------|------------|-------|")

    for r in grid:
        size_str = _fmt_size_m(r.position_notional_idr)
        names_str = _size_label(r.position_notional_idr)
        survivors_str = ", ".join(r.intraday_survivors) if r.intraday_survivors else "—"
        reentry_str = ", ".join(r.reentry_cells) if r.reentry_cells else "—"
        dropped_str = ", ".join(r.dropped_cells) if r.dropped_cells else "—"
        lines.append(
            f"| {r.coeff} | {size_str} | {names_str} | {r.survivor_count} "
            f"| {survivors_str} | {reentry_str} | {dropped_str} |"
        )
    lines.append("")

    # ---- (b) Per-cell net-bps matrix ----
    lines.append("## Per-Cell Net-Edge Matrix (bps after full cost stack)")
    lines.append("")
    lines.append(
        "Each row is one intraday-tested cell.  "
        "Values are net_edge_bps (gross − hurdle) at each (coeff, size) point.  "
        "Positive = passes cost gate; negative = gated."
    )
    lines.append("")

    matrix = _net_bps_matrix(cells, grid, sweep_coeffs=sweep_coeffs, sweep_sizes=sweep_sizes)

    if matrix:
        # Build header: symbol:family | (0.5,1.5M) | (0.5,3M) | ... |
        col_headers = [f"({c},{_fmt_size_m(s)})" for c in sweep_coeffs for s in sweep_sizes]
        lines.append("| Cell | " + " | ".join(col_headers) + " |")
        lines.append("|------|" + "|".join(["------"] * len(col_headers)) + "|")

        for (sym, fam), pts in sorted(matrix.items()):
            cell_name = f"{sym}:{fam}"
            row_vals: list[str] = []
            for coeff in sweep_coeffs:
                for size in sweep_sizes:
                    val = pts.get((coeff, size))
                    if val is None:
                        row_vals.append("—")
                    else:
                        flag = " ✓" if val > 0 else " ✗"
                        row_vals.append(f"{val:.1f}{flag}")
            lines.append(f"| {cell_name} | " + " | ".join(row_vals) + " |")
    else:
        lines.append("*No intraday tested cells found.*")
    lines.append("")

    # ---- (c) Re-entry / drop annotations ----
    lines.append("## Re-entries and Drops vs Each Cell's Own Priced-At Base")
    lines.append("")
    lines.append(
        "Detected against EACH CELL'S OWN recorded `passes_cost_gate` at whatever "
        "(coeff, notional) it was actually priced at in the base grid run — not a single "
        "hardcoded point.  For a normal run every cell shares one priced-at point (this "
        f"run's: coeff={SQRT_IMPACT_COEFF}, notional={int(POSITION_NOTIONAL_IDR):,} IDR), "
        "but the detection itself does not assume that."
    )
    lines.append("")

    any_annotation = False
    for r in grid:
        is_base = abs(r.coeff - SQRT_IMPACT_COEFF) < 1e-9 and abs(r.position_notional_idr - POSITION_NOTIONAL_IDR) < 1.0
        if is_base:
            continue
        if r.reentry_cells:
            any_annotation = True
            size_str = _fmt_size_m(r.position_notional_idr)
            lines.append(
                f"- **Re-entry at coeff={r.coeff}, size={size_str} ({_size_label(r.position_notional_idr)}):** "
                f"{', '.join(r.reentry_cells)} — gated at base, now passes cost gate at this point. "
                "This is a cost-gate/FDR re-entry ONLY — it does NOT mean the cell clears the "
                "always-long null gate (ADR-0017 Consequences); check that separately."
            )
        if r.dropped_cells:
            any_annotation = True
            size_str = _fmt_size_m(r.position_notional_idr)
            lines.append(
                f"- **Drop at coeff={r.coeff}, size={size_str} ({_size_label(r.position_notional_idr)}):** "
                f"{', '.join(r.dropped_cells)} — base survivor, now gated at this point."
            )

    if not any_annotation:
        lines.append("*No re-entries or drops — survivor set is stable across the swept range.*")
    lines.append("")

    # ---- (d) Methodology ----
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "**Exact analytical scaling (no re-fetch, no API calls).**  "
        "For any intraday cell, the impact it actually reported was computed as:"
    )
    lines.append("")
    lines.append(
        "    impact_base = 2 × coeff_priced_at × daily_vol_bps × sqrt(notional_priced_at / adv)"
    )
    lines.append("")
    lines.append("At a new (coeff', size'):")
    lines.append("")
    lines.append(
        "    impact' = impact_base × (coeff' / coeff_priced_at) × sqrt(size' / notional_priced_at)"
    )
    lines.append("")
    lines.append(
        "This is exact because daily_vol_bps and adv are fixed per symbol and already embedded "
        "in impact_base.  The full cost hurdle is floor + impact' (for hold=0, financing=0).  "
        "The structural friction floor is extracted as: floor = min_edge_hurdle_bps − modelled_impact_bps."
    )
    lines.append("")
    _provenance_note = (
        "**`coeff_priced_at` and `notional_priced_at` are read from EACH CELL's own recorded "
        "`CellResult.impact_priced_coeff` / `impact_priced_position_notional_idr` (ADR-0017) "
        "— never a module constant.**  Before this fix, the sweep read "
        "`strategy_fit.impact.SQRT_IMPACT_COEFF` / `POSITION_NOTIONAL_IDR` directly and treated "
        "the CURRENT value of those module globals as \"what this cell was priced at\" — correct "
        "only as long as the constants hadn't changed since the cell was priced, which stopped "
        "being guaranteed the moment sizing became configurable (see strategy_fit/sleeve_sizing.py). "
        "A cell missing this provenance (both fields None — it predates ADR-0017) is NOT rescaled."
    )
    if grid and grid[0].unscaled_cells:
        _provenance_note += " See the \"Cells Not Rescaled\" section above."
    lines.append(_provenance_note)
    lines.append("")
    lines.append(
        "BH-FDR is re-run at each grid point via the production `apply_fdr_to_grid` function.  "
        "Every cell that actually priced impact is rescaled — intraday cells and, since plan 13, "
        "daily single-name cells (priced from daily bars by the same square-root law).  Only cells "
        "that never price impact at all (event / cross-sectional / pairs, "
        "`modelled_impact_bps=None`) pass through unchanged."
    )
    lines.append("")
    lines.append(
        "**Read the Survivors column with care — it is not monotonic in position size, and a "
        "LARGER size can yield MORE survivors.**  `apply_fdr_to_grid` runs BH only across "
        "cost-gate-PASSING cells, so a larger notional gates more cells on cost, shrinks the "
        "tested pool `m`, and thereby RAISES the BH threshold `k·alpha/m` for every cell still in "
        "it.  Survivor counts are therefore not a ranking of how favourable a grid point is; the "
        "per-cell matrix and the re-entry/drop rows are.  Note also that the matrix covers "
        "INTRADAY cells only while Survivors counts every shape, so a grid point with zero "
        "intraday survivors can still report a large total."
    )
    lines.append("")
    lines.append(
        "**The always-long null gate is not re-evaluated by this sweep** — it depends on the "
        "underlying asset's drift, not on position size, so it is unaffected by anything swept "
        "here (ADR-0017 Consequences).  A re-entry above is a cost-gate/FDR re-entry only; a cell "
        "revived purely by cheaper impact has not thereby cleared the null gate."
    )
    lines.append("")
    lines.append(
        f"This run's priced-at base (from strategy_fit/impact.py and strategy_fit/sleeve_sizing.py "
        f"at the time the base grid ran): SQRT_IMPACT_COEFF={SQRT_IMPACT_COEFF}, "
        f"POSITION_NOTIONAL_IDR={int(POSITION_NOTIONAL_IDR):,} IDR "
        f"(`{ACTIVE_SLEEVE_SIZING.name}`, equity={int(ACTIVE_SLEEVE_SIZING.equity_idr):,} IDR "
        f"measured {ACTIVE_SLEEVE_SIZING.measured_at}, N={ACTIVE_SLEEVE_SIZING.concurrent_names})."
    )

    path = os.path.join(outdir, "impact_sweep.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        fh.write("\n")

    return path
