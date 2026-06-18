"""
preopen_iep.py — Descriptive pre-opening IEP study (n=6 symbols, 1 day).

**DESCRIPTIVE ONLY — no statistical inference. n=6 data points.**

Reads {SYM}_features.csv for: ANTM, BUMI, DEWA, INDY, ITMG, MDKA.
Compares the direction of IEP movement during pre-opening (08:45–08:58 WIB)
against the direction of the opening mid-price move, and checks whether
OBI1 at last pre-opening quote agrees with the open direction.

Usage:
    python preopen_iep.py --dir DIR [--outdir DIR2]
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any

import matplotlib
matplotlib.use("Agg")

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SYMBOLS = ["ANTM", "BUMI", "DEWA", "INDY", "ITMG", "MDKA"]

PREOPEN_START_H, PREOPEN_START_M = 8, 45
PREOPEN_END_H, PREOPEN_END_M = 8, 58
SESSION_START_H, SESSION_START_M = 9, 0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    for col in [c for c in df.columns if c != "iso_wib"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("recv_ns").reset_index(drop=True)
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    df = df.dropna(subset=["_wib_dt"])
    return df


def _time_minutes(wib_dt: pd.Series) -> pd.Series:
    return wib_dt.dt.hour * 60 + wib_dt.dt.minute


# ---------------------------------------------------------------------------
# Per-symbol study
# ---------------------------------------------------------------------------

@dataclass
class SymbolRow:
    symbol: str
    iep_first: float
    iep_final: float
    iep_dir: int          # -1, 0, +1 (sign)
    open_dir: int
    iep_matches_open: bool | None
    obi1_preopen: float
    obi1_dir: int
    obi1_matches_open: bool | None
    preopen_mid: float
    open_mid: float


def _sign(v: float) -> int:
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def analyse_symbol(df: pd.DataFrame, symbol: str) -> SymbolRow | None:
    t_min = _time_minutes(df["_wib_dt"])

    preopen_s = PREOPEN_START_H * 60 + PREOPEN_START_M
    preopen_e = PREOPEN_END_H * 60 + PREOPEN_END_M
    sess_s = SESSION_START_H * 60 + SESSION_START_M

    preopen_mask = (t_min >= preopen_s) & (t_min <= preopen_e)
    df_pre = df[preopen_mask].copy()

    # Drop rows with no IEP or zero IEP
    df_pre = df_pre[df_pre["iep"].notna() & (df_pre["iep"] != 0)]

    if len(df_pre) < 2:
        print(f"  {symbol}: insufficient pre-opening IEP rows ({len(df_pre)})", flush=True)
        return None

    iep_first = float(df_pre["iep"].iloc[0])
    iep_final = float(df_pre["iep"].iloc[-1])

    if iep_first == iep_final:
        print(f"  {symbol}: IEP unchanged in pre-opening window — skipping direction.", flush=True)
        iep_dir = 0
    else:
        iep_dir = _sign(iep_final - iep_first)

    # OBI1 at last pre-opening book update
    last_pre = df_pre.iloc[-1]
    bvol0 = float(last_pre["bid_vol_0"]) if "bid_vol_0" in last_pre and pd.notna(last_pre["bid_vol_0"]) else np.nan
    avol0 = float(last_pre["ask_vol_0"]) if "ask_vol_0" in last_pre and pd.notna(last_pre["ask_vol_0"]) else np.nan
    bid0 = float(last_pre["bid_px_0"]) if "bid_px_0" in last_pre and pd.notna(last_pre["bid_px_0"]) else np.nan
    ask0 = float(last_pre["ask_px_0"]) if "ask_px_0" in last_pre and pd.notna(last_pre["ask_px_0"]) else np.nan

    total_vol = bvol0 + avol0 if (np.isfinite(bvol0) and np.isfinite(avol0)) else np.nan
    obi1 = (bvol0 - avol0) / total_vol if (np.isfinite(total_vol) and total_vol > 0) else np.nan

    preopen_mid = (bid0 + ask0) / 2.0 if (np.isfinite(bid0) and np.isfinite(ask0)) else np.nan

    # First continuous-session row after 09:00
    sess_mask = t_min >= sess_s
    df_sess = df[sess_mask & df["bid_px_0"].notna() & df["ask_px_0"].notna()].copy()
    df_sess = df_sess[(df_sess["bid_px_0"] > 0) & (df_sess["ask_px_0"] > 0)]

    if len(df_sess) == 0:
        print(f"  {symbol}: no continuous-session rows found.", flush=True)
        return None

    first_sess = df_sess.iloc[0]
    open_bid = float(first_sess["bid_px_0"])
    open_ask = float(first_sess["ask_px_0"])
    open_mid = (open_bid + open_ask) / 2.0

    if not np.isfinite(preopen_mid) or not np.isfinite(open_mid):
        open_dir = 0
    else:
        open_dir = _sign(open_mid - preopen_mid)

    iep_matches = None
    if iep_dir != 0 and open_dir != 0:
        iep_matches = iep_dir == open_dir

    obi1_dir = _sign(obi1) if np.isfinite(obi1) else 0
    obi1_matches = None
    if obi1_dir != 0 and open_dir != 0:
        obi1_matches = obi1_dir == open_dir

    print(
        f"  {symbol}: iep_dir={iep_dir:+d}  open_dir={open_dir:+d}  "
        f"iep_match={iep_matches}  obi1={obi1:.3f}  obi1_match={obi1_matches}",
        flush=True,
    )

    return SymbolRow(
        symbol=symbol,
        iep_first=iep_first,
        iep_final=iep_final,
        iep_dir=iep_dir,
        open_dir=open_dir,
        iep_matches_open=iep_matches,
        obi1_preopen=obi1,
        obi1_dir=obi1_dir,
        obi1_matches_open=obi1_matches,
        preopen_mid=preopen_mid,
        open_mid=open_mid,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt(v: Any, fmt: str = ".2f") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return format(v, fmt)


def _bool_str(v: bool | None) -> str:
    if v is None:
        return "—"
    return "True" if v else "False"


def build_report(rows: list[SymbolRow], symbol_date: str) -> str:
    valid_rows = [r for r in rows if r is not None]
    n_iep_matches = sum(1 for r in valid_rows if r.iep_matches_open is True)
    n_iep_assessable = sum(1 for r in valid_rows if r.iep_matches_open is not None)
    n_obi_matches = sum(1 for r in valid_rows if r.obi1_matches_open is True)
    n_obi_assessable = sum(1 for r in valid_rows if r.obi1_matches_open is not None)

    lines: list[str] = []
    lines += [
        f"# Pre-Opening IEP Descriptive Study — {symbol_date}",
        "",
        "> **DESCRIPTIVE STUDY ONLY.** n=6 symbols, 1 trading day. "
        "No statistical inference is valid from this data. "
        "Counts and hit-rates are reported purely as exploration; they have no "
        "sampling distribution and should not be treated as estimates of a population parameter.",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- Symbols analysed: {len(valid_rows)}/{len(SYMBOLS)}",
        f"- IEP direction matches open direction: {n_iep_matches}/{n_iep_assessable} assessable symbols",
        f"- OBI1 sign matches open direction: {n_obi_matches}/{n_obi_assessable} assessable symbols",
        "",
        "Assessable = both directions are non-zero (there was a move to characterise).",
        "",
        "---",
        "",
        "## Per-symbol table",
        "",
        "| symbol | iep_first | iep_final | iep_dir | preopen_mid | open_mid | open_dir | iep_matches_open | obi1_preopen | obi1_dir | obi1_matches_open |",
        "|--------|----------|----------|---------|------------|---------|---------|----------------|-------------|---------|-----------------|",
    ]

    for r in valid_rows:
        lines.append(
            f"| {r.symbol} "
            f"| {_fmt(r.iep_first)} "
            f"| {_fmt(r.iep_final)} "
            f"| {r.iep_dir:+d} "
            f"| {_fmt(r.preopen_mid)} "
            f"| {_fmt(r.open_mid)} "
            f"| {r.open_dir:+d} "
            f"| {_bool_str(r.iep_matches_open)} "
            f"| {_fmt(r.obi1_preopen, '.3f')} "
            f"| {r.obi1_dir:+d} "
            f"| {_bool_str(r.obi1_matches_open)} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Definitions",
        "",
        "- **iep_first / iep_final**: First and last non-NaN/non-zero IEP value in 08:45–08:58 WIB window.",
        "- **iep_dir**: sign(iep_final − iep_first). 0 = no change.",
        "- **preopen_mid**: (bid_px_0 + ask_px_0) / 2 at the last pre-opening book update.",
        "- **open_mid**: (bid_px_0 + ask_px_0) / 2 at the first continuous-session row after 09:00 WIB.",
        "- **open_dir**: sign(open_mid − preopen_mid). 0 = no change.",
        "- **obi1_preopen**: OBI1 = (bid_vol_0 − ask_vol_0) / (bid_vol_0 + ask_vol_0) at last pre-opening row.",
        "- **obi1_dir**: sign(obi1_preopen). Positive = more bids, negative = more asks.",
        "- **iep_matches_open / obi1_matches_open**: True if directions agree; None if either direction is 0.",
        "",
        "---",
        "",
        "## Interpretation guidance",
        "",
        "IEP (Indicative Equilibrium Price) is published by the JSE during the pre-opening "
        "auction. A rising IEP suggests buy-side pressure is dominating the auction order book. "
        "Whether this reliably predicts the direction of the first continuous-session mid-price "
        "move cannot be determined from a single day across six names. These numbers are purely "
        "exploratory context for designing a longer-horizon study.",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_study(data_dir: str, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    rows: list[SymbolRow | None] = []
    symbol_date = ""

    for sym in SYMBOLS:
        path = os.path.join(data_dir, f"{sym}_features_preopen.csv")
        if not os.path.isfile(path):
            # Fall back to full features CSV if pre-open file not present.
            path = os.path.join(data_dir, f"{sym}_features.csv")
        if not os.path.isfile(path):
            print(f"  WARNING: {path} not found — skipping {sym}", file=sys.stderr)
            rows.append(None)
            continue
        df = load_csv(path)
        if symbol_date == "" and len(df) > 0:
            dt0 = df["_wib_dt"].iloc[0]
            symbol_date = dt0.strftime("%Y-%m-%d") if not pd.isna(dt0) else ""
        result = analyse_symbol(df, sym)
        rows.append(result)

    valid_rows = [r for r in rows if r is not None]
    report = build_report(valid_rows, symbol_date)
    report_path = os.path.join(outdir, "preopen_iep_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport written → {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Descriptive pre-opening IEP study across 6 JSE symbols. "
            "DESCRIPTIVE ONLY — no statistical inference. "
            "Writes out/preopen_iep_report.md."
        )
    )
    p.add_argument("--dir", required=True, metavar="DIR", help="Directory containing {SYM}_features.csv files.")
    p.add_argument(
        "--outdir",
        default=os.path.join(SCRIPT_DIR, "out"),
        metavar="DIR2",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not os.path.isdir(args.dir):
        print(f"ERROR: data directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)
    run_study(args.dir, args.outdir)


if __name__ == "__main__":
    main()
