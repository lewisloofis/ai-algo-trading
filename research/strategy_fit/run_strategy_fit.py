"""
strategy_fit/run_strategy_fit.py — Grid runner and main entrypoint for plan-10.

Usage:
    python -m strategy_fit.run_strategy_fit [--source fixture|live] [--outdir DIR]

Flow:
    1. Load universe from DataSource (fixture or live).
    2. For each symbol: load daily_history, compute structural friction floor.
    3. Define strategy families grid (holds 1, 2, 5, 20, 60 — ADR-0015).
    4. For each (symbol, family) cell:
         a. Reject if hold > MAX_HOLD_TRADING_DAYS (capital_blocked).
         b. Run walk_forward_cell → WalkForwardResult.
         c. If decayed: record as decayed, skip cost gate.
         d. Apply cost gate at the best-feasible (cheapest-financing) entry
            weekday — the allocator chooses when to enter.  See
            financing.best_feasible_hurdle_bps and ADR-0014.
    5. Collect all CellResult objects.
    6. Apply Benjamini-Hochberg FDR at α=0.10 across tested cells.
    7. Emit research/out/strategy_portfolio.md.
"""

from __future__ import annotations

import argparse
import functools
import dataclasses
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
from strategy_fit.impact import (
    IMPACT_BASES,
    IMPACT_BASIS_LEGACY_FULL_HISTORY,
    IMPACT_BASIS_TRAILING,
    POSITION_NOTIONAL_IDR,
    TrailingImpactModel,
    validate_impact_basis,
    compute_impact_bps_from_daily_df,
    compute_impact_bps_from_df,
    resolve_impact_coeff_from_daily_df,
    resolve_impact_coeff_from_df,
)
from strategy_fit.intraday_walk_forward import (
    walk_forward_intraday,
    IntradayWalkForwardResult,
)
from strategy_fit.daily_walk_forward import (
    walk_forward_daily,
    DailyWalkForwardResult,
)
from strategy_fit.report import emit_strategy_portfolio
from lib.market_calendar import CALENDAR as _CALENDAR
from lib.stats import MIN_EFF_N

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

# (A WORST_CASE_ENTRY_DOW constant lived here, pinned to Thursday.  It was
#  unused, and ADR-0014 makes it wrong besides: the costliest 1-day-hold entry
#  is Wednesday, whose settlement window straddles the weekend.  The gate uses
#  financing.best_feasible_hurdle_bps, which sweeps every weekday.)
FDR_ALPHA: float = 0.10             # Benjamini-Hochberg FDR level
MODELLED_IMPACT_BPS: float = 0.0
# Impact for event / cross-sectional / pairs cells: still 0, and those cells
# therefore still leave impact_priced=False and cannot reach PASS.  Both
# per-symbol shapes now price impact from their own bars:
#   intraday → impact.compute_impact_bps_from_df       (1-min bars)
#   daily    → impact.compute_impact_bps_from_daily_df (daily bars, plan 13)
# Note cross_sectional and pairs are separately long_short_infeasible, so the
# only shape left genuinely awaiting an impact model is event_driven (pooled).

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.join(SCRIPT_DIR, "..", "out")

HISTORY_START = "2024-01-01"
HISTORY_END = "2026-12-31"

# Strategy family catalogue.
#
# Holds span the short band (1, 2) and the long band (5, 20, 60) opened by
# ADR-0015. The ≤2-day ceiling came from ADR-0011's T+2 leverage sleeve; owned
# capital carries no tenor clock, so the harness no longer rejects a family on
# horizon grounds. The ceiling had to be removed from BOTH places to be gone:
# lifting it in the capital model alone left the long cells un-enumerated
# rather than blocked, which reads as "not applicable" instead of "untested".
#
# 20 and 60 are where the long-only 12-1 monthly momentum tilt lives — the most
# attack-resistant candidate this project has produced. Note that a long hold
# spends effective-N fast: after de-overlap, a 60-day hold over the default
# 2024-01-01..2026-12-31 window yields roughly a dozen independent trades per
# name against MIN_EFF_N=30, so most long cells will report insufficient_n
# until the history window is widened. That is a real finding, not a bug, and
# widening the window trades it for survivorship exposure (ADR-0015, plan 18
# A-3): the funding endpoints return currently-tradeable names by construction.
# Families tagged data_blocked/deferred are tracked in the coverage table but
# not run (plan-10 §4).
SINGLE_NAME_SIGNAL_FNS: dict[str, "callable"] = {
    "daily_momentum": daily_momentum_signal,
    "mean_reversion": mean_reversion_signal,
    "volume_breakout": volume_breakout_signal,
}

