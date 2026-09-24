"""
dashboard/panel_c.py — per-strategy profitability (plan-19 §5).

Question answered: **what did each algo earn, net of everything, and is that
number believable?**

Grouped from ``attribution`` by ``strategy_id``, with the four columns without
which the panel misleads:

1. ``sink_kind`` — replay PnL and live PnL never share a row or a total.
2. The always-long null — which the runtime cannot measure, so the column says
   "not measured here" and points at the research cell, rather than leaving a
   gross-edge number to look like a result.
3. ``required_effective_n`` vs. actual N — plan-11 §7.8's deployability screen,
   imported from ``lib.stats``, never reimplemented.
4. ``calibration`` excluded — ADR-0016 §6. This module drops those rows itself
   rather than trusting the caller to have filtered them.

The one statistic computed here is a sample standard deviation, as the INPUT to
``lib.stats.required_effective_n``. Means and counts are aggregations of stored
rows; ``mean_net_pnl_bps`` is deliberately the same arithmetic as
``engine.finalize``'s ``Counters.NetPnlBps``, and a Go test pins the two
together (``TestAttributionAverageMatchesCounters``).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from lib.stats import required_effective_n

from dashboard.store import Attribution, DecisionFunnel, RejectRow, StoreData
from dashboard.strategy_map import (
    EXCLUDED_STRATEGY_IDS,
    StrategyJoin,
    is_excluded,
    join_for,
    require_all_mapped,
)

POWER_Z: float = 2.0
"""
The z used for the deployability screen, matching lib.stats.required_effective_n's
own default and plan-10 W2.1's worked example. Named here so the panel can show
which z produced the number.
"""


@dataclass(frozen=True)
class StrategyPnL:
    """One strategy's realised performance, within one sink kind."""

    strategy_id: str
    sink_kind: str
    join: StrategyJoin

    round_trips: int
    symbols: int
    mean_gross_pnl_bps: float
    mean_net_pnl_bps: float
    fee_slippage_gap_bps: float
    win_rate: float
    mean_hold_calendar_days: float
    worst_net_pnl_bps: float
    best_net_pnl_bps: float
    max_drawdown_bps: float
    per_trade_sigma_bps: float | None
    required_effective_n: int | None
    first_as_of_date: str
    last_as_of_date: str
    run_ids: tuple[str, ...]

    @property
    def is_replay(self) -> bool:
        """
        Until plan-18 B4 lands, every figure here is replay or paper. The panel
        says so in a banner, not a footnote.
        """
        return self.sink_kind != "live"

    @property
    def underpowered(self) -> bool | None:
        """
        True when realised N falls short of the power-derived requirement.

        None when power could not be assessed at all (fewer than two
        round-trips, or a non-positive mean edge, so there is no edge to
        resolve) — a distinct condition from "assessed and adequately
        powered", and not to be rendered as the same thing.
        """
        if self.required_effective_n is None:
            return None
        return self.round_trips < self.required_effective_n


def _max_drawdown_bps(values: Sequence[float]) -> float:
    """
    Largest peak-to-trough fall of the cumulative net-bps curve, in bps.

    A simple running maximum over the round-trips in the order they closed —
    an arithmetic sum of per-trade bps, not a compounded equity curve. It is
    reported as a magnitude (>= 0).
    """
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return abs(worst)


def _required_n(mean_edge_bps: float, sigma_bps: float | None) -> int | None:
    """
    The deployability screen, delegated to lib.stats.

    Returns None rather than a number when the question is not askable: a
    non-positive edge has nothing to resolve, and fewer than two observations
    give no dispersion. Substituting a default sigma here would manufacture a
    threshold, which is exactly what D2 forbids.
    """
    if sigma_bps is None or sigma_bps <= 0 or mean_edge_bps <= 0:
        return None
    return required_effective_n(
        target_edge_bps=mean_edge_bps, per_trade_sigma_bps=sigma_bps, z=POWER_Z
    )


