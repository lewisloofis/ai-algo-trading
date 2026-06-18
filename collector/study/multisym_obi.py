"""
multisym_obi.py — OBI1 generalisation study across 6 JSE symbols.

Reads {SYM}_features.csv for each of: ANTM, BUMI, DEWA, INDY, ITMG, MDKA.
Runs the OBI1 forward-return study at 5s (primary), 1s and 10s (secondary)
for each symbol and tests whether OBI1 generalises across the set.

Generalisation verdict:
  STRONG         — all 6 pass
  WEAK           — ≥4 pass
  NO_GENERALISE  — <4 pass
  FLAG_REVERSAL  — any symbol has OOS hit-rate < 0.45

Usage:
    python multisym_obi.py --dir DIR [--outdir DIR2] [--depth N]
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from dataclasses import dataclass
from typing import Any

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.stats as sps
import statsmodels.api as sm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SYMBOLS = ["ANTM", "BUMI", "DEWA", "INDY", "ITMG", "MDKA"]
HORIZONS_S = [1, 5, 10, 30, 60]
PRIMARY_HORIZON = 5
N_BOOTSTRAP = 500
BOOTSTRAP_BLOCK = 30

LUNCH_START_H, LUNCH_START_M = 12, 0
LUNCH_END_H, LUNCH_END_M = 13, 30

SESSION_START_H, SESSION_START_M = 9, 0
SESSION_AM_END_H, SESSION_AM_END_M = 12, 0
SESSION_PM_START_H, SESSION_PM_START_M = 13, 30
SESSION_END_H, SESSION_END_M = 16, 15

PASS_OOS_HR_THRESHOLD = 0.55
REVERSAL_THRESHOLD = 0.45


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _is_lunch(dt: pd.Timestamp) -> bool:
    t_min = dt.hour * 60 + dt.minute
    return (LUNCH_START_H * 60 + LUNCH_START_M) <= t_min < (LUNCH_END_H * 60 + LUNCH_END_M)


def _crosses_lunch(t_ns: int, t_fwd_ns: int, wib_dt: pd.Timestamp) -> bool:
    if pd.isna(wib_dt):
        return True
    fwd_dt = wib_dt + pd.Timedelta(nanoseconds=(t_fwd_ns - t_ns))
    t_min = wib_dt.hour * 60 + wib_dt.minute
    fwd_t_min = fwd_dt.hour * 60 + fwd_dt.minute
    lunch_s = LUNCH_START_H * 60 + LUNCH_START_M

    if _is_lunch(wib_dt) or _is_lunch(fwd_dt):
        return True
    if t_min < lunch_s and fwd_t_min >= lunch_s:
        return True
    if fwd_t_min < lunch_s and t_min >= lunch_s:
        return True
    return False


def add_forward_returns(df: pd.DataFrame, horizons_s: list[int]) -> pd.DataFrame:
    df = df.copy()
    recv_ns_arr = df["recv_ns"].to_numpy(dtype=np.int64)
    mid_arr = df["mid"].to_numpy(dtype=float)
    wib_arr = df["_wib_dt"].to_numpy()
    TOLERANCE_NS = 5 * int(1e9)

    for h in horizons_s:
        h_ns = h * int(1e9)
        fwd_mid = np.full(len(df), np.nan)
        valid = np.zeros(len(df), dtype=bool)

        for i in range(len(df)):
            t = recv_ns_arr[i]
            t_h = t + h_ns
            idx = int(np.searchsorted(recv_ns_arr, t_h, side="left"))
            if idx >= len(recv_ns_arr):
                continue
            t_matched = recv_ns_arr[idx]
            if t_matched > t_h + TOLERANCE_NS:
                continue
            wib_dt = pd.Timestamp(wib_arr[i])
            if _crosses_lunch(t, t_matched, wib_dt):
                continue
            fwd_mid[i] = mid_arr[idx] - mid_arr[i]
            valid[i] = True

        fwd_mid[~valid] = np.nan
        df[f"fwd_mid_ret_{h}s"] = fwd_mid

    return df


def _circular_block_resample(
    arr: np.ndarray, block: int, n_draws: int, rng: np.random.Generator
) -> np.ndarray:
    n = len(arr)
    n_blocks = int(np.ceil(n / block))
    out = np.empty((n_draws, n), dtype=float)
    for d in range(n_draws):
        starts = rng.integers(0, n, size=n_blocks)
        pieces = [arr[np.arange(s, s + block) % n] for s in starts]
        out[d] = np.concatenate(pieces)[:n]
    return out


def bootstrap_null(
    signal: np.ndarray,
    target: np.ndarray,
    block: int = BOOTSTRAP_BLOCK,
    n_draws: int = N_BOOTSTRAP,
    seed: int = 42,
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 50:
        return np.nan, np.nan

    resampled = _circular_block_resample(s, block, n_draws, rng)
    nonzero_t = np.abs(t) > 0

    hrs = np.empty(n_draws)
    for d in range(n_draws):
        perm = resampled[d]
        nt = t[nonzero_t]
        np_ = perm[nonzero_t]
        hrs[d] = float(np.mean(np.sign(np_) == np.sign(nt))) if len(nt) > 0 else np.nan

    corrs = np.array([abs(sps.pearsonr(resampled[d], t)[0]) for d in range(n_draws)])
    return float(np.nanpercentile(corrs, 95)), float(np.nanpercentile(hrs, 95))


def conditional_hit_rate(
    signal: np.ndarray, target: np.ndarray
) -> tuple[float, int]:
    valid = np.isfinite(signal) & np.isfinite(target)
    s, t = signal[valid], target[valid]
    nonzero = np.abs(t) > 0
    s_nz, t_nz = s[nonzero], t[nonzero]
    if len(s_nz) == 0:
        return np.nan, 0
    return float(np.mean(np.sign(s_nz) == np.sign(t_nz))), int(len(s_nz))


def decile_means(
    signal: np.ndarray, target: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 20:
        return np.full(10, np.nan), np.full(10, np.nan), np.full(10, np.nan)
    edges = np.unique(np.percentile(s, np.linspace(0, 100, 11)))
    if len(edges) < 3:
        return np.full(10, np.nan), np.full(10, np.nan), np.full(10, np.nan)
    bin_idx = np.digitize(s, edges[1:-1])
    n_bins = len(edges) - 1
    centers = np.array([np.mean(s[bin_idx == b]) if np.any(bin_idx == b) else np.nan for b in range(n_bins)])
    means = np.array([np.mean(t[bin_idx == b]) if np.any(bin_idx == b) else np.nan for b in range(n_bins)])
    sems = np.array([
        (np.std(t[bin_idx == b], ddof=1) / np.sqrt(np.sum(bin_idx == b))) if np.sum(bin_idx == b) > 1 else np.nan
        for b in range(n_bins)
    ])
    return centers, means, sems


def is_monotone(means: np.ndarray, min_frac: float = 0.80) -> bool:
    valid = means[np.isfinite(means)]
    if len(valid) < 3:
        return False
    diffs = np.diff(valid)
    n_inc = np.sum(diffs >= 0)
    n_dec = np.sum(diffs <= 0)
    return max(n_inc, n_dec) / len(diffs) >= min_frac


# ---------------------------------------------------------------------------
# Data loading and cleaning
# ---------------------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    for col in [c for c in df.columns if c != "iso_wib"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("recv_ns").reset_index(drop=True)
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.dropna(subset=["bid_px_0", "ask_px_0", "bid_vol_0", "ask_vol_0", "_wib_dt"])
    df = df[(df["bid_px_0"] > 0) & (df["ask_px_0"] > 0)]
    df = df[(df["bid_vol_0"] > 0) & (df["ask_vol_0"] > 0)]
    return df.reset_index(drop=True)


def filter_continuous_session(df: pd.DataFrame) -> pd.DataFrame:
    t_min = df["_wib_dt"].dt.hour * 60 + df["_wib_dt"].dt.minute
    am_s = SESSION_START_H * 60 + SESSION_START_M
    am_e = SESSION_AM_END_H * 60 + SESSION_AM_END_M
    pm_s = SESSION_PM_START_H * 60 + SESSION_PM_START_M
    pm_e = SESSION_END_H * 60 + SESSION_END_M
    mask = ((t_min >= am_s) & (t_min < am_e)) | ((t_min >= pm_s) & (t_min <= pm_e))
    return df[mask].reset_index(drop=True)


def add_obi1(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    bvol0 = df["bid_vol_0"].to_numpy(dtype=float)
    avol0 = df["ask_vol_0"].to_numpy(dtype=float)
    bid0 = df["bid_px_0"].to_numpy(dtype=float)
    ask0 = df["ask_px_0"].to_numpy(dtype=float)
    df["mid"] = (bid0 + ask0) / 2.0
    total = bvol0 + avol0
    safe = np.where(total > 0, total, np.nan)
    df["OBI1"] = (bvol0 - avol0) / safe
    return df


# ---------------------------------------------------------------------------
# Per-symbol study
# ---------------------------------------------------------------------------

@dataclass
class SymbolResult:
    symbol: str
    n_raw: int
    n_clean: int
    oos_hr_5s: float
    null_95_5s: float
    oos_hr_1s: float
    oos_hr_10s: float
    passes: bool
    reversed: bool


def _oos_hit_rate(df: pd.DataFrame, signal_col: str, target_col: str) -> tuple[float, int]:
    if target_col not in df.columns:
        return np.nan, 0
    n = len(df)
    split = int(0.60 * n)
    oos = df.iloc[split:]
    s = oos[signal_col].to_numpy(dtype=float)
    t = oos[target_col].to_numpy(dtype=float)
    valid = np.isfinite(s) & np.isfinite(t)
    s_v, t_v = s[valid], t[valid]
    nonzero = np.abs(t_v) > 0
    if not nonzero.any():
        return np.nan, 0
    return float(np.mean(np.sign(s_v[nonzero]) == np.sign(t_v[nonzero]))), int(nonzero.sum())


def run_symbol(path: str, symbol: str) -> SymbolResult:
    print(f"  {symbol}: loading {path} …", flush=True)
    df_raw = load_csv(path)
    n_raw = len(df_raw)
    df = clean(df_raw)
    df = filter_continuous_session(df)
    df = add_obi1(df)
    n_clean = len(df)

    if n_clean < 50:
        return SymbolResult(
            symbol=symbol, n_raw=n_raw, n_clean=n_clean,
            oos_hr_5s=np.nan, null_95_5s=np.nan,
            oos_hr_1s=np.nan, oos_hr_10s=np.nan,
            passes=False, reversed=False,
        )

    df = add_forward_returns(df, HORIZONS_S)

    sig = df["OBI1"].to_numpy(dtype=float)
    tgt_5s = df["fwd_mid_ret_5s"].to_numpy(dtype=float) if "fwd_mid_ret_5s" in df.columns else np.full(len(df), np.nan)

    _, null_hr_95 = bootstrap_null(sig, tgt_5s)
    oos_hr_5s, _ = _oos_hit_rate(df, "OBI1", "fwd_mid_ret_5s")
    oos_hr_1s, _ = _oos_hit_rate(df, "OBI1", "fwd_mid_ret_1s")
    oos_hr_10s, _ = _oos_hit_rate(df, "OBI1", "fwd_mid_ret_10s")

    passes = (
        np.isfinite(oos_hr_5s)
        and np.isfinite(null_hr_95)
        and oos_hr_5s > null_hr_95
        and oos_hr_5s > PASS_OOS_HR_THRESHOLD
    )
    reversed_ = np.isfinite(oos_hr_5s) and oos_hr_5s < REVERSAL_THRESHOLD

    print(
        f"    {symbol}: n_clean={n_clean:,}  oos_hr_5s={oos_hr_5s:.4f}  null_95={null_hr_95:.4f}  "
        f"{'PASS' if passes else 'FAIL'}{'  REVERSED' if reversed_ else ''}",
        flush=True,
    )

    return SymbolResult(
        symbol=symbol,
        n_raw=n_raw,
        n_clean=n_clean,
        oos_hr_5s=oos_hr_5s,
        null_95_5s=null_hr_95,
        oos_hr_1s=oos_hr_1s,
        oos_hr_10s=oos_hr_10s,
        passes=passes,
        reversed=reversed_,
    )


# ---------------------------------------------------------------------------
# Generalisation verdict
# ---------------------------------------------------------------------------

def generalisation_verdict(results: list[SymbolResult]) -> str:
    n_pass = sum(r.passes for r in results)
    any_reversed = any(r.reversed for r in results)
    if any_reversed:
        return "FLAG_REVERSAL"
    if n_pass == len(results):
        return "STRONG"
    if n_pass >= 4:
        return "WEAK"
    return "NO_GENERALISE"


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_boxplot(results: list[SymbolResult], outdir: str) -> str:
    oos_hrs = [r.oos_hr_5s for r in results if np.isfinite(r.oos_hr_5s)]
    symbols_with_data = [r.symbol for r in results if np.isfinite(r.oos_hr_5s)]

    fig, ax = plt.subplots(figsize=(8, max(3, len(symbols_with_data) * 0.6 + 1)))

    if oos_hrs:
        ax.boxplot(
            oos_hrs,
            vert=False,
            patch_artist=True,
            boxprops={"facecolor": "steelblue", "alpha": 0.4},
            medianprops={"color": "navy", "linewidth": 1.5},
        )
        for i, (sym, hr) in enumerate(zip(symbols_with_data, oos_hrs)):
            ax.scatter(hr, 1, s=50, zorder=5, color="steelblue", alpha=0.8)
            ax.annotate(sym, (hr, 1), textcoords="offset points", xytext=(0, 6), fontsize=7, ha="center")

    ax.axvline(0.5, color="tomato", linewidth=1.0, linestyle="--", label="0.5 baseline")
    ax.axvline(PASS_OOS_HR_THRESHOLD, color="green", linewidth=1.0, linestyle=":", label=f"pass threshold ({PASS_OOS_HR_THRESHOLD})")
    ax.set_xlabel("OOS hit-rate at 5s horizon")
    ax.set_title("OBI1 OOS hit-rate at 5s — all symbols")
    ax.set_yticks([])
    ax.legend(fontsize=8)
    ax.grid(True, axis="x", linewidth=0.4, alpha=0.5)
    plt.tight_layout()

    fname = "multisym_obi_boxplot.png"
    fig.savefig(os.path.join(outdir, fname), dpi=110)
    plt.close(fig)
    return fname


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v: Any, fmt: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return format(v, fmt)


def build_report(
    results: list[SymbolResult],
    verdict: str,
    png_paths: list[str],
) -> str:
    n_pass = sum(r.passes for r in results)
    lines: list[str] = []
    lines += [
        "# OBI1 Generalisation Study — 6 Symbols",
        "",
        "---",
        "",
        "## Generalisation Verdict",
        "",
        f"**{verdict}**",
        "",
        f"Symbols passing: {n_pass}/{len(results)}.",
        "",
        "PASS criterion per symbol: OOS hit-rate at 5s > bootstrap null 95th pct AND > "
        f"{PASS_OOS_HR_THRESHOLD}.",
        "",
        "FLAG_REVERSAL is set if any symbol has OOS hit-rate < "
        f"{REVERSAL_THRESHOLD} (directional reversal — investigate data quality).",
        "",
        "---",
        "",
        "## Per-symbol summary",
        "",
        "| symbol | n_clean | oos_hr_5s | null_95_5s | oos_hr_1s | oos_hr_10s | passes | reversed |",
        "|--------|---------|-----------|-----------|-----------|-----------|--------|---------|",
    ]
    for r in results:
        lines.append(
            f"| {r.symbol} "
            f"| {r.n_clean:,} "
            f"| {_fmt(r.oos_hr_5s)} "
            f"| {_fmt(r.null_95_5s)} "
            f"| {_fmt(r.oos_hr_1s)} "
            f"| {_fmt(r.oos_hr_10s)} "
            f"| {'True' if r.passes else 'False'} "
            f"| {'True' if r.reversed else 'False'} |"
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
        "- One trading day across 6 symbols — feasibility gate only, not peer-reviewed evidence.",
        "- LQ45-class names (BUMI, ITMG, INDY) have different tick-size regimes than "
          "lower-liquidity names (DEWA, MDKA); compare within tier where possible.",
        "- Forward returns are mid-price based; last-price forward returns may differ on "
          "low-liquidity names.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_study(data_dir: str, outdir: str, depth: int) -> None:
    os.makedirs(outdir, exist_ok=True)
    results: list[SymbolResult] = []

    for sym in SYMBOLS:
        path = os.path.join(data_dir, f"{sym}_features.csv")
        if not os.path.isfile(path):
            print(f"  WARNING: {path} not found — skipping {sym}", file=sys.stderr)
            results.append(SymbolResult(
                symbol=sym, n_raw=0, n_clean=0,
                oos_hr_5s=np.nan, null_95_5s=np.nan,
                oos_hr_1s=np.nan, oos_hr_10s=np.nan,
                passes=False, reversed=False,
            ))
            continue
        results.append(run_symbol(path, sym))

    verdict = generalisation_verdict(results)
    png_paths: list[str] = []
    png_paths.append(plot_boxplot(results, outdir))

    report = build_report(results, verdict, png_paths)
    report_path = os.path.join(outdir, "multisym_obi_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nReport written → {report_path}")
    print(f"Generalisation verdict: {verdict}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OBI1 generalisation study across 6 symbols. Writes out/multisym_obi_report.md + PNG."
    )
    p.add_argument("--dir", required=True, metavar="DIR", help="Directory containing {SYM}_features.csv files.")
    p.add_argument(
        "--outdir",
        default=os.path.join(SCRIPT_DIR, "out"),
        metavar="DIR2",
    )
    p.add_argument("--depth", type=int, default=5, metavar="N")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not os.path.isdir(args.dir):
        print(f"ERROR: data directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)
    run_study(args.dir, args.outdir, args.depth)


if __name__ == "__main__":
    main()
