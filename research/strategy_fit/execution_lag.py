"""
strategy_fit/execution_lag.py — Execution-lag sensitivity for the surviving
PTRO daily cells (plan 13, docs/plans/00-roadmap-research-to-live.md §7).

Motivation
----------
Every daily single-name family (strategy_fit/families_single_name.py) derives
its signal from ``close_t`` and measures ``forward_return`` from ``close_t``
to ``close_{t+hold}`` — the SAME closing price the signal was computed from.
That is a conventional close-to-close backtest measurement, but it is not an
attainable execution: ``close_t`` is only observed after the session ends, so
a real order cannot also transact at ``close_t``. A realistic retail account
enters at ``open_{t+1}`` at the earliest.

This module re-scores the two surviving PTRO cells (``daily_momentum`` h2 and
``volume_breakout`` h2 — TINS `mean_reversion` h2 is OUT OF SCOPE, see below)
under four execution conventions. The SIGNAL is always computed from data
available up to and including ``close_t`` — only the transacted entry/exit
prices change between conventions:

    C1  close->close             enter close_t,     exit close_{t+hold}   (published)
    C2  next-open->next-open     enter open_{t+1},  exit open_{t+hold+1}  (realistic retail)
    C3  next-close->next-close   enter close_{t+1}, exit close_{t+hold+1} (full-day lag, close fills)
    C4  next-open->close         enter open_{t+1},  exit close_{t+hold}

Note the cost stack (impact, friction floor, financing) depends only on the
symbol and the hold, not on which convention transacted it — so
``hurdle_bps`` is identical across C1-C4 for a given cell.  Only
``gross_edge_bps`` (and therefore ``net_edge_bps``) moves.

Data substrate
--------------
Daily OHLCV is reconstructed by aggregating ``research/data/intraday/PTRO.parquet``
(screener/intraday_store.py's accrual store) to one row per session. That
store is chartbit-sourced 1-minute bars, already restricted to the JSX
continuous session (lib/session.py: 09:00-12:00, 13:30-16:15 WIB) at
ingestion (screener/screen_universe.py's LiveDataSource.intraday_history) —
there is no separate session filter to apply here; confirmed by inspection
(no bars outside that window, no lunch-gap bars). It is NOT the tick-capture
(sbcap) substrate in data/tick_bars[_tagged]/ — the column set
(datetime, date, open, high, low, close, volume, value, net_foreign) matches
intraday_store.py's schema, not tick_bars.py's. Because both this store and
the published run's daily history are chartbit-sourced, aggregating this
store's 1-min bars to daily bars is expected to closely approximate — not
exactly reproduce — chartbit's own daily bars (see run() / the substrate
check emitted by the CLI): the intraday store's history starts 2026-03-25
(~108 trading days) versus the published run's 631-row history from
2024-01-02, so eff_n here is far smaller than the published cells' eff_n.

TINS out of scope: the intraday store only holds BREN/BRPT/ENRG/PTRO/RAJA/
RATU/TPIA. TINS `mean_reversion` h2 cannot be reconstructed from local data
and is not evaluated here — it is not substituted with another name.

Tick-volume caveat (plan-12 §5d): the ~16-21% frame loss measured on the
busiest names is a property of the CAPTURED WEBSOCKET TICK STREAM
(data/tick_bars*), not of this chartbit intraday store. It does not apply to
the volume reconstructed here. It is mentioned only because a reader familiar
with plan-12 might otherwise assume it does.

Method — reuse, not reimplementation
-------------------------------------
Every step below calls the SAME functions the published cells were scored
with: families_single_name.{daily_momentum_signal,volume_breakout_signal,
run_single_name_family} (signal, long-only filter, de-overlap, eff_n gate,
gross_edge_bps), strategy_fit.impact (--use-calibrated coefficients),
strategy_fit.financing (best_feasible_hurdle_bps; ADR-0014 settlement-date
accrual), and screener.metrics.min_spread_bps (plan-09 friction floor). The
only new logic in this module is ``conventional_forward_return`` — computing
forward_return under C2/C3/C4 has no existing implementation to reuse,
because every family up to now has hardcoded the C1 convention.

CLI
---
    python -m strategy_fit.execution_lag [--store-dir DIR] [--no-calibrated]
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from lib.stats import MIN_EFF_N
from screener.intraday_store import load_intraday
from screener.metrics import min_spread_bps
from strategy_fit.daily_walk_forward import IS_FRAC, walk_forward_daily
from strategy_fit.families_single_name import (
    ROUND_TRIP_BPS,
    daily_momentum_signal,
    mean_reversion_signal,
    run_single_name_family,
    volume_breakout_signal,
)
from strategy_fit.financing import best_feasible_hurdle_bps
from strategy_fit.harness import deoverlap_daily
from strategy_fit.impact import compute_impact_bps_from_daily_df
from strategy_fit.long_only import long_only_mask
from strategy_fit.run_strategy_fit import HISTORY_START

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYMBOL: str = "PTRO"
"""The only symbol this module evaluates — TINS has no local intraday store."""

HOLD_TRADING_DAYS: int = 2
"""Both surviving PTRO cells are h2 (docs/plans/00-roadmap-research-to-live.md §7)."""

# The two surviving PTRO cells, family_name -> signal_fn. Both use HOLD_TRADING_DAYS.
PTRO_CELLS: dict[str, Callable[..., pd.DataFrame]] = {
    "daily_momentum": daily_momentum_signal,
    "volume_breakout": volume_breakout_signal,
}

CONVENTIONS: tuple[str, ...] = ("C1", "C2", "C3", "C4")

CONVENTION_LABELS: dict[str, str] = {
    "C1": "close -> close (published)",
    "C2": "next-open -> next-open",
    "C3": "next-close -> next-close",
    "C4": "next-open -> close",
}

# Published figures, docs/plans/00-roadmap-research-to-live.md §7 (2026-09-05).
# net_bps / impact_bps from the live 115-name, --use-calibrated run over
# chartbit's daily history from HISTORY_START (run_strategy_fit.py's own
# window: 2024-01-01 -> 2026-12-31; 636 rows through 2026-09-04 for PTRO).
# Keyed by (symbol, family_name) since family_name alone is no longer unique
# once the control cells below share family names with PTRO.
PUBLISHED_HISTORY_END: str = "2026-09-04"
"""
The last date in the published run's own chartbit snapshot (roadmap §7:
"chartbit's full 631-row daily history (2024-01-02 -> 2026-09-04)"). The
local data/daily store keeps accruing new sessions daily, so reproducing a
published figure requires capping AT this date, not just starting from
HISTORY_START -- see load_chartbit_daily_bars's docstring for the control-
cell mismatch this caught.
"""

PUBLISHED_C1: dict[tuple[str, str], dict[str, float]] = {
    ("PTRO", "daily_momentum"): {"net_bps": 43.5, "impact_bps": 5.5},
    ("PTRO", "volume_breakout"): {"net_bps": 11.7, "impact_bps": 5.5},
    ("TINS", "mean_reversion"): {"net_bps": 4.6, "impact_bps": 21.9},
}

# The three cells the plan-13 OOS split KILLED (roadmap §7) -- real
# in-sample-only edges. Carried through the same grid below as a self-check:
# an execution-lag or ordering change that makes one of these clear its
# hurdle OOS is a strong signal of a bug in THIS module, not a discovery.
PUBLISHED_OOS_KILLED: dict[tuple[str, str, int], float] = {
    ("BREN", "daily_momentum", 2): -87.9,
    ("RATU", "daily_momentum", 1): -53.9,
    ("WIFI", "daily_momentum", 2): -21.9,
}


@dataclass(frozen=True)
class CellSpec:
    """One (symbol, family, hold) cell to score across conventions/orderings."""

    symbol: str
    family_name: str
    signal_fn: Callable[..., pd.DataFrame]
    hold: int


# The three surviving cells (roadmap §7): PTRO daily_momentum h2, PTRO
# volume_breakout h2, TINS mean_reversion h2. TINS has no local intraday
# store but DOES have a chartbit daily store -- see DEEP_HISTORY_START.
SURVIVOR_CELLS: list[CellSpec] = [
    CellSpec("PTRO", "daily_momentum", daily_momentum_signal, 2),
    CellSpec("PTRO", "volume_breakout", volume_breakout_signal, 2),
    CellSpec("TINS", "mean_reversion", mean_reversion_signal, 2),
]

# Control cells: real in-sample-only edges (PUBLISHED_OOS_KILLED). Carried
# through the same convention/ordering grid purely as a self-check.
CONTROL_CELLS: list[CellSpec] = [
    CellSpec("BREN", "daily_momentum", daily_momentum_signal, 2),
    CellSpec("RATU", "daily_momentum", daily_momentum_signal, 1),
    CellSpec("WIFI", "daily_momentum", daily_momentum_signal, 2),
]

# PTRO/TINS get a deeper history window than the published run used (see
# module docstring): PTRO's pre-2013 years are up to 87% zero-volume days
# (illiquid/thin listing), so 2013-01-01 is where the series becomes usable;
# TINS is clean back to 2001 but is started at the same date for
# comparability rather than giving it a different-length window. A symbol
# absent from this mapping (the CONTROL_CELLS) falls back to HISTORY_START --
# the published run's own window -- so the OOS-killed self-check stays
# apples-to-apples with PUBLISHED_OOS_KILLED.
DEEP_HISTORY_START: dict[str, str] = {
    "PTRO": "2013-01-01",
    "TINS": "2013-01-01",
}

ORDERINGS: tuple[str, ...] = ("A", "B")

ORDERING_LABELS: dict[str, str] = {
    "A": "production: de-overlap raw signal, then filter long-only",
    "B": "corrected: filter long-only first, then de-overlap survivors",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STORE_DIR = os.path.join(SCRIPT_DIR, "..", "data", "intraday")
DEFAULT_CHARTBIT_STORE_DIR = os.path.join(SCRIPT_DIR, "..", "data", "daily")


# ---------------------------------------------------------------------------
# Daily-bar reconstruction
# ---------------------------------------------------------------------------

def aggregate_daily_bars(intraday_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 1-minute intraday bars into one OHLCV row per trading session.

    open = first bar's open, high = max, low = min, close = last bar's close,
    volume = sum, value = sum (chartbit's own per-bar IDR turnover — carried
    through so impact.compute_adv_notional_idr_from_daily_bars can use the
    ``value`` column rather than its close*volume fallback, exactly as the
    published daily cells did).

    No session filter is applied here: the intraday store already restricts
    to the JSX continuous session at ingestion (see module docstring).

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-minute bars with columns: datetime, date, open, high, low, close,
        volume, value (screener/intraday_store.py schema).

    Returns
    -------
    pd.DataFrame
        One row per date, sorted ascending, columns: date, open, high, low,
        close, volume, value. Empty input yields an empty (zero-row) frame
        with the same columns.
    """
    columns = ["date", "open", "high", "low", "close", "volume", "value"]
    if intraday_df is None or len(intraday_df) == 0:
        return pd.DataFrame(columns=columns)

    ordered = intraday_df.sort_values("datetime")
    daily = ordered.groupby("date", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        value=("value", "sum"),
    )
    daily = daily.reset_index().sort_values("date").reset_index(drop=True)
    return daily[columns]


