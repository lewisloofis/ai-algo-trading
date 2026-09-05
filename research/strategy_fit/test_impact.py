"""
strategy_fit/test_impact.py — TDD tests for the market-impact / slippage model.

Validates strategy_fit/impact.py:
  - compute_daily_vol_bps: daily close-to-close return stdev in basis points
  - compute_adv_notional_idr: median daily turnover in IDR
  - compute_impact_bps: square-root Almgren impact, round-trip, in bps
  - Degenerate guards: single day, zero volume, NaN rows
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.impact import (
    _DEGENERATE_IMPACT_BPS,
    POSITION_NOTIONAL_IDR,
    SQRT_IMPACT_COEFF,
    compute_adv_notional_idr,
    compute_adv_notional_idr_from_daily_bars,
    compute_daily_vol_bps,
    compute_daily_vol_bps_from_daily_bars,
    compute_impact_bps,
    compute_impact_bps_from_daily_df,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_intraday_df(
    n_days: int = 20,
    ref_price: float = 1000.0,
    daily_vol_frac: float = 0.01,
    bars_per_day: int = 345,
    volume_per_bar: int = 1000,
    seed: int = 42,
) -> pd.DataFrame:
    """Build a minimal synthetic 1-min intraday DataFrame."""
    rng = np.random.default_rng(seed)
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # Simulate n_bars bars per day with a simple random walk
        log_returns = rng.normal(0.0, daily_vol_frac / np.sqrt(bars_per_day), size=bars_per_day)
        closes = ref_price * np.exp(np.cumsum(log_returns))
        volumes = rng.integers(volume_per_bar // 2, volume_per_bar * 2, size=bars_per_day)
        # Just use simple bar times (hour/minute irrelevant for these unit tests)
        for i in range(bars_per_day):
            rows.append({
                "datetime": date + pd.Timedelta(minutes=i),
                "date": date.date(),
                "open": float(closes[max(0, i - 1)]),
                "high": float(closes[i] * 1.001),
                "low": float(closes[i] * 0.999),
                "close": float(closes[i]),
                "volume": int(volumes[i]),
            })
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Section A: compute_daily_vol_bps
# ---------------------------------------------------------------------------

class TestComputeDailyVolBps:
    def test_known_vol_matches_expectation(self) -> None:
        """daily_vol_bps should be close to the injected daily-return std * 1e4."""
        # Build a 30-day intraday df with daily close-to-close ~1% (100 bps) vol.
        n_days = 30
        rng = np.random.default_rng(0)
        rows = []
        date = pd.Timestamp("2025-01-06")
        days_added = 0
        ref = 1000.0
        prev_close = ref
        while days_added < n_days:
            if date.weekday() >= 5:
                date += pd.Timedelta(days=1)
                continue
            # one representative close bar per day (last bar of day)
            ret = rng.normal(0.0, 0.01)  # 1% daily vol
            new_close = prev_close * np.exp(ret)
            rows.append({
                "datetime": date + pd.Timedelta(hours=16, minutes=15),
                "date": date.date(),
                "open": prev_close,
                "high": max(prev_close, new_close),
                "low": min(prev_close, new_close),
                "close": new_close,
                "volume": 1000,
            })
            prev_close = new_close
            days_added += 1
            date += pd.Timedelta(days=1)
        df = pd.DataFrame(rows)
        vol_bps = compute_daily_vol_bps(df)
        # With seed=0 and n=30, should be in the range [50, 200] bps
        assert 50.0 < vol_bps < 200.0, f"daily_vol_bps={vol_bps} out of expected range [50, 200]"

    def test_single_day_returns_fallback(self) -> None:
        """Single day of data → cannot compute c-to-c return → returns fallback large value."""
        df = _make_intraday_df(n_days=1)
        vol_bps = compute_daily_vol_bps(df)
        # Single day: not enough to compute c-to-c return; must return a large penalty value
        assert vol_bps > 0, "single-day vol_bps must be positive (penalising fallback)"

    def test_all_nan_close_returns_fallback(self) -> None:
        """If all close values are NaN, must return a large penalty value."""
        df = _make_intraday_df(n_days=10)
        df["close"] = np.nan
        vol_bps = compute_daily_vol_bps(df)
        assert vol_bps > 0, "all-NaN close vol_bps must be a positive penalty"

    def test_zero_vol_series_returns_small_positive(self) -> None:
        """Perfectly flat price (vol=0) should return near-zero or a floor, never negative."""
        rows = []
        date = pd.Timestamp("2025-01-06")
        for i in range(20):
            rows.append({"datetime": date + pd.Timedelta(days=i), "date": (date + pd.Timedelta(days=i)).date(),
                         "open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0, "volume": 1000})
        df = pd.DataFrame(rows)
        vol_bps = compute_daily_vol_bps(df)
        assert vol_bps >= 0.0, "zero-vol series must not return negative vol"


# ---------------------------------------------------------------------------
# Section B: compute_adv_notional_idr
# ---------------------------------------------------------------------------

class TestComputeAdvNotionalIdr:
    def test_known_adv_matches_expectation(self) -> None:
        """With constant close=1000, volume=1000/bar, 345 bars/day → ADV ≈ 345_000 IDR."""
        df = _make_intraday_df(n_days=20, ref_price=1000.0, volume_per_bar=1000)
        # Each bar: close*volume ≈ 1000 * ~1000; 345 bars/day → ~345_000 IDR/day (rough)
        adv = compute_adv_notional_idr(df)
        # Accept a wide tolerance because volumes are random in [500, 2000]
        assert adv > 0, "ADV must be positive"
        # With ref_price=1000 and volume averaging ~1000/bar, 345 bars → ~345M IDR/day
        # (1000 IDR * 1000 shares * 345 bars)
        assert 50_000_000 < adv < 2_000_000_000, f"ADV={adv} out of expected range"

    def test_zero_volume_returns_fallback(self) -> None:
        """Zero volume on all bars → ADV=0 guard; compute_impact must handle it."""
        df = _make_intraday_df(n_days=10)
        df["volume"] = 0
        adv = compute_adv_notional_idr(df)
        assert adv >= 0, "zero-volume ADV must be >= 0"

    def test_single_day_adv_works(self) -> None:
        """Single day should still produce a non-negative ADV."""
        df = _make_intraday_df(n_days=1)
        adv = compute_adv_notional_idr(df)
        assert adv >= 0

    def test_nan_volume_handled_gracefully(self) -> None:
        """NaN volume rows should be handled without crashing."""
        df = _make_intraday_df(n_days=5)
        df.loc[df.index[:10], "volume"] = np.nan
        adv = compute_adv_notional_idr(df)
        assert adv >= 0


# ---------------------------------------------------------------------------
# Section C: compute_impact_bps
# ---------------------------------------------------------------------------

class TestComputeImpactBps:
    def test_known_inputs_known_output(self) -> None:
        """Manual computation:
        daily_vol_bps = 100, adv = 1_000_000, notional = 30_000_000.
        participation = 30_000_000 / 1_000_000 = 30.0 → clamped to 1.0
        impact = 2 * 1.0 * 100 * sqrt(1.0) = 200 bps
        """
        impact = compute_impact_bps(
            daily_vol_bps=100.0,
            adv_notional_idr=1_000_000.0,
            position_notional_idr=30_000_000.0,
        )
        assert abs(impact - 200.0) < 1e-6, f"Expected 200.0, got {impact}"

    def test_low_participation_below_one(self) -> None:
        """participation = 30M / 300M = 0.1; impact = 2 * 1.0 * 100 * sqrt(0.1) ≈ 63.25 bps."""
        expected = 2.0 * SQRT_IMPACT_COEFF * 100.0 * np.sqrt(0.1)
        impact = compute_impact_bps(
            daily_vol_bps=100.0,
            adv_notional_idr=300_000_000.0,
            position_notional_idr=30_000_000.0,
        )
        assert abs(impact - expected) < 1e-6, f"Expected {expected:.4f}, got {impact}"

    def test_participation_clamped_at_one(self) -> None:
        """participation = 90M / 30M = 3.0 → clamped to 1.0; same as participation=1.0."""
        impact_clamped = compute_impact_bps(
            daily_vol_bps=100.0,
            adv_notional_idr=30_000_000.0,
            position_notional_idr=90_000_000.0,
        )
        impact_one = compute_impact_bps(
            daily_vol_bps=100.0,
            adv_notional_idr=30_000_000.0,
            position_notional_idr=30_000_000.0,
        )
        assert abs(impact_clamped - impact_one) < 1e-6, (
            f"Clamped participation must equal participation=1.0: {impact_clamped} vs {impact_one}"
        )

    def test_zero_adv_returns_large_penalty(self) -> None:
        """ADV=0 cannot compute participation; must return a large penalty, not crash."""
        impact = compute_impact_bps(
            daily_vol_bps=100.0,
            adv_notional_idr=0.0,
            position_notional_idr=30_000_000.0,
        )
        assert impact > 0, "zero-ADV impact must be a positive (penalising) value"

    def test_negative_adv_returns_large_penalty(self) -> None:
        """Negative ADV is degenerate; must return a large penalty, not crash."""
        impact = compute_impact_bps(
            daily_vol_bps=100.0,
            adv_notional_idr=-1.0,
            position_notional_idr=30_000_000.0,
        )
        assert impact > 0

    def test_impact_increases_with_participation(self) -> None:
        """Larger position (higher participation) → higher impact."""
        small = compute_impact_bps(100.0, 1_000_000_000.0, 10_000_000.0)
        large = compute_impact_bps(100.0, 1_000_000_000.0, 100_000_000.0)
        assert large > small, f"large participation must yield more impact: {large} vs {small}"

    def test_impact_increases_with_vol(self) -> None:
        """Higher daily vol → higher impact."""
        low_vol = compute_impact_bps(50.0, 300_000_000.0, 30_000_000.0)
        high_vol = compute_impact_bps(200.0, 300_000_000.0, 30_000_000.0)
        assert high_vol > low_vol, f"high vol must yield more impact: {high_vol} vs {low_vol}"

    def test_default_position_notional_constant(self) -> None:
        """POSITION_NOTIONAL_IDR constant must be positive and in a reasonable range."""
        assert POSITION_NOTIONAL_IDR > 0
        # Must be <= 300M IDR (sleeve) and >= 1M (sensible per-name slice)
        assert 1_000_000 <= POSITION_NOTIONAL_IDR <= 300_000_000

    def test_sqrt_impact_coeff_constant(self) -> None:
        """SQRT_IMPACT_COEFF must be the documented 1.0."""
        assert SQRT_IMPACT_COEFF == 1.0


# ---------------------------------------------------------------------------
# Section D: end-to-end from intraday DataFrame
# ---------------------------------------------------------------------------

class TestComputeImpactBpsFromDf:
    def test_impact_from_df_is_positive(self) -> None:
        """compute_impact_bps with a realistic intraday DataFrame returns a positive value."""
        from strategy_fit.impact import compute_impact_bps_from_df
        df = _make_intraday_df(n_days=20, ref_price=27500.0, volume_per_bar=5000)
        impact = compute_impact_bps_from_df(df)
        assert impact > 0.0, f"Expected positive impact from realistic df, got {impact}"

    def test_impact_from_df_returns_large_for_zero_volume(self) -> None:
        """Zero volume in df → should return a large penalising impact, not crash."""
        from strategy_fit.impact import compute_impact_bps_from_df
        df = _make_intraday_df(n_days=10)
        df["volume"] = 0
        impact = compute_impact_bps_from_df(df)
        assert impact > 0.0

    def test_impact_from_df_returns_large_for_single_day(self) -> None:
        """Single day of data → penalising impact (can't compute vol)."""
        from strategy_fit.impact import compute_impact_bps_from_df
        df = _make_intraday_df(n_days=1)
        impact = compute_impact_bps_from_df(df)
        assert impact > 0.0

    def test_high_adv_stock_has_lower_impact_than_low_adv(self) -> None:
        """High-volume/ADV stock should have lower impact than low-volume stock."""
        from strategy_fit.impact import compute_impact_bps_from_df
        # High volume: 1_000_000 per bar
        df_high = _make_intraday_df(n_days=20, ref_price=1000.0, volume_per_bar=1_000_000)
        # Low volume: 100 per bar
        df_low = _make_intraday_df(n_days=20, ref_price=1000.0, volume_per_bar=100)
        impact_high = compute_impact_bps_from_df(df_high)
        impact_low = compute_impact_bps_from_df(df_low)
        assert impact_low > impact_high, (
            f"Low-ADV stock must have more impact: {impact_low} vs {impact_high}"
        )


# ---------------------------------------------------------------------------
# Calibrated coefficient resolver (plan 15 §7.1, §8 Tier-1)
# ---------------------------------------------------------------------------

class TestGetImpactCoeff:
    """get_impact_coeff resolves per-name → bucket → global default."""

    @staticmethod
    def _inject(monkeypatch, coeffs: dict | None) -> None:
        import strategy_fit.impact as impact_mod
        monkeypatch.setattr(impact_mod, "_CALIBRATED_COEFFS", coeffs, raising=False)
        monkeypatch.setattr(impact_mod, "_COEFFS_LOADED", True, raising=False)

    def test_confirmed_per_name_coeff_used(self, monkeypatch) -> None:
        from strategy_fit.impact import get_impact_coeff
        self._inject(monkeypatch, {
            "global_default": 1.0,
            "buckets": {"high_liq": {"coeff": 0.5, "confidence": "confirmed"}},
            "per_name": {"PTRO": {"coeff": 0.32, "confidence": "confirmed", "bucket": "high_liq"}},
        })
        assert get_impact_coeff("PTRO") == pytest.approx(0.32)

    def test_provisional_name_falls_back_to_bucket(self, monkeypatch) -> None:
        from strategy_fit.impact import get_impact_coeff
        self._inject(monkeypatch, {
            "global_default": 1.0,
            "buckets": {"high_liq": {"coeff": 0.5, "confidence": "confirmed"}},
            "per_name": {"PTRO": {"coeff": None, "confidence": "provisional", "bucket": "high_liq"}},
        })
        assert get_impact_coeff("PTRO") == pytest.approx(0.5)

    def test_provisional_name_and_bucket_falls_back_to_global(self, monkeypatch) -> None:
        from strategy_fit.impact import get_impact_coeff
        self._inject(monkeypatch, {
            "global_default": 1.0,
            "buckets": {"high_liq": {"coeff": None, "confidence": "provisional"}},
            "per_name": {"PTRO": {"coeff": None, "confidence": "provisional", "bucket": "high_liq"}},
        })
        assert get_impact_coeff("PTRO") == pytest.approx(1.0)

    def test_unknown_symbol_uses_global_default(self, monkeypatch) -> None:
        from strategy_fit.impact import get_impact_coeff
        self._inject(monkeypatch, {"global_default": 0.8, "buckets": {}, "per_name": {}})
        assert get_impact_coeff("ZZZZ") == pytest.approx(0.8)

    def test_none_symbol_uses_global_default(self, monkeypatch) -> None:
        from strategy_fit.impact import get_impact_coeff
        self._inject(monkeypatch, {"global_default": 0.8, "buckets": {}, "per_name": {}})
        assert get_impact_coeff(None) == pytest.approx(0.8)

    def test_missing_file_uses_module_constant(self, monkeypatch) -> None:
        from strategy_fit.impact import get_impact_coeff, SQRT_IMPACT_COEFF
        self._inject(monkeypatch, None)  # file absent / unreadable
        assert get_impact_coeff("PTRO") == pytest.approx(SQRT_IMPACT_COEFF)


class TestUseCalibratedFlag:
    """use_calibrated=False must reproduce pre-plan-15 behaviour exactly."""

    def test_backward_compat_default_matches_assumed_model(self) -> None:
        # default call (no kwargs) and explicit use_calibrated=False must agree,
        # and both must use SQRT_IMPACT_COEFF regardless of any loaded config.
        baseline = compute_impact_bps(100.0, 1e12)
        explicit = compute_impact_bps(100.0, 1e12, use_calibrated=False, symbol="PTRO")
        expected = 2.0 * SQRT_IMPACT_COEFF * 100.0 * np.sqrt(
            min(POSITION_NOTIONAL_IDR / 1e12, 1.0)
        )
        assert baseline == pytest.approx(expected)
        assert explicit == pytest.approx(expected)

    def test_calibrated_path_uses_resolved_coeff(self, monkeypatch) -> None:
        import strategy_fit.impact as impact_mod
        monkeypatch.setattr(impact_mod, "_CALIBRATED_COEFFS", {
            "global_default": 1.0,
            "buckets": {},
            "per_name": {"PTRO": {"coeff": 0.25, "confidence": "confirmed", "bucket": "high_liq"}},
        }, raising=False)
        monkeypatch.setattr(impact_mod, "_COEFFS_LOADED", True, raising=False)
        calibrated = compute_impact_bps(100.0, 1e12, use_calibrated=True, symbol="PTRO")
        expected = 2.0 * 0.25 * 100.0 * np.sqrt(min(POSITION_NOTIONAL_IDR / 1e12, 1.0))
        assert calibrated == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Daily-bar impact pricing (plan 13)
# ---------------------------------------------------------------------------

def _make_daily_df(
    dates: list[str],
    closes: list[float],
    volumes: list[float] | None = None,
    values: list[float] | None = None,
) -> pd.DataFrame:
    """Build a minimal daily-bar DataFrame, matching daily_history's schema."""
    data = {"date": dates, "close": closes}
    data["volume"] = volumes if volumes is not None else [1000.0] * len(dates)
    if values is not None:
        data["value"] = values
    return pd.DataFrame(data)


class TestComputeDailyVolBpsFromDailyBars:
    def test_matches_hand_computed_value_and_sorts_by_date(self) -> None:
        """Rows are deliberately out of date order; function must sort before diffing."""
        df = _make_daily_df(
            dates=["2025-01-08", "2025-01-06", "2025-01-07", "2025-01-09"],
            closes=[121.0, 100.0, 110.0, 108.9],
        )
        vol_bps = compute_daily_vol_bps_from_daily_bars(df)

        # Ascending by date: 100.0, 110.0, 121.0, 108.9 — hand-computed expectation.
        ordered_closes = np.array([100.0, 110.0, 121.0, 108.9])
        expected_log_rets = np.diff(np.log(ordered_closes))
        expected_vol_bps = float(np.std(expected_log_rets, ddof=1)) * 1e4
        assert vol_bps == pytest.approx(expected_vol_bps)

    def test_fewer_than_two_rows_returns_degenerate(self) -> None:
        from strategy_fit.impact import _DEGENERATE_VOL_BPS
        single_row = _make_daily_df(dates=["2025-01-06"], closes=[100.0])
        assert compute_daily_vol_bps_from_daily_bars(single_row) == _DEGENERATE_VOL_BPS

        empty = _make_daily_df(dates=[], closes=[])
        assert compute_daily_vol_bps_from_daily_bars(empty) == _DEGENERATE_VOL_BPS


class TestComputeAdvNotionalIdrFromDailyBars:
    def test_prefers_value_column_over_close_times_volume(self) -> None:
        """`value` and close*volume are constructed to differ so the test discriminates."""
        df = _make_daily_df(
            dates=["2025-01-06", "2025-01-07", "2025-01-08"],
            closes=[1000.0, 1000.0, 1000.0],
            volumes=[1000.0, 1000.0, 1000.0],  # close*volume = 1_000_000 every day
            values=[5_000_000.0, 6_000_000.0, 7_000_000.0],  # deliberately different
        )
        adv = compute_adv_notional_idr_from_daily_bars(df)
        assert adv == pytest.approx(6_000_000.0)  # median of `value`
        assert adv != pytest.approx(1_000_000.0)  # not the close*volume fallback

    def test_falls_back_to_close_times_volume_when_value_absent(self) -> None:
        df = _make_daily_df(
            dates=["2025-01-06", "2025-01-07", "2025-01-08"],
            closes=[1000.0, 2000.0, 1500.0],
            volumes=[1000.0, 500.0, 800.0],
        )
        adv = compute_adv_notional_idr_from_daily_bars(df)
        expected_median = float(np.median([1000.0 * 1000.0, 2000.0 * 500.0, 1500.0 * 800.0]))
        assert adv == pytest.approx(expected_median)

    def test_empty_frame_returns_zero_and_impact_is_degenerate(self) -> None:
        from strategy_fit.impact import _DEGENERATE_IMPACT_BPS
        empty = _make_daily_df(dates=[], closes=[])
        adv = compute_adv_notional_idr_from_daily_bars(empty)
        assert adv == 0.0
        impact = compute_impact_bps(daily_vol_bps=100.0, adv_notional_idr=adv)
        assert impact == _DEGENERATE_IMPACT_BPS


class TestComputeImpactBpsFromDailyDf:
    _DATES = ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09"]
    _CLOSES = [1000.0, 1010.0, 990.0, 1005.0]

    def test_participation_clamps_at_one_for_huge_position(self) -> None:
        df = _make_daily_df(dates=self._DATES, closes=self._CLOSES)
        vol_bps = compute_daily_vol_bps_from_daily_bars(df)
        impact = compute_impact_bps_from_daily_df(df, position_notional_idr=1e15)
        expected = 2.0 * SQRT_IMPACT_COEFF * vol_bps  # sqrt(participation=1.0) == 1.0
        assert impact == pytest.approx(expected)

    def test_use_calibrated_unknown_symbol_falls_through_to_global_default(self) -> None:
        """BNBR is absent from impact_coeffs.json's per_name — must resolve to
        global_default (1.0), reproducing the uncalibrated SQRT_IMPACT_COEFF path."""
        df = _make_daily_df(dates=self._DATES, closes=self._CLOSES)
        uncalibrated = compute_impact_bps_from_daily_df(df, use_calibrated=False)
        calibrated_unknown = compute_impact_bps_from_daily_df(df, use_calibrated=True, symbol="BNBR")
        assert calibrated_unknown == pytest.approx(uncalibrated)

    def test_use_calibrated_known_symbol_gives_smaller_impact(self) -> None:
        """PTRO is `confirmed` in impact_coeffs.json with coeff < 1.0."""
        df = _make_daily_df(dates=self._DATES, closes=self._CLOSES)
        uncalibrated = compute_impact_bps_from_daily_df(df, use_calibrated=False)
        calibrated_ptro = compute_impact_bps_from_daily_df(df, use_calibrated=True, symbol="PTRO")
        assert calibrated_ptro < uncalibrated

    def test_new_functions_do_not_mutate_input_dataframe(self) -> None:
        df = _make_daily_df(
            dates=["2025-01-08", "2025-01-06", "2025-01-07"],
            closes=[121.0, 100.0, 110.0],
            values=[5_000_000.0, 4_000_000.0, 4_500_000.0],
        )
        original = df.copy(deep=True)

        compute_daily_vol_bps_from_daily_bars(df)
        compute_adv_notional_idr_from_daily_bars(df)
        compute_impact_bps_from_daily_df(df)

        pd.testing.assert_frame_equal(df, original)

    # -- Zero-turnover days (orchestrator fix, plan 13) ---------------------

    def test_zero_turnover_days_are_kept_in_the_median(self) -> None:
        """A no-trade day is real evidence about capacity and must lower ADV.

        Filtering zeros out would raise ADV, lower participation, and
        UNDERSTATE impact — worst on exactly the thin penny names where the
        impact charge decides the cell.  Median over [0, 0, 10M, 10M, 10M]
        is 10M; over the positive-only subset it is also 10M, so the
        discriminating case needs enough zeros to move the median.
        """
        df = _make_daily_df(
            dates=[f"2025-01-{d:02d}" for d in range(6, 11)],
            closes=[100.0, 100.0, 100.0, 100.0, 100.0],
            values=[0.0, 0.0, 0.0, 10_000_000.0, 10_000_000.0],
        )
        adv = compute_adv_notional_idr_from_daily_bars(df)
        # Median of [0, 0, 0, 10M, 10M] = 0.0 — the name is untradeable on
        # most days.  Positive-only filtering would have returned 10M.
        assert adv == pytest.approx(0.0)

    def test_all_zero_turnover_lands_on_degenerate_impact(self) -> None:
        """A name that never trades has no capacity: ADV 0 → degenerate penalty."""
        df = _make_daily_df(
            dates=["2025-01-06", "2025-01-07", "2025-01-08"],
            closes=[100.0, 101.0, 102.0],
            volumes=[0.0, 0.0, 0.0],
            values=[0.0, 0.0, 0.0],
        )
        # `value` is all-zero so it carries no turnover information and the
        # close x volume fallback runs — which is also all-zero here.
        assert compute_adv_notional_idr_from_daily_bars(df) == pytest.approx(0.0)
        assert compute_impact_bps_from_daily_df(df) == pytest.approx(_DEGENERATE_IMPACT_BPS)

    def test_value_column_of_all_zeros_falls_back_to_close_x_volume(self) -> None:
        """`value` with no positive entry carries no turnover information."""
        df = _make_daily_df(
            dates=["2025-01-06", "2025-01-07", "2025-01-08"],
            closes=[100.0, 100.0, 100.0],
            values=[0.0, 0.0, 0.0],
        )
        df["volume"] = [1_000.0, 1_000.0, 1_000.0]
        # Falls back to median(close x volume) = 100 * 1000 = 100_000.
        assert compute_adv_notional_idr_from_daily_bars(df) == pytest.approx(100_000.0)
