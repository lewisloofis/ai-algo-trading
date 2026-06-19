"""
audit_join.py — adversarial audit of the OBI1 forward-mid hit-rate.

Goal: decide whether the headline OOS hit-rates (esp. ANTM 0.9866) reflect a
real, exploitable predictor or an artifact of the measurement.

Reproduces the multisym join exactly, then stress-tests it:
  A. Sanity: join has no self/look-ahead match (idx>i; target strictly forward).
  B. Reproduce the reported OOS hit-rate.
  C. Move frequency: how often does the mid actually change (the conditional base)?
  D. Overlap test: re-measure hit-rate on NON-OVERLAPPING observations spaced
     >= horizon apart, so no two obs share the same forward move. If the edge is
     real it survives; if it's overlap inflation it collapses toward 0.5.
  E. Episode count: how many *distinct* directional mid moves underlie the
     thousands of "hits"? (effective sample size)
  F. Entry-timing check: at the moment OBI is read, has the mid already begun
     moving? (i.e. is the signal contemporaneous with the move, not ahead of it)
"""
from __future__ import annotations
import os
import sys
import numpy as np
import pandas as pd

HORIZON_S = 5
SESSION = [(9 * 60, 12 * 60), (13 * 60 + 30, 16 * 60 + 15)]  # WIB minute windows


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    for c in ("bid_px_0", "ask_px_0", "bid_vol_0", "ask_vol_0"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("recv_ns").reset_index(drop=True)
    df["_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    df = df.dropna(subset=["bid_px_0", "ask_px_0", "bid_vol_0", "ask_vol_0", "_dt"])
    df = df[(df.bid_px_0 > 0) & (df.ask_px_0 > 0) & (df.bid_vol_0 > 0) & (df.ask_vol_0 > 0)]
    tmin = df["_dt"].dt.hour * 60 + df["_dt"].dt.minute
    mask = np.zeros(len(df), dtype=bool)
    for lo, hi in SESSION:
        mask |= (tmin >= lo) & (tmin < hi) if hi != 16 * 60 + 15 else (tmin >= lo) & (tmin <= hi)
    df = df[mask].reset_index(drop=True)
    df["mid"] = (df.bid_px_0 + df.ask_px_0) / 2.0
    tot = df.bid_vol_0 + df.ask_vol_0
    df["OBI1"] = (df.bid_vol_0 - df.ask_vol_0) / tot
    return df.reset_index(drop=True)


def forward_join(df: pd.DataFrame, h_s: int):
    """Return (fwd_mid_ret, idx_matched) exactly as the study does."""
    t = df["recv_ns"].to_numpy(np.int64)
    mid = df["mid"].to_numpy(float)
    h_ns = h_s * int(1e9)
    tol = 5 * int(1e9)
    fwd = np.full(len(df), np.nan)
    midx = np.full(len(df), -1, dtype=np.int64)
    self_match = 0
    for i in range(len(df)):
        j = int(np.searchsorted(t, t[i] + h_ns, side="left"))
        if j >= len(t):
            continue
        if t[j] > t[i] + h_ns + tol:
            continue
        if j <= i:
            self_match += 1
        fwd[i] = mid[j] - mid[i]
        midx[i] = j
    return fwd, midx, self_match


def hit_rate(obi: np.ndarray, fwd: np.ndarray):
    v = np.isfinite(obi) & np.isfinite(fwd)
    o, f = obi[v], fwd[v]
    nz = np.abs(f) > 0
    if nz.sum() == 0:
        return np.nan, 0
    return float(np.mean(np.sign(o[nz]) == np.sign(f[nz]))), int(nz.sum())


def audit(path: str, sym: str):
    df = load(path)
    n = len(df)
    fwd, midx, self_match = forward_join(df, HORIZON_S)
    obi = df["OBI1"].to_numpy(float)
    t = df["recv_ns"].to_numpy(np.int64)
    mid = df["mid"].to_numpy(float)

    print(f"\n===== {sym} =====")
    print(f"clean continuous rows: {n:,}")

    # A. look-ahead sanity
    print(f"[A] self/backward matches (idx<=i): {self_match}  (expect 0)")
    fwd_steps = (midx[midx >= 0] - np.arange(n)[midx >= 0])
    print(f"    forward step size rows: min={fwd_steps.min()} median={int(np.median(fwd_steps))}")

    # B. reproduce OOS hit-rate (last 40%)
    split = int(0.60 * n)
    oos = slice(split, n)
    oos_hr, oos_nz = hit_rate(obi[oos], fwd[oos])
    full_hr, full_nz = hit_rate(obi, fwd)
    print(f"[B] OOS hit-rate(5s) = {oos_hr:.4f}  (nonzero n={oos_nz:,})   full={full_hr:.4f}")

    # C. move frequency
    moved = np.isfinite(fwd) & (np.abs(fwd) > 0)
    valid = np.isfinite(fwd)
    print(f"[C] mid moved in 5s: {moved.sum():,}/{valid.sum():,} = {moved.mean()*100:.1f}% of valid rows")

    # D. NON-OVERLAPPING resample: walk forward, accept an obs only if its time
    #    is >= prev accepted time + horizon, so forward windows never overlap.
    h_ns = HORIZON_S * int(1e9)
    keep = []
    last = -(1 << 62)
    for i in range(n):
        if not np.isfinite(fwd[i]):
            continue
        if t[i] >= last + h_ns:
            keep.append(i)
            last = t[i]
    keep = np.array(keep)
    ko, kf = obi[keep], fwd[keep]
    nov_hr, nov_nz = hit_rate(ko, kf)
    # OOS slice of the non-overlapping set
    ksplit = int(0.60 * len(keep))
    nov_oos_hr, nov_oos_nz = hit_rate(ko[ksplit:], kf[ksplit:])
    print(f"[D] NON-OVERLAPPING ({HORIZON_S}s spacing): n={len(keep):,}  "
          f"full_hr={nov_hr:.4f} (nz={nov_nz:,})  oos_hr={nov_oos_hr:.4f} (nz={nov_oos_nz:,})")

    # E. distinct directional episodes (effective sample size)
    mids_moved = mid[moved]
    signs = np.sign(np.diff(mid[np.isfinite(mid)]))
    signs = signs[signs != 0]
    episodes = 1 + int(np.sum(np.diff(signs) != 0)) if len(signs) else 0
    print(f"[E] distinct mid-direction episodes (sign flips in mid path): ~{episodes:,}")
    print(f"    -> {oos_nz:,} OOS 'hits' rest on ~{int(episodes*0.4):,} independent moves")

    # F. entry-timing: when |fwd|>0, is OBI extreme already (telegraphed)?
    o_move = obi[moved]
    print(f"[F] |OBI1| on move-rows: mean={np.mean(np.abs(o_move)):.3f}  "
          f"vs all-rows mean={np.mean(np.abs(obi[np.isfinite(obi)])):.3f}  "
          f"(>> means move is already obvious in the book)")


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Adversarial audit of OBI1 forward-mid hit-rate."
    )
    p.add_argument(
        "--dir",
        default="out",
        metavar="DIR",
        help="Directory containing {SYM}_features.csv files (default: out).",
    )
    args = p.parse_args()

    for sym in ("ANTM", "BUMI", "DEWA"):
        path = os.path.join(args.dir, f"{sym}_features.csv")
        audit(path, sym)


if __name__ == "__main__":
    main()
