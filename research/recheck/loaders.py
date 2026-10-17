"""
recheck/loaders.py — read the local daily store once per run (offline: parquet files only, no network).

``load_universe_bars`` is the shortlist's ``load_bars`` / ``discover_universe`` with the data directory as
a parameter (the shortlist module hard-codes ``research/data/daily``): same cleaning, same trailing
``vol60`` / ``adv60`` columns, same "traded within 7 days of as_of" universe rule. ``load_panel`` is
``run_idx_structural.load_hist`` + ``families_idx_structural.build_panel`` with an as_of cut.

Both are cached per (directory, as_of, a fingerprint of the files), so the twelve specs of one run read
the store once, and a fixture rewritten between two calls is never served stale. Callers must not mutate
what they get back.
"""

from __future__ import annotations

import glob
import os
from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

import strategy_fit.families_idx_structural as fx

MIN_BARS = 100
"""Names with fewer cleaned bars are left out (shortlist_2026_10.load_bars)."""
MIN_NAMES_TRADING = 10
UNIVERSE_RECENCY_DAYS = 7
"""A name is in the universe when its last traded bar is within this many days of as_of."""


def _fingerprint(daily_dir: str) -> tuple:
    out = []
    for p in sorted(glob.glob(os.path.join(daily_dir, "*.parquet"))):
        st = os.stat(p)
        out.append((os.path.basename(p), st.st_size, st.st_mtime_ns))
    return tuple(out)


def latest_bar_date(daily_dir: str) -> date | None:
    """The newest date that carries real volume in any stored file (an in-progress zero-volume bar is ignored)."""
    best = None
    for p in glob.glob(os.path.join(daily_dir, "*.parquet")):
        d = pd.read_parquet(p, columns=["date", "volume"])
        d = d[pd.to_numeric(d["volume"], errors="coerce") > 0]
        if len(d):
            last = str(d["date"].astype(str).max())
            best = last if best is None or last > best else best
    return date.fromisoformat(best) if best else None


def _clean(df: pd.DataFrame, as_of: str) -> pd.DataFrame | None:
    df = df.copy()
    df["date"] = df["date"].astype(str)
    for c in ("open", "high", "low", "close", "volume", "value", "net_foreign"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "volume"])
    df = df[df["date"] <= as_of].sort_values("date").reset_index(drop=True)
    if len(df) < MIN_BARS:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):      # a stored zero close is a missing price, not an error
        lr = np.log(df["close"]).diff()
    df["vol60"] = lr.rolling(59, min_periods=19).std(ddof=1) * 1e4
    turn = df["value"] if ("value" in df and (df["value"] > 0).any()) else df["close"] * df["volume"]
    df["adv60"] = turn.rolling(60, min_periods=20).median()
    return df


@lru_cache(maxsize=4)
def _universe(daily_dir: str, as_of: str, fingerprint: tuple) -> dict[str, pd.DataFrame]:
    cutoff = (date.fromisoformat(as_of) - timedelta(days=UNIVERSE_RECENCY_DAYS)).isoformat()
    out: dict[str, pd.DataFrame] = {}
    for p in sorted(glob.glob(os.path.join(daily_dir, "*.parquet"))):
        df = _clean(pd.read_parquet(p), as_of)
        if df is None:
            continue
        traded = df[df["volume"] > 0]
        if len(traded) and traded["date"].iloc[-1] >= cutoff:
            out[os.path.splitext(os.path.basename(p))[0]] = df
    return out


def trading_calendar(daily_dir: str, as_of: date) -> list[str]:
    """Sorted trading dates up to as_of: dates on which at least ``MIN_NAMES_TRADING`` universe names traded
    (the same rule ``build_panel`` uses to drop market holidays padded with zero-volume rows)."""
    counts: dict[str, int] = {}
    for df in load_universe_bars(daily_dir, as_of).values():
        for d in df.loc[df["volume"] > 0, "date"]:
            counts[d] = counts.get(d, 0) + 1
    need = min(MIN_NAMES_TRADING, max(counts.values(), default=0))
    return sorted(d for d, c in counts.items() if c >= need)


def load_universe_bars(daily_dir: str, as_of: date) -> dict[str, pd.DataFrame]:
    return _universe(os.path.abspath(daily_dir), as_of.isoformat(), _fingerprint(daily_dir))


@lru_cache(maxsize=4)
def _panel(daily_dir: str, as_of: str, fingerprint: tuple):
    hist = {}
    for p in sorted(glob.glob(os.path.join(daily_dir, "*.parquet"))):
        df = pd.read_parquet(p)
        df = df[df["date"].astype(str) <= as_of]
        if len(df):
            hist[os.path.splitext(os.path.basename(p))[0]] = df
    panel = fx.build_panel(hist, min_names_trading=10)
    eligible = fx.eligibility(panel)
    bear = fx.market_regime(panel, eligible)["bear"]
    return panel, eligible, bear


def load_panel(daily_dir: str, as_of: date):
    """(Panel, eligible mask, bear flag) on the full history up to as_of, as run_idx_structural builds them."""
    return _panel(os.path.abspath(daily_dir), as_of.isoformat(), _fingerprint(daily_dir))
