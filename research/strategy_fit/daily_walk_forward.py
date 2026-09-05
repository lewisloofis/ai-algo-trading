"""
strategy_fit/daily_walk_forward.py — Chronological IS/OOS split for daily
single-name families.

Today, families_single_name.py's DAILY cells only ever receive a rolling
*decay* verdict (strategy_fit/walk_forward.py) — every window that scores an
edge was drawn from the SAME chronological range the name was selected on.
That is precisely the defect that produced a fake in-sample-only edge in the
intraday ORB family (plan-10 W2): a rolling window schedule that re-tests the
same data a name was screened on is not an out-of-sample test, no matter how
many windows it uses. This module gives daily single-name cells the same
structural guarantee intraday_walk_forward.py already gives intraday cells:
a single chronological split where the OOS half is signal- and
selection-independent of the IS half.

Split method
------------
    - In-sample (IS):       first IS_FRAC (~60%) of ROWS, by row position
    - Out-of-sample (OOS):  remaining ~40%

The raw daily-bar DataFrame is sorted by ``date`` ascending and split BY ROW
POSITION *before* any signal is computed — ``run_single_name_family`` (and
therefore ``signal_fn``) is invoked independently on each slice. This is
deliberately stricter than "compute the signal on the full frame, then split
the observations": computing the signal first and splitting after would let
a rolling window's lookback (or a forward-return window) straddle the IS/OOS
boundary, which is exactly the kind of leakage a walk-forward split exists to
rule out. Splitting the raw bars first and re-deriving the signal on each
slice independently makes that leakage structurally impossible, at the cost
of two side effects, both conservative (they can only shrink measured N,
never manufacture an edge):

1. The OOS slice loses its rolling warm-up. ``mean_reversion_signal`` needs
   ``rolling(5)``, ``volume_breakout_signal`` needs ``rolling(10)``, and
   ``daily_momentum_signal`` needs ``pct_change(1)`` — each produces NaN
   signal for its first few rows when computed on a slice that does not
   carry the tail of the previous slice's history. Those NaN rows are
   dropped by the signal function itself (see families_single_name.py). At
   most ~10 observations are lost this way (bounded by the largest lookback,
   10). This is a deliberate trade of a little OOS power for a guarantee of
   no leakage — it is not a bug to "fix" by carrying lookback context across
   the split.
2. Symmetrically, the LAST few rows of the IS slice get a NaN
   ``forward_return`` (the signal functions shift the close price forward by
   ``hold`` rows to compute it, and the IS slice ends before that forward
   window exists) and are dropped from the IS-side observation count. This
   guarantees that no IS observation's forward-return window can reach past
   the split boundary into the OOS slice — the same one-sided leakage risk
   that a rolling walk-forward scheme must also avoid, but here it is
   structural rather than a parameter to get right.

Two distinct floors on oos_eff_n (mirrors intraday_walk_forward.py)
--------------------------------------------------------------------
MIN_OOS_EFF_N (imported from strategy_fit.intraday_walk_forward — single
source of truth, not redefined here) is a hard, not-zero floor: below it,
OOS is too thin to say anything at all, so is_persistent is forced False.
This is NOT a power calculation. Separately, required_oos_eff_n (via
lib.stats.required_effective_n) asks whether oos_eff_n is enough to *trust*
the specific observed edge given its dispersion. A cell can clear the hard
floor and still miss this power-derived number — that state is
oos_underpowered, an expected, common, and honest outcome (NOT a pipeline
failure), distinct from failing the hard floor outright.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from lib.stats import required_effective_n
from strategy_fit.families_single_name import run_single_name_family
from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IS_FRAC: float = 0.60
"""
Fraction of rows (chronologically first, by row position) used as the
in-sample (IS) window.

