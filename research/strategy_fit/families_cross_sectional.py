"""
strategy_fit/families_cross_sectional.py — Cross-sectional momentum / factor family.

Shape: ``cross_sectional`` (plan-10 §7.5).

Observation unit
----------------
One cross-sectional period = one trading day across all universe names.
The factor is each name's trailing 1-day return rank (momentum proxy); the
target is each name's forward 1-day return.  The test statistic is the
Spearman Information Coefficient (IC) between factor rank and forward return.

MIN_EFF_N = 30 is applied on the number of independent cross-sectional
periods (days), NOT on symbol-count.  Days are treated as independent under
the assumption that the 1-day hold horizon means no carry-forward overlap.

Cost-stack floor
----------------
For cross-sectional long/short strategies, we take the median
``min_edge_to_clear_bps`` across the long and short legs as the friction
floor.  This is documented as a deliberate, conservative choice: it avoids
double-counting the full round-trip on both legs for a market-neutral book
where legs partly offset.  The choice is marked with a ``# design:`` comment
below; change to sum-of-legs if the execution model changes.

Metrics computed
----------------
- IC: Spearman rank correlation between factor ranks and forward returns
  on each cross-sectional period.
- ic_tstat: t-statistic on the IC series (mean / (std / sqrt(T))).
- icir: Information Coefficient Information Ratio = mean(IC) / std(IC).
  Positive ICIR indicates a persistent cross-sectional signal.
- eff_n: number of cross-sectional periods (days) where ≥ MIN_SYMBOLS_PER_PERIOD
  names have valid data.

Long-only infeasibility
-----------------------
A cross-sectional decile-spread strategy is *inherently* long/short (long the
top-ranked names, short the bottom-ranked names) — there is no long-only
version of it that preserves what the strategy tests, unlike the directional
families in families_intraday.py / families_single_name.py / families_event.py
(see strategy_fit/long_only.py). IDX retail accounts cannot short-sell, so
this family is always reported as STATUS_LONG_SHORT_INFEASIBLE regardless of
how strong the computed IC/ICIR is — it must be excluded and labelled, never
silently scored as if it were deployable.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats as sps

from strategy_fit.families_single_name import N_BOOTSTRAP_FAMILIES, bootstrap_hit_rate_pvalue
from strategy_fit.long_only import long_short_infeasible_result

try:
    from lib.stats import MIN_EFF_N
except ImportError:
    MIN_EFF_N = 30

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SYMBOLS_PER_PERIOD: int = 5
"""Minimum number of symbols with valid data required for one IC observation."""

FACTOR_LOOKBACK: int = 1
"""Trailing return window used as the cross-sectional factor (1-day momentum)."""


# ---------------------------------------------------------------------------
# Cross-sectional IC computation
# ---------------------------------------------------------------------------

def _compute_daily_ic(
    hist_dict: dict[str, pd.DataFrame],
    horizon_trading_days: int,
) -> pd.DataFrame:
    """
    Compute daily Spearman IC between trailing-return rank and forward return.

    Returns a DataFrame with columns: date, ic, n_symbols.
    Each row is one cross-sectional period.  Periods with fewer than
    MIN_SYMBOLS_PER_PERIOD valid observations are excluded.

    Parameters
    ----------
    hist_dict : dict[str, pd.DataFrame]
        Symbol → daily OHLCV DataFrame (close column required).
    horizon_trading_days : int
        Forward return horizon in trading days.

    Returns
    -------
    pd.DataFrame
        Columns: date (str), ic (float), n_symbols (int).
    """
    # Build a wide DataFrame of closes, indexed by date.
    close_dfs: list[pd.DataFrame] = []
    for sym, df in hist_dict.items():
        s = df.set_index("date")[["close"]].rename(columns={"close": sym})
        close_dfs.append(s)

    if not close_dfs:
        return pd.DataFrame(columns=["date", "ic", "n_symbols"])

    wide = pd.concat(close_dfs, axis=1).sort_index()
    wide.index = wide.index.astype(str)

    # Factor: trailing 1-day return (momentum proxy).
    factor = wide.pct_change(FACTOR_LOOKBACK)
    # Forward return: horizon-day return.
    fwd = wide.pct_change(horizon_trading_days).shift(-horizon_trading_days)

    ic_records: list[dict] = []
    for date in factor.index:
        f_row = factor.loc[date].dropna()
        fwd_row = fwd.loc[date].dropna() if date in fwd.index else pd.Series(dtype=float)
        common = f_row.index.intersection(fwd_row.index)
        if len(common) < MIN_SYMBOLS_PER_PERIOD:
            continue
        f_vals = f_row[common].to_numpy(dtype=float)
        fwd_vals = fwd_row[common].to_numpy(dtype=float)
        # Spearman IC = Pearson corr on ranks.
        ic_val, _ = sps.spearmanr(f_vals, fwd_vals)
        if np.isfinite(ic_val):
            ic_records.append({"date": date, "ic": float(ic_val), "n_symbols": len(common)})

    return pd.DataFrame(ic_records)


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

def run_cross_sectional_family(
    hist_dict: dict[str, pd.DataFrame],
    horizon_trading_days: int,
    universe_min_edge_map: dict[str, float] | None = None,
) -> dict:
    """
    Run the cross-sectional momentum family across the universe.

    Parameters
    ----------
    hist_dict : dict[str, pd.DataFrame]
        Symbol → daily OHLCV DataFrame.
    horizon_trading_days : int
        Forward return horizon in trading days.
    universe_min_edge_map : dict[str, float] or None
        Structural friction floors per name (used for net_edge annotation only).

    Returns
    -------
    dict
        Keys: status, skip_reason, eff_n, ic, ic_tstat, icir, p_value,
              hit_rate, gross_edge_bps, net_edge_bps.

        status is never "tested": this family requires a short leg (long the
        top decile, short the bottom decile), which IDX retail accounts
        cannot execute. Once eff_n and IC are computed (data-sufficiency is
        still checked and reported normally), the family is reported as
        STATUS_LONG_SHORT_INFEASIBLE — see strategy_fit/long_only.py.
    """
    ic_df = _compute_daily_ic(hist_dict, horizon_trading_days)

    if ic_df.empty or len(ic_df) == 0:
        return {
            "status": "insufficient_n",
            "skip_reason": "No cross-sectional periods with sufficient symbols",
            "eff_n": 0,
            "ic": None,
            "ic_tstat": None,
            "icir": None,
            "p_value": None,
            "hit_rate": None,
            "gross_edge_bps": None,
            "net_edge_bps": None,
        }

    eff_n = len(ic_df)
    if eff_n < MIN_EFF_N:
        return {
            "status": "insufficient_n",
            "skip_reason": f"Cross-sectional eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}",
            "eff_n": eff_n,
            "ic": None,
            "ic_tstat": None,
            "icir": None,
            "p_value": None,
            "hit_rate": None,
            "gross_edge_bps": None,
            "net_edge_bps": None,
        }

    ic_series = ic_df["ic"].to_numpy(dtype=float)
    mean_ic = float(np.nanmean(ic_series))
    std_ic = float(np.nanstd(ic_series, ddof=1))
    ic_tstat = float(mean_ic / (std_ic / np.sqrt(eff_n))) if std_ic > 0 else 0.0
    icir = float(mean_ic / std_ic) if std_ic > 0 else 0.0

    # Hit rate: fraction of IC periods where IC > 0 (factor predicts direction).
    hit_rate = float(np.mean(ic_series > 0))

    # Gross edge proxy: mean IC × cross-sectional vol × 1e4.
    # Using mean absolute return as a proxy for cross-sectional vol.
    gross_edge_bps = float(mean_ic * 100.0)  # 1% cross-sec vol assumption; rough proxy
    # net_edge_bps is NOT pre-computed here: the runner computes true
    # net-after-full-cost as (gross - hurdle) to avoid double-counting the
    # friction floor.  The key is preserved in the dict for API stability.
    # The correct floor for cross-sectional L/S (median, not sum-of-legs) is
    # applied by the runner in _run_cross_sectional_cell.

    # Bootstrap p-value via sign permutation of IC series.
    rng = np.random.default_rng(42)
    null_mean_ics = np.array([
        float(np.nanmean(ic_series * rng.choice([-1.0, 1.0], size=len(ic_series))))
        for _ in range(N_BOOTSTRAP_FAMILIES)
    ])
    p_value = float(np.mean(null_mean_ics >= mean_ic))

    logger.info(
        "cross_sectional: eff_n=%d, mean_IC=%.4f, ICIR=%.3f, IC_tstat=%.2f, p=%.3f "
        "— long_short_infeasible (decile spread requires a short leg)",
        eff_n, mean_ic, icir, ic_tstat, p_value,
    )

    # Cross-sectional decile spread is structurally long/short (buy top decile,
    # short bottom decile) — it cannot be made long-only without changing what
    # the strategy tests (strategy_fit/long_only.py). The IC/ICIR/p_value
    # computed above are diagnostic only (logged, and summarized in
    # skip_reason for a human reading the report); they are never returned as
    # a scored, tradeable result.
    return long_short_infeasible_result(
        reason_detail=(
            f"Cross-sectional IC computed (eff_n={eff_n}, mean_IC={mean_ic:.4f}, "
            f"ICIR={icir:.3f}, p={p_value:.3f}) but"
        ),
        eff_n=eff_n,
        ic=None,
        ic_tstat=None,
        icir=None,
    )
