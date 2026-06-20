"""
strategy_fit/impact_sweep.py — Impact sensitivity sweep for intraday cells.

Purpose
-------
The square-root impact model has two free parameters: the Almgren coefficient
(SQRT_IMPACT_COEFF) and the per-name position notional (POSITION_NOTIONAL_IDR).
Both are v1 constants; plan-13 will calibrate them from live fills.  This
module sweeps a small grid of (coeff, position_notional) values to show how
sensitive the intraday survivor count and net edges are to those assumptions.

Analytical scaling
------------------
For any intraday cell, the base impact was computed as:

    impact_base = 2 × SQRT_IMPACT_COEFF × daily_vol_bps × sqrt(participation_base)

where participation_base = POSITION_NOTIONAL_IDR / adv.  At a new (coeff', size'):

    impact' = 2 × coeff' × daily_vol_bps × sqrt(size' / adv)
            = impact_base × (coeff' / SQRT_IMPACT_COEFF) × sqrt(size' / POSITION_NOTIONAL_IDR)

This is EXACT for the sqrt model — daily_vol_bps and adv are fixed per symbol
and already embedded in impact_base.  No API calls or DataFrame re-reads are
needed.  The sweep therefore runs purely from the CellResult objects produced
by one normal grid run.

Grid
----
SWEEP_COEFFS  : {0.5, 1.0}    (conservative vs half-coefficient)
SWEEP_SIZES   : {15M, 30M, 60M} IDR (half / base / double position size)
6 grid points total: 2 × 3.

Scope
-----
Only INTRADAY (hold=0, shape=intraday_same_day) cells have their impact
rescaled.  Daily / event / cross-sectional / pairs cells always have
modelled_impact_bps=None (impact=0) and are included unchanged in the FDR
pool at every grid point — this preserves BH-FDR pool consistency with the
production pipeline.

Survivors at each grid point
-----------------------------
Survivors' = status=="tested" AND cost_pass' AND fdr_accepted AND net'>0.

FDR is re-run at each grid point by calling the existing `apply_fdr_to_grid`
from run_strategy_fit.py — no BH reimplementation.
"""

from __future__ import annotations

import dataclasses
import math
import os
from dataclasses import dataclass
from typing import Sequence

from strategy_fit.harness import SHAPE_INTRADAY_SAME_DAY, CellResult
from strategy_fit.impact import POSITION_NOTIONAL_IDR, SQRT_IMPACT_COEFF

# ---------------------------------------------------------------------------
# Sweep grid constants — pulled from impact.py so they stay in sync
# ---------------------------------------------------------------------------

SWEEP_COEFFS: tuple[float, ...] = (0.5, 1.0)
"""
Two coefficient values to sweep:
  0.5 — lower end of the Almgren empirical range (more optimistic)
  1.0 — SQRT_IMPACT_COEFF base value (conservative, plan-10 assumption)
"""

SWEEP_SIZES: tuple[float, ...] = (
    POSITION_NOTIONAL_IDR / 2,   # 15_000_000 IDR
    POSITION_NOTIONAL_IDR,       # 30_000_000 IDR (base)
    POSITION_NOTIONAL_IDR * 2,   # 60_000_000 IDR
)
"""
Three position notional values to sweep:
  half base  (15M IDR) — smaller per-name slice, e.g. 20 concurrent names
  base       (30M IDR) — plan-10 v1 assumption
  double     (60M IDR) — larger per-name slice, e.g. 5 concurrent names
"""


# ---------------------------------------------------------------------------
# Scaling formula
# ---------------------------------------------------------------------------

def scale_impact_bps(
    impact_base_bps: float,
    coeff: float,
    position_notional_idr: float,
) -> float:
    """
    Rescale a base impact to a new (coeff, position_notional) point.

    Formula (exact for the sqrt model, no re-fetch needed):

        impact' = impact_base × (coeff / SQRT_IMPACT_COEFF) × sqrt(size' / POSITION_NOTIONAL_IDR)

    Parameters
    ----------
    impact_base_bps : float
        The cell's modelled_impact_bps at the base point
        (SQRT_IMPACT_COEFF=1.0, POSITION_NOTIONAL_IDR=30M IDR).
    coeff : float
        New Almgren coefficient (e.g. 0.5 or 1.0).
    position_notional_idr : float
        New per-name position notional in IDR.

    Returns
    -------
    float
        Rescaled round-trip impact in basis points.
    """
    coeff_ratio = coeff / SQRT_IMPACT_COEFF
    size_ratio = position_notional_idr / POSITION_NOTIONAL_IDR
    return impact_base_bps * coeff_ratio * math.sqrt(size_ratio)


