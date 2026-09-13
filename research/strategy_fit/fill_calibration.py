"""
strategy_fit/fill_calibration.py — Plan 15 measured fill & impact calibration.

Calibrates the square-root Almgren temporary-impact law against captured book +
trade-tape data, replacing the global ``SQRT_IMPACT_COEFF=1.0`` assumption from
:mod:`strategy_fit.impact` with empirically measured, per-name and per-bucket
coefficients.

Pipeline
--------
1. Load ``{SYMBOL}_features.csv`` (L5 book snapshots) and
   ``{symbol}_trades*.csv`` (trade tape) for each available symbol.
2. Compute time-weighted spread_bps, depth_L1_lots, depth_L5_lots per name.
3. Detect directional aggressor runs in the tape and measure realized temporary
   impact 30s after the run ends.
4. Fit ``observed_impact_bps ≈ 2 * coeff * daily_vol_bps * sqrt(participation)``
   via intercept-free OLS, per name and pooled per liquidity bucket.
5. Write ``impact_coeffs.json`` and ``../out/fill_calibration_report.md``.

Run::

    cd research && python -m strategy_fit.fill_calibration --data-dir out_live/ticks

``--data-dir`` is resolved against the CURRENT WORKING DIRECTORY, not against
this file, so from ``research/`` it is ``out`` / ``out_live/ticks`` — not
``../out``, which resolves outside the repo and raises ``FileNotFoundError``.

The harness runs cleanly even when every name has fewer than ``MIN_IMPACT_OBS``
observations: such fits are emitted with ``confidence="provisional"`` and a
``coeff`` of ``null`` so callers fall back to bucket or global defaults.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import math
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from lib.loaders import clean, load_csv
from lib.session import _crosses_lunch, filter_continuous_session
from strategy_fit.impact import (
    HIGH_LIQ_ADV_IDR,
    MID_LIQ_ADV_IDR,
    SQRT_IMPACT_COEFF,
    compute_daily_vol_bps,
    compute_daily_vol_bps_from_daily_bars,
    liquidity_bucket,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_CALIBRATION_DAYS: int = 3
"""Plan proposed 5; lowered to 3 to match the 1-3 days of capture available."""

MIN_IMPACT_OBS: int = 20
"""Independent impact observations required before a fit is ``confirmed``."""

IMPACT_HORIZON_NS: int = 30 * 1_000_000_000
"""Temporary-impact measurement horizon: 30 seconds after the last run print."""

CALIBRATION_VOL_WINDOW_DAYS: int = 60
"""
Trailing daily bars used to estimate ``daily_vol_bps`` at calibration time.

Chosen deliberately, because window choice is not neutral here. The fit is
``observed_impact_bps ~ 2 * coeff * daily_vol_bps * sqrt(participation)``, so
``coeff`` is inversely proportional to this estimate — a window that misreads
volatility by 4x misreads every coefficient by 4x (plan 18 §4.3a).

60 trading days (~3 months) sits between two failure modes:

- **Too short** — the capture panel itself is 5 days, giving 4 close-to-close
  returns. That is what produced defect 3: PTRO read 79 bps on a week it
  realized 241-470 bps intraday every day, because it happened to close where
  it opened.
- **Too long** — a multi-year median averages across regime changes. The same
  trap is already recorded for ADV, where PTRO's modelled impact ranged
  3.9 -> 33.6 bps purely by window choice (roadmap ADV window table).

60 days is long enough to be a stable estimate and short enough to describe the
regime the capture actually sampled. Anything that changes it changes every
coefficient, so change it deliberately and re-run, never as a default tweak.
"""

RUN_DEPTH_FRACTION: float = 0.10
"""A directional run qualifies once accumulated volume > 10% of median L1 depth.

