"""
recheck/idx.py — post-verdict records of the alpha-search 2026-10 families (plan 26).

Reuses ``families_idx_structural`` unchanged (panel, eligibility, ``mom_12_1`` factor, ARB detector,
dividend schedule, turn-of-month windows, the book / event / calendar back-tests and the current-coefficient
cost model) with the notionals ``run_idx_structural`` used (sleeve 300M; 30M per event name).

No family code is rewritten. The post-verdict restriction is made by masking ``eligible`` (and, for the
event families, the signal mask) to dates strictly after the verdict:

  * ``run_factor_backtest`` starts its sequential chain at the first date with enough eligible names, and
    ``run_calendar_backtest`` builds its always-long basket null only from eligible rows, so both start
    fresh after the verdict and their nulls are post-verdict only;
  * ``build_event_trades`` computes ``null_name`` as a per-name mean over the WHOLE calendar year, which
    would reach into pre-verdict days; it is replaced here by the same name's always-long mean over
    eligible post-verdict signal days (the same definition, restricted to the window).

The bear / non-bear flag comes from ``market_regime`` and is carried per record for the regime report.
It is descriptive only and has unbounded memory (200-day MA, 252-day high, hysteresis), so it is the one
column the poison test does not compare.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

import strategy_fit.families_idx_structural as fx
from recheck.loaders import load_panel
from strategy_fit.run_idx_structural import BASE_NOTIONAL, DIV_MIN_YIELD, SLEEVE

MOMENTUM_FACTOR = "mom_12_1"
FACTOR_HOLD = 21
LIMIT_HOLD = 5
CALENDAR_KIND = "turn_of_month"
COLUMNS = ["signal_date", "entry_date", "gross_bps", "null_bps", "cost_bps", "regime_bear"]
LOOKBACK_BARS = 320
"""mom_12_1 reads close[s-252]; eligibility needs 252 bars of history and 60-bar windows; with margin."""


def _post_mask(panel: fx.Panel, start_excl: date) -> np.ndarray:
    return np.asarray(panel.dates > pd.Timestamp(start_excl))


def _mask_rows(frame: pd.DataFrame, post: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(np.asarray(frame, dtype=bool) & post[:, None], index=frame.index, columns=frame.columns)


def _frame(panel, bear, signal_pos, entry_pos, gross, null, cost) -> pd.DataFrame:
    signal_pos, entry_pos = np.asarray(signal_pos, dtype=int), np.asarray(entry_pos, dtype=int)
    sdates = panel.dates[signal_pos]
    return pd.DataFrame({
        "signal_date": sdates.strftime("%Y-%m-%d"), "entry_date": panel.dates[entry_pos].strftime("%Y-%m-%d"),
        "gross_bps": np.asarray(gross, dtype=float), "null_bps": np.asarray(null, dtype=float),
        "cost_bps": np.asarray(cost, dtype=float),
        "regime_bear": bear.reindex(sdates).fillna(False).to_numpy(dtype=bool),
    }, columns=COLUMNS)


def _event_records(panel, bear, trades: pd.DataFrame, fwd: pd.DataFrame, elig_null: pd.DataFrame, post) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=COLUMNS)
    null_by_name = fwd.where(_mask_rows(elig_null, post)).mean(axis=0)
    null = trades["symbol"].map(null_by_name).to_numpy(dtype=float) * 1e4
    return _frame(panel, bear, trades["pos"], trades["pos"].to_numpy() + 1, trades["gross"].to_numpy() * 1e4,
                  null, trades["cost_bps"].to_numpy())


def factor_books_builder(daily_dir: str, signal_start_exclusive: date, as_of: date) -> pd.DataFrame:
    panel, eligible, bear = load_panel(daily_dir, as_of)
    elig_post = _mask_rows(eligible, _post_mask(panel, signal_start_exclusive))
    per = fx.run_factor_backtest(panel, fx.FACTORS[MOMENTUM_FACTOR](panel), FACTOR_HOLD, eligible=elig_post,
                                 phase=0, notional_idr=SLEEVE)
    if per.empty:
        return pd.DataFrame(columns=COLUMNS)
    return _frame(panel, bear, per["pos"], per["pos"] + 1, per["gross"] * 1e4, per["null"] * 1e4, per["cost_bps"])


def limit_move_builder(daily_dir: str, signal_start_exclusive: date, as_of: date) -> pd.DataFrame:
    panel, eligible, bear = load_panel(daily_dir, as_of)
    post = _post_mask(panel, signal_start_exclusive)
    elig_prev = eligible.shift(1, fill_value=False)
    mask = _mask_rows(fx.detect_limit_events(panel, "down"), post)
    trades = fx.build_event_trades(panel, mask, LIMIT_HOLD, "next_close", eligible=elig_prev,
                                   notional_idr=BASE_NOTIONAL, skip_locked=False)
    fwd = fx.fwd_total_return(panel, LIMIT_HOLD, "next_close")
    return _event_records(panel, bear, trades, fwd, elig_prev, post)


def dividend_builder(daily_dir: str, signal_start_exclusive: date, as_of: date) -> pd.DataFrame:
    panel, eligible, bear = load_panel(daily_dir, as_of)
    post = _post_mask(panel, signal_start_exclusive)
    mask, hold = fx.dividend_event_mask(panel, "runup", min_yield=max(DIV_MIN_YIELD, 1e-9))
    trades = fx.build_event_trades(panel, _mask_rows(mask, post), hold, eligible=eligible, notional_idr=BASE_NOTIONAL)
    fwd = fx.fwd_total_return(panel, hold)
    return _event_records(panel, bear, trades, fwd, eligible, post)


def calendar_builder(daily_dir: str, signal_start_exclusive: date, as_of: date) -> pd.DataFrame:
    panel, eligible, bear = load_panel(daily_dir, as_of)
    post = _post_mask(panel, signal_start_exclusive)
    windows = [(p, h) for p, h in fx.calendar_windows(panel, CALENDAR_KIND) if p >= 1 and post[p - 1]]
    per = fx.run_calendar_backtest(panel, windows, eligible=_mask_rows(eligible, post), notional_idr=SLEEVE)
    if per.empty:
        return pd.DataFrame(columns=COLUMNS)
    return _frame(panel, bear, per["pos"] - 1, per["pos"], per["gross"] * 1e4, per["null"] * 1e4, per["cost_bps"])


BUILDERS: dict[str, Callable[[str, date, date], pd.DataFrame]] = {
    "factor_books": factor_books_builder,
    "limit_move_events": limit_move_builder,
    "dividend_events": dividend_builder,
    "calendar_effects": calendar_builder,
}