Chosen to match intraday_walk_forward.IS_FRAC — the same 60/40 balance of IS
statistical power against OOS coverage, applied here to daily bars instead of
distinct intraday trading dates.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DailyWalkForwardResult:
    """
    Result of a daily single-name IS/OOS persistence check.

    Field names are EXACTLY those of
    ``strategy_fit.intraday_walk_forward.IntradayWalkForwardResult`` — the
    caller threads both result types into the same ``CellResult`` fields, so
    the two dataclasses must stay name-compatible even though they are
    populated by different split logic.

    Attributes
    ----------
    is_edge_bps : float or None
        Gross edge in basis points on the in-sample split.
    oos_edge_bps : float or None
        Gross edge in basis points on the out-of-sample split.
    is_hit_rate : float or None
        Hit rate on the in-sample split.
    oos_hit_rate : float or None
        Hit rate on the out-of-sample split.
    oos_eff_n : int or None
        Effective (long-only, de-overlapped) observation count in the OOS
        split.
    decay_ratio : float or None
        oos_edge_bps / is_edge_bps when is_edge_bps > 0; otherwise None.
        A ratio close to 1.0 means the edge persisted fully OOS.
    is_persistent : bool
        True iff oos_edge_bps > 0 AND oos_eff_n >= min_oos_eff_n.
        False when OOS is too thin, OOS edge is negative, or either split
        fails. Conservatively False when data is insufficient.
    skip_reason : str or None
        Populated when is_persistent=False with a human-readable explanation.
    oos_sigma_bps : float or None
        Measured OOS per-trade return dispersion in bps — the sample std of
        THIS SAME de-overlapped, long-only-filtered signed-returns series
        that produces oos_eff_n and oos_edge_bps
        (families_single_name.py's per_trade_sigma_bps). A measurement,
        never a constant or a proxy (e.g. NOT unconditional daily
        close-to-close vol). None when unavailable (fewer than 2 OOS
        observations, or OOS not "tested") — required_oos_eff_n must not be
        computed with a fallback in that case.
    required_oos_eff_n : int or None
        Power-derived minimum oos_eff_n needed to resolve oos_edge_bps at
        z=2 sigma against oos_sigma_bps (lib.stats.required_effective_n).
        None when not computable — i.e. whenever oos_edge_bps is not a
        positive number (including when the hard floor was never cleared),
        OR oos_sigma_bps is unavailable. NOT a blocking gate; see
        oos_underpowered.
    oos_underpowered : bool
        True iff oos_eff_n clears min_oos_eff_n (the hard floor) but falls
        short of required_oos_eff_n. An honest, expected "not enough data
        yet" outcome — not a pipeline failure. Always False when
        is_persistent is False (the cell already failed for a more
        fundamental reason: thin OOS or a non-positive edge) OR when
        oos_sigma_bps is unavailable (power cannot be assessed at all).
    """

    is_edge_bps: float | None
    oos_edge_bps: float | None
    is_hit_rate: float | None
    oos_hit_rate: float | None
    oos_eff_n: int | None
    decay_ratio: float | None
    is_persistent: bool
    skip_reason: str | None
    oos_sigma_bps: float | None = None
    required_oos_eff_n: int | None = None
    oos_underpowered: bool = False


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def walk_forward_daily(
    df: pd.DataFrame,
    signal_fn: Callable[..., pd.DataFrame],
    symbol: str,
    min_edge_to_clear_bps: float,
    family_name: str,
    horizon_trading_days: int,
    is_frac: float = IS_FRAC,
    min_oos_eff_n: int = MIN_OOS_EFF_N,
) -> DailyWalkForwardResult:
    """
    Run a single chronological IS/OOS split on a daily-bar DataFrame.

    Steps
    -----
    1. Sort ``df`` by ``date`` ascending and split BY ROW POSITION: IS = the
       first ``is_frac`` of rows, OOS = the remainder.
    2. Run ``run_single_name_family`` on the IS slice and on the OOS slice
       INDEPENDENTLY (signal_fn is re-invoked on each raw slice — never on
       the full frame first). See the module docstring for why, and for the
       conservative row-count cost this independence carries.
    3. Compute is_persistent = OOS edge > 0 AND OOS eff_n >= min_oos_eff_n.

    Note on thin data
    -----------------
    When the OOS slice produces fewer than min_oos_eff_n observations
    (because either the raw OOS row-count is small, or run_single_name_family
    reports insufficient_n), is_persistent is set to False conservatively
    with an explanatory skip_reason. This mirrors
    intraday_walk_forward.walk_forward_intraday's stance exactly.

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV bar DataFrame with at least a ``date`` and ``close``
        column (``open``/``volume`` as required by ``signal_fn``). Not
        mutated.
    signal_fn : callable
        One of families_single_name.py's signal functions (daily_momentum_
        signal, mean_reversion_signal, volume_breakout_signal). Passed
        through to run_single_name_family unchanged.
    symbol : str
        Universe name (for logging), passed through to
        run_single_name_family.
    min_edge_to_clear_bps : float
        Structural friction floor for this name (plan-09 annotation), passed
        through to run_single_name_family.
    family_name : str
        Human-readable family identifier, passed through to
        run_single_name_family.
    horizon_trading_days : int
        Hold duration in trading days, passed through to
        run_single_name_family.
    is_frac : float
        Fraction of rows for IS (default: IS_FRAC=0.60).
    min_oos_eff_n : int
        Minimum OOS eff_n for a non-conservative persistence verdict
        (default: MIN_OOS_EFF_N=10, imported from intraday_walk_forward.py).

    Returns
    -------
    DailyWalkForwardResult
        Immutable result struct.
    """
    _conservative = DailyWalkForwardResult(
        is_edge_bps=None,
        oos_edge_bps=None,
        is_hit_rate=None,
        oos_hit_rate=None,
        oos_eff_n=None,
        decay_ratio=None,
        is_persistent=False,
        skip_reason=None,
    )

    if df is None or len(df) == 0:
        return DailyWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": "empty daily DataFrame"}
        )

    n_total = len(df)
    if n_total < 2:
        return DailyWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": f"only {n_total} row(s)"}
        )

    # 1. Chronological split BY ROW POSITION (never by re-deriving the
    # signal on the full frame first — see module docstring).
    sorted_df = df.sort_values("date", kind="stable").reset_index(drop=True)
    n_is = max(1, int(round(n_total * is_frac)))
    is_df = sorted_df.iloc[:n_is].copy()
    oos_df = sorted_df.iloc[n_is:].copy()

    # 2. Run the family independently on IS.
    try:
        is_result = run_single_name_family(
            is_df, signal_fn, symbol, min_edge_to_clear_bps, family_name, horizon_trading_days
        )
    except Exception as exc:
        logger.warning("walk_forward_daily: IS run raised %s", exc)
        return DailyWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": f"IS run raised: {exc}"}
        )

    # 3. Run the family independently on OOS.
    if len(oos_df) == 0:
        return DailyWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": "OOS split is empty"}
        )
    try:
        oos_result = run_single_name_family(
            oos_df, signal_fn, symbol, min_edge_to_clear_bps, family_name, horizon_trading_days
        )
    except Exception as exc:
        logger.warning("walk_forward_daily: OOS run raised %s", exc)
        return DailyWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": f"OOS run raised: {exc}"}
        )

    # 4. Extract metrics; handle non-"tested" statuses gracefully.
    is_tested = is_result.get("status") == "tested"
    is_edge = is_result.get("gross_edge_bps") if is_tested else None
    is_hr = is_result.get("hit_rate") if is_tested else None

    oos_tested = oos_result.get("status") == "tested"
    oos_edge = oos_result.get("gross_edge_bps") if oos_tested else None
    oos_hr = oos_result.get("hit_rate") if oos_tested else None
    oos_eff_n = oos_result.get("eff_n") if oos_tested else None
    # Measured per-trade dispersion from the SAME series that produced
    # oos_eff_n/oos_edge_bps (families_single_name.py) — never a proxy or a
    # fallback constant. None whenever OOS wasn't "tested" at all.
    oos_sigma_bps = oos_result.get("per_trade_sigma_bps") if oos_tested else None

    # 5. Thin-OOS check: too few observations → conservative.
    if oos_eff_n is None or oos_eff_n < min_oos_eff_n:
        actual_n = oos_eff_n if oos_eff_n is not None else 0
        if not is_tested:
            # Neither half of the split is usable — a materially different,
            # more fundamental failure than "OOS merely thin with a valid IS
            # comparison available". Report this explicitly rather than
            # letting a generic "thin OOS" message imply IS was fine.
            reason = (
                f"invalid split: neither IS (status={is_result.get('status')!r}) "
                f"nor OOS (oos_eff_n={actual_n} < MIN_OOS_EFF_N={min_oos_eff_n}) "
                "is usable at this split frequency — cannot validate persistence "
                "or form a decay comparison"
            )
        else:
            reason = (
                f"thin OOS: oos_eff_n={actual_n} < MIN_OOS_EFF_N={min_oos_eff_n}; "
                "insufficient evidence for persistence"
            )
        return DailyWalkForwardResult(
            is_edge_bps=is_edge,
            oos_edge_bps=oos_edge,
            is_hit_rate=is_hr,
            oos_hit_rate=oos_hr,
            oos_eff_n=actual_n,
            decay_ratio=None,
            is_persistent=False,
            skip_reason=reason,
        )

    # 6. Decay ratio: only meaningful when IS edge is positive.
    decay_ratio: float | None = None
    if is_edge is not None and is_edge > 0 and oos_edge is not None:
        decay_ratio = oos_edge / is_edge

    # 7. Persistence: OOS edge must be positive AND OOS eff_n >= threshold.
    is_persistent = bool(oos_edge is not None and oos_edge > 0 and oos_eff_n >= min_oos_eff_n)

    skip: str | None = None
    if not is_persistent:
        if oos_edge is None:
            skip = "OOS run returned non-tested status"
        elif oos_edge <= 0:
            skip = f"OOS edge non-positive: oos_edge_bps={oos_edge:.1f}"
        else:
            skip = f"oos_eff_n={oos_eff_n} < MIN_OOS_EFF_N={min_oos_eff_n}"

    # 8. Power-derived required-N (plan-10 W2.1, mirrors intraday_walk_
    # forward.py): given the hard floor is already cleared (we're past the
    # thin-OOS early return) and the OOS edge is positive, is oos_eff_n
    # actually enough to trust THIS edge, given its MEASURED dispersion?
    # Meaningless when the edge is non-positive (already a persistence
    # failure for reasons unrelated to power).
    #
    # oos_sigma_bps comes only from families_single_name.py's
    # per_trade_sigma_bps — the sample std of the exact same signed-returns
    # series that produced oos_eff_n/oos_edge_bps. There is NO fallback to a
    # constant or a proxy here: when oos_sigma_bps is unavailable,
    # required_n/is_underpowered simply stay unset ("sigma unavailable" is a
    # distinct, honestly-reported state, not silently substituted).
    required_n: int | None = None
    is_underpowered = False
    if oos_edge is not None and oos_edge > 0 and oos_sigma_bps is not None:
        required_n = required_effective_n(
            target_edge_bps=oos_edge,
            per_trade_sigma_bps=oos_sigma_bps,
            z=2.0,
        )
        is_underpowered = oos_eff_n < required_n

    return DailyWalkForwardResult(
        is_edge_bps=is_edge,
        oos_edge_bps=oos_edge,
        is_hit_rate=is_hr,
        oos_hit_rate=oos_hr,
        oos_eff_n=oos_eff_n,
        decay_ratio=decay_ratio,
        is_persistent=is_persistent,
        skip_reason=skip,
        oos_sigma_bps=oos_sigma_bps,
        required_oos_eff_n=required_n,
        oos_underpowered=is_underpowered,
    )
