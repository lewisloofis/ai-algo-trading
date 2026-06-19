"""
ofi_obi_compare.py — OFI vs OBI comparison study for a single symbol/day.

Reads the features CSV emitted by `sbcap features`, computes Order Flow
Imbalance (OFI) from the delta columns and OBI1 from top-of-book, then runs
the full go/no-go test suite and a joint model to measure incremental R².

PASS criterion: OFI's incremental R² over OBI1 ≥ 5 pp on held-out OOS data.
Otherwise: OFI_NO_GAIN.

Usage:
    python ofi_obi_compare.py --csv PATH [--outdir DIR] [--depth N]
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from typing import Any

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm

from lib.loaders import load_csv, clean
from lib.session import filter_continuous_session
from lib.signals import add_forward_returns, add_obi1
from lib.stats import (
    bootstrap_null,
    conditional_hit_rate as _conditional_hit_rate_2tuple,
    decile_means,
    is_monotone,
    ols_hac,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

HORIZONS_S = [1, 5, 10, 30, 60]
N_BOOTSTRAP = 500
BOOTSTRAP_BLOCK = 30

OFI_INCREMENTAL_THRESHOLD = 0.05  # 5 percentage points


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def conditional_hit_rate(
    signal: np.ndarray, target: np.ndarray
) -> tuple[float, float, int]:
    """3-tuple wrapper around lib.stats.conditional_hit_rate."""
    hr, n = _conditional_hit_rate_2tuple(signal, target)
    return hr, 0.5, n


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def compute_ofi(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    db = df["delta_bid_vol"].to_numpy(dtype=float)
    da = df["delta_ask_vol"].to_numpy(dtype=float)
    df["ofi"] = (
        np.clip(db, 0, None)
        - np.abs(np.clip(db, None, 0))
        - np.clip(da, 0, None)
        + np.abs(np.clip(da, None, 0))
    )
    return df


def add_rolling_ofi(df: pd.DataFrame, windows_s: list[int]) -> pd.DataFrame:
    """
    For each window width, sum ofi values whose recv_ns falls in [t-window_ns, t].
    Uses searchsorted for efficiency.
    """
    df = df.copy()
    recv_ns = df["recv_ns"].to_numpy(dtype=np.int64)
    ofi_arr = df["ofi"].to_numpy(dtype=float)

    for w in windows_s:
        w_ns = w * int(1e9)
        result = np.empty(len(df), dtype=float)
        for i in range(len(df)):
            t = recv_ns[i]
            lo = int(np.searchsorted(recv_ns, t - w_ns, side="left"))
            # sum ofi values in [t-w_ns, t], inclusive of current row
            chunk = ofi_arr[lo : i + 1]
            valid = chunk[np.isfinite(chunk)]
            result[i] = float(np.sum(valid)) if len(valid) > 0 else np.nan
        df[f"ofi_{w}s"] = result

    return df


# ---------------------------------------------------------------------------
# OOS split helper
# ---------------------------------------------------------------------------

def oos_arrays(
    df: pd.DataFrame, signal_col: str, target_col: str
) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    n = len(df)
    split = int(0.60 * n)
    df_in = df.iloc[:split]
    df_oos = df.iloc[split:]

    def arrays(sub: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        s = sub[signal_col].to_numpy(dtype=float)
        t = sub[target_col].to_numpy(dtype=float) if target_col in sub.columns else np.full(len(sub), np.nan)
        valid = np.isfinite(s) & np.isfinite(t)
        return s[valid], t[valid]

    return arrays(df_in), arrays(df_oos)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_scatter_png(
    signal_name: str,
    horizon_s: int,
    signal: np.ndarray,
    target: np.ndarray,
    outdir: str,
) -> str:
    valid = np.isfinite(signal) & np.isfinite(target)
    s, t = signal[valid], target[valid]
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
    fig.savefig(os.path.join(outdir, fname), dpi=110)
    plt.close(fig)
    return fname


def plot_decile_png(
    signal_name: str,
    horizon_s: int,
    centers: np.ndarray,
    means: np.ndarray,
    sems: np.ndarray,
    outdir: str,
) -> str:
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
    fig.savefig(os.path.join(outdir, fname), dpi=110)
    plt.close(fig)
    return fname


# ---------------------------------------------------------------------------
# Per-(signal, horizon) metrics
# ---------------------------------------------------------------------------

def _fmt(v: Any, fmt: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return format(v, fmt)


def run_signal_horizon(
    df: pd.DataFrame,
    signal_col: str,
    horizon_s: int,
    outdir: str,
    png_paths: list[str],
) -> dict[str, Any]:
    target_col = f"fwd_mid_ret_{horizon_s}s"
    if target_col not in df.columns:
        return {}

    sig = df[signal_col].to_numpy(dtype=float)
    tgt = df[target_col].to_numpy(dtype=float)
    valid = np.isfinite(sig) & np.isfinite(tgt)
    s, t = sig[valid], tgt[valid]
    n_obs = int(valid.sum())

    if n_obs < 30:
        return {"signal": signal_col, "horizon_s": horizon_s, "n_obs": n_obs}

    row_rate = max(1, int(n_obs / max(1, (df["recv_ns"].iloc[-1] - df["recv_ns"].iloc[0]) / 1e9)))
    maxlags = min(200, max(5, horizon_s * row_rate))

    _, _, r2 = ols_hac(s, t, maxlags)
    hr, _, n_cond = conditional_hit_rate(s, t)
    bs_corr_95, bs_hr_95 = bootstrap_null(s, t)

    (_, _), (oos_s, oos_t) = oos_arrays(df, signal_col, target_col)
    oos_hr = float(np.mean(np.sign(oos_s[np.abs(oos_t) > 0]) == np.sign(oos_t[np.abs(oos_t) > 0]))) if len(oos_t) > 0 and np.any(np.abs(oos_t) > 0) else np.nan
    oos_n = int(np.sum(np.abs(oos_t) > 0)) if len(oos_t) > 0 else 0

    centers, means, sems = decile_means(s, t)
    png_paths.append(plot_decile_png(signal_col, horizon_s, centers, means, sems, outdir))
    png_paths.append(plot_scatter_png(signal_col, horizon_s, s, t, outdir))

    return {
        "signal": signal_col,
        "horizon_s": horizon_s,
        "n_obs": n_obs,
        "r2": r2,
        "hit_rate": hr,
        "n_cond": n_cond,
        "bs_hr_95": bs_hr_95,
        "oos_hit_rate": oos_hr,
        "oos_n": oos_n,
    }


# ---------------------------------------------------------------------------
# Joint model: OBI1 + ofi_5s
# ---------------------------------------------------------------------------

def joint_model_r2(
    df: pd.DataFrame, target_col: str, maxlags: int
) -> dict[str, float]:
    if target_col not in df.columns:
        return {}

    tgt = df[target_col].to_numpy(dtype=float)
    obi = df["OBI1"].to_numpy(dtype=float)
    ofi = df["ofi_5s"].to_numpy(dtype=float)

    valid = np.isfinite(tgt) & np.isfinite(obi) & np.isfinite(ofi)
    t, ob, of = tgt[valid], obi[valid], ofi[valid]
    if len(t) < 30:
        return {}

    def _r2(X_cols: np.ndarray) -> float:
        X = sm.add_constant(X_cols, prepend=True)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = sm.OLS(t, X).fit(
                    cov_type="HAC",
                    cov_kwds={"maxlags": maxlags, "use_correction": True},
                )
            return float(fit.rsquared)
        except Exception:
            return np.nan

    r2_obi = _r2(ob.reshape(-1, 1))
    r2_ofi = _r2(of.reshape(-1, 1))
    r2_joint = _r2(np.column_stack([ob, of]))

    # OOS incremental R²
    n = len(t)
    split = int(0.60 * n)
    t_oos, ob_oos, of_oos = t[split:], ob[split:], of[split:]
    oos_r2_obi = np.nan
    oos_r2_joint = np.nan
    if len(t_oos) >= 30:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit_obi = sm.OLS(t_oos, sm.add_constant(ob_oos)).fit(
                    cov_type="HAC", cov_kwds={"maxlags": maxlags, "use_correction": True}
                )
                oos_r2_obi = float(fit_obi.rsquared)
                fit_joint = sm.OLS(
                    t_oos, sm.add_constant(np.column_stack([ob_oos, of_oos]))
                ).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags, "use_correction": True})
                oos_r2_joint = float(fit_joint.rsquared)
        except Exception:
            pass

    oos_incremental = (oos_r2_joint - oos_r2_obi) if np.isfinite(oos_r2_joint) and np.isfinite(oos_r2_obi) else np.nan

    return {
        "r2_obi1_only": r2_obi,
        "r2_ofi5s_only": r2_ofi,
        "r2_joint": r2_joint,
        "incremental_r2_ofi": r2_joint - r2_obi if np.isfinite(r2_joint) and np.isfinite(r2_obi) else np.nan,
        "oos_r2_obi1_only": oos_r2_obi,
        "oos_r2_joint": oos_r2_joint,
        "oos_incremental_r2_ofi": oos_incremental,
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_report(
    rows: list[dict[str, Any]],
    joint_results: dict[str, dict[str, float]],
    n_raw: int,
    n_clean: int,
    verdict: str,
    png_paths: list[str],
    symbol_date: str,
) -> str:
    n_tests = len(rows)
    bonf_alpha = 0.05 / max(1, n_tests)

    lines: list[str] = []
    lines += [
        f"# OFI vs OBI Comparison Study — {symbol_date}",
        "",
        "---",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
    ]

    if verdict == "PASS":
        lines.append(
            "OFI's incremental R² over OBI1 on held-out OOS data exceeded the "
            f"{OFI_INCREMENTAL_THRESHOLD*100:.0f} pp threshold. OFI adds predictive power "
            "beyond top-of-book imbalance alone."
        )
    else:
        lines.append(
            f"OFI's incremental R² over OBI1 did not reach the {OFI_INCREMENTAL_THRESHOLD*100:.0f} pp "
            "threshold on the held-out OOS split. OFI offers no reliable gain over OBI1 on this dataset."
        )

    lines += [
        "",
        "---",
        "",
        "## Dataset",
        "",
        f"- Raw rows: {n_raw:,}",
        f"- Clean rows (continuous session, delta columns present): {n_clean:,}",
        "",
        "---",
        "",
        "## Multiple-testing note",
        "",
        f"This study tests {n_tests} (signal × horizon) cells. "
        f"Bonferroni-corrected α = {bonf_alpha:.5f}. "
        "Treat any single significant cell with scepticism; only patterns consistent "
        "across multiple signals and horizons are credible.",
        "",
        "---",
        "",
        "## Per-(signal, horizon) results",
        "",
        "| signal | horizon | n_obs | R² | hit_rate | OOS hit_rate | OOS n | null_hr_95 |",
        "|--------|---------|-------|-----|----------|-------------|-------|------------|",
    ]

    for r in rows:
        if "r2" not in r:
            continue
        lines.append(
            f"| {r['signal']} | {r['horizon_s']}s | {r.get('n_obs', 0):,} "
            f"| {_fmt(r.get('r2'))} "
            f"| {_fmt(r.get('hit_rate'))} "
            f"| {_fmt(r.get('oos_hit_rate'))} "
            f"| {r.get('oos_n', 0)} "
            f"| {_fmt(r.get('bs_hr_95'))} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Joint model: OBI1 + ofi_5s",
        "",
        "| horizon | R²(OBI1) | R²(OFI5s) | R²(joint) | ΔR²(OFI) | OOS R²(OBI1) | OOS R²(joint) | OOS ΔR²(OFI) |",
        "|---------|----------|-----------|-----------|----------|-------------|--------------|-------------|",
    ]

    oos_incremental_values: list[float] = []
    for h, jr in joint_results.items():
        if not jr:
            continue
        oos_inc = jr.get("oos_incremental_r2_ofi", np.nan)
        if np.isfinite(oos_inc):
            oos_incremental_values.append(oos_inc)
        lines.append(
            f"| {h} "
            f"| {_fmt(jr.get('r2_obi1_only'))} "
            f"| {_fmt(jr.get('r2_ofi5s_only'))} "
            f"| {_fmt(jr.get('r2_joint'))} "
            f"| {_fmt(jr.get('incremental_r2_ofi'))} "
            f"| {_fmt(jr.get('oos_r2_obi1_only'))} "
            f"| {_fmt(jr.get('oos_r2_joint'))} "
            f"| {_fmt(jr.get('oos_incremental_r2_ofi'))} |"
        )

    lines += [
        "",
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
        "## Caveats",
        "",
        "- One name, one day — feasibility gate only.",
        "- OFI computed from delta columns which are NaN at reconnect boundaries; those rows are dropped.",
        "- Rolling OFI windows use all available prior trade flow, which may thin near session open.",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Study runner
# ---------------------------------------------------------------------------

def run_study(df_raw: pd.DataFrame, depth: int, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    n_raw = len(df_raw)

    df = clean(df_raw)
    df = add_obi1(df)

    df = df.dropna(subset=["delta_bid_vol", "delta_ask_vol"])
    df = filter_continuous_session(df)
    n_clean = len(df)

    if n_clean == 0:
        print("ERROR: no rows remain after filtering.", file=sys.stderr)
        sys.exit(1)

    df = compute_ofi(df)
    df = add_rolling_ofi(df, [1, 5, 10])
    df = add_forward_returns(df, HORIZONS_S)

    symbol_date = ""
    if len(df) > 0:
        dt0 = df["_wib_dt"].iloc[0]
        symbol_date = dt0.strftime("%Y-%m-%d") if not pd.isna(dt0) else ""

    signal_cols = ["OBI1", "ofi_1s", "ofi_5s", "ofi_10s"]
    rows: list[dict[str, Any]] = []
    png_paths: list[str] = []

    for sig in signal_cols:
        for h in HORIZONS_S:
            print(f"  {sig:12s} / {h:2d}s …", flush=True)
            result = run_signal_horizon(df, sig, h, outdir, png_paths)
            if result:
                rows.append(result)

    row_rate = max(1, int(n_clean / max(1, (df["recv_ns"].iloc[-1] - df["recv_ns"].iloc[0]) / 1e9)))
    joint_results: dict[str, dict[str, float]] = {}
    for h in HORIZONS_S:
        maxlags = min(200, max(5, h * row_rate))
        joint_results[f"{h}s"] = joint_model_r2(df, f"fwd_mid_ret_{h}s", maxlags)

    # PASS if OFI incremental OOS R² >= threshold at any horizon
    verdict = "OFI_NO_GAIN"
    for jr in joint_results.values():
        oos_inc = jr.get("oos_incremental_r2_ofi", np.nan)
        if np.isfinite(oos_inc) and oos_inc >= OFI_INCREMENTAL_THRESHOLD:
            verdict = "PASS"
            break

    report = build_report(rows, joint_results, n_raw, n_clean, verdict, png_paths, symbol_date)
    report_path = os.path.join(outdir, "ofi_obi_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nReport written → {report_path}")
    print(f"Verdict: {verdict}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OFI vs OBI comparison study. Writes out/ofi_obi_report.md + PNGs."
    )
    p.add_argument("--csv", required=True, metavar="PATH")
    p.add_argument(
        "--outdir",
        default=os.path.join(SCRIPT_DIR, "out"),
        metavar="DIR",
    )
    p.add_argument("--depth", type=int, default=5, metavar="N")
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
