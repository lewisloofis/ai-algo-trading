"""
strategy_fit/families_single_name.py — Single-name daily-bar signal families.

Each signal function is a pure transform: takes a daily OHLCV DataFrame,
returns a new DataFrame with ``signal`` and ``forward_return`` columns added.
Inputs are never mutated.

Families
--------
daily_momentum_signal   — 1-day close-to-close momentum; expects continuation
mean_reversion_signal   — deviation from rolling mean (negated); expects reversion
volume_breakout_signal  — volume-spike filtered by price direction

All families target ≤2 trading-day holds (the ADR-0011 deployable band).

Long-only
---------
run_single_name_family skips signal <= 0 rows rather than sign-flipping them
into a synthetic long return — IDX retail accounts cannot short-sell. See
strategy_fit/long_only.py for the shared helper and rationale (this is the
same constraint families_intraday.py:run_orb_family established in c62eba8).

Bootstrap p-value
-----------------
bootstrap_hit_rate_pvalue(signals, fwd_returns, observed_hr, n_boot, seed)
→ float   (fraction of bootstrap draws with hit_rate ≥ observed; smaller = more significant)

De-overlap
----------
Uses harness.deoverlap_daily to ensure non-overlapping forward windows.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from strategy_fit.entry_convention import (
    DEFAULT_ENTRY,
    ENTRY_SIGNAL_CLOSE_LOOKAHEAD,
    forward_return_series,
    validate_entry,
)
from strategy_fit.harness import deoverlap_daily, deoverlap_sequential
from strategy_fit.impact import InsufficientImpactHistory, TrailingImpactModel
from strategy_fit.long_only import long_only_mask

try:
    from backtest.backtest import FEE as _FEE
    ROUND_TRIP_BPS: float = (_FEE["buy"] + _FEE["sell"]) * 1e4  # 40.0
except ImportError:
    # Fallback: matches the FEE dict values exactly.
    ROUND_TRIP_BPS = 40.0

try:
    from backtest.backtest import bootstrap_pvalue
except ImportError:
    bootstrap_pvalue = None  # see run_single_name_family: falls back to bootstrap_hit_rate_pvalue

from lib.stats import MIN_EFF_N

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOOKBACK_MOMENTUM: int = 1      # 1-day look-back for momentum signal
DEFAULT_LOOKBACK_MEAN_REV: int = 5      # 5-day rolling mean for mean-reversion
DEFAULT_LOOKBACK_VOL_BREAK: int = 10    # 10-day rolling mean for volume breakout
VOLUME_SPIKE_Z: float = 1.0             # z-score threshold for volume spike signal
N_BOOTSTRAP_FAMILIES: int = 500         # bootstrap draws for p-value estimation


# ---------------------------------------------------------------------------
# Signal functions
# ---------------------------------------------------------------------------

def _finish(d: pd.DataFrame, drop_unpriced: bool) -> pd.DataFrame:
    """NaN-signal rows are always dropped. Rows without a forward return (the
    latest bars) are dropped for scoring; the live emitter keeps them
    (drop_unpriced=False) because the newest signal has no outcome yet."""
    subset = ["signal", "forward_return"] if drop_unpriced else ["signal"]
    return d.dropna(subset=subset).reset_index(drop=True)


def daily_momentum_signal(
    df: pd.DataFrame,
    hold: int = 1,
    entry: str = DEFAULT_ENTRY,
    drop_unpriced: bool = True,
) -> pd.DataFrame:
    """
    1-day close-to-close momentum signal.

    signal = close_t / close_{t-1} - 1   (positive = upward momentum)
    forward_return = measured from the entry convention (see
    strategy_fit/entry_convention.py): default next_close, close_{t+1} ->
    close_{t+1+hold}. The signal day's own close is NEVER the entry (D1).

    Rows with NaN signal or forward_return are dropped.

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV with at least a ``close`` column.
    hold : int
        Forward window in trading days.

    Returns
    -------
    pd.DataFrame
        New DataFrame with columns: date, close, signal, forward_return.
        Input DataFrame is not mutated.
    """
    d = df[["date", "close"]].copy()
    d["signal"] = d["close"].pct_change(DEFAULT_LOOKBACK_MOMENTUM)
    d["forward_return"] = forward_return_series(df, hold, entry).to_numpy()
    return _finish(d, drop_unpriced)


def mean_reversion_signal(
    df: pd.DataFrame,
    lookback: int = DEFAULT_LOOKBACK_MEAN_REV,
    hold: int = 1,
    entry: str = DEFAULT_ENTRY,
    drop_unpriced: bool = True,
) -> pd.DataFrame:
    """
    Mean-reversion (overextension) signal on daily bars.

    signal = -(close_t / rolling_mean_{lookback} - 1)
           = negative of deviation-from-mean

    Rationale: when price is above its rolling mean (positive deviation),
    the mean-reversion hypothesis predicts a downward correction, so the
    signal is negated to align with the expected direction.

    forward_return = measured from the entry convention (see
    strategy_fit/entry_convention.py): default next_close, close_{t+1} ->
    close_{t+1+hold}. The signal day's own close is NEVER the entry (D1).

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV with at least a ``close`` column.
    lookback : int
        Rolling window for mean computation (trading days).
    hold : int
        Forward window in trading days.

    Returns
    -------
    pd.DataFrame
        New DataFrame with columns: date, close, signal, forward_return.
    """
    d = df[["date", "close"]].copy()
    rolling_mean = d["close"].rolling(lookback).mean()
    # Negated: large positive signal = price was below mean → expect upward bounce.
    d["signal"] = -(d["close"] / rolling_mean - 1.0)
    d["forward_return"] = forward_return_series(df, hold, entry).to_numpy()
    return _finish(d, drop_unpriced)


def volume_breakout_signal(
    df: pd.DataFrame,
    lookback: int = DEFAULT_LOOKBACK_VOL_BREAK,
    hold: int = 1,
    entry: str = DEFAULT_ENTRY,
    drop_unpriced: bool = True,
) -> pd.DataFrame:
    """
    Volume-breakout signal: high-volume days filtered by price direction.

    signal = volume_z_score * price_direction
    where:
        volume_z_score = (volume - rolling_mean) / rolling_std
        price_direction = sign(close - open)  (positive = up-day)

    Only rows with volume_z_score > VOLUME_SPIKE_Z are meaningful signals;
    rows below are near-zero and do not drive the test (but are kept for N).

    forward_return = measured from the entry convention (see
    strategy_fit/entry_convention.py): default next_close, close_{t+1} ->
    close_{t+1+hold}. The signal day's own close is NEVER the entry (D1).

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV with ``close``, ``volume``, and optionally ``open`` columns.
    lookback : int
        Rolling window for volume mean and std.
    hold : int
        Forward window in trading days.

    Returns
    -------
    pd.DataFrame
        New DataFrame with columns: date, close, signal, forward_return.
    """
    required = {"close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"volume_breakout_signal requires columns {missing}")

    d = df[["date", "close", "volume"]].copy()
    if "open" in df.columns:
        d["open"] = df["open"].values
    else:
        # Approximate open as prev close.
        d["open"] = d["close"].shift(1)

    roll_mean = d["volume"].rolling(lookback).mean()
    roll_std = d["volume"].rolling(lookback).std(ddof=1)
    vol_z = (d["volume"] - roll_mean) / roll_std.clip(lower=1.0)
    price_dir = np.sign(d["close"] - d["open"])

    d["signal"] = vol_z * price_dir
    d["forward_return"] = forward_return_series(df, hold, entry).to_numpy()
    d = d[["date", "close", "signal", "forward_return"]]
    return _finish(d, drop_unpriced)


# ---------------------------------------------------------------------------
# Bootstrap p-value
# ---------------------------------------------------------------------------

def bootstrap_hit_rate_pvalue(
    signals: np.ndarray,
    forward_returns: np.ndarray,
    observed_hr: float,
    n_boot: int = N_BOOTSTRAP_FAMILIES,
    seed: int = 42,
) -> float:
    """
    Fraction of bootstrap label-shuffled runs where hit_rate >= observed_hr.

    Under the null, signals have no predictive power.  We shuffle signals
    (permutation test) and compute hit_rate on each shuffle.  The p-value is
    the fraction of shuffles that match or beat the observed hit_rate.

    A small p-value (e.g. < 0.05) means the observed hit_rate is unlikely
    under the null of no signal.

    Parameters
    ----------
    signals : np.ndarray
        Signal values (1-D).
    forward_returns : np.ndarray
        Forward returns aligned with signals (1-D, same length).
    observed_hr : float
        Observed empirical hit rate (fraction of times sign matches).
    n_boot : int
        Number of bootstrap permutation draws.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    float
        p-value in [0, 1].  Returns 1.0 if inputs are too short (< 10).
    """
    if len(signals) < 10:
        return 1.0

    rng = np.random.default_rng(seed)
    valid = np.isfinite(signals) & np.isfinite(forward_returns)
    s = signals[valid]
    t = forward_returns[valid]
    if len(s) < 10:
        return 1.0

    nonzero = np.abs(t) > 0
    t_nz = t[nonzero]
    s_nz = s[nonzero]
    if len(s_nz) == 0:
        return 1.0

    count_ge = 0
    for _ in range(n_boot):
        shuffled = rng.permutation(s_nz)
        hr = float(np.mean(np.sign(shuffled) == np.sign(t_nz)))
        if hr >= observed_hr:
            count_ge += 1
    return count_ge / n_boot


# ---------------------------------------------------------------------------
# Hit-rate helper (de-overlapped)
# ---------------------------------------------------------------------------

def _compute_hit_rate(deoverlapped_df: pd.DataFrame) -> tuple[float, int]:
    """
    Compute hit rate from a de-overlapped DataFrame.

    Long-only: only signal > 0 rows are tradeable on an IDX retail account
    (see strategy_fit/long_only.py). signal <= 0 rows would require shorting
    to act on and are excluded here — not sign-flipped, and not counted
    toward eff_n. hit_rate is therefore simply the fraction of long trades
    with a positive forward return.

    Returns (hit_rate, eff_n).
    """
    sig = deoverlapped_df["signal"].to_numpy(dtype=float)
    fwd = deoverlapped_df["forward_return"].to_numpy(dtype=float)
    valid = np.isfinite(sig) & np.isfinite(fwd) & (np.abs(fwd) > 0) & long_only_mask(sig)
    sig_v = sig[valid]
    fwd_v = fwd[valid]
    eff_n = int(valid.sum())
    if eff_n == 0:
        return float("nan"), 0
    hit_rate = float(np.mean(np.sign(sig_v) == np.sign(fwd_v)))
    return hit_rate, eff_n


# ---------------------------------------------------------------------------
# Per-family runner
# ---------------------------------------------------------------------------

def _price_trades_point_in_time(
    deoverlapped: pd.DataFrame,
    model: TrailingImpactModel,
    symbol: str,
    family_name: str,
) -> tuple[pd.DataFrame, np.ndarray, int]:
    """Impact at each kept row's signal day; rows without enough history are
    dropped (and counted if they were long trades).  Returns the surviving rows,
    their impacts, and the number of excluded LONG trades."""
    impacts = np.full(len(deoverlapped), np.nan)
    for i, d in enumerate(deoverlapped["date"].astype(str).to_numpy()):
        try:
            impacts[i] = model.impact_bps(d)
        except InsufficientImpactHistory:
            pass
    keep = np.isfinite(impacts)
    sig = deoverlapped["signal"].to_numpy(dtype=float)
    excluded = int(np.sum(~keep & long_only_mask(sig)))
    if excluded:
        logger.warning(
            "%s/%s: %d trade(s) excluded from scoring: < %d bars of history before the signal day",
            symbol, family_name, excluded, model.min_bars,
        )
    return deoverlapped[keep], impacts[keep], excluded


def run_single_name_family(
    df: pd.DataFrame,
    signal_fn: "callable",
    symbol: str,
    min_edge_to_clear_bps: float,
    family_name: str,
    horizon_trading_days: int,
    entry: str | None = None,
    impact_model: "TrailingImpactModel | None" = None,
) -> dict:
    """
    Run a single-name signal function against daily OHLCV and return a result dict.

    ``impact_model`` (point-in-time impact, see impact.TrailingImpactModel): when
    given, every kept trade is priced at its own signal day s from ADV/vol
    windows ending at s.  A trade with too little history before s is EXCLUDED
    (counted in ``impact_excluded_n``, never priced from full history) and the
    returned ``per_trade_impact_bps`` is aligned with the trades behind
    ``gross_edge_bps`` / ``eff_n``.  None (default) leaves the result unchanged.

    Steps:
    1. Apply signal_fn to produce signal and forward_return columns. The
       forward return is measured from the NEXT session (entry convention,
       strategy_fit/entry_convention.py; D1 fix).
    2+3. Long-only filter (signal > 0, see strategy_fit/long_only.py — IDX retail
       accounts cannot short; same constraint as families_intraday.py:
       run_orb_family, commit c62eba8), then SEQUENTIAL de-overlap of the
       long-only rows (D2 fix, harness.deoverlap_sequential): when flat and a
       long signal fires at s, enter s+1, exit s+1+hold, resume at the exit
       day. A filtered-out row does not count toward eff_n.
       entry="signal_close_LOOKAHEAD" reproduces the legacy pipeline (same-close
       entry, stride-hold grid over all rows, mask after) for old reports only.
    4. Check MIN_EFF_N threshold (on the long-only-filtered count).
    5. Compute hit_rate, gross_edge_bps, net_edge_bps, p_value.

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV (columns: date, close, volume, [open, high, low]).
    signal_fn : callable
        One of: daily_momentum_signal, mean_reversion_signal, volume_breakout_signal.
        Must accept (df, hold=int) and return df with signal/forward_return.
    symbol : str
        Universe name (for logging).
    min_edge_to_clear_bps : float
        Structural friction floor for this name (plan-09 annotation).
    family_name : str
        Human-readable family identifier (for the result dict).
    horizon_trading_days : int
        Hold duration in trading days.
    entry : str or None
        Entry convention (None = signal_fn's default, next_close). Passed to
        signal_fn only when given, so stub/wrapped signal_fns keep working.

    Returns
    -------
    dict
        Keys: status, skip_reason, hit_rate, eff_n, gross_edge_bps,
              net_edge_bps, p_value, per_trade_sigma_bps, null_edge_bps,
              edge_over_null_bps.  Suitable for constructing CellResult.
              per_trade_sigma_bps (plan-10 W2.1) mirrors families_intraday.py:
              the sample std of the long-only signed_returns array that
              produced gross_edge_bps, None on every non-"tested" path and
              when fewer than 2 observations. null_edge_bps is the mean
              forward_return of the SAME de-overlapped population with the
              long-only signal filter removed (the "always-long null" —
              see strategy_fit/report.py's null-edge blocking gate);
              edge_over_null_bps = gross_edge_bps - null_edge_bps. Both None
              on every non-"tested" path.
    """
    try:
        if entry is None:
            signal_df = signal_fn(df, hold=horizon_trading_days)
        else:
            signal_df = signal_fn(df, hold=horizon_trading_days, entry=validate_entry(entry))
    except Exception as exc:
        logger.warning("%s/%s: signal_fn raised %s", symbol, family_name, exc)
        return {
            "status": "insufficient_n",
            "skip_reason": f"signal_fn error: {exc}",
            "hit_rate": None,
            "eff_n": 0,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
            "per_trade_sigma_bps": None,
            "null_edge_bps": None,
            "edge_over_null_bps": None,
        }

    if len(signal_df) == 0:
        return {
            "status": "insufficient_n",
            "skip_reason": "signal_fn returned empty DataFrame",
            "hit_rate": None,
            "eff_n": 0,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
            "per_trade_sigma_bps": None,
            "null_edge_bps": None,
            "edge_over_null_bps": None,
        }

    legacy = entry == ENTRY_SIGNAL_CLOSE_LOOKAHEAD
    if legacy:
        deoverlapped = deoverlap_daily(signal_df, hold=horizon_trading_days)
    else:
        deoverlapped = deoverlap_sequential(
            signal_df,
            hold=horizon_trading_days,
            eligible=long_only_mask(signal_df["signal"].to_numpy(dtype=float)),
        )
    trade_impact: np.ndarray | None = None
    impact_excluded_n = 0
    if impact_model is not None:
        deoverlapped, trade_impact, impact_excluded_n = _price_trades_point_in_time(
            deoverlapped, impact_model, symbol, family_name
        )
    hit_rate, eff_n = _compute_hit_rate(deoverlapped)

    if eff_n < MIN_EFF_N:
        return {
            "status": "insufficient_n",
            "skip_reason": f"eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}"
            + (f" ({impact_excluded_n} trade(s) excluded: <{impact_model.min_bars} bars of history for impact)"
               if impact_excluded_n else ""),
            "hit_rate": hit_rate,
            "eff_n": eff_n,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
            "per_trade_sigma_bps": None,
            "null_edge_bps": None,
            "edge_over_null_bps": None,
        }

    fwd = deoverlapped["forward_return"].to_numpy(dtype=float)
    sig_vals = deoverlapped["signal"].to_numpy(dtype=float)
    # Long-only (strategy_fit/long_only.py): keep only signal > 0. signal <= 0
    # would require shorting to act on and is excluded — not sign-flipped, and
    # not counted toward eff_n (matches _compute_hit_rate's filter above).
    # After this filter sign(sig_vals) is always +1, so the realized per-trade
    # return is just forward_return — no sign multiplication needed.
    valid_mask = np.isfinite(sig_vals) & np.isfinite(fwd) & long_only_mask(sig_vals)
    signed_returns = fwd[valid_mask]
    per_trade_impact = None if trade_impact is None else trade_impact[valid_mask]
    gross_edge_bps = float(np.nanmean(signed_returns) * 1e4)
    # net_edge_bps is NOT pre-computed here: the runner computes true
    # net-after-full-cost as (gross - hurdle) to avoid double-counting the
    # friction floor.  The key is preserved in the dict for API stability.

    # per_trade_sigma_bps (plan-10 W2.1, mirrors families_intraday.py): the
    # sample std of THIS SAME long-only-filtered signed_returns array — the
    # actual per-trade dispersion, never a proxy (e.g. unconditional daily
    # close-to-close vol). Guarded on len(signed_returns), NOT on eff_n:
    # _compute_hit_rate's mask additionally requires |forward_return| > 0,
    # which valid_mask above does not, so len(signed_returns) can differ from
    # eff_n — sigma must be measured on the array it actually describes.
    per_trade_sigma_bps = (
        float(np.std(signed_returns, ddof=1) * 1e4) if len(signed_returns) > 1 else None
    )

    # null_edge_bps (always-long null). Matching timing: the same forward_return
    # (same entry convention and hold) averaged over EVERY priced day, i.e.
    # buying unconditionally at any session — the mean over all possible
    # sequential grid phases, so the null does not depend on where the scan
    # starts. Legacy (LOOKAHEAD) keeps its historical definition: the same
    # stride-hold-grid population with the long-only filter removed.
    if legacy:
        null_fwd = deoverlapped["forward_return"].to_numpy(dtype=float)
    else:
        null_fwd = signal_df["forward_return"].to_numpy(dtype=float)
    null_fwd = null_fwd[np.isfinite(null_fwd)]
    null_edge_bps = float(np.mean(null_fwd) * 1e4) if len(null_fwd) > 0 else None
    edge_over_null_bps = (
        gross_edge_bps - null_edge_bps if null_edge_bps is not None else None
    )

    # p-value: one-sample bootstrap that mean(signed_returns) > 0. After the
    # long-only filter, signal is always +1, so bootstrap_hit_rate_pvalue's
    # sign-permutation test degenerates (permuting an all-positive array can
    # never change its sign pattern, so every permutation draw reproduces the
    # same hit rate) — use the realized-return one-sample test instead, the
    # same test families_intraday.py's long-only families use.
    if bootstrap_pvalue is not None:
        p_value, _pct5_bps = bootstrap_pvalue(signed_returns * 1e4)
    else:  # pragma: no cover - only hit if backtest.backtest is unavailable
        sig = deoverlapped["signal"].to_numpy(dtype=float)
        p_value = bootstrap_hit_rate_pvalue(sig, fwd, hit_rate)

    return {
        "status": "tested",
        "skip_reason": None,
        "hit_rate": hit_rate,
        "eff_n": eff_n,
        "gross_edge_bps": gross_edge_bps,
        "net_edge_bps": None,
        "p_value": p_value,
        "per_trade_sigma_bps": per_trade_sigma_bps,
        "null_edge_bps": null_edge_bps,
        "edge_over_null_bps": edge_over_null_bps,
        "per_trade_impact_bps": None if per_trade_impact is None else [float(x) for x in per_trade_impact],
        "impact_excluded_n": impact_excluded_n,
        "trade_returns_bps": [float(x) * 1e4 for x in signed_returns],
        "trade_signal_dates": [str(x) for x in deoverlapped["date"].to_numpy()[valid_mask]],
    }
