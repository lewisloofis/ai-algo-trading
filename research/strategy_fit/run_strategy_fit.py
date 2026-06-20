"""
strategy_fit/run_strategy_fit.py — Grid runner and main entrypoint for plan-10.

Usage:
    python -m strategy_fit.run_strategy_fit [--source fixture|live] [--outdir DIR]

Flow:
    1. Load universe from DataSource (fixture or live).
    2. For each symbol: load daily_history, compute structural friction floor.
    3. Define strategy families grid (deployable band: ≤2 trading-day holds).
    4. For each (symbol, family) cell:
         a. Reject if hold > MAX_HOLD_TRADING_DAYS (capital_blocked).
         b. Run walk_forward_cell → WalkForwardResult.
         c. If decayed: record as decayed, skip cost gate.
         d. Apply cost gate at worst-case entry_dow=Thursday.
    5. Collect all CellResult objects.
    6. Apply Benjamini-Hochberg FDR at α=0.10 across tested cells.
    7. Emit research/out/strategy_portfolio.md.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Sequence

import numpy as np
import pandas as pd

from strategy_fit.harness import (
    MAX_HOLD_TRADING_DAYS,
    SHAPE_CROSS_SECTIONAL,
    SHAPE_EVENT_DRIVEN,
    SHAPE_INTRADAY_SAME_DAY,
    SHAPE_PAIRS,
    SHAPE_SINGLE_NAME,
    CellResult,
    StrategyFamily,
    reject_horizon_exceeded,
)
from strategy_fit.financing import best_feasible_hurdle_bps, full_pass_hurdle_bps, passes_gate
from strategy_fit.families_single_name import (
    daily_momentum_signal,
    mean_reversion_signal,
    volume_breakout_signal,
    run_single_name_family,
    ROUND_TRIP_BPS,
)
from strategy_fit.families_event import run_event_family, EVENT_DETECTORS
from strategy_fit.families_cross_sectional import run_cross_sectional_family
from strategy_fit.families_pairs import run_pairs_family
from strategy_fit.families_intraday import (
    run_orb_family,
    run_intraday_momentum_family,
    run_intraday_mean_reversion_family,
)
from strategy_fit.walk_forward import (
    walk_forward_cell,
    WalkForwardResult,
    DEFAULT_FIT_WINDOW,
    DEFAULT_TEST_WINDOW,
    DEFAULT_MIN_WINDOWS,
)
from strategy_fit.impact import compute_impact_bps_from_df
from strategy_fit.intraday_walk_forward import (
    walk_forward_intraday,
    IntradayWalkForwardResult,
)
from strategy_fit.report import emit_strategy_portfolio

try:
    from screener.metrics import min_spread_bps as _min_spread_bps
except ImportError:
    def _min_spread_bps(price: float) -> float:  # type: ignore[misc]
        """Fallback tick-spread from IDX fraksi harga."""
        if price < 200:
            tick = 1
        elif price < 500:
            tick = 2
        elif price < 2_000:
            tick = 5
        elif price < 5_000:
            tick = 10
        else:
            tick = 25
        return tick / price * 1e4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORST_CASE_ENTRY_DOW: int = 3       # Thursday — maximises financing cost
FDR_ALPHA: float = 0.10             # Benjamini-Hochberg FDR level
MODELLED_IMPACT_BPS: float = 0.0
# Impact for daily / event / cross-sectional / pairs cells: still 0 (plan-13
# will extend).  Intraday cells now compute per-symbol impact from the actual
# intraday DataFrame via strategy_fit.impact.compute_impact_bps_from_df.

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.join(SCRIPT_DIR, "..", "out")

HISTORY_START = "2024-01-01"
HISTORY_END = "2026-12-31"

# Strategy family catalogue — deployable band only (≤2 trading-day holds).
# Families tagged data_blocked/deferred are tracked in the coverage table but
# not run (plan-10 §4).
SINGLE_NAME_SIGNAL_FNS: dict[str, "callable"] = {
    "daily_momentum": daily_momentum_signal,
    "mean_reversion": mean_reversion_signal,
    "volume_breakout": volume_breakout_signal,
}

SINGLE_NAME_HOLDS: list[int] = [1, 2]   # ≤2 trading-day horizon ceiling

EVENT_FAMILY_NAMES: list[str] = list(EVENT_DETECTORS.keys())
EVENT_HOLDS: list[int] = [1, 2]

# Intraday same-day families (hold=0).
INTRADAY_FAMILIES: dict[str, "callable"] = {
    "orb": run_orb_family,
    "intraday_momentum": run_intraday_momentum_family,
    "intraday_mean_reversion": run_intraday_mean_reversion_family,
}


# ---------------------------------------------------------------------------
# Benjamini-Hochberg FDR
# ---------------------------------------------------------------------------

def apply_bh_fdr(p_values: list[float], alpha: float = FDR_ALPHA) -> list[bool]:
    """
    Apply Benjamini-Hochberg FDR correction.

    Parameters
    ----------
    p_values : list[float]
        p-values for each hypothesis (original order preserved in output).
    alpha : float
        FDR level (default 0.10).

    Returns
    -------
    list[bool]
        True for each hypothesis that survives BH correction.
    """
    if not p_values:
        return []

    n = len(p_values)
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    accepted = [False] * n

    # Find the largest i (1-indexed) where p[i] <= alpha * i / n.
    last_accepted = -1
    for rank, (orig_idx, p) in enumerate(indexed):
        threshold = alpha * (rank + 1) / n
        if p <= threshold:
            last_accepted = rank

    # All hypotheses up to and including last_accepted are accepted.
    if last_accepted >= 0:
        for rank in range(last_accepted + 1):
            orig_idx = indexed[rank][0]
            accepted[orig_idx] = True

    return accepted


# ---------------------------------------------------------------------------
# Per-cell runner
# ---------------------------------------------------------------------------

def _run_single_name_cell(
    symbol: str,
    df: pd.DataFrame,
    family: StrategyFamily,
    min_edge_to_clear_bps: float,
) -> CellResult:
    """Run one (symbol, single_name_family) cell end-to-end."""
    # 1. Horizon ceiling check.
    rejection = reject_horizon_exceeded(family)
    if rejection:
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=rejection["status"],
            skip_reason=rejection["skip_reason"],
        )

    signal_fn = SINGLE_NAME_SIGNAL_FNS[family.name]

    # 2. Walk-forward persistence check.
    wf: WalkForwardResult = walk_forward_cell(
        df=df,
        signal_fn=signal_fn,
        horizon_trading_days=family.horizon_trading_days,
        fit_window=DEFAULT_FIT_WINDOW,
        test_window=DEFAULT_TEST_WINDOW,
        min_windows=DEFAULT_MIN_WINDOWS,
    )

    decay_status = "decayed" if wf.is_decayed else "persistent"

    if wf.is_decayed:
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status="decayed",
            skip_reason=f"decayed: mean_sharpe={wf.mean_sharpe:.3f}, mean_hr={wf.mean_hit_rate:.3f}",
            decay_status=decay_status,
            decay_metric=wf.mean_sharpe,
        )

    # 3. Signal-level backtest metrics.
    result = run_single_name_family(
        df=df,
        signal_fn=signal_fn,
        symbol=symbol,
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        family_name=family.name,
        horizon_trading_days=family.horizon_trading_days,
    )

    if result["status"] != "tested":
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=result["status"],
            skip_reason=result["skip_reason"],
            hit_rate=result.get("hit_rate"),
            eff_n=result.get("eff_n"),
            decay_status=decay_status,
            decay_metric=wf.mean_sharpe,
        )

    # 4. Cost gate at best-feasible entry_dow (cheapest financing day).
    # Gate on GROSS edge vs the full cost stack (floor + impact + financing).
    # The family's net_edge_bps already subtracted the friction floor, so we
    # must NOT use it for the gate — that would count the floor twice.
    # net_after_cost = gross - hurdle: > 0 for survivors, ≤ 0 for rejections.
    hurdle, entry_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        modelled_impact_bps=MODELLED_IMPACT_BPS,
        hold_trading_days=family.horizon_trading_days,
    )
    gross_edge = result["gross_edge_bps"] or 0.0
    cost_pass = gross_edge > hurdle
    net_after_cost = gross_edge - hurdle

    return CellResult(
        symbol=symbol,
        family_name=family.name,
        shape=family.shape,
        horizon_trading_days=family.horizon_trading_days,
        status="tested",
        hit_rate=result["hit_rate"],
        eff_n=result["eff_n"],
        gross_edge_bps=result["gross_edge_bps"],
        net_edge_bps=net_after_cost,
        p_value=result["p_value"],
        decay_status=decay_status,
        decay_metric=wf.mean_sharpe,
        passes_cost_gate=cost_pass,
        min_edge_hurdle_bps=hurdle,
        best_entry_dow=entry_dow,
    )


def _run_event_cell(
    hist_dict: dict[str, pd.DataFrame],
    family: StrategyFamily,
    universe_min_edge_map: dict[str, float],
) -> CellResult:
    """Run one (pooled, event_family) cell end-to-end."""
    symbol = "(pooled)"

    rejection = reject_horizon_exceeded(family)
    if rejection:
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=rejection["status"],
            skip_reason=rejection["skip_reason"],
        )

    result = run_event_family(
        hist_dict=hist_dict,
        event_detector_name=family.name,
        horizon_trading_days=family.horizon_trading_days,
        universe_min_edge_map=universe_min_edge_map,
    )

    if result["status"] != "tested":
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=result["status"],
            skip_reason=result["skip_reason"],
            eff_n=result.get("eff_n"),
        )

    # Use the median min_edge as the friction floor for the pooled event family.
    # Gate on GROSS edge vs the full cost stack (floor + impact + financing).
    # The family's net_edge_bps already subtracted ROUND_TRIP_BPS, so we
    # must NOT use it for the gate — that would count the floor twice.
    median_min_edge = float(np.median(list(universe_min_edge_map.values()))) if universe_min_edge_map else ROUND_TRIP_BPS
    hurdle, entry_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=median_min_edge,
        modelled_impact_bps=MODELLED_IMPACT_BPS,
        hold_trading_days=family.horizon_trading_days,
    )
    gross_edge = result["gross_edge_bps"] or 0.0
    cost_pass = gross_edge > hurdle
    net_after_cost = gross_edge - hurdle

    return CellResult(
        symbol=symbol,
        family_name=family.name,
        shape=family.shape,
        horizon_trading_days=family.horizon_trading_days,
        status="tested",
        hit_rate=result["hit_rate"],
        eff_n=result["eff_n"],
        gross_edge_bps=result["gross_edge_bps"],
        net_edge_bps=net_after_cost,
        p_value=result["p_value"],
        passes_cost_gate=cost_pass,
        min_edge_hurdle_bps=hurdle,
        best_entry_dow=entry_dow,
    )


def _run_cross_sectional_cell(
    hist_dict: dict[str, pd.DataFrame],
    family: StrategyFamily,
    universe_min_edge_map: dict[str, float],
) -> CellResult:
    """Run one cross-sectional family cell end-to-end."""
    symbol = "(cross_sectional)"

    rejection = reject_horizon_exceeded(family)
    if rejection:
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=rejection["status"],
            skip_reason=rejection["skip_reason"],
        )

    result = run_cross_sectional_family(
        hist_dict=hist_dict,
        horizon_trading_days=family.horizon_trading_days,
        universe_min_edge_map=universe_min_edge_map,
    )

    if result["status"] != "tested":
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=result["status"],
            skip_reason=result["skip_reason"],
            eff_n=result.get("eff_n"),
        )

    # Cost-stack floor: median min_edge (cross-sectional L/S — documented in
    # families_cross_sectional.py; uses median, not sum-of-legs).
    # Gate on GROSS edge vs the full cost stack (floor + impact + financing).
    # The family's net_edge_bps already subtracted cs_min_edge, so we must NOT
    # use it for the gate — that would count the floor twice.
    median_min_edge = float(np.median(list(universe_min_edge_map.values()))) if universe_min_edge_map else ROUND_TRIP_BPS
    hurdle, entry_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=median_min_edge,
        modelled_impact_bps=MODELLED_IMPACT_BPS,
        hold_trading_days=family.horizon_trading_days,
    )
    gross_edge = result["gross_edge_bps"] or 0.0
    cost_pass = gross_edge > hurdle
    net_after_cost = gross_edge - hurdle

    return CellResult(
        symbol=symbol,
        family_name=family.name,
        shape=family.shape,
        horizon_trading_days=family.horizon_trading_days,
        status="tested",
        hit_rate=result["hit_rate"],
        eff_n=result["eff_n"],
        gross_edge_bps=result["gross_edge_bps"],
        net_edge_bps=net_after_cost,
        p_value=result["p_value"],
        passes_cost_gate=cost_pass,
        min_edge_hurdle_bps=hurdle,
        best_entry_dow=entry_dow,
    )


def _run_pairs_cell(
    hist_dict: dict[str, pd.DataFrame],
    family: StrategyFamily,
    universe_min_edge_map: dict[str, float],
) -> CellResult:
    """Run one pairs / cointegration family cell end-to-end."""
    symbol = "(pairs)"

    rejection = reject_horizon_exceeded(family)
    if rejection:
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=rejection["status"],
            skip_reason=rejection["skip_reason"],
        )

    result = run_pairs_family(
        hist_dict=hist_dict,
        horizon_trading_days=family.horizon_trading_days,
        universe_min_edge_map=universe_min_edge_map,
    )

    if result["status"] not in ("tested",):
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=result["status"],
            skip_reason=result["skip_reason"],
            eff_n=result.get("eff_n"),
        )

    # Cost-stack floor: sum of both legs' min_edge (each leg is a separate order).
    # Documented in families_pairs.py; use sum, not median, for pairs.
    # Gate on GROSS edge vs the full cost stack (floor + impact + financing).
    # The family's net_edge_bps already subtracted pair_min_edge, so we must NOT
    # use it for the gate — that would count the floor twice.
    if universe_min_edge_map:
        edges = sorted(universe_min_edge_map.values())
        # Use the two smallest friction floors as a proxy for the cheapest pair legs.
        pair_min_edge = float(sum(edges[:2])) if len(edges) >= 2 else float(sum(edges))
    else:
        pair_min_edge = ROUND_TRIP_BPS * 2.0
    hurdle, entry_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=pair_min_edge,
        modelled_impact_bps=MODELLED_IMPACT_BPS,
        hold_trading_days=family.horizon_trading_days,
    )
    gross_edge = result["gross_edge_bps"] or 0.0
    cost_pass = gross_edge > hurdle
    net_after_cost = gross_edge - hurdle

    return CellResult(
        symbol=symbol,
        family_name=family.name,
        shape=family.shape,
        horizon_trading_days=family.horizon_trading_days,
        status="tested",
        hit_rate=result["hit_rate"],
        eff_n=result["eff_n"],
        gross_edge_bps=result["gross_edge_bps"],
        net_edge_bps=net_after_cost,
        p_value=result["p_value"],
        passes_cost_gate=cost_pass,
        min_edge_hurdle_bps=hurdle,
        best_entry_dow=entry_dow,
    )


def _run_intraday_cell(
    symbol: str,
    intraday_df: pd.DataFrame,
    family: StrategyFamily,
    family_fn: "callable",
    min_edge_to_clear_bps: float,
) -> CellResult:
    """Run one (symbol, intraday_family) cell end-to-end.

    No horizon check needed: hold=0 is always within MAX_HOLD_TRADING_DAYS=2.

    Two additional validations vs. the prior version:

    1. Market-impact model (plan-10 §9):
       Per-symbol round-trip slippage is computed from the actual intraday
       DataFrame via impact.compute_impact_bps_from_df (square-root Almgren
       law).  This replaces the global MODELLED_IMPACT_BPS=0 for intraday
       cells, so the cost gate now charges realistic slippage.  Daily / event /
       cross-sectional / pairs cells still use MODELLED_IMPACT_BPS=0 — plan-13
       will extend the impact model to those shapes.

    2. OOS persistence check (plan-10 §9):
       A single chronological IS/OOS split (60/40) is run to check whether the
       intraday edge persists out-of-sample.  Survivors with oos_persistent=False
       are NOT removed from the report (data is thin — only ~55 trading days);
       they are annotated for human review.  See intraday_walk_forward.py.
    """
    # 1. Per-symbol market impact (replaces MODELLED_IMPACT_BPS=0 for intraday).
    intraday_impact_bps = compute_impact_bps_from_df(intraday_df)
    logger.debug("intraday impact for %s/%s: %.1f bps", symbol, family.name, intraday_impact_bps)

    # 2. OOS persistence split.
    wf_intraday: IntradayWalkForwardResult = walk_forward_intraday(intraday_df, family_fn)

    result = family_fn(intraday_df)

    if result["status"] != "tested":
        return CellResult(
            symbol=symbol,
            family_name=family.name,
            shape=family.shape,
            horizon_trading_days=family.horizon_trading_days,
            status=result["status"],
            skip_reason=result.get("skip_reason"),
            hit_rate=result.get("hit_rate"),
            eff_n=result.get("eff_n"),
            modelled_impact_bps=intraday_impact_bps,
        )

    # Cost gate: for hold=0, financing=0, hurdle = min_edge + impact.
    hurdle, entry_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        modelled_impact_bps=intraday_impact_bps,
        hold_trading_days=0,
    )
    gross_edge = result["gross_edge_bps"] or 0.0
    cost_pass = gross_edge > hurdle
    net_after_cost = gross_edge - hurdle

    return CellResult(
        symbol=symbol,
        family_name=family.name,
        shape=family.shape,
        horizon_trading_days=0,
        status="tested",
        hit_rate=result["hit_rate"],
        eff_n=result["eff_n"],
        gross_edge_bps=result["gross_edge_bps"],
        net_edge_bps=net_after_cost,
        p_value=result["p_value"],
        passes_cost_gate=cost_pass,
        min_edge_hurdle_bps=hurdle,
        best_entry_dow=entry_dow,
        modelled_impact_bps=intraday_impact_bps,
        oos_edge_bps=wf_intraday.oos_edge_bps,
        oos_hit_rate=wf_intraday.oos_hit_rate,
        oos_eff_n=wf_intraday.oos_eff_n,
        oos_decay_ratio=wf_intraday.decay_ratio,
        oos_persistent=wf_intraday.is_persistent,
    )


# ---------------------------------------------------------------------------
# Grid runner
# ---------------------------------------------------------------------------

def run_grid(
    ds: "object",
    start: str = HISTORY_START,
    end: str = HISTORY_END,
) -> list[CellResult]:
    """
    Run the full (symbol × family × horizon) strategy-fit grid.

    Parameters
    ----------
    ds : DataSource
        A DataSource implementing funding_universe() and daily_history().
    start, end : str
        Date range for daily history.

    Returns
    -------
    list[CellResult]
        All cell results (tested + skipped + blocked).
    """
    universe = ds.funding_universe()
    logger.info("Universe: %s", sorted(universe))

    # Load history for all symbols.
    hist_dict: dict[str, pd.DataFrame] = {}
    min_edge_map: dict[str, float] = {}
    for symbol in sorted(universe):
        try:
            df = ds.daily_history(symbol, start, end)
        except Exception as exc:
            logger.warning("Could not load history for %s: %s", symbol, exc)
            continue
        if len(df) < 10:
            logger.warning("Skipping %s: too few rows (%d)", symbol, len(df))
            continue
        hist_dict[symbol] = df

        # Compute structural friction floor from plan-09.
        last_close = float(df["close"].iloc[-1])
        spread_bps = _min_spread_bps(last_close)
        min_edge_map[symbol] = spread_bps + ROUND_TRIP_BPS

    all_cells: list[CellResult] = []

    # --- Single-name families ---
    for symbol, df in hist_dict.items():
        for family_name, _signal_fn in SINGLE_NAME_SIGNAL_FNS.items():
            for hold in SINGLE_NAME_HOLDS:
                family = StrategyFamily(
                    name=family_name,
                    shape=SHAPE_SINGLE_NAME,
                    horizon_trading_days=hold,
                )
                cell = _run_single_name_cell(
                    symbol=symbol,
                    df=df,
                    family=family,
                    min_edge_to_clear_bps=min_edge_map.get(symbol, ROUND_TRIP_BPS),
                )
                all_cells.append(cell)

    # --- Event-driven families (pooled) ---
    for event_name in EVENT_FAMILY_NAMES:
        for hold in EVENT_HOLDS:
            family = StrategyFamily(
                name=event_name,
                shape=SHAPE_EVENT_DRIVEN,
                horizon_trading_days=hold,
            )
            cell = _run_event_cell(
                hist_dict=hist_dict,
                family=family,
                universe_min_edge_map=min_edge_map,
            )
            all_cells.append(cell)

    # --- Cross-sectional family ---
    for hold in [1, 2]:
        family = StrategyFamily(
            name="cross_sectional_momentum",
            shape=SHAPE_CROSS_SECTIONAL,
            horizon_trading_days=hold,
        )
        cell = _run_cross_sectional_cell(
            hist_dict=hist_dict,
            family=family,
            universe_min_edge_map=min_edge_map,
        )
        all_cells.append(cell)

    # --- Pairs / cointegration family ---
    for hold in [1, 2]:
        family = StrategyFamily(
            name="pairs_cointegration",
            shape=SHAPE_PAIRS,
            horizon_trading_days=hold,
        )
        cell = _run_pairs_cell(
            hist_dict=hist_dict,
            family=family,
            universe_min_edge_map=min_edge_map,
        )
        all_cells.append(cell)

    # --- Intraday same-day families ---
    for symbol in sorted(hist_dict.keys()):
        try:
            intraday_df = ds.intraday_history(symbol)
        except (NotImplementedError, AttributeError):
            logger.info(
                "intraday_history not available for %s (source: %s)",
                symbol, type(ds).__name__,
            )
            continue
        except Exception as exc:
            logger.warning("intraday_history failed for %s: %s", symbol, exc)
            continue
        if intraday_df is None or len(intraday_df) == 0:
            logger.warning("No intraday data for %s", symbol)
            continue
        for family_name, family_fn in INTRADAY_FAMILIES.items():
            family = StrategyFamily(
                name=family_name,
                shape=SHAPE_INTRADAY_SAME_DAY,
                horizon_trading_days=0,
            )
            cell = _run_intraday_cell(
                symbol=symbol,
                intraday_df=intraday_df,
                family=family,
                family_fn=family_fn,
                min_edge_to_clear_bps=min_edge_map.get(symbol, ROUND_TRIP_BPS),
            )
            all_cells.append(cell)

    # --- Add a deferred >2-day hold entry per family for coverage ---
    for family_name in SINGLE_NAME_SIGNAL_FNS:
        family = StrategyFamily(
            name=family_name,
            shape=SHAPE_SINGLE_NAME,
            horizon_trading_days=5,  # representative deferred hold
        )
        rejection = reject_horizon_exceeded(family)
        if rejection:
            for symbol in sorted(hist_dict.keys()):
                all_cells.append(
                    CellResult(
                        symbol=symbol,
                        family_name=family_name,
                        shape=SHAPE_SINGLE_NAME,
                        horizon_trading_days=5,
                        status="skipped_horizon",
                        skip_reason=rejection["skip_reason"],
                    )
                )

    return all_cells


# ---------------------------------------------------------------------------
# FDR application on grid results
# ---------------------------------------------------------------------------

def apply_fdr_to_grid(cells: list[CellResult], alpha: float = FDR_ALPHA) -> list[CellResult]:
    """
    Apply BH FDR correction across all tested, cost-gate-passing cells.

    Returns a new list with fdr_accepted populated (immutable — new CellResult).
    """
    # Only apply FDR to cells that were fully tested AND passed the cost gate.
    # Bug 3 fix: FDR correction must be applied only across hypotheses that
    # cleared the economic gate; including cost-gate failures dilutes the BH
    # correction and can spuriously label failed cells as FDR-accepted.
    testable_indices = [
        i for i, c in enumerate(cells)
        if c.status == "tested" and c.p_value is not None and c.passes_cost_gate is True
    ]
    p_values = [cells[i].p_value for i in testable_indices]  # type: ignore[misc]
    bh_accepted = apply_bh_fdr(p_values, alpha=alpha)

    # Build a map from original index → BH decision.
    fdr_map: dict[int, bool] = {
        testable_indices[j]: bh_accepted[j]
        for j in range(len(testable_indices))
    }

    # Reconstruct immutable CellResult list with fdr_accepted populated.
    updated: list[CellResult] = []
    for i, c in enumerate(cells):
        if i in fdr_map:
            updated.append(
                CellResult(
                    symbol=c.symbol,
                    family_name=c.family_name,
                    shape=c.shape,
                    horizon_trading_days=c.horizon_trading_days,
                    status=c.status,
                    skip_reason=c.skip_reason,
                    hit_rate=c.hit_rate,
                    eff_n=c.eff_n,
                    gross_edge_bps=c.gross_edge_bps,
                    net_edge_bps=c.net_edge_bps,
                    p_value=c.p_value,
                    decay_status=c.decay_status,
                    decay_metric=c.decay_metric,
                    passes_cost_gate=c.passes_cost_gate,
                    min_edge_hurdle_bps=c.min_edge_hurdle_bps,
                    best_entry_dow=c.best_entry_dow,
                    fdr_accepted=fdr_map[i],
                    modelled_impact_bps=c.modelled_impact_bps,
                    oos_edge_bps=c.oos_edge_bps,
                    oos_hit_rate=c.oos_hit_rate,
                    oos_eff_n=c.oos_eff_n,
                    oos_decay_ratio=c.oos_decay_ratio,
                    oos_persistent=c.oos_persistent,
                )
            )
        else:
            updated.append(c)
    return updated


# ---------------------------------------------------------------------------
# Public API for tests
# ---------------------------------------------------------------------------

def run_strategy_fit(
    ds: "object",
    outdir: str = DEFAULT_OUTDIR,
    start: str = HISTORY_START,
    end: str = HISTORY_END,
    is_synthetic: bool = True,
) -> str:
    """
    Run the full strategy-fit pipeline and emit strategy_portfolio.md.

    Parameters
    ----------
    ds : DataSource
        Offline or live DataSource.
    outdir : str
        Directory for the output report.
    start, end : str
        History date range.
    is_synthetic : bool
        Passed through to emit_strategy_portfolio.  True (default) emits
        the SYNTHETIC DATA banner; False emits the LIVE DATA provenance line.

    Returns
    -------
    str
        Path to the written strategy_portfolio.md.
    """
    cells = run_grid(ds=ds, start=start, end=end)
    cells = apply_fdr_to_grid(cells)
    path = emit_strategy_portfolio(cells=cells, outdir=outdir, is_synthetic=is_synthetic)
    return path


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Plan-10 strategy fit & portfolio runner."
    )
    parser.add_argument(
        "--source",
        choices=["fixture", "live"],
        default="fixture",
        help="Data source: 'fixture' (offline, no token) or 'live' (needs SB_BEARER_TOKEN).",
    )
    parser.add_argument(
        "--outdir",
        default=DEFAULT_OUTDIR,
        help=f"Output directory for strategy_portfolio.md (default: {DEFAULT_OUTDIR}).",
    )
    parser.add_argument(
        "--start",
        default=HISTORY_START,
        help="History start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end",
        default=HISTORY_END,
        help="History end date (YYYY-MM-DD).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.source == "fixture":
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        is_synthetic = True
    else:
        try:
            from screener.screen_universe import LiveDataSource
            ds = LiveDataSource()
        except ImportError as exc:
            logger.error("LiveDataSource not available: %s", exc)
            sys.exit(1)
        is_synthetic = False

    path = run_strategy_fit(
        ds=ds,
        outdir=args.outdir,
        start=args.start,
        end=args.end,
        is_synthetic=is_synthetic,
    )
    print(f"strategy_portfolio.md written to: {path}")


if __name__ == "__main__":
    main()
