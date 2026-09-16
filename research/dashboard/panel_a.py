"""
dashboard/panel_a.py — the alpha mining process (plan-19 §3).

Question answered: **where does a candidate strategy die, and how many die
there?**

The state machine already exists as a typed, frozen dataclass, so the funnel is
a GROUP BY over ``CellResult``, not a new model.  Every threshold and every
verdict is imported:

* ``strategy_fit.report._is_pass_eligible`` / ``_clears_null_gate`` — the gates
* ``strategy_fit.walk_forward.DECAY_THRESHOLD_SHARPE`` — the decay line
* ``strategy_fit.intraday_walk_forward.MIN_OOS_EFF_N`` — the OOS floor
* ``strategy_fit.catalog_meta.ALL_FAMILIES`` — the completeness contract

Nothing in this module decides what "decayed" or "PASS" means.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from strategy_fit.catalog_meta import ALL_FAMILIES
from strategy_fit.harness import CellResult
from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N
from strategy_fit.long_only import STATUS_LONG_SHORT_INFEASIBLE
from strategy_fit.report import (
    NULL_GATE_APPLICABLE_SHAPES,
    _clears_null_gate,
    _is_pass_eligible,
)
from dashboard.catalog_map import (
    catalog_status_for,
    unmapped_families,
    untested_catalog_families,
)
from strategy_fit.walk_forward import (
    DECAY_MAJORITY_FRAC,
    DECAY_THRESHOLD_HR,
    DECAY_THRESHOLD_SHARPE,
)

# ---------------------------------------------------------------------------
# Funnel stages
# ---------------------------------------------------------------------------

# Ordered, exhaustive and mutually exclusive: every catalog cell reaches
# exactly one. The first five are CellResult.status verbatim; the last three
# split the "tested" cells by the gates report.py applies.
STAGE_DATA_BLOCKED = "data_blocked"
STAGE_SKIPPED_HORIZON = "skipped_horizon"
STAGE_INSUFFICIENT_N = "insufficient_n"
STAGE_COST_GATED = "cost_gated"
STAGE_DECAYED = "decayed"
# Two terminal states the grid emits for structurally long/short shapes. They
# are imported (or, for pairs, named) rather than invented: a cell that reaches
# one of these was not tested and is not a failure of the edge — it is a shape
# an IDX retail account cannot trade, or a pair that did not cointegrate.
STAGE_LONG_SHORT_INFEASIBLE = STATUS_LONG_SHORT_INFEASIBLE
STAGE_COINTEGRATION_NOT_FOUND = "cointegration_not_found"
STAGE_FDR_REJECTED = "fdr_rejected"
STAGE_LOSES_TO_NULL = "loses_to_null"
STAGE_PASS = "pass"
STAGE_UNCLASSIFIED = "unclassified"

FUNNEL_STAGES: tuple[str, ...] = (
    STAGE_DATA_BLOCKED,
    STAGE_SKIPPED_HORIZON,
    STAGE_INSUFFICIENT_N,
    STAGE_COST_GATED,
    STAGE_DECAYED,
    STAGE_LONG_SHORT_INFEASIBLE,
    STAGE_COINTEGRATION_NOT_FOUND,
    STAGE_FDR_REJECTED,
    STAGE_LOSES_TO_NULL,
    STAGE_PASS,
    STAGE_UNCLASSIFIED,
)

# The stages that are CellResult.status carried through verbatim, as opposed to
# the three this module derives from report.py's gates over "tested" cells.
STATUS_STAGES: tuple[str, ...] = (
    STAGE_DATA_BLOCKED,
    STAGE_SKIPPED_HORIZON,
    STAGE_INSUFFICIENT_N,
    STAGE_COST_GATED,
    STAGE_DECAYED,
    STAGE_LONG_SHORT_INFEASIBLE,
    STAGE_COINTEGRATION_NOT_FOUND,
)

STAGE_LABELS: Mapping[str, str] = {
    STAGE_DATA_BLOCKED: "data blocked — the history does not exist yet",
    STAGE_SKIPPED_HORIZON: "horizon skipped — hold outside the tested band",
    STAGE_INSUFFICIENT_N: "insufficient N — below the floor after de-overlap",
    STAGE_COST_GATED: "cost gated — net edge did not clear the full hurdle",
    STAGE_DECAYED: f"decayed — rolling Sharpe below {DECAY_THRESHOLD_SHARPE}",
    STAGE_LONG_SHORT_INFEASIBLE: (
        "long/short infeasible — structurally long/short, and IDX retail cannot short"
    ),
    STAGE_COINTEGRATION_NOT_FOUND: "no cointegrated pair among the candidates",
    STAGE_FDR_REJECTED: "FDR rejected — did not survive Benjamini-Hochberg",
    STAGE_LOSES_TO_NULL: "loses to null — the signal lost to always-long",
    STAGE_PASS: "PASS — cleared every gate",
    STAGE_UNCLASSIFIED: "UNCLASSIFIED — reached no terminal state (a grid bug)",
}


def stage_of(cell: CellResult) -> str:
    """
    Which funnel stage this cell died at.

    Order matters and mirrors the order the grid itself applies the gates:
    a cell that never got tested cannot also be cost-gated. The residual
    ``unclassified`` stage exists so that a cell reaching no terminal state
    shows up as a bug rather than silently vanishing from the totals
    (plan-19 §8, funnel conservation).
    """
    if cell.status in STATUS_STAGES:
        return cell.status
    if cell.status != "tested":
        return STAGE_UNCLASSIFIED
    if cell.passes_cost_gate is not True:
        return STAGE_COST_GATED
    if cell.fdr_accepted is not True:
        return STAGE_FDR_REJECTED
    if not _clears_null_gate(cell):
        return STAGE_LOSES_TO_NULL
    if _is_pass_eligible(cell):
        return STAGE_PASS
    # Tested, cost-gate-clear, FDR-accepted, beats the null — but still not
    # PASS. That is the impact-priced or OOS-power gate, and it is a real
    # outcome, not an error: report.py calls it INCONCLUSIVE.
    return STAGE_FDR_REJECTED


@dataclass(frozen=True)
class FunnelRow:
    stage: str
    label: str
    count: int
    share: float


@dataclass(frozen=True)
class Funnel:
    rows: list[FunnelRow]
    total: int

    def count(self, stage: str) -> int:
        for r in self.rows:
            if r.stage == stage:
                return r.count
        return 0

    @property
    def conservation_error(self) -> int:
        """
        Cells counted minus cells in. Must be zero; a non-zero value means a
        cell reached no stage, which the dashboard surfaces rather than hides.
        """
        return sum(r.count for r in self.rows) - self.total


def build_funnel(cells: Sequence[CellResult]) -> Funnel:
    """Counts at each stage. A GROUP BY, nothing more."""
    counts = Counter(stage_of(c) for c in cells)
    total = len(cells)
    rows = [
        FunnelRow(
            stage=stage,
            label=STAGE_LABELS[stage],
            count=counts.get(stage, 0),
            share=(counts.get(stage, 0) / total if total else 0.0),
        )
        for stage in FUNNEL_STAGES
    ]
    return Funnel(rows=rows, total=total)


# ---------------------------------------------------------------------------
# Coverage grid — the completeness contract, rendered
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverageCell:
    family: str
    hold: int
    catalog_status: str
    """From catalog_meta.ALL_FAMILIES: what the CATALOG says about this family."""
    dominant_stage: str
    """The most common run-time stage among this family/hold's cells."""
    counts: Mapping[str, int]
    tested: int
    total: int


