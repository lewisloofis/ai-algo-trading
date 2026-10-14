"""
dashboard/scorecard.py — turn a strategy-fit report into one scorecard row per cell.

Each single-name cell gets its trades re-derived offline (strategy_fit.trade_export,
which calls the harness's own signal / sequential-selection functions) and a handful
of plain figures read off that trade list. The cost per trade is the harness's own
full cost hurdle for the cell, parsed from its report (results.py) — never re-modelled.

Figures, all per trade and all arithmetic over the exported trades:
  * average profit after costs, in % and in rupiah per 1,000,000 IDR traded
  * the same average without the 5 best trades (compare_d1_rescore.TOP_N)
  * win rate after costs
  * what simply holding the stock did over the same span
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

from lib.stats import welch_t
from strategy_fit.compare_d1_rescore import TOP_N
from strategy_fit.trade_export import BacktestTrade, CellTrades

from dashboard import results as R
from dashboard.verdicts import Verdict, verdict_for

STAKE_IDR = 1_000_000
"""Rupiah-per-trade used to express percentages as money (a reading aid, not the account size)."""

# The cells the owner has heard about before the re-score. Always shown, whatever their gates.
FAMOUS: tuple[tuple[str, str, int], ...] = (
    ("BNBR", "daily_momentum", 2),
    ("HRTA", "volume_breakout", 5),
    ("INCO", "mean_reversion", 2),
)

REPRODUCTION_TOLERANCE_BPS = 1.0
"""The report prints one decimal of a bps; allow a hair more before calling a mismatch."""


@dataclass(frozen=True)
class TradeStats:
    n_trades: int
    cost_pct: float
    gross_avg_pct: float
    net_avg_pct: float
    net_avg_idr: float
    """Average profit per trade after costs, per STAKE_IDR traded."""
    net_without_best_pct: float | None
    net_without_best_idr: float | None
    win_rate: float
    """Share of trades with a profit after costs."""
    hold_total_pct: float | None
    """Buy at the first entry, sell at the last exit: what holding the stock did."""
    hold_total_idr: float | None
    first_entry: str
    last_exit: str
    any_day_avg_pct: float | None = None
    """Buying the same stock on ANY day for the same hold, after the same costs (per trade)."""
    any_day_avg_idr: float | None = None
    edge_vs_null_pct: float | None = None
    """Strategy's average gross trade minus the any-day average, per trade (cost cancels)."""
    t_stat: float | None = None
    """Welch's t of that edge (lib.stats.welch_t); None when it cannot be computed."""
    in_market_pct: float | None = None
    """Share of the span's sessions the strategy actually held a position."""


@dataclass(frozen=True)
class ScoreRow:
    cell: R.ResultCell
    stats: TradeStats | None
    verdict: Verdict
    famous: bool
    highlighted: bool
    reproduces: bool | None
    """None = nothing in the report to compare against; False = re-derived trades differ."""
    slug: str

    @property
    def detail_page(self) -> str | None:
        return f"strategy-{self.slug}.html" if self.highlighted and self.stats else None


def slug_for(cell: R.ResultCell) -> str:
    return f"{cell.symbol}-{cell.family}-h{cell.hold}".lower().replace("_", "-")


def trade_stats(
    trades: Sequence[BacktestTrade],
    cost_bps: float,
    bars_close: Mapping[str, float] | None = None,
    always_long_return: float | None = None,
    null_returns: Sequence[float] = (),
    hold: int | None = None,
) -> TradeStats | None:
    """Plain per-trade figures for one cell, or None when there are no trades."""
    if not trades:
        return None
    cost = cost_bps / 1e4
    gross = [t.gross_return for t in trades]
    net = [g - cost for g in gross]
    net_avg = statistics.fmean(net)
    trimmed = sorted(net)[:-TOP_N] if len(net) > TOP_N else None
    trimmed_avg = statistics.fmean(trimmed) if trimmed else None
    hold_total = None
    first, last = trades[0], trades[-1]
    if bars_close and first.entry_date in bars_close and last.exit_date in bars_close:
        hold_total = bars_close[last.exit_date] / bars_close[first.entry_date] - 1.0
    null_mean = always_long_return
    if null_mean is None and len(null_returns):
        null_mean = statistics.fmean(null_returns)
    t_stat = None
    if len(null_returns):
        # Always-long trades overlap by the hold, so only ~len/hold of them are independent.
        t = welch_t(gross, null_returns, null_n_eff=len(null_returns) / max(hold or 1, 1))
        t_stat = None if t != t else t
    span = None
    if bars_close and first.entry_date in bars_close and last.exit_date in bars_close:
        span = sum(1 for d in bars_close if first.entry_date <= d <= last.exit_date)
    return TradeStats(
        n_trades=len(trades),
        cost_pct=cost * 100,
        gross_avg_pct=statistics.fmean(gross) * 100,
        net_avg_pct=net_avg * 100,
        net_avg_idr=net_avg * STAKE_IDR,
        net_without_best_pct=None if trimmed_avg is None else trimmed_avg * 100,
        net_without_best_idr=None if trimmed_avg is None else trimmed_avg * STAKE_IDR,
        win_rate=sum(1 for v in net if v > 0) / len(net),
        hold_total_pct=None if hold_total is None else hold_total * 100,
        hold_total_idr=None if hold_total is None else hold_total * STAKE_IDR,
        first_entry=first.entry_date,
        last_exit=last.exit_date,
        any_day_avg_pct=None if always_long_return is None else (always_long_return - cost) * 100,
        any_day_avg_idr=None if always_long_return is None else (always_long_return - cost) * STAKE_IDR,
        edge_vs_null_pct=None if null_mean is None else (statistics.fmean(gross) - null_mean) * 100,
        t_stat=t_stat,
        in_market_pct=in_market_pct(len(trades), hold, span),
    )


