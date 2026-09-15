"""
strategy_fit/impact.py — Square-root market-impact (slippage) model for intraday
and daily single-name cells.

Implements a round-trip Almgren-style square-root impact law:

    impact_bps_roundtrip = 2 * SQRT_IMPACT_COEFF * daily_vol_bps * sqrt(participation_rate)

where:
    participation_rate = position_notional_idr / adv_notional_idr  (clamped to [0, 1])

Scope (plan-13): intraday same-day cells (hold=0) AND daily single_name_time_series
cells are now priced.  Event / cross-sectional / pairs cells still retain impact=0
for now — a future plan will extend impact to those shapes.  The core formula
below is identical for both scopes; only how ``daily_vol_bps`` and
``adv_notional_idr`` are *estimated* differs (see
``compute_daily_vol_bps_from_daily_bars`` / ``compute_adv_notional_idr_from_daily_bars``
below) — a daily-signal basket is still entered atomically in one day and exited
atomically in one day (ADR-0011), so it crosses the book exactly twice, same as
an intraday round trip. Holding the position between those two days is not an
execution and is not repriced.

Provenance (plan-10 W2.1): CellResult.impact_priced records whether *this*
module actually ran for a given cell.  strategy_fit/run_strategy_fit.py's
_run_intraday_cell sets impact_priced=True, and _run_single_name_cell will also
set it once wired for daily cells (plan-13) — every other shape leaves it False
(the default), and strategy_fit/report.py treats impact_priced=True as a hard
prerequisite for PASS.  Do not infer "impact priced" from modelled_impact_bps
being non-None/non-zero alone; a genuinely-priced 0.0 (e.g. near-zero
participation) is a valid outcome of this module and must read differently
from "never priced at all".

Round-trip justification
------------------------
The ``2 *`` factor accounts for crossing the book at BOTH entry and exit.  The
one-tick spread is already captured inside ``min_edge_to_clear_bps`` (plan-09
structural friction floor), so this model estimates the *additional* book-walking
cost beyond the first tick — i.e., the cost of trading the rest of the position
through the order book.  There is no double-counting of the first tick.

Constants
---------
SQRT_IMPACT_COEFF : float = 1.0
    Almgren temporary-impact coefficient.  The empirical literature places this
    at 0.5–1.0; 1.0 is the conservative (high-friction) end, appropriate for
    sizing research before live calibration.  plan-13 will refine with live fills.

POSITION_NOTIONAL_IDR : float
    Per-name order notional in IDR, DERIVED from
    ``strategy_fit.sleeve_sizing.ACTIVE_SLEEVE_SIZING.per_name_notional_idr``
    — see ``strategy_fit/sleeve_sizing.py``.

    .. important::
        **CHANGED 2026-09-14 (ADR-0017).**  This used to be a literal
        ``30_000_000.0``, derived as "300M IDR AI sleeve (ADR-0011) / ~10
        concurrent names".  ADR-0011 was already superseded by ADR-0015
        before that constant was ever corrected, and neither ADR had
        measured the sleeve's actual capital.  ADR-0017 measured it: the
        sleeve is a separate broker sub-account holding **15,000,000 IDR**
        — HALF the old per-name constant, for the entire account, not one
        name.  ``30_000_000`` is void: every impact/capacity figure priced
        against it was priced at a size the account cannot take.  See
        ``strategy_fit/sleeve_sizing.py`` for the presets and the "open
        question" (concurrent-name count) this value still depends on, and
        ``strategy_fit/impact_sweep.py`` for the sweep across sleeve sizes
        that ADR-0017 requires in place of trusting one assumed number.
"""

from __future__ import annotations

import json
import logging
import os

import numpy as np
import pandas as pd

from strategy_fit.sleeve_sizing import ACTIVE_SLEEVE_SIZING

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

SQRT_IMPACT_COEFF: float = 1.0
"""
Almgren temporary-impact coefficient (conservative end: 1.0).

Almgren et al. (2005) estimate the coefficient at roughly 0.5–1.0.  Using 1.0
is the most conservative (largest impact estimate) for pre-live sizing research.
plan-13 will calibrate against live fills once the strategy is deployed.
"""

