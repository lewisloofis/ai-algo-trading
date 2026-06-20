"""
strategy_fit/harness.py — Pluggable shape interface and dispatch for strategy-fit testing.

Defines the StrategyFamily contract, capital-ceiling enforcement, and the
effective-N counting logic for each test shape.  The harness dispatches to the
correct test primitive based on shape (see plan-10 §2, §6).

Shapes implemented (deployable band):
    single_name_time_series — per-symbol de-overlapped daily-bar observations
    event_driven            — pooled non-overlapping events across all names

Deferred (see plan-10 §7.5–7.6, 7.9):
    cross_sectional         — daily cross-sectional rank / IC
    pairs                   — cointegration / spread-reversion
    order_book              — OBI/OFI re-test on low-friction names
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Shape constants
# ---------------------------------------------------------------------------

SHAPE_SINGLE_NAME: str = "single_name_time_series"
SHAPE_EVENT_DRIVEN: str = "event_driven"

# Deferred shapes — defined as constants so coverage tables can reference them.
SHAPE_CROSS_SECTIONAL: str = "cross_sectional"   # plan-10 §7.5, now testable
SHAPE_PAIRS: str = "pairs"                        # plan-10 §7.6, now testable

# ---------------------------------------------------------------------------
# Capital-ceiling constant (ADR-0011)
# ---------------------------------------------------------------------------

MAX_HOLD_TRADING_DAYS: int = 2
"""
Capital horizon ceiling from ADR-0011: T+2 sell-safe / T+4 forced liquidation.

