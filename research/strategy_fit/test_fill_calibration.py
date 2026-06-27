"""
strategy_fit/test_fill_calibration.py — TDD tests for the fill & impact
calibration harness (plan 15).

Validates strategy_fit/fill_calibration.py:
  - compute_spread_depth: spread_bps + L1/L5 depth columns from a book DataFrame
  - time_weighted_median: dwell-weighted median of a series
  - detect_impact_events: directional aggressor-run detection + realized impact
  - fit_sqrt_impact: intercept-free OLS of the square-root Almgren law
  - liquidity_bucket: ADV → bucket label
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.fill_calibration import (
    IMPACT_HORIZON_NS,
    MIN_IMPACT_OBS,
    RUN_DEPTH_FRACTION,
    ImpactObs,
    compute_spread_depth,
    detect_impact_events,
    fit_sqrt_impact,
    liquidity_bucket,
    time_weighted_median,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _book_row(recv_ns: int, bid_px: float, ask_px: float, bid_vol: int, ask_vol: int) -> dict:
    """One features row with a flat 5-level book (levels share px/vol for simplicity)."""
    row = {
        "recv_ns": recv_ns,
        "iso_wib": "2026-06-18T09:30:00.000",
    }
    for i in range(5):
        row[f"bid_px_{i}"] = bid_px - i
        row[f"ask_px_{i}"] = ask_px + i
        row[f"bid_vol_{i}"] = bid_vol
        row[f"ask_vol_{i}"] = ask_vol
    return row


def _book_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["_wib_dt"] = pd.to_datetime(df["iso_wib"], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# compute_spread_depth
# ---------------------------------------------------------------------------

def test_spread_bps_basic() -> None:
    # bid=100, ask=102 -> mid=101, spread=2 -> 2/101*1e4 ≈ 198.02 bps
    df = _book_df([_book_row(0, 100.0, 102.0, 50, 40)])
    out = compute_spread_depth(df)
    assert out["spread_bps"].iloc[0] == pytest.approx(2.0 / 101.0 * 1e4, rel=1e-9)


def test_depth_l1_and_l5() -> None:
    df = _book_df([_book_row(0, 100.0, 102.0, 50, 40)])
    out = compute_spread_depth(df)
    # L1 = bid_vol_0 + ask_vol_0
    assert out["depth_l1"].iloc[0] == pytest.approx(90.0)
    # L5 = sum of all 5 bid + 5 ask vols (flat book => 5*50 + 5*40)
    assert out["depth_l5"].iloc[0] == pytest.approx(5 * 50 + 5 * 40)


def test_mid_column() -> None:
    df = _book_df([_book_row(0, 100.0, 102.0, 50, 40)])
    out = compute_spread_depth(df)
    assert out["mid"].iloc[0] == pytest.approx(101.0)


# ---------------------------------------------------------------------------
# time_weighted_median
# ---------------------------------------------------------------------------

def test_time_weighted_median_dwell_dominates() -> None:
    # value 10 held for a long time, value 1000 a single instant -> median ~ 10
    values = np.array([10.0, 10.0, 1000.0])
    # dwell weights derived from timestamps: 0->100 (10), 100->200 (10), 200->201 (1000)
    ts = np.array([0.0, 100.0, 200.0])
    twm = time_weighted_median(values, ts)
    assert twm == pytest.approx(10.0)


def test_time_weighted_median_equal_weights() -> None:
    values = np.array([1.0, 2.0, 3.0])
    ts = np.array([0.0, 1.0, 2.0])
    # equal dwell on the first two; standard median of weighted is 2.0
    twm = time_weighted_median(values, ts)
    assert twm == pytest.approx(2.0)


def test_time_weighted_median_single_value() -> None:
    assert time_weighted_median(np.array([7.0]), np.array([0.0])) == pytest.approx(7.0)


def test_time_weighted_median_empty() -> None:
    assert np.isnan(time_weighted_median(np.array([]), np.array([])))


# ---------------------------------------------------------------------------
# detect_impact_events
# ---------------------------------------------------------------------------

def _features_for_impact(start_ns: int, base_mid: float) -> pd.DataFrame:
    """A book series: starts at base_mid, then rises after the run window."""
    rows = []
    # pre-run book at base_mid
    rows.append(_book_row(start_ns, base_mid - 1.0, base_mid + 1.0, 100, 100))
    # book ~30s+ later, mid moved up by 10
    later = start_ns + IMPACT_HORIZON_NS + 1_000_000
    rows.append(_book_row(later, base_mid + 9.0, base_mid + 11.0, 100, 100))
    return compute_spread_depth(_book_df(rows))


def test_detect_impact_event_simple_buy_run() -> None:
    start_ns = 1_000_000_000
    base_mid = 1000.0
    feats = _features_for_impact(start_ns, base_mid)
    # median L1 small so a modest run clears the >10% threshold
    median_l1 = 100.0
    # a directional B run: 3 consecutive buys, total 200 lots (> 10% of 100)
    trades = pd.DataFrame(
        {
            "recv_ns": [start_ns + 10, start_ns + 20, start_ns + 30],
            "price": [1001.0, 1001.0, 1002.0],
            "vol": [80, 80, 40],
            "side": ["B", "B", "B"],
        }
    )
    adv = 1_000_000_000.0  # 1B IDR ADV
    events = detect_impact_events(trades, feats, median_l1, adv)
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, ImpactObs)
    assert ev.side == "B"
    # mid moved 1000 -> 1010 = +10 -> 10/1000*1e4 = 100 bps
    assert ev.observed_impact_bps == pytest.approx(100.0, rel=1e-6)
    assert ev.participation_rate > 0.0


def test_detect_impact_skips_subthreshold_runs() -> None:
    start_ns = 1_000_000_000
    feats = _features_for_impact(start_ns, 1000.0)
    median_l1 = 100.0
    # tiny run: 5 lots total < 10% of 100 = 10 lots
    trades = pd.DataFrame(
        {
            "recv_ns": [start_ns + 10, start_ns + 20],
            "price": [1001.0, 1001.0],
            "vol": [2, 3],
            "side": ["B", "B"],
        }
    )
    events = detect_impact_events(trades, feats, median_l1, 1e9)
    assert events == []


def test_detect_impact_deoverlaps_windows() -> None:
    start_ns = 1_000_000_000
    base_mid = 1000.0
    # build a longer feature series so both runs have a forward mid
    rows = [_book_row(start_ns, base_mid - 1, base_mid + 1, 100, 100)]
    rows.append(_book_row(start_ns + IMPACT_HORIZON_NS + 1000, base_mid + 9, base_mid + 11, 100, 100))
    rows.append(
        _book_row(
            start_ns + 2 * IMPACT_HORIZON_NS + 5000,
            base_mid + 19,
            base_mid + 21,
            100,
            100,
        )
    )
    feats = compute_spread_depth(_book_df(rows))
    median_l1 = 100.0
    # Two runs: second starts BEFORE first run's 30s window closes -> overlap, dropped.
    trades = pd.DataFrame(
        {
            "recv_ns": [start_ns + 10, start_ns + 1_000_000],  # both within first 30s window
            "price": [1001.0, 1001.0],
            "vol": [200, 200],
            "side": ["B", "S"],
        }
    )
    events = detect_impact_events(trades, feats, median_l1, 1e9)
    # only the first run survives de-overlap
    assert len(events) == 1


# ---------------------------------------------------------------------------
# fit_sqrt_impact
# ---------------------------------------------------------------------------

def test_fit_sqrt_impact_recovers_coeff() -> None:
    # Construct y = 2*coeff*vol*sqrt(p) exactly -> fit should recover coeff.
    coeff_true = 0.7
    daily_vol_bps = 150.0
    parts = np.array([0.01, 0.04, 0.09, 0.16, 0.25])
    obs = [
        ImpactObs(
            side="B",
            mid_before=1000.0,
            mid_after=1000.0,
            observed_impact_bps=2.0 * coeff_true * daily_vol_bps * np.sqrt(p),
            participation_rate=p,
            event_notional_idr=1.0,
            start_ns=i,
            end_ns=i,
        )
        for i, p in enumerate(parts)
    ]
    coeff, r2, eff_n = fit_sqrt_impact(obs, daily_vol_bps)
    assert coeff == pytest.approx(coeff_true, rel=1e-6)
    assert r2 == pytest.approx(1.0, abs=1e-9)
    assert eff_n == len(parts)


def test_fit_sqrt_impact_empty() -> None:
    coeff, r2, eff_n = fit_sqrt_impact([], 100.0)
    assert coeff is None
    assert eff_n == 0


def test_fit_sqrt_impact_degenerate_vol() -> None:
    obs = [
        ImpactObs("B", 1000.0, 1001.0, 10.0, 0.01, 1.0, 0, 0),
    ]
    coeff, r2, eff_n = fit_sqrt_impact(obs, 0.0)
    assert coeff is None


# ---------------------------------------------------------------------------
# liquidity_bucket
# ---------------------------------------------------------------------------

def test_liquidity_bucket_boundaries() -> None:
    assert liquidity_bucket(200e9) == "high_liq"
    assert liquidity_bucket(100e9 + 1) == "high_liq"
    assert liquidity_bucket(50e9) == "mid_liq"
    assert liquidity_bucket(20e9) == "mid_liq"
    assert liquidity_bucket(20e9 - 1) == "low_liq"
    assert liquidity_bucket(0.0) == "low_liq"


def test_min_impact_obs_constant() -> None:
    assert MIN_IMPACT_OBS == 20
    assert 0.0 < RUN_DEPTH_FRACTION < 1.0