POSITION_NOTIONAL_IDR: float = ACTIVE_SLEEVE_SIZING.per_name_notional_idr
"""
Per-name order notional in IDR, derived from the sleeve's MEASURED equity
(ADR-0017), not a facility it doesn't use.

CHANGED 2026-09-14 (ADR-0017): previously a literal ``30_000_000.0``, derived
as "300M IDR AI sleeve (ADR-0011, already superseded by ADR-0015) / ~10
concurrent names" — a constant fitted to a leverage facility the sleeve was
never funded by.  ADR-0017 measured the sleeve directly: a separate broker
sub-account holding 15,000,000 IDR as of 2026-09-14, **half** the old
constant for the *entire* account.  ``POSITION_NOTIONAL_IDR`` is now derived
from ``strategy_fit.sleeve_sizing.ACTIVE_SLEEVE_SIZING`` — see that module
for the presets (by concurrent-name count), which value is active, and why
the concurrent-name count is an open portfolio-construction question rather
than a settled input.  The name is kept stable so existing callers (this
module's own ``compute_impact_bps*`` default arguments, ``impact_sweep.py``)
don't have to change, only what it derives from.
"""

# Fallback / penalty value returned when impact cannot be computed (degenerate
# inputs such as zero ADV, single day, or all-NaN series).  Chosen to be large
# enough to fail any reasonable cost gate, surfacing the data-quality issue.
_DEGENERATE_IMPACT_BPS: float = 10_000.0

_DEGENERATE_VOL_BPS: float = 10_000.0


# ---------------------------------------------------------------------------
# Calibrated coefficients (plan 15)
# ---------------------------------------------------------------------------

DAILY_COEFF_IS_BORROWED_NOTE: str = (
    "impact_coeffs.json's per_name coefficients (BREN, BRPT, ENRG, PTRO, RAJA, "
    "RATU, TPIA) were calibrated by plan 15 from TICK fills on INTRADAY "
    "executions only. Applying them to daily single_name_time_series cells "
    "(plan 13) assumes execution impact for a daily-signal basket is the same "
    "as for an intraday one — that is an ASSUMPTION, not a measurement; no "
    "daily-cell fills have been used to calibrate anything here. A symbol "
    "absent from per_name (e.g. a name outside the plan-15 tick-fill sample) "
    "now resolves its LIQUIDITY BUCKET from its own ADV (plan 18 §4.3) and "
    "uses that bucket's measured coefficient; it falls through to "
    "global_default = 1.0 — the conservative high-friction end of the Almgren "
    "range — only when that bucket is itself unconfirmed. See "
    "impact_coeffs.json."
)
"""Honest caveat: daily-cell impact borrows intraday-calibrated coefficients (plan 13)."""

HIGH_LIQ_ADV_IDR: float = 100e9
"""ADV above which a name is ``high_liq`` (IDR)."""

MID_LIQ_ADV_IDR: float = 20e9
"""ADV at or above which a name is ``mid_liq`` (IDR); below it is ``low_liq``."""


def liquidity_bucket(adv_notional_idr: float) -> str:
    """Map an ADV in IDR to a liquidity bucket label."""
    if adv_notional_idr > HIGH_LIQ_ADV_IDR:
        return "high_liq"
    if adv_notional_idr >= MID_LIQ_ADV_IDR:
        return "mid_liq"
    return "low_liq"


_COEFFS_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "impact_coeffs.json")

_CALIBRATED_COEFFS: dict | None = None
"""Lazily loaded contents of ``impact_coeffs.json``; ``None`` once load is attempted and the file is absent or malformed."""

_COEFFS_LOADED: bool = False


def _load_calibrated_coeffs() -> dict | None:
    """Load ``impact_coeffs.json`` once; return ``None`` if absent or malformed."""
    global _CALIBRATED_COEFFS, _COEFFS_LOADED
    if _COEFFS_LOADED:
        return _CALIBRATED_COEFFS
    _COEFFS_LOADED = True
    try:
        with open(_COEFFS_PATH) as f:
            _CALIBRATED_COEFFS = json.load(f)
    except FileNotFoundError:
        logger.debug("impact_coeffs.json absent at %s; using global default", _COEFFS_PATH)
        _CALIBRATED_COEFFS = None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("impact_coeffs.json unreadable (%s); using global default", exc)
        _CALIBRATED_COEFFS = None
    return _CALIBRATED_COEFFS


