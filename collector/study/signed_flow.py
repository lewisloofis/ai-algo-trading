"""
signed_flow.py — Signed trade flow, Kyle's lambda, and OBI→trade-sign study.

Reads the features CSV and the trades CSV emitted by `sbcap features` and
`sbcap trades`, then:
  1. Computes Kyle's lambda (price impact per unit of signed flow).
  2. Tests whether OBI1 predicts the direction of incoming trades (logistic).
  3. Measures incremental R² of signed flow Q over OBI1 alone.

Usage:
    python signed_flow.py --features PATH --trades PATH [--outdir DIR]
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
import scipy.stats as sps
import statsmodels.api as sm
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

HORIZONS_S = [1, 5, 10, 30, 60]
N_BOOTSTRAP = 500
BOOTSTRAP_BLOCK = 30

LUNCH_START_H, LUNCH_START_M = 12, 0
LUNCH_END_H, LUNCH_END_M = 13, 30

SESSION_START_H, SESSION_START_M = 9, 0
SESSION_AM_END_H, SESSION_AM_END_M = 12, 0
SESSION_PM_START_H, SESSION_PM_START_M = 13, 30
SESSION_END_H, SESSION_END_M = 16, 15

FLOW_WINDOWS_S = [1, 5, 10, 30]
LOTS_PER_UNIT = 100  # for Kyle's lambda scaling

OFI_INCREMENTAL_THRESHOLD = 0.05


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _is_lunch(dt: pd.Timestamp) -> bool:
    h, m = dt.hour, dt.minute
    lunch_start = LUNCH_START_H * 60 + LUNCH_START_M
    lunch_end = LUNCH_END_H * 60 + LUNCH_END_M
    t_min = h * 60 + m
    return lunch_start <= t_min < lunch_end


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


def ols_hac(
    signal: np.ndarray, target: np.ndarray, maxlags: int
) -> tuple[float, float, float, float, float]:
    """Returns (slope, slope_se, t_stat, ci_lo, ci_hi, r_squared) — 6 values.
    Raises ValueError if fit fails so caller can handle."""
    valid = np.isfinite(signal) & np.isfinite(target)
    s = signal[valid]
    t = target[valid]
    if len(s) < 30:
        return np.nan, np.nan, np.nan, np.nan, np.nan
    X = sm.add_constant(s, prepend=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = sm.OLS(t, X).fit(
                cov_type="HAC",
                cov_kwds={"maxlags": maxlags, "use_correction": True},
            )
        slope = float(fit.params[1])
        se = float(fit.bse[1])
        t_stat = float(fit.tvalues[1])
        ci = np.asarray(fit.conf_int(alpha=0.05))
        ci_lo = float(ci[1, 0])
        ci_hi = float(ci[1, 1])
        return slope, se, t_stat, ci_lo, ci_hi
    except Exception:
        return np.nan, np.nan, np.nan, np.nan, np.nan


def ols_hac_r2(
    X_mat: np.ndarray, target: np.ndarray, maxlags: int
) -> float:
    valid = np.isfinite(target) & np.all(np.isfinite(X_mat), axis=1)
    t = target[valid]
    X = X_mat[valid]
    if len(t) < 30:
        return np.nan
    Xc = sm.add_constant(X, prepend=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = sm.OLS(t, Xc).fit(
                cov_type="HAC",
                cov_kwds={"maxlags": maxlags, "use_correction": True},
            )
        return float(fit.rsquared)
    except Exception:
        return np.nan


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


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_features(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    for col in [c for c in df.columns if c != "iso_wib"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("recv_ns").reset_index(drop=True)
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    return df


def load_trades(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["recv_ns"] = pd.to_numeric(df["recv_ns"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")
    df["trade_num"] = pd.to_numeric(df["trade_num"], errors="coerce")
    df = df.dropna(subset=["recv_ns", "price", "vol", "side"])
    df = df[df["side"].isin(["B", "S"])]
    df = df.sort_values("recv_ns").reset_index(drop=True)
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    return df


def clean_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.dropna(subset=["bid_px_0", "ask_px_0", "bid_vol_0", "ask_vol_0", "_wib_dt"])
    df = df[(df["bid_px_0"] > 0) & (df["ask_px_0"] > 0)]
    df = df[(df["bid_vol_0"] > 0) & (df["ask_vol_0"] > 0)]
    return df.reset_index(drop=True)


def _in_continuous_session(wib_dt: pd.Series) -> pd.Series:
    h = wib_dt.dt.hour
    m = wib_dt.dt.minute
    t_min = h * 60 + m
    am_start = SESSION_START_H * 60 + SESSION_START_M
    am_end = SESSION_AM_END_H * 60 + SESSION_AM_END_M
    pm_start = SESSION_PM_START_H * 60 + SESSION_PM_START_M
    pm_end = SESSION_END_H * 60 + SESSION_END_M
    return ((t_min >= am_start) & (t_min < am_end)) | (
        (t_min >= pm_start) & (t_min <= pm_end)
    )


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------

def add_mid_obi1(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    bid0 = df["bid_px_0"].to_numpy(dtype=float)
    ask0 = df["ask_px_0"].to_numpy(dtype=float)
    bvol0 = df["bid_vol_0"].to_numpy(dtype=float)
    avol0 = df["ask_vol_0"].to_numpy(dtype=float)
    df["mid"] = (bid0 + ask0) / 2.0
    total = bvol0 + avol0
    safe = np.where(total > 0, total, np.nan)
    df["OBI1"] = (bvol0 - avol0) / safe
    return df


def add_signed_flow(
    feat: pd.DataFrame, trades: pd.DataFrame, windows_s: list[int]
) -> pd.DataFrame:
    """
    For each book-update row at time t, Q_Xs = Σ(ε·vol) for trades with
    recv_ns in [t - X*1e9, t].
    """
    feat = feat.copy()
    trades = trades.copy()
    trades["epsilon"] = trades["side"].map({"B": 1.0, "S": -1.0})
    trades["signed_vol"] = trades["epsilon"] * trades["vol"]

    trade_ns = trades["recv_ns"].to_numpy(dtype=np.int64)
    sv_arr = trades["signed_vol"].to_numpy(dtype=float)
    feat_ns = feat["recv_ns"].to_numpy(dtype=np.int64)

    for w in windows_s:
        w_ns = w * int(1e9)
        result = np.empty(len(feat), dtype=float)
        for i in range(len(feat)):
            t = feat_ns[i]
            lo = int(np.searchsorted(trade_ns, t - w_ns, side="left"))
            hi = int(np.searchsorted(trade_ns, t, side="right"))
            if lo >= hi:
                result[i] = 0.0
            else:
                sv_slice = sv_arr[lo:hi]
                result[i] = float(np.nansum(sv_slice))
        feat[f"Q_{w}s"] = result

    return feat


# ---------------------------------------------------------------------------
# Study: Kyle's lambda
# ---------------------------------------------------------------------------

def kyle_lambda(
    feat: pd.DataFrame, window_col: str, maxlags: int
) -> dict[str, float]:
    """
    Fit OLS: Δmid_t ~ Q_window_t (HAC SE).
    Δmid = mid(t) - mid(t-1), computed on continuous-session rows.
    """
    feat = feat.copy()
    mid = feat["mid"].to_numpy(dtype=float)
    delta_mid = np.empty(len(feat), dtype=float)
    delta_mid[0] = np.nan
    delta_mid[1:] = mid[1:] - mid[:-1]

    feat["_delta_mid"] = delta_mid
    q = feat[window_col].to_numpy(dtype=float)
    dm = feat["_delta_mid"].to_numpy(dtype=float)

    slope, se, t_stat, ci_lo, ci_hi = ols_hac(q, dm, maxlags)
    return {
        "lambda_raw": slope,
        "lambda_per_100lots": slope * LOTS_PER_UNIT if np.isfinite(slope) else np.nan,
        "se": se,
        "t_stat": t_stat,
        "ci_lo_per_100lots": ci_lo * LOTS_PER_UNIT if np.isfinite(ci_lo) else np.nan,
        "ci_hi_per_100lots": ci_hi * LOTS_PER_UNIT if np.isfinite(ci_hi) else np.nan,
    }


# ---------------------------------------------------------------------------
# Study: OBI → trade sign
# ---------------------------------------------------------------------------

def obi_to_trade_sign(
    feat: pd.DataFrame, trades: pd.DataFrame
) -> dict[str, Any]:
    """
    Look up OBI1 at the nearest prior book update for each trade.
    Fit logistic: P(side=B | OBI1).
    """
    feat_ns = feat["recv_ns"].to_numpy(dtype=np.int64)
    obi_arr = feat["OBI1"].to_numpy(dtype=float)

    trades_sess = trades[_in_continuous_session(trades["_wib_dt"])].copy()
    if len(trades_sess) == 0:
        return {}

    trade_ns = trades_sess["recv_ns"].to_numpy(dtype=np.int64)
    trade_side = trades_sess["side"].to_numpy()

    obi_at_trade = np.empty(len(trades_sess), dtype=float)
    for i in range(len(trades_sess)):
        idx = int(np.searchsorted(feat_ns, trade_ns[i], side="right")) - 1
        if idx < 0:
            obi_at_trade[i] = np.nan
        else:
            obi_at_trade[i] = obi_arr[idx]

    is_buy = (trade_side == "B").astype(float)
    valid = np.isfinite(obi_at_trade)
    X = obi_at_trade[valid].reshape(-1, 1)
    y = is_buy[valid]

    if len(y) < 30 or len(np.unique(y)) < 2:
        return {}

    clf = LogisticRegression(max_iter=500)
    clf.fit(X, y)
    y_prob = clf.predict_proba(X)[:, 1]
    auc = float(roc_auc_score(y, y_prob))

    obi_vals = X.ravel()
    strong_pos = obi_vals > 0.3
    strong_neg = obi_vals < -0.3

    hr_pos = float(np.mean(y[strong_pos] == 1)) if strong_pos.any() else np.nan
    hr_neg = float(np.mean(y[strong_neg] == 0)) if strong_neg.any() else np.nan
    n_pos = int(strong_pos.sum())
    n_neg = int(strong_neg.sum())

    return {
        "auc": auc,
        "coef": float(clf.coef_[0][0]),
        "intercept": float(clf.intercept_[0]),
        "n_total": int(valid.sum()),
        "hr_obi_pos": hr_pos,
        "n_obi_pos": n_pos,
        "hr_obi_neg": hr_neg,
        "n_obi_neg": n_neg,
        "obi_vals": obi_vals,
        "y": y,
    }


# ---------------------------------------------------------------------------
# Study: incremental R²
# ---------------------------------------------------------------------------

def incremental_r2_study(
    feat: pd.DataFrame, target_col: str, maxlags: int
) -> dict[str, float]:
    if target_col not in feat.columns:
        return {}

    tgt = feat[target_col].to_numpy(dtype=float)
    obi = feat["OBI1"].to_numpy(dtype=float)
    q = feat["Q_5s"].to_numpy(dtype=float)

    r2_obi = ols_hac_r2(obi.reshape(-1, 1), tgt, maxlags)
    r2_q = ols_hac_r2(q.reshape(-1, 1), tgt, maxlags)
    r2_joint = ols_hac_r2(np.column_stack([obi, q]), tgt, maxlags)

    n = int(np.sum(np.isfinite(tgt) & np.isfinite(obi) & np.isfinite(q)))
    split = int(0.60 * n)
    valid = np.isfinite(tgt) & np.isfinite(obi) & np.isfinite(q)
    t_v = tgt[valid]
    obi_v = obi[valid]
    q_v = q[valid]

    oos_r2_obi = ols_hac_r2(obi_v[split:].reshape(-1, 1), t_v[split:], maxlags) if len(t_v) > split else np.nan
    oos_r2_joint = ols_hac_r2(np.column_stack([obi_v[split:], q_v[split:]]), t_v[split:], maxlags) if len(t_v) > split else np.nan

    oos_inc = (oos_r2_joint - oos_r2_obi) if np.isfinite(oos_r2_joint) and np.isfinite(oos_r2_obi) else np.nan

    return {
        "r2_obi1": r2_obi,
        "r2_q5s": r2_q,
        "r2_joint": r2_joint,
        "incremental_r2_q": r2_joint - r2_obi if np.isfinite(r2_joint) and np.isfinite(r2_obi) else np.nan,
        "oos_r2_obi1": oos_r2_obi,
        "oos_r2_joint": oos_r2_joint,
        "oos_incremental_r2_q": oos_inc,
        "q_adds_gain_oos": bool(np.isfinite(oos_inc) and oos_inc >= OFI_INCREMENTAL_THRESHOLD),
    }


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def plot_lambda_scatter(
    feat: pd.DataFrame, q_col: str, outdir: str
) -> str:
    feat_copy = feat.copy()
    mid = feat_copy["mid"].to_numpy(dtype=float)
    delta_mid = np.empty(len(feat_copy), dtype=float)
    delta_mid[0] = np.nan
    delta_mid[1:] = mid[1:] - mid[:-1]
    q = feat_copy[q_col].to_numpy(dtype=float)

    valid = np.isfinite(q) & np.isfinite(delta_mid)
    q_v, dm_v = q[valid], delta_mid[valid]

    fig, ax = plt.subplots(figsize=(7, 5))
    if len(q_v) > 5000:
        ax.hexbin(q_v, dm_v, gridsize=50, cmap="Blues", mincnt=1)
    else:
        ax.scatter(q_v, dm_v, s=4, alpha=0.3, color="steelblue")
    ax.axhline(0, color="black", linewidth=0.6)
    ax.axvline(0, color="black", linewidth=0.6)
    ax.set_xlabel("Q_5s (signed flow, lots)")
    ax.set_ylabel("Δmid (IDR)")
    ax.set_title("Kyle's lambda — BUMI Jun 18")
    ax.grid(True, linewidth=0.4, alpha=0.5)
    plt.tight_layout()
    fname = "lambda_scatter_Q5s.png"
    fig.savefig(os.path.join(outdir, fname), dpi=110)
    plt.close(fig)
    return fname


def plot_obi_trade_sign_bar(
    obi_vals: np.ndarray, y: np.ndarray, outdir: str
) -> str:
    valid = np.isfinite(obi_vals)
    ov, yv = obi_vals[valid], y[valid]
    decile_edges = np.percentile(ov, np.linspace(0, 100, 11))
    decile_edges = np.unique(decile_edges)
    if len(decile_edges) < 3:
        return ""

    bin_idx = np.digitize(ov, decile_edges[1:-1])
    n_bins = len(decile_edges) - 1
    centers = np.empty(n_bins)
    buy_fracs = np.empty(n_bins)
    for b in range(n_bins):
        mask = bin_idx == b
        centers[b] = np.mean(ov[mask]) if mask.any() else np.nan
        buy_fracs[b] = np.mean(yv[mask]) if mask.any() else np.nan

    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(np.arange(n_bins), buy_fracs, color="steelblue", alpha=0.75)
    ax.axhline(0.5, color="tomato", linewidth=1.0, linestyle="--", label="0.5 baseline")
    ax.set_xticks(np.arange(n_bins))
    ax.set_xticklabels(
        [f"{c:.3f}" for c in centers], rotation=45, ha="right", fontsize=7
    )
    ax.set_xlabel("OBI1 decile centre")
    ax.set_ylabel("Fraction buy trades")
    ax.set_title("OBI1 → trade sign — BUMI Jun 18")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", linewidth=0.4, alpha=0.5)
    plt.tight_layout()
    fname = "obi_trade_sign_bar.png"
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
    lambda_results: dict[str, dict[str, float]],
    logit_result: dict[str, Any],
    incr_results: dict[str, dict[str, Any]],
    n_feat: int,
    n_trades: int,
    symbol_date: str,
    png_paths: list[str],
) -> str:
    lines: list[str] = []
    lines += [
        f"# Signed Trade Flow Study — {symbol_date}",
        "",
        "---",
        "",
        "## Dataset",
        "",
        f"- Feature rows (continuous session): {n_feat:,}",
        f"- Trade rows (continuous session): {n_trades:,}",
        "",
        "---",
        "",
        "## Kyle's Lambda",
        "",
        "Estimated via OLS Δmid ~ Q_window (Newey-West HAC SE). "
        "λ scaled to IDR per 100 lots.",
        "",
        "| window | λ (IDR/100 lots) | CI 95% low | CI 95% high | t-stat |",
        "|--------|-----------------|------------|-------------|--------|",
    ]
    for w_label, lr in lambda_results.items():
        lines.append(
            f"| {w_label} "
            f"| {_fmt(lr.get('lambda_per_100lots'))} "
            f"| {_fmt(lr.get('ci_lo_per_100lots'))} "
            f"| {_fmt(lr.get('ci_hi_per_100lots'))} "
            f"| {_fmt(lr.get('t_stat'))} |"
        )

    lines += [
        "",
        "---",
        "",
        "## OBI1 → Trade Sign (Logistic Regression)",
        "",
    ]
    if logit_result:
        lines += [
            f"- N observations: {logit_result.get('n_total', 0):,}",
            f"- AUC: {_fmt(logit_result.get('auc'))}",
            f"- Logit coefficient on OBI1: {_fmt(logit_result.get('coef'))}",
            f"- Intercept: {_fmt(logit_result.get('intercept'))}",
            f"- Hit-rate when OBI1 > 0.3 (n={logit_result.get('n_obi_pos', 0)}): {_fmt(logit_result.get('hr_obi_pos'))}",
            f"- Hit-rate when OBI1 < -0.3 (n={logit_result.get('n_obi_neg', 0)}): {_fmt(logit_result.get('hr_obi_neg'))} (fraction selling)",
        ]
    else:
        lines.append("Insufficient data for logistic regression.")

    lines += [
        "",
        "---",
        "",
        "## Incremental R²: OBI1 + Q_5s",
        "",
        "| horizon | R²(OBI1) | R²(Q_5s) | R²(joint) | ΔR²(Q) | OOS R²(OBI1) | OOS R²(joint) | OOS ΔR²(Q) | Q adds ≥5pp OOS |",
        "|---------|---------|---------|----------|-------|------------|-------------|-----------|----------------|",
    ]
    for h_label, ir in incr_results.items():
        if not ir:
            continue
        lines.append(
            f"| {h_label} "
            f"| {_fmt(ir.get('r2_obi1'))} "
            f"| {_fmt(ir.get('r2_q5s'))} "
            f"| {_fmt(ir.get('r2_joint'))} "
            f"| {_fmt(ir.get('incremental_r2_q'))} "
            f"| {_fmt(ir.get('oos_r2_obi1'))} "
            f"| {_fmt(ir.get('oos_r2_joint'))} "
            f"| {_fmt(ir.get('oos_incremental_r2_q'))} "
            f"| {'YES' if ir.get('q_adds_gain_oos') else 'NO'} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Figures",
        "",
    ]
    for p in sorted(set(png_paths)):
        if p:
            lines.append(f"![{p}]({p})")
    lines.append("")
    lines += [
        "---",
        "",
        "## Caveats",
        "",
        "- One name, one day — feasibility gate only.",
        "- Kyle's lambda assumes linear price impact; real impact is concave at high volumes.",
        "- Trade sign is pre-assigned by the exchange (B/S); the logistic model tests whether the "
          "book already reflects the direction before the tape prints.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_study(feat_path: str, trades_path: str, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)

    feat = load_features(feat_path)
    trades = load_trades(trades_path)

    feat = clean_features(feat)
    feat = add_mid_obi1(feat)
    feat = feat[_in_continuous_session(feat["_wib_dt"])].reset_index(drop=True)

    trades_sess = trades[_in_continuous_session(trades["_wib_dt"])].reset_index(drop=True)

    symbol_date = ""
    if len(feat) > 0:
        dt0 = feat["_wib_dt"].iloc[0]
        symbol_date = dt0.strftime("%Y-%m-%d") if not pd.isna(dt0) else ""

    print(f"Feature rows (session): {len(feat):,}")
    print(f"Trade rows (session): {len(trades_sess):,}")

    feat = add_signed_flow(feat, trades_sess, FLOW_WINDOWS_S)
    feat = add_forward_returns(feat, HORIZONS_S)

    row_rate = max(1, int(len(feat) / max(1, (feat["recv_ns"].iloc[-1] - feat["recv_ns"].iloc[0]) / 1e9)))

    lambda_results: dict[str, dict[str, float]] = {}
    for w in FLOW_WINDOWS_S:
        maxlags = min(200, max(5, w * row_rate))
        lambda_results[f"Q_{w}s"] = kyle_lambda(feat, f"Q_{w}s", maxlags)
        lam = lambda_results[f"Q_{w}s"].get("lambda_per_100lots", np.nan)
        print(f"  Kyle λ Q_{w}s = {lam:.4f} IDR/100lots" if np.isfinite(lam) else f"  Kyle λ Q_{w}s = —")

    print("Running OBI → trade sign logistic …")
    logit_result = obi_to_trade_sign(feat, trades_sess)

    incr_results: dict[str, dict[str, Any]] = {}
    for h in HORIZONS_S:
        maxlags = min(200, max(5, h * row_rate))
        incr_results[f"{h}s"] = incremental_r2_study(feat, f"fwd_mid_ret_{h}s", maxlags)

    png_paths: list[str] = []
    print("Plotting lambda scatter …")
    png_paths.append(plot_lambda_scatter(feat, "Q_5s", outdir))

    if logit_result and "obi_vals" in logit_result:
        print("Plotting OBI→trade-sign bar …")
        p = plot_obi_trade_sign_bar(logit_result["obi_vals"], logit_result["y"], outdir)
        if p:
            png_paths.append(p)
        # Remove numpy arrays from logit_result before passing to report
        logit_report = {k: v for k, v in logit_result.items() if k not in ("obi_vals", "y")}
    else:
        logit_report = logit_result

    report = build_report(
        lambda_results, logit_report, incr_results,
        len(feat), len(trades_sess), symbol_date, png_paths
    )
    report_path = os.path.join(outdir, "signed_flow_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport written → {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Signed trade flow + Kyle's lambda + OBI→trade-sign study."
    )
    p.add_argument("--features", required=True, metavar="PATH")
    p.add_argument("--trades", required=True, metavar="PATH")
    p.add_argument(
        "--outdir",
        default=os.path.join(SCRIPT_DIR, "out"),
        metavar="DIR",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    for label, path in [("features", args.features), ("trades", args.trades)]:
        if not os.path.isfile(path):
            print(f"ERROR: {label} CSV not found: {path}", file=sys.stderr)
            sys.exit(1)
    print(f"Loading features: {args.features} …")
    print(f"Loading trades:   {args.trades} …")
    run_study(args.features, args.trades, args.outdir)


if __name__ == "__main__":
    main()
