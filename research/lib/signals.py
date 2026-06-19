"""
lib/signals.py — Signal computation helpers (OBI1, forward mid-returns).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lib.session import _is_lunch, _crosses_lunch


# ---------------------------------------------------------------------------
# OBI1
# ---------------------------------------------------------------------------

def add_obi1(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add ``mid`` and ``OBI1`` (top-of-book order-book imbalance) columns.

    OBI1 = (bid_vol_0 - ask_vol_0) / (bid_vol_0 + ask_vol_0).
    Returns a copy; does not mutate the input.
    """
    df = df.copy()
    bvol0 = df["bid_vol_0"].to_numpy(dtype=float)
    avol0 = df["ask_vol_0"].to_numpy(dtype=float)
    bid0 = df["bid_px_0"].to_numpy(dtype=float)
    ask0 = df["ask_px_0"].to_numpy(dtype=float)
    df["mid"] = (bid0 + ask0) / 2.0
    total = bvol0 + avol0
    safe = np.where(total > 0, total, np.nan)
    df["OBI1"] = (bvol0 - avol0) / safe
    return df


# ---------------------------------------------------------------------------
# Forward mid-returns
# ---------------------------------------------------------------------------

def add_forward_returns(df: pd.DataFrame, horizons_s: list[int]) -> pd.DataFrame:
    """
    For each horizon *h* (seconds), join each row at time *t* to the row at
    *t + h* via ``numpy.searchsorted`` on ``recv_ns``.

    Rows that cross the lunch gap or have no data within 5 s of *t + h* are
    dropped (NaN).  Adds columns ``fwd_mid_ret_Xs`` for each horizon.

    Requires columns: ``recv_ns``, ``mid``, ``_wib_dt``.
    Returns a copy; does not mutate the input.
    """
    df = df.copy()
    recv_ns_arr = df["recv_ns"].to_numpy(dtype=np.int64)
    mid_arr = df["mid"].to_numpy(dtype=float)
    wib_arr = df["_wib_dt"].to_numpy()

    TOLERANCE_NS = 5 * int(1e9)

    for h in horizons_s:
        h_ns = h * int(1e9)
        fwd_mid = np.full(len(df), np.nan)
        valid = np.zeros(len(df), dtype=bool)

        for i in range(len(df)):
            t = recv_ns_arr[i]
            t_h = t + h_ns
            idx = int(np.searchsorted(recv_ns_arr, t_h, side="left"))
            if idx >= len(recv_ns_arr):
                continue
            t_matched = recv_ns_arr[idx]
            if t_matched > t_h + TOLERANCE_NS:
                continue
            wib_dt = pd.Timestamp(wib_arr[i])
            if _crosses_lunch(t, t_matched, wib_dt):
                continue
            fwd_mid[i] = mid_arr[idx] - mid_arr[i]
            valid[i] = True

        fwd_mid[~valid] = np.nan
        df[f"fwd_mid_ret_{h}s"] = fwd_mid

    return df
