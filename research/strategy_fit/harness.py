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

import numpy as np
import pandas as pd

from strategy_fit.capital_model import ACTIVE_CAPITAL_MODEL, CapitalModel

# ---------------------------------------------------------------------------
# Shape constants
# ---------------------------------------------------------------------------

SHAPE_SINGLE_NAME: str = "single_name_time_series"
SHAPE_EVENT_DRIVEN: str = "event_driven"

# Deferred shapes — defined as constants so coverage tables can reference them.
SHAPE_CROSS_SECTIONAL: str = "cross_sectional"   # plan-10 §7.5, now testable
SHAPE_PAIRS: str = "pairs"                        # plan-10 §7.6, now testable
SHAPE_INTRADAY_SAME_DAY: str = "intraday_same_day"  # same-day / hold=0 families

# ---------------------------------------------------------------------------
# Capital-ceiling constant (ADR-0011, superseded by ADR-0015)
# ---------------------------------------------------------------------------

MAX_HOLD_TRADING_DAYS: int | None = ACTIVE_CAPITAL_MODEL.max_hold_trading_days
"""
Capital horizon ceiling, derived from ``ACTIVE_CAPITAL_MODEL``.

.. deprecated:: ADR-0015 (2026-09-13)
    This module-level constant is kept for backward compatibility only — it
    snapshots ``ACTIVE_CAPITAL_MODEL.max_hold_trading_days`` at import time
    and will NOT reflect a capital model passed explicitly to
    :func:`reject_horizon_exceeded`.  Read
    ``strategy_fit.capital_model.ACTIVE_CAPITAL_MODEL`` (or pass an explicit
    ``capital_model``) instead of importing this name in new code.

Under ADR-0011 (leveraged sleeve), this was a hard-wired ``2``: the leverage
facility caps practical holds at ≤2 trading days (T+2 sell-safe / T+4 forced
liquidation).  Under ADR-0015, leverage is optional and the default capital
model (``OWNED_CASH``) carries no ceiling, so this constant is ``None`` unless
``ACTIVE_CAPITAL_MODEL`` is switched back to ``LEVERAGED_SLEEVE``.
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

def reject_horizon_exceeded(
    family: StrategyFamily, *, capital_model: CapitalModel | None = None
) -> dict[str, Any] | None:
    """
    Return a rejection dict if *family* exceeds the capital horizon ceiling.

    Returns None if the family is within the deployable band (hold ≤
    ``capital_model.max_hold_trading_days``), or unconditionally when the
    active capital model has no ceiling (``max_hold_trading_days is None`` —
    e.g. ``OWNED_CASH``).  Returns a status dict with
    ``status="skipped_horizon"`` and a descriptive ``skip_reason`` when the
    ceiling is exceeded.

    This check is applied before any backtest computation; it is not possible
    to override it by injecting a signal — capital is a hard constraint
    whenever the active model has one.

    Parameters
    ----------
    family : StrategyFamily
        The strategy family to check.
    capital_model : CapitalModel or None
        The capital model whose horizon ceiling applies.  Defaults to
        ``strategy_fit.capital_model.ACTIVE_CAPITAL_MODEL`` (ADR-0015:
        ``OWNED_CASH``, no ceiling) when not supplied.

    Returns
    -------
    dict or None
        None if not rejected; otherwise a rejection dict suitable for
        building a CellResult record.
    """
    model = capital_model if capital_model is not None else ACTIVE_CAPITAL_MODEL
    max_hold = model.max_hold_trading_days
    if max_hold is None or family.horizon_trading_days <= max_hold:
        return None
    return {
        "status": "skipped_horizon",
        "skip_reason": (
            f"capital_blocked: hold {family.horizon_trading_days} trading days "
            f"> max_hold={max_hold} (capital model: {model.name}, ADR-0015)"
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


def deoverlap_sequential(
    df: pd.DataFrame,
    hold: int,
    eligible: "pd.Series | np.ndarray | None" = None,
    pos_col: str | None = None,
) -> pd.DataFrame:
    """
    Live-feasible sequential non-overlapping trade selection (harness D2 fix).

    Scan rows in order. When flat and an *eligible* row s (the long-only signal
    fired) is reached, take it: enter at session s+1, exit at session s+1+hold,
    and resume scanning at the exit day, i.e. the next eligible row is the first
    with position >= pos(s) + 1 + hold. Eligibility is applied BEFORE the skip,
    so a signal on any day is tradeable if the bot is flat (unlike
    ``deoverlap_daily``, which strides over all rows from the window start).

    Parameters
    ----------
    df : DataFrame sorted ascending by time (not mutated).
    hold : hold in trading days.
    eligible : boolean mask aligned to df rows (positional); None = all rows.
    pos_col : column holding each row's trading-day position. Needed when rows
        are sparse (event frames); default is row order (contiguous daily rows).

    Returns a new DataFrame of the taken rows (original index preserved).
    """
    n = len(df)
    ok = np.ones(n, dtype=bool) if eligible is None else np.asarray(eligible, dtype=bool)
    if len(ok) != n:
        raise ValueError("eligible must have one entry per row")
    pos = np.arange(n) if pos_col is None else df[pos_col].to_numpy(dtype=float)
    kept: list[int] = []
    next_ok = -np.inf
    for i in range(n):
        if ok[i] and pos[i] >= next_ok:
            kept.append(i)
            next_ok = pos[i] + 1 + hold
    return df.iloc[kept].copy()


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
        Terminal state of this cell. The full vocabulary the grid actually
        emits, with the module that owns each value:

        "tested"                 the cell was measured; the gates in
                                 report.py then decide PASS vs. not.
        "skipped_horizon"        hold outside the tested band
                                 (reject_horizon_exceeded, below).
        "data_blocked"           the history this family needs does not exist
                                 yet (catalog_meta.FamilyMeta.catalog_status).
        "insufficient_n"         below MIN_EFF_N after de-overlap.
        "cost_gated"             net edge did not clear the full hurdle.
        "decayed"                walk-forward rolling Sharpe below
                                 walk_forward.DECAY_THRESHOLD_SHARPE.
        "long_short_infeasible"  structurally long/short, and IDX retail
                                 accounts cannot short — see
                                 long_only.STATUS_LONG_SHORT_INFEASIBLE.
                                 Emitted by cross_sectional and pairs cells.
        "cointegration_not_found" no cointegrated pair among the candidates
                                 (families_pairs.py, Engle-Granger
                                 p > COINT_PVALUE_THRESHOLD).

        The last two were emitted by the grid long before they were written
        down here; anything consuming this field must treat an unrecognised
        value as a visible unknown rather than dropping the cell.
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
    # --- Intraday-only additions (plan-10 §9) ---
    modelled_impact_bps: float | None = None
    """
    Per-symbol round-trip market impact in bps (intraday cells only).
    Computed by strategy_fit.impact.compute_impact_bps_from_df.
    Replaces the global MODELLED_IMPACT_BPS=0 constant for intraday shapes.
    Daily / event / cross-sectional / pairs cells leave this as None (impact=0
    for those shapes until plan-13 extends the model).
    """
    oos_edge_bps: float | None = None
    """OOS gross edge from the intraday IS/OOS split (intraday cells only)."""
    oos_hit_rate: float | None = None
    """OOS hit rate from the intraday IS/OOS split (intraday cells only)."""
    oos_eff_n: int | None = None
    """OOS effective-N from the intraday IS/OOS split (intraday cells only)."""
    oos_decay_ratio: float | None = None
    """Ratio oos_edge / is_edge from the intraday IS/OOS split (intraday cells only)."""
    oos_persistent: bool | None = None
    """
    Whether the intraday cell shows OOS persistence.
    True → OOS edge > 0 AND OOS eff_n >= MIN_OOS_EFF_N.
    False → non-persistent or too thin to judge.
    None → not applicable (non-intraday shape).
    Survivors with oos_persistent=False are NOT removed — data is thin —
    but they are annotated clearly in the report for human review.
    """
    oos_sigma_bps: float | None = None
    """
    Measured OOS per-trade return dispersion in bps — the sample std of the
    SAME de-overlapped signed-returns series that produces oos_eff_n and
    oos_edge_bps (families_intraday.py's per_trade_sigma_bps, threaded
    through strategy_fit/intraday_walk_forward.py).  Never a proxy (e.g. NOT
    unconditional daily close-to-close vol) and never a hardcoded constant.
    None when unavailable (non-intraday shape, or fewer than 2 OOS trades) —
    oos_required_eff_n must not be computed with a fallback in that case.
    """
    oos_required_eff_n: int | None = None
    """
    Power-derived minimum OOS effective-N needed to resolve this cell's
    observed OOS edge at z=2 sigma, given oos_sigma_bps (see
    lib.stats.required_effective_n and intraday_walk_forward.py).
    None when not computable (non-intraday shape, OOS edge non-positive, or
    oos_sigma_bps unavailable).  This is NOT the hard not-zero floor
    (MIN_OOS_EFF_N) — a cell can clear the hard floor and still fall short of
    this number; see oos_underpowered.
    """
    oos_underpowered: bool | None = None
    """
    True iff this intraday cell clears the OOS hard floor (MIN_OOS_EFF_N) but
    its oos_eff_n falls short of oos_required_eff_n — an honest, expected
    "not enough data yet" verdict (plan-10 W2.1), not a pipeline failure.
    False when the cell is not underpowered, when it fails the hard floor for
    an unrelated reason (e.g. a non-positive OOS edge), or when oos_sigma_bps
    was unavailable (power could not be assessed at all — a distinct, rarer
    condition from "assessed and underpowered").
    None → not applicable (non-intraday shape).
    """
    oos_split_skip_reason: str | None = None
    """
    The intraday walk-forward's own diagnostic for why the IS/OOS split
    couldn't validate persistence (IntradayWalkForwardResult.skip_reason),
    e.g. "thin OOS", "invalid split: neither IS nor OOS is usable at this
    split frequency", or "OOS edge non-positive".  Distinct from the top-level
    CellResult.skip_reason (populated only when status != "tested") — a cell
    can be status="tested" (its full-window test succeeded) while its
    walk-forward split independently found a problem; that must be reported
    explicitly rather than silently discarded.  None when is_persistent=True
    or not applicable (non-intraday shape).
    """
    # --- Impact-pricing provenance (plan-10 W2.1) ---
    impact_priced: bool = False
    """
    Whether modelled_impact_bps reflects an actual measured/modelled impact
    computation (True) versus impact never being priced for this cell's shape
    (False, default).  Only intraday_same_day cells price impact today (see
    strategy_fit/impact.py); single_name_time_series / event_driven /
    cross_sectional / pairs cells leave this False until plan-13 extends the
    impact model to those shapes.  Distinguishes "impact priced at a genuine
    0.0" from "impact never priced at all" — a hard prerequisite for a cell
    to contribute to a PASS verdict (see strategy_fit/report.py:compute_verdict).
    """
    impact_priced_position_notional_idr: float | None = None
    """
    The per-name order notional (IDR) ``modelled_impact_bps`` was ACTUALLY
    priced at — the value of ``strategy_fit.impact.POSITION_NOTIONAL_IDR`` at
    the moment this cell was computed.  ``None`` when ``impact_priced`` is
    False (nothing was priced) or for a cell computed before this field
    existed.

    ADR-0017 (the sleeve is a measured 15M IDR sub-account, not a 300M
    facility / ~10 names) is the reason this field exists.  Before it,
    ``strategy_fit/impact_sweep.py`` rescaled a cell's impact by reading
    ``strategy_fit.impact.POSITION_NOTIONAL_IDR`` — the current MODULE
    CONSTANT — and treating it as "the size this cell was priced at".  That
    is only true when the constant hasn't changed since the cell was priced;
    it is an implicit coupling that happened to hold before sizing became
    configurable and breaks silently the moment it doesn't.  Recording the
    priced-at notional ON THE CELL (alongside ``impact_priced_coeff``) lets
    a rescale be computed relative to what actually happened, not what the
    module global happens to say right now — see
    ``strategy_fit.impact_sweep.recompute_cell_at_impact``.
    """
    impact_priced_coeff: float | None = None
    """
    The Almgren coefficient ``modelled_impact_bps`` was actually priced with
    — either the flat ``strategy_fit.impact.SQRT_IMPACT_COEFF`` or a
    per-name/bucket value resolved by ``strategy_fit.impact.get_impact_coeff``
    when the run used ``use_calibrated=True``.  ``None`` under the same
    conditions as ``impact_priced_position_notional_idr`` above, and for the
    same reason: a calibrated cell's coefficient can vary by symbol, so a
    rescale that assumes a single global coefficient is wrong for exactly the
    cells calibration was meant to help.
    """
    impact_basis: str | None = None
    """
    How the impact inputs were estimated: ``trailing_point_in_time`` (each trade
    priced from 60-bar ADV/vol windows ending at its signal day;
    ``modelled_impact_bps`` is then the MEAN per-trade impact, which is exactly
    what the cost gate charges because the hurdle is linear in impact) or
    ``legacy_full_history`` (one ADV/vol over the whole history; reproduces
    reports from before 2026-10-14).  None on cells that never priced impact.
    """
    impact_median_bps: float | None = None
    """Median per-trade impact (bps) under ``trailing_point_in_time``; None otherwise."""
    impact_excluded_n: int | None = None
    """Trades dropped because fewer than MIN_IMPACT_HISTORY_BARS bars preceded the signal day."""
    # --- Always-long null (beta-vs-alpha) gate ---
    null_edge_bps: float | None = None
    """
    Mean per-trade edge (bps) of the SAME population — same de-overlap, same
    hold, same long-only constraint — with the signal deleted: i.e. what
    buying every de-overlapped slot unconditionally (signal ignored) would
    have earned.  This is the "always-long null": a strategy that leans on
    asset drift rather than genuine signal will show a null_edge_bps close to
    (or above) its own gross_edge_bps.  Computed by the family runner right
    alongside gross_edge_bps, so it is definitionally the same population.
    None for shapes with no long-only null to compare against
    (cross_sectional / pairs — structurally long/short, see
    strategy_fit/long_only.py) or for any non-"tested" cell.
    """
    edge_over_null_bps: float | None = None
    """
    gross_edge_bps - null_edge_bps: the edge attributable to the SIGNAL
    itself, net of whatever the underlying asset's drift alone would have
    contributed.  A cell can report a strongly positive gross_edge_bps (and
    even a positive net_edge_bps) while edge_over_null_bps is small or
    negative — the return is beta (asset drift), not alpha (signal), and a
    per-trade strategy paying per-trade friction N times has no business
    case for beta that a single buy-and-hold wouldn't capture more cheaply.
    This is a hard PASS prerequisite (see
    strategy_fit/report.py:NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE and
    _is_pass_eligible).  None wherever null_edge_bps is None.
    """