@dataclass(frozen=True)
class Coverage:
    cells: list[CoverageCell]
    families: list[str]
    holds: list[int]
    untested_families: list[str]
    """
    Catalog families with no cell in this run, AFTER mapping runner family
    names to catalog ids (dashboard/catalog_map.py). Plan-08 §7.3 and plan-16
    require that no family is silently skipped; this list is that requirement
    made checkable instead of asserted. Compared by raw string it was wrong:
    the runner calls the cross-sectional family cross_sectional_momentum and
    the catalog calls it cross_sectional.
    """
    unmapped_families: list[str]
    """
    Runner family names catalog_map.py has no entry for. Rendered as a banner:
    their coverage claim is unverified until someone adds a row.
    """


def catalog_status_by_family() -> dict[str, str]:
    """family_id -> catalog_status, from the frozen catalog."""
    return {f.family_id: f.catalog_status for f in ALL_FAMILIES}


def build_coverage(cells: Sequence[CellResult]) -> Coverage:
    catalog = catalog_status_by_family()
    grouped: dict[tuple[str, int], list[CellResult]] = {}
    for c in cells:
        grouped.setdefault((c.family_name, c.horizon_trading_days), []).append(c)

    out: list[CoverageCell] = []
    for (family, hold), group in sorted(grouped.items()):
        counts = Counter(stage_of(c) for c in group)
        dominant = max(counts.items(), key=lambda kv: (kv[1], -FUNNEL_STAGES.index(kv[0])))[0]
        out.append(
            CoverageCell(
                family=family,
                hold=hold,
                catalog_status=catalog_status_for(family, catalog),
                dominant_stage=dominant,
                counts=dict(counts),
                tested=sum(1 for c in group if c.status == "tested"),
                total=len(group),
            )
        )

    seen_families = sorted({c.family_name for c in cells})
    return Coverage(
        cells=out,
        families=sorted({c.family for c in out}),
        holds=sorted({c.hold for c in out}),
        untested_families=untested_catalog_families(seen_families),
        unmapped_families=unmapped_families(seen_families),
    )


