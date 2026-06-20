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

from strategy_fit.harness import deoverlap_daily

try:
    from backtest.backtest import FEE as _FEE
    ROUND_TRIP_BPS: float = (_FEE["buy"] + _FEE["sell"]) * 1e4  # 40.0
except ImportError:
    # Fallback: matches the FEE dict values exactly.
    ROUND_TRIP_BPS = 40.0

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

def daily_momentum_signal(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
    """
    1-day close-to-close momentum signal.

    signal = close_t / close_{t-1} - 1   (positive = upward momentum)
    forward_return = close_{t+hold} / close_t - 1

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
    d["forward_return"] = d["close"].shift(-hold) / d["close"] - 1.0
    d = d.dropna(subset=["signal", "forward_return"]).reset_index(drop=True)
    return d


def mean_reversion_signal(
    df: pd.DataFrame,
    lookback: int = DEFAULT_LOOKBACK_MEAN_REV,
    hold: int = 1,
) -> pd.DataFrame:
    """
    Mean-reversion (overextension) signal on daily bars.

    signal = -(close_t / rolling_mean_{lookback} - 1)
           = negative of deviation-from-mean

    Rationale: when price is above its rolling mean (positive deviation),
    the mean-reversion hypothesis predicts a downward correction, so the
    signal is negated to align with the expected direction.

    forward_return = close_{t+hold} / close_t - 1

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
    d["forward_return"] = d["close"].shift(-hold) / d["close"] - 1.0
    d = d.dropna(subset=["signal", "forward_return"]).reset_index(drop=True)
    return d


def volume_breakout_signal(
    df: pd.DataFrame,
    lookback: int = DEFAULT_LOOKBACK_VOL_BREAK,
    hold: int = 1,
) -> pd.DataFrame:
    """
    Volume-breakout signal: high-volume days filtered by price direction.

    signal = volume_z_score * price_direction
    where:
        volume_z_score = (volume - rolling_mean) / rolling_std
        price_direction = sign(close - open)  (positive = up-day)

    Only rows with volume_z_score > VOLUME_SPIKE_Z are meaningful signals;
    rows below are near-zero and do not drive the test (but are kept for N).

    forward_return = close_{t+hold} / close_t - 1

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
    d["forward_return"] = d["close"].shift(-hold) / d["close"] - 1.0
    d = d[["date", "close", "signal", "forward_return"]]
    d = d.dropna(subset=["signal", "forward_return"]).reset_index(drop=True)
    return d


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

    Returns (hit_rate, eff_n).  hit_rate = fraction where sign(signal)==sign(fwd_return).
    """
    sig = deoverlapped_df["signal"].to_numpy(dtype=float)
    fwd = deoverlapped_df["forward_return"].to_numpy(dtype=float)
    valid = np.isfinite(sig) & np.isfinite(fwd) & (np.abs(fwd) > 0)
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

def run_single_name_family(
    df: pd.DataFrame,
    signal_fn: "callable",
    symbol: str,
    min_edge_to_clear_bps: float,
    family_name: str,
    horizon_trading_days: int,
) -> dict:
    """
    Run a single-name signal function against daily OHLCV and return a result dict.

    Steps:
    1. Apply signal_fn to produce signal and forward_return columns.
    2. De-overlap observations (keep rows ≥ horizon_trading_days apart).
    3. Check MIN_EFF_N threshold.
    4. Compute hit_rate, gross_edge_bps, net_edge_bps, p_value.

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

    Returns
    -------
    dict
        Keys: status, skip_reason, hit_rate, eff_n, gross_edge_bps,
              net_edge_bps, p_value.  Suitable for constructing CellResult.
    """
    try:
        signal_df = signal_fn(df, hold=horizon_trading_days)
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
        }

    deoverlapped = deoverlap_daily(signal_df, hold=horizon_trading_days)
    hit_rate, eff_n = _compute_hit_rate(deoverlapped)

    if eff_n < MIN_EFF_N:
        return {
            "status": "insufficient_n",
            "skip_reason": f"eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}",
            "hit_rate": hit_rate,
            "eff_n": eff_n,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    fwd = deoverlapped["forward_return"].to_numpy(dtype=float)
    sig_vals = deoverlapped["signal"].to_numpy(dtype=float)
    valid_mask = np.isfinite(sig_vals) & np.isfinite(fwd)
    # Edge = mean(sign(signal) × forward_return): captures directional trade P&L.
    # A strategy trades in the signal direction; expected P&L per trade = this.
    signed_returns = np.sign(sig_vals[valid_mask]) * fwd[valid_mask]
    gross_edge_bps = float(np.nanmean(signed_returns) * 1e4)
    # net_edge_bps is NOT pre-computed here: the runner computes true
    # net-after-full-cost as (gross - hurdle) to avoid double-counting the
    # friction floor.  The key is preserved in the dict for API stability.

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
    }