def in_market_pct(n_trades: int, hold: int | None, span_sessions: int | None) -> float | None:
    """Share of sessions in the span the strategy held a position (each trade holds ``hold`` sessions)."""
    if not n_trades or not hold or not span_sessions:
        return None
    return min(100.0, n_trades * hold / span_sessions * 100)


def cumulative_series(
    trades: Sequence[BacktestTrade], cost_pct: float, null_mean: float | None
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[BacktestTrade, float, float]]]:
    """
    Cumulative profit of the strategy and of the like-for-like baseline, on the strategy's trade dates.

    Both stake STAKE_IDR per trade, after the same cost. The baseline is "the same number of trades,
    same hold, entered on random days": after k trades it has made k x (any-day mean - cost) x stake
    (the mean, not a sampled path, so it is the expectation the strategy has to beat).
    Returns (strategy points, baseline points, per-trade rows of (trade, net return, running total)).
    """
    cost = cost_pct / 100
    run, strat, rows = 0.0, [(trades[0].entry_date, 0.0)], []
    base = [(trades[0].entry_date, 0.0)]
    per_trade_base = 0.0 if null_mean is None else (null_mean - cost) * STAKE_IDR
    for k, t in enumerate(trades, start=1):
        net = t.gross_return - cost
        run += net * STAKE_IDR
        strat.append((t.exit_date, run))
        base.append((t.exit_date, per_trade_base * k))
        rows.append((t, net, run))
    return strat, base, rows


def reproduces_report(cell: R.ResultCell, stats: TradeStats | None) -> bool | None:
    """Does the re-derived gross average match what the harness printed for this cell?"""
    if stats is None:
        return None
    reported = cell.gross_edge_bps
    if reported is None and cell.net_edge_bps is not None and cell.hurdle_bps is not None:
        reported = cell.net_edge_bps + cell.hurdle_bps
    if reported is None:
        return None
    return abs(stats.gross_avg_pct * 100 - reported) <= REPRODUCTION_TOLERANCE_BPS


def is_highlighted(cell: R.ResultCell) -> bool:
    """Passed any gate (the cost gate or later) — single-name cells only — or is a famous one."""
    if cell.key in FAMOUS:
        return True
    return cell.is_single_name and cell.gate in (
        R.GATE_PASSED_ALL,
        R.GATE_FDR,
        R.GATE_NULL,
        R.GATE_OOS,
    )


def needs_trades(cell: R.ResultCell) -> bool:
    """Cells worth re-deriving trades for: every single-name cell the harness actually scored."""
    return cell.is_single_name and cell.gate in (
        R.GATE_PASSED_ALL,
        R.GATE_COST,
        R.GATE_FDR,
        R.GATE_NULL,
        R.GATE_OOS,
    )


def build_rows(
    run: R.RunResults,
    exported: Mapping[tuple[str, str, int], CellTrades],
) -> list[ScoreRow]:
    """One row per reported cell; stats and verdicts filled where trades were exported."""
    rows: list[ScoreRow] = []
    for cell in run.cells:
        stats = None
        ct = exported.get(cell.key)
        cost = cell.hurdle_bps if cell.hurdle_bps is not None else run.symbol_hurdles.get(cell.symbol)
        if ct is not None and cost is not None:
            closes = dict(zip(ct.bars["date"].astype(str), ct.bars["close"].astype(float)))
            stats = trade_stats(
                ct.trades, cost, closes, ct.always_long_return, ct.null_returns, hold=ct.hold
            )
        verdict = verdict_for(
            cell,
            avg_net_pct=stats.net_avg_pct if stats else None,
            avg_net_without_best_pct=stats.net_without_best_pct if stats else None,
            n_trades=stats.n_trades if stats else None,
            edge_vs_null_pct=stats.edge_vs_null_pct if stats else None,
            t_stat=stats.t_stat if stats else None,
        )
        rows.append(
            ScoreRow(
                cell=cell,
                stats=stats,
                verdict=verdict,
                famous=cell.key in FAMOUS,
                highlighted=is_highlighted(cell),
                reproduces=reproduces_report(cell, stats),
                slug=slug_for(cell),
            )
        )
    return rows
