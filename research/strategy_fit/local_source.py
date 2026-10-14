"""
strategy_fit/local_source.py — offline DataSource over research/data/daily/*.parquet.

Used only to re-score the daily/event grid without a token or network (the D1/D2
re-score, plan 10). The universe is an explicit symbol list (never inferred from
the network), and ``intraday_history`` is deliberately unavailable so intraday
cells are skipped, not faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "daily"


class LocalParquetDataSource:
    def __init__(self, symbols: Iterable[str], data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
        self._dir = Path(data_dir)
        self._symbols = {s.upper() for s in symbols}
        if not self._symbols:
            raise ValueError("LocalParquetDataSource needs a non-empty symbol list")

    def funding_universe(self) -> set[str]:
        return {s for s in self._symbols if (self._dir / f"{s}.parquet").is_file()}

    def daily_history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        path = self._dir / f"{symbol.upper()}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"local daily bars not found: {path}")
        df = pd.read_parquet(path)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
        df = df.dropna(subset=["close", "volume"])
        df["date"] = df["date"].astype(str)
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        return df.sort_values("date").reset_index(drop=True)

    def intraday_history(self, symbol: str, lookback_days: int = 90) -> pd.DataFrame:
        raise NotImplementedError("local daily source has no intraday bars")
