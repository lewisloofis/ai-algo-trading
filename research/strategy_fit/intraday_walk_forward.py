"""
strategy_fit/intraday_walk_forward.py — Chronological IS/OOS split for intraday families.

Intraday data yields one trade-sized observation per trading day (e.g. one ORB
trade per day).  With ~55–60 trading days available per symbol, a rolling
multi-window walk-forward scheme is too thin (each OOS window would have ~5–8
obs, far below MIN_EFF_N=30 for the family functions).

This module therefore uses a single chronological split:
    - In-sample (IS):       first IS_FRAC (~60%) of distinct trading dates
    - Out-of-sample (OOS):  remaining ~40%

The family function is run independently on each split.  Persistence is declared
when OOS edge > 0 AND OOS eff_n >= MIN_OOS_EFF_N.  When OOS is too thin to
judge, the result is marked non-persistent conservatively (mirrors the daily
walk-forward module's "no evidence → decayed" stance).

This mirrors the constants and spirit of strategy_fit/walk_forward.py.  Reuses
DECAY_THRESHOLD_SHARPE, DECAY_THRESHOLD_HR, DECAY_MAJORITY_FRAC from that module
for conceptual consistency (even though the single-split approach does not use
rolling windows).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — mirroring walk_forward.py for consistency
# ---------------------------------------------------------------------------

IS_FRAC: float = 0.60
"""
Fraction of distinct trading dates used as the in-sample (IS) window.

Chosen to balance IS statistical power (~36 obs from 60 total) against
OOS coverage (~24 obs → enough to be slightly above MIN_OOS_EFF_N).
"""

MIN_OOS_EFF_N: int = 10
"""
Minimum out-of-sample effective-N required to declare a persistence verdict.

