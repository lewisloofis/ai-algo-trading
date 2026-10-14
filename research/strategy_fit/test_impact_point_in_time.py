"""
strategy_fit/test_impact_point_in_time.py — point-in-time impact inputs.

The grid used to price impact from FULL-history ADV (median over 2024+) and
FULL-history vol.  Two scale errors: ADV is stale after a volume regime change
(BNBR: 0.45B full-history vs 92.7B over the last 60 bars) and the vol window
does not match the calibration basis (fill_calibration.CALIBRATION_VOL_WINDOW_DAYS
trailing daily bars).  The fix is trailing windows that END at the signal day s.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit import impact
from strategy_fit.fill_calibration import CALIBRATION_VOL_WINDOW_DAYS, resolve_daily_vol_bps


def _bars(n: int, *, volume=None, close=None, seed: int = 0, start="2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n).strftime("%Y-%m-%d")
    if close is None:
        close = 1000.0 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    if volume is None:
        volume = np.full(n, 1_000_000.0)
    return pd.DataFrame({"date": dates, "close": np.asarray(close, float), "volume": np.asarray(volume, float)})


def _model(df, **kw):
    return impact.TrailingImpactModel(df, symbol="TEST", use_calibrated=False, **kw)


class TestConstants:
    def test_windows_match_calibration_basis(self):
        assert impact.IMPACT_VOL_WINDOW_DAYS == CALIBRATION_VOL_WINDOW_DAYS == 60
        assert impact.IMPACT_ADV_WINDOW_DAYS == 60
        assert impact.MIN_IMPACT_HISTORY_BARS == 20


class TestNoLookahead:
    def _regime_df(self, later_mult: float) -> pd.DataFrame:
        # constant close so turnover == 1000 * volume; 65x volume regime change at bar 150
        volume = np.concatenate([np.full(150, 100_000.0), np.full(150, 100_000.0 * later_mult)])
        return _bars(300, close=np.full(300, 1000.0), volume=volume)

    def test_early_adv_does_not_see_later_volume(self):
        df = self._regime_df(65.0)
        as_of = df["date"].iloc[100]
        _, adv = _model(df).inputs(as_of)
        assert adv == pytest.approx(1000.0 * 100_000.0)

    def test_early_inputs_identical_when_future_changes(self):
        as_of = _bars(300)["date"].iloc[100]
        a = _model(self._regime_df(65.0)).inputs(as_of)
        b = _model(self._regime_df(1.0)).inputs(as_of)
        assert a == b

    def test_late_adv_reflects_new_regime_not_full_history_median(self):
        df = self._regime_df(65.0)
        _, adv_late = _model(df).inputs(df["date"].iloc[-1])
        assert adv_late == pytest.approx(1000.0 * 100_000.0 * 65.0)
        # the legacy full-history median is what the old grid used; it is stale
        # (here the median of a 150/150 split lands between the two regimes)
        full = impact.compute_adv_notional_idr_from_daily_bars(df)
        assert full < 0.6 * adv_late

    def test_window_ends_at_signal_day_inclusive(self):
        df = self._regime_df(65.0)
        # 60-bar window ending at bar 209 is entirely the new regime; ending at 208 is too
        _, adv = _model(df).inputs(df["date"].iloc[209])
        assert adv == pytest.approx(1000.0 * 100_000.0 * 65.0)
        # window ending at bar 149 has not seen the regime change
        _, adv_pre = _model(df).inputs(df["date"].iloc[149])
        assert adv_pre == pytest.approx(1000.0 * 100_000.0)

    def test_impact_lower_after_regime_change_than_legacy(self):
        df = self._regime_df(65.0)
        df["close"] = _bars(300, seed=1)["close"]  # non-zero vol so impact is non-zero
        late = _model(df).impact_bps(df["date"].iloc[-1])
        legacy = impact.compute_impact_bps_from_daily_df(df, symbol="TEST")
        assert late < legacy


class TestVolWindowMatchesCalibration:
    def test_vol_equals_calibration_estimator(self):
        df = _bars(300, seed=3)
        as_of = df["date"].iloc[200]
        vol, _ = _model(df).inputs(as_of)
        expected, source = resolve_daily_vol_bps(None, df, as_of=as_of)
        assert source == "daily_bars"
        assert vol == pytest.approx(expected)

    def test_vol_is_60_bar_trailing_not_full_history(self):
        # quiet first 200 bars, wild last 100: full-history vol != trailing vol
        rng = np.random.default_rng(5)
        rets = np.concatenate([rng.normal(0, 0.005, 200), rng.normal(0, 0.04, 100)])
        df = _bars(300, close=1000.0 * np.exp(np.cumsum(rets)))
        vol, _ = _model(df).inputs(df["date"].iloc[-1])
        full = impact.compute_daily_vol_bps_from_daily_bars(df)
        manual = float(np.std(np.diff(np.log(df["close"].to_numpy()[-60:])), ddof=1)) * 1e4
        assert vol == pytest.approx(manual)
        assert vol > 1.5 * full


class TestInsufficientHistory:
    def test_fails_loudly_below_min_bars(self):
        df = _bars(100)
        with pytest.raises(impact.InsufficientImpactHistory):
            _model(df).inputs(df["date"].iloc[18])  # 19 bars
        _model(df).inputs(df["date"].iloc[19])  # 20 bars is enough

    def test_does_not_fall_back_to_full_history(self):
        df = _bars(100)
        with pytest.raises(impact.InsufficientImpactHistory):
            _model(df).impact_bps(df["date"].iloc[5])

    def test_nan_bars_do_not_count_as_valid(self):
        df = _bars(100)
        df.loc[:60, "volume"] = np.nan
        with pytest.raises(impact.InsufficientImpactHistory):
            _model(df).inputs(df["date"].iloc[70])  # only 10 finite-turnover bars in window


class TestLegacyFlag:
    def test_legacy_function_unchanged(self):
        df = _bars(300, seed=7)
        vol = impact.compute_daily_vol_bps_from_daily_bars(df)
        adv = impact.compute_adv_notional_idr_from_daily_bars(df)
        expected = impact.compute_impact_bps(vol, adv, symbol="T")
        assert impact.compute_impact_bps_from_daily_df(df, symbol="T") == pytest.approx(expected)

    def test_legacy_cell_reproduces_old_number_and_trailing_differs(self):
        from strategy_fit.harness import StrategyFamily
        from strategy_fit.run_strategy_fit import _run_single_name_cell

        volume = np.concatenate([np.full(150, 50_000.0), np.full(150, 50_000.0 * 65)])
        df = _bars(300, volume=volume, seed=11)
        fam = StrategyFamily(name="daily_momentum", shape="single_name_time_series", horizon_trading_days=2)
        old = impact.compute_impact_bps_from_daily_df(df, symbol="TEST")
        legacy = _run_single_name_cell(
            "TEST", df, fam, 80.0, impact_basis=impact.IMPACT_BASIS_LEGACY_FULL_HISTORY
        )
        assert legacy.modelled_impact_bps == pytest.approx(old)
        assert legacy.impact_basis == impact.IMPACT_BASIS_LEGACY_FULL_HISTORY
        new = _run_single_name_cell("TEST", df, fam, 80.0)
        assert new.impact_basis == impact.IMPACT_BASIS_TRAILING
        assert new.modelled_impact_bps != pytest.approx(old)


class TestPerTradeCharge:
    def _df(self):
        volume = np.concatenate([np.full(150, 50_000.0), np.full(250, 50_000.0 * 65)])
        return _bars(400, volume=volume, seed=13)

    def test_family_charges_each_trade_at_its_own_signal_day(self):
        from strategy_fit.families_single_name import run_single_name_family

        df = self._df()
        n = len(df)
        sig = np.zeros(n)
        sig[40:380:3] = 1.0
        fwd = np.full(n, 0.01)

        def fn(d, hold=1):
            return pd.DataFrame({"date": df["date"], "close": df["close"], "signal": sig, "forward_return": fwd})

        m = _model(df)
        res = run_single_name_family(
            df, fn, "TEST", 80.0, "stub", 1, impact_model=m
        )
        assert res["status"] == "tested"
        per = np.asarray(res["per_trade_impact_bps"])
        assert len(per) == res["eff_n"]
        assert len(res["trade_returns_bps"]) == len(per) == len(res["trade_signal_dates"])
        # early trades (low-ADV regime) cost more than late ones (high-ADV regime)
        assert per[0] > 2 * per[-1]
        assert res["impact_excluded_n"] == 0

    def test_trades_without_history_are_excluded_and_counted(self):
        from strategy_fit.families_single_name import run_single_name_family

        df = self._df()
        n = len(df)
        sig = np.zeros(n)
        sig[2:380:3] = 1.0  # first signals have < 20 bars of history
        fwd = np.full(n, 0.01)

        def fn(d, hold=1):
            return pd.DataFrame({"date": df["date"], "close": df["close"], "signal": sig, "forward_return": fwd})

        res = run_single_name_family(df, fn, "TEST", 80.0, "stub", 1, impact_model=_model(df))
        assert res["impact_excluded_n"] > 0
        assert len(res["per_trade_impact_bps"]) == res["eff_n"]
        assert np.all(np.isfinite(res["per_trade_impact_bps"]))


class TestWarmup:
    def test_warmup_bars_before_start_remove_start_of_window_exclusions(self):
        from strategy_fit.run_strategy_fit import build_impact_model

        full = _bars(300, seed=2, start="2023-06-01")
        start = full["date"].iloc[100]

        class _DS:
            def daily_history(self, symbol, s, e):
                return full[(full["date"] >= s) & (full["date"] <= e)].reset_index(drop=True)

        window = _DS().daily_history("T", start, full["date"].iloc[-1])
        model = build_impact_model(_DS(), "T", window, start, full["date"].iloc[-1], False)
        # first bar of the scoring window is priceable only thanks to pre-start bars
        assert model.impact_bps(start) >= 0.0
        with pytest.raises(impact.InsufficientImpactHistory):
            _model(window).impact_bps(start)

    def test_warmup_fetch_failure_is_not_silent_fallback_to_full_history(self):
        from strategy_fit.run_strategy_fit import build_impact_model

        window = _bars(100, seed=4)

        class _Broken:
            def daily_history(self, *a):
                raise RuntimeError("boom")

        model = build_impact_model(_Broken(), "T", window, window["date"].iloc[0], window["date"].iloc[-1], False)
        with pytest.raises(impact.InsufficientImpactHistory):
            model.impact_bps(window["date"].iloc[3])