def load_daily_bars(symbol: str, store_dir: str) -> pd.DataFrame:
    """
    Load and aggregate a symbol's daily bars from the intraday accrual store.

    Returns an empty (zero-row) frame — never None — when the store is
    absent, so callers can treat "no data" and "empty data" identically.
    """
    intraday_df = load_intraday(symbol, store_dir=store_dir)
    if intraday_df is None:
        logger.warning("No intraday store found for %s under %s", symbol, store_dir)
        return aggregate_daily_bars(None)
    return aggregate_daily_bars(intraday_df)


def load_chartbit_daily_bars(
    symbol: str, store_dir: str, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """
    Load a symbol's daily bars directly from a chartbit daily-bar store
    (``data/daily/<SYMBOL>.parquet``: already one row per session, unlike
    ``load_daily_bars`` there is nothing to aggregate here).

    Parameters
    ----------
    symbol : str
    store_dir : str
        Directory containing ``"<symbol>.parquet"``.
    start : str | None
        ISO date (inclusive) to truncate history at, e.g. ``"2013-01-01"``
        to skip a symbol's illiquid early years (see DEEP_HISTORY_START).
        None keeps the full stored history from its start.
    end : str | None
        ISO date (inclusive) to truncate history at. None keeps the full
        stored history through its most recent row. Needed because this
        local store keeps accruing daily -- reproducing a PUBLISHED figure
        computed on an earlier snapshot requires capping at the SAME end
        date it used (see PUBLISHED_HISTORY_END), or a thin OOS split can
        shift enough to change even the SIGN of its edge (confirmed: this
        is exactly what made this module's own reproduction of the three
        PUBLISHED_OOS_KILLED controls disagree before this parameter was
        added -- five extra accrued trading days moved BREN/WIFI's OOS
        split from a documented negative edge to a positive one).

    Returns
    -------
    pd.DataFrame
        Columns: date (pd.Timestamp), open, high, low, close, volume, value.
        Sorted ascending by date, deduped by date. Empty (zero-row) frame —
        never None — when the store file is absent.
    """
    columns = ["date", "open", "high", "low", "close", "volume", "value"]
    path = os.path.join(store_dir, f"{symbol}.parquet")
    if not os.path.exists(path):
        logger.warning("No chartbit daily store found for %s at %s", symbol, path)
        return pd.DataFrame(columns=columns)

    raw = pd.read_parquet(path, engine="pyarrow")
    df = raw[columns].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates(subset="date", keep="last").sort_values("date").reset_index(drop=True)
    if start is not None:
        df = df[df["date"] >= pd.Timestamp(start)].reset_index(drop=True)
    if end is not None:
        df = df[df["date"] <= pd.Timestamp(end)].reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Execution conventions
# ---------------------------------------------------------------------------

def conventional_forward_return(daily_df: pd.DataFrame, hold: int, convention: str) -> pd.Series:
    """
    Forward return under one of the four execution conventions (C1-C4).

    Row i is the signal day t (data available up to and including close_t).
    Only WHERE the position is transacted changes between conventions — the
    signal itself is untouched (see conventional_signal_fn).

        C1  entry=close_t         exit=close_{t+hold}
        C2  entry=open_{t+1}      exit=open_{t+hold+1}
        C3  entry=close_{t+1}     exit=close_{t+hold+1}
        C4  entry=open_{t+1}      exit=close_{t+hold}

    Implemented with pandas ``.shift(-k)`` so row i holds the value k rows
    ahead; rows that would need data past the end of daily_df are NaN.

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily OHLCV, sorted ascending by date, with ``open`` and ``close``.
    hold : int
        Hold horizon in trading days (>= 1).
    convention : str
        One of CONVENTIONS.

    Returns
    -------
    pd.Series
        forward_return aligned to daily_df's row index (float, NaN at tail).
    """
    if convention not in CONVENTIONS:
        raise ValueError(f"Unknown execution convention {convention!r}; expected one of {CONVENTIONS}")
    if hold < 1:
        raise ValueError(f"hold must be >= 1, got {hold}")

    close = daily_df["close"]
    open_ = daily_df["open"]

    if convention == "C1":
        entry, exit_ = close, close.shift(-hold)
    elif convention == "C2":
        entry, exit_ = open_.shift(-1), open_.shift(-(hold + 1))
    elif convention == "C3":
        entry, exit_ = close.shift(-1), close.shift(-(hold + 1))
    else:  # C4
        entry, exit_ = open_.shift(-1), close.shift(-hold)

    return exit_ / entry - 1.0


def conventional_signal_fn(
    signal_fn: Callable[..., pd.DataFrame], convention: str
) -> Callable[..., pd.DataFrame]:
    """
    Wrap a families_single_name signal_fn so its forward_return is computed
    under *convention* instead of the family's hardcoded C1.

    The wrapped signal_fn still calls the REAL signal_fn to get ``signal``
    (so the lookback/NaN-dropping at the head of the series is exactly the
    family's own), then replaces ``forward_return`` with the convention's
    value, joined by ``date`` (robust to whatever the head-side dropna
    already removed). Rows the new convention cannot price at the tail
    (it may need one more day of data than C1 — see conventional_forward_return)
    are dropped by re-applying dropna, mirroring signal_fn's own contract.

    Passing this wrapper to run_single_name_family reuses that function's
    long-only filter, de-overlap, MIN_EFF_N gate and gross_edge_bps
    computation unchanged — only the forward_return values differ.
    """

    def _wrapped(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
        base = signal_fn(df, hold=hold)
        if len(base) == 0:
            return base
        conv_returns = conventional_forward_return(df, hold=hold, convention=convention)
        conv_by_date = pd.Series(conv_returns.to_numpy(), index=df["date"].to_numpy())
        out = base.copy()
        out["forward_return"] = out["date"].map(conv_by_date)
        out = out.dropna(subset=["signal", "forward_return"]).reset_index(drop=True)
        return out

    return _wrapped


# ---------------------------------------------------------------------------
# Per-cell scoring
# ---------------------------------------------------------------------------

def _score_signal_df_ordering_b(signal_df: pd.DataFrame, hold: int) -> dict:
    """
    Score an already-signalled DataFrame (columns date/close/signal/
    forward_return) under ORDERING B: filter to long-only FIRST, then
    de-overlap the long-only survivors.

    families_single_name.run_single_name_family (ORDERING A, "production")
    does the opposite: it de-overlaps the RAW signal stream (both long and
    short/flat rows) and applies the long-only mask to the de-overlapped
    survivors afterward. The two are NOT equivalent, because
    harness.deoverlap_daily's "skip the next hold-1 rows" is a ROW-POSITION
    skip, not a calendar-day skip:

    - Under ORDERING A the skipped rows are still hold-1 calendar-adjacent
      trading days, because de-overlap acts on the contiguous full daily
      stream before anything is removed.
    - Under ORDERING B, the short/flat rows are already gone before
      de-overlap runs, so "the next row" in the filtered frame is not
      necessarily the next trading day — a hold=2 de-overlap on the
      survivors can end up skipping more or fewer than 2 calendar days
      depending on how many short/flat days fell in between.

    This function exists because that ordering has no existing
    implementation to reuse (run_single_name_family hardcodes ORDERING A);
    it reuses the same two primitives (long_only_mask, deoverlap_daily) in
    the opposite order rather than reimplementing either one.

    Returns
    -------
    dict
        Shaped like run_single_name_family's return value (status,
        skip_reason, eff_n, gross_edge_bps) so callers can price a hurdle
        against either ordering identically.
    """
    if len(signal_df) == 0:
        return {
            "status": "insufficient_n",
            "skip_reason": "signal_fn returned empty DataFrame",
            "eff_n": 0,
            "gross_edge_bps": None,
        }

    sig_all = signal_df["signal"].to_numpy(dtype=float)
    long_only_df = signal_df[long_only_mask(sig_all)]
    deoverlapped = deoverlap_daily(long_only_df, hold=hold)

    sig = deoverlapped["signal"].to_numpy(dtype=float)
    fwd = deoverlapped["forward_return"].to_numpy(dtype=float)
    # Already long-only by construction (filtered before de-overlap); still
    # guard for non-finite values the same way run_single_name_family does.
    valid = np.isfinite(sig) & np.isfinite(fwd)
    eff_n = int(valid.sum())
    if eff_n < MIN_EFF_N:
        return {
            "status": "insufficient_n",
            "skip_reason": f"eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}",
            "eff_n": eff_n,
            "gross_edge_bps": None,
        }

    signed_returns = fwd[valid]
    return {
        "status": "tested",
        "skip_reason": None,
        "eff_n": eff_n,
        "gross_edge_bps": float(np.mean(signed_returns) * 1e4),
    }


def score_cell(
    daily_df: pd.DataFrame,
    signal_fn: Callable[..., pd.DataFrame],
    symbol: str,
    family_name: str,
    hold: int,
    convention: str,
    min_edge_to_clear_bps: float,
    *,
    use_calibrated: bool = True,
    ordering: str = "A",
) -> dict:
    """
    Score one (cell, convention, ordering) combination using the published
    cost stack.

    ordering="A" (default, production) reuses run_single_name_family (long-
    only filter, de-overlap, MIN_EFF_N gate, gross_edge_bps) via
    conventional_signal_fn unchanged. ordering="B" calls the wrapped
    signal_fn directly and scores it with _score_signal_df_ordering_b
    instead (filter long-only BEFORE de-overlap — see that function's
    docstring for why the two are not equivalent). Both orderings then apply
    the SAME hurdle arithmetic strategy_fit.run_strategy_fit.
    _run_single_name_cell uses: hurdle = best_feasible_hurdle_bps(
    min_edge_to_clear_bps, modelled_impact_bps, hold); net = gross - hurdle.

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily OHLCV for *symbol* (see load_daily_bars / load_chartbit_daily_bars).
    signal_fn : callable
        One of families_single_name.{daily_momentum_signal,
        mean_reversion_signal,volume_breakout_signal}.
    symbol, family_name : str
        Identifiers carried into the result dict.
    hold : int
        Hold horizon in trading days.
    convention : str
        One of CONVENTIONS.
    min_edge_to_clear_bps : float
        Plan-09 structural friction floor for *symbol*.
    use_calibrated : bool
        Resolve the impact coefficient via impact_coeffs.json (plan-15),
        matching the published run's ``--use-calibrated``. Default True —
        deliberately different from run_strategy_fit.py's own default of
        False, because the published cells this module compares against
        were scored with calibrated coefficients.
    ordering : str
        One of ORDERINGS. Default "A" (production) preserves this
        function's original behaviour.

    Returns
    -------
    dict
        Keys: symbol, family_name, hold, convention, ordering, status,
        eff_n, gross_edge_bps, impact_bps, min_edge_to_clear_bps,
        hurdle_bps, net_edge_bps, verdict. The bps/verdict keys are None
        and verdict mirrors ``status`` when the MIN_EFF_N gate is not
        cleared.
    """
    if ordering not in ORDERINGS:
        raise ValueError(f"Unknown ordering {ordering!r}; expected one of {ORDERINGS}")

    wrapped = conventional_signal_fn(signal_fn, convention)
    if ordering == "A":
        result = run_single_name_family(
            df=daily_df,
            signal_fn=wrapped,
            symbol=symbol,
            min_edge_to_clear_bps=min_edge_to_clear_bps,
            family_name=family_name,
            horizon_trading_days=hold,
        )
    else:
        result = _score_signal_df_ordering_b(wrapped(daily_df, hold=hold), hold=hold)

    base = {
        "symbol": symbol,
        "family_name": family_name,
        "hold": hold,
        "convention": convention,
        "ordering": ordering,
        "eff_n": result["eff_n"],
    }

    if result["status"] != "tested":
        return {
            **base,
            "status": result["status"],
            "gross_edge_bps": None,
            "impact_bps": None,
            "min_edge_to_clear_bps": min_edge_to_clear_bps,
            "hurdle_bps": None,
            "net_edge_bps": None,
            "verdict": result["status"],
        }

    impact_bps = compute_impact_bps_from_daily_df(
        daily_df, use_calibrated=use_calibrated, symbol=symbol
    )
    hurdle_bps, _best_dow = best_feasible_hurdle_bps(
        min_edge_to_clear_bps=min_edge_to_clear_bps,
        modelled_impact_bps=impact_bps,
        hold_trading_days=hold,
    )
    gross_edge_bps = result["gross_edge_bps"]
    net_edge_bps = gross_edge_bps - hurdle_bps

    return {
        **base,
        "status": "tested",
        "gross_edge_bps": gross_edge_bps,
        "impact_bps": impact_bps,
        "min_edge_to_clear_bps": min_edge_to_clear_bps,
        "hurdle_bps": hurdle_bps,
        "net_edge_bps": net_edge_bps,
        "verdict": "PASS" if net_edge_bps > 0 else "FAIL",
    }


def min_edge_to_clear_bps_for(daily_df: pd.DataFrame) -> float:
    """
    Plan-09 structural friction floor: one-tick spread at the latest close,
    plus the round-trip fee. Same formula run_strategy_fit.py's grid runner
    uses per symbol.
    """
    last_close = float(daily_df["close"].iloc[-1])
    return min_spread_bps(last_close) + ROUND_TRIP_BPS


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_execution_lag_grid(
    daily_df: pd.DataFrame,
    symbol: str = SYMBOL,
    *,
    use_calibrated: bool = True,
) -> list[dict]:
    """
    Score every (cell, convention) combination for *symbol*.

    Returns
    -------
    list[dict]
        One row per (family_name, convention) in PTRO_CELLS x CONVENTIONS,
        each shaped as score_cell's return value.
    """
    if len(daily_df) == 0:
        raise ValueError(f"No daily bars for {symbol}; cannot score any cell")

    min_edge = min_edge_to_clear_bps_for(daily_df)
    rows: list[dict] = []
    for family_name, signal_fn in PTRO_CELLS.items():
        for convention in CONVENTIONS:
            rows.append(
                score_cell(
                    daily_df=daily_df,
                    signal_fn=signal_fn,
                    symbol=symbol,
                    family_name=family_name,
                    hold=HOLD_TRADING_DAYS,
                    convention=convention,
                    min_edge_to_clear_bps=min_edge,
                    use_calibrated=use_calibrated,
                )
            )
    return rows


def substrate_check(daily_df: pd.DataFrame, symbol: str = SYMBOL, *, use_calibrated: bool = True) -> list[dict]:
    """
    Reproduce the published C1/ordering-A gross/net figures on daily bars
    RECONSTRUCTED FROM 1-MINUTE INTRADAY BARS (see load_daily_bars) --
    i.e. the shallow, tick-substrate side of the substrate check. See
    substrate_check_chartbit for the primary (chartbit daily bars) check,
    which is expected to reproduce the published figures far more closely.

    Returns one row per cell in PTRO_CELLS, each augmented with the
    published net_bps/impact_bps (docs/plans/00-roadmap-research-to-live.md
    §7) for side-by-side comparison. Does not raise or judge closeness —
    the caller (CLI / report) decides whether the gap is small enough.
    """
    rows = []
    for family_name, signal_fn in PTRO_CELLS.items():
        row = score_cell(
            daily_df=daily_df,
            signal_fn=signal_fn,
            symbol=symbol,
            family_name=family_name,
            hold=HOLD_TRADING_DAYS,
            convention="C1",
            min_edge_to_clear_bps=min_edge_to_clear_bps_for(daily_df),
            use_calibrated=use_calibrated,
        )
        published = PUBLISHED_C1[(symbol, family_name)]
        row["published_net_bps"] = published["net_bps"]
        row["published_impact_bps"] = published["impact_bps"]
        rows.append(row)
    return rows


def substrate_check_chartbit(
    store_dir: str, cells: list[CellSpec] = SURVIVOR_CELLS, *, use_calibrated: bool = True
) -> list[dict]:
    """
    Reproduce the published C1/ordering-A gross/net figures on CHARTBIT
    DAILY BARS restricted to HISTORY_START (2024-01-01) -- the same window
    strategy_fit.run_strategy_fit's grid runner used to produce the
    published figures, NOT the deeper DEEP_HISTORY_START window the rest of
    this module's analysis uses. This is the primary substrate check (see
    module docstring); ``substrate_check`` above is the secondary,
    tick-derived one, kept for the PTRO side-by-side comparison.

    Returns
    -------
    list[dict]
        One row per cell in *cells*, each augmented with published_net_bps /
        published_impact_bps for side-by-side comparison. A cell whose
        chartbit store file is absent is reported with status="no_data"
        rather than raising, so one missing symbol does not abort the rest.
    """
    rows = []
    for cell in cells:
        daily_df = load_chartbit_daily_bars(
            cell.symbol, store_dir, start=HISTORY_START, end=PUBLISHED_HISTORY_END
        )
        if len(daily_df) == 0:
            rows.append({
                "symbol": cell.symbol,
                "family_name": cell.family_name,
                "status": "no_data",
                "eff_n": 0,
                "gross_edge_bps": None,
                "net_edge_bps": None,
                "published_net_bps": PUBLISHED_C1[(cell.symbol, cell.family_name)]["net_bps"],
                "published_impact_bps": PUBLISHED_C1[(cell.symbol, cell.family_name)]["impact_bps"],
            })
            continue
        row = score_cell(
            daily_df=daily_df,
            signal_fn=cell.signal_fn,
            symbol=cell.symbol,
            family_name=cell.family_name,
            hold=cell.hold,
            convention="C1",
            min_edge_to_clear_bps=min_edge_to_clear_bps_for(daily_df),
            use_calibrated=use_calibrated,
            ordering="A",
        )
        published = PUBLISHED_C1[(cell.symbol, cell.family_name)]
        row["published_net_bps"] = published["net_bps"]
        row["published_impact_bps"] = published["impact_bps"]
        rows.append(row)
    return rows


def validate_published_oos_killed(store_dir: str) -> list[dict]:
    """
    Self-check (team-lead's explicit request): reproduce the OOS edge for
    the three plan-13 OOS-KILLED control cells (PUBLISHED_OOS_KILLED) at
    C1/ordering A -- i.e. run walk_forward_daily exactly as production
    would, capped at PUBLISHED_HISTORY_END so the IS/OOS split lands on the
    same boundary the published run used. This is the shallow (HISTORY_START
    -> PUBLISHED_HISTORY_END) window, NOT the deep-history window the rest
    of the grid uses for these controls -- the point here is exact
    reproduction, not more data.

    An early version of this module (before PUBLISHED_HISTORY_END existed)
    disagreed on 2 of 3: BREN and WIFI both flipped from the documented
    negative OOS edge to positive once five extra accrued trading days
    (2026-09-05 -> 2026-09-11) shifted their thin (n~48-59) OOS split. That
    was not a sign/off-by-one bug in this module -- capping at
    PUBLISHED_HISTORY_END reproduces all three published OOS figures to
    within 0.1 bps. Kept here as a standing regression check.

    Returns
    -------
    list[dict]
        One row per control cell: symbol, family_name, hold, oos_edge_bps
        (mine), published_oos_edge_bps, agrees (bool, within 1.0 bps).
    """
    rows = []
    for cell in CONTROL_CELLS:
        daily_df = load_chartbit_daily_bars(
            cell.symbol, store_dir, start=HISTORY_START, end=PUBLISHED_HISTORY_END
        )
        published_oos = PUBLISHED_OOS_KILLED[(cell.symbol, cell.family_name, cell.hold)]
        if len(daily_df) == 0:
            rows.append({
                "symbol": cell.symbol, "family_name": cell.family_name, "hold": cell.hold,
                "oos_edge_bps": None, "published_oos_edge_bps": published_oos, "agrees": False,
            })
            continue
        wf = walk_forward_daily(
            df=daily_df,
            signal_fn=cell.signal_fn,
            symbol=cell.symbol,
            min_edge_to_clear_bps=min_edge_to_clear_bps_for(daily_df),
            family_name=cell.family_name,
            horizon_trading_days=cell.hold,
        )
        agrees = wf.oos_edge_bps is not None and abs(wf.oos_edge_bps - published_oos) < 1.0
        rows.append({
            "symbol": cell.symbol, "family_name": cell.family_name, "hold": cell.hold,
            "oos_edge_bps": wf.oos_edge_bps, "published_oos_edge_bps": published_oos, "agrees": agrees,
        })
    return rows


# ---------------------------------------------------------------------------
# Chronological IS/OOS split (plan-13 methodology, extended to ordering B)
# ---------------------------------------------------------------------------

def _split_is_oos(daily_df: pd.DataFrame, is_frac: float = IS_FRAC) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Chronological, row-position 60/40 split -- mirrors
    strategy_fit.daily_walk_forward.walk_forward_daily's split EXACTLY (same
    IS_FRAC, same "sort by date, then slice by row count" rule), so ordering
    B's IS/OOS numbers stay comparable to ordering A's (which calls
    walk_forward_daily directly -- see score_cell_is_oos). Reimplemented
    rather than imported because walk_forward_daily hardcodes
    run_single_name_family (ordering A) internally and cannot be handed a
    different per-slice scorer.
    """
    sorted_df = daily_df.sort_values("date", kind="stable").reset_index(drop=True)
    n_total = len(sorted_df)
    n_is = max(1, int(round(n_total * is_frac)))
    return sorted_df.iloc[:n_is].copy(), sorted_df.iloc[n_is:].copy()


def score_cell_is_oos(
    daily_df: pd.DataFrame,
    signal_fn: Callable[..., pd.DataFrame],
    symbol: str,
    family_name: str,
    hold: int,
    convention: str,
    min_edge_to_clear_bps: float,
    *,
    ordering: str = "A",
) -> dict:
    """
    IS/OOS gross edge (not cost-adjusted -- matches walk_forward_daily's own
    contract) for one (cell, convention, ordering) combination, via the SAME
    60/40 chronological, row-position split plan-13's
    strategy_fit.daily_walk_forward.walk_forward_daily uses: signal_fn is
    re-invoked independently on each raw slice, never on the full frame
    first, so no forward-return window can straddle the split boundary (see
    daily_walk_forward.py's module docstring for why that matters).

    ordering="A" delegates to walk_forward_daily directly -- full reuse, no
    new split logic -- since it already calls run_single_name_family (i.e.
    ordering A) independently on each slice. ordering="B" reimplements only
    the split-and-dispatch (walk_forward_daily cannot be handed a different
    per-slice scorer) via _split_is_oos + _score_signal_df_ordering_b.

    Returns
    -------
    dict
        Keys: symbol, family_name, hold, convention, ordering, is_edge_bps,
        oos_edge_bps, oos_eff_n, skip_reason. Gross edge only, by design --
        this function answers "did the edge persist", not "does it clear
        its hurdle" (score_cell / score_cell answers that on the full
        sample).
    """
    if ordering not in ORDERINGS:
        raise ValueError(f"Unknown ordering {ordering!r}; expected one of {ORDERINGS}")

    wrapped = conventional_signal_fn(signal_fn, convention)

    if ordering == "A":
        wf = walk_forward_daily(
            df=daily_df,
            signal_fn=wrapped,
            symbol=symbol,
            min_edge_to_clear_bps=min_edge_to_clear_bps,
            family_name=family_name,
            horizon_trading_days=hold,
        )
        return {
            "symbol": symbol,
            "family_name": family_name,
            "hold": hold,
            "convention": convention,
            "ordering": ordering,
            "is_edge_bps": wf.is_edge_bps,
            "oos_edge_bps": wf.oos_edge_bps,
            "oos_eff_n": wf.oos_eff_n,
            "skip_reason": wf.skip_reason,
        }

    is_df, oos_df = _split_is_oos(daily_df)
    is_result = (
        _score_signal_df_ordering_b(wrapped(is_df, hold=hold), hold=hold)
        if len(is_df) > 0
        else {"status": "insufficient_n", "gross_edge_bps": None, "eff_n": 0, "skip_reason": "IS split is empty"}
    )
    oos_result = (
        _score_signal_df_ordering_b(wrapped(oos_df, hold=hold), hold=hold)
        if len(oos_df) > 0
        else {"status": "insufficient_n", "gross_edge_bps": None, "eff_n": 0, "skip_reason": "OOS split is empty"}
    )
    return {
        "symbol": symbol,
        "family_name": family_name,
        "hold": hold,
        "convention": convention,
        "ordering": ordering,
        "is_edge_bps": is_result.get("gross_edge_bps"),
        "oos_edge_bps": oos_result.get("gross_edge_bps"),
        "oos_eff_n": oos_result.get("eff_n"),
        "skip_reason": None if oos_result["status"] == "tested" else oos_result.get("skip_reason"),
    }


# ---------------------------------------------------------------------------
# Deep-history grid (all 6 cells x 4 conventions x 2 orderings)
# ---------------------------------------------------------------------------

def _deep_history_bars(cell: CellSpec, store_dir: str) -> pd.DataFrame:
    """Load *cell*'s symbol at its DEEP_HISTORY_START (or HISTORY_START when
    the symbol has no override -- the CONTROL_CELLS case, kept apples-to-
    apples with PUBLISHED_OOS_KILLED)."""
    start = DEEP_HISTORY_START.get(cell.symbol, HISTORY_START)
    return load_chartbit_daily_bars(cell.symbol, store_dir, start=start)


def run_deep_history_grid(
    store_dir: str, cells: list[CellSpec] | None = None, *, use_calibrated: bool = True
) -> list[dict]:
    """
    Full-sample cell x convention x ordering grid over chartbit daily bars.

    Defaults to SURVIVOR_CELLS + CONTROL_CELLS (the three surviving cells
    plus the three plan-13 OOS-killed controls, run through the identical
    grid as a self-check -- see PUBLISHED_OOS_KILLED). Symbols with no local
    chartbit store are skipped (not raised) so one missing name does not
    abort the rest.
    """
    if cells is None:
        cells = SURVIVOR_CELLS + CONTROL_CELLS
    rows = []
    for cell in cells:
        daily_df = _deep_history_bars(cell, store_dir)
        if len(daily_df) == 0:
            continue
        min_edge = min_edge_to_clear_bps_for(daily_df)
        for convention in CONVENTIONS:
            for ordering in ORDERINGS:
                rows.append(
                    score_cell(
                        daily_df=daily_df,
                        signal_fn=cell.signal_fn,
                        symbol=cell.symbol,
                        family_name=cell.family_name,
                        hold=cell.hold,
                        convention=convention,
                        min_edge_to_clear_bps=min_edge,
                        use_calibrated=use_calibrated,
                        ordering=ordering,
                    )
                )
    return rows


def run_deep_history_is_oos_grid(
    store_dir: str, cells: list[CellSpec] | None = None, *, use_calibrated: bool = True
) -> list[dict]:
    """IS/OOS counterpart of run_deep_history_grid -- see score_cell_is_oos."""
    if cells is None:
        cells = SURVIVOR_CELLS + CONTROL_CELLS
    rows = []
    for cell in cells:
        daily_df = _deep_history_bars(cell, store_dir)
        if len(daily_df) == 0:
            continue
        min_edge = min_edge_to_clear_bps_for(daily_df)
        for convention in CONVENTIONS:
            for ordering in ORDERINGS:
                rows.append(
                    score_cell_is_oos(
                        daily_df=daily_df,
                        signal_fn=cell.signal_fn,
                        symbol=cell.symbol,
                        family_name=cell.family_name,
                        hold=cell.hold,
                        convention=convention,
                        min_edge_to_clear_bps=min_edge,
                        ordering=ordering,
                    )
                )
    return rows


def per_year_gross_edge(
    daily_df: pd.DataFrame, signal_fn: Callable[..., pd.DataFrame], hold: int, convention: str = "C1"
) -> pd.DataFrame:
    """
    Descriptive (NOT MIN_EFF_N-gated) mean long-only forward_return by
    calendar year of the entry date, ordering A (production de-overlap).

    For interpretive context only -- e.g. to see whether a name's gross
    edge is concentrated in a strong secular run-up rather than spread
    evenly across years. Never used for any PASS/FAIL verdict; a year with
    only 1-2 trades is reported exactly like any other, with its own ``n``
    so the reader can judge how much weight it deserves.

    Returns
    -------
    pd.DataFrame
        Columns: year, n, gross_edge_bps. Empty frame if there are no
        long-only observations at all.
    """
    wrapped = conventional_signal_fn(signal_fn, convention)
    signal_df = wrapped(daily_df, hold=hold)
    if len(signal_df) == 0:
        return pd.DataFrame(columns=["year", "n", "gross_edge_bps"])

    deoverlapped = deoverlap_daily(signal_df, hold=hold)
    sig = deoverlapped["signal"].to_numpy(dtype=float)
    fwd = deoverlapped["forward_return"].to_numpy(dtype=float)
    mask = np.isfinite(sig) & np.isfinite(fwd) & long_only_mask(sig)
    if not mask.any():
        return pd.DataFrame(columns=["year", "n", "gross_edge_bps"])

    trades = deoverlapped.loc[mask, ["date"]].copy()
    trades["forward_return"] = fwd[mask]
    trades["year"] = pd.to_datetime(trades["date"]).dt.year
    grouped = trades.groupby("year")["forward_return"]
    summary = pd.DataFrame({"n": grouped.count(), "gross_edge_bps": grouped.mean() * 1e4})
    return summary.reset_index()


# ---------------------------------------------------------------------------
# Text rendering (CLI)
# ---------------------------------------------------------------------------

def _fmt(value: float | None, decimals: int = 1) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def format_substrate_check(rows: list[dict]) -> str:
    """Render the substrate_check() rows as a plain-text side-by-side table."""
    lines = [
        f"{'cell':<18} {'eff_n':>6} {'gross':>8} {'impact':>8} {'net(mine)':>10} {'net(published)':>15}",
    ]
    for row in rows:
        lines.append(
            f"{row['family_name']:<18} {row['eff_n']:>6} "
            f"{_fmt(row['gross_edge_bps']):>8} {_fmt(row['impact_bps']):>8} "
            f"{_fmt(row['net_edge_bps']):>10} {_fmt(row['published_net_bps']):>15}"
        )
    return "\n".join(lines)


def format_results_table(rows: list[dict]) -> str:
    """Render score_cell rows (run_execution_lag_grid / run_deep_history_grid)
    as a markdown table. Includes an Ordering column only when any row
    carries an "ordering" other than "A" (keeps the shallow PTRO-only table
    uncluttered while the deep-history table shows both)."""
    show_ordering = any(row.get("ordering", "A") != "A" for row in rows)
    if show_ordering:
        header = "| Symbol | Cell | Convention | Ordering | Eff N | Gross (bps) | Net (bps) | Hurdle (bps) | Verdict |"
        sep = "|---|---|---|---|---|---|---|---|---|"
    else:
        header = "| Cell | Convention | Eff N | Gross (bps) | Net (bps) | Hurdle (bps) | Verdict |"
        sep = "|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        label = CONVENTION_LABELS[row["convention"]]
        if show_ordering:
            lines.append(
                f"| {row['symbol']} | {row['family_name']} h{row['hold']} | {row['convention']} ({label}) | "
                f"{row['ordering']} | {row['eff_n']} | {_fmt(row['gross_edge_bps'])} | "
                f"{_fmt(row['net_edge_bps'])} | {_fmt(row['hurdle_bps'])} | {row['verdict']} |"
            )
        else:
            lines.append(
                f"| {row['family_name']} | {row['convention']} ({label}) | {row['eff_n']} | "
                f"{_fmt(row['gross_edge_bps'])} | {_fmt(row['net_edge_bps'])} | "
                f"{_fmt(row['hurdle_bps'])} | {row['verdict']} |"
            )
    return "\n".join(lines)


def format_substrate_check_chartbit(rows: list[dict]) -> str:
    """Render substrate_check_chartbit()'s rows as a plain-text side-by-side table."""
    lines = [
        f"{'symbol':<8} {'cell':<18} {'eff_n':>6} {'gross':>8} {'impact':>8} {'net(mine)':>10} {'net(published)':>15}",
    ]
    for row in rows:
        if row.get("status") == "no_data":
            lines.append(f"{row['symbol']:<8} {row['family_name']:<18}   (no local chartbit store)")
            continue
        lines.append(
            f"{row['symbol']:<8} {row['family_name']:<18} {row['eff_n']:>6} "
            f"{_fmt(row['gross_edge_bps']):>8} {_fmt(row['impact_bps']):>8} "
            f"{_fmt(row['net_edge_bps']):>10} {_fmt(row['published_net_bps']):>15}"
        )
    return "\n".join(lines)


