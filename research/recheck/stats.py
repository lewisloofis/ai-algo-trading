"""
recheck/stats.py — milestone gating, the unit-level evaluation and the frozen alert bar (plan 26 §2).

Observation units. A trade held ``hold`` trading days earns returns on those days, so records whose entry
dates are fewer than ``hold`` trading days apart share return days: counting one unit per entry date
inflates the t-stat by roughly sqrt(hold) and reaches a milestone after N trading days instead of N
independent horizons (plan 04 was burned by exactly this). Units here are therefore NON-OVERLAPPING BLOCKS
of ``hold`` consecutive trading days by entry date: block id = trading_day_index(entry_date) // hold, with the
index taken from the daily store's sorted trading calendar (passed in). A unit's value is the mean of
``gross - null - cost`` over every record in the block; effective N is the number of non-empty blocks.
Families that already produce one non-overlapping period per observation (cross_sectional, factor_books, and
the monthly calendar windows) declare ``unit_hold=1``: one unit per entry date.

Residual overlap: entries late in block i still earn returns into block i+1, so the t-stat is Newey-West
HAC with one lag on the block series (``hac_t``); p is its one-sided normal survival function. Drop-top-5
is over blocks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats as sps


MILESTONES: tuple[int, ...] = (30, 60, 120)
N_LOOKS: int = len(MILESTONES)
ALPHA_FAMILY: float = 0.10
TOP_TRIM: int = 5
"""Observation units dropped for the concentration check (plan 26 §2.3)."""

RECORD_COLUMNS = ["signal_date", "entry_date", "gross_bps", "null_bps", "cost_bps"]
OPTIONAL_COLUMNS = ["symbol", "regime_bear"]


@dataclass(frozen=True)
class Evaluation:
    n: int
    """Effective N: independent observation units."""
    edge: float
    """Mean edge over the always-long null, after current cost, bps per unit."""
    cost: float
    p: float
    """One-sided p that the after-cost edge is above zero (normal survival function of t)."""
    drop5: float
    """Mean after-cost edge without the 5 best units; NaN when there are 5 or fewer."""
    t: float


def next_milestone(done: Sequence[int]) -> Optional[int]:
    """The first milestone above everything already evaluated; None when all looks are used."""
    last = max(done, default=0)
    return next((m for m in MILESTONES if m > last), None)


def milestone_to_evaluate(n: int, done: Sequence[int]) -> Optional[int]:
    """The highest milestone reached and above everything already evaluated (at most one per run).

    A lower milestone skipped because a higher one was reached is superseded: it is never queued, so a
    later run cannot evaluate 'milestone 30' on data that is already past 60."""
    last = max(done, default=0)
    reached = [m for m in MILESTONES if last < m <= n]
    return max(reached) if reached else None


def alert_p_threshold(k: int) -> float:
    """Bonferroni over every family and every look: ALPHA_FAMILY / (N_LOOKS * k)."""
    return ALPHA_FAMILY / (N_LOOKS * k)


def hac_t(x: np.ndarray, lags: int = 1) -> float:
    """t of the mean of ``x`` with a Newey-West (Bartlett) long-run variance: omega = g0 + 2 * sum_k w_k g_k,
    w_k = 1 - k / (lags + 1), g_k = (1/n) sum (x_t - m)(x_{t-k} - m); se = sqrt(omega / n). NaN if undefined."""
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = len(a)
    if n < 2:
        return float("nan")
    d = a - a.mean()
    omega = float(d @ d) / n
    for k in range(1, min(lags, n - 1) + 1):
        omega += 2.0 * (1.0 - k / (lags + 1.0)) * float(d[k:] @ d[:-k]) / n
    if not omega > 1e-12:
        return float("nan")
    return float(a.mean() / math.sqrt(omega / n))


def _block_ids(entry_dates: pd.Series, hold: int, calendar: Optional[Sequence[str]]) -> np.ndarray:
    dates = entry_dates.astype(str)
    if hold <= 1:
        return dates.to_numpy()
    if calendar is None or len(calendar) == 0:
        raise ValueError("a hold above 1 needs the trading calendar to form non-overlapping blocks")
    idx = np.searchsorted(np.asarray(list(calendar), dtype=str), dates.to_numpy(dtype=str), side="left")
    return idx // hold


def _units(records: pd.DataFrame, hold: int = 1, calendar: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """One row per block (per entry date when hold is 1), in time order: mean net edge, mean cost, bear share."""
    cols = ["gross_bps", "null_bps", "cost_bps"]
    ok = records[cols].apply(pd.to_numeric, errors="coerce").notna().all(axis=1)
    r = records[ok]
    if r.empty:
        return pd.DataFrame(columns=["net", "cost", "bear"])
    frame = pd.DataFrame({"unit": _block_ids(r["entry_date"], hold, calendar),
                          "net": r["gross_bps"] - r["null_bps"] - r["cost_bps"], "cost": r["cost_bps"]})
    if "regime_bear" in r.columns:
        frame["bear"] = r["regime_bear"].astype(float)
    g = frame.groupby("unit", sort=True)
    out = g[["net", "cost"]].mean()
    if "bear" in frame.columns:
        out["bear"] = g["bear"].mean()
    return out


def effective_n(records: pd.DataFrame, hold: int = 1, calendar: Optional[Sequence[str]] = None) -> int:
    return int(len(_units(records, hold, calendar)))


def evaluate(records: pd.DataFrame, hold: int = 1, calendar: Optional[Sequence[str]] = None) -> Evaluation:
    u = _units(records, hold, calendar)
    nan = float("nan")
    if u.empty:
        return Evaluation(0, nan, nan, nan, nan, nan)
    net = u["net"].to_numpy(dtype=float)
    t = hac_t(net)
    p = float(sps.norm.sf(t)) if np.isfinite(t) else nan
    drop5 = float(np.sort(net)[:-TOP_TRIM].mean()) if len(net) > TOP_TRIM else nan
    return Evaluation(int(len(net)), float(net.mean()), float(u["cost"].mean()), p, drop5, t)


def is_alert(ev: Evaluation, p_threshold: float) -> bool:
    """Edge after cost > 0, one-sided p strictly below the bar, and drop-top-5 still > 0."""
    def pos(x: float) -> bool:
        return bool(math.isfinite(x) and x > 0)

    return pos(ev.edge) and bool(math.isfinite(ev.p) and ev.p < p_threshold) and pos(ev.drop5)


def regime_split(records: pd.DataFrame, hold: int = 1, calendar: Optional[Sequence[str]] = None) -> Optional[dict]:
    """Bear / non-bear units and mean after-cost edge, for the families whose lead is regime-dependent.
    Descriptive only: it never enters the edge, p, drop-5 or the alert."""
    if "regime_bear" not in records.columns or records["regime_bear"].isna().all():
        return None
    u = _units(records, hold, calendar)
    out = {}
    for name, sel in (("bear", u["bear"] >= 0.5), ("nonbear", u["bear"] < 0.5)):
        sub = u[sel]
        out[name] = {"n": int(len(sub)), "edge": float(sub["net"].mean()) if len(sub) else float("nan")}
    return out
