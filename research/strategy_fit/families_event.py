"""
strategy_fit/families_event.py — Event-driven strategy families (pooled across universe).

For event-driven families, observations are pooled across all universe names and
MIN_EFF_N=30 is applied on the pooled non-overlapping event count.  This is the
primary (and often only) viable shape for very-wide-tick names like DEWA (plan-10 §4).

Event types
-----------
gap_event            — overnight gap > threshold; signal = sign of gap
volume_spike_event   — volume z-score > threshold; signal = price direction on spike day
foreign_flow_spike_event — if foreign_flow column present; signal = sign of flow

Each detector:
    1. Produces a per-symbol events DataFrame with columns:
       date, symbol, signal, forward_return
    2. De-overlaps per-symbol (greedy forward walk)
    3. Is pooled across universe names

The pooled runner then checks effective-N and computes pooled hit rate.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from strategy_fit.harness import deoverlap_daily, effective_n_event_pooled
from strategy_fit.families_single_name import (
    ROUND_TRIP_BPS,
    N_BOOTSTRAP_FAMILIES,
    bootstrap_hit_rate_pvalue,
)

try:
    from lib.stats import MIN_EFF_N
except ImportError:
    MIN_EFF_N = 30  # fallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAP_THRESHOLD_PCT: float = 0.02        # 2% overnight gap to trigger event
VOLUME_SPIKE_Z_THRESHOLD: float = 1.5  # volume z-score threshold
FOREIGN_FLOW_Z_THRESHOLD: float = 1.5  # foreign-flow z-score threshold
VOLUME_LOOKBACK: int = 20              # rolling window for volume baseline
FOREIGN_FLOW_LOOKBACK: int = 20        # rolling window for foreign-flow baseline


# ---------------------------------------------------------------------------
# Per-symbol event detectors
# ---------------------------------------------------------------------------

def _detect_gap_events(
    df: pd.DataFrame,
    symbol: str,
    hold: int,
    threshold_pct: float = GAP_THRESHOLD_PCT,
) -> pd.DataFrame:
    """
    Detect overnight-gap events for a single symbol.

    An event triggers when |gap| > threshold_pct where
    gap = (open_t - close_{t-1}) / close_{t-1}.

    Signal = sign of gap (positive gap → signal=+1 tests continuation).

    Returns DataFrame with columns: date, symbol, signal, forward_return.
    """
    d = df.copy()
    if "open" not in d.columns:
        return pd.DataFrame(columns=["date", "symbol", "signal", "forward_return"])

    d["gap"] = (d["open"] - d["close"].shift(1)) / d["close"].shift(1)
    d["forward_return"] = d["close"].shift(-hold) / d["close"] - 1.0
    # Trigger on large absolute gap
    events = d[np.abs(d["gap"]) > threshold_pct].copy()
    events["signal"] = np.sign(events["gap"])
    events["symbol"] = symbol
    return events[["date", "symbol", "signal", "forward_return"]].dropna()


def _detect_volume_spike_events(
    df: pd.DataFrame,
    symbol: str,
    hold: int,
    lookback: int = VOLUME_LOOKBACK,
    z_threshold: float = VOLUME_SPIKE_Z_THRESHOLD,
) -> pd.DataFrame:
    """
    Detect volume-spike events for a single symbol.

    An event triggers when volume z-score > z_threshold.
    Signal = price direction on the spike day (sign of close - open).

    Returns DataFrame with columns: date, symbol, signal, forward_return.
    """
    d = df.copy()
    roll_mean = d["volume"].rolling(lookback).mean()
    roll_std = d["volume"].rolling(lookback).std(ddof=1).clip(lower=1.0)
    vol_z = (d["volume"] - roll_mean) / roll_std

    if "open" in d.columns:
        price_dir = np.sign(d["close"] - d["open"])
    else:
        price_dir = np.sign(d["close"].diff())

    d["vol_z"] = vol_z
    d["signal"] = price_dir
    d["forward_return"] = d["close"].shift(-hold) / d["close"] - 1.0

    events = d[d["vol_z"] > z_threshold].copy()
    events["symbol"] = symbol
    return events[["date", "symbol", "signal", "forward_return"]].dropna()


def _detect_foreign_flow_spike_events(
    df: pd.DataFrame,
    symbol: str,
    hold: int,
    lookback: int = FOREIGN_FLOW_LOOKBACK,
    z_threshold: float = FOREIGN_FLOW_Z_THRESHOLD,
) -> pd.DataFrame:
    """
    Detect foreign-flow-spike events for a single symbol.

    Uses the ``net_foreign`` column (= foreignbuy - foreignsell daily net).
    The cumulative ``foreignflow`` API column is NOT used here — it is a running
    total and must not be differenced inside this detector.

    Returns empty DataFrame if ``net_foreign`` column is absent.
    Signal = sign of net_foreign on the spike day.
    Spike criterion: |net_foreign z-score| > z_threshold over a trailing window.
    """
    if "net_foreign" not in df.columns:
        return pd.DataFrame(columns=["date", "symbol", "signal", "forward_return"])

    d = df.copy()
    roll_mean = d["net_foreign"].rolling(lookback).mean()
    # Clip std to a small positive floor so we don't divide by 0 on flat series;
    # a flat series (std=0 → clipped to 1.0) will produce z-scores near 0 and
    # thus no events — correct behaviour.
    roll_std = d["net_foreign"].rolling(lookback).std(ddof=1).clip(lower=1.0)
    flow_z = (d["net_foreign"] - roll_mean) / roll_std

    d["flow_z"] = flow_z
    d["signal"] = np.sign(d["net_foreign"])
    d["forward_return"] = d["close"].shift(-hold) / d["close"] - 1.0

    events = d[np.abs(d["flow_z"]) > z_threshold].copy()
    events["symbol"] = symbol
    return events[["date", "symbol", "signal", "forward_return"]].dropna()


# ---------------------------------------------------------------------------
# Event detector registry
# ---------------------------------------------------------------------------

EVENT_DETECTORS: dict[str, "callable"] = {
    "gap_event": _detect_gap_events,
    "volume_spike_event": _detect_volume_spike_events,
    "foreign_flow_spike_event": _detect_foreign_flow_spike_events,
}


# ---------------------------------------------------------------------------
# Pooled event runner
# ---------------------------------------------------------------------------

def run_event_family(
    hist_dict: dict[str, pd.DataFrame],
    event_detector_name: str,
    horizon_trading_days: int,
    universe_min_edge_map: dict[str, float] | None = None,
) -> dict:
    """
    Run a pooled event-driven family across all universe names.

    Flow:
    1. For each symbol, detect events with the named detector.
    2. De-overlap each symbol's events independently (per-symbol greedy walk).
    3. Pool all de-overlapped events across symbols.
    4. Check pooled effective-N ≥ MIN_EFF_N.
    5. Compute pooled hit_rate, gross_edge_bps, net_edge_bps, p_value.

    Parameters
    ----------
    hist_dict : dict[str, pd.DataFrame]
        Mapping of symbol → daily OHLCV DataFrame.
    event_detector_name : str
        Key into EVENT_DETECTORS: "gap_event", "volume_spike_event",
        or "foreign_flow_spike_event".
    horizon_trading_days : int
        Hold duration in trading days (forward window).
    universe_min_edge_map : dict[str, float] or None
        Structural friction floors per name.  Used for net_edge annotation
        only (not for gating — that is done in the grid runner).

    Returns
    -------
    dict
        Keys: status, skip_reason, hit_rate, eff_n, gross_edge_bps,
              net_edge_bps, p_value.
    """
    detector = EVENT_DETECTORS.get(event_detector_name)
    if detector is None:
        return {
            "status": "insufficient_n",
            "skip_reason": f"Unknown event detector: {event_detector_name}",
            "hit_rate": None,
            "eff_n": 0,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    per_symbol_events: list[pd.DataFrame] = []
    for symbol, df in hist_dict.items():
        try:
            events = detector(df, symbol=symbol, hold=horizon_trading_days)
        except Exception as exc:
            logger.warning("Event detector %s failed for %s: %s", event_detector_name, symbol, exc)
            continue
        if len(events) == 0:
            continue
        # De-overlap per symbol.
        deoverlapped = deoverlap_daily(
            events.reset_index(drop=True), hold=horizon_trading_days
        )
        per_symbol_events.append(deoverlapped)

    if not per_symbol_events:
        return {
            "status": "insufficient_n",
            "skip_reason": "No events detected across any symbol",
            "hit_rate": None,
            "eff_n": 0,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    pooled = pd.concat(per_symbol_events, ignore_index=True)
    eff_n = len(pooled)

    if eff_n < MIN_EFF_N:
        return {
            "status": "insufficient_n",
            "skip_reason": f"Pooled eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}",
            "hit_rate": None,
            "eff_n": eff_n,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    sig = pooled["signal"].to_numpy(dtype=float)
    fwd = pooled["forward_return"].to_numpy(dtype=float)
    valid = np.isfinite(sig) & np.isfinite(fwd) & (np.abs(fwd) > 0)
    sig_v = sig[valid]
    fwd_v = fwd[valid]
    eff_n_valid = int(valid.sum())

    if eff_n_valid == 0:
        return {
            "status": "insufficient_n",
            "skip_reason": "No valid signal/return pairs after NaN filter",
            "hit_rate": None,
            "eff_n": 0,
            "gross_edge_bps": None,
            "net_edge_bps": None,
            "p_value": None,
        }

    hit_rate = float(np.mean(np.sign(sig_v) == np.sign(fwd_v)))
    gross_edge_bps = float(np.nanmean(fwd_v) * 1e4)
    net_edge_bps = gross_edge_bps - ROUND_TRIP_BPS
    p_value = bootstrap_hit_rate_pvalue(sig_v, fwd_v, hit_rate)

    return {
        "status": "tested",
        "skip_reason": None,
        "hit_rate": hit_rate,
        "eff_n": eff_n_valid,
        "gross_edge_bps": gross_edge_bps,
        "net_edge_bps": net_edge_bps,
        "p_value": p_value,
    }
