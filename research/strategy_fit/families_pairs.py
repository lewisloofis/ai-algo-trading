"""
strategy_fit/families_pairs.py — Pairs / cointegration family (plan-10 §7.6).

Shape: ``pairs`` (plan-10 §7.6).

Observation unit
----------------
One non-overlapping residual-spread observation per pair.
MIN_PAIR_OBS = 30 non-overlapping residual-spread observations at the PAIR level
(not pooled across pairs) — this is the conservative interpretation that ensures
each pair is individually well-supported.

Candidate-set cap
-----------------
With N universe names there are N*(N-1)/2 candidate pairs — combinatorially
explosive for large universes.  To bound runtime:
  1. Restrict to the top MAX_CANDIDATE_NAMES names by median daily volume.
  2. From those, consider all C(MAX_CANDIDATE_NAMES, 2) pairs.
  3. Log the cap explicitly (no silent truncation — plan-10 §7.8).

The cap is documented here.  Revisit if the universe shrinks to <15 names
(no cap needed) or grows beyond 30 names (tighten MAX_CANDIDATE_NAMES).

Cointegration test
------------------
Uses ``statsmodels.tsa.stattools.coint`` (Engle-Granger two-step at 5% level).
On failure / p > 0.05, the pair is rejected as "cointegration_not_found".

Spread half-life
----------------
Estimated from the AR(1) coefficient on the demeaned spread residual:
  phi = OLS coefficient; half_life = -log(2) / log(phi).
Finite positive half-life is required; infinite (non-stationary) or negative
half-lives skip the pair.

OOS block-bootstrap
-------------------
The spread series is split into non-overlapping blocks of length
``horizon_trading_days``.  Each block's sign is treated as an observation
(mean-reversion: spread reverts toward 0 → next-block return opposes spread).
Hit rate = fraction of blocks where sign(spread[0]) ≠ sign(spread[-1])
(spread moved toward zero).

Cost-stack floor
----------------
For pairs strategies: sum of both legs' min_edge_to_clear_bps.
Each leg is a separate order; both incur the full structural friction.
"""

from __future__ import annotations

import logging
from itertools import combinations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint, adfuller

from strategy_fit.families_single_name import ROUND_TRIP_BPS, bootstrap_hit_rate_pvalue

try:
    from lib.stats import MIN_EFF_N
except ImportError:
    MIN_EFF_N = 30

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_PAIR_OBS: int = 30
"""Minimum non-overlapping residual-spread observations required per pair."""

MAX_CANDIDATE_NAMES: int = 15
"""
Maximum number of names in the candidate set for pairs generation.

From N names we consider C(N, 2) pairs.  MAX_CANDIDATE_NAMES=15 → 105 pairs,
a tractable bound given backtest runtimes.  Names are selected by median daily
volume (most-liquid first) so the most feasible pairs are tested first.
"""

COINT_PVALUE_THRESHOLD: float = 0.05
"""Engle-Granger cointegration p-value threshold."""