SINGLE_NAME_HOLDS: list[int] = [1, 2, 5, 20, 60]

EVENT_FAMILY_NAMES: list[str] = list(EVENT_DETECTORS.keys())
EVENT_HOLDS: list[int] = [1, 2, 5, 20, 60]

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
    use_calibrated: bool = False,
    entry: str | None = None,
    impact_basis: str = IMPACT_BASIS_TRAILING,
    impact_model: TrailingImpactModel | None = None,
) -> CellResult:
    """Run one (symbol, single_name_family) cell end-to-end.

    ``impact_basis`` (impact.IMPACT_BASES): ``trailing_point_in_time`` (default)
    prices each trade from 60-bar ADV/vol windows ending at its signal day and
    charges the mean per-trade impact; ``legacy_full_history`` reproduces the
    old single per-cell number (full-history median ADV and vol) and exists only
    so pre-2026-10-14 reports can be regenerated.  ``impact_model`` lets the
    grid share one model (and its cache) across a symbol's cells.

    ``entry`` selects the entry convention (strategy_fit/entry_convention.py);
    None = the live-feasible default (next-session close). See harness defect D1.

    Market impact (plan 13)
    -----------------------
    Daily cells are now priced by strategy_fit.impact, exactly as intraday
    cells are, and impact_priced=True is set on every path that actually ran
    the model.  Before plan 13 this shape used the global MODELLED_IMPACT_BPS
    =0 and left impact_priced=False, which is why 17 FDR-accepted daily cells
    were blocked as `impact_not_priced` rather than judged: an assumed 0.0
    impact is not a measurement, and W2.1 made that distinction blocking.

    The formula is identical to the intraday path — the square-root law
    prices ONE execution, and the ``2 *`` factor is the entry leg plus the
    exit leg.  A 2-day hold does not double impact; holding is not executing.
    Only the estimation of daily_vol_bps / adv_notional_idr differs, because
    these bars are already one row per day.

    The coefficient is borrowed, and that is a real caveat rather than a
    detail: impact_coeffs.json was calibrated by plan 15 from tick fills on
    INTRADAY executions for seven large-cap names.  Symbols outside that
    sample fall through to global_default=1.0, the conservative
    high-friction end.  See impact.DAILY_COEFF_IS_BORROWED_NOTE.
    """
    # 1. Horizon ceiling check.  Deliberately BEFORE impact is priced: a
    # cell beyond the ADR-0011 horizon ceiling is capital-blocked and can
    # never be a candidate, so pricing it would be wasted work — and leaving
    # impact_priced=False on it is honest, nothing was measured.
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
    if entry is not None:
        signal_fn = functools.partial(signal_fn, entry=entry)

    # 2. Per-symbol market impact (plan 13; replaces MODELLED_IMPACT_BPS=0).
    # Computed here, before any early return below, so that every cell which
    # reached this point reports a genuinely-priced impact — including cells
    # that go on to be decayed or insufficient_n.  This mirrors
    # _run_intraday_cell, where impact_priced=True is likewise unconditional
    # past this point.
    validate_impact_basis(impact_basis)
    legacy_impact = impact_basis == IMPACT_BASIS_LEGACY_FULL_HISTORY
    if legacy_impact:
        model = None
        daily_impact_bps = compute_impact_bps_from_daily_df(
            df, use_calibrated=use_calibrated, symbol=symbol
        )
        daily_impact_coeff = resolve_impact_coeff_from_daily_df(
            df, use_calibrated=use_calibrated, symbol=symbol
        )
    else:
        model = impact_model or TrailingImpactModel(df, symbol=symbol, use_calibrated=use_calibrated)
        # Provisional (cells with no trades to price: decayed / insufficient_n):
        # the median impact over every day with enough history.  Replaced below
        # by the mean over the actual trades once a cell is scored.
        series, coeffs = model.priced_days(since=str(df["date"].astype(str).min()))
        if len(series) == 0:
            daily_impact_bps = float("nan")
            daily_impact_coeff = None
        else:
            daily_impact_bps = float(np.median(series))
            daily_impact_coeff = float(np.median(coeffs))
    impact_extras = {"impact_basis": impact_basis}
    logger.debug(
        "daily impact for %s/%s: %.1f bps (coeff=%.3f, notional=%.0f, calibrated=%s)",
        symbol, family.name, daily_impact_bps, daily_impact_coeff, POSITION_NOTIONAL_IDR, use_calibrated,
    )

    # 3. Out-of-sample persistence split (plan 13).
    # walk_forward_cell below is a rolling DECAY metric: every window it scores
    # is drawn from the same chronological range the name was selected on, so
    # it cannot answer "does this edge survive outside its selection window".
    # That question is what killed the intraday ORB family, and until now no
    # daily cell had ever been asked it.  walk_forward_daily answers it with a
    # single chronological IS/OOS split.  The two are complementary and both
    # run: decay kills persistently-bad cells, the split catches in-sample-only
    # edges.
    wf_daily: DailyWalkForwardResult = walk_forward_daily(
        df=df,
        signal_fn=signal_fn,
        symbol=symbol,
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        family_name=family.name,
        horizon_trading_days=family.horizon_trading_days,
        entry=entry,
    )

    # 3b. Rolling decay check (distinct from the OOS split above).
    wf: WalkForwardResult = walk_forward_cell(
        df=df,
        signal_fn=signal_fn,
        horizon_trading_days=family.horizon_trading_days,
        fit_window=DEFAULT_FIT_WINDOW,
        test_window=DEFAULT_TEST_WINDOW,
        min_windows=DEFAULT_MIN_WINDOWS,
        entry=entry,
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
            modelled_impact_bps=daily_impact_bps,
            impact_priced=True,
            impact_priced_position_notional_idr=POSITION_NOTIONAL_IDR,
            impact_priced_coeff=daily_impact_coeff,
            **impact_extras,
            oos_eff_n=wf_daily.oos_eff_n,
            oos_split_skip_reason=wf_daily.skip_reason,
        )

    # 4. Signal-level backtest metrics.
    result = run_single_name_family(
        df=df,
        signal_fn=signal_fn,
        symbol=symbol,
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        family_name=family.name,
        horizon_trading_days=family.horizon_trading_days,
        entry=entry,
        impact_model=model,
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
            modelled_impact_bps=daily_impact_bps,
            impact_priced=True,
            impact_priced_position_notional_idr=POSITION_NOTIONAL_IDR,
            impact_priced_coeff=daily_impact_coeff,
            impact_excluded_n=result.get("impact_excluded_n") if model is not None else None,
            **impact_extras,
            oos_eff_n=wf_daily.oos_eff_n,
            oos_split_skip_reason=wf_daily.skip_reason,
        )

    # 5. Cost gate at best-feasible entry_dow (cheapest financing day).
    # Gate on GROSS edge vs the full cost stack (floor + impact + financing).
    # The family's net_edge_bps already subtracted the friction floor, so we
    # must NOT use it for the gate — that would count the floor twice.
    # net_after_cost = gross - hurdle: > 0 for survivors, ≤ 0 for rejections.
    # Point-in-time basis: the per-trade hurdle is floor + impact_i + financing
    # and is linear in impact, so mean(gross_i - hurdle_i) == gross - hurdle(mean
    # impact_i).  The cell therefore charges the MEAN per-trade impact.
    if model is not None:
        per_trade = np.asarray(result["per_trade_impact_bps"], dtype=float)
        daily_impact_bps = float(np.mean(per_trade))
        impact_extras = {**impact_extras, "impact_median_bps": float(np.median(per_trade))}
        daily_impact_coeff = float(
            np.median([model.coeff(d) for d in result["trade_signal_dates"]])
        )
    hurdle, entry_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        modelled_impact_bps=daily_impact_bps,
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
        modelled_impact_bps=daily_impact_bps,
        impact_priced=True,
        impact_priced_position_notional_idr=POSITION_NOTIONAL_IDR,
        impact_priced_coeff=daily_impact_coeff,
        impact_excluded_n=result.get("impact_excluded_n") if model is not None else None,
        **impact_extras,
        null_edge_bps=result.get("null_edge_bps"),
        edge_over_null_bps=result.get("edge_over_null_bps"),
        oos_edge_bps=wf_daily.oos_edge_bps,
        oos_hit_rate=wf_daily.oos_hit_rate,
        oos_eff_n=wf_daily.oos_eff_n,
        oos_decay_ratio=wf_daily.decay_ratio,
        oos_persistent=wf_daily.is_persistent,
        oos_sigma_bps=wf_daily.oos_sigma_bps,
        oos_required_eff_n=wf_daily.required_oos_eff_n,
        oos_underpowered=wf_daily.oos_underpowered,
        oos_split_skip_reason=wf_daily.skip_reason,
    )


