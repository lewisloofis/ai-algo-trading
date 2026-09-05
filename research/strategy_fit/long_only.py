"""
strategy_fit/long_only.py — Shared long-only enforcement for the family library.

Governing constraint (commit c62eba8, 2026-06-29, "feat(orb): constrain to
long-only; remove short leg")
-----------------------------------------------------------------------------
IDX retail accounts cannot short sell — only selected institutions have
access. c62eba8 made long-only breakout the only valid trade direction for
the ORB family (families_intraday.py:run_orb_family). That constraint must
apply to every directional family in this library, not just ORB — this
module is the single place the constraint is implemented so it cannot be
applied to one family and silently omitted from the rest.

Two categories of family
------------------------
1. Directional families (single-name daily, intraday, event-driven): each
   observation carries a signal whose sign gives the trade direction. A
   negative-signal (or zero-signal) observation would require shorting to
   act on — it must be SKIPPED, never sign-flipped into a synthetic long
   return, and it must NOT count toward eff_n. Use `is_long_signal` (scalar,
   per-observation loops) or `long_only_mask` (vectorised, numpy arrays).

2. Structurally long/short families (cross-sectional decile spread, pairs
   stat-arb): the strategy *is* a short leg — there is no long-only version
   of "buy the top decile, short the bottom decile" or "long the cheap leg,
   short the rich leg" that preserves what the strategy tests. These must be
   excluded and labelled, never silently scored. Use
   `long_short_infeasible_result`.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Status / reason vocabulary
# ---------------------------------------------------------------------------

STATUS_LONG_SHORT_INFEASIBLE: str = "long_short_infeasible"

LONG_SHORT_INFEASIBLE_NOTE: str = (
    "IDX retail accounts cannot short-sell (see families_intraday.py "
    "run_orb_family, commit c62eba8). This family is structurally long/short "
    "and cannot be made long-only without changing what the strategy tests."
)


# ---------------------------------------------------------------------------
# Directional families: skip short/flat observations
# ---------------------------------------------------------------------------

def is_long_signal(signal: float) -> bool:
    """True if a scalar `signal` indicates a buyable (long) trade.

    signal <= 0 (short or flat) is not tradeable on an IDX retail account and
    must be skipped by the caller — never sign-flipped into a synthetic long
    return, and never counted toward eff_n.
    """
    return signal > 0.0


def long_only_mask(signals: np.ndarray) -> np.ndarray:
    """Vectorised counterpart of `is_long_signal` for an array of signals.

    Returns a boolean mask selecting only long-side (signal > 0) entries.
    Apply this mask to BOTH the signal array and any aligned forward-return
    array before computing eff_n, hit_rate, gross_edge_bps or a p-value —
    a masked-out (short/flat) observation must not contribute to any of them.
    """
    return np.asarray(signals, dtype=float) > 0.0


# ---------------------------------------------------------------------------
# Structurally long/short families: exclude and label, never score
# ---------------------------------------------------------------------------

def long_short_infeasible_result(
    reason_detail: str = "",
    eff_n: int | None = None,
    **extra_none_keys: Any,
) -> dict[str, Any]:
    """Standard result dict for a family that is structurally long/short.

    Mirrors the shape of the existing `insufficient_n` / `data_blocked`
    result dicts used across the family modules: a status + skip_reason,
    with every scored metric set to None so a downstream consumer can never
    mistake this for a tradeable result.

    Parameters
    ----------
    reason_detail : str
        Optional family-specific context prepended to LONG_SHORT_INFEASIBLE_NOTE
        (e.g. what was found before the infeasibility gate — a cointegrated
        pair, a computed ICIR — for a human reading the report).
    eff_n : int or None
        Unlike the scored metrics, eff_n is not itself a P&L number — it is a
        count of observations that *would* have been available. Callers may
        pass the real observed count (informative) or leave it None/0.
    extra_none_keys : Any
        Family-specific keys to add (e.g. `ic=None, ic_tstat=None, icir=None`
        for cross-sectional) so the returned dict satisfies that family's own
        "required keys" contract.

    Returns
    -------
    dict
        status="long_short_infeasible", skip_reason=<reason>, eff_n=<eff_n>,
        hit_rate=None, gross_edge_bps=None, net_edge_bps=None, p_value=None,
        plus any extra_none_keys.
    """
    reason = LONG_SHORT_INFEASIBLE_NOTE
    if reason_detail:
        reason = f"{reason_detail} {reason}"
    result: dict[str, Any] = {
        "status": STATUS_LONG_SHORT_INFEASIBLE,
        "skip_reason": reason,
        "eff_n": eff_n,
        "hit_rate": None,
        "gross_edge_bps": None,
        "net_edge_bps": None,
        "p_value": None,
    }
    result.update(extra_none_keys)
    return result
