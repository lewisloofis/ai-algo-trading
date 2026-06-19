"""
metrics.py — Pure screening metric functions for IDX universe selection.

All functions are pure (no side effects, immutable inputs).  Designed to be
called per-symbol inside screen_universe.py; also importable in notebooks.

Column contracts
----------------
hist_df passed to median_daily_turnover / realised_vol_bps must have at
minimum a ``close`` column (float, IDR).  For median_daily_turnover, also
either a ``value`` column (pre-computed daily value in IDR) **or** a
``volume`` column (shares).  When both are present ``value`` is used.
"""

from __future__ import annotations

import math

import pandas as pd


# ---------------------------------------------------------------------------
# Tick ladder
# ---------------------------------------------------------------------------

def tick_size(price: float) -> int:
    """
    IDX fraksi harga (tick ladder) — step function.

    Source: collector/broker.go roundTick() and docs/reference/06-pre-trade-rules.md §5.

    Bands (price exclusive upper bound):
        price < 200   → 1
        price < 500   → 2
        price < 2000  → 5
        price < 5000  → 10
        else          → 25

    Returns the minimum price increment in IDR (integer ticks).
    """
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


# ---------------------------------------------------------------------------
# Structural edge cap
# ---------------------------------------------------------------------------

def min_spread_bps(price: float) -> float:
    """
    Minimum achievable spread in basis points for a stock at ``price``.

    One tick is the smallest possible spread in a continuous limit-order book.
    If this floor exceeds the round-trip fee cost, the name is structurally
    edge-capped: a passive maker can earn at most one tick of edge, which may
    not cover fees.

    Formula: tick_size(price) / price * 1e4
    """
    return tick_size(price) / price * 1e4


# ---------------------------------------------------------------------------
# Liquidity metric
# ---------------------------------------------------------------------------

def median_daily_turnover(hist_df: pd.DataFrame) -> float:
    """
    Median daily value traded (IDR) over the history window.

    Column contract:
        - If ``value`` column is present, use it directly (pre-computed IDR
          turnover per bar).
        - Otherwise compute ``close * volume`` per bar.
        - Each row is assumed to represent one trading day.

    Returns the median IDR value.  Raises KeyError if neither ``value`` nor
    both ``close`` / ``volume`` columns are present.

    Input DataFrame is not mutated.
    """
    df = hist_df.copy()
    if "value" in df.columns:
        daily_value = df["value"].astype(float)
    else:
        daily_value = df["close"].astype(float) * df["volume"].astype(float)
    return float(daily_value.median())


# ---------------------------------------------------------------------------
# Volatility metric
# ---------------------------------------------------------------------------

def realised_vol_bps(hist_df: pd.DataFrame) -> float:
    """
    Daily close-to-close log-return standard deviation, expressed in bps.

    Column contract: ``close`` column (float, IDR).

    Each row is one trading day.  Returns stdev of log returns × 1e4.
    Requires at least 2 rows; raises ValueError on fewer.

    Input DataFrame is not mutated.
    """
    closes = hist_df["close"].astype(float)
    if len(closes) < 2:
        raise ValueError(
            f"realised_vol_bps requires at least 2 rows, got {len(closes)}"
        )
    log_returns = (closes / closes.shift(1)).apply(math.log).dropna()
    return float(log_returns.std() * 1e4)


# ---------------------------------------------------------------------------
# Market-impact model
# ---------------------------------------------------------------------------

def impact_bps(
    q: float,
    adv: float,
    sigma_bps: float,
    half_spread_bps: float,
    y: float = 1.0,
) -> float:
    """
    Square-root market-impact law (bps), all-in cost for a single taker leg.

    Formula: half_spread_bps + y * sigma_bps * sqrt(q / adv)

    Parameters
    ----------
    q : float
        Order size.  Must be in the SAME units as ``adv``
        (either both in shares or both in IDR value).
    adv : float
        Average daily volume in the same units as ``q``.
    sigma_bps : float
        Daily realised volatility in bps (e.g. from realised_vol_bps).
    half_spread_bps : float
        Half of the bid-ask spread in bps (i.e. min_spread_bps / 2 is
        a sensible floor).
    y : float
        Impact coefficient.  Default 1.0 (square-root law as commonly
        reported for liquid mid-cap equities).

    Returns
    -------
    float
        Estimated one-way taker cost in bps.
    """
    return half_spread_bps + y * sigma_bps * math.sqrt(q / adv)


def max_position_for_budget(
    adv: float,
    sigma_bps: float,
    budget_bps: float,
    half_spread_bps: float,
    y: float = 1.0,
) -> float:
    """
    Invert the square-root impact law: maximum position size (same units as
    ``adv``) such that impact_bps(q, ...) <= budget_bps.

    Algebraic inversion:
        budget_bps = half_spread_bps + y * sigma_bps * sqrt(q / adv)
        sqrt(q / adv) = (budget_bps - half_spread_bps) / (y * sigma_bps)
        q = adv * ((budget_bps - half_spread_bps) / (y * sigma_bps))**2

    Returns 0.0 when budget_bps <= half_spread_bps (can't even pay the
    spread, so no position is viable at this budget).

    Parameters
    ----------
    adv           : average daily volume (shares or IDR — must match intended q units)
    sigma_bps     : daily realised vol in bps
    budget_bps    : maximum acceptable one-way taker cost in bps
    half_spread_bps : half of the current bid-ask spread in bps
    y             : impact coefficient (default 1.0)
    """
    if budget_bps <= half_spread_bps:
        return 0.0
    ratio = (budget_bps - half_spread_bps) / (y * sigma_bps)
    return adv * ratio ** 2