def _run_event_cell(
    hist_dict: dict[str, pd.DataFrame],
    family: StrategyFamily,
    universe_min_edge_map: dict[str, float],
    entry: str | None = None,
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
        entry=entry,
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
        null_edge_bps=result.get("null_edge_bps"),
        edge_over_null_bps=result.get("edge_over_null_bps"),
    )


def _run_cross_sectional_cell(
    hist_dict: dict[str, pd.DataFrame],
    family: StrategyFamily,
    universe_min_edge_map: dict[str, float],
    entry: str | None = None,
) -> CellResult:
    """Run one cross-sectional family cell end-to-end (``entry``: D1 convention)."""
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
        entry=entry,
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
    use_calibrated: bool = False,
) -> CellResult:
    """Run one (symbol, intraday_family) cell end-to-end.

    No horizon check needed: hold=0 is within any capital model's ceiling.

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

    impact_priced=True is set unconditionally here (impact is always computed
    for intraday cells, tested or not) — this is what lets report.py's
    compute_verdict distinguish "impact priced at 0" from "impact never
    priced" (W2.1).  oos_required_eff_n / oos_underpowered are threaded
    through from the walk-forward result so the report can distinguish a
    below-hard-floor cell from an honestly UNDERPOWERED one.
    """
    # 1. Per-symbol market impact (replaces MODELLED_IMPACT_BPS=0 for intraday).
    intraday_impact_bps = compute_impact_bps_from_df(
        intraday_df, use_calibrated=use_calibrated, symbol=symbol
    )
    # Provenance (ADR-0017): see the matching comment in _run_single_name_cell.
    intraday_impact_coeff = resolve_impact_coeff_from_df(
        intraday_df, use_calibrated=use_calibrated, symbol=symbol
    )
    logger.debug(
        "intraday impact for %s/%s: %.1f bps (coeff=%.3f, notional=%.0f, calibrated=%s)",
        symbol, family.name, intraday_impact_bps, intraday_impact_coeff, POSITION_NOTIONAL_IDR, use_calibrated,
    )

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
            impact_priced=True,
            impact_priced_position_notional_idr=POSITION_NOTIONAL_IDR,
            impact_priced_coeff=intraday_impact_coeff,
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
        impact_priced=True,
        impact_priced_position_notional_idr=POSITION_NOTIONAL_IDR,
        impact_priced_coeff=intraday_impact_coeff,
        null_edge_bps=result.get("null_edge_bps"),
        edge_over_null_bps=result.get("edge_over_null_bps"),
        oos_edge_bps=wf_intraday.oos_edge_bps,
        oos_hit_rate=wf_intraday.oos_hit_rate,
        oos_eff_n=wf_intraday.oos_eff_n,
        oos_decay_ratio=wf_intraday.decay_ratio,
        oos_persistent=wf_intraday.is_persistent,
        oos_sigma_bps=wf_intraday.oos_sigma_bps,
        oos_required_eff_n=wf_intraday.required_oos_eff_n,
        oos_underpowered=wf_intraday.oos_underpowered,
        oos_split_skip_reason=wf_intraday.skip_reason,
    )