def summarise(rows: Sequence[Attribution]) -> list[StrategyPnL]:
    """
    One row per (strategy_id, sink_kind).

    Grouping by sink kind rather than filtering on it is the point: a paper
    total and a live total both get rendered, and neither can be added to the
    other by accident.
    """
    kept = [r for r in rows if not is_excluded(r.strategy_id)]
    require_all_mapped([r.strategy_id for r in kept])

    groups: dict[tuple[str, str], list[Attribution]] = {}
    for r in kept:
        groups.setdefault((r.strategy_id, r.sink_kind), []).append(r)

    out: list[StrategyPnL] = []
    for (strategy_id, sink_kind), group in sorted(groups.items()):
        ordered = sorted(group, key=lambda r: r.exit_recv_ns)
        net = [r.net_pnl_bps for r in ordered]
        gross = [r.gross_pnl_bps for r in ordered]
        mean_net = statistics.fmean(net)
        sigma = statistics.stdev(net) if len(net) > 1 else None
        dates = sorted(r.as_of_date for r in ordered if r.as_of_date)

        out.append(
            StrategyPnL(
                strategy_id=strategy_id,
                sink_kind=sink_kind,
                join=join_for(strategy_id),
                round_trips=len(ordered),
                symbols=len({r.symbol for r in ordered}),
                mean_gross_pnl_bps=statistics.fmean(gross),
                mean_net_pnl_bps=mean_net,
                fee_slippage_gap_bps=statistics.fmean(gross) - mean_net,
                win_rate=sum(1 for v in net if v > 0) / len(net),
                mean_hold_calendar_days=statistics.fmean(
                    [r.hold_calendar_days for r in ordered]
                ),
                worst_net_pnl_bps=min(net),
                best_net_pnl_bps=max(net),
                max_drawdown_bps=_max_drawdown_bps(net),
                per_trade_sigma_bps=sigma,
                required_effective_n=_required_n(mean_net, sigma),
                first_as_of_date=dates[0] if dates else "",
                last_as_of_date=dates[-1] if dates else "",
                run_ids=tuple(sorted({r.run_id for r in ordered})),
            )
        )
    return out


@dataclass(frozen=True)
class RejectSummary:
    """One (strategy_id, sink_kind, reason) group from the order_reject table."""

    strategy_id: str
    sink_kind: str
    reason: str
    count: int


def summarise_rejects(rows: Sequence[RejectRow]) -> list[RejectSummary]:
    """
    Group order_reject rows by (strategy_id, sink_kind, reason) and count
    them — a COUNT over stored rows (test_no_new_metrics.py's own
    exception), not a metric. Calibration traffic is excluded the same way
    every other Panel C total excludes it (ADR-0016 §6): a rejection under
    the reserved strategy_id is calibration noise, not a strategy's own
    risk-gate history.
    """
    kept = [r for r in rows if not is_excluded(r.strategy_id)]
    counts: dict[tuple[str, str, str], int] = {}
    for r in kept:
        key = (r.strategy_id, r.sink_kind, r.reason)
        counts[key] = counts.get(key, 0) + 1
    return [
        RejectSummary(strategy_id=sid, sink_kind=sink_kind, reason=reason, count=n)
        for (sid, sink_kind, reason), n in sorted(counts.items())
    ]


@dataclass(frozen=True)
class PanelC:
    rows: list[StrategyPnL]
    funnels: list[DecisionFunnel]
    excluded_round_trips: int
    """
    How many rows were dropped because their strategy_id is reserved
    (ADR-0016 §6). Reported, not hidden: "0 shown" and "0 recorded" are
    different facts.
    """
    excluded_ids: tuple[str, ...]
    has_live: bool
    """True if any row came from a live sink. False means every number is replay."""
    rejects: list[RejectSummary]
    """
    Rejections grouped by (strategy_id, sink_kind, reason) — see
    summarise_rejects. Empty for a store that predates the order_reject
    table (dashboard/store.py tolerates its absence) and for a store that
    genuinely recorded none; the two are not distinguished here, matching
    DecisionFunnel.rejects' own doc comment.
    """


def build_panel_c(data: StoreData) -> PanelC:
    """Assemble Panel C from the post-trade store(s)."""
    excluded = [r for r in data.attributions if is_excluded(r.strategy_id)]
    rows = summarise(data.attributions)
    funnels = [f for f in data.funnels if not is_excluded(f.strategy_id)]
    require_all_mapped([f.strategy_id for f in funnels])
    return PanelC(
        rows=rows,
        funnels=sorted(funnels, key=lambda f: (f.strategy_id, f.run_id)),
        excluded_round_trips=len(excluded),
        excluded_ids=tuple(sorted(EXCLUDED_STRATEGY_IDS)),
        has_live=any(r.sink_kind == "live" for r in rows),
        rejects=summarise_rejects(data.rejects),
    )
