"""
strategy_fit/families_cross_sectional.py — Cross-sectional momentum / factor family.

Shape: ``cross_sectional`` (plan-10 §7.5).

Observation unit
----------------
One cross-sectional period = one trading day across all universe names.
The factor is each name's trailing 1-day return rank (momentum proxy); the
target is each name's forward 1-day return.  The test statistic is the
Spearman Information Coefficient (IC) between factor rank and forward return.

Entry convention and rebalance schedule (harness defect D1)
-----------------------------------------------------------
The rank at session s uses close_s, so a live bot can only enter at s+1.
Forward returns are routed through strategy_fit/entry_convention.py
(default ``next_close``: close_{s+1} -> close_{s+1+hold}).  The old same-close
return survives only as ``entry="signal_close_LOOKAHEAD"``.

The live-feasible periodic rebalance is: rank on s, hold s+1 .. s+1+hold,
re-rank ONLY on the exit day s+1+hold (``harness.deoverlap_sequential`` on
calendar positions).  Under LOOKAHEAD the old behaviour (every day, no
de-overlap) is kept so the legacy numbers reproduce exactly.

MIN_EFF_N = 30 is applied on the number of rebalance periods actually taken
(non-overlapping), NOT on symbol-count and NOT on all signal days.

Long-leg diagnostic and null
----------------------------
Besides the IC, each rebalance period records the equal-weight forward return
of the top-quintile names by factor (``long_ret``) and the equal-weight return
of ALL names with data that period (``null_ret``) on the SAME entry/hold/period.
The paired difference is the only honest read of a long-only leg in a trending
universe.  This is a DIAGNOSTIC: status stays long_short_infeasible.

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

from strategy_fit.entry_convention import (
    DEFAULT_ENTRY,
    ENTRY_SIGNAL_CLOSE_LOOKAHEAD,
    forward_return_series,
    validate_entry,
)
from strategy_fit.families_single_name import N_BOOTSTRAP_FAMILIES, bootstrap_hit_rate_pvalue
from strategy_fit.harness import deoverlap_sequential
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


TOP_FRAC: float = 0.2
"""Fraction of names (top quintile by factor) in the long-leg diagnostic."""

TOP_TRIM: int = 5
"""Number of best periods dropped for the concentration check."""

PERIOD_COLUMNS = ["date", "pos", "ic", "n_symbols", "long_ret", "null_ret"]


# ---------------------------------------------------------------------------
# Cross-sectional IC computation
# ---------------------------------------------------------------------------

def _wide_close(hist_dict: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close_dfs = [
        df.set_index("date")[["close"]].rename(columns={"close": sym})
        for sym, df in hist_dict.items()
    ]
    wide = pd.concat(close_dfs, axis=1).sort_index()
    wide.index = wide.index.astype(str)
    return wide


def _wide_forward(
    hist_dict: dict[str, pd.DataFrame],
    wide: pd.DataFrame,
    hold: int,
    entry: str,
) -> pd.DataFrame:
    """Forward return per (date, symbol), entry-convention aware (D1)."""
    if entry == ENTRY_SIGNAL_CLOSE_LOOKAHEAD:
        # Pre-fix formula, verbatim, so the legacy numbers reproduce exactly.
        return wide.pct_change(hold).shift(-hold)
    cols = {}
    for sym, df in hist_dict.items():
        d = df.sort_values("date").reset_index(drop=True)
        fwd = forward_return_series(d, hold, entry)
        cols[sym] = pd.Series(fwd.to_numpy(dtype=float), index=d["date"].astype(str).to_numpy())
    return pd.DataFrame(cols).reindex(wide.index)


def compute_period_returns(
    hist_dict: dict[str, pd.DataFrame],
    horizon_trading_days: int,
    entry: str | None = None,
) -> pd.DataFrame:
    """
    One row per signal day with enough data (ALL such days; no de-overlap).

    Columns: date, pos (index of the date in the sorted union calendar),
    ic (Spearman of 1-day trailing-return rank vs forward return), n_symbols,
    long_ret (equal-weight forward return of the top-quintile names by factor),
    null_ret (equal-weight forward return of every name in the same period).
    All forward returns use ``entry`` (default next_close, see module docstring).
    """
    entry = validate_entry(DEFAULT_ENTRY if entry is None else entry)
    if not hist_dict:
        return pd.DataFrame(columns=PERIOD_COLUMNS)

    wide = _wide_close(hist_dict)
    factor = wide.pct_change(FACTOR_LOOKBACK)
    fwd = _wide_forward(hist_dict, wide, horizon_trading_days, entry)

    records: list[dict] = []
    for pos, date in enumerate(factor.index):
        f_row = factor.loc[date].dropna()
        fwd_row = fwd.loc[date].dropna()
        common = f_row.index.intersection(fwd_row.index)
        if len(common) < MIN_SYMBOLS_PER_PERIOD:
            continue
        f_vals = f_row[common].to_numpy(dtype=float)
        fwd_vals = fwd_row[common].to_numpy(dtype=float)
        ic_val, _ = sps.spearmanr(f_vals, fwd_vals)
        if not np.isfinite(ic_val):
            continue
        n_top = max(1, int(np.ceil(TOP_FRAC * len(common))))
        top = np.argsort(-f_vals, kind="stable")[:n_top]
        records.append({
            "date": date,
            "pos": pos,
            "ic": float(ic_val),
            "n_symbols": len(common),
            "long_ret": float(np.mean(fwd_vals[top])),
            "null_ret": float(np.mean(fwd_vals)),
        })
    return pd.DataFrame(records, columns=PERIOD_COLUMNS)


def select_rebalance_periods(
    periods: pd.DataFrame,
    horizon_trading_days: int,
    entry: str | None = None,
) -> pd.DataFrame:
    """
    Live-feasible periodic rebalance: rank on s, hold s+1 .. s+1+hold, re-rank
    only on the exit day s+1+hold (sequential, on calendar positions).
    LOOKAHEAD keeps every day (the pre-fix behaviour).
    """
    entry = validate_entry(DEFAULT_ENTRY if entry is None else entry)
    if entry == ENTRY_SIGNAL_CLOSE_LOOKAHEAD or periods.empty:
        return periods.copy()
    return deoverlap_sequential(periods, hold=horizon_trading_days, pos_col="pos")


def long_leg_diagnostic(periods: pd.DataFrame, entry: str) -> dict:
    """Paired long-top-quintile vs equal-weight-universe summary (bps)."""
    long_r = periods["long_ret"].to_numpy(dtype=float) * 1e4
    null_r = periods["null_ret"].to_numpy(dtype=float) * 1e4
    diff = long_r - null_r
    n = len(diff)
    sd = float(np.std(diff, ddof=1)) if n > 1 else 0.0
    return {
        "entry": entry,
        "n_periods": n,
        "long_gross_bps": float(np.mean(long_r)),
        "null_bps": float(np.mean(null_r)),
        "edge_over_null_bps": float(np.mean(diff)),
        "t_paired": float(np.mean(diff) / (sd / np.sqrt(n))) if sd > 0 else 0.0,
        "long_gross_ex_top5_bps": float(np.mean(np.sort(long_r)[:-TOP_TRIM])) if n > TOP_TRIM else None,
        "edge_ex_top5_bps": float(np.mean(np.sort(diff)[:-TOP_TRIM])) if n > TOP_TRIM else None,
    }


# ---------------------------------------------------------------------------
# Public runner
# ---------------------------------------------------------------------------

def run_cross_sectional_family(
    hist_dict: dict[str, pd.DataFrame],
    horizon_trading_days: int,
    universe_min_edge_map: dict[str, float] | None = None,
    entry: str | None = None,
) -> dict:
    """
    Run the cross-sectional momentum family across the universe.

    ``entry`` selects the entry convention (strategy_fit/entry_convention.py);
    None = next_close.  The IC series is taken on non-overlapping rebalance
    periods (see select_rebalance_periods).

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
    entry = validate_entry(DEFAULT_ENTRY if entry is None else entry)
    ic_df = select_rebalance_periods(
        compute_period_returns(hist_dict, horizon_trading_days, entry),
        horizon_trading_days,
        entry,
    )

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
            "diagnostic": None,
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
            "diagnostic": None,
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
    diagnostic = long_leg_diagnostic(ic_df, entry)
    result = long_short_infeasible_result(
        reason_detail=(
            f"Cross-sectional IC computed (entry={entry}, eff_n={eff_n}, "
            f"mean_IC={mean_ic:.4f}, ICIR={icir:.3f}, p={p_value:.3f}; "
            f"diagnostic long top-quintile {diagnostic['long_gross_bps']:.1f} bps vs "
            f"equal-weight null {diagnostic['null_bps']:.1f} bps per period) but"
        ),
        eff_n=eff_n,
        ic=None,
        ic_tstat=None,
        icir=None,
    )
    result["diagnostic"] = diagnostic
    return result