# ---------------------------------------------------------------------------
# Grid runner
# ---------------------------------------------------------------------------

class EmptyUniverseError(RuntimeError):
    """
    Raised when the funding universe resolves to zero symbols.

    A credentials failure must not be able to author a research result. An
    expired ``SB_ACCESS_TOKEN`` makes both emitten endpoints return HTTP 401;
    ``screen_universe`` degrades to an empty intersection rather than raising,
    so without this guard the grid completes over zero cells and emits a
    verdict indistinguishable from a real one.
    """


IMPACT_WARMUP_CALENDAR_DAYS: int = 120
"""Calendar days of bars BEFORE ``start`` fetched for the trailing-impact model
only (>= 60 trading days).  Without it every trade in the first ~20 bars of the
window has no history to price from and is excluded, which silently changes the
scored sample.  Bars dated <= the signal day are point-in-time by construction,
so using pre-``start`` bars is not lookahead; signals/trades still start at
``start``."""


def build_impact_model(
    ds: "object", symbol: str, df: pd.DataFrame, start: str, end: str, use_calibrated: bool
) -> TrailingImpactModel:
    """Trailing-impact model for *symbol*, warmed up with bars before ``start``.
    Falls back to ``df`` alone (trades then excluded loudly when history is
    short) if the warm-up fetch fails."""
    from datetime import date as _d, timedelta as _td

    bars = df
    try:
        warm_start = (_d.fromisoformat(start) - _td(days=IMPACT_WARMUP_CALENDAR_DAYS)).isoformat()
        ext = ds.daily_history(symbol, warm_start, end)
        if len(ext) >= len(df):
            bars = ext
    except Exception as exc:  # noqa: BLE001 - loud, not silent: logged, trades then excluded
        logger.warning("%s: impact warm-up fetch failed (%s); trailing windows start at %s", symbol, exc, start)
    return TrailingImpactModel(bars, symbol=symbol, use_calibrated=use_calibrated)