Below this threshold, OOS data is too thin to distinguish edge from noise, so
the result is conservatively marked non-persistent.  Consistent with the daily
walk-forward's "insufficient windows → decayed" stance.
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntradayWalkForwardResult:
    """
    Result of an intraday IS/OOS persistence check.

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
        Effective observation count in the OOS split.
    decay_ratio : float or None
        oos_edge_bps / is_edge_bps when is_edge_bps > 0; otherwise None.
        A ratio close to 1.0 means the edge persisted fully OOS.
    is_persistent : bool
        True iff oos_edge_bps > 0 AND oos_eff_n >= MIN_OOS_EFF_N.
        False when OOS is too thin, OOS edge is negative, or any split fails.
        Conservatively False when data is insufficient.
    skip_reason : str or None
        Populated when is_persistent=False with a human-readable explanation.
    """

    is_edge_bps: float | None
    oos_edge_bps: float | None
    is_hit_rate: float | None
    oos_hit_rate: float | None
    oos_eff_n: int | None
    decay_ratio: float | None
    is_persistent: bool
    skip_reason: str | None


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def walk_forward_intraday(
    intraday_df: pd.DataFrame,
    family_fn: Callable[[pd.DataFrame], dict[str, Any]],
    is_frac: float = IS_FRAC,
    min_oos_eff_n: int = MIN_OOS_EFF_N,
) -> IntradayWalkForwardResult:
    """
    Run a single chronological IS/OOS split on an intraday DataFrame.

    Steps:
    1. Collect distinct trading dates and split chronologically: IS = first
       is_frac, OOS = remainder.
    2. Run ``family_fn`` on IS slice and on OOS slice independently.
    3. Compute is_persistent = OOS edge > 0 AND OOS eff_n >= min_oos_eff_n.

    Note on thin data
    -----------------
    When the OOS slice produces fewer than min_oos_eff_n observations
    (because either the raw OOS day-count is small, or the family_fn
    reports insufficient_n), is_persistent is set to False conservatively
    with an explanatory skip_reason.  This is the same stance as the daily
    walk_forward_cell when it has fewer than min_windows windows.

    Note on data independence
    -------------------------
    The family_fn is applied separately to IS and OOS slices (not to the full
    DataFrame first).  This is stricter than the daily walk-forward (which
    applies signal_fn to the full df first).  For intraday families, each day's
    observation is independent (each is a separate intraday session), so per-split
    application is both feasible and correct.

    Parameters
    ----------
    intraday_df : pd.DataFrame
        1-min bar DataFrame with a ``date`` column.
    family_fn : callable
        An intraday family function (e.g. ``run_orb_family``).  Must accept a
        1-min bar DataFrame and return a dict with keys: status, gross_edge_bps,
        hit_rate, eff_n, p_value, skip_reason.
    is_frac : float
        Fraction of trading dates for IS (default: IS_FRAC=0.60).
    min_oos_eff_n : int
        Minimum OOS eff_n for a non-conservative persistence verdict
        (default: MIN_OOS_EFF_N=10).

    Returns
    -------
    IntradayWalkForwardResult
        Immutable result struct.
    """
    _conservative = IntradayWalkForwardResult(
        is_edge_bps=None,
        oos_edge_bps=None,
        is_hit_rate=None,
        oos_hit_rate=None,
        oos_eff_n=None,
        decay_ratio=None,
        is_persistent=False,
        skip_reason=None,
    )

    if intraday_df is None or len(intraday_df) == 0:
        return IntradayWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": "empty intraday DataFrame"}
        )

    # 1. Chronological date split.
    all_dates = sorted(intraday_df["date"].unique())
    n_total = len(all_dates)
    if n_total < 2:
        return IntradayWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": f"only {n_total} trading date(s)"}
        )

    n_is = max(1, int(round(n_total * is_frac)))
    is_dates = set(all_dates[:n_is])
    oos_dates = set(all_dates[n_is:])

    is_df = intraday_df[intraday_df["date"].isin(is_dates)].copy()
    oos_df = intraday_df[intraday_df["date"].isin(oos_dates)].copy()

    # 2. Run family on IS.
    try:
        is_result = family_fn(is_df)
    except Exception as exc:
        logger.warning("walk_forward_intraday: IS family_fn raised %s", exc)
        return IntradayWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": f"IS family_fn raised: {exc}"}
        )

    # 3. Run family on OOS.
    if len(oos_df) == 0:
        return IntradayWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": "OOS split is empty"}
        )
    try:
        oos_result = family_fn(oos_df)
    except Exception as exc:
        logger.warning("walk_forward_intraday: OOS family_fn raised %s", exc)
        return IntradayWalkForwardResult(
            **{**_conservative.__dict__, "skip_reason": f"OOS family_fn raised: {exc}"}
        )

    # 4. Extract metrics; handle non-"tested" statuses gracefully.
    is_edge = is_result.get("gross_edge_bps") if is_result.get("status") == "tested" else None
    is_hr = is_result.get("hit_rate") if is_result.get("status") == "tested" else None

    oos_edge = oos_result.get("gross_edge_bps") if oos_result.get("status") == "tested" else None
    oos_hr = oos_result.get("hit_rate") if oos_result.get("status") == "tested" else None
    oos_eff_n = oos_result.get("eff_n") if oos_result.get("status") == "tested" else None

    # 5. Thin-OOS check: too few observations → conservative.
    if oos_eff_n is None or oos_eff_n < min_oos_eff_n:
        actual_n = oos_eff_n if oos_eff_n is not None else 0
        return IntradayWalkForwardResult(
            is_edge_bps=is_edge,
            oos_edge_bps=oos_edge,
            is_hit_rate=is_hr,
            oos_hit_rate=oos_hr,
            oos_eff_n=actual_n,
            decay_ratio=None,
            is_persistent=False,
            skip_reason=(
                f"thin OOS: oos_eff_n={actual_n} < MIN_OOS_EFF_N={min_oos_eff_n}; "
                "insufficient evidence for persistence"
            ),
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
            skip = "OOS family returned non-tested status"
        elif oos_edge <= 0:
            skip = f"OOS edge non-positive: oos_edge_bps={oos_edge:.1f}"
        else:
            skip = f"oos_eff_n={oos_eff_n} < MIN_OOS_EFF_N={min_oos_eff_n}"

    return IntradayWalkForwardResult(
        is_edge_bps=is_edge,
        oos_edge_bps=oos_edge,
        is_hit_rate=is_hr,
        oos_hit_rate=oos_hr,
        oos_eff_n=oos_eff_n,
        decay_ratio=decay_ratio,
        is_persistent=is_persistent,
        skip_reason=skip,
    )