Book depth and tape volume come from the same feed and are in the same unit
(shares), so this comparison is unit-consistent regardless of the unit itself.
"""

GLOBAL_DEFAULT_COEFF: float = SQRT_IMPACT_COEFF

_BUCKETS: tuple[str, ...] = ("high_liq", "mid_liq", "low_liq")

# NOTE (plan 18 §4.2, fixed 2026-09-13). This module previously multiplied trade
# notional by a LOTS_PER_SHARE_UNIT = 100 constant, on the premise that the
# running-trade tape reports ``vol`` in lots. It does not — the tape is already
# in SHARES. Verified on BUMI 2026-06-18 (47,976 prints): sum(vol × price) =
# Rp 415.09 B, the right order of magnitude against the screener's Rp 668.96 B
# median, where ×100 gives Rp 41,509 B — more than the whole exchange trades in
# a day. The factor cancelled inside ``participation`` (it was applied to both
# the event notional and the ADV), so no fitted coefficient was ever wrong; what
# it broke was ``liquidity_bucket``, whose thresholds are ABSOLUTE. An effective
# Rp 1 B cutoff filed every name as ``high_liq``, which is why ``mid_liq`` and
# ``low_liq`` carried eff_N = 0. If you reintroduce a lot-size factor here, it
# must go in both places or every coefficient moves by sqrt(100).


@dataclass(frozen=True)
class ImpactObs:
    """One de-overlapped temporary-impact observation."""

    side: str
    mid_before: float
    mid_after: float
    observed_impact_bps: float
    participation_rate: float
    event_notional_idr: float
    start_ns: int
    end_ns: int


# ---------------------------------------------------------------------------
# Book features
# ---------------------------------------------------------------------------

def compute_spread_depth(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``mid``, ``spread_bps``, ``depth_l1``, ``depth_l5`` to a book frame."""
    out = df.copy()
    bid0 = out["bid_px_0"].astype(float)
    ask0 = out["ask_px_0"].astype(float)
    mid = (bid0 + ask0) / 2.0
    out["mid"] = mid
    out["spread_bps"] = (ask0 - bid0) / mid * 1e4
    out["depth_l1"] = out["bid_vol_0"].astype(float) + out["ask_vol_0"].astype(float)
    bid_cols = [f"bid_vol_{i}" for i in range(5)]
    ask_cols = [f"ask_vol_{i}" for i in range(5)]
    out["depth_l5"] = out[bid_cols + ask_cols].astype(float).sum(axis=1)
    return out


def time_weighted_median(values: np.ndarray, timestamps: np.ndarray) -> float:
    """
    Dwell-weighted median of *values* sampled at *timestamps* (nanoseconds).

    Each sample's weight is the time until the next sample; the final sample
    inherits the median dwell so it is not silently dropped.
    """
    values = np.asarray(values, dtype=float)
    timestamps = np.asarray(timestamps, dtype=float)
    if values.size == 0:
        return float("nan")
    if values.size == 1:
        return float(values[0])

    dwell = np.diff(timestamps)
    dwell = np.append(dwell, np.median(dwell) if dwell.size else 1.0)
    weights = np.clip(dwell, 0.0, None)

    finite = np.isfinite(values) & (weights > 0)
    if not finite.any():
        return float("nan")
    v = values[finite]
    w = weights[finite]

    order = np.argsort(v)
    v = v[order]
    w = w[order]
    cum = np.cumsum(w)
    half = cum[-1] / 2.0
    idx = int(np.searchsorted(cum, half, side="left"))
    idx = min(idx, v.size - 1)
    return float(v[idx])


# ---------------------------------------------------------------------------
# Impact event detection
# ---------------------------------------------------------------------------

def _mid_after(features: pd.DataFrame, target_ns: int) -> float:
    """First mid at or after *target_ns*; NaN when no such snapshot exists."""
    recv = features["recv_ns"].to_numpy()
    pos = int(np.searchsorted(recv, target_ns, side="left"))
    if pos >= len(features):
        return float("nan")
    return float(features["mid"].iloc[pos])


def _mid_at_or_before(features: pd.DataFrame, target_ns: int) -> float:
    """Last mid at or before *target_ns* (prevailing book); NaN if none precede."""
    recv = features["recv_ns"].to_numpy()
    pos = int(np.searchsorted(recv, target_ns, side="right")) - 1
    if pos < 0:
        return float("nan")
    return float(features["mid"].iloc[pos])


def _wib_at(features: pd.DataFrame, recv_ns: int) -> pd.Timestamp:
    recv = features["recv_ns"].to_numpy()
    pos = int(np.searchsorted(recv, recv_ns, side="left"))
    pos = min(pos, len(features) - 1)
    return features["_wib_dt"].iloc[pos]


