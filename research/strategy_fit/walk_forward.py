"""
strategy_fit/walk_forward.py — Rolling walk-forward loop and decay metric.

Implements the rolling fit→test→roll persistence test required by plan-10 §2
and shared verbatim with plan-11 (live decay monitor).  The same rolling-Sharpe
/ hit-rate threshold used here to gate strategy entry is the same criterion
plan-11 uses to pull a live position.

Algorithm
---------
1. Split the available history into (fit_window, test_window) pairs, rolling
   forward by test_window steps each iteration.
2. On each test window, compute:
     - rolling Sharpe: mean(fwd_returns) / std(fwd_returns) [de-overlapped obs]
     - rolling hit_rate: fraction where sign(signal) == sign(fwd_return)
3. A cell is marked *decayed* if the fraction of windows where both the
   rolling Sharpe < DECAY_THRESHOLD_SHARPE AND hit_rate < DECAY_THRESHOLD_HR
   exceeds DECAY_MAJORITY_FRAC.
4. When fewer than min_windows complete windows exist, the cell is conservatively
   marked decayed (no evidence of persistence → no deployment).

This module exports the decay metric constants so plan-11 can import them directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from strategy_fit.harness import deoverlap_daily

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — exported for plan-11 reuse
# ---------------------------------------------------------------------------

DECAY_THRESHOLD_SHARPE: float = 0.0
"""
A window's rolling Sharpe must exceed this to count as "alive".
0.0 = any positive mean return on the test set is required.
"""

DECAY_THRESHOLD_HR: float = 0.50
"""
A window's rolling hit-rate must exceed this to count as "alive".
0.50 = at least directionally right on the test set.
"""

DECAY_MAJORITY_FRAC: float = 0.60
"""
Fraction of windows that must fail BOTH thresholds to mark the cell decayed.
0.60 = more than half the windows must look bad → conservative kill.
"""

# Default walk-forward hyperparameters (trading days).
DEFAULT_FIT_WINDOW: int = 120
DEFAULT_TEST_WINDOW: int = 40
DEFAULT_MIN_WINDOWS: int = 3


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowResult:
    """Metrics for a single (fit, test) walk-forward window."""

    fit_start: int     # row index (inclusive)
    fit_end: int       # row index (exclusive)
    test_start: int    # row index (inclusive)
    test_end: int      # row index (exclusive)
    sharpe: float
    hit_rate: float
    eff_n: int


@dataclass(frozen=True)
class WalkForwardResult:
    """
    Aggregate result of a walk-forward sweep over a single (symbol, family) cell.

    Attributes
    ----------
    windows : list[WindowResult]
        Per-window metrics.
    mean_sharpe : float
        Mean rolling Sharpe across all test windows.
    mean_hit_rate : float
        Mean rolling hit-rate across all test windows.
    is_decayed : bool
        True if the cell is marked non-persistent (see DECAY_MAJORITY_FRAC).
    n_windows : int
        Total number of completed walk-forward windows.
    """

    windows: list[WindowResult]
    mean_sharpe: float
    mean_hit_rate: float
    is_decayed: bool
    n_windows: int


# ---------------------------------------------------------------------------
# Core walk-forward function
# ---------------------------------------------------------------------------

def walk_forward_cell(
    df: pd.DataFrame,
    signal_fn: Callable[..., pd.DataFrame],
    horizon_trading_days: int,
    fit_window: int = DEFAULT_FIT_WINDOW,
    test_window: int = DEFAULT_TEST_WINDOW,
    min_windows: int = DEFAULT_MIN_WINDOWS,
    decay_threshold_sharpe: float = DECAY_THRESHOLD_SHARPE,
    decay_threshold_hr: float = DECAY_THRESHOLD_HR,
    decay_majority_frac: float = DECAY_MAJORITY_FRAC,
) -> WalkForwardResult:
    """
    Run a rolling walk-forward sweep on a single (symbol, family) cell.

    Steps:
    1. Apply signal_fn to the full DataFrame to get signal and forward_return.
    2. Slice into (fit, test) windows; roll forward by test_window.
    3. On each test slice: de-overlap and compute rolling Sharpe and hit-rate.
    4. Assess persistence: mark decayed if enough windows fall below thresholds.

    Note: signal_fn is applied to the full DataFrame first (not per window) to
    avoid look-ahead on the signal definition itself.  The fit/test split is
    on the rows used for measurement.  For v1 this is conservative (the signal
    parameters are not re-tuned per window); plan-11 can extend to per-window
    re-fitting if needed.

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV with at least ``close`` column.
    signal_fn : callable
        Signal function with signature (df, hold=int) → DataFrame.
    horizon_trading_days : int
        Hold duration in trading days.
    fit_window : int
        Number of rows in each fit/training window (not actively used for
        signal re-fitting in v1, but defines the skip before each test).
    test_window : int
        Number of rows in each test/OOS window.
    min_windows : int
        Minimum complete windows required; fewer → mark as decayed.
    decay_threshold_sharpe : float
        Per-window Sharpe threshold below which a window is "bad".
    decay_threshold_hr : float
        Per-window hit-rate threshold below which a window is "bad".
    decay_majority_frac : float
        Fraction of windows that must be "bad" to mark the cell decayed.

    Returns
    -------
    WalkForwardResult
        Immutable result struct.
    """
    # Apply signal function to full data.
    try:
        signal_df = signal_fn(df, hold=horizon_trading_days)
    except Exception as exc:
        logger.warning("walk_forward_cell: signal_fn raised %s", exc)
        return WalkForwardResult(
            windows=[],
            mean_sharpe=float("nan"),
            mean_hit_rate=float("nan"),
            is_decayed=True,
            n_windows=0,
        )

    if len(signal_df) < fit_window + test_window:
        # Insufficient history for even one window.
        return WalkForwardResult(
            windows=[],
            mean_sharpe=float("nan"),
            mean_hit_rate=float("nan"),
            is_decayed=True,
            n_windows=0,
        )

    n_rows = len(signal_df)
    windows: list[WindowResult] = []

    start = 0
    while start + fit_window + test_window <= n_rows:
        fit_start = start
        fit_end = start + fit_window
        test_start = fit_end
        test_end = min(test_start + test_window, n_rows)

        test_slice = signal_df.iloc[test_start:test_end].reset_index(drop=True)
        deoverlapped = deoverlap_daily(test_slice, hold=horizon_trading_days)

        if len(deoverlapped) == 0:
            start += test_window
            continue

        fwd = deoverlapped["forward_return"].to_numpy(dtype=float)
        sig = deoverlapped["signal"].to_numpy(dtype=float)
        valid = np.isfinite(sig) & np.isfinite(fwd)
        fwd_v = fwd[valid]
        sig_v = sig[valid]
        eff_n = int(valid.sum())

        if eff_n == 0:
            start += test_window
            continue

        # Rolling Sharpe: mean / std of forward returns (de-overlapped).
        sharpe: float
        if len(fwd_v) > 1 and float(np.std(fwd_v, ddof=1)) > 0:
            sharpe = float(np.mean(fwd_v) / np.std(fwd_v, ddof=1))
        else:
            sharpe = float(np.mean(fwd_v)) if len(fwd_v) > 0 else float("nan")

        # Rolling hit-rate on non-zero returns.
        nonzero = np.abs(fwd_v) > 0
        if int(nonzero.sum()) > 0:
            hit_rate = float(
                np.mean(np.sign(sig_v[nonzero]) == np.sign(fwd_v[nonzero]))
            )
        else:
            hit_rate = float("nan")

        windows.append(
            WindowResult(
                fit_start=fit_start,
                fit_end=fit_end,
                test_start=test_start,
                test_end=test_end,
                sharpe=sharpe,
                hit_rate=hit_rate if np.isfinite(hit_rate) else 0.5,
                eff_n=eff_n,
            )
        )
        start += test_window

    n_windows = len(windows)

    if n_windows < min_windows:
        # Insufficient walk-forward evidence → conservative: mark decayed.
        return WalkForwardResult(
            windows=windows,
            mean_sharpe=float("nan"),
            mean_hit_rate=float("nan"),
            is_decayed=True,
            n_windows=n_windows,
        )

    sharpes = np.array([w.sharpe for w in windows])
    hit_rates = np.array([w.hit_rate for w in windows])

    mean_sharpe = float(np.nanmean(sharpes))
    mean_hit_rate = float(np.nanmean(hit_rates))

    # A window is "bad" when BOTH sharpe < threshold AND hit_rate < threshold.
    bad_mask = (sharpes < decay_threshold_sharpe) & (hit_rates < decay_threshold_hr)
    bad_frac = float(np.mean(bad_mask))
    is_decayed = bad_frac >= decay_majority_frac

    return WalkForwardResult(
        windows=windows,
        mean_sharpe=mean_sharpe,
        mean_hit_rate=mean_hit_rate,
        is_decayed=is_decayed,
        n_windows=n_windows,
    )
