"""
lib/session.py — JSX session/lunch constants and time-range helpers.

All time values are WIB (UTC+7).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Trading session constants
# ---------------------------------------------------------------------------

LUNCH_START_H, LUNCH_START_M = 12, 0
LUNCH_END_H, LUNCH_END_M = 13, 30

SESSION_START_H, SESSION_START_M = 9, 0
SESSION_AM_END_H, SESSION_AM_END_M = 12, 0
SESSION_PM_START_H, SESSION_PM_START_M = 13, 30
SESSION_END_H, SESSION_END_M = 16, 15


# ---------------------------------------------------------------------------
# Lunch-gap helpers
# ---------------------------------------------------------------------------

def _is_lunch(dt: pd.Timestamp) -> bool:
    """Return True if *dt* falls in the lunch gap [12:00, 13:30) WIB."""
    t_min = dt.hour * 60 + dt.minute
    return (LUNCH_START_H * 60 + LUNCH_START_M) <= t_min < (LUNCH_END_H * 60 + LUNCH_END_M)


def _crosses_lunch(t_ns: int, t_fwd_ns: int, wib_dt: pd.Timestamp) -> bool:
    """
    Return True if the forward window [t_ns, t_fwd_ns] crosses the lunch boundary.

    A boundary crossing occurs when the window spans [12:00, 13:30).
    We check by reconstructing the approximate fwd timestamp and testing
    whether either endpoint is in the gap, or the gap lies between them.
    """
    if pd.isna(wib_dt):
        return True
    fwd_dt = wib_dt + pd.Timedelta(nanoseconds=(t_fwd_ns - t_ns))
    t_min = wib_dt.hour * 60 + wib_dt.minute
    fwd_t_min = fwd_dt.hour * 60 + fwd_dt.minute
    lunch_s = LUNCH_START_H * 60 + LUNCH_START_M

    if _is_lunch(wib_dt) or _is_lunch(fwd_dt):
        return True
    if t_min < lunch_s and fwd_t_min >= lunch_s:
        return True
    if fwd_t_min < lunch_s and t_min >= lunch_s:
        return True
    return False


# ---------------------------------------------------------------------------
# Session filter
# ---------------------------------------------------------------------------

def filter_continuous_session(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only rows that fall within the JSX continuous trading session:
      AM: [09:00, 12:00)  PM: [13:30, 16:15]

    Requires *df* to have a ``_wib_dt`` column (datetime, already parsed).
    """
    t_min = df["_wib_dt"].dt.hour * 60 + df["_wib_dt"].dt.minute
    am_s = SESSION_START_H * 60 + SESSION_START_M
    am_e = SESSION_AM_END_H * 60 + SESSION_AM_END_M
    pm_s = SESSION_PM_START_H * 60 + SESSION_PM_START_M
    pm_e = SESSION_END_H * 60 + SESSION_END_M
    mask = ((t_min >= am_s) & (t_min < am_e)) | ((t_min >= pm_s) & (t_min <= pm_e))
    return df[mask].reset_index(drop=True)