def detect_impact_events(
    trades: pd.DataFrame,
    features: pd.DataFrame,
    median_l1_shares: float,
    adv_notional_idr: float,
) -> list[ImpactObs]:
    """
    Detect directional aggressor runs and measure their realized temporary impact.

    A run is a maximal sequence of same-side prints whose accumulated volume
    exceeds ``RUN_DEPTH_FRACTION`` of *median_l1_shares*.  Impact is measured as
    the absolute mid move from the run's first print to the book 30s after its
    last print.  Runs crossing the lunch break or lacking a forward snapshot are
    skipped; observations whose 30s window overlaps the prior one are dropped.
    """
    if trades.empty or features.empty or median_l1_shares <= 0 or adv_notional_idr <= 0:
        return []

    threshold_shares = RUN_DEPTH_FRACTION * median_l1_shares
    trades = trades.sort_values("recv_ns").reset_index(drop=True)

    observations: list[ImpactObs] = []
    prev_window_end_ns = -1

    i = 0
    n = len(trades)
    while i < n:
        side = trades["side"].iloc[i]
        j = i
        acc_shares = 0.0
        while j < n and trades["side"].iloc[j] == side:
            acc_shares += float(trades["vol"].iloc[j])
            j += 1
        run = trades.iloc[i:j]
        i = j

        if acc_shares <= threshold_shares:
            continue

        start_ns = int(run["recv_ns"].iloc[0])
        end_ns = int(run["recv_ns"].iloc[-1])
        target_ns = end_ns + IMPACT_HORIZON_NS

        start_wib = _wib_at(features, start_ns)
        if _crosses_lunch(start_ns, target_ns, start_wib):
            continue

        mid_before = _mid_at_or_before(features, start_ns)
        mid_after = _mid_after(features, target_ns)
        if not (math.isfinite(mid_before) and math.isfinite(mid_after)) or mid_before <= 0:
            continue

        if start_ns <= prev_window_end_ns:
            continue

        notional = float((run["vol"] * run["price"]).sum())
        participation = notional / adv_notional_idr
        impact_bps = abs(mid_after - mid_before) / mid_before * 1e4

        observations.append(
            ImpactObs(
                side=str(side),
                mid_before=mid_before,
                mid_after=mid_after,
                observed_impact_bps=impact_bps,
                participation_rate=participation,
                event_notional_idr=notional,
                start_ns=start_ns,
                end_ns=end_ns,
            )
        )
        prev_window_end_ns = target_ns

    return observations


# ---------------------------------------------------------------------------
# OLS fit
# ---------------------------------------------------------------------------

def fit_sqrt_impact(
    observations: list[ImpactObs],
    daily_vol_bps: float,
) -> tuple[float | None, float, int]:
    """
    Intercept-free OLS of the square-root law.

    Fits ``y = x * (2 * coeff)`` where ``y = observed_impact_bps`` and
    ``x = daily_vol_bps * sqrt(participation_rate)``; equivalently regresses
    ``observed/(2*vol)`` on ``sqrt(participation)``.  Returns ``(coeff, r2,
    eff_n)``; ``coeff`` is ``None`` for degenerate inputs (no obs, non-positive
    vol).
    """
    eff_n = len(observations)
    if eff_n == 0 or daily_vol_bps <= 0:
        return None, 0.0, eff_n

    x = np.array([math.sqrt(max(o.participation_rate, 0.0)) for o in observations])
    y = np.array([o.observed_impact_bps / (2.0 * daily_vol_bps) for o in observations])

    denom = float(np.sum(x * x))
    if denom <= 0:
        return None, 0.0, eff_n

    coeff = float(np.sum(x * y) / denom)

    y_hat = coeff * x
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum(y**2))  # intercept-free: total relative to zero
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return coeff, r2, eff_n


def _confidence(eff_n: int) -> str:
    return "confirmed" if eff_n >= MIN_IMPACT_OBS else "provisional"


# ---------------------------------------------------------------------------
# Per-symbol calibration
# ---------------------------------------------------------------------------