The leverage facility caps practical holds at ≤2 trading days.  Any strategy
family with horizon_trading_days > MAX_HOLD_TRADING_DAYS is capital-blocked
(not merely data-blocked) and must be rejected by the harness.
"""

# ---------------------------------------------------------------------------
# StrategyFamily contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyFamily:
    """
    Descriptor for a single strategy family in the test grid.

    Attributes
    ----------
    name : str
        Human-readable identifier (e.g. "daily_momentum", "mean_reversion").
    shape : str
        Test shape; one of SHAPE_SINGLE_NAME or SHAPE_EVENT_DRIVEN.
    horizon_trading_days : int
        Intended hold duration in trading days.  0 = intraday / same-day.
    status : str
        Catalogue status: "testable_now" | "data_blocked" | "cost_gated" | "deferred".
        Default "testable_now".
    """

    name: str
    shape: str
    horizon_trading_days: int
    status: str = "testable_now"


# ---------------------------------------------------------------------------
# Horizon ceiling enforcement
# ---------------------------------------------------------------------------

def reject_horizon_exceeded(family: StrategyFamily) -> dict[str, Any] | None:
    """
    Return a rejection dict if *family* exceeds the capital horizon ceiling.

    Returns None if the family is within the deployable band (hold ≤
    MAX_HOLD_TRADING_DAYS).  Returns a status dict with ``status="skipped_horizon"``
    and a descriptive ``skip_reason`` when the ceiling is exceeded.

    This check is applied before any backtest computation; it is not possible
    to override it by injecting a signal — capital is a hard constraint.

    Parameters
    ----------
    family : StrategyFamily
        The strategy family to check.

    Returns
    -------
    dict or None
        None if not rejected; otherwise a rejection dict suitable for
        building a CellResult record.
    """
    if family.horizon_trading_days <= MAX_HOLD_TRADING_DAYS:
        return None
    return {
        "status": "skipped_horizon",
        "skip_reason": (
            f"capital_blocked: hold {family.horizon_trading_days} trading days "
            f"> MAX_HOLD_TRADING_DAYS={MAX_HOLD_TRADING_DAYS} (ADR-0011)"
        ),
    }


# ---------------------------------------------------------------------------
# De-overlap helpers
# ---------------------------------------------------------------------------

def deoverlap_daily(df: pd.DataFrame, hold: int) -> pd.DataFrame:
    """
    Return a new DataFrame keeping only non-overlapping rows at daily granularity.

    Two observations overlap if the second starts within *hold* rows of the
    first (i.e. their forward windows share data).  The greedy forward walk
    keeps the first available observation and skips the next *hold-1* rows.

    For hold=1, every row is independent (no overlap) and all rows are kept.

    Parameters
    ----------
    df : pd.DataFrame
        Input DataFrame (not mutated).  Must be sorted by date ascending.
    hold : int
        Hold duration in trading days (the forward window length).

    Returns
    -------
    pd.DataFrame
        New DataFrame with non-overlapping rows only; original index preserved.
    """
    if hold <= 1:
        return df.copy()

    idx = df.index.tolist()
    kept: list[int] = []
    i = 0
    while i < len(idx):
        kept.append(idx[i])
        i += hold  # skip the next hold-1 rows (they overlap with kept row)

    return df.loc[kept].copy()


# ---------------------------------------------------------------------------
# Effective-N by shape
# ---------------------------------------------------------------------------

def effective_n_event_pooled(events_df: pd.DataFrame, hold: int) -> int:
    """
    Count pooled non-overlapping events across all universe names.

    Each symbol's events are de-overlapped independently (per-symbol greedy
    walk), then the de-overlapped counts are summed across symbols to form
    the pooled effective-N.  This preserves the plan-10 §2 rule: for
    event_driven families the natural observation unit is a pooled event
    across all names, not 30-per-name.

    Parameters
    ----------
    events_df : pd.DataFrame
        Combined event DataFrame with a ``symbol`` column.
    hold : int
        Hold duration in trading days (forward window length for de-overlap).

    Returns
    -------
    int
        Total pooled de-overlapped event count across all symbols.

    Raises
    ------
    ValueError
        If ``symbol`` column is not present.
    """
    if "symbol" not in events_df.columns:
        raise ValueError(
            "effective_n_event_pooled requires a 'symbol' column in events_df; "
            f"got columns: {list(events_df.columns)}"
        )

    total = 0
    for _symbol, group in events_df.groupby("symbol", sort=False):
        group_sorted = group.sort_values("date").reset_index(drop=True)
        deoverlapped = deoverlap_daily(group_sorted, hold=hold)
        total += len(deoverlapped)
    return total


def effective_n_single_name_per_symbol(df: pd.DataFrame, hold: int) -> int:
    """
    Count de-overlapped observations for a single-name time-series family.

    The effective-N for single_name_time_series is the number of non-overlapping
    daily-bar rows after the greedy forward walk.  Computed per-symbol;
    this function operates on a single symbol's DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Single-symbol daily-bar DataFrame (sorted ascending by date).
    hold : int
        Hold duration in trading days.

    Returns
    -------
    int
        Number of de-overlapped observations.
    """
    deoverlapped = deoverlap_daily(df, hold=hold)
    return len(deoverlapped)


# ---------------------------------------------------------------------------
# CellResult dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellResult:
    """
    Per-(symbol, family) cell result from the strategy-fit harness.

    Attributes
    ----------
    symbol : str
        Universe name (e.g. "ITMG").
    family_name : str
        Strategy family identifier.
    shape : str
        Test shape (SHAPE_SINGLE_NAME or SHAPE_EVENT_DRIVEN).
    horizon_trading_days : int
        Intended hold in trading days.
    status : str
        "tested" | "skipped_horizon" | "data_blocked" | "insufficient_n" |
        "cost_gated" | "decayed".
    skip_reason : str or None
        Populated when status is not "tested".
    hit_rate : float or None
        Observed de-overlapped hit rate on the OOS set.
    eff_n : int or None
        Effective (de-overlapped) observation count.
    gross_edge_bps : float or None
        Mean forward return × 1e4 (before fees and friction).
    net_edge_bps : float or None
        True net-after-full-cost edge per trade in bps: gross_edge_bps minus the
        full cost hurdle (floor + modelled_impact + financing).  Positive for
        survivors (passes_cost_gate=True), non-positive otherwise.  Computed by
        the runner, not the family, to count the friction floor exactly once.
    p_value : float or None
        Bootstrap p-value (fraction of null draws with hit_rate ≥ observed).
    decay_status : str or None
        "persistent" | "decayed" | None (when not tested).
    decay_metric : float or None
        Mean rolling Sharpe across walk-forward windows.
    passes_cost_gate : bool or None
        Result of net_edge_bps > min_hurdle_bps at the best feasible entry day.
    min_edge_hurdle_bps : float or None
        Full cost hurdle at the best-feasible entry day (cheapest financing).
    best_entry_dow : int or None
        Day-of-week (0=Mon … 4=Fri) with the cheapest financing cost for this
        strategy's hold horizon.  Chosen by best_feasible_hurdle_bps.
    fdr_accepted : bool | None
        Whether this cell survived Benjamini-Hochberg FDR correction.
    """

    symbol: str
    family_name: str
    shape: str
    horizon_trading_days: int
    status: str
    skip_reason: str | None = None
    hit_rate: float | None = None
    eff_n: int | None = None
    gross_edge_bps: float | None = None
    net_edge_bps: float | None = None
    p_value: float | None = None
    decay_status: str | None = None
    decay_metric: float | None = None
    passes_cost_gate: bool | None = None
    min_edge_hurdle_bps: float | None = None
    best_entry_dow: int | None = None
    fdr_accepted: bool | None = None
