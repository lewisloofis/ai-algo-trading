"""
strategy_fit/families_intraday.py — Intraday (same-day) signal families.

Families
--------
run_orb_family                     — Opening-Range Breakout (30-min window)
run_intraday_momentum_family       — Morning-to-afternoon momentum
run_intraday_mean_reversion_family — VWAP overextension reversion

Long-only
---------
Every family here skips short-signal (or flat) observations rather than
sign-flipping them — IDX retail accounts cannot short-sell. See
strategy_fit/long_only.py for the shared helper and rationale (this module's
run_orb_family, commit c62eba8, is the original template for the pattern).
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from backtest.backtest import bootstrap_pvalue
from lib.stats import MIN_EFF_N
from strategy_fit.long_only import is_long_signal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Documented assumptions
# ---------------------------------------------------------------------------
ORB_WINDOW_MINUTES: int = 30
# Assumption: standard 30-minute opening range [09:00, 09:30) WIB.
# Trigger bar is the first bar at or after 09:30 (i.e. the 09:30 bar close).

MIDDAY_HOUR: int = 11
# Assumption: "midday" = 11:00 bar close for momentum and VWAP reversion.
# This is before the lunch break (12:00–13:30) to ensure signal is
# computed on pre-lunch price action.

REQUIRED_COLS: frozenset[str] = frozenset(
    {"datetime", "date", "open", "high", "low", "close", "volume"}
)

_INSUFFICIENT_N: str = "insufficient_n"
_TESTED: str = "tested"


def _check_required_cols(df: pd.DataFrame) -> None:
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"intraday DataFrame missing columns: {missing}")


def _insufficient_n_result(skip_reason: str) -> dict[str, Any]:
    return {
        "status": _INSUFFICIENT_N,
        "skip_reason": skip_reason,
        "gross_edge_bps": None,
        "hit_rate": None,
        "eff_n": None,
        "p_value": None,
        "net_edge_bps": None,
        "per_trade_sigma_bps": None,
    }


def _compute_family_metrics(
    signed_returns: np.ndarray,
) -> dict[str, Any]:
    """Compute gross_edge_bps, hit_rate, eff_n, p_value, per_trade_sigma_bps
    from signed returns array.

    per_trade_sigma_bps (plan-10 W2.1) is the sample standard deviation of
    THIS SAME signed_returns array — the actual per-trade dispersion, not a
    proxy (e.g. unconditional daily close-to-close vol).  A proxy over- or
    under-states the true per-trade sigma depending on how exposed the family
    actually is to full-day variance (e.g. ORB only enters after the opening
    range forms, so it sees less than the full day's variance) — this is why
    lib.stats.required_effective_n must be fed the real thing.  None when
    fewer than 2 observations (sample std is undefined) — callers must treat
    that as "sigma unavailable", never silently substitute a default.
    """
    eff_n = len(signed_returns)
    if eff_n < MIN_EFF_N:
        return _insufficient_n_result(f"eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}")

    gross_edge_bps = float(np.mean(signed_returns) * 1e4)
    # hit_rate: fraction where signed_return > 0
    hit_rate = float(np.mean(signed_returns > 0))
    per_trade_sigma_bps = float(np.std(signed_returns, ddof=1) * 1e4) if eff_n > 1 else None

    # One-sample bootstrap p-value: signed_returns is the realized per-trade PnL
    # (direction × magnitude), one trade per day and iid across days, so an iid
    # resample of the mean is the correct test that mean(signed_returns) > 0.
    # bootstrap_pvalue returns (p_value, pct5) where p_value = fraction of
    # resample means <= 0. Pass returns in bps to match its units.
    p_value, _pct5_net_bps = bootstrap_pvalue(signed_returns * 1e4)

    return {
        "status": _TESTED,
        "skip_reason": None,
        "gross_edge_bps": gross_edge_bps,
        "hit_rate": hit_rate,
        "eff_n": eff_n,
        "p_value": p_value,
        "net_edge_bps": None,
        "per_trade_sigma_bps": per_trade_sigma_bps,
    }


def run_orb_family(intraday_df: pd.DataFrame) -> dict[str, Any]:
    """
    Opening-Range Breakout family.

    ORB window: 09:00–09:29 (30 bars). Trigger bar: 09:30.
    Long if trigger_close > OR_high.
    Long-only: down-breaks are skipped (no short selling on IDX for retail accounts).
    Exit: last bar of day close.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        All bars for one symbol, sorted ascending.

    Returns
    -------
    dict with keys: status, skip_reason, gross_edge_bps, hit_rate,
                    eff_n, p_value, net_edge_bps.
    """
    _check_required_cols(intraday_df)

    signed_returns: list[float] = []
    df = intraday_df.copy()
    df["_dt"] = pd.to_datetime(df["datetime"])
    df["_t_min"] = df["_dt"].dt.hour * 60 + df["_dt"].dt.minute

    for date, day_df in df.groupby("date", sort=True):
        day = day_df.sort_values("_dt").reset_index(drop=True)

        # OR window: bars where 9*60 <= t_min < 9*60+30
        or_mask = (day["_t_min"] >= 9 * 60) & (day["_t_min"] < 9 * 60 + ORB_WINDOW_MINUTES)
        or_bars = day[or_mask]
        if len(or_bars) == 0:
            continue

        or_high = float(or_bars["high"].max())
        or_low = float(or_bars["low"].min())

        # Scan post-window bars (t_min >= 09:00 + K) and enter on the FIRST bar
        # whose close breaks the opening range. At most one trade/day. The
        # decision at each bar uses only the OR (established earlier) and that
        # bar's close → look-ahead-free.
        post_bars = day[day["_t_min"] >= 9 * 60 + ORB_WINDOW_MINUTES]
        if len(post_bars) == 0:
            continue

        entry_price = 0.0
        for close_val in post_bars["close"].to_numpy(dtype=float):
            if close_val > or_high:
                entry_price = close_val
                break
            # down-break is not tradeable (no short selling on IDX for retail)
        if entry_price == 0.0:
            continue  # no long breakout this day

        # Exit: last bar close of the day.
        exit_close = float(day.iloc[-1]["close"])
        ret = (exit_close - entry_price) / entry_price
        signed_returns.append(ret)

    if len(signed_returns) < MIN_EFF_N:
        return _insufficient_n_result(
            f"eff_n={len(signed_returns)} < MIN_EFF_N={MIN_EFF_N}"
        )

    return _compute_family_metrics(np.array(signed_returns))


def run_intraday_momentum_family(intraday_df: pd.DataFrame) -> dict[str, Any]:
    """
    Intraday momentum: morning direction predicts afternoon continuation.

    signal = sign(close_at_MIDDAY_HOUR - open_of_day)
    forward_return = close_of_day / close_at_MIDDAY_HOUR - 1
    Long if signal > 0 (morning up-move, bet on continuation).
    Long-only: signal <= 0 (morning down-move) is skipped — it would require
    shorting to act on, and IDX retail accounts cannot short-sell (same
    constraint as run_orb_family, commit c62eba8). A skipped day is NOT
    counted toward eff_n.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        All bars for one symbol, sorted ascending.

    Returns
    -------
    dict with keys: status, skip_reason, gross_edge_bps, hit_rate,
                    eff_n, p_value, net_edge_bps.
    """
    _check_required_cols(intraday_df)

    signed_returns: list[float] = []
    df = intraday_df.copy()
    df["_dt"] = pd.to_datetime(df["datetime"])
    df["_t_min"] = df["_dt"].dt.hour * 60 + df["_dt"].dt.minute

    for date, day_df in df.groupby("date", sort=True):
        day = day_df.sort_values("_dt").reset_index(drop=True)

        # open_of_day: first bar open
        open_of_day = float(day.iloc[0]["open"])

        # close_at_MIDDAY_HOUR: last bar in hour MIDDAY_HOUR
        midday_mask = day["_dt"].dt.hour == MIDDAY_HOUR
        midday_bars = day[midday_mask]
        if len(midday_bars) == 0:
            continue
        close_at_midday = float(midday_bars.iloc[-1]["close"])

        # close_of_day: last bar
        close_of_day = float(day.iloc[-1]["close"])

        if close_at_midday == 0:
            continue

        signal = float(np.sign(close_at_midday - open_of_day))
        if not is_long_signal(signal):
            continue  # short/flat morning move — not tradeable long-only on IDX retail

        forward_return = close_of_day / close_at_midday - 1.0
        signed_returns.append(forward_return)  # signal is always +1 here after the filter

    if len(signed_returns) < MIN_EFF_N:
        return _insufficient_n_result(
            f"eff_n={len(signed_returns)} < MIN_EFF_N={MIN_EFF_N}"
        )

    return _compute_family_metrics(np.array(signed_returns))


def run_intraday_mean_reversion_family(intraday_df: pd.DataFrame) -> dict[str, Any]:
    """
    Intraday VWAP mean reversion.

    VWAP_am = volume-weighted average price of bars 09:00–11:00.
    signal = -(close_at_11 / vwap_am - 1): price above VWAP → expect downward reversion;
    price below VWAP → signal > 0 → expect upward reversion (long entry).
    forward_return = close_of_day / close_at_11 - 1.
    Long-only: signal <= 0 (price at/above VWAP, betting on a downward
    reversion) is skipped — it would require shorting to act on, and IDX
    retail accounts cannot short-sell (same constraint as run_orb_family,
    commit c62eba8). A skipped day is NOT counted toward eff_n.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        All bars for one symbol, sorted ascending.

    Returns
    -------
    dict with keys: status, skip_reason, gross_edge_bps, hit_rate,
                    eff_n, p_value, net_edge_bps.
    """
    _check_required_cols(intraday_df)

    signed_returns: list[float] = []
    df = intraday_df.copy()
    df["_dt"] = pd.to_datetime(df["datetime"])
    df["_t_min"] = df["_dt"].dt.hour * 60 + df["_dt"].dt.minute

    for date, day_df in df.groupby("date", sort=True):
        day = day_df.sort_values("_dt").reset_index(drop=True)

        # AM bars 09:00–11:00 for VWAP
        am_mask = (day["_t_min"] >= 9 * 60) & (day["_t_min"] <= MIDDAY_HOUR * 60)
        am_bars = day[am_mask]
        if len(am_bars) == 0:
            continue

        total_vol = float(am_bars["volume"].sum())
        if total_vol == 0:
            continue
        vwap_am = float((am_bars["close"] * am_bars["volume"]).sum()) / total_vol

        # close_at_11: last bar in MIDDAY_HOUR
        midday_mask = day["_dt"].dt.hour == MIDDAY_HOUR
        midday_bars = day[midday_mask]
        if len(midday_bars) == 0:
            continue
        close_at_11 = float(midday_bars.iloc[-1]["close"])

        if vwap_am == 0 or close_at_11 == 0:
            continue

        # signal: negative of deviation (above VWAP → short signal)
        signal = -(close_at_11 / vwap_am - 1.0)
        if not is_long_signal(signal):
            continue  # price at/above VWAP — not tradeable long-only on IDX retail

        # close_of_day: last bar
        close_of_day = float(day.iloc[-1]["close"])
        forward_return = close_of_day / close_at_11 - 1.0

        signed_returns.append(forward_return)  # signal is always +1 here after the filter

    if len(signed_returns) < MIN_EFF_N:
        return _insufficient_n_result(
            f"eff_n={len(signed_returns)} < MIN_EFF_N={MIN_EFF_N}"
        )

    return _compute_family_metrics(np.array(signed_returns))
