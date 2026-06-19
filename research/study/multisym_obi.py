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
from dataclasses import dataclass
from typing import Any

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lib.loaders import load_csv, clean
from lib.session import (
    _is_lunch,
    _crosses_lunch,
    filter_continuous_session,
    LUNCH_START_H,
    LUNCH_START_M,
    LUNCH_END_H,
    LUNCH_END_M,
    SESSION_START_H,
    SESSION_START_M,
    SESSION_AM_END_H,
    SESSION_AM_END_M,
    SESSION_PM_START_H,
    SESSION_PM_START_M,
    SESSION_END_H,
    SESSION_END_M,
)
from lib.signals import add_obi1, add_forward_returns
from lib.stats import (
    bootstrap_null,
    conditional_hit_rate,
    nonoverlap_hit_rate,
    MIN_EFF_N,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SYMBOLS = ["ANTM", "BUMI", "DEWA", "INDY", "ITMG", "MDKA"]
HORIZONS_S = [1, 5, 10, 30, 60]
PRIMARY_HORIZON = 5
N_BOOTSTRAP = 500
BOOTSTRAP_BLOCK = 30

PASS_OOS_HR_THRESHOLD = 0.55
REVERSAL_THRESHOLD = 0.45


# ---------------------------------------------------------------------------
# Per-symbol study
# ---------------------------------------------------------------------------

@dataclass
class SymbolResult:
    symbol: str
    n_raw: int
    n_clean: int
    oos_hr_5s: float       # de-overlapped OOS hit-rate at 5s (primary trustworthy metric)
    oos_hr_5s_raw: float   # raw (overlapping) OOS hit-rate at 5s (for transparency)
    oos_eff_n: int         # independent conditional observations in OOS slice
    null_95_5s: float
    oos_hr_1s: float
    oos_hr_10s: float
    passes: bool
    reversed: bool
    inconclusive: bool     # passes rate thresholds but eff_n < MIN_EFF_N


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
            oos_hr_5s=np.nan, oos_hr_5s_raw=np.nan, oos_eff_n=0,
            null_95_5s=np.nan,
            oos_hr_1s=np.nan, oos_hr_10s=np.nan,
            passes=False, reversed=False, inconclusive=False,
        )

    df = add_forward_returns(df, HORIZONS_S)

    sig = df["OBI1"].to_numpy(dtype=float)
    tgt_5s = df["fwd_mid_ret_5s"].to_numpy(dtype=float) if "fwd_mid_ret_5s" in df.columns else np.full(len(df), np.nan)

    # Adaptive bootstrap block: at least BOOTSTRAP_BLOCK, at least ~1 horizon's
    # worth of rows, capped so it stays well below series length.
    _n_valid = int(np.isfinite(sig).sum())
    _span_s = max(1.0, (df["recv_ns"].iloc[-1] - df["recv_ns"].iloc[0]) / 1e9)
    _rows_per_s = max(1.0, _n_valid / _span_s)
    _adaptive_block = min(
        max(BOOTSTRAP_BLOCK, int(round(PRIMARY_HORIZON * _rows_per_s))),
        max(50, _n_valid // 20),
    )
    _, null_hr_95 = bootstrap_null(sig, tgt_5s, block=_adaptive_block)
    oos_hr_5s_raw, _ = _oos_hit_rate(df, "OBI1", "fwd_mid_ret_5s")
    oos_hr_1s, _ = _oos_hit_rate(df, "OBI1", "fwd_mid_ret_1s")
    oos_hr_10s, _ = _oos_hit_rate(df, "OBI1", "fwd_mid_ret_10s")

    # De-overlapped OOS hit-rate — the trustworthy independent-observation metric.
    df_oos = df.iloc[int(0.60 * len(df)):]
    oos_hr_5s, oos_eff_n = nonoverlap_hit_rate(df_oos, "OBI1", "fwd_mid_ret_5s", PRIMARY_HORIZON)

    _rate_ok = (
        np.isfinite(oos_hr_5s)
        and np.isfinite(null_hr_95)
        and oos_hr_5s > null_hr_95
        and oos_hr_5s > PASS_OOS_HR_THRESHOLD
    )
    passes = _rate_ok and oos_eff_n >= MIN_EFF_N
    inconclusive = _rate_ok and not passes  # rate OK but not enough independent obs
    reversed_ = np.isfinite(oos_hr_5s) and oos_hr_5s < REVERSAL_THRESHOLD

    print(
        f"    {symbol}: n_clean={n_clean:,}  oos_hr_5s={oos_hr_5s:.4f}(noov,eff_n={oos_eff_n})"
        f"  raw={oos_hr_5s_raw:.4f}  null_95={null_hr_95:.4f}  "
        f"{'PASS' if passes else ('INCONCLUSIVE' if inconclusive else 'FAIL')}"
        f"{'  REVERSED' if reversed_ else ''}",
        flush=True,
    )

    return SymbolResult(
        symbol=symbol,
        n_raw=n_raw,
        n_clean=n_clean,
        oos_hr_5s=oos_hr_5s,
        oos_hr_5s_raw=oos_hr_5s_raw,
        oos_eff_n=oos_eff_n,
        null_95_5s=null_hr_95,
        oos_hr_1s=oos_hr_1s,
        oos_hr_10s=oos_hr_10s,
        passes=passes,
        reversed=reversed_,
        inconclusive=inconclusive,
    )


# ---------------------------------------------------------------------------
# Generalisation verdict
# ---------------------------------------------------------------------------

def generalisation_verdict(results: list[SymbolResult]) -> str:
    """
    STRONG          — all symbols truly pass (rate criteria AND eff_n >= MIN_EFF_N)
    INSUFFICIENT_DATA — any symbol inconclusive (rate OK but eff_n < MIN_EFF_N)
                        and not enough clean passes for STRONG/WEAK
    WEAK            — >=4 symbols pass (with sufficient eff_n)
    NO_GENERALISE   — <4 pass
    FLAG_REVERSAL   — any symbol has OOS hit-rate < REVERSAL_THRESHOLD (highest priority)
    """
    n_pass = sum(r.passes for r in results)
    any_reversed = any(r.reversed for r in results)
    any_inconclusive = any(r.inconclusive for r in results)

    if any_reversed:
        return "FLAG_REVERSAL"
    if n_pass == len(results):
        return "STRONG"
    if any_inconclusive and n_pass < len(results):
        return "INSUFFICIENT_DATA"
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
        "PASS criterion per symbol: de-overlapped OOS hit-rate at 5s > bootstrap null 95th pct AND > "
        f"{PASS_OOS_HR_THRESHOLD} AND eff_n >= {MIN_EFF_N} independent observations. "
        "INCONCLUSIVE: rate thresholds met but eff_n < MIN_EFF_N (need more trading days).",
        "",
        "FLAG_REVERSAL is set if any symbol has OOS hit-rate < "
        f"{REVERSAL_THRESHOLD} (directional reversal — investigate data quality).",
        "",
        "---",
        "",
        "## Per-symbol summary",
        "",
        "| symbol | n_clean | oos_hr_5s (de-overlap) | oos_hr_5s_raw | eff_n | null_95_5s | oos_hr_1s | oos_hr_10s | passes | inconclusive | reversed |",
        "|--------|---------|----------------------|---------------|-------|-----------|-----------|-----------|--------|-------------|---------|",
    ]
    for r in results:
        lines.append(
            f"| {r.symbol} "
            f"| {r.n_clean:,} "
            f"| {_fmt(r.oos_hr_5s)} "
            f"| {_fmt(r.oos_hr_5s_raw)} "
            f"| {r.oos_eff_n} "
            f"| {_fmt(r.null_95_5s)} "
            f"| {_fmt(r.oos_hr_1s)} "
            f"| {_fmt(r.oos_hr_10s)} "
            f"| {'True' if r.passes else 'False'} "
            f"| {'True' if r.inconclusive else 'False'} "
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
        "- **Raw hit-rates count overlapping windows** — consecutive book-update rows share "
        "almost the same 5-second future, so raw n values (e.g. 71,257) are inflated by "
        "1–2 orders of magnitude versus the true independent-observation count (eff_n). "
        "The de-overlapped hit-rate and eff_n are the trustworthy figures.",
        "- **Effective sample sizes are small** — eff_n is typically dozens, not thousands, "
        "on a single trading day. A single day is a feasibility gate only.",
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
                oos_hr_5s=np.nan, oos_hr_5s_raw=np.nan, oos_eff_n=0,
                null_95_5s=np.nan,
                oos_hr_1s=np.nan, oos_hr_10s=np.nan,
                passes=False, reversed=False, inconclusive=False,
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