MIN_SERIES_LENGTH: int = 60
"""Minimum rows required in each pair's aligned series to run cointegration."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _estimate_half_life(spread: np.ndarray) -> float:
    """
    Estimate mean-reversion half-life of *spread* via AR(1) OLS.

    Returns ``float('inf')`` when phi >= 1 (non-stationary) or the OLS fails.
    Returns ``float('nan')`` on any numerical error.
    """
    s = spread - spread.mean()
    y = s[1:]
    x = s[:-1]
    if len(x) < 10:
        return float("inf")
    try:
        cov = np.cov(x, y)
        phi = float(cov[0, 1] / cov[0, 0]) if cov[0, 0] > 0 else 1.0
    except Exception:
        return float("nan")

    if phi >= 1.0 or phi <= 0.0:
        return float("inf")
    return float(-np.log(2.0) / np.log(phi))


def _oos_spread_hit_rate(
    spread: np.ndarray,
    block_size: int,
) -> tuple[float, int]:
    """
    Block-bootstrap OOS test for spread mean-reversion.

    Splits *spread* into non-overlapping blocks of *block_size*.  Each block
    contributes one observation: the spread reverts if the last value in the
    block has the opposite sign from the first (or moves toward zero).

    Returns ``(hit_rate, n_blocks)``.
    """
    n_blocks = len(spread) // block_size
    if n_blocks < 1:
        return float("nan"), 0

    hits = 0
    for b in range(n_blocks):
        blk = spread[b * block_size: (b + 1) * block_size]
        if len(blk) < 2:
            continue
        # Reversion: |blk[-1]| < |blk[0]| (spread moved toward zero).
        if abs(blk[-1]) < abs(blk[0]):
            hits += 1

    hit_rate = hits / n_blocks if n_blocks > 0 else float("nan")
    return hit_rate, n_blocks


def _run_single_pair(
    sym_a: str,
    sym_b: str,
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    horizon_trading_days: int,
    min_edge_a: float,
    min_edge_b: float,
) -> dict | None:
    """
    Run cointegration + spread OOS test for one candidate pair.

    Returns a result dict if the pair passes cointegration, else None.
    The result dict contains the same keys as run_pairs_family's output
    (for the winning pair).
    """
    # Align by date.
    merged = pd.merge(
        df_a[["date", "close"]].rename(columns={"close": "close_a"}),
        df_b[["date", "close"]].rename(columns={"close": "close_b"}),
        on="date",
        how="inner",
    ).sort_values("date")

    if len(merged) < MIN_SERIES_LENGTH:
        logger.debug(
            "Pair (%s, %s): only %d aligned rows (need %d); skipping.",
            sym_a, sym_b, len(merged), MIN_SERIES_LENGTH,
        )
        return None

    price_a = merged["close_a"].to_numpy(dtype=float)
    price_b = merged["close_b"].to_numpy(dtype=float)

    # Engle-Granger cointegration test.
    try:
        _, p_coint, _ = coint(price_a, price_b)
    except Exception as exc:
        logger.debug("Cointegration test failed for (%s, %s): %s", sym_a, sym_b, exc)
        return None

    if p_coint > COINT_PVALUE_THRESHOLD:
        return None

    # Estimate hedge ratio (OLS: price_b ~ beta * price_a).
    beta = float(np.cov(price_a, price_b)[0, 1] / np.var(price_a)) if np.var(price_a) > 0 else 1.0
    spread = price_b - beta * price_a

    # Half-life estimate.
    half_life = _estimate_half_life(spread)
    if not np.isfinite(half_life) or half_life <= 0:
        logger.debug(
            "Pair (%s, %s): infinite/negative half-life (%.1f); skipping.",
            sym_a, sym_b, half_life,
        )
        return None

    # OOS block-bootstrap (non-overlapping blocks of length horizon_trading_days).
    block_size = max(1, horizon_trading_days)
    hit_rate, n_blocks = _oos_spread_hit_rate(spread, block_size=block_size)

    if not np.isfinite(hit_rate):
        return None

    eff_n = n_blocks
    gross_edge_bps = float((hit_rate - 0.5) * 200.0)  # rough: 1% per winning block
    # net_edge_bps is NOT pre-computed here: the runner computes true
    # net-after-full-cost as (gross - hurdle) to avoid double-counting the
    # friction floor (sum of both legs).  The correct sum-of-legs floor is
    # applied by the runner in _run_pairs_cell.

    # Bootstrap p-value (sign permutation on spread blocks).
    block_signs = np.array([
        1.0 if abs(spread[(b + 1) * block_size - 1]) < abs(spread[b * block_size]) else 0.0
        for b in range(n_blocks)
        if (b + 1) * block_size <= len(spread)
    ])
    if len(block_signs) == 0:
        return None

    rng = np.random.default_rng(42)
    null_hrs = np.array([
        float(np.mean(rng.permutation(block_signs)))
        for _ in range(500)
    ])
    p_value = float(np.mean(null_hrs >= hit_rate))

    return {
        "pair": (sym_a, sym_b),
        "eff_n": eff_n,
        "hit_rate": hit_rate,
        "gross_edge_bps": gross_edge_bps,
        "p_value": p_value,
        "half_life": half_life,
        "beta": beta,
    }


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

def run_pairs_family(
    hist_dict: dict[str, pd.DataFrame],
    horizon_trading_days: int,
    universe_min_edge_map: dict[str, float] | None = None,
) -> dict:
    """
    Run the pairs / cointegration family across the universe.

    Caps the candidate set to the top MAX_CANDIDATE_NAMES names by volume to
    bound combinatorics.  All caps are logged (no silent truncation).

    Parameters
    ----------
    hist_dict : dict[str, pd.DataFrame]
        Symbol → daily OHLCV DataFrame.
    horizon_trading_days : int
        Spread-observation block length in trading days.
    universe_min_edge_map : dict[str, float] or None
        Structural friction floors per name (pairs cost = sum of both legs).

    Returns
    -------
    dict
        Keys: status, skip_reason, eff_n, hit_rate, gross_edge_bps,
              net_edge_bps, p_value.
    """
    if len(hist_dict) < 2:
        return {
            "status": "insufficient_n",
            "skip_reason": "Need at least 2 symbols to form a pair",
            "eff_n": 0,
            "hit_rate": None,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    # Rank symbols by median daily volume to select the top-N candidate set.
    volume_by_sym: dict[str, float] = {}
    for sym, df in hist_dict.items():
        if "volume" in df.columns:
            volume_by_sym[sym] = float(np.nanmedian(df["volume"].to_numpy(dtype=float)))
        else:
            volume_by_sym[sym] = 0.0

    all_symbols = sorted(hist_dict.keys(), key=lambda s: volume_by_sym.get(s, 0.0), reverse=True)
    candidate_symbols = all_symbols[:MAX_CANDIDATE_NAMES]

    if len(all_symbols) > MAX_CANDIDATE_NAMES:
        logger.info(
            "pairs: capping candidate set from %d to top-%d symbols by volume "
            "(MAX_CANDIDATE_NAMES=%d). Excluded: %s",
            len(all_symbols),
            MAX_CANDIDATE_NAMES,
            MAX_CANDIDATE_NAMES,
            all_symbols[MAX_CANDIDATE_NAMES:],
        )
    else:
        logger.info(
            "pairs: %d candidate symbols, %d candidate pairs (no cap applied).",
            len(candidate_symbols),
            len(candidate_symbols) * (len(candidate_symbols) - 1) // 2,
        )

    min_edge_map = universe_min_edge_map or {}
    default_min_edge = ROUND_TRIP_BPS

    # Test all pairs in the candidate set; keep the best-eff_n passing pair.
    best_result: dict | None = None
    n_cointegrated = 0

    for sym_a, sym_b in combinations(candidate_symbols, 2):
        df_a = hist_dict[sym_a]
        df_b = hist_dict[sym_b]
        min_edge_a = min_edge_map.get(sym_a, default_min_edge)
        min_edge_b = min_edge_map.get(sym_b, default_min_edge)
        pair_result = _run_single_pair(
            sym_a, sym_b, df_a, df_b,
            horizon_trading_days=horizon_trading_days,
            min_edge_a=min_edge_a,
            min_edge_b=min_edge_b,
        )
        if pair_result is None:
            continue
        n_cointegrated += 1
        # Select the pair with the most OOS observations.
        if best_result is None or (pair_result["eff_n"] or 0) > (best_result["eff_n"] or 0):
            best_result = pair_result

    if best_result is None:
        return {
            "status": "cointegration_not_found",
            "skip_reason": (
                f"No cointegrated pair found among {len(candidate_symbols)} candidates "
                f"(Engle-Granger p > {COINT_PVALUE_THRESHOLD})"
            ),
            "eff_n": 0,
            "hit_rate": None,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    eff_n = best_result["eff_n"]
    if eff_n < MIN_PAIR_OBS:
        return {
            "status": "insufficient_n",
            "skip_reason": (
                f"Best pair {best_result['pair']} has eff_n={eff_n} < MIN_PAIR_OBS={MIN_PAIR_OBS}"
            ),
            "eff_n": eff_n,
            "hit_rate": best_result["hit_rate"],
            "gross_edge_bps": best_result["gross_edge_bps"],
            "net_edge_bps": None,
            "p_value": best_result["p_value"],
        }

    logger.info(
        "pairs: best pair=%s, eff_n=%d, hit_rate=%.3f, gross_edge=%.1f bps, "
        "half_life=%.1f days, p=%.3f. "
        "%d cointegrated pairs found.",
        best_result["pair"],
        eff_n,
        best_result["hit_rate"],
        best_result["gross_edge_bps"],
        best_result.get("half_life", float("nan")),
        best_result["p_value"],
        n_cointegrated,
    )

    return {
        "status": "tested",
        "skip_reason": None,
        "eff_n": eff_n,
        "hit_rate": best_result["hit_rate"],
        "gross_edge_bps": best_result["gross_edge_bps"],
        "net_edge_bps": None,
        "p_value": best_result["p_value"],
    }
