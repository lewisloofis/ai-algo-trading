"""
recheck/fixtures.py — a small synthetic daily store for the re-check tests (no real prices, no network).

``write_store`` writes ``n_names`` random-walk names as parquet files in the same columns as
``research/data/daily``. Names are liquid enough to be eligible (ADV well above 2e9 IDR), a few carry a
-16% close-at-low day (an ARB event) and a dividend of 3% of price inside the post-verdict window, so every
family has something to fire on. ``poison`` rewrites every bar before ``cutoff_idx`` as NaN or as garbage.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

N_NAMES = 40
N_BARS = 760
VERDICT_IDX = 600
"""Row index of the 'verdict date' in the fixture calendar; signals must come strictly after it."""


def calendar(n_bars: int = N_BARS) -> list[str]:
    return list(pd.bdate_range("2023-01-02", periods=n_bars).strftime("%Y-%m-%d"))


def _frame(rng: np.random.Generator, dates: list[str], event_idx: int, dividend_idx: int, with_events: bool):
    n = len(dates)
    ret = rng.normal(0.0004, 0.02, n)
    if with_events:
        ret[event_idx] = -0.16
    close = np.round(1000 * np.exp(np.cumsum(ret)), 0)
    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = np.round(prev * (1 + rng.normal(0, 0.012, n)), 0)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    if with_events:
        low[event_idx] = close[event_idx]
        open_[event_idx] = prev[event_idx]
        high[event_idx] = open_[event_idx]
    volume = np.round(rng.lognormal(np.log(6e6), 0.5, n), 0)
    dividend = np.zeros(n)
    if with_events:
        dividend[dividend_idx] = round(0.03 * close[dividend_idx - 1], 0)
    return pd.DataFrame({
        "date": dates, "open": open_, "high": high, "low": low, "close": close, "volume": volume,
        "value": np.round(close * volume, 0), "net_foreign": np.round(rng.normal(0, 3e8, n), 0),
        "foreignbuy": np.round(rng.uniform(1e8, 5e8, n), 0), "foreignsell": np.round(rng.uniform(1e8, 5e8, n), 0),
        "dividend": dividend,
    })


def write_store(path: str, n_names: int = N_NAMES, n_bars: int = N_BARS, seed: int = 7) -> list[str]:
    """Write the store; returns the calendar. Every third name has an ARB day and a dividend after VERDICT_IDX."""
    os.makedirs(path, exist_ok=True)
    dates = calendar(n_bars)
    rng = np.random.default_rng(seed)
    for i in range(n_names):
        with_events = i % 3 == 0
        df = _frame(rng, dates, VERDICT_IDX + 25 + (i % 5) * 7, VERDICT_IDX + 40 + (i % 4) * 9, with_events)
        df.to_parquet(os.path.join(path, f"N{i:03d}.parquet"), index=False)
    return dates


def poison(src: str, dst: str, cutoff_idx: int, mode: str) -> None:
    """Copy the store to ``dst`` with every row before ``cutoff_idx`` replaced ('nan' or 'garbage')."""
    os.makedirs(dst, exist_ok=True)
    rng = np.random.default_rng(99)
    num = ["open", "high", "low", "close", "volume", "value", "net_foreign", "foreignbuy", "foreignsell", "dividend"]
    for name in sorted(os.listdir(src)):
        if not name.endswith(".parquet"):
            continue
        df = pd.read_parquet(os.path.join(src, name))
        if mode == "nan":
            df.loc[: cutoff_idx - 1, num] = np.nan
        elif mode == "garbage":
            for c in num:
                df.loc[: cutoff_idx - 1, c] = rng.uniform(-1e12, 1e12, cutoff_idx)
        else:
            raise ValueError(mode)
        df.to_parquet(os.path.join(dst, name), index=False)
