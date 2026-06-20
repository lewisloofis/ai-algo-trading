"""
strategy_fit/impact.py — Square-root market-impact (slippage) model for intraday cells.

Implements a round-trip Almgren-style square-root impact law:

    impact_bps_roundtrip = 2 * SQRT_IMPACT_COEFF * daily_vol_bps * sqrt(participation_rate)

where:
    participation_rate = position_notional_idr / adv_notional_idr  (clamped to [0, 1])

Scope: intraday same-day cells only (hold=0).  Daily / event / cross-sectional /
pairs cells retain impact=0 for now — plan-13 will extend impact to other shapes.

Round-trip justification
------------------------
The ``2 *`` factor accounts for crossing the book at BOTH entry and exit.  The
one-tick spread is already captured inside ``min_edge_to_clear_bps`` (plan-09
structural friction floor), so this model estimates the *additional* book-walking
cost beyond the first tick — i.e., the cost of trading the rest of the position
through the order book.  There is no double-counting of the first tick.

Constants
---------
SQRT_IMPACT_COEFF : float = 1.0
    Almgren temporary-impact coefficient.  The empirical literature places this
    at 0.5–1.0; 1.0 is the conservative (high-friction) end, appropriate for
    sizing research before live calibration.  plan-13 will refine with live fills.

POSITION_NOTIONAL_IDR : float = 30_000_000
    Per-name order notional in IDR.  This is a v1 constant: 300M IDR AI sleeve
    (ADR-0011) divided by ~10 concurrent names as a rough per-name slice.
    plan-13 will replace this with symbol-specific optimal position sizing.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

SQRT_IMPACT_COEFF: float = 1.0
"""
Almgren temporary-impact coefficient (conservative end: 1.0).

Almgren et al. (2005) estimate the coefficient at roughly 0.5–1.0.  Using 1.0
is the most conservative (largest impact estimate) for pre-live sizing research.
plan-13 will calibrate against live fills once the strategy is deployed.
"""

POSITION_NOTIONAL_IDR: float = 30_000_000.0
"""
Per-name order notional in IDR (v1 constant).