def format_is_oos_table(rows: list[dict]) -> str:
    """Render score_cell_is_oos rows (run_deep_history_is_oos_grid) as a markdown table."""
    header = "| Symbol | Cell | Convention | Ordering | OOS Eff N | IS Gross (bps) | OOS Gross (bps) | Note |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        label = CONVENTION_LABELS[row["convention"]]
        note = row["skip_reason"] or ""
        lines.append(
            f"| {row['symbol']} | {row['family_name']} h{row['hold']} | {row['convention']} ({label}) | "
            f"{row['ordering']} | {row['oos_eff_n'] if row['oos_eff_n'] is not None else '—'} | "
            f"{_fmt(row['is_edge_bps'])} | {_fmt(row['oos_edge_bps'])} | {note} |"
        )
    return "\n".join(lines)


def format_per_year(table: pd.DataFrame) -> str:
    """Render per_year_gross_edge()'s DataFrame as a plain-text table."""
    if len(table) == 0:
        return "(no long-only observations)"
    lines = [f"{'year':<6} {'n':>4} {'gross_edge_bps':>15}"]
    for _, row in table.iterrows():
        lines.append(f"{int(row['year']):<6} {int(row['n']):>4} {row['gross_edge_bps']:>15.1f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m strategy_fit.execution_lag",
        description=(
            "Quantify how much of the survivor cells' edge survives a "
            "realistic (post-close) execution convention, and how much "
            "depends on de-overlap/long-only ordering."
        ),
    )
    parser.add_argument(
        "--intraday-store-dir",
        default=DEFAULT_STORE_DIR,
        help="1-min intraday accrual store directory (default: research/data/intraday)",
    )
    parser.add_argument(
        "--chartbit-store-dir",
        default=DEFAULT_CHARTBIT_STORE_DIR,
        help="Chartbit daily-bar store directory (default: research/data/daily)",
    )
    parser.add_argument(
        "--no-calibrated",
        action="store_true",
        default=False,
        help="Use the assumed SQRT_IMPACT_COEFF=1.0 instead of impact_coeffs.json (default: calibrated on)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    use_calibrated = not args.no_calibrated

    # --- Shallow substrate check: PTRO reconstructed from 1-min intraday bars ---
    ptro_intraday = load_daily_bars(SYMBOL, store_dir=args.intraday_store_dir)
    if len(ptro_intraday) > 0:
        print(f"[intraday-tick substrate] Reconstructed {len(ptro_intraday)} trading days for PTRO "
              f"({ptro_intraday['date'].iloc[0]} -> {ptro_intraday['date'].iloc[-1]}).\n")
        print("=== Substrate check A: PTRO, C1, tick-aggregated vs published ===")
        print(format_substrate_check(substrate_check(ptro_intraday, use_calibrated=use_calibrated)))
        print()
    else:
        print(f"No local intraday data for PTRO under {args.intraday_store_dir}; skipping tick substrate check.\n")

    # --- Primary substrate check: chartbit daily bars, HISTORY_START window (matches published run) ---
    print("=== Substrate check B: chartbit daily bars (published window) vs published ===")
    print(format_substrate_check_chartbit(
        substrate_check_chartbit(args.chartbit_store_dir, use_calibrated=use_calibrated)
    ))
    print()

    # --- OOS-killed controls self-check (exact reproduction, capped at PUBLISHED_HISTORY_END) ---
    print("=== Self-check: OOS-killed controls, capped at PUBLISHED_HISTORY_END ===")
    for row in validate_published_oos_killed(args.chartbit_store_dir):
        status = "OK" if row["agrees"] else "MISMATCH"
        print(
            f"  {row['symbol']} {row['family_name']} h{row['hold']}: "
            f"mine={_fmt(row['oos_edge_bps'])} published={_fmt(row['published_oos_edge_bps'])} [{status}]"
        )
    print()

    # --- Deep-history grid: all 6 cells x 4 conventions x 2 orderings, full sample ---
    print("=== Deep-history grid: cell x convention x ordering (full sample) ===")
    print(format_results_table(run_deep_history_grid(args.chartbit_store_dir, use_calibrated=use_calibrated)))
    print()

    # --- IS/OOS split: same grid, gross edge only ---
    print("=== Deep-history grid: cell x convention x ordering (IS/OOS, gross edge) ===")
    is_oos_rows = run_deep_history_is_oos_grid(args.chartbit_store_dir, use_calibrated=use_calibrated)
    print(format_is_oos_table(is_oos_rows))
    print()

    # --- Headline number ---
    headline = [
        r for r in is_oos_rows
        if r["symbol"] == "PTRO" and r["family_name"] == "daily_momentum"
        and r["convention"] == "C2" and r["ordering"] == "B"
    ]
    if headline:
        h = headline[0]
        print(
            "=== Headline: PTRO daily_momentum h2, OOS gross edge, C2 (next-open entry), ordering B ===\n"
            f"OOS gross edge: {_fmt(h['oos_edge_bps'])} bps (OOS eff_n={h['oos_eff_n']}); "
            f"{h['skip_reason'] or 'tested'}\n"
        )

    # --- Per-year context: PTRO daily_momentum, full deep-history window, C1/ordering A ---
    ptro_cell = next(c for c in SURVIVOR_CELLS if c.symbol == "PTRO" and c.family_name == "daily_momentum")
    ptro_deep = _deep_history_bars(ptro_cell, args.chartbit_store_dir)
    if len(ptro_deep) > 0:
        print(f"=== Per-year gross edge (descriptive, C1/ordering A): PTRO daily_momentum h2, "
              f"{ptro_deep['date'].iloc[0].date()} -> {ptro_deep['date'].iloc[-1].date()} ===")
        print(format_per_year(per_year_gross_edge(ptro_deep, ptro_cell.signal_fn, hold=ptro_cell.hold)))


if __name__ == "__main__":
    main()