def _confirmed_bucket_coeff(coeffs: dict, bucket_name: str | None) -> float | None:
    """The bucket's coefficient when it exists and is ``confirmed``, else ``None``."""
    if not bucket_name:
        return None
    bucket = coeffs.get("buckets", {}).get(bucket_name)
    if bucket and bucket.get("confidence") == "confirmed" and bucket.get("coeff") is not None:
        return float(bucket["coeff"])
    return None


def get_impact_coeff(
    symbol: str | None = None,
    adv_notional_idr: float | None = None,
) -> float:
    """
    Resolve the square-root impact coefficient for *symbol*.

    Resolution order, using only ``confirmed`` entries from
    ``impact_coeffs.json``:

    1. Per-name coeff (when the name is confirmed and has a non-null coeff).
    2. The bucket recorded against that name in ``per_name``.
    3. The bucket implied by *adv_notional_idr*, when one is supplied.
    4. The file's ``global_default``.
    5. The module constant ``SQRT_IMPACT_COEFF`` (when the file is absent).

    Step 3 is the point of this resolver (plan 18 §4.3). ``per_name`` holds only
    the handful of names with tick capture; every other name in the universe was
    previously routed straight to ``global_default = 1.0``, so a measured bucket
    coefficient could never reach the ~237 names that most needed it. 1.0 is the
    conservative high-friction end of the Almgren range, which is the safe
    direction for capital and the WRONG direction for a verdict — it kills
    candidates by assumption rather than by measurement.

    Passing ``symbol=None``, or an unknown symbol with no *adv_notional_idr*,
    still lands on the global default, so existing callers are unaffected.
    """
    coeffs = _load_calibrated_coeffs()
    if coeffs is None:
        return SQRT_IMPACT_COEFF

    global_default = float(coeffs.get("global_default", SQRT_IMPACT_COEFF))
    if symbol is None:
        return global_default

    per_name = coeffs.get("per_name", {}).get(symbol)
    if per_name is not None:
        if per_name.get("confidence") == "confirmed" and per_name.get("coeff") is not None:
            return float(per_name["coeff"])
        recorded = _confirmed_bucket_coeff(coeffs, per_name.get("bucket"))
        if recorded is not None:
            return recorded

    if adv_notional_idr is not None and adv_notional_idr > 0.0:
        derived = _confirmed_bucket_coeff(coeffs, liquidity_bucket(float(adv_notional_idr)))
        if derived is not None:
            return derived

    return global_default


# ---------------------------------------------------------------------------
# Helpers: daily vol and ADV from an intraday DataFrame
# ---------------------------------------------------------------------------

def compute_daily_vol_bps(intraday_df: pd.DataFrame) -> float:
    """
    Estimate daily close-to-close return volatility in basis points.

    Groups the intraday DataFrame by ``date``, takes the last bar's close as
    the daily close, then computes the sample standard deviation of
    log(close[t] / close[t-1]) × 1e4.

    Degenerate cases:
    - Fewer than 2 trading days → returns ``_DEGENERATE_VOL_BPS`` (large penalty).
    - All closes NaN → returns ``_DEGENERATE_VOL_BPS``.
    - Zero-vol series (std = 0) → returns 0.0 (no impact from zero vol).

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame with ``date`` and ``close`` columns.

    Returns
    -------
    float
        Daily close-to-close return stdev in basis points (≥ 0).
    """
    if intraday_df is None or len(intraday_df) == 0:
        return _DEGENERATE_VOL_BPS

    # Take the last bar close for each trading day.
    daily_close = (
        intraday_df
        .groupby("date")["close"]
        .last()
        .dropna()
        .sort_index()
    )

    if len(daily_close) < 2:
        logger.debug("compute_daily_vol_bps: only %d day(s), returning degenerate", len(daily_close))
        return _DEGENERATE_VOL_BPS

    log_rets = np.diff(np.log(daily_close.to_numpy(dtype=float)))

    # Guard against NaN / Inf in log returns (e.g. zero close).
    valid = log_rets[np.isfinite(log_rets)]
    if len(valid) < 1:
        return _DEGENERATE_VOL_BPS

    vol = float(np.std(valid, ddof=1)) * 1e4  # convert to bps
    return max(0.0, vol)