@dataclass
class NameResult:
    symbol: str
    bucket: str
    adv_notional_idr: float
    daily_vol_bps: float
    spread_bps: float
    depth_l1: float
    depth_l5: float
    n_days: int
    coeff: float | None
    r2: float
    eff_n: int
    observations: list[ImpactObs]
    vol_source: str = "daily_bars"
    """Which estimator produced ``daily_vol_bps`` — never mix the two silently."""


def _adv_from_trades(trades: pd.DataFrame) -> float:
    """Median daily notional turnover from the trade tape (IDR)."""
    if trades.empty:
        return 0.0
    df = trades.copy()
    df["_date"] = df["_wib_dt"].dt.date
    df["_notional"] = df["vol"] * df["price"]
    daily = df.groupby("_date")["_notional"].sum()
    return float(daily.median()) if len(daily) else 0.0


CONTINUOUS_SESSION_MINUTES: float = (3 * 60) + (2 * 60 + 45)
"""JSX continuous-session minutes: AM 09:00–12:00 (180) + PM 13:30–16:15 (165)."""


def _intraday_vol_bps(features: pd.DataFrame) -> float:
    """
    Single-day fallback: scale 1-minute mid log-return stdev to a full session.

    With only one capture day, cross-day close-to-close vol is undefined.  We
    resample mid to 1-minute bars, take the log-return stdev, then scale by
    sqrt(session_minutes) to express it as a daily-equivalent figure in bps.
    """
    df = features.dropna(subset=["mid", "_wib_dt"]).copy()
    if len(df) < 3:
        return 0.0
    bars = df.set_index("_wib_dt")["mid"].resample("1min").last().dropna()
    if len(bars) < 3:
        return 0.0
    log_rets = np.diff(np.log(bars.to_numpy(dtype=float)))
    log_rets = log_rets[np.isfinite(log_rets)]
    if len(log_rets) < 2:
        return 0.0
    per_min = float(np.std(log_rets, ddof=1))
    return per_min * math.sqrt(CONTINUOUS_SESSION_MINUTES) * 1e4


def resolve_daily_vol_bps(
    features: pd.DataFrame | None,
    daily_bars: pd.DataFrame | None,
    as_of: str | None = None,
) -> tuple[float, str]:
    """
    Resolve ``daily_vol_bps`` for a calibration fit, and say where it came from.

    Returns ``(vol_bps, source)`` where *source* is ``"daily_bars"`` or
    ``"intraday_proxy"``.

    Preference order:

    1. ``compute_daily_vol_bps_from_daily_bars`` over the trailing
       ``CALIBRATION_VOL_WINDOW_DAYS`` bars at or before *as_of*.
    2. The single-session intraday realized proxy, medianed across capture days.

    **Why daily bars win.** This is the same estimator, on the same kind of
    input, that the consumer uses: the strategy grid prices its hurdle through
    ``compute_impact_bps_from_daily_df``, which takes volatility from
    ``compute_daily_vol_bps_from_daily_bars``. Calibrating against a different
    estimator silently multiplies every coefficient by the ratio between the
    two, and that ratio has nothing to do with market impact.

    **What this replaces.** The previous helper estimated volatility from the
    capture panel and switched estimator on panel length — intraday proxy at one
    day, close-to-close at two or more. The 2026-06-27 run was single-day and
    the Track C panel is five days, so the two runs were never comparable, and
    four close-to-close returns is a hopeless estimator besides (plan 18 §4.3a).

    *as_of* caps the window so a calibration dated T never sees T+1.
    """
    if daily_bars is not None and len(daily_bars) > 0:
        bars = daily_bars
        if as_of is not None and "date" in bars.columns:
            bars = bars[bars["date"].astype(str) <= str(as_of)]
        bars = bars.tail(CALIBRATION_VOL_WINDOW_DAYS)
        if len(bars) >= 2:
            vol = compute_daily_vol_bps_from_daily_bars(bars)
            if math.isfinite(vol) and vol > 0.0:
                return float(vol), "daily_bars"

    if features is None or features.empty:
        return 0.0, "intraday_proxy"

    per_day = [
        _intraday_vol_bps(day_feats)
        for _, day_feats in features.groupby(features["_wib_dt"].dt.date)
    ]
    per_day = [v for v in per_day if math.isfinite(v) and v > 0.0]
    if not per_day:
        return 0.0, "intraday_proxy"
    return float(np.median(per_day)), "intraday_proxy"


