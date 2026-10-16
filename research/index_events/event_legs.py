"""
index_events/event_legs.py — numerics for the plan-21 §1a event-time breakdown.

Pure numpy/pandas, no I/O. Everything works on (sessions x names) arrays so it
can be tested on a synthetic panel.

Legs (plan §1a), index = session position, a = announce session, p = passive session:
  L1 close_{a-20} -> close_a        L2 close_a -> open_{a+1}     L3 open_{a+1} -> close_{a+1}
  L4 close_{a+1} -> close_p         L5 close_p -> close_{p+k}   (k <= 20, partial allowed)
All in log returns. Market-adjusted leg = name leg - equal-weight market leg
(market excludes the event name). Legs are additive: sum(L1..L5) equals the
full-window market-adjusted log return close_{a-20} -> close_{p+k}.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BAD_BAR_ABS_RETURN = 0.50     # same convention as families_idx_structural
PRE_DAYS = 20
POST_DAYS = 20
LEGS = ("L1", "L2", "L3", "L4", "L5")
PATH_A = (-20, 15)            # event-time range anchored at a
PATH_P = (-15, 20)            # event-time range anchored at p


@dataclass(frozen=True)
class Market:
    """Per-name overnight/intraday log returns plus cross-sectional sums.

    A (day, name) cell is valid when previous close, open and close exist, the
    name traded (volume > 0) and |close/prev_close - 1| <= 50%. Overnight and
    intraday use the same cell set, so overnight + intraday == close-to-close.
    """
    on: np.ndarray
    idn: np.ndarray
    valid: np.ndarray
    s_on: np.ndarray
    s_id: np.ndarray
    cnt: np.ndarray


def build_market(close: np.ndarray, open_: np.ndarray, volume: np.ndarray) -> Market:
    c, o, v = (np.asarray(x, dtype=float) for x in (close, open_, volume))
    prev = np.vstack([np.full((1, c.shape[1]), np.nan), c[:-1]])
    with np.errstate(all="ignore"):
        sane = np.abs(c / prev - 1.0) <= BAD_BAR_ABS_RETURN
        valid = np.isfinite(prev) & np.isfinite(o) & np.isfinite(c) & (np.nan_to_num(v) > 0) & sane
        on = np.where(valid, np.log(o / prev), np.nan)
        idn = np.where(valid, np.log(c / o), np.nan)
    return Market(on, idn, valid, np.nansum(on, axis=1), np.nansum(idn, axis=1), valid.sum(axis=1))


def market_excl(mkt: Market, j: int, lo: int, hi: int) -> tuple[np.ndarray, np.ndarray]:
    """Equal-weight overnight and intraday market returns on sessions lo..hi (inclusive), excluding name j."""
    sl = slice(lo, hi + 1)
    own = mkt.valid[sl, j]
    n = mkt.cnt[sl] - own
    s_on = mkt.s_on[sl] - np.where(own, np.nan_to_num(mkt.on[sl, j]), 0.0)
    s_id = mkt.s_id[sl] - np.where(own, np.nan_to_num(mkt.idn[sl, j]), 0.0)
    with np.errstate(all="ignore"):
        return np.where(n > 0, s_on / n, np.nan), np.where(n > 0, s_id / n, np.nan)


def _ln(a: float, b: float) -> float:
    return float(np.log(b / a))


def l5_horizon(close_col: np.ndarray, ip: int, kmax: int = POST_DAYS) -> int:
    """Largest k <= kmax with a valid close at p+k (0 if none)."""
    top = min(kmax, len(close_col) - 1 - ip)
    for k in range(top, 0, -1):
        if np.isfinite(close_col[ip + k]):
            return k
    return 0


def event_legs(close: np.ndarray, open_: np.ndarray, mkt: Market, j: int, ia: int, ip: int) -> dict | None:
    """Raw and market-adjusted legs for name j. None if a required endpoint is missing.

    Required: close_{a-20}, close_a, open_{a+1}, close_{a+1}, close_p. L5 uses
    the longest available horizon k <= 20 (``l5_k``); NaN if k == 0.
    """
    T = close.shape[0]
    if ia - PRE_DAYS < 0 or ia + 1 >= T or ip < ia + 1 or ip >= T:
        return None
    c, o = close[:, j], open_[:, j]
    need = (c[ia - PRE_DAYS], c[ia], o[ia + 1], c[ia + 1], c[ip])
    if not all(np.isfinite(x) and x > 0 for x in need):
        return None
    k = l5_horizon(c, ip)
    lo, hi = ia - PRE_DAYS, ip + k
    m_on, m_id = market_excl(mkt, j, lo, hi)
    m_cc = np.nan_to_num(m_on) + np.nan_to_num(m_id)          # index 0 == session lo
    at = lambda s: s - lo
    name = {
        "L1": _ln(c[ia - PRE_DAYS], c[ia]), "L2": _ln(c[ia], o[ia + 1]),
        "L3": _ln(o[ia + 1], c[ia + 1]), "L4": _ln(c[ia + 1], c[ip]),
        "L5": _ln(c[ip], c[ip + k]) if k > 0 else np.nan,
    }
    market = {
        "L1": m_cc[at(ia - PRE_DAYS) + 1: at(ia) + 1].sum(), "L2": np.nan_to_num(m_on[at(ia + 1)]),
        "L3": np.nan_to_num(m_id[at(ia + 1)]), "L4": m_cc[at(ia + 1) + 1: at(ip) + 1].sum(),
        "L5": m_cc[at(ip) + 1: at(ip + k) + 1].sum() if k > 0 else np.nan,
    }
    out: dict = {"l5_k": k}
    for L in LEGS:
        out[f"raw_{L}"] = name[L]
        out[f"mkt_{L}"] = market[L]
        out[L] = name[L] - market[L]
    out["T14"] = sum(out[L] for L in LEGS[:4])
    out["T15"] = out["T14"] + (out["L5"] if k > 0 else 0.0)
    return out


def bad_bar_in_window(close_col: np.ndarray, lo: int, hi: int) -> bool:
    """True if any consecutive-session close-to-close move in lo..hi exceeds 50%."""
    seg = close_col[lo: hi + 1]
    with np.errstate(all="ignore"):
        r = np.abs(seg[1:] / seg[:-1] - 1.0)
    return bool(np.nanmax(r) > BAD_BAR_ABS_RETURN) if np.isfinite(r).any() else False


def event_paths(close_ff: np.ndarray, mkt: Market, j: int, ia: int, ip: int, last_valid: int) -> dict | None:
    """Market-adjusted cumulative log-return paths anchored at a and at p.

    ``close_ff`` is forward-filled close; sessions after ``last_valid`` are NaN.
    Returns {"a": array over PATH_A, "p": array over PATH_P} (NaN where outside data).
    """
    T = close_ff.shape[0]
    lo = max(0, min(ia + PATH_A[0], ip + PATH_P[0]))
    hi = min(T - 1, max(ia + PATH_A[1], ip + PATH_P[1]))
    if ia + PATH_A[0] < 0:
        return None
    m_on, m_id = market_excl(mkt, j, lo, hi)
    m_cc = np.nan_to_num(m_on) + np.nan_to_num(m_id)
    cs = np.cumsum(m_cc)                                       # cs[i] = sum m_cc over lo..lo+i
    with np.errstate(all="ignore"):
        lc = np.log(close_ff[lo: hi + 1, j])
    lc = np.where(np.arange(lo, hi + 1) <= last_valid, lc, np.nan)
    adj = lc - cs

    def anchored(anchor: int, rng: tuple[int, int]) -> np.ndarray:
        out = np.full(rng[1] - rng[0] + 1, np.nan)
        base = adj[anchor - lo]
        for n, r in enumerate(range(rng[0], rng[1] + 1)):
            s = anchor + r
            if lo <= s <= hi:
                out[n] = adj[s - lo] - base
        return out
    return {"a": anchored(ia, PATH_A), "p": anchored(ip, PATH_P)}


# ---------------------------------------------------------------------------
# Review-clustered statistics
# ---------------------------------------------------------------------------

def clustered_t(values, clusters) -> dict:
    """Average within cluster first; t = mean(cluster means) / (sd / sqrt(n_clusters))."""
    s = pd.Series(np.asarray(values, dtype=float), index=pd.Index(clusters)).dropna()
    n_ev = len(s)
    if n_ev == 0:
        return {"n_events": 0, "n_reviews": 0, "mean_reviews": np.nan, "se": np.nan, "t": np.nan}
    rm = s.groupby(level=0).mean()
    n = len(rm)
    if n < 2:
        return {"n_events": n_ev, "n_reviews": n, "mean_reviews": float(rm.mean()), "se": np.nan, "t": np.nan}
    se = float(rm.std(ddof=1) / np.sqrt(n))
    return {"n_events": n_ev, "n_reviews": n, "mean_reviews": float(rm.mean()), "se": se,
            "t": float(rm.mean() / se) if se > 0 else np.nan}


def leg_stats(df: pd.DataFrame, col: str) -> dict:
    ct = clustered_t(df[col].to_numpy(), df["review_label"].to_numpy())
    v = df[col].dropna()
    return {"N": ct["n_events"], "reviews": ct["n_reviews"], "mean": float(v.mean()) if len(v) else np.nan,
            "median": float(v.median()) if len(v) else np.nan, "t": ct["t"]}


def cell_table(df: pd.DataFrame) -> dict:
    """Per-leg stats plus the mean-based share of the L1..L4 total."""
    out = {L: leg_stats(df, L) for L in LEGS}
    out["T14"] = leg_stats(df, "T14")
    out["T15"] = leg_stats(df, "T15")
    tot = out["T14"]["mean"]
    for L in LEGS[:4]:
        out[L]["share"] = out[L]["mean"] / tot if tot and np.isfinite(tot) and tot != 0 else np.nan
    out["reviews"] = int(df["review_label"].nunique())
    out["events"] = len(df)
    return out


FLOOR_REVIEWS = 12
T_MIN = 2.0
SHARE_MIN = 0.5


def decision_readings(tab: dict, side: str) -> list[str]:
    """Apply the plan §1a decision table literally. side: 'add' or 'del'."""
    if tab["events"] == 0:
        return ["no events"]
    L = {k: tab[k] for k in LEGS}
    tot = tab["T14"]["mean"]
    out = []
    expected = (tot > 0) if side == "add" else (tot < 0)
    if not (np.isfinite(tot) and expected):
        out.append("L1-L4 total is not in the expected direction (additions > 0, deletions < 0): "
                   "no anticipation / L2 / L4 reading")
        return out + _deletion_rebound(L, tot, side)
    l1 = L["L1"]
    if (np.isfinite(l1.get("share", np.nan)) and l1["share"] >= SHARE_MIN and np.isfinite(l1["t"])
            and abs(l1["t"]) >= T_MIN and np.sign(l1["mean"]) == np.sign(tot)):
        out.append("anticipation (L1 share >= 50% of L1-L4 and |t| >= 2): promote H4")
    if np.isfinite(L["L2"].get("share", np.nan)) and L["L2"]["share"] >= SHARE_MIN:
        out.append("L2 dominates (share >= 50%): priced in at the open, nothing long-only on this side")
    if side == "add" and L["L4"]["mean"] > 0 and np.isfinite(L["L4"]["t"]) and L["L4"]["t"] >= T_MIN:
        out.append("L4 positive and material (mean > 0, t >= 2): H1 live candidate")
    out += _deletion_rebound(L, tot, side)
    return out or ["no reading applies"]


def _deletion_rebound(L: dict, tot: float, side: str) -> list[str]:
    if side == "del" and np.isfinite(tot) and tot < 0 and L["L5"]["mean"] > 0:
        sup = np.isfinite(L["L5"]["t"]) and L["L5"]["t"] >= T_MIN
        return ["deletions L1-L4 negative then L5 positive" + (" (L5 t >= 2): H2 live candidate"
                if sup else " (L5 t < 2): pattern only, not supported")]
    return []