def compute_adv_notional_idr(intraday_df: pd.DataFrame) -> float:
    """
    Estimate average daily turnover (ADV) in IDR.

    For each trading day, sums (close × volume) over all bars to get the day's
    turnover.  Returns the median across all days.

    Degenerate cases:
    - Empty DataFrame or all-zero/NaN volume → returns 0.0.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame with ``date``, ``close``, and ``volume`` columns.

    Returns
    -------
    float
        Median daily turnover in IDR (≥ 0).
    """
    if intraday_df is None or len(intraday_df) == 0:
        return 0.0

    df = intraday_df.copy()
    df["_turnover"] = pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")

    daily_turnover = (
        df
        .groupby("date")["_turnover"]
        .sum()
        .dropna()
    )

    if len(daily_turnover) == 0:
        return 0.0

    adv = float(np.nanmedian(daily_turnover.to_numpy(dtype=float)))
    return max(0.0, adv)


# ---------------------------------------------------------------------------
# Core impact formula
# ---------------------------------------------------------------------------

def compute_impact_bps(
    daily_vol_bps: float,
    adv_notional_idr: float,
    position_notional_idr: float = POSITION_NOTIONAL_IDR,
    *,
    use_calibrated: bool = False,
    symbol: str | None = None,
) -> float:
    """
    Compute round-trip market impact in basis points using the square-root model.

    Formula:
        participation_rate = clamp(position_notional_idr / adv_notional_idr, 0, 1)
        impact_bps = 2 * SQRT_IMPACT_COEFF * daily_vol_bps * sqrt(participation_rate)

    The ``2 *`` factor accounts for both entry and exit legs of a round trip.
    The one-tick spread is already in ``min_edge_to_clear_bps``; this model
    estimates the *additional* book-walking cost beyond the first tick.

    Degenerate cases:
    - adv_notional_idr <= 0 → participation cannot be computed; returns
      ``_DEGENERATE_IMPACT_BPS`` (large penalty) to fail any cost gate.

    Parameters
    ----------
    daily_vol_bps : float
        Daily close-to-close return standard deviation in basis points.
    adv_notional_idr : float
        Average daily turnover in IDR (from ``compute_adv_notional_idr``).
    position_notional_idr : float
        Order notional for this name (default: ``POSITION_NOTIONAL_IDR``).
    use_calibrated : bool
        When ``True``, resolve the impact coefficient via
        ``get_impact_coeff(symbol)`` (per-name → bucket → global default from
        ``impact_coeffs.json``).  When ``False`` (default), use the assumed
        ``SQRT_IMPACT_COEFF`` constant, preserving pre-plan-15 behaviour.
    symbol : str | None
        Symbol to resolve a calibrated coeff for; ignored unless
        ``use_calibrated`` is ``True``.

    Returns
    -------
    float
        Round-trip impact in basis points (≥ 0).
    """
    if adv_notional_idr <= 0.0:
        logger.debug(
            "compute_impact_bps: adv_notional_idr=%s <= 0; returning degenerate impact",
            adv_notional_idr,
        )
        return _DEGENERATE_IMPACT_BPS

    participation_rate = min(position_notional_idr / adv_notional_idr, 1.0)
    participation_rate = max(participation_rate, 0.0)

    coeff = (
        get_impact_coeff(symbol, adv_notional_idr=adv_notional_idr)
        if use_calibrated
        else SQRT_IMPACT_COEFF
    )
    impact = 2.0 * coeff * daily_vol_bps * np.sqrt(participation_rate)
    return float(impact)


def compute_impact_bps_from_df(
    intraday_df: pd.DataFrame,
    position_notional_idr: float = POSITION_NOTIONAL_IDR,
    *,
    use_calibrated: bool = False,
    symbol: str | None = None,
) -> float:
    """
    Convenience wrapper: compute round-trip impact from an intraday DataFrame.

    Chains ``compute_daily_vol_bps`` → ``compute_adv_notional_idr`` →
    ``compute_impact_bps``.  Suitable for direct use in ``_run_intraday_cell``.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame.
    position_notional_idr : float
        Order notional (default: ``POSITION_NOTIONAL_IDR``).

    Returns
    -------
    float
        Round-trip impact in basis points (≥ 0).
    """
    vol_bps = compute_daily_vol_bps(intraday_df)
    adv = compute_adv_notional_idr(intraday_df)
    return compute_impact_bps(
        daily_vol_bps=vol_bps,
        adv_notional_idr=adv,
        position_notional_idr=position_notional_idr,
        use_calibrated=use_calibrated,
        symbol=symbol,
    )