def _detect_impact_events_multiday(
    trades: pd.DataFrame,
    features: pd.DataFrame,
    median_l1_shares: float,
    adv_notional_idr: float,
) -> list[ImpactObs]:
    """
    Run impact detection per calendar day and pool the observations.

    Detecting per day prevents a run's 30s forward window from reaching across
    an overnight gap into the next session's opening book, which would fabricate
    a huge spurious impact.
    """
    if trades.empty or features.empty:
        return []

    trade_dates = trades["_wib_dt"].dt.date
    feat_dates = features["_wib_dt"].dt.date

    pooled: list[ImpactObs] = []
    for day in sorted(set(trade_dates.unique())):
        day_trades = trades[trade_dates == day]
        day_feats = features[feat_dates == day]
        if day_trades.empty or day_feats.empty:
            continue
        pooled.extend(
            detect_impact_events(day_trades, day_feats, median_l1_shares, adv_notional_idr)
        )
    return pooled


def calibrate_symbol(
    symbol: str,
    features_raw: pd.DataFrame,
    trades: pd.DataFrame,
    daily_bars: pd.DataFrame | None = None,
) -> NameResult:
    features = filter_continuous_session(clean(compute_spread_depth(features_raw)))
    if not trades.empty:
        trades = filter_continuous_session(trades)

    twm_spread = time_weighted_median(
        features["spread_bps"].to_numpy(), features["recv_ns"].to_numpy()
    )
    twm_l1 = time_weighted_median(features["depth_l1"].to_numpy(), features["recv_ns"].to_numpy())
    twm_l5 = time_weighted_median(features["depth_l5"].to_numpy(), features["recv_ns"].to_numpy())

    adv = _adv_from_trades(trades)
    n_days = int(features["_wib_dt"].dt.date.nunique())
    as_of = str(features["_wib_dt"].dt.date.max()) if n_days else None
    vol_bps, vol_source = resolve_daily_vol_bps(features, daily_bars, as_of=as_of)
    if vol_source != "daily_bars":
        logger.warning(
            "%s: no usable daily bars; volatility falls back to the intraday proxy. "
            "Its coefficient is NOT on the same scale as the rest and must not be "
            "pooled into a bucket without saying so (plan 18 §4.3a).",
            symbol,
        )

    events = _detect_impact_events_multiday(trades, features, twm_l1, adv)
    coeff, r2, eff_n = fit_sqrt_impact(events, vol_bps)

    return NameResult(
        symbol=symbol,
        bucket=liquidity_bucket(adv),
        adv_notional_idr=adv,
        daily_vol_bps=vol_bps,
        spread_bps=twm_spread,
        depth_l1=twm_l1,
        depth_l5=twm_l5,
        n_days=n_days,
        coeff=coeff if eff_n >= MIN_IMPACT_OBS else None,
        r2=r2,
        eff_n=eff_n,
        observations=events,
        vol_source=vol_source,
    )


# ---------------------------------------------------------------------------
# Data discovery & orchestration
# ---------------------------------------------------------------------------

def _discover_pairs(data_dir: str) -> dict[str, tuple[list[str], list[str]]]:
    """
    Map SYMBOL → (sorted feature paths, sorted trade paths).

    Two layouts are supported, case-insensitive on symbol:

    - **Flat:** ``{data_dir}/{SYMBOL}_features.csv`` + ``{symbol}_trades*.csv``
      (one capture day per name).
    - **Per-symbol subdir:** ``{data_dir}/{SYMBOL}/{date}_features.csv`` +
      ``{date}_trades.csv`` (multiple capture days pooled per name).

    Subdir layout takes precedence when ``{data_dir}`` contains directories.
    """
    subdirs = [
        d for d in glob.glob(os.path.join(data_dir, "*")) if os.path.isdir(d)
    ]
    if subdirs:
        pairs: dict[str, tuple[list[str], list[str]]] = {}
        for d in subdirs:
            sym = os.path.basename(d.rstrip(os.sep)).upper()
            feats = sorted(glob.glob(os.path.join(d, "*_features.csv")))
            trades = sorted(glob.glob(os.path.join(d, "*_trades*.csv")))
            if feats:
                pairs[sym] = (feats, trades)
        return dict(sorted(pairs.items()))

    feat_paths = glob.glob(os.path.join(data_dir, "*_features.csv"))
    trade_paths = glob.glob(os.path.join(data_dir, "*_trades*.csv"))
    trades_by_sym: dict[str, list[str]] = {}
    for p in trade_paths:
        trades_by_sym.setdefault(os.path.basename(p).split("_")[0].upper(), []).append(p)

    flat: dict[str, tuple[list[str], list[str]]] = {}
    for fp in feat_paths:
        sym = os.path.basename(fp).split("_")[0].upper()
        flat[sym] = ([fp], sorted(trades_by_sym.get(sym, [])))
    return dict(sorted(flat.items()))