def run_grid(
    ds: "object",
    start: str = HISTORY_START,
    end: str = HISTORY_END,
    use_calibrated: bool = False,
    entry: str | None = None,
    impact_basis: str = IMPACT_BASIS_TRAILING,
) -> list[CellResult]:
    """
    Run the full (symbol × family × horizon) strategy-fit grid.

    Parameters
    ----------
    ds : DataSource
        A DataSource implementing funding_universe() and daily_history().
    start, end : str
        Date range for daily history.
    use_calibrated : bool
        When True, resolve per-name impact coefficients from impact_coeffs.json
        (plan-15 calibrated values) instead of the assumed SQRT_IMPACT_COEFF=1.0.
        Affects intraday AND daily single-name cells (plan 13; the daily
        shape borrows intraday-calibrated coefficients — see
        impact.DAILY_COEFF_IS_BORROWED_NOTE). Default False preserves
        pre-plan-15 behaviour.

    Returns
    -------
    list[CellResult]
        All cell results (tested + skipped + blocked).
    """
    universe = ds.funding_universe()
    if not universe:
        raise EmptyUniverseError(
            "funding universe resolved to zero symbols — refusing to render a "
            "verdict. The usual cause is an expired SB_ACCESS_TOKEN: "
            "screen_universe logs HTTP 401 per endpoint, returns an empty "
            "intersection, and the grid would otherwise run to completion over "
            "nothing and write a NO-GO report over a good one."
        )
    logger.info("Universe: %s", sorted(universe))

    # Log the calendar-aware expected trading-day span for this date window.
    # Uses CALENDAR.trading_days_between so the count reflects IDX holidays, not
    # just weekdays — the shared reader (plan-14 §4) is the single source of truth.
    from datetime import date as _date
    _start_d = _date.fromisoformat(start)
    _end_d = _date.fromisoformat(end)
    _expected_tdays = _CALENDAR.trading_days_between(_start_d, _end_d)
    logger.info(
        "Date window %s → %s: %d expected trading days (calendar-aware, IDX holidays applied)",
        start, end, _expected_tdays,
    )

    # Load history for all symbols.
    hist_dict: dict[str, pd.DataFrame] = {}
    min_edge_map: dict[str, float] = {}
    for symbol in sorted(universe):
        try:
            df = ds.daily_history(symbol, start, end)
        except Exception as exc:
            logger.warning("Could not load history for %s: %s", symbol, exc)
            continue
        # Minimum rows guard: must have at least MIN_EFF_N rows (the statistical
        # floor for a meaningful test) before we bother computing walk-forward or
        # cost-gate metrics.  MIN_EFF_N = 30 from lib.stats, not a magic number.
        if len(df) < MIN_EFF_N:
            logger.warning("Skipping %s: too few rows (%d < MIN_EFF_N=%d)", symbol, len(df), MIN_EFF_N)
            continue
        hist_dict[symbol] = df

        # Compute structural friction floor from plan-09.
        last_close = float(df["close"].iloc[-1])
        spread_bps = _min_spread_bps(last_close)
        min_edge_map[symbol] = spread_bps + ROUND_TRIP_BPS

    all_cells: list[CellResult] = []

    # --- Single-name families ---
    for symbol, df in hist_dict.items():
        # One trailing-impact model per symbol, shared by its cells (cache).
        symbol_impact_model = (
            build_impact_model(ds, symbol, df, start, end, use_calibrated)
            if impact_basis == IMPACT_BASIS_TRAILING
            else None
        )
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
                    use_calibrated=use_calibrated,
                    entry=entry,
                    impact_basis=impact_basis,
                    impact_model=symbol_impact_model,
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
                entry=entry,
            )
            all_cells.append(cell)

    # --- Cross-sectional family ---
    for hold in SINGLE_NAME_HOLDS:
        family = StrategyFamily(
            name="cross_sectional_momentum",
            shape=SHAPE_CROSS_SECTIONAL,
            horizon_trading_days=hold,
        )
        cell = _run_cross_sectional_cell(
            hist_dict=hist_dict,
            family=family,
            universe_min_edge_map=min_edge_map,
            entry=entry,
        )
        all_cells.append(cell)

    # --- Pairs / cointegration family ---
    for hold in SINGLE_NAME_HOLDS:
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
                use_calibrated=use_calibrated,
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
    #
    # dataclasses.replace, NOT a field-by-field CellResult(...) rebuild. This used
    # to enumerate all 28 fields by hand, and every field added since was silently
    # dropped back to its default — which is uniquely dangerous here, because the
    # cells passed to this function are exactly the cost-gate-passing pool that can
    # become survivors. W2.1's impact_priced defaults to False, so a dropped field
    # would have permanently blocked every real intraday cell from reaching PASS,
    # with no error anywhere. replace() copies every field by construction, so the
    # next field added cannot reintroduce the bug.
    updated: list[CellResult] = [
        dataclasses.replace(c, fdr_accepted=fdr_map[i]) if i in fdr_map else c
        for i, c in enumerate(cells)
    ]
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
    impact_sweep: bool = False,
    intraday_store_dir: str | None = None,
    use_calibrated: bool = False,
    entry: str | None = None,
    impact_basis: str = IMPACT_BASIS_TRAILING,
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
    impact_sweep : bool
        When True, also run the impact sensitivity sweep and write
        impact_sweep.md to outdir.  Default False; normal output is unchanged
        when False.
    intraday_store_dir : str | None
        When set, wrap ds in IntraStorePrefDataSource so that the intraday
        walk-forward loop (intraday_walk_forward.py) uses the accrued parquet
        store when it has more distinct trading days than a fresh live pull.
        Has no effect on daily/event/cross-sectional/pairs cells.
        Ignored when None (default; fixture path unchanged).

    Returns
    -------
    str
        Path to the written strategy_portfolio.md.
    """
    effective_ds = ds
    if intraday_store_dir is not None:
        from screener.intraday_store import IntraStorePrefDataSource
        effective_ds = IntraStorePrefDataSource(
            live_ds=ds, store_dir=intraday_store_dir
        )
        logger.info("Intraday store-aware wrapper active: %s", intraday_store_dir)

    cells = run_grid(
        ds=effective_ds, start=start, end=end, use_calibrated=use_calibrated, entry=entry,
        impact_basis=impact_basis,
    )
    cells = apply_fdr_to_grid(cells)
    path = emit_strategy_portfolio(cells=cells, outdir=outdir, is_synthetic=is_synthetic)

    if impact_sweep:
        from strategy_fit.impact_sweep import sweep_grid, emit_impact_sweep
        grid = sweep_grid(cells)
        sweep_path = emit_impact_sweep(grid=grid, cells=cells, outdir=outdir)
        logger.info("impact_sweep.md written to: %s", sweep_path)

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
        choices=["fixture", "live", "local"],
        default="fixture",
        help="Data source: 'fixture' (offline, no token) or 'live' (needs SB_ACCESS_TOKEN).",
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
    parser.add_argument(
        "--impact-sweep",
        action="store_true",
        default=False,
        help=(
            "Run impact sensitivity sweep after the normal grid run and write "
            "impact_sweep.md to outdir.  Sweeps SQRT_IMPACT_COEFF ∈ {0.5, 1.0} × "
            "position notional over the MEASURED sleeve (ADR-0017) at N ∈ "
            "{10, 5, 3, 1} concurrent names ({1.5M, 3M, 5M, 15M} IDR), plus the "
            "superseded ADR-0011 30M reference point.  Default: off."
        ),
    )
    parser.add_argument(
        "--intraday-store-dir",
        default=None,
        help=(
            "Path to the intraday accrual store directory "
            "(populated by screener/intraday_store.py snapshot).  When set, "
            "the intraday walk-forward loop prefers the store when it has more "
            "distinct trading days than the live pull.  Default: not used."
        ),
    )
    parser.add_argument(
        "--use-calibrated",
        action="store_true",
        default=False,
        help=(
            "Use plan-15 calibrated per-name impact coefficients from "
            "impact_coeffs.json instead of the assumed SQRT_IMPACT_COEFF=1.0.  "
            "Requires confirmed coefficients to exist in impact_coeffs.json; "
            "names without confirmed coefficients fall back to bucket then "
            "global default.  Affects intraday AND daily single-name cells "
            "(plan 13).  Default: off."
        ),
    )
    parser.add_argument(
        "--entry",
        default=None,
        choices=["next_close", "next_open", "signal_close_LOOKAHEAD"],
        help=(
            "Entry convention for daily/event families (harness D1). Default: "
            "next_close (live-feasible). signal_close_LOOKAHEAD reproduces the "
            "pre-fix same-close entry and is unattainable live."
        ),
    )
    parser.add_argument(
        "--impact-basis",
        default=IMPACT_BASIS_TRAILING,
        choices=list(IMPACT_BASES),
        help=(
            "How daily single-name impact inputs are estimated. Default "
            "trailing_point_in_time: each trade is priced from 60-bar ADV/vol "
            "windows ending at its signal day. legacy_full_history reproduces "
            "reports from before 2026-10-14 (one full-history ADV/vol per cell)."
        ),
    )
    parser.add_argument(
        "--local-data-dir",
        default=None,
        help="--source local: directory of <SYMBOL>.parquet daily bars (default research/data/daily).",
    )
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="--source local: text file, one symbol per line ('#' comments allowed). Required.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.source == "local":
        from strategy_fit.local_source import DEFAULT_DATA_DIR, LocalParquetDataSource

        if not args.symbols_file:
            parser.error("--source local requires --symbols-file")
        with open(args.symbols_file, encoding="utf-8") as fh:
            symbols = [ln.split("#")[0].strip() for ln in fh]
        ds = LocalParquetDataSource(
            [x for x in symbols if x], args.local_data_dir or DEFAULT_DATA_DIR
        )
        is_synthetic = False
    elif args.source == "fixture":
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
        impact_sweep=args.impact_sweep,
        intraday_store_dir=args.intraday_store_dir,
        use_calibrated=args.use_calibrated,
        entry=args.entry,
        impact_basis=args.impact_basis,
    )
    print(f"strategy_portfolio.md written to: {path}")
    if args.impact_sweep:
        sweep_path = os.path.join(args.outdir, "impact_sweep.md")
        print(f"impact_sweep.md written to: {sweep_path}")


if __name__ == "__main__":
    main()
