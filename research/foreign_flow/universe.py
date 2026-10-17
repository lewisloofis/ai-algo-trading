"""
foreign_flow/universe.py - point-in-time top-40 universe (plan 22 section 2.2).

Definitions (frozen choices, documented here because the plan leaves them open):

* value traded = the bars' own ``value`` column when it is > 0, else ``close * volume``.
* At month-end ``E`` (calendar last day), the window is the last 60 market sessions
  whose date is **<= E**. Those bars are all published by the close of ``E``, so the
  membership is known at month-end and is applied to sessions **strictly after** ``E``.
  No bar after ``E`` is ever read: no look-ahead.
* A market session is any date present in at least one stock's bars (index series such as
  IHSG are excluded from both the ranking and the calendar).
* A symbol's median is taken over all 60 sessions with a missing day counted as 0, so a name
  that traded on fewer than 31 of the 60 sessions (or was not yet listed) gets median 0 and is
  not eligible. Ties are broken by symbol.
* Output: top 40 per month-end from 2016-12 to 2026-09 inclusive.

CLI: ``python -m foreign_flow.universe`` writes data/foreign_flow/universe_monthly.csv.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

from foreign_flow import DAILY_DIR, UNIVERSE_CSV
from index_events import INDEX_SYMBOLS

TOP_N = 40
WINDOW = 60
START_MONTH = "2016-12"
END_MONTH = "2026-09"
CSV_COLUMNS = ["month_end", "rank", "symbol", "median_value"]


def value_traded(df: pd.DataFrame) -> pd.Series:
    """Value traded per bar: ``value`` where > 0, else ``close * volume``; NaN when neither."""
    fallback = df["close"] * df["volume"]
    if "value" in df.columns:
        val = df["value"].astype("float64")
        return val.where(val > 0, fallback)
    return fallback


def load_daily(daily_dir: Path = DAILY_DIR, exclude=INDEX_SYMBOLS) -> dict[str, pd.DataFrame]:
    """symbol -> frame with columns date (datetime64), close, volume, value (value_traded)."""
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(Path(daily_dir).glob("*.parquet")):
        sym = path.stem
        if sym in exclude:
            continue
        df = pd.read_parquet(path)
        if not {"date", "close", "volume"} <= set(df.columns):
            continue
        frame = pd.DataFrame({
            "date": pd.to_datetime(df["date"]),
            "close": df["close"].astype("float64"),
            "volume": df["volume"].astype("float64"),
        })
        frame["value"] = value_traded(df.assign(**{c: frame[c] for c in ("close", "volume")}))
        out[sym] = frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return out


def value_panel(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Wide date x symbol panel of value traded (NaN = no bar)."""
    return pd.DataFrame({s: f.set_index("date")["value"] for s, f in daily.items()}).sort_index()


def month_ends(start: str = START_MONTH, end: str = END_MONTH) -> list[pd.Timestamp]:
    periods = pd.period_range(start, end, freq="M")
    return [p.end_time.normalize() for p in periods]


def monthly_universe(panel: pd.DataFrame, *, start: str = START_MONTH, end: str = END_MONTH,
                     top_n: int = TOP_N, window: int = WINDOW) -> pd.DataFrame:
    """Top-``top_n`` per month-end by median value over the last ``window`` sessions <= month-end."""
    rows = []
    for me in month_ends(start, end):
        sub = panel.loc[:me].tail(window)           # sessions <= month-end only
        if len(sub) < window:
            continue                                # not enough history to rank
        med = sub.fillna(0.0).median(axis=0)
        med = med[med > 0].rename_axis("symbol").reset_index(name="median_value")
        med = med.sort_values(["median_value", "symbol"], ascending=[False, True]).head(top_n)
        for rank, r in enumerate(med.itertuples(index=False), start=1):
            rows.append((me.strftime("%Y-%m-%d"), rank, r.symbol, float(r.median_value)))
    return pd.DataFrame(rows, columns=CSV_COLUMNS)


def download_list(universe: pd.DataFrame) -> list[str]:
    return sorted(universe["symbol"].unique())


def read_universe_csv(path: Path = UNIVERSE_CSV) -> pd.DataFrame:
    return pd.read_csv(path)


def write_universe_csv(universe: pd.DataFrame, path: Path = UNIVERSE_CSV) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    universe.to_csv(tmp, index=False)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the point-in-time top-40 universe (plan 22 s2.2).")
    ap.add_argument("--daily-dir", type=Path, default=DAILY_DIR)
    ap.add_argument("--out", type=Path, default=UNIVERSE_CSV)
    args = ap.parse_args(argv)
    daily = load_daily(args.daily_dir)
    uni = monthly_universe(value_panel(daily))
    write_universe_csv(uni, args.out)
    syms = download_list(uni)
    latest = max(f["date"].max() for f in daily.values())
    stale = [s for s in syms if (latest - daily[s]["date"].max()).days > 60]
    print(f"month-ends: {uni['month_end'].nunique()}  rows: {len(uni)}  unique symbols: {len(syms)}")
    print(f"members with local bars: {len(syms)}; without local bars: 0 "
          "(membership is derived from local bars, so a delisted name absent locally cannot be seen here)")
    print(f"members whose last local bar is >60d before the store's latest bar: {len(stale)} {stale}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