def _load_trades(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    for col in ("recv_ns", "price", "vol", "trade_num"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    df = df.dropna(subset=["recv_ns", "price", "vol", "side", "_wib_dt"])
    return df.sort_values("recv_ns").reset_index(drop=True)


def _bucket_fits(results: list[NameResult]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for bucket in _BUCKETS:
        pooled: list[ImpactObs] = []
        vols: list[float] = []
        for r in results:
            if r.bucket == bucket and r.eff_n >= MIN_IMPACT_OBS:
                pooled.extend(r.observations)
                vols.append(r.daily_vol_bps)
        pooled_vol = float(np.median(vols)) if vols else 0.0
        coeff, _r2, eff_n = fit_sqrt_impact(pooled, pooled_vol)
        out[bucket] = {
            "coeff": coeff if eff_n >= MIN_IMPACT_OBS else None,
            "eff_N": eff_n,
            "confidence": _confidence(eff_n),
        }
    return out


def build_coeffs(results: list[NameResult]) -> dict:
    return {
        "generated": _dt.date.today().isoformat(),
        "min_impact_obs": MIN_IMPACT_OBS,
        "global_default": GLOBAL_DEFAULT_COEFF,
        # Provenance. A coefficient is only meaningful against the volatility
        # estimator it was fitted with (plan 18 §4.3a); record it so a later
        # reader can tell whether two runs are comparable at all.
        "vol_window_days": CALIBRATION_VOL_WINDOW_DAYS,
        "vol_estimator": "compute_daily_vol_bps_from_daily_bars",
        "buckets": _bucket_fits(results),
        "per_name": {
            r.symbol: {
                "coeff": r.coeff,
                "eff_N": r.eff_n,
                "confidence": _confidence(r.eff_n),
                "bucket": r.bucket,
                "vol_bps": r.daily_vol_bps,
                "vol_source": r.vol_source,
            }
            for r in results
        },
    }


def merge_coeffs(existing: dict, fresh: dict) -> dict:
    """
    Merge a *fresh* calibration into an *existing* impact_coeffs payload.

    Per-name entries union (fresh wins on key collision).  Bucket entries prefer
    the fresh fit when it has observations (higher ``eff_N``); otherwise the
    existing bucket entry is retained.  Top-level scalars come from *fresh*.
    """
    if existing.get("vol_estimator") != fresh.get("vol_estimator") or existing.get(
        "vol_window_days"
    ) != fresh.get("vol_window_days"):
        logger.warning(
            "merge_coeffs: existing (%s/%s) and fresh (%s/%s) volatility provenance "
            "differ. coeff is inversely proportional to the volatility estimate, so "
            "merging across estimators pools numbers that are not on the same scale "
            "(plan 18 §4.3a). Prefer a clean re-run over a merge.",
            existing.get("vol_estimator"), existing.get("vol_window_days"),
            fresh.get("vol_estimator"), fresh.get("vol_window_days"),
        )

    merged = dict(fresh)
    merged["per_name"] = {**existing.get("per_name", {}), **fresh.get("per_name", {})}

    buckets: dict[str, dict] = dict(existing.get("buckets", {}))
    for name, fresh_b in fresh.get("buckets", {}).items():
        prev = buckets.get(name)
        if prev is None or fresh_b.get("eff_N", 0) >= prev.get("eff_N", 0):
            buckets[name] = fresh_b
    merged["buckets"] = buckets
    return merged


def _render_report(results: list[NameResult], coeffs: dict, low_data: bool) -> str:
    lines: list[str] = []
    lines.append("# Fill & Impact Calibration Report (Plan 15)")
    lines.append("")
    lines.append(f"Generated: {coeffs['generated']}")
    lines.append(f"Global default coeff: {coeffs['global_default']}")
    lines.append(f"Confirm threshold (MIN_IMPACT_OBS): {MIN_IMPACT_OBS}")
    lines.append(
        f"Volatility: `{coeffs.get('vol_estimator')}` over the trailing "
        f"{coeffs.get('vol_window_days')} daily bars at or before the last capture date."
    )
    lines.append("")
    lines.append(
        "> `coeff` is inversely proportional to `daily_vol_bps`, so it is only "
        "comparable against another run that used the SAME volatility estimator and "
        "window. Runs before 2026-09-13 estimated volatility from the capture panel "
        "instead, switching estimator at `n_days >= 2` — their coefficients are on a "
        "different scale and must not be compared or pooled with these. See plan 18 "
        "§4.3a."
    )
    lines.append("")
    if low_data:
        lines.append(
            f"> **Warning:** fewer than MIN_CALIBRATION_DAYS={MIN_CALIBRATION_DAYS} "
            "trading days available for one or more names; coefficients are "
            "provisional and callers should fall back to bucket/global defaults."
        )
        lines.append("")

    lines.append("## Per-name liquidity & impact")
    lines.append("")
    lines.append(
        "| Symbol | Bucket | ADV (IDR) | Spread (bps) | Depth L1 (shares) | "
        "Depth L5 (shares) | Daily vol (bps) | Vol source | Days | Impact obs | "
        "Coeff | R² | Confidence |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        coeff_s = "—" if r.coeff is None else f"{r.coeff:.4f}"
        lines.append(
            f"| {r.symbol} | {r.bucket} | {r.adv_notional_idr:,.0f} | "
            f"{r.spread_bps:.2f} | {r.depth_l1:,.0f} | {r.depth_l5:,.0f} | "
            f"{r.daily_vol_bps:.2f} | {r.vol_source} | {r.n_days} | {r.eff_n} | {coeff_s} | "
            f"{r.r2:.3f} | {_confidence(r.eff_n)} |"
        )
    lines.append("")

    fresh_syms = {r.symbol for r in results}
    prior = {s: e for s, e in coeffs.get("per_name", {}).items() if s not in fresh_syms}
    if prior:
        lines.append("## Previously calibrated names (merged)")
        lines.append("")
        lines.append("| Symbol | Bucket | Impact obs | Coeff | Confidence |")
        lines.append("|---|---|---|---|---|")
        for sym in sorted(prior):
            e = prior[sym]
            coeff_s = "—" if e.get("coeff") is None else f"{e['coeff']:.4f}"
            lines.append(
                f"| {sym} | {e.get('bucket', '—')} | {e.get('eff_N', 0)} | "
                f"{coeff_s} | {e.get('confidence', '—')} |"
            )
        lines.append("")

    lines.append("## Bucket-level fallback coefficients")
    lines.append("")
    lines.append("| Bucket | Coeff | eff_N | Confidence |")
    lines.append("|---|---|---|---|")
    for bucket in _BUCKETS:
        b = coeffs["buckets"][bucket]
        coeff_s = "—" if b["coeff"] is None else f"{b['coeff']:.4f}"
        lines.append(f"| {bucket} | {coeff_s} | {b['eff_N']} | {b['confidence']} |")
    lines.append("")

    total_obs = sum(r.eff_n for r in results)
    lines.append("## Notes")
    lines.append("")
    lines.append(
        f"- Total impact observations across all names: {total_obs}. "
        "Impact temporary horizon = 30s; runs crossing lunch/close are excluded; "
        "overlapping 30s windows are de-overlapped."
    )
    lines.append(
        "- `coeff=null` in `impact_coeffs.json` means the name had fewer than "
        f"MIN_IMPACT_OBS={MIN_IMPACT_OBS} observations; callers fall back to the "
        "bucket coefficient, then `global_default`."
    )
    lines.append(
        "- Daily vol (bps) is a close-to-close sample stdev over the captured "
        "window; on a short (≈5-day) window it is noisy and tends to overstate. "
        "Because the fit divides observed impact by `2·daily_vol_bps`, an "
        "overstated vol *deflates* the implied coeff — the measured coefficients "
        "are therefore a conservative (low) estimate, not an inflated one."
    )
    # §8 direction check: coeff in [0.2, 2.0]
    out_of_range = [
        r for r in results
        if r.coeff is not None and not (0.2 <= r.coeff <= 2.0)
    ]
    if out_of_range:
        lines.append("## Direction-check warnings (§8: coeff outside [0.2, 2.0])")
        lines.append("")
        lines.append(
            "> Plan-15 §8 direction check: a confirmed coeff outside [0.2, 2.0] "
            "flags a data-quality or event-detection problem, not a discovery."
        )
        lines.append("")
        for r in out_of_range:
            lines.append(
                f"- **{r.symbol}** coeff={r.coeff:.4f} (eff_N={r.eff_n}) — "
                "review event detection or extend capture window before using."
            )
        lines.append("")

    lines.append("")
    return "\n".join(lines)


DAILY_BARS_DIR: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "daily"
)
"""Where ``run`` looks for ``{SYMBOL}.parquet`` daily history."""


def _load_daily_bars(symbol: str, daily_dir: str) -> pd.DataFrame | None:
    """Daily bars for *symbol*, or ``None`` when there is no history on disk."""
    path = os.path.join(daily_dir, f"{symbol.upper()}.parquet")
    if not os.path.exists(path):
        return None
    try:
        return pd.read_parquet(path)
    except (OSError, ValueError) as exc:
        logger.warning("%s: daily bars unreadable (%s)", symbol, exc)
        return None


def run(
    data_dir: str,
    out_json: str,
    out_report: str,
    merge: bool = False,
    daily_dir: str = DAILY_BARS_DIR,
) -> dict:
    pairs = _discover_pairs(data_dir)
    if not pairs:
        raise FileNotFoundError(f"No *_features.csv found under {data_dir}")

    results: list[NameResult] = []
    low_data = False
    for sym, (feat_paths, trade_paths) in pairs.items():
        features_raw = pd.concat([load_csv(p) for p in feat_paths], ignore_index=True)
        if trade_paths:
            trades = pd.concat([_load_trades(p) for p in trade_paths], ignore_index=True)
            trades = trades.sort_values("recv_ns").reset_index(drop=True)
        else:
            trades = pd.DataFrame()
            logger.warning("%s: no trade tape found; impact obs will be 0", sym)
        res = calibrate_symbol(sym, features_raw, trades, _load_daily_bars(sym, daily_dir))
        if res.n_days < MIN_CALIBRATION_DAYS:
            low_data = True
        logger.info(
            "%s bucket=%s adv=%.3e vol_bps=%.2f(%s) obs=%d coeff=%s days=%d",
            sym, res.bucket, res.adv_notional_idr, res.daily_vol_bps, res.vol_source,
            res.eff_n, res.coeff, res.n_days,
        )
        results.append(res)

    coeffs = build_coeffs(results)
    if merge and os.path.exists(out_json):
        with open(out_json) as f:
            existing = json.load(f)
        coeffs = merge_coeffs(existing, coeffs)

    with open(out_json, "w") as f:
        json.dump(coeffs, f, indent=2)
        f.write("\n")

    os.makedirs(os.path.dirname(out_report), exist_ok=True)
    with open(out_report, "w") as f:
        f.write(_render_report(results, coeffs, low_data))

    return coeffs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Plan 15 fill & impact calibration")
    parser.add_argument("--data-dir", default=os.path.join(here, "..", "out"))
    parser.add_argument("--out-json", default=os.path.join(here, "impact_coeffs.json"))
    parser.add_argument(
        "--out-report", default=os.path.join(here, "..", "out", "fill_calibration_report.md")
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="merge per-name/bucket results into an existing --out-json instead of overwriting",
    )
    args = parser.parse_args()
    coeffs = run(args.data_dir, args.out_json, args.out_report, merge=args.merge)
    logger.info("Wrote %s and %s", args.out_json, args.out_report)
    logger.info("Buckets: %s", json.dumps(coeffs["buckets"]))


if __name__ == "__main__":
    main()
