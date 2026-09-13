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

import os

import numpy as np
import pandas as pd
import pytest

from strategy_fit.fill_calibration import (
    IMPACT_HORIZON_NS,
    MIN_IMPACT_OBS,
    RUN_DEPTH_FRACTION,
    ImpactObs,
    _discover_pairs,
    compute_spread_depth,
    detect_impact_events,
    fit_sqrt_impact,
    liquidity_bucket,
    merge_coeffs,
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


# ---------------------------------------------------------------------------
# _discover_pairs (flat vs per-symbol subdir layouts)
# ---------------------------------------------------------------------------

def test_discover_pairs_flat_layout(tmp_path) -> None:
    (tmp_path / "ITMG_features.csv").write_text("x")
    (tmp_path / "itmg_trades_jun18.csv").write_text("x")
    (tmp_path / "ANTM_features.csv").write_text("x")  # no trades
    pairs = _discover_pairs(str(tmp_path))
    assert set(pairs) == {"ITMG", "ANTM"}
    assert len(pairs["ITMG"][0]) == 1 and len(pairs["ITMG"][1]) == 1
    assert pairs["ANTM"][1] == []


def test_discover_pairs_subdir_layout_pools_days(tmp_path) -> None:
    ptro = tmp_path / "PTRO"
    ptro.mkdir()
    for d in ("2026-06-22", "2026-06-23", "2026-06-24"):
        (ptro / f"{d}_features.csv").write_text("x")
        (ptro / f"{d}_trades.csv").write_text("x")
    pairs = _discover_pairs(str(tmp_path))
    assert set(pairs) == {"PTRO"}
    feats, trades = pairs["PTRO"]
    assert len(feats) == 3 and len(trades) == 3
    # paths returned sorted so day order is deterministic
    assert feats == sorted(feats)


def test_discover_pairs_subdir_takes_precedence(tmp_path) -> None:
    # A stray flat file at top level is ignored once subdirs exist.
    (tmp_path / "STRAY_features.csv").write_text("x")
    sub = tmp_path / "PTRO"
    sub.mkdir()
    (sub / "2026-06-22_features.csv").write_text("x")
    pairs = _discover_pairs(str(tmp_path))
    assert set(pairs) == {"PTRO"}


# ---------------------------------------------------------------------------
# merge_coeffs
# ---------------------------------------------------------------------------

def test_merge_coeffs_unions_per_name() -> None:
    existing = {
        "generated": "2026-06-27",
        "global_default": 1.0,
        "buckets": {"high_liq": {"coeff": 0.47, "eff_N": 561, "confidence": "confirmed"}},
        "per_name": {"BUMI": {"coeff": 0.53, "eff_N": 108, "confidence": "confirmed", "bucket": "high_liq"}},
    }
    fresh = {
        "generated": "2026-06-28",
        "global_default": 1.0,
        "buckets": {"mid_liq": {"coeff": 0.6, "eff_N": 40, "confidence": "confirmed"}},
        "per_name": {"PTRO": {"coeff": 0.6, "eff_N": 50, "confidence": "confirmed", "bucket": "mid_liq"}},
    }
    merged = merge_coeffs(existing, fresh)
    assert set(merged["per_name"]) == {"BUMI", "PTRO"}
    assert "high_liq" in merged["buckets"] and "mid_liq" in merged["buckets"]
    assert merged["generated"] == "2026-06-28"  # top-level scalars from fresh


def test_merge_coeffs_fresh_bucket_wins_on_more_obs() -> None:
    existing = {"buckets": {"high_liq": {"coeff": 0.47, "eff_N": 100, "confidence": "confirmed"}}, "per_name": {}}
    fresh = {
        "generated": "x", "global_default": 1.0,
        "buckets": {"high_liq": {"coeff": 0.5, "eff_N": 300, "confidence": "confirmed"}},
        "per_name": {},
    }
    merged = merge_coeffs(existing, fresh)
    assert merged["buckets"]["high_liq"]["eff_N"] == 300


def test_merge_coeffs_keeps_existing_bucket_when_fresh_empty() -> None:
    existing = {"buckets": {"high_liq": {"coeff": 0.47, "eff_N": 561, "confidence": "confirmed"}}, "per_name": {}}
    fresh = {
        "generated": "x", "global_default": 1.0,
        "buckets": {"high_liq": {"coeff": None, "eff_N": 0, "confidence": "provisional"}},
        "per_name": {},
    }
    merged = merge_coeffs(existing, fresh)
    # existing higher-eff_N bucket retained
    assert merged["buckets"]["high_liq"]["eff_N"] == 561


# ---------------------------------------------------------------------------
# Trade-tape units (plan 18 §4.2 — Track C defect 1)
# ---------------------------------------------------------------------------

def _tape(vols: list[float], price: float, date: str = "2026-09-08") -> pd.DataFrame:
    """A minimal one-day trade tape with the ``_wib_dt`` column the ADV helper needs."""
    base = pd.Timestamp(f"{date} 09:00:00")
    return pd.DataFrame(
        {
            "recv_ns": [1_000_000_000 * (i + 1) for i in range(len(vols))],
            "price": [price] * len(vols),
            "vol": vols,
            "side": ["B"] * len(vols),
            "_wib_dt": [base + pd.Timedelta(seconds=i) for i in range(len(vols))],
        }
    )


class TestTradeTapeIsInShares:
    """
    ``vol`` on the running-trade tape is in SHARES, not lots.

    Verified against real capture: BUMI 2026-06-18, 47,976 prints, sum(vol x
    price) = Rp 415.09 B — the right order of magnitude against the screener's
    Rp 668.96 B median.  Multiplying by 100 gives Rp 41,509 B, which exceeds
    total IDX daily turnover across all names.  See plan 18 4.2.
    """

    def test_adv_is_sum_of_vol_times_price(self) -> None:
        from strategy_fit.fill_calibration import _adv_from_trades
        # 687,965,300 shares at Rp 100 = Rp 68.80 B — the real BNBR 2026-09-08 day.
        adv = _adv_from_trades(_tape([687_965_300.0], price=100.0))
        assert adv == pytest.approx(68.7965300e9)

    def test_bnbr_shaped_day_is_mid_liq_not_high_liq(self) -> None:
        """The bug's whole consequence: absolute thresholds mis-bucket every name."""
        from strategy_fit.fill_calibration import _adv_from_trades
        adv = _adv_from_trades(_tape([687_965_300.0], price=100.0))
        assert liquidity_bucket(adv) == "mid_liq"

    def test_adv_is_median_across_days(self) -> None:
        from strategy_fit.fill_calibration import _adv_from_trades
        tape = pd.concat(
            [
                _tape([10_000_000.0], price=1000.0, date="2026-09-07"),  # Rp 10 B
                _tape([30_000_000.0], price=1000.0, date="2026-09-08"),  # Rp 30 B
                _tape([50_000_000.0], price=1000.0, date="2026-09-09"),  # Rp 50 B
            ],
            ignore_index=True,
        )
        assert _adv_from_trades(tape) == pytest.approx(30e9)

    def test_event_notional_is_not_scaled_by_lot_size(self) -> None:
        """``event_notional_idr`` is reported, so it must be right, not merely cancel."""
        start_ns = 1_000_000_000
        feats = _features_for_impact(start_ns, 1000.0)
        trades = pd.DataFrame(
            {
                "recv_ns": [start_ns + 10, start_ns + 20],
                "price": [1000.0, 1000.0],
                "vol": [100.0, 100.0],
                "side": ["B", "B"],
            }
        )
        events = detect_impact_events(trades, feats, 100.0, 1e9)
        assert len(events) == 1
        assert events[0].event_notional_idr == pytest.approx(200_000.0)

    def test_participation_is_unchanged_by_the_fix(self) -> None:
        """
        Regression guard on the cancellation.

        Before the fix both ``_adv_from_trades`` and ``detect_impact_events``
        multiplied by 100, so ``participation = notional / adv`` was already
        correct and the FITTED COEFFICIENT was never wrong — only the bucket
        label was.  Removing the factor from one site alone would silently move
        every fitted coefficient by sqrt(100).
        """
        from strategy_fit.fill_calibration import _adv_from_trades
        start_ns = 1_000_000_000
        feats = _features_for_impact(start_ns, 1000.0)
        day_vols = [100.0, 100.0]
        trades = pd.DataFrame(
            {
                "recv_ns": [start_ns + 10, start_ns + 20],
                "price": [1000.0, 1000.0],
                "vol": day_vols,
                "side": ["B", "B"],
            }
        )
        adv = _adv_from_trades(_tape(day_vols, price=1000.0))
        events = detect_impact_events(trades, feats, 100.0, adv)
        assert len(events) == 1
        # The whole day is this one run, so participation is exactly 1.0.
        assert events[0].participation_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Volatility source (plan 18 §4.3a — Track C defect 3)
# ---------------------------------------------------------------------------

def _daily_bars(closes: list[float], start: str = "2026-06-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame({"date": dates, "close": closes, "volume": [1_000] * len(closes)})


class TestCalibrationVolatilitySource:
    """
    ``coeff`` must carry impact, not an estimator mismatch.

    The fit is ``observed_impact_bps ~ 2 * coeff * daily_vol_bps *
    sqrt(participation)``, so ``coeff`` is inversely proportional to the
    volatility estimate.  The consumer (``compute_impact_bps_from_daily_df``,
    used by the strategy grid) takes its volatility from
    ``compute_daily_vol_bps_from_daily_bars`` over daily history.  Calibration
    must therefore use the same estimator on the same kind of input, or every
    coefficient is scaled by the ratio between two different estimators.

    Before this change, calibration estimated volatility from the CAPTURE
    PANEL, switching estimator at ``n_days >= 2``: one day used an intraday
    realized proxy, five days used close-to-close over four returns.  On the
    2026-09-07..11 panel that read PTRO at 79 bps on a week it realized
    241-470 bps intraday every day, inflating its coefficient 3.9x.
    """

    def test_daily_bars_are_preferred_over_the_capture_panel(self) -> None:
        from strategy_fit.fill_calibration import (
            CALIBRATION_VOL_WINDOW_DAYS,
            resolve_daily_vol_bps,
        )
        from strategy_fit.impact import compute_daily_vol_bps_from_daily_bars
        bars = _daily_bars([100.0, 104.0, 99.0, 103.0, 98.0, 105.0] * 12)
        vol, source = resolve_daily_vol_bps(features=None, daily_bars=bars, as_of="2026-12-31")
        assert source == "daily_bars"
        # Same estimator the consumer uses, over the capped trailing window.
        expected = compute_daily_vol_bps_from_daily_bars(bars.tail(CALIBRATION_VOL_WINDOW_DAYS))
        assert vol == pytest.approx(expected, rel=1e-9)

    def test_window_is_capped_to_the_configured_lookback(self) -> None:
        """A long history must not silently become a decade-wide median."""
        from strategy_fit.fill_calibration import CALIBRATION_VOL_WINDOW_DAYS, resolve_daily_vol_bps
        calm = [100.0, 100.5] * 400          # ancient, low vol
        wild = [100.0, 130.0] * 40           # recent, high vol
        bars = _daily_bars(calm + wild, start="2020-01-01")
        vol, _ = resolve_daily_vol_bps(features=None, daily_bars=bars, as_of="2099-01-01")
        tail_only, _ = resolve_daily_vol_bps(
            features=None,
            daily_bars=bars.tail(CALIBRATION_VOL_WINDOW_DAYS),
            as_of="2099-01-01",
        )
        assert vol == pytest.approx(tail_only, rel=1e-9)

    def test_bars_after_the_capture_date_are_excluded(self) -> None:
        """No look-ahead: a calibration dated T cannot see T+1."""
        from strategy_fit.fill_calibration import resolve_daily_vol_bps
        quiet = _daily_bars([100.0, 100.1] * 40, start="2026-06-01")
        future = _daily_bars([100.0, 200.0] * 40, start="2027-01-01")
        bars = pd.concat([quiet, future], ignore_index=True)
        vol_cut, _ = resolve_daily_vol_bps(features=None, daily_bars=bars, as_of="2026-09-11")
        vol_all, _ = resolve_daily_vol_bps(features=None, daily_bars=bars, as_of="2099-01-01")
        assert vol_cut < vol_all

    def test_falls_back_to_intraday_proxy_without_daily_bars(self) -> None:
        """Missing history must degrade loudly, not silently reintroduce defect 3."""
        from strategy_fit.fill_calibration import resolve_daily_vol_bps
        feats = _features_for_impact(1_000_000_000, 1000.0)
        vol, source = resolve_daily_vol_bps(features=feats, daily_bars=None, as_of="2026-09-11")
        assert source == "intraday_proxy"
        assert vol >= 0.0

    def test_close_to_close_over_the_capture_panel_is_never_used(self) -> None:
        """
        The specific defect: five capture days gave four returns, and a flat
        week collapsed the estimate.  That path must be gone entirely.
        """
        from strategy_fit.fill_calibration import resolve_daily_vol_bps
        feats = _features_for_impact(1_000_000_000, 1000.0)
        bars = _daily_bars([100.0, 106.0, 97.0, 104.0] * 20)
        _, source = resolve_daily_vol_bps(features=feats, daily_bars=bars, as_of="2026-12-31")
        assert source == "daily_bars", "daily bars must win even when a capture panel is present"

    def test_empty_or_unusable_bars_fall_back(self) -> None:
        from strategy_fit.fill_calibration import resolve_daily_vol_bps
        feats = _features_for_impact(1_000_000_000, 1000.0)
        _, source = resolve_daily_vol_bps(
            features=feats, daily_bars=pd.DataFrame(columns=["date", "close", "volume"]),
            as_of="2026-09-11",
        )
        assert source == "intraday_proxy"