def resolve_impact_coeff_from_df(
    intraday_df: pd.DataFrame,
    *,
    use_calibrated: bool = False,
    symbol: str | None = None,
) -> float:
    """
    The Almgren coefficient ``compute_impact_bps_from_df`` would resolve for
    this DataFrame/symbol — for PROVENANCE only, not a second impact model.

    ``compute_impact_bps`` picks its coefficient internally (either the flat
    ``SQRT_IMPACT_COEFF`` or ``get_impact_coeff(symbol, adv)`` when
    ``use_calibrated``) and returns only the resulting bps figure, so a
    caller that needs to RECORD which coefficient actually priced a cell
    (see ``strategy_fit.harness.CellResult.impact_priced_coeff``, ADR-0017)
    has no way to recover it after the fact.  This mirrors that internal
    resolution exactly, using the same ADV estimate, so the recorded value
    is guaranteed to match what was actually charged.
    """
    adv = compute_adv_notional_idr(intraday_df)
    return get_impact_coeff(symbol, adv_notional_idr=adv) if use_calibrated else SQRT_IMPACT_COEFF


# ---------------------------------------------------------------------------
# Helpers: daily vol and ADV from a DAILY-bar DataFrame (plan 13)
# ---------------------------------------------------------------------------

def compute_daily_vol_bps_from_daily_bars(daily_df: pd.DataFrame) -> float:
    """
    Estimate daily close-to-close return volatility in basis points from
    bars that are ALREADY one-row-per-day.

    Same contract as ``compute_daily_vol_bps``, minus the ``groupby("date")``:
    daily-family bars (e.g. from ``ScreenerDataSource.daily_history``) are
    already one row per trading day, so grouping would be a no-op at best and
    silently wrong if a symbol ever has duplicate dates at worst.

    Sorts by ``date`` ascending, coerces ``close`` to numeric, and computes
    the sample standard deviation of log(close[t] / close[t-1]) × 1e4.

    Degenerate cases:
    - Fewer than 2 rows → returns ``_DEGENERATE_VOL_BPS`` (large penalty).
    - All closes NaN / no finite log returns → returns ``_DEGENERATE_VOL_BPS``.
    - Zero-vol series (std = 0) → returns 0.0 (no impact from zero vol).

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily bar DataFrame with ``date`` and ``close`` columns.

    Returns
    -------
    float
        Daily close-to-close return stdev in basis points (≥ 0).
    """
    if daily_df is None or len(daily_df) == 0:
        return _DEGENERATE_VOL_BPS

    df = daily_df.sort_values("date")
    closes = pd.to_numeric(df["close"], errors="coerce").dropna().to_numpy(dtype=float)

    if len(closes) < 2:
        logger.debug(
            "compute_daily_vol_bps_from_daily_bars: only %d row(s), returning degenerate",
            len(closes),
        )
        return _DEGENERATE_VOL_BPS

    log_rets = np.diff(np.log(closes))

    # Guard against NaN / Inf in log returns (e.g. zero close).
    valid = log_rets[np.isfinite(log_rets)]
    if len(valid) < 1:
        return _DEGENERATE_VOL_BPS

    vol = float(np.std(valid, ddof=1)) * 1e4  # convert to bps
    return max(0.0, vol)


def compute_adv_notional_idr_from_daily_bars(daily_df: pd.DataFrame) -> float:
    """
    Estimate average daily turnover (ADV) in IDR from bars that are already
    one-row-per-day.

    Prefers the ``value`` column when present — chartbit's own daily traded
    IDR turnover, which is more accurate than a close×volume reconstruction
    (it reflects actual traded value across the session rather than a single
    closing print times the day's share volume).  Falls back to
    median(close × volume) when ``value`` is absent or has no positive entry
    at all (i.e. it carries no usable turnover information).

    The median itself is taken over every finite, non-negative day — zero-
    turnover days are KEPT, not filtered.  See ``_median_turnover`` for why
    that is the conservative choice.

    Degenerate cases:
    - Empty DataFrame, all-NaN, or no finite turnover anywhere (in either
      ``value`` or the close×volume fallback) → returns 0.0.  The caller's
      ``compute_impact_bps`` treats ``adv <= 0`` as degenerate and returns
      ``_DEGENERATE_IMPACT_BPS``.  An all-zero-turnover name therefore also
      lands on the degenerate penalty, which is correct: it has no capacity.

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily bar DataFrame with ``close`` and ``volume`` columns, and
        optionally a pre-computed ``value`` (daily IDR turnover) column.

    Returns
    -------
    float
        Median daily turnover in IDR (≥ 0).
    """
    if daily_df is None or len(daily_df) == 0:
        return 0.0

    df = daily_df.copy()

    if "value" in df.columns:
        value = pd.to_numeric(df["value"], errors="coerce")
        if bool((value > 0).any()):
            logger.debug("compute_adv_notional_idr_from_daily_bars: using value column")
            return _median_turnover(value)

    logger.debug("compute_adv_notional_idr_from_daily_bars: using close x volume fallback")
    turnover = pd.to_numeric(df["close"], errors="coerce") * pd.to_numeric(df["volume"], errors="coerce")
    return _median_turnover(turnover)


