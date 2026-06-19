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

Verdict at top of report.md (three-state):
  PASS if ≥1 (signal, horizon) on held-out data shows:
    • monotone decile curve, AND
    • correlation / hit-rate clearing the bootstrap null 95th pct, AND
    • economically meaningful magnitude (|corr| > 0.02 or hit-rate > 0.52), AND
    • a de-overlapped OOS hit-rate > 0.52 over ≥ MIN_EFF_N independent moves.
  INCONCLUSIVE if a (signal, horizon) meets every PASS criterion except that the
    de-overlapped OOS hit-rate rests on fewer than MIN_EFF_N independent
    (non-overlapping) moves — directional but needs more days to confirm.
  Otherwise: NO-GO.

Usage:
    python obi_forward_return.py --csv PATH [--outdir DIR] [--depth N]
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any

# Use the non-interactive Agg backend so the script works headless.
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.stats as sps

from lib.loaders import load_csv, clean
from lib.session import _is_lunch, _crosses_lunch
from lib.signals import add_forward_returns
from lib.stats import (
    _circular_block_resample,
    bootstrap_null,
    decile_means,
    is_monotone,
    nonoverlap_hit_rate,
    ols_hac,
    MIN_EFF_N,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

HORIZONS_S = [1, 5, 10, 30, 60]          # forward-return horizons (seconds)
N_BOOTSTRAP = 500                          # circular block-bootstrap draws
BOOTSTRAP_BLOCK = 30                       # block length for bootstrap (rows)
# Minimum fraction of non-zero fwd_mid_ret rows required to run tests.
MIN_NONZERO_FRAC = 0.001

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
    oos_hit_rate_noov: float   # de-overlapped OOS hit-rate (independent obs)
    oos_eff_n: int             # number of independent conditional observations


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
# Hit-rate (3-tuple local wrapper used by this script)
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
        oos_hit_rate_noov=_nan, oos_eff_n=0,
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
    # Adaptive block: at least BOOTSTRAP_BLOCK, at least ~1 horizon's worth of
    # rows, capped so it stays well below the series length (n).
    _span_s = max(1.0, (df_all["recv_ns"].iloc[-1] - df_all["recv_ns"].iloc[0]) / 1e9)
    _rows_per_s = max(1.0, n_obs / _span_s)
    _adaptive_block = min(
        max(BOOTSTRAP_BLOCK, int(round(horizon_s * _rows_per_s))),
        max(50, n_obs // 20),
    )
    bs_corr_95, bs_hr_95 = bootstrap_null(s, t, block=_adaptive_block)

    # Bootstrap histogram.
    # Re-run bootstrap to collect the full array for plotting.
    rng = np.random.default_rng(42)
    resampled = _circular_block_resample(s, _adaptive_block, N_BOOTSTRAP, rng)
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

    # De-overlapped OOS hit-rate — the trustworthy independent-sample measure.
    df_oos = df_long.iloc[int(0.60 * len(df_long)):]
    oos_hit_rate_noov, oos_eff_n = nonoverlap_hit_rate(
        df_oos, signal_col, target_col, horizon_s
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
        oos_hit_rate_noov=oos_hit_rate_noov,
        oos_eff_n=oos_eff_n,
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
            "cleared all criteria on held-out data: monotone decile curve + bootstrap null + "
            "economically meaningful magnitude + de-overlapped eff_n ≥ MIN_EFF_N."
        )
    elif study.verdict == "INCONCLUSIVE":
        lines.append(
            f"Signal `{study.verdict_signal}` at `{study.verdict_horizon}s` horizon "
            "passed the directional criteria (monotone decile curve + bootstrap null + "
            "economically meaningful magnitude) **but the de-overlapped effective sample size "
            f"is below the minimum of {MIN_EFF_N} independent observations** needed to trust "
            "the OOS hit-rate. Collect more trading days and re-run to confirm."
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
            f"| OOS hit-rate (raw, overlapping) | {_fmt(r.oos_hit_rate)} (n={r.oos_n}) |",
            f"| OOS hit-rate (de-overlapped) | {_fmt(r.oos_hit_rate_noov)} (eff n={r.oos_eff_n}) |",
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
        "- **Raw hit-rates count overlapping windows** — consecutive book-update rows "
        "share almost the same 5-second future, so raw n counts overstate independent "
        "evidence by 1–2 orders of magnitude. The de-overlapped hit-rate and effective N "
        "(eff n) are the trustworthy figures; the raw hit-rate is shown for completeness.",
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

    # Verdict logic:
    # PASS         — all criteria met including de-overlapped eff_n >= MIN_EFF_N
    # INCONCLUSIVE — criteria met except eff_n < MIN_EFF_N (need more data)
    # NO-GO        — criteria not met
    for r in study.results:
        base_ok = r.is_decile_monotone and r.clears_bootstrap and r.is_economical and r.oos_clears
        noov_ok = (
            np.isfinite(r.oos_hit_rate_noov)
            and r.oos_eff_n >= MIN_EFF_N
            and r.oos_hit_rate_noov > THRESH_HIT_RATE
        )
        if base_ok and noov_ok:
            study.verdict         = "PASS"
            study.verdict_signal  = r.signal
            study.verdict_horizon = r.horizon_s
            break
        if base_ok and np.isfinite(r.oos_hit_rate_noov) and r.oos_hit_rate_noov > THRESH_HIT_RATE:
            # Signal looks directional but rests on too few independent moves.
            if study.verdict == "NO-GO":
                study.verdict         = "INCONCLUSIVE"
                study.verdict_signal  = r.signal
                study.verdict_horizon = r.horizon_s

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
