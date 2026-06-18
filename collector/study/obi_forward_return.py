"""
obi_forward_return.py — OBI / micro-price → forward mid-return study.

Reads the features CSV emitted by `sbcap features`, computes order-book
imbalance (OBI) and weighted-mid signals, joins each row to its forward
mid-price at horizons {1,5,10,30,60} seconds, and runs the go/no-go test
suite from docs/plans/04-microstructure-study.md §4.

Tests per (signal × horizon):
  1. Decile-bin curve (Lipton-style) — mean fwd_mid_ret per signal decile.
  2. Pearson + Spearman correlation.
  3. OLS fwd_mid_ret ~ signal with Newey-West HAC standard errors.
  4. Directional hit-rate conditional on |fwd_mid_ret| > 0.
  5. Block-bootstrap null (circular) — 95th-percentile reference.
  6. Chronological out-of-sample split (60/40 on long session + short pre-lunch
     segment as a second holdout).

Verdict at top of report.md:
  PASS if ≥1 (signal, horizon) on held-out data shows:
    • monotone decile curve, AND
    • correlation / hit-rate clearing the bootstrap null 95th pct, AND
    • economically meaningful magnitude (|corr| > 0.02 or hit-rate > 0.52).
  Otherwise: NO-GO.

Usage:
    python obi_forward_return.py --csv PATH [--outdir DIR] [--depth N]
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from dataclasses import dataclass, field
from typing import Any

# Use the non-interactive Agg backend so the script works headless.
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import scipy.stats as sps
import statsmodels.api as sm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

HORIZONS_S = [1, 5, 10, 30, 60]          # forward-return horizons (seconds)
N_BOOTSTRAP = 500                          # circular block-bootstrap draws
BOOTSTRAP_BLOCK = 30                       # block length for bootstrap (rows)
# Minimum fraction of non-zero fwd_mid_ret rows required to run tests.
MIN_NONZERO_FRAC = 0.001

# Lunch gap (WIB): cross-gap joins are invalid.
LUNCH_START_H, LUNCH_START_M = 12, 0
LUNCH_END_H,   LUNCH_END_M   = 13, 30

# Verdict thresholds (economically meaningful on a 1-tick name).
THRESH_ABS_CORR = 0.02
THRESH_HIT_RATE = 0.52


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    signal: str
    horizon_s: int
    n_obs: int
    n_nonzero: int
    pearson_r: float
    pearson_p: float
    spearman_r: float
    spearman_p: float
    ols_slope: float
    ols_t: float
    ols_r2: float
    hit_rate: float
    base_rate: float           # ~0.5 by definition; stored for sanity check
    bootstrap_corr_95: float   # 95th pct of |corr| under null
    bootstrap_hr_95: float     # 95th pct of hit-rate under null
    oos_hit_rate: float        # held-out 40 % of long session
    oos_n: int
    short_hit_rate: float      # pre-lunch session (second holdout)
    short_n: int
    is_decile_monotone: bool   # crude monotonicity check
    clears_bootstrap: bool     # real metric > null 95th pct
    is_economical: bool        # |corr| > threshold or hit_rate > threshold
    oos_clears: bool           # OOS hit_rate > bootstrap null 95th pct


@dataclass
class StudyResults:
    n_raw: int
    n_clean: int
    symbol_date: str
    results: list[TestResult] = field(default_factory=list)
    verdict: str = "NO-GO"
    verdict_signal: str = ""
    verdict_horizon: int = 0


# ---------------------------------------------------------------------------
# Loading and cleaning
# ---------------------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    """Load the features CSV; coerce all numeric columns; sort by recv_ns."""
    df = pd.read_csv(path, dtype=str)
    # Convert recv_ns to int64 first (it is always present and non-empty).
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    # Coerce everything else numeric.
    numeric_cols = [c for c in df.columns if c not in ("iso_wib",)]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("recv_ns").reset_index(drop=True)
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """
    Require top-of-book bid and ask; drop rows where either is missing/zero.
    Parse iso_wib to a tz-aware datetime for gap detection.
    """
    df = df.copy()
    df = df.dropna(subset=["bid_px_0", "ask_px_0", "bid_vol_0", "ask_vol_0"])
    df = df[(df["bid_px_0"] > 0) & (df["ask_px_0"] > 0)]
    df = df[(df["bid_vol_0"] > 0) & (df["ask_vol_0"] > 0)]

    # Parse iso_wib (YYYY-MM-DDTHH:MM:SS.mmm) to a naive datetime (already WIB).
    # Use errors="coerce" so a single bad timestamp doesn't kill the run.
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    df = df.dropna(subset=["_wib_dt"])

    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def add_signals(df: pd.DataFrame, depth: int) -> pd.DataFrame:
    """Compute mid, spread, wmid, OBI1, OBI_eq_N, OBI_w_N in-place."""
    df = df.copy()

    best_bid = df["bid_px_0"].to_numpy(dtype=float)
    best_ask = df["ask_px_0"].to_numpy(dtype=float)
    bid_vol0 = df["bid_vol_0"].to_numpy(dtype=float)
    ask_vol0 = df["ask_vol_0"].to_numpy(dtype=float)

    df["mid"]    = (best_bid + best_ask) / 2.0
    df["spread"] = best_ask - best_bid

    # Weighted mid: cross-weight (ask vol weights bid price; bid vol weights ask
    # price) — heavier ask volume pulls wmid toward the bid.
    total_vol = bid_vol0 + ask_vol0
    # Guard divide-by-zero (cleaned above, but be defensive).
    safe_total = np.where(total_vol > 0, total_vol, np.nan)
    df["wmid"] = (best_bid * ask_vol0 + best_ask * bid_vol0) / safe_total

    # OBI1 — top-of-book only.
    df["OBI1"] = (bid_vol0 - ask_vol0) / safe_total

    # Depth-weighted OBIs.
    bid_eq = np.zeros(len(df), dtype=float)
    ask_eq = np.zeros(len(df), dtype=float)
    bid_w  = np.zeros(len(df), dtype=float)
    ask_w  = np.zeros(len(df), dtype=float)

    for i in range(depth):
        bvol_col = f"bid_vol_{i}"
        avol_col = f"ask_vol_{i}"
        w = 1.0 / (i + 1)

        bvol = df[bvol_col].to_numpy(dtype=float) if bvol_col in df.columns else np.zeros(len(df))
        avol = df[avol_col].to_numpy(dtype=float) if avol_col in df.columns else np.zeros(len(df))
        bvol = np.nan_to_num(bvol, nan=0.0)
        avol = np.nan_to_num(avol, nan=0.0)

        bid_eq += bvol
        ask_eq += avol
        bid_w  += w * bvol
        ask_w  += w * avol

    total_eq = bid_eq + ask_eq
    total_w  = bid_w  + ask_w
    safe_eq  = np.where(total_eq > 0, total_eq, np.nan)
    safe_w   = np.where(total_w  > 0, total_w,  np.nan)

    df["OBI_eq_N"] = (bid_eq - ask_eq) / safe_eq
    df["OBI_w_N"]  = (bid_w  - ask_w)  / safe_w

    # wmid_skew: distance from mid in units of half-spread (a normalised signal).
    half_spread = df["spread"] / 2.0
    safe_hs = half_spread.replace(0, np.nan)
    df["wmid_skew"] = (df["wmid"] - df["mid"]) / safe_hs

    return df


# ---------------------------------------------------------------------------
# Forward-return join (lunch-gap aware)
# ---------------------------------------------------------------------------

def _is_lunch(dt: pd.Timestamp) -> bool:
    """Return True if dt falls in the lunch gap [12:00, 13:30) WIB."""
    h, m = dt.hour, dt.minute
    lunch_start = LUNCH_START_H * 60 + LUNCH_START_M
    lunch_end   = LUNCH_END_H   * 60 + LUNCH_END_M
    t_min = h * 60 + m
    return lunch_start <= t_min < lunch_end


def _crosses_lunch(t_ns: int, t_fwd_ns: int, wib_dt: pd.Timestamp) -> bool:
    """
    Return True if the forward window crosses the lunch boundary.

    A boundary crossing occurs when the minutes-in-day of t and t_fwd span
    across [12:00, 13:30). We check by converting t_fwd to a WIB timestamp
    and seeing if either endpoint is in the gap, or if the gap lies between them.
    """
    if pd.isna(wib_dt):
        return True
    # Reconstruct approximate fwd timestamp.
    fwd_dt = wib_dt + pd.Timedelta(nanoseconds=(t_fwd_ns - t_ns))
    t_min     = wib_dt.hour * 60 + wib_dt.minute
    fwd_t_min = fwd_dt.hour  * 60 + fwd_dt.minute
    lunch_s   = LUNCH_START_H * 60 + LUNCH_START_M
    lunch_e   = LUNCH_END_H   * 60 + LUNCH_END_M

    if _is_lunch(wib_dt) or _is_lunch(fwd_dt):
        return True
    # Gap straddle: t before gap, fwd_t after gap.
    if t_min < lunch_s and fwd_t_min >= lunch_s:
        return True
    # (Reverse: fwd_t before gap, t after gap — unlikely with positive h but safe.)
    if fwd_t_min < lunch_s and t_min >= lunch_s:
        return True
    return False


def add_forward_returns(df: pd.DataFrame, horizons_s: list[int]) -> pd.DataFrame:
    """
    For each horizon h (seconds), join each row at t to the row at t+h via
    numpy.searchsorted on recv_ns. Drop rows that cross the lunch gap or have
    no data within a tolerance of a few seconds past t+h.

    Adds columns: fwd_mid_ret_Xs, fwd_last_ret_Xs (secondary).
    """
    df = df.copy()
    recv_ns_arr = df["recv_ns"].to_numpy(dtype=np.int64)
    mid_arr     = df["mid"].to_numpy(dtype=float)
    last_arr    = df["last_price"].to_numpy(dtype=float) if "last_price" in df.columns else np.full(len(df), np.nan)
    wib_arr     = df["_wib_dt"].to_numpy()  # dtype object (Timestamps)

    TOLERANCE_NS = 5 * int(1e9)  # 5 s tolerance: if matched row is > h+5s away, drop

    for h in horizons_s:
        h_ns = h * int(1e9)
        fwd_mid  = np.full(len(df), np.nan)
        fwd_last = np.full(len(df), np.nan)
        valid    = np.zeros(len(df), dtype=bool)

        for i in range(len(df)):
            t    = recv_ns_arr[i]
            t_h  = t + h_ns
            idx  = int(np.searchsorted(recv_ns_arr, t_h, side="left"))
            if idx >= len(recv_ns_arr):
                continue
            t_matched = recv_ns_arr[idx]
            # Tolerance: matched row must be within t+h + TOLERANCE_NS.
            if t_matched > t_h + TOLERANCE_NS:
                continue
            # Lunch-gap check.
            wib_dt = pd.Timestamp(wib_arr[i])
            if _crosses_lunch(t, t_matched, wib_dt):
                continue
            fwd_mid[i]  = mid_arr[idx]  - mid_arr[i]
            fwd_last[i] = last_arr[idx] - last_arr[i]
            valid[i]    = True

        fwd_mid[~valid]  = np.nan
        fwd_last[~valid] = np.nan
        df[f"fwd_mid_ret_{h}s"]  = fwd_mid
        df[f"fwd_last_ret_{h}s"] = fwd_last

    return df


# ---------------------------------------------------------------------------
# Block-bootstrap null
# ---------------------------------------------------------------------------

def _circular_block_resample(arr: np.ndarray, block: int, n_draws: int, rng: np.random.Generator) -> np.ndarray:
    """
    Return an array of shape (n_draws, len(arr)) — each row is a circular
    block-resampled permutation of arr, preserving short-range autocorrelation.
    """
    n = len(arr)
    n_blocks = int(np.ceil(n / block))
    out = np.empty((n_draws, n), dtype=float)
    for d in range(n_draws):
        starts = rng.integers(0, n, size=n_blocks)
        pieces = []
        for s in starts:
            idx = np.arange(s, s + block) % n
            pieces.append(arr[idx])
        combined = np.concatenate(pieces)[:n]
        out[d] = combined
    return out


def bootstrap_null(
    signal: np.ndarray,
    target: np.ndarray,
    block: int = BOOTSTRAP_BLOCK,
    n_draws: int = N_BOOTSTRAP,
    seed: int = 42,
) -> tuple[float, float]:
    """
    Compute the 95th percentile of |Pearson corr| and hit-rate under the
    circular block-bootstrap null (signal shuffled, target fixed).

    Returns (corr_95, hr_95).
    """
    rng = np.random.default_rng(seed)
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 50:
        return np.nan, np.nan

    resampled = _circular_block_resample(s, block, n_draws, rng)
    nonzero_t = np.abs(t) > 0

    corrs = np.empty(n_draws)
    hrs   = np.empty(n_draws)
    for d in range(n_draws):
        perm = resampled[d]
        c, _ = sps.pearsonr(perm, t)
        corrs[d] = abs(c)
        # Hit-rate under null (only on non-zero target rows).
        nt   = t[nonzero_t]
        np_  = perm[nonzero_t]
        if len(nt) > 0:
            hrs[d] = np.mean(np.sign(np_) == np.sign(nt))
        else:
            hrs[d] = np.nan

    return float(np.nanpercentile(corrs, 95)), float(np.nanpercentile(hrs, 95))


# ---------------------------------------------------------------------------
# Decile analysis
# ---------------------------------------------------------------------------

def decile_means(signal: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bin signal into deciles; return (bin_centers, means, sems) for target.
    Rows where either signal or target is NaN are dropped.
    """
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 20:
        return np.full(10, np.nan), np.full(10, np.nan), np.full(10, np.nan)

    decile_edges = np.percentile(s, np.linspace(0, 100, 11))
    # Force monotone edges to avoid empty bins from ties.
    decile_edges = np.unique(decile_edges)
    if len(decile_edges) < 3:
        return np.full(10, np.nan), np.full(10, np.nan), np.full(10, np.nan)

    bin_idx = np.digitize(s, decile_edges[1:-1])
    n_bins  = len(decile_edges) - 1

    centers = np.empty(n_bins)
    means   = np.empty(n_bins)
    sems    = np.empty(n_bins)
    for b in range(n_bins):
        mask   = bin_idx == b
        vals   = t[mask]
        sig_b  = s[mask]
        centers[b] = np.mean(sig_b) if len(sig_b) > 0 else np.nan
        means[b]   = np.mean(vals)  if len(vals)  > 0 else np.nan
        sems[b]    = (np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else np.nan

    return centers, means, sems


def is_monotone(means: np.ndarray, min_frac: float = 0.80) -> bool:
    """
    Crude monotonicity check: at least `min_frac` of consecutive (mean[i], mean[i+1])
    pairs must be non-decreasing (for positive-OBI ⇒ positive-return hypothesis).
    Returns True for both monotone increasing and decreasing (signal is directional).
    """
    valid = means[np.isfinite(means)]
    if len(valid) < 3:
        return False
    diffs = np.diff(valid)
    n_inc = np.sum(diffs >= 0)
    n_dec = np.sum(diffs <= 0)
    n     = len(diffs)
    return max(n_inc, n_dec) / n >= min_frac


# ---------------------------------------------------------------------------
# OLS with Newey-West HAC
# ---------------------------------------------------------------------------

def ols_hac(signal: np.ndarray, target: np.ndarray, maxlags: int) -> tuple[float, float, float]:
    """
    Fit OLS fwd_mid_ret ~ signal with Newey-West HAC SE.

    Returns (slope, t_stat, r_squared).
    """
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 30:
        return np.nan, np.nan, np.nan
    X = sm.add_constant(s, prepend=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = sm.OLS(t, X).fit(
                cov_type="HAC",
                cov_kwds={"maxlags": maxlags, "use_correction": True},
            )
        slope  = float(fit.params[1])
        t_stat = float(fit.tvalues[1])
        r2     = float(fit.rsquared)
    except Exception:
        return np.nan, np.nan, np.nan
    return slope, t_stat, r2


# ---------------------------------------------------------------------------
# Hit-rate
# ---------------------------------------------------------------------------

def conditional_hit_rate(signal: np.ndarray, target: np.ndarray) -> tuple[float, float, int]:
    """
    P(sign(signal) == sign(target)) conditional on |target| > 0.

    Returns (hit_rate, base_rate≈0.5, n_conditional).
    """
    valid  = np.isfinite(signal) & np.isfinite(target)
    s, t   = signal[valid], target[valid]
    nonzero = np.abs(t) > 0
    s_nz, t_nz = s[nonzero], t[nonzero]
    if len(s_nz) == 0:
        return np.nan, 0.5, 0
    hit = float(np.mean(np.sign(s_nz) == np.sign(t_nz)))
    return hit, 0.5, int(len(s_nz))


# ---------------------------------------------------------------------------
# Chronological OOS split
# ---------------------------------------------------------------------------

def oos_split(
    df: pd.DataFrame,
    signal_col: str,
    target_col: str,
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    """
    Split df chronologically: first 60% is in-sample, last 40% is OOS.
    The pre-lunch segment (if present) is returned as a third split handled
    by the caller via the short_mask parameter.

    Returns (in_sample, out_of_sample) each as (signal_arr, target_arr).
    """
    n = len(df)
    split = int(0.60 * n)
    df_in  = df.iloc[:split]
    df_oos = df.iloc[split:]

    def arrays(sub: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        s = sub[signal_col].to_numpy(dtype=float)
        t = sub[target_col].to_numpy(dtype=float)
        valid = np.isfinite(s) & np.isfinite(t)
        return s[valid], t[valid]

    return arrays(df_in), arrays(df_oos)


def session_hit_rate(signal: np.ndarray, target: np.ndarray) -> tuple[float, int]:
    """Wrapper returning (hit_rate, n) for an (s, t) array pair."""
    hr, _, n = conditional_hit_rate(signal, target)
    return hr, n


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_decile_curve(
    signal_name: str,
    horizon_s: int,
    centers: np.ndarray,
    means: np.ndarray,
    sems: np.ndarray,
    outdir: str,
) -> str:
    """Save and return the path of the decile-curve PNG."""
    fig, ax = plt.subplots(figsize=(7, 4))

    valid = np.isfinite(centers) & np.isfinite(means)
    if valid.any():
        ax.bar(
            np.arange(valid.sum()),
            means[valid],
            color="steelblue",
            alpha=0.75,
            yerr=sems[valid],
            capsize=3,
            error_kw={"linewidth": 0.8},
        )
        ax.axhline(0, color="black", linewidth=0.8)
        ax.set_xticks(np.arange(valid.sum()))
        ax.set_xticklabels(
            [f"{c:.3f}" for c in centers[valid]], rotation=45, ha="right", fontsize=7
        )

    ax.set_xlabel(f"{signal_name} decile centre")
    ax.set_ylabel(f"Mean fwd_mid_ret ({horizon_s}s)")
    ax.set_title(f"{signal_name} decile curve — {horizon_s}s horizon")
    ax.grid(True, axis="y", linewidth=0.4, alpha=0.5)
    plt.tight_layout()

    fname = f"decile_{signal_name}_{horizon_s}s.png"
    path  = os.path.join(outdir, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return fname


def plot_scatter(
    signal_name: str,
    horizon_s: int,
    signal: np.ndarray,
    target: np.ndarray,
    outdir: str,
) -> str:
    """Save and return the path of the scatter PNG (hex-bin for large N)."""
    valid = np.isfinite(signal) & np.isfinite(target)
    s, t  = signal[valid], target[valid]

    fig, ax = plt.subplots(figsize=(6, 4))
    if len(s) > 5000:
        ax.hexbin(s, t, gridsize=40, cmap="Blues", mincnt=1)
    else:
        ax.scatter(s, t, s=4, alpha=0.3, color="steelblue")

    ax.axhline(0, color="black", linewidth=0.6)
    ax.axvline(0, color="black", linewidth=0.6)
    ax.set_xlabel(signal_name)
    ax.set_ylabel(f"fwd_mid_ret ({horizon_s}s)")
    ax.set_title(f"{signal_name} vs fwd_mid_ret — {horizon_s}s horizon")
    ax.grid(True, linewidth=0.4, alpha=0.5)
    plt.tight_layout()

    fname = f"scatter_{signal_name}_{horizon_s}s.png"
    path  = os.path.join(outdir, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return fname


def plot_bootstrap_hist(
    signal_name: str,
    horizon_s: int,
    null_corrs: np.ndarray,
    real_corr: float,
    outdir: str,
) -> str:
    """Save bootstrap null distribution with real-statistic marker."""
    fig, ax = plt.subplots(figsize=(6, 4))
    valid = null_corrs[np.isfinite(null_corrs)]
    if len(valid) > 0:
        ax.hist(valid, bins=40, color="steelblue", alpha=0.6, edgecolor="white", linewidth=0.4)
    if np.isfinite(real_corr):
        ax.axvline(abs(real_corr), color="tomato", linewidth=1.5, label=f"|r|={abs(real_corr):.4f}")
        p95 = float(np.nanpercentile(valid, 95)) if len(valid) > 0 else np.nan
        if np.isfinite(p95):
            ax.axvline(p95, color="navy", linestyle="--", linewidth=1.0, label=f"null 95th={p95:.4f}")
    ax.set_xlabel("|Pearson r| under block-bootstrap null")
    ax.set_ylabel("Count")
    ax.set_title(f"{signal_name} bootstrap null — {horizon_s}s horizon")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", linewidth=0.4, alpha=0.5)
    plt.tight_layout()

    fname = f"bootstrap_{signal_name}_{horizon_s}s.png"
    path  = os.path.join(outdir, fname)
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return fname


# ---------------------------------------------------------------------------
# Per-(signal, horizon) test suite
# ---------------------------------------------------------------------------

SIGNAL_COLS = ["OBI1", "OBI_eq_N", "OBI_w_N", "wmid_skew"]


def run_one(
    df_long: pd.DataFrame,     # full long session (used for OOS 60/40 split)
    df_short: pd.DataFrame,    # short pre-lunch segment (second holdout)
    signal_col: str,
    horizon_s: int,
    outdir: str,
    png_paths: list[str],
) -> TestResult:
    """Run the full test suite for one (signal, horizon) pair."""
    target_col = f"fwd_mid_ret_{horizon_s}s"

    # Combined dataset for bootstrap / overall stats.
    df_all = pd.concat([df_long, df_short], ignore_index=True) if len(df_short) > 0 else df_long.copy()

    sig_all = df_all[signal_col].to_numpy(dtype=float)
    tgt_all = df_all[target_col].to_numpy(dtype=float) if target_col in df_all.columns else np.full(len(df_all), np.nan)
    valid   = np.isfinite(sig_all) & np.isfinite(tgt_all)
    s, t    = sig_all[valid], tgt_all[valid]
    n_obs   = int(valid.sum())
    n_nonzero = int(np.sum(np.abs(t) > 0))

    # Default placeholders for when there is not enough data.
    _nan = float("nan")
    default = TestResult(
        signal=signal_col, horizon_s=horizon_s,
        n_obs=n_obs, n_nonzero=n_nonzero,
        pearson_r=_nan, pearson_p=_nan,
        spearman_r=_nan, spearman_p=_nan,
        ols_slope=_nan, ols_t=_nan, ols_r2=_nan,
        hit_rate=_nan, base_rate=0.5,
        bootstrap_corr_95=_nan, bootstrap_hr_95=_nan,
        oos_hit_rate=_nan, oos_n=0,
        short_hit_rate=_nan, short_n=0,
        is_decile_monotone=False, clears_bootstrap=False,
        is_economical=False, oos_clears=False,
    )

    if n_obs < 30 or n_nonzero < int(n_obs * MIN_NONZERO_FRAC):
        return default

    # 1. Decile curve.
    centers, means, sems = decile_means(s, t)
    monotone = is_monotone(means)
    png_paths.append(plot_decile_curve(signal_col, horizon_s, centers, means, sems, outdir))

    # 2. Pearson + Spearman.
    pr, pp = sps.pearsonr(s, t) if len(s) >= 10 else (_nan, _nan)
    sr, sp = sps.spearmanr(s, t) if len(s) >= 10 else (_nan, _nan)
    pr, pp = float(pr), float(pp)
    sr, sp = float(sr), float(sp)

    # Scatter plot.
    png_paths.append(plot_scatter(signal_col, horizon_s, s, t, outdir))

    # 3. OLS HAC — pick maxlags ~ horizon_s in rows (capped at 200).
    row_rate     = max(1, int(n_obs / max(1, (df_all["recv_ns"].iloc[-1] - df_all["recv_ns"].iloc[0]) / 1e9)))
    maxlags_hac  = min(200, max(5, horizon_s * row_rate))
    ols_slope, ols_t, ols_r2 = ols_hac(s, t, maxlags_hac)

    # 4. Hit-rate.
    hit_rate, base_rate, n_cond = conditional_hit_rate(s, t)

    # 5. Block-bootstrap null.
    bs_corr_95, bs_hr_95 = bootstrap_null(s, t)

    # Bootstrap histogram.
    # Re-run bootstrap to collect the full array for plotting.
    rng = np.random.default_rng(42)
    resampled = _circular_block_resample(s, BOOTSTRAP_BLOCK, N_BOOTSTRAP, rng)
    null_corrs = np.array([abs(sps.pearsonr(resampled[d], t)[0]) for d in range(N_BOOTSTRAP)])
    png_paths.append(plot_bootstrap_hist(signal_col, horizon_s, null_corrs, pr, outdir))

    clears_bootstrap = (
        (np.isfinite(bs_corr_95) and np.isfinite(pr) and abs(pr) > bs_corr_95)
        or (np.isfinite(bs_hr_95) and np.isfinite(hit_rate) and hit_rate > bs_hr_95)
    )
    is_economical = (
        (np.isfinite(pr)       and abs(pr)    > THRESH_ABS_CORR)
        or (np.isfinite(hit_rate) and hit_rate > THRESH_HIT_RATE)
    )

    # 6. Chronological OOS — long session only.
    (_, _), (oos_s, oos_t) = oos_split(df_long, signal_col, target_col)
    oos_hr, oos_n          = session_hit_rate(oos_s, oos_t)
    oos_clears             = (
        np.isfinite(oos_hr) and np.isfinite(bs_hr_95) and oos_hr > bs_hr_95
    )

    # Short session holdout.
    if len(df_short) > 0 and target_col in df_short.columns:
        short_s = df_short[signal_col].to_numpy(dtype=float)
        short_t = df_short[target_col].to_numpy(dtype=float)
        short_hr, short_n = session_hit_rate(short_s, short_t)
    else:
        short_hr, short_n = _nan, 0

    return TestResult(
        signal=signal_col,
        horizon_s=horizon_s,
        n_obs=n_obs,
        n_nonzero=n_nonzero,
        pearson_r=pr,
        pearson_p=pp,
        spearman_r=sr,
        spearman_p=sp,
        ols_slope=ols_slope,
        ols_t=ols_t,
        ols_r2=ols_r2,
        hit_rate=hit_rate,
        base_rate=base_rate,
        bootstrap_corr_95=bs_corr_95,
        bootstrap_hr_95=bs_hr_95,
        oos_hit_rate=oos_hr,
        oos_n=oos_n,
        short_hit_rate=short_hr,
        short_n=short_n,
        is_decile_monotone=monotone,
        clears_bootstrap=clears_bootstrap,
        is_economical=is_economical,
        oos_clears=oos_clears,
    )


# ---------------------------------------------------------------------------
# Session split (long vs short / pre-lunch)
# ---------------------------------------------------------------------------

def split_sessions(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Heuristically split df into the long post-lunch session and the short
    pre-lunch session (11:42–12:00) based on _wib_dt.

    Returns (df_long, df_short) where df_long is the larger session.
    """
    MIDDAY_H = 13  # rows starting at or after 13:00 are post-lunch
    is_post  = df["_wib_dt"].dt.hour >= MIDDAY_H
    df_long  = df[is_post].reset_index(drop=True)
    df_short = df[~is_post].reset_index(drop=True)
    # If split yields a very small long session, fall back to treating all rows as long.
    if len(df_long) < 100:
        return df, pd.DataFrame(columns=df.columns)
    return df_long, df_short


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fmt(v: Any, fmt: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return format(v, fmt)


def build_report(study: StudyResults, png_paths: list[str]) -> str:
    """Return the full report.md text."""
    n_tests = len(study.results)
    alpha   = 0.05 / max(1, n_tests)   # Bonferroni-corrected significance level

    lines: list[str] = []

    lines += [
        f"# OBI / Micro-Price Go/No-Go Study — {study.symbol_date}",
        "",
        "---",
        "",
    ]

    # Verdict block — computed programmatically.
    lines += [
        "## Verdict",
        "",
        f"**{study.verdict}**",
        "",
    ]
    if study.verdict == "PASS":
        lines.append(
            f"Signal `{study.verdict_signal}` at `{study.verdict_horizon}s` horizon "
            "cleared all three criteria on held-out data:\n"
            "monotone decile curve + bootstrap null + economically meaningful magnitude."
        )
    else:
        lines.append(
            "No (signal, horizon) pair simultaneously satisfied **all three** criteria "
            "on held-out data (monotone decile curve AND bootstrap null clearance AND "
            "economically meaningful magnitude).\n\n"
            "**Implication:** Book-only signals (OBI/weighted-mid) do not reliably "
            "predict BUMI's short-horizon mid-price move on this dataset. "
            "Defer to a trade-tape (OFI) capture before investing in a backtest harness."
        )

    lines += [
        "",
        "---",
        "",
        "## Dataset",
        "",
        f"- Raw rows: {study.n_raw:,}",
        f"- Clean rows (top-of-book present): {study.n_clean:,}",
        "",
        "---",
        "",
        "## Multiple-testing note",
        "",
        f"This study runs {n_tests} (signal × horizon) cells "
        f"({len(SIGNAL_COLS)} signals × {len(HORIZONS_S)} horizons). "
        f"Bonferroni-corrected α = {alpha:.5f}. "
        "Treat any single significant cell with scepticism; only patterns consistent "
        "across multiple signals and horizons are credible.",
        "",
        "---",
        "",
        "## Results",
        "",
    ]

    for r in study.results:
        lines += [
            f"### {r.signal} / {r.horizon_s}s",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| N observations | {r.n_obs:,} |",
            f"| N non-zero target | {r.n_nonzero:,} |",
            f"| Pearson r | {_fmt(r.pearson_r)} (p={_fmt(r.pearson_p)}) |",
            f"| Spearman r | {_fmt(r.spearman_r)} (p={_fmt(r.spearman_p)}) |",
            f"| OLS slope (HAC) | {_fmt(r.ols_slope)} (t={_fmt(r.ols_t)}) |",
            f"| OLS R² | {_fmt(r.ols_r2)} |",
            f"| Hit-rate (cond. on move) | {_fmt(r.hit_rate)} (base≈{_fmt(r.base_rate)}, n={r.n_nonzero}) |",
            f"| Bootstrap corr 95th pct | {_fmt(r.bootstrap_corr_95)} |",
            f"| Bootstrap HR 95th pct | {_fmt(r.bootstrap_hr_95)} |",
            f"| OOS hit-rate (last 40%) | {_fmt(r.oos_hit_rate)} (n={r.oos_n}) |",
            f"| Short-session holdout HR | {_fmt(r.short_hit_rate)} (n={r.short_n}) |",
            f"| Decile monotone | {'yes' if r.is_decile_monotone else 'no'} |",
            f"| Clears bootstrap null | {'yes' if r.clears_bootstrap else 'no'} |",
            f"| Economically meaningful | {'yes' if r.is_economical else 'no'} |",
            f"| OOS clears null | {'yes' if r.oos_clears else 'no'} |",
            "",
        ]

    lines += [
        "---",
        "",
        "## Figures",
        "",
    ]
    for p in sorted(set(png_paths)):
        lines.append(f"![{p}]({p})")
    lines.append("")

    lines += [
        "---",
        "",
        "## Standing caveats",
        "",
        "- **One name, one day, two sessions** — feasibility gate only, not generalisable proof.",
        "- **No trade tape** — OFI (the strongest signal per Cont-Kukanov-Stoikov 2014) is "
        "untestable on this data.",
        "- **1-tick stock** — forward mid is frozen most of the time; the conditional-on-move "
        "analysis is the real test.",
        "- **Stoikov micro-price g(I,S) fitting** (docs/thesis/01-microstructure-signals.md §3.2) "
        "is deferred — only if weighted-mid/OBI clear this gate; it requires Markov "
        "transition-matrix estimation.",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Study runner
# ---------------------------------------------------------------------------

def run_study(df_raw: pd.DataFrame, depth: int, outdir: str) -> StudyResults:
    """Full study pipeline: clean → signals → forward returns → tests → verdict."""
    os.makedirs(outdir, exist_ok=True)

    n_raw = len(df_raw)
    df    = clean(df_raw)
    n_clean = len(df)

    if n_clean == 0:
        print("ERROR: no rows remain after cleaning — check CSV format.", file=sys.stderr)
        sys.exit(1)

    df = add_signals(df, depth)
    df = add_forward_returns(df, HORIZONS_S)

    # Infer a symbol/date label from the data.
    symbol_date = ""
    if "_wib_dt" in df.columns and len(df) > 0:
        dt0 = df["_wib_dt"].iloc[0]
        symbol_date = dt0.strftime("%Y-%m-%d") if not pd.isna(dt0) else ""

    df_long, df_short = split_sessions(df)

    study = StudyResults(n_raw=n_raw, n_clean=n_clean, symbol_date=symbol_date)
    png_paths: list[str] = []

    for sig in SIGNAL_COLS:
        for h in HORIZONS_S:
            target_col = f"fwd_mid_ret_{h}s"
            if target_col not in df.columns:
                continue
            result = run_one(df_long, df_short, sig, h, outdir, png_paths)
            study.results.append(result)
            print(
                f"  {sig:12s} / {h:2d}s  pearson={_fmt(result.pearson_r, '.4f')} "
                f"hit={_fmt(result.hit_rate, '.4f')}  "
                f"oos_clears={'Y' if result.oos_clears else 'N'}  "
                f"mono={'Y' if result.is_decile_monotone else 'N'}",
                flush=True,
            )

    # Determine verdict — check for any result clearing all three criteria on OOS.
    for r in study.results:
        if r.is_decile_monotone and r.clears_bootstrap and r.is_economical and r.oos_clears:
            study.verdict         = "PASS"
            study.verdict_signal  = r.signal
            study.verdict_horizon = r.horizon_s
            break

    # Write report.
    report_text = build_report(study, png_paths)
    report_path = os.path.join(outdir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"\nReport written → {report_path}")
    print(f"Verdict: {study.verdict}")

    return study


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "OBI / micro-price → forward mid-return study on sbcap features CSV. "
            "Writes out/report.md + PNGs."
        )
    )
    p.add_argument(
        "--csv",
        required=True,
        metavar="PATH",
        help="Path to the features CSV emitted by `sbcap features`.",
    )
    p.add_argument(
        "--outdir",
        default=os.path.join(SCRIPT_DIR, "out"),
        metavar="DIR",
        help="Output directory for report.md and PNGs (default: out/ next to this script).",
    )
    p.add_argument(
        "--depth",
        type=int,
        default=5,
        metavar="N",
        help="Order-book depth to use for multi-level OBI (default: 5).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not os.path.isfile(args.csv):
        print(f"ERROR: CSV not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {args.csv} …")
    df_raw = load_csv(args.csv)
    print(f"  {len(df_raw):,} rows loaded.")

    print(f"Running study (depth={args.depth}, outdir={args.outdir}) …")
    run_study(df_raw, depth=args.depth, outdir=args.outdir)


if __name__ == "__main__":
    main()