def _median_turnover(turnover: pd.Series) -> float:
    """
    Median of a daily-turnover series over every finite, NON-NEGATIVE entry.

    Zero-turnover days (suspensions, no-trade days on a thin name) are kept in
    the median rather than filtered out.  This is deliberate and it is the
    conservative choice: a day on which the name did not trade is a day the
    basket could not have been executed, so it is real evidence about capacity.
    Dropping zeros would raise the ADV estimate, lower
    ``participation = position_notional / adv``, and therefore *understate*
    impact — and it would do so most on exactly the thin, frequently-untraded
    penny names where the impact charge decides the cell (plan 13).

    Only NaN / non-finite entries are excluded, because those are missing
    measurements rather than observed non-trading.  Returns 0.0 when nothing
    finite remains, which the caller treats as degenerate.
    """
    finite = turnover[np.isfinite(turnover) & (turnover >= 0)]
    if len(finite) == 0:
        return 0.0
    return max(0.0, float(np.median(finite.to_numpy(dtype=float))))


def compute_impact_bps_from_daily_df(
    daily_df: pd.DataFrame,
    position_notional_idr: float = POSITION_NOTIONAL_IDR,
    *,
    use_calibrated: bool = False,
    symbol: str | None = None,
) -> float:
    """
    Convenience wrapper: compute round-trip impact from a DAILY-bar DataFrame.

    Chains ``compute_daily_vol_bps_from_daily_bars`` →
    ``compute_adv_notional_idr_from_daily_bars`` → ``compute_impact_bps``.
    Suitable for direct use in ``_run_single_name_cell`` (plan 13).

    The underlying formula is unchanged from the intraday path: the
    square-root law prices ONE execution (entry leg + exit leg, the ``2 *``
    factor), and under ADR-0011 a daily-signal basket is entered atomically
    in one day and exited atomically in one day — exactly like the intraday
    same-day case.  Holding the position across the days in between is not
    an execution and does not get repriced.  What differs from the intraday
    path is only how ``daily_vol_bps`` and ``adv_notional_idr`` are
    *estimated*, because daily-family bars are already one row per day and
    the intraday helpers' ``groupby("date")`` would be wrong (a no-op at
    best) applied to them.

    Parameters
    ----------
    daily_df : pd.DataFrame
        Daily bar DataFrame.
    position_notional_idr : float
        Order notional (default: ``POSITION_NOTIONAL_IDR``).

    Returns
    -------
    float
        Round-trip impact in basis points (≥ 0).
    """
    vol_bps = compute_daily_vol_bps_from_daily_bars(daily_df)
    adv = compute_adv_notional_idr_from_daily_bars(daily_df)
    return compute_impact_bps(
        daily_vol_bps=vol_bps,
        adv_notional_idr=adv,
        position_notional_idr=position_notional_idr,
        use_calibrated=use_calibrated,
        symbol=symbol,
    )


def resolve_impact_coeff_from_daily_df(
    daily_df: pd.DataFrame,
    *,
    use_calibrated: bool = False,
    symbol: str | None = None,
) -> float:
    """
    The Almgren coefficient ``compute_impact_bps_from_daily_df`` would
    resolve for this DataFrame/symbol — for PROVENANCE only.  See
    ``resolve_impact_coeff_from_df`` (the intraday counterpart) for the
    rationale; this uses the daily-bar ADV estimator so the resolved
    coefficient matches what ``compute_impact_bps_from_daily_df`` actually
    charged, bucket-for-bucket.
    """
    adv = compute_adv_notional_idr_from_daily_bars(daily_df)
    return get_impact_coeff(symbol, adv_notional_idr=adv) if use_calibrated else SQRT_IMPACT_COEFF