# ---------------------------------------------------------------------------
# Cell recompute
# ---------------------------------------------------------------------------

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

    # Rescale impact analytically.
    new_impact = scale_impact_bps(
        impact_base_bps=cell.modelled_impact_bps,
        coeff=coeff,
        position_notional_idr=position_notional_idr,
    )

    # Reconstruct floor from the stored hurdle and base impact.
    floor = cell.min_edge_hurdle_bps - cell.modelled_impact_bps  # type: ignore[operator]

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
        Intraday cells that were cost-gated at the base point (1.0, 30M) but
        pass at this grid point.
    dropped_cells : list[str]
        Intraday cells that passed the cost gate at the base point but fail here.
    """

    coeff: float
    position_notional_idr: float
    survivor_count: int
    intraday_survivors: list[str]
    all_survivors: list[CellResult]
    reentry_cells: list[str]
    dropped_cells: list[str]


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
    1. Recompute each intraday cell's impact/hurdle/net at the new (coeff, size).
       Non-intraday cells pass through unchanged.
    2. Re-run BH-FDR via apply_fdr_to_grid (same function as production — ensures
       the FDR pool is identical to the production pipeline).
    3. Collect survivors and detect re-entries / drops vs. the base point.

    The base point is identified as (coeff=SQRT_IMPACT_COEFF, size=POSITION_NOTIONAL_IDR).
    Re-entries: gated at base, passes at this point.
    Drops: passes at base, gated at this point.

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
    # Base point: coeff=SQRT_IMPACT_COEFF, size=POSITION_NOTIONAL_IDR.
    # Use modelled_impact_bps from the original cell (not recomputed) as the base.
    _base_pass: dict[tuple[str, str], bool] = {
        (c.symbol, c.family_name): bool(c.passes_cost_gate)
        for c in cells
        if c.shape == SHAPE_INTRADAY_SAME_DAY and c.status == "tested"
    }

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
    from strategy_fit.run_strategy_fit import apply_fdr_to_grid

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
    (b) Per-intraday-cell net-bps matrix across all 6 grid points.
    (c) Annotations for re-entries (new survivor at lower impact) and drops
        (base survivor lost at higher impact).
    (d) Methodology note explaining the exact analytical scaling.

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
        "no API calls.  See Methodology section below."
    )
    lines.append("")
    lines.append(
        "Grid: SQRT_IMPACT_COEFF ∈ {" + ", ".join(str(c) for c in sweep_coeffs) + "} × "
        "POSITION_NOTIONAL_IDR ∈ {" + ", ".join(f"{int(s/1e6)}M" for s in sweep_sizes) + "} IDR "
        f"({len(sweep_coeffs) * len(sweep_sizes)} points)."
    )
    lines.append("")

    # ---- (a) Summary table ----
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Coeff | Position (IDR) | Survivors | Intraday Survivors | Re-entries | Drops |")
    lines.append("|-------|---------------|-----------|-------------------|------------|-------|")

    for r in grid:
        size_str = f"{int(r.position_notional_idr / 1e6)}M"
        survivors_str = ", ".join(r.intraday_survivors) if r.intraday_survivors else "—"
        reentry_str = ", ".join(r.reentry_cells) if r.reentry_cells else "—"
        dropped_str = ", ".join(r.dropped_cells) if r.dropped_cells else "—"
        lines.append(
            f"| {r.coeff} | {size_str} | {r.survivor_count} "
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
        # Build header: symbol:family | (0.5,15M) | (0.5,30M) | ... |
        col_headers = [f"({c},{int(s/1e6)}M)" for c in sweep_coeffs for s in sweep_sizes]
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
    lines.append("## Re-entries and Drops vs Base Point (coeff=1.0, size=30M)")
    lines.append("")

    any_annotation = False
    for r in grid:
        is_base = abs(r.coeff - SQRT_IMPACT_COEFF) < 1e-9 and abs(r.position_notional_idr - POSITION_NOTIONAL_IDR) < 1.0
        if is_base:
            continue
        if r.reentry_cells:
            any_annotation = True
            size_str = f"{int(r.position_notional_idr / 1e6)}M"
            lines.append(
                f"- **Re-entry at coeff={r.coeff}, size={size_str}:** "
                f"{', '.join(r.reentry_cells)} — gated at base, now passes cost gate at lower impact."
            )
        if r.dropped_cells:
            any_annotation = True
            size_str = f"{int(r.position_notional_idr / 1e6)}M"
            lines.append(
                f"- **Drop at coeff={r.coeff}, size={size_str}:** "
                f"{', '.join(r.dropped_cells)} — base survivor, now gated at higher impact."
            )

    if not any_annotation:
        lines.append("*No re-entries or drops — survivor set is stable across the swept range.*")
    lines.append("")

    # ---- (d) Methodology ----
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "**Exact analytical scaling (no re-fetch, no API calls).**  "
        "For any intraday cell, the base impact was computed as:"
    )
    lines.append("")
    lines.append(
        "    impact_base = 2 × SQRT_IMPACT_COEFF × daily_vol_bps × sqrt(POSITION_NOTIONAL_IDR / adv)"
    )
    lines.append("")
    lines.append("At a new (coeff', size'):")
    lines.append("")
    lines.append(
        "    impact' = impact_base × (coeff' / SQRT_IMPACT_COEFF) × sqrt(size' / POSITION_NOTIONAL_IDR)"
    )
    lines.append("")
    lines.append(
        "This is exact because daily_vol_bps and adv are fixed per symbol and already embedded "
        "in impact_base.  The full cost hurdle is floor + impact' (for hold=0, financing=0).  "
        "The structural friction floor is extracted as: floor = min_edge_hurdle_bps − modelled_impact_bps."
    )
    lines.append("")
    lines.append(
        "BH-FDR is re-run at each grid point via the production `apply_fdr_to_grid` function.  "
        "Daily / event / cross-sectional / pairs cells are included unchanged (impact=0) so the "
        "FDR pool is identical in size to the production pipeline at every grid point."
    )
    lines.append("")
    lines.append(
        f"Base constants (from strategy_fit/impact.py): "
        f"SQRT_IMPACT_COEFF={SQRT_IMPACT_COEFF}, "
        f"POSITION_NOTIONAL_IDR={int(POSITION_NOTIONAL_IDR):,} IDR."
    )

    path = os.path.join(outdir, "impact_sweep.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        fh.write("\n")

    return path
