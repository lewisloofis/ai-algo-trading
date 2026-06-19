"""
lib/loaders.py — CSV loading and cleaning helpers for sbcap features files.
"""

from __future__ import annotations

import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    """
    Load a features CSV emitted by ``sbcap features``.

    - All columns except ``iso_wib`` are coerced to numeric.
    - Rows are sorted by ``recv_ns``.
    - A ``_wib_dt`` datetime column is added from ``iso_wib``.
    """
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    for col in [c for c in df.columns if c != "iso_wib"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("recv_ns").reset_index(drop=True)
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop rows with missing/zero top-of-book bid or ask price/volume,
    and rows with unparseable ``_wib_dt``.

    Assumes ``_wib_dt`` is already present (added by :func:`load_csv`).
    """
    df = df.copy()
    df = df.dropna(subset=["bid_px_0", "ask_px_0", "bid_vol_0", "ask_vol_0", "_wib_dt"])
    df = df[(df["bid_px_0"] > 0) & (df["ask_px_0"] > 0)]
    df = df[(df["bid_vol_0"] > 0) & (df["ask_vol_0"] > 0)]
    return df.reset_index(drop=True)