# ---------------------------------------------------------------------------
# Run diff — the cheapest defect detector available
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellDiff:
    """One cell that changed between two runs."""

    symbol: str
    family_name: str
    hold: int
    was_stage: str | None
    now_stage: str | None
    was_net_edge_bps: float | None
    now_net_edge_bps: float | None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.symbol, self.family_name, self.hold)

    @property
    def stage_changed(self) -> bool:
        return self.was_stage != self.now_stage

    @property
    def edge_delta_bps(self) -> float | None:
        if self.was_net_edge_bps is None or self.now_net_edge_bps is None:
            return None
        return self.now_net_edge_bps - self.was_net_edge_bps

    @property
    def magnitude(self) -> float:
        """Sort key: how much this change deserves attention."""
        delta = self.edge_delta_bps
        return abs(delta) if delta is not None else float("inf")


def _cell_key(c: CellResult) -> tuple[str, str, int]:
    return (c.symbol, c.family_name, c.horizon_trading_days)


def build_diff(previous: Sequence[CellResult], current: Sequence[CellResult]) -> list[CellDiff]:
    """
    Cells whose stage or net edge changed, sorted by magnitude.

    This is the panel that would have caught BNBR flipping −113.1 → +119.5 bps,
    the ×100 lot-size defect, and the volatility-estimator flip: each was a
    change in a value that nothing was watching. Appearances and disappearances
    count as changes — a cell that stopped being tested is a change too, and is
    sorted first (infinite magnitude) because there is no delta to weigh it by.
    """
    was = {_cell_key(c): c for c in previous}
    now = {_cell_key(c): c for c in current}

    diffs: list[CellDiff] = []
    for key in sorted(set(was) | set(now)):
        a, b = was.get(key), now.get(key)
        d = CellDiff(
            symbol=key[0],
            family_name=key[1],
            hold=key[2],
            was_stage=stage_of(a) if a else None,
            now_stage=stage_of(b) if b else None,
            was_net_edge_bps=a.net_edge_bps if a else None,
            now_net_edge_bps=b.net_edge_bps if b else None,
        )
        if d.stage_changed or (d.edge_delta_bps not in (None, 0.0)):
            diffs.append(d)

    diffs.sort(key=lambda d: (-d.magnitude, d.key))
    return diffs


# ---------------------------------------------------------------------------
# Thresholds in force, for the panel's own footer
# ---------------------------------------------------------------------------

def thresholds_in_force() -> dict[str, Any]:
    """
    The thresholds this render used, read live from their owning modules.

    Rendered on the panel so the reader can see which line each verdict was
    drawn against, without the dashboard ever defining one.
    """
    return {
        "DECAY_THRESHOLD_SHARPE": DECAY_THRESHOLD_SHARPE,
        "DECAY_THRESHOLD_HR": DECAY_THRESHOLD_HR,
        "DECAY_MAJORITY_FRAC": DECAY_MAJORITY_FRAC,
        "MIN_OOS_EFF_N": MIN_OOS_EFF_N,
        "NULL_GATE_APPLICABLE_SHAPES": sorted(NULL_GATE_APPLICABLE_SHAPES),
    }