Derived as: 300M IDR AI sleeve (ADR-0011) / ~10 concurrent names.  This is a
simplistic slice; plan-13 will refine sizing with volatility targeting and
signal confidence.  Documented here so every caller uses a single, auditable
value.
"""

# Fallback / penalty value returned when impact cannot be computed (degenerate
# inputs such as zero ADV, single day, or all-NaN series).  Chosen to be large
# enough to fail any reasonable cost gate, surfacing the data-quality issue.
_DEGENERATE_IMPACT_BPS: float = 10_000.0

_DEGENERATE_VOL_BPS: float = 10_000.0


# ---------------------------------------------------------------------------
# Helpers: daily vol and ADV from an intraday DataFrame
# ---------------------------------------------------------------------------

def compute_daily_vol_bps(intraday_df: pd.DataFrame) -> float:
    """
    Estimate daily close-to-close return volatility in basis points.

    Groups the intraday DataFrame by ``date``, takes the last bar's close as
    the daily close, then computes the sample standard deviation of
    log(close[t] / close[t-1]) × 1e4.

    Degenerate cases:
    - Fewer than 2 trading days → returns ``_DEGENERATE_VOL_BPS`` (large penalty).
    - All closes NaN → returns ``_DEGENERATE_VOL_BPS``.
    - Zero-vol series (std = 0) → returns 0.0 (no impact from zero vol).

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame with ``date`` and ``close`` columns.

    Returns
    -------
    float
        Daily close-to-close return stdev in basis points (≥ 0).
    """
    if intraday_df is None or len(intraday_df) == 0:
        return _DEGENERATE_VOL_BPS

    # Take the last bar close for each trading day.
    daily_close = (
        intraday_df
        .groupby("date")["close"]
        .last()
        .dropna()
        .sort_index()
    )

    if len(daily_close) < 2:
        logger.debug("compute_daily_vol_bps: only %d day(s), returning degenerate", len(daily_close))
        return _DEGENERATE_VOL_BPS

    log_rets = np.diff(np.log(daily_close.to_numpy(dtype=float)))

    # Guard against NaN / Inf in log returns (e.g. zero close).
    valid = log_rets[np.isfinite(log_rets)]
    if len(valid) < 1:
        return _DEGENERATE_VOL_BPS

    vol = float(np.std(valid, ddof=1)) * 1e4  # convert to bps
    return max(0.0, vol)


def compute_adv_notional_idr(intraday_df: pd.DataFrame) -> float:
    """
    Estimate average daily turnover (ADV) in IDR.

    For each trading day, sums (close × volume) over all bars to get the day's
    turnover.  Returns the median across all days.

    Degenerate cases:
    - Empty DataFrame or all-zero/NaN volume → returns 0.0.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame with ``date``, ``close``, and ``volume`` columns.

    Returns
    -------
    float
        Median daily turnover in IDR (≥ 0).
    """
    if intraday_df is None or len(intraday_df) == 0:
        return 0.0

    df = intraday_df.copy()
    df["_turnover"] = pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")

    daily_turnover = (
        df
        .groupby("date")["_turnover"]
        .sum()
        .dropna()
    )

    if len(daily_turnover) == 0:
        return 0.0

    adv = float(np.nanmedian(daily_turnover.to_numpy(dtype=float)))
    return max(0.0, adv)


# ---------------------------------------------------------------------------
# Core impact formula
# ---------------------------------------------------------------------------

def compute_impact_bps(
    daily_vol_bps: float,
    adv_notional_idr: float,
    position_notional_idr: float = POSITION_NOTIONAL_IDR,
) -> float:
    """
    Compute round-trip market impact in basis points using the square-root model.

    Formula:
        participation_rate = clamp(position_notional_idr / adv_notional_idr, 0, 1)
        impact_bps = 2 * SQRT_IMPACT_COEFF * daily_vol_bps * sqrt(participation_rate)

    The ``2 *`` factor accounts for both entry and exit legs of a round trip.
    The one-tick spread is already in ``min_edge_to_clear_bps``; this model
    estimates the *additional* book-walking cost beyond the first tick.

    Degenerate cases:
    - adv_notional_idr <= 0 → participation cannot be computed; returns
      ``_DEGENERATE_IMPACT_BPS`` (large penalty) to fail any cost gate.

    Parameters
    ----------
    daily_vol_bps : float
        Daily close-to-close return standard deviation in basis points.
    adv_notional_idr : float
        Average daily turnover in IDR (from ``compute_adv_notional_idr``).
    position_notional_idr : float
        Order notional for this name (default: ``POSITION_NOTIONAL_IDR``).

    Returns
    -------
    float
        Round-trip impact in basis points (≥ 0).
    """
    if adv_notional_idr <= 0.0:
        logger.debug(
            "compute_impact_bps: adv_notional_idr=%s <= 0; returning degenerate impact",
            adv_notional_idr,
        )
        return _DEGENERATE_IMPACT_BPS

    participation_rate = min(position_notional_idr / adv_notional_idr, 1.0)
    participation_rate = max(participation_rate, 0.0)

    impact = 2.0 * SQRT_IMPACT_COEFF * daily_vol_bps * np.sqrt(participation_rate)
    return float(impact)


def compute_impact_bps_from_df(
    intraday_df: pd.DataFrame,
    position_notional_idr: float = POSITION_NOTIONAL_IDR,
) -> float:
    """
    Convenience wrapper: compute round-trip impact from an intraday DataFrame.

    Chains ``compute_daily_vol_bps`` → ``compute_adv_notional_idr`` →
    ``compute_impact_bps``.  Suitable for direct use in ``_run_intraday_cell``.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame.
    position_notional_idr : float
        Order notional (default: ``POSITION_NOTIONAL_IDR``).

    Returns
    -------
    float
        Round-trip impact in basis points (≥ 0).
    """
    vol_bps = compute_daily_vol_bps(intraday_df)
    adv = compute_adv_notional_idr(intraday_df)
    return compute_impact_bps(
        daily_vol_bps=vol_bps,
        adv_notional_idr=adv,
        position_notional_idr=position_notional_idr,
    )
