"""
strategy_fit/trade_export.py — per-trade backtest records, regenerated offline.

The strategy-fit grid reports each cell as a handful of averages (``CellResult``);
the individual trades behind them are never saved. The dashboard's strategy detail
pages need those trades (a cumulative-profit curve, a trade list, "average without
the best 5 trades"), so this module re-derives them from local daily bars.

Nothing is re-implemented. The trade list is produced by exactly the calls
``families_single_name.run_single_name_family`` makes:

    signal frame   SINGLE_NAME_SIGNAL_FNS[family](df, hold=hold)   (default next_close entry)
    eligibility    long_only.long_only_mask(signal)
    selection      harness.deoverlap_sequential(signal_frame, hold, eligible=mask)
    return         entry_convention.forward_return_series(df, hold) at the signal row

The only new code is bookkeeping: attaching the signal/entry/exit dates and prices to
each selected row so a person can read them. ``test_trade_export.py`` pins the result
to ``run_single_name_family``'s own gross edge so the two cannot drift apart.

Entry convention: next_close (strategy_fit.entry_convention.DEFAULT_ENTRY). A signal on
session s is acted on at the close of session s+1 and exited ``hold`` sessions later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from strategy_fit.entry_convention import DEFAULT_ENTRY, ENTRY_NEXT_CLOSE, forward_return_series
from strategy_fit.harness import deoverlap_sequential
from strategy_fit.local_source import DEFAULT_DATA_DIR, LocalParquetDataSource
from strategy_fit.long_only import long_only_mask
from strategy_fit.run_strategy_fit import (
    HISTORY_END,
    HISTORY_START,
    SINGLE_NAME_SIGNAL_FNS,
)

ENTRY_USED = DEFAULT_ENTRY
"""The entry convention every exported trade uses (corrected next-session entry)."""

_RETURN_TOLERANCE = 1e-9


@dataclass(frozen=True)
class BacktestTrade:
    """One long-only trade the backtest took."""

    signal_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    gross_return: float
    """exit/entry - 1 as a fraction, before any cost."""


@dataclass(frozen=True)
class CellTrades:
    """Every trade of one (symbol, family, hold) cell, plus the bars they came from."""

    symbol: str
    family: str
    hold: int
    trades: tuple[BacktestTrade, ...]
    bars: pd.DataFrame
    """The daily bars used (date, close), for the buy-and-hold comparison."""
    always_long_return: float | None = None
    """
    Mean forward return of buying on EVERY priced day for this hold, ignoring the signal: the
    harness's always-long null (families_single_name.run_single_name_family ``null_fwd``), as a
    fraction. The like-for-like answer to "what did just holding the stock do, per trade?".
    """


def export_trades(df: pd.DataFrame, family: str, hold: int) -> list[BacktestTrade]:
    """
    The trades ``run_single_name_family`` would take on *df*, with dates and prices.

    *df* is daily OHLCV sorted ascending with a default RangeIndex (as
    LocalParquetDataSource.daily_history returns it).
    """
    return export_with_baseline(df, family, hold)[0]


def export_with_baseline(
    df: pd.DataFrame, family: str, hold: int
) -> tuple[list[BacktestTrade], float | None]:
    """``export_trades`` plus the always-long mean return (the harness's null) from the same frame."""
    if ENTRY_USED != ENTRY_NEXT_CLOSE:
        raise RuntimeError("trade_export assumes next_close entry; the default convention changed")
    if family not in SINGLE_NAME_SIGNAL_FNS:
        raise KeyError(f"unknown single-name family {family!r}; known: {sorted(SINGLE_NAME_SIGNAL_FNS)}")

    signal_df = SINGLE_NAME_SIGNAL_FNS[family](df, hold=hold)
    kept = deoverlap_sequential(
        signal_df,
        hold=hold,
        eligible=long_only_mask(signal_df["signal"].to_numpy(dtype=float)),
    )
    dates = df["date"].astype(str).to_numpy()
    close = df["close"].to_numpy(dtype=float)
    # The signal frame is re-indexed (NaN-warmup rows dropped), so map back to the bars
    # by date, never by index label.
    pos_by_date = {d: i for i, d in enumerate(dates)}
    fwd_by_pos = forward_return_series(df, hold).to_numpy(dtype=float)

    out: list[BacktestTrade] = []
    for signal_date, harness_ret in zip(kept["date"].astype(str), kept["forward_return"]):
        pos = pos_by_date[signal_date]
        entry_pos, exit_pos = pos + 1, pos + 1 + hold
        ret = fwd_by_pos[pos]
        if not np.isfinite(ret) or exit_pos >= len(df):
            continue  # unpriced tail: the harness drops it too (finite forward_return filter)
        if abs(ret - harness_ret) > _RETURN_TOLERANCE:
            raise RuntimeError(f"forward_return mismatch at {signal_date}")
        entry_px, exit_px = close[entry_pos], close[exit_pos]
        if abs((exit_px / entry_px - 1.0) - ret) > _RETURN_TOLERANCE:
            raise RuntimeError(
                f"trade_export disagrees with forward_return_series at {dates[pos]}: "
                "the harness's entry convention changed; update this module"
            )
        out.append(
            BacktestTrade(
                signal_date=dates[pos],
                entry_date=dates[entry_pos],
                exit_date=dates[exit_pos],
                entry_price=float(entry_px),
                exit_price=float(exit_px),
                gross_return=float(ret),
            )
        )
    null_fwd = signal_df["forward_return"].to_numpy(dtype=float)
    null_fwd = null_fwd[np.isfinite(null_fwd)]
    return out, (float(np.mean(null_fwd)) if len(null_fwd) else None)


def load_bars(
    symbol: str,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    start: str = HISTORY_START,
    end: str = HISTORY_END,
) -> pd.DataFrame:
    """Daily bars exactly as the grid's offline re-score reads them."""
    return LocalParquetDataSource([symbol], data_dir).daily_history(symbol, start, end)


def export_cell(
    symbol: str,
    family: str,
    hold: int,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    start: str = HISTORY_START,
    end: str = HISTORY_END,
) -> CellTrades:
    """Load the bars for *symbol* and export one cell's trades."""
    return export_cells([(symbol, family, hold)], data_dir, start, end)[(symbol, family, hold)]


def export_cells(
    cells: Sequence[tuple[str, str, int]],
    data_dir: str | Path = DEFAULT_DATA_DIR,
    start: str = HISTORY_START,
    end: str = HISTORY_END,
) -> dict[tuple[str, str, int], CellTrades]:
    """Export many cells, loading each symbol's bars once."""
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    out: dict[tuple[str, str, int], CellTrades] = {}
    for symbol, family, hold in cells:
        if symbol not in bars_by_symbol:
            bars_by_symbol[symbol] = load_bars(symbol, data_dir, start, end)
        bars = bars_by_symbol[symbol]
        trades, always_long = export_with_baseline(bars, family, hold)
        out[(symbol, family, hold)] = CellTrades(
            symbol=symbol,
            family=family,
            hold=hold,
            trades=tuple(trades),
            bars=bars[["date", "close"]].copy(),
            always_long_return=always_long,
        )
    return out
