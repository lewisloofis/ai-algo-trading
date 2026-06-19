"""
backtest.py — Fill simulator and backtester for OBI1 trading study on IDX.

Tests whether OBI1 signal survives realistic frictions (fees, spread, fills).
Shared helpers (load_csv, clean, session filtering, OBI1) come from lib/.

Usage:
    python backtest.py [--dir DIR] [--outdir DIR2]
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import sys
from dataclasses import dataclass
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lib.loaders import load_csv, clean
from lib.session import (
    _crosses_lunch,
    filter_continuous_session,
)
from lib.signals import add_obi1
from lib.stats import MIN_EFF_N

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

FEE = {"buy": 0.0015, "sell": 0.0025}
THRESHOLDS = [0.3, 0.5, 0.7]
HORIZONS_S = [5, 30]
TARGET_LOTS = 100
OOS_SPLIT = 0.60
N_BOOTSTRAP = 2000
LOT_SHARES = 100
EXIT_TOLERANCE_NS = 5 * 10**9
SYMBOLS = ["BUMI", "DEWA", "INDY", "ITMG", "MDKA"]


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

def leg_cost(value: float, side: str) -> float:
    """Fee in IDR for a single leg. side='buy' or 'sell'."""
    return value * FEE[side]


# ---------------------------------------------------------------------------
# Trade dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Trade:
    entry_ns: int
    exit_ns: int
    entry_px: float
    exit_px: float
    shares: int
    side: str          # "long" or "short"
    gross_pnl: float
    fee: float
    net_pnl: float
    net_bps: float
    forced_exit: bool = False


# ---------------------------------------------------------------------------
# PnL helpers
# ---------------------------------------------------------------------------

def _long_pnl(entry_px: float, exit_px: float, shares: int) -> tuple[float, float, float, float]:
    """Returns (gross, fee, net, net_bps) for a long trade."""
    entry_value = entry_px * shares
    exit_value = exit_px * shares
    gross = (exit_px - entry_px) * shares
    fee = leg_cost(entry_value, "buy") + leg_cost(exit_value, "sell")
    net = gross - fee
    net_bps = net / entry_value * 1e4
    return gross, fee, net, net_bps


def _short_pnl(entry_px: float, exit_px: float, shares: int) -> tuple[float, float, float, float]:
    """Returns (gross, fee, net, net_bps) for a short trade."""
    entry_value = entry_px * shares
    exit_value = exit_px * shares
    gross = (entry_px - exit_px) * shares
    fee = leg_cost(entry_value, "sell") + leg_cost(exit_value, "buy")
    net = gross - fee
    net_bps = net / entry_value * 1e4
    return gross, fee, net, net_bps


def _calc_shares(vol_at_level: float) -> int:
    """Round down to nearest lot, cap at TARGET_LOTS."""
    lots = min(TARGET_LOTS, math.floor(vol_at_level / LOT_SHARES))
    return lots * LOT_SHARES


def _find_exit_idx(
    recv_ns_arr: np.ndarray,
    entry_ns: int,
    h_ns: int,
    wib_dt_entry: pd.Timestamp,
) -> Optional[int]:
    """
    Find exit row index via searchsorted. Returns None if no valid exit found.
    Drop if: beyond array bounds, timestamp too far from target, or crosses lunch.
    """
    target_ns = entry_ns + h_ns
    idx = int(np.searchsorted(recv_ns_arr, target_ns, side="left"))
    if idx >= len(recv_ns_arr):
        return None
    if recv_ns_arr[idx] > target_ns + EXIT_TOLERANCE_NS:
        return None
    if _crosses_lunch(entry_ns, recv_ns_arr[idx], wib_dt_entry):
        return None
    return idx


# ---------------------------------------------------------------------------
# Taker fill model
# ---------------------------------------------------------------------------

def run_taker(
    df: pd.DataFrame,
    thresh: float,
    h: int,
    allow_short: bool = False,
) -> tuple[list[Trade], int]:
    """
    Simulate taker (market-order) fills on OBI1 signal.

    Returns (trades, truncation_count).
    """
    recv_ns_arr = df["recv_ns"].to_numpy(dtype=np.int64)
    obi1_arr = df["OBI1"].to_numpy(dtype=float)
    bid_px = df["bid_px_0"].to_numpy(dtype=float)
    ask_px = df["ask_px_0"].to_numpy(dtype=float)
    bid_vol = df["bid_vol_0"].to_numpy(dtype=float)
    ask_vol = df["ask_vol_0"].to_numpy(dtype=float)
    wib_arr = df["_wib_dt"].to_numpy()

    h_ns = h * 10**9
    trades: list[Trade] = []
    truncation_count = 0
    i = 0
    n = len(df)

    while i < n:
        obi = obi1_arr[i]
        if not np.isfinite(obi):
            i += 1
            continue

        trade_side: Optional[str] = None
        if obi >= thresh:
            trade_side = "long"
        elif allow_short and obi <= -thresh:
            trade_side = "short"

        if trade_side is None:
            i += 1
            continue

        # Determine entry fill and size
        if trade_side == "long":
            entry_px = ask_px[i]
            size_vol = ask_vol[i]
        else:
            entry_px = bid_px[i]
            size_vol = bid_vol[i]

        shares = _calc_shares(size_vol)
        if shares == 0:
            i += 1
            continue

        # Check for truncation
        max_shares = TARGET_LOTS * LOT_SHARES
        if shares < max_shares:
            truncation_count += 1

        entry_ns = recv_ns_arr[i]
        wib_dt_entry = pd.Timestamp(wib_arr[i])

        # Find exit
        exit_idx = _find_exit_idx(recv_ns_arr, entry_ns, h_ns, wib_dt_entry)
        if exit_idx is None:
            # Drop trade, resume scanning after i
            i += 1
            continue

        # Exit fill
        if trade_side == "long":
            exit_px = bid_px[exit_idx]
        else:
            exit_px = ask_px[exit_idx]

        # PnL
        if trade_side == "long":
            gross, fee, net, net_bps = _long_pnl(entry_px, exit_px, shares)
        else:
            gross, fee, net, net_bps = _short_pnl(entry_px, exit_px, shares)

        trades.append(Trade(
            entry_ns=entry_ns,
            exit_ns=recv_ns_arr[exit_idx],
            entry_px=entry_px,
            exit_px=exit_px,
            shares=shares,
            side=trade_side,
            gross_pnl=gross,
            fee=fee,
            net_pnl=net,
            net_bps=net_bps,
            forced_exit=False,
        ))

        i = exit_idx + 1

    return trades, truncation_count


# ---------------------------------------------------------------------------
# Passive fill model
# ---------------------------------------------------------------------------

def _load_trades_df(sym: str, data_dir: str) -> Optional[pd.DataFrame]:
    """Load trades CSV for symbol. Returns None if not found."""
    pattern = os.path.join(data_dir, f"*{sym.lower()}*trades*.csv")
    matches = glob.glob(pattern)
    if not matches:
        return None
    path = matches[0]
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")
    df = df.dropna(subset=["recv_ns", "price", "vol"])
    df = df.sort_values("recv_ns").reset_index(drop=True)
    return df


def _passive_entry_fill(
    trades_ns: np.ndarray,
    trades_price: np.ndarray,
    trades_vol: np.ndarray,
    trades_side: np.ndarray,
    start_ns: int,
    end_ns: int,
    bid_px: float,
    queue_ahead: float,
) -> Optional[tuple[int, int]]:
    """
    Walk trades forward from start_ns to end_ns looking for a passive buy fill.
    Counts sell-initiated trades (side='S') at price <= bid_px.
    Returns (fill_ns, fill_trade_idx) when cumulative vol > queue_ahead, else None.
    """
    start_idx = int(np.searchsorted(trades_ns, start_ns, side="left"))
    cumvol = 0.0
    for j in range(start_idx, len(trades_ns)):
        if trades_ns[j] > end_ns:
            break
        if trades_side[j] == "S" and trades_price[j] <= bid_px:
            cumvol += trades_vol[j]
            if cumvol > queue_ahead:
                return int(trades_ns[j]), j
    return None


def _passive_exit_fill(
    trades_ns: np.ndarray,
    trades_price: np.ndarray,
    trades_vol: np.ndarray,
    trades_side: np.ndarray,
    start_ns: int,
    end_ns: int,
    ask_px: float,
    queue_ahead: float,
) -> Optional[int]:
    """
    Walk trades forward looking for passive sell fill.
    Counts buy-initiated trades (side='B') at price >= ask_px.
    Returns fill_ns when cumulative vol > queue_ahead, else None.
    """
    start_idx = int(np.searchsorted(trades_ns, start_ns, side="left"))
    cumvol = 0.0
    for j in range(start_idx, len(trades_ns)):
        if trades_ns[j] > end_ns:
            break
        if trades_side[j] == "B" and trades_price[j] >= ask_px:
            cumvol += trades_vol[j]
            if cumvol > queue_ahead:
                return int(trades_ns[j])
    return None


def run_passive(
    df: pd.DataFrame,
    trades_df: pd.DataFrame,
    thresh: float,
    h: int,
) -> tuple[list[Trade], int, int]:
    """
    Simulate passive (limit-order) fills on OBI1 signal. Long-only.

    Returns (trades, truncation_count, abandoned_count).
    """
    recv_ns_arr = df["recv_ns"].to_numpy(dtype=np.int64)
    obi1_arr = df["OBI1"].to_numpy(dtype=float)
    bid_px_arr = df["bid_px_0"].to_numpy(dtype=float)
    ask_px_arr = df["ask_px_0"].to_numpy(dtype=float)
    bid_vol_arr = df["bid_vol_0"].to_numpy(dtype=float)
    ask_vol_arr = df["ask_vol_0"].to_numpy(dtype=float)
    wib_arr = df["_wib_dt"].to_numpy()

    trades_ns = trades_df["recv_ns"].to_numpy(dtype=np.int64)
    trades_price = trades_df["price"].to_numpy(dtype=float)
    trades_vol = trades_df["vol"].to_numpy(dtype=float)
    trades_side = trades_df["side"].to_numpy(dtype=str)

    h_ns = h * 10**9
    trades: list[Trade] = []
    truncation_count = 0
    abandoned_count = 0
    i = 0
    n = len(df)

    while i < n:
        obi = obi1_arr[i]
        if not (np.isfinite(obi) and obi >= thresh):
            i += 1
            continue

        entry_ns = recv_ns_arr[i]
        entry_bid_px = bid_px_arr[i]
        queue_ahead = bid_vol_arr[i]
        wib_dt_entry = pd.Timestamp(wib_arr[i])

        # Passive entry: rest buy at bid, filled when sell vol > queue_ahead
        fill_result = _passive_entry_fill(
            trades_ns, trades_price, trades_vol, trades_side,
            start_ns=entry_ns,
            end_ns=entry_ns + h_ns,
            bid_px=entry_bid_px,
            queue_ahead=queue_ahead,
        )
        if fill_result is None:
            abandoned_count += 1
            i += 1
            continue

        fill_ns, _ = fill_result
        entry_px = entry_bid_px

        # Shares: size from bid_vol at signal row
        max_shares = TARGET_LOTS * LOT_SHARES
        shares_raw = _calc_shares(queue_ahead)
        shares = shares_raw if shares_raw > 0 else LOT_SHARES
        if shares < max_shares:
            truncation_count += 1

        # Find exit features row
        exit_idx = _find_exit_idx(recv_ns_arr, fill_ns, h_ns, wib_dt_entry)
        if exit_idx is None:
            i += 1
            continue

        exit_row_ns = recv_ns_arr[exit_idx]
        exit_ask_px = ask_px_arr[exit_idx]
        exit_ask_vol = ask_vol_arr[exit_idx]

        # Passive exit: rest sell at ask, filled when buy vol > queue_ahead_exit
        queue_ahead_exit = exit_ask_vol
        passive_exit_ns = _passive_exit_fill(
            trades_ns, trades_price, trades_vol, trades_side,
            start_ns=exit_row_ns,
            end_ns=exit_row_ns + h_ns,
            ask_px=exit_ask_px,
            queue_ahead=queue_ahead_exit,
        )

        forced = False
        if passive_exit_ns is not None:
            exit_px = exit_ask_px
            exit_ns_final = passive_exit_ns
        else:
            # Force taker flatten at bid of last features row in window
            last_feat_idx = int(np.searchsorted(recv_ns_arr, exit_row_ns + h_ns, side="right")) - 1
            last_feat_idx = max(exit_idx, min(last_feat_idx, n - 1))
            exit_px = bid_px_arr[last_feat_idx]
            exit_ns_final = recv_ns_arr[last_feat_idx]
            forced = True

        gross, fee, net, net_bps = _long_pnl(entry_px, exit_px, shares)

        trades.append(Trade(
            entry_ns=fill_ns,
            exit_ns=exit_ns_final,
            entry_px=entry_px,
            exit_px=exit_px,
            shares=shares,
            side="long",
            gross_pnl=gross,
            fee=fee,
            net_pnl=net,
            net_bps=net_bps,
            forced_exit=forced,
        ))

        i = exit_idx + 1

    return trades, truncation_count, abandoned_count


# ---------------------------------------------------------------------------
# Bootstrap p-value
# ---------------------------------------------------------------------------

def bootstrap_pvalue(
    net_bps_arr: np.ndarray,
    n_draws: int = N_BOOTSTRAP,
    seed: int = 42,
) -> tuple[float, float]:
    """Returns (p_value, pct5) where p_value = fraction of bootstrap means <= 0."""
    if len(net_bps_arr) < 2:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    boot_means = np.array([
        rng.choice(net_bps_arr, size=len(net_bps_arr), replace=True).mean()
        for _ in range(n_draws)
    ])
    p_value = float(np.mean(boot_means <= 0))
    pct5 = float(np.percentile(boot_means, 5))
    return p_value, pct5


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class CellMetrics:
    symbol: str
    model: str          # "taker" or "passive"
    variant: str        # "long_only" or "long_short"
    threshold: float
    horizon_s: int
    n_trades: int
    gross_pnl: float
    net_pnl: float
    mean_net_bps: float
    mean_gross_bps: float
    win_rate: float
    profit_factor: float
    avg_win_bps: float
    avg_loss_bps: float
    fill_rate: float    # nan for taker
    truncation_count: int
    forced_exit_count: int
    p_value: float
    pct5_net_bps: float


def _compute_metrics(
    sym: str,
    model: str,
    variant: str,
    thresh: float,
    h: int,
    trades: list[Trade],
    fill_rate: float,
    trunc_count: int,
) -> CellMetrics:
    """Compute CellMetrics from a list of trades."""
    net_bps_arr = np.array([t.net_bps for t in trades])
    gross_bps_arr = np.array([t.gross_pnl / (t.entry_px * t.shares) * 1e4 for t in trades])
    wins = net_bps_arr[net_bps_arr > 0]
    losses = net_bps_arr[net_bps_arr <= 0]
    win_rate = float(len(wins) / len(net_bps_arr)) if len(net_bps_arr) > 0 else float("nan")
    total_win = float(wins.sum()) if len(wins) > 0 else 0.0
    total_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
    profit_factor = total_win / total_loss if total_loss > 0 else float("inf")
    avg_win = float(wins.mean()) if len(wins) > 0 else float("nan")
    avg_loss = float(losses.mean()) if len(losses) > 0 else float("nan")
    forced = sum(1 for t in trades if t.forced_exit)
    pval, pct5 = bootstrap_pvalue(net_bps_arr)

    return CellMetrics(
        symbol=sym,
        model=model,
        variant=variant,
        threshold=thresh,
        horizon_s=h,
        n_trades=len(trades),
        gross_pnl=float(sum(t.gross_pnl for t in trades)),
        net_pnl=float(sum(t.net_pnl for t in trades)),
        mean_net_bps=float(net_bps_arr.mean()) if len(net_bps_arr) > 0 else float("nan"),
        mean_gross_bps=float(gross_bps_arr.mean()) if len(gross_bps_arr) > 0 else float("nan"),
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win_bps=avg_win,
        avg_loss_bps=avg_loss,
        fill_rate=fill_rate,
        truncation_count=trunc_count,
        forced_exit_count=forced,
        p_value=pval,
        pct5_net_bps=pct5,
    )


# ---------------------------------------------------------------------------
# Per-symbol runner
# ---------------------------------------------------------------------------

def run_symbol_backtest(
    sym: str,
    data_dir: str,
) -> list[CellMetrics]:
    """Run all cells for one symbol. Returns list of CellMetrics (OOS only)."""
    # Load features
    path = os.path.join(data_dir, f"{sym.lower()}_features.csv")
    if not os.path.isfile(path):
        # Try UPPER case
        path = os.path.join(data_dir, f"{sym}_features.csv")
    if not os.path.isfile(path):
        print(f"  WARNING: features file not found for {sym}, skipping.", file=sys.stderr)
        return []

    print(f"  {sym}: loading features…", flush=True)
    df = load_csv(path)
    df = clean(df)
    df = filter_continuous_session(df)
    df = add_obi1(df)

    if len(df) < 10:
        print(f"  {sym}: too few rows after cleaning, skipping.", file=sys.stderr)
        return []

    n = len(df)
    split_idx = int(OOS_SPLIT * n)
    df_oos = df.iloc[split_idx:].reset_index(drop=True)

    # Load trades (BUMI only)
    trades_df = _load_trades_df(sym, data_dir)

    all_cells: list[CellMetrics] = []

    for thresh in THRESHOLDS:
        for h in HORIZONS_S:
            # Taker long-only
            taker_trades_full, trunc_lo = run_taker(df_oos, thresh, h, allow_short=False)
            cell_lo = _compute_metrics(
                sym, "taker", "long_only", thresh, h,
                taker_trades_full, float("nan"), trunc_lo,
            )
            all_cells.append(cell_lo)
            print(
                f"    taker long_only t={thresh} h={h}s: n={cell_lo.n_trades} "
                f"mean_net={cell_lo.mean_net_bps:.2f}bps p={cell_lo.p_value:.3f}",
                flush=True,
            )

            # Taker long-short (upper bound)
            taker_trades_ls, trunc_ls = run_taker(df_oos, thresh, h, allow_short=True)
            cell_ls = _compute_metrics(
                sym, "taker", "long_short", thresh, h,
                taker_trades_ls, float("nan"), trunc_ls,
            )
            all_cells.append(cell_ls)
            print(
                f"    taker long_short t={thresh} h={h}s: n={cell_ls.n_trades} "
                f"mean_net={cell_ls.mean_net_bps:.2f}bps p={cell_ls.p_value:.3f}",
                flush=True,
            )

            # Passive long-only (only if trades data available)
            if trades_df is not None:
                passive_trades, trunc_p, abandoned = run_passive(
                    df_oos, trades_df, thresh, h
                )
                fill_rate = (
                    len(passive_trades) / (len(passive_trades) + abandoned)
                    if (len(passive_trades) + abandoned) > 0
                    else float("nan")
                )
                cell_p = _compute_metrics(
                    sym, "passive", "long_only", thresh, h,
                    passive_trades, fill_rate, trunc_p,
                )
                all_cells.append(cell_p)
                print(
                    f"    passive long_only t={thresh} h={h}s: n={cell_p.n_trades} "
                    f"fill_rate={fill_rate:.3f} mean_net={cell_p.mean_net_bps:.2f}bps "
                    f"p={cell_p.p_value:.3f} abandoned={abandoned}",
                    flush=True,
                )
            else:
                print(f"    passive: no trades data for {sym}, skipping passive model.", flush=True)

    return all_cells


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def compute_verdict(cells: list[CellMetrics]) -> tuple[str, str]:
    """
    Returns (verdict, ls_note).
    verdict is based on long_only cells (taker + passive).
    ls_note is based on long_short cells.
    """
    lo_cells = [c for c in cells if c.variant == "long_only"]
    ls_cells = [c for c in cells if c.variant == "long_short"]

    def _passes(c: CellMetrics) -> bool:
        return (
            c.net_pnl > 0
            and c.n_trades >= MIN_EFF_N
            and not np.isnan(c.p_value)
            and c.p_value < 0.05
        )

    def _positive_net(c: CellMetrics) -> bool:
        return c.net_pnl > 0

    any_pass = any(_passes(c) for c in lo_cells)
    any_positive = any(_positive_net(c) for c in lo_cells)
    positive_but_underpowered = (
        any_positive
        and not any_pass
        and any(
            _positive_net(c) and (c.n_trades < MIN_EFF_N or np.isnan(c.p_value) or c.p_value >= 0.05)
            for c in lo_cells
        )
    )

    if any_pass:
        verdict = "PASS"
    elif positive_but_underpowered:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "NO-GO"

    ls_pass = any(_passes(c) for c in ls_cells)
    ls_note = "long-short upper-bound: PASS" if ls_pass else "long-short upper-bound: NO-GO"

    return verdict, ls_note


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------

def _equity_curve(
    trades: list[Trade],
    sym: str,
    thresh: float,
    h: int,
    outdir: str,
) -> str:
    fname = f"backtest_equity_{sym}.png"
    fpath = os.path.join(outdir, fname)
    fig, ax = plt.subplots(figsize=(10, 4))

    if not trades:
        ax.text(0.5, 0.5, "No trades in OOS", transform=ax.transAxes,
                ha="center", va="center", fontsize=14, color="gray")
        ax.set_title(f"{sym} OOS Equity — taker long-only thresh={thresh} h={h}s")
    else:
        sorted_trades = sorted(trades, key=lambda t: t.entry_ns)
        cum_pnl = np.cumsum([t.net_pnl for t in sorted_trades])
        ax.plot(range(len(cum_pnl)), cum_pnl, linewidth=1.2, color="steelblue")
        ax.axhline(0, color="tomato", linewidth=0.8, linestyle="--")
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Cumulative net PnL (IDR)")
        ax.set_title(f"{sym} OOS Equity — taker long-only thresh={thresh} h={h}s")
        ax.grid(True, linewidth=0.4, alpha=0.5)

    plt.tight_layout()
    fig.savefig(fpath, dpi=110)
    plt.close(fig)
    return fname


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _fmt(v: float, fmt: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "—"
    return format(v, fmt)


def _cell_table_rows(cells: list[CellMetrics]) -> list[str]:
    header = (
        "| sym | model | variant | thresh | h_s | n_trades | mean_net_bps | mean_gross_bps "
        "| win_rate | profit_factor | fill_rate | forced_exits | p_value | pct5_net_bps |"
    )
    sep = "|" + "|".join(["---"] * 14) + "|"
    rows = [header, sep]
    for c in cells:
        rows.append(
            f"| {c.symbol} | {c.model} | {c.variant} | {c.threshold} | {c.horizon_s} "
            f"| {c.n_trades} | {_fmt(c.mean_net_bps, '.2f')} | {_fmt(c.mean_gross_bps, '.2f')} "
            f"| {_fmt(c.win_rate, '.3f')} | {_fmt(c.profit_factor, '.2f')} "
            f"| {_fmt(c.fill_rate, '.3f')} | {c.forced_exit_count} "
            f"| {_fmt(c.p_value, '.4f')} | {_fmt(c.pct5_net_bps, '.2f')} |"
        )
    return rows


def build_report(
    all_cells: list[CellMetrics],
    verdict: str,
    ls_note: str,
    equity_pngs: list[str],
) -> str:
    lo_taker = [c for c in all_cells if c.model == "taker" and c.variant == "long_only"]
    lo_passive = [c for c in all_cells if c.model == "passive" and c.variant == "long_only"]
    ls_taker = [c for c in all_cells if c.model == "taker" and c.variant == "long_short"]

    lines: list[str] = [
        "# OBI1 Backtest Report — Fill Simulator",
        "",
        "---",
        "",
        "## Verdict",
        "",
        f"**{verdict}**  ({ls_note})",
        "",
        f"PASS criterion: mean_net_pnl > 0, n_trades >= {MIN_EFF_N}, p_value < 0.05 "
        "(bootstrap fraction of means <= 0 < 5%).",
        "",
        "---",
        "",
        "## Taker Long-Only (retail base case)",
        "",
    ]
    lines += _cell_table_rows(lo_taker)
    lines += [
        "",
        "---",
        "",
        "## Passive Long-Only (limit-order model, BUMI only)",
        "",
    ]
    if lo_passive:
        lines += _cell_table_rows(lo_passive)
    else:
        lines.append("_No passive data available (trades file missing for all symbols)._")
    lines += [
        "",
        "---",
        "",
        "## Taker Long-Short (upper bound — not retail feasible)",
        "",
    ]
    lines += _cell_table_rows(ls_taker)
    lines += [
        "",
        "---",
        "",
        "## Fee Drag Commentary",
        "",
        "Round-trip fee cost at IDX: buy 0.15% + sell 0.25% = 0.40% (40 bps) all-in.",
        "A trade must move ≥ 40 bps gross just to break even.",
        "At typical BUMI spread of 1 tick (168→169 = ~59 bps) the gross move required",
        "to cover fees is achievable only on strongly directional moves.",
        "The net_bps figures above are after this drag; negative mean_net_bps cells",
        "indicate fee exceeds signal edge.",
        "",
        "---",
        "",
        "## Equity Curves",
        "",
    ]
    for p in equity_pngs:
        lines.append(f"![{p}]({p})")
    lines += [
        "",
        "---",
        "",
        "## Caveats",
        "",
        "- **One trading day only** — June 18 2026. Results are a feasibility gate, not evidence.",
        "- **Taker fills assume infinite liquidity at BBO** — real fills may be partial or worse.",
        "- **Passive model simulates queue position** but ignores adverse selection and cancel risk.",
        "- **No slippage model** — actual market-impact not modelled.",
        "- **OOS split = 60%** — OOS period is the last 40% of intraday rows, not a separate day.",
        "- **Long-short variant is an upper bound** — shorting IDX stocks requires margin approval.",
        "- **Passive model runs on all 5 names** — per-symbol trade tapes exported from the June 18 segment.",
        "- **Fee constants**: buy 0.15%, sell 0.25% (all-in, including exchange + broker).",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_backtest(data_dir: str, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    all_cells: list[CellMetrics] = []

    for sym in SYMBOLS:
        print(f"\n=== {sym} ===", flush=True)
        cells = run_symbol_backtest(sym, data_dir)
        all_cells.extend(cells)

    if not all_cells:
        print("ERROR: no cells produced — check data directory.", file=sys.stderr)
        sys.exit(1)

    verdict, ls_note = compute_verdict(all_cells)

    # Equity curves — taker long-only, thresh=0.5, h=5s (or first available)
    equity_pngs: list[str] = []
    for sym in SYMBOLS:
        sym_lo = [c for c in all_cells
                  if c.symbol == sym and c.model == "taker" and c.variant == "long_only"]
        if not sym_lo:
            continue
        # Pick thresh=0.5, h=5 if available, else first
        target = [c for c in sym_lo if c.threshold == 0.5 and c.horizon_s == 5]
        chosen_cell = target[0] if target else sym_lo[0]

        # Re-run taker OOS for equity curve
        path = os.path.join(data_dir, f"{sym.lower()}_features.csv")
        if not os.path.isfile(path):
            path = os.path.join(data_dir, f"{sym}_features.csv")
        if os.path.isfile(path):
            df_eq = load_csv(path)
            df_eq = clean(df_eq)
            df_eq = filter_continuous_session(df_eq)
            df_eq = add_obi1(df_eq)
            split_idx = int(OOS_SPLIT * len(df_eq))
            df_oos_eq = df_eq.iloc[split_idx:].reset_index(drop=True)
            eq_trades, _ = run_taker(df_oos_eq, chosen_cell.threshold, chosen_cell.horizon_s, allow_short=False)
        else:
            eq_trades = []

        fname = _equity_curve(eq_trades, sym, chosen_cell.threshold, chosen_cell.horizon_s, outdir)
        equity_pngs.append(fname)

    report = build_report(all_cells, verdict, ls_note, equity_pngs)
    report_path = os.path.join(outdir, "backtest_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Print summary to stdout
    print(f"\n{'='*60}")
    print(f"VERDICT: {verdict}  ({ls_note})")
    print(f"{'='*60}")
    print(f"\n{'Model':<8} {'Variant':<12} {'Sym':<6} {'T':<5} {'H':<4} "
          f"{'N':>6} {'mean_net_bps':>13} {'p_val':>7}")
    print("-" * 70)
    for c in all_cells:
        print(
            f"{c.model:<8} {c.variant:<12} {c.symbol:<6} {c.threshold:<5} {c.horizon_s:<4} "
            f"{c.n_trades:>6} {_fmt(c.mean_net_bps, '.2f'):>13} {_fmt(c.p_value, '.4f'):>7}"
        )
    print(f"\nReport written → {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OBI1 fill simulator + backtester.")
    p.add_argument("--dir", default=os.path.join(SCRIPT_DIR, "out"), metavar="DIR")
    p.add_argument("--outdir", default=os.path.join(SCRIPT_DIR, "out"), metavar="DIR2")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not os.path.isdir(args.dir):
        print(f"ERROR: data directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)
    run_backtest(args.dir, args.outdir)


if __name__ == "__main__":
    main()
