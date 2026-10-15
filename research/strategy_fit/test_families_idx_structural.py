"""
strategy_fit/test_families_idx_structural.py — tests for families_idx_structural.py.

Written BEFORE the module (TDD). The emphasis is on the failure modes that
manufacture fake edges in a daily alpha search:

  * lookahead (every signal must be a causal function of bars <= signal day;
    checked generically by truncation invariance),
  * the D1 entry convention (the signal-day close is NOT tradeable),
  * un-fillable entries (limit-locked bars, zero-volume days),
  * bad bars (unadjusted splits) leaking into forward returns,
  * cost accounting (turnover-only cost for held names, tick ladder),
  * the verdict gate (a result must clear ALL gates to PASS).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from strategy_fit import families_idx_structural as fx


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _hist(close, *, open_=None, high=None, low=None, volume=None, dividend=None,
          start="2020-01-06", foreign=None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    n = len(close)
    dates = pd.bdate_range(start, periods=n)
    open_ = close if open_ is None else np.asarray(open_, dtype=float)
    high = np.maximum(close, open_) if high is None else np.asarray(high, dtype=float)
    low = np.minimum(close, open_) if low is None else np.asarray(low, dtype=float)
    volume = np.full(n, 2_000_000.0) if volume is None else np.asarray(volume, dtype=float)
    dividend = np.zeros(n) if dividend is None else np.asarray(dividend, dtype=float)
    fb = np.zeros(n) if foreign is None else np.asarray(foreign, dtype=float)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "value": close * volume,
        "foreignbuy": np.maximum(fb, 0), "foreignsell": np.maximum(-fb, 0),
        "net_foreign": fb, "dividend": dividend,
    })


def _random_hist(n=420, n_syms=6, seed=0, price=1000.0) -> dict:
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(n_syms):
        r = rng.normal(0.0, 0.02, n)
        close = np.round(price * np.exp(np.cumsum(r)), 0)
        open_ = np.round(np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.004, n)), 0)
        vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
        fb = rng.normal(0, 1e8, n)
        div = np.zeros(n)
        if n > 70:
            div[rng.integers(30, n - 30, 2)] = 20.0
        out[f"S{k}"] = _hist(close, open_=open_, volume=vol, dividend=div, foreign=fb)
    return out


def _panel(hist=None, **kw):
    hist = _random_hist(**kw) if hist is None else hist
    return fx.build_panel(hist, min_names_trading=2)


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------

class TestBuildPanel:
    def test_union_calendar_and_shapes(self):
        p = _panel(n=100, n_syms=3)
        assert list(p.close.columns) == ["S0", "S1", "S2"]
        assert len(p.dates) == 100
        assert p.close.index.equals(p.volume.index)

    def test_holiday_rows_with_no_trading_are_dropped(self):
        hist = _random_hist(n=60, n_syms=3)
        for df in hist.values():
            df.loc[10, "volume"] = 0.0     # market-wide holiday row
            df.loc[10, "value"] = 0.0
        p = fx.build_panel(hist, min_names_trading=2)
        assert len(p.dates) == 59
        assert pd.Timestamp(hist["S0"].loc[10, "date"]) not in p.dates

    def test_value_falls_back_to_close_times_volume(self):
        hist = _random_hist(n=40, n_syms=2)
        hist["S0"].loc[:, "value"] = 0.0
        p = fx.build_panel(hist, min_names_trading=1)
        assert np.allclose(p.value_idr["S0"].values, (p.close["S0"] * p.volume["S0"]).values)

    def test_non_positive_prices_become_nan(self):
        hist = _random_hist(n=40, n_syms=2)
        hist["S0"].loc[5, ["open", "high", "low", "close"]] = 0.0
        p = fx.build_panel(hist, min_names_trading=1)
        assert np.isnan(p.close["S0"].iloc[5]) and np.isnan(p.open["S0"].iloc[5])
        assert np.isfinite(np.log(p.close["S1"])).all()

    def test_date_objects_accepted(self):
        hist = _random_hist(n=30, n_syms=2)
        for df in hist.values():
            df["date"] = pd.to_datetime(df["date"]).dt.date
        p = fx.build_panel(hist, min_names_trading=1)
        assert len(p.dates) == 30


# ---------------------------------------------------------------------------
# Causality: truncating the future must not change any past signal value
# ---------------------------------------------------------------------------

def _truncate(hist: dict, n_keep: int) -> dict:
    return {s: df.iloc[:n_keep].copy() for s, df in hist.items()}


class TestCausality:
    CUT = 330

    @pytest.mark.parametrize("name", sorted(fx.FACTORS))
    def test_factor_is_causal(self, name):
        hist = _random_hist(n=420, n_syms=5, seed=3)
        full = fx.FACTORS[name](fx.build_panel(hist, min_names_trading=2))
        part = fx.FACTORS[name](fx.build_panel(_truncate(hist, self.CUT), min_names_trading=2))
        a = full.iloc[: self.CUT]
        b = part.iloc[: self.CUT]
        pd.testing.assert_frame_equal(a, b, check_exact=False, atol=1e-12, rtol=1e-9)

    def test_eligibility_is_causal(self):
        hist = _random_hist(n=420, n_syms=5, seed=4)
        kw = dict(min_adv_idr=1e8, min_price=100.0, min_history=100)
        full = fx.eligibility(fx.build_panel(hist, min_names_trading=2), **kw)
        part = fx.eligibility(fx.build_panel(_truncate(hist, self.CUT), min_names_trading=2), **kw)
        pd.testing.assert_frame_equal(full.iloc[: self.CUT], part.iloc[: self.CUT])

    @pytest.mark.parametrize("kind", ["up", "down"])
    def test_limit_event_detector_is_causal(self, kind):
        hist = _random_hist(n=420, n_syms=5, seed=5)
        # plant an extreme close-at-high day so the mask is non-trivial
        df = hist["S0"]
        i = 200
        df.loc[i, "close"] = df.loc[i - 1, "close"] * (1.25 if kind == "up" else 0.8)
        df.loc[i, "high"] = df.loc[i, "close"] if kind == "up" else df.loc[i - 1, "close"]
        df.loc[i, "low"] = df.loc[i - 1, "close"] if kind == "up" else df.loc[i, "close"]
        full = fx.detect_limit_events(fx.build_panel(hist, min_names_trading=2), kind)
        part = fx.detect_limit_events(fx.build_panel(_truncate(hist, self.CUT), min_names_trading=2), kind)
        pd.testing.assert_frame_equal(full.iloc[: self.CUT], part.iloc[: self.CUT])
        assert bool(full.iloc[:, 0].sum() >= 1)

    def test_forward_return_uses_only_later_bars_by_design(self):
        """fwd_total_return IS forward-looking by construction; the check is that
        it reads strictly bars > s (a change to bar s must not alter row s)."""
        hist = _random_hist(n=120, n_syms=3, seed=6)
        base = fx.fwd_total_return(fx.build_panel(hist, min_names_trading=2), 3)
        hist["S1"].loc[50, "close"] *= 1.3
        hist["S1"].loc[50, "high"] = max(hist["S1"].loc[50, "high"], hist["S1"].loc[50, "close"])
        changed = fx.fwd_total_return(fx.build_panel(hist, min_names_trading=2), 3)
        assert base["S1"].iloc[50] == pytest.approx(changed["S1"].iloc[50], nan_ok=True)


# ---------------------------------------------------------------------------
# Factor values
# ---------------------------------------------------------------------------

class TestFactorValues:
    def test_mom_12_1_skips_last_month(self):
        close = np.linspace(100, 400, 300)
        p = fx.build_panel({"A": _hist(close), "B": _hist(close)}, min_names_trading=1)
        f = fx.factor_mom_12_1(p)
        t = 280
        assert f["A"].iloc[t] == pytest.approx(close[t - 21] / close[t - 252] - 1.0)
        assert np.isnan(f["A"].iloc[100])

    def test_low_vol_prefers_calm_name(self):
        rng = np.random.default_rng(1)
        calm = 1000 * np.exp(np.cumsum(rng.normal(0, 0.005, 200)))
        wild = 1000 * np.exp(np.cumsum(rng.normal(0, 0.05, 200)))
        p = fx.build_panel({"CALM": _hist(calm), "WILD": _hist(wild)}, min_names_trading=1)
        f = fx.factor_low_vol(p)
        assert f["CALM"].iloc[150] > f["WILD"].iloc[150]

    def test_high52w_is_one_at_a_new_high(self):
        close = np.linspace(100, 300, 300)
        p = fx.build_panel({"A": _hist(close)}, min_names_trading=1)
        assert fx.factor_high52w(p)["A"].iloc[290] == pytest.approx(1.0)

    def test_div_yield_counts_only_past_dividends(self):
        n = 300
        div = np.zeros(n)
        div[100] = 50.0
        div[200] = 50.0
        p = fx.build_panel({"A": _hist(np.full(n, 1000.0), dividend=div)}, min_names_trading=1)
        f = fx.factor_div_yield(p)["A"]
        assert f.iloc[99] == pytest.approx(0.0)
        assert f.iloc[100] == pytest.approx(0.05)
        assert f.iloc[250] == pytest.approx(0.10)

    def test_foreign_accum_nan_without_foreign_activity(self):
        close = np.linspace(100, 200, 200)
        p = fx.build_panel({"A": _hist(close)}, min_names_trading=1)
        assert fx.factor_foreign_accum(p)["A"].isna().all()

    def test_rev_1m_is_negative_of_trailing_return(self):
        close = np.linspace(100, 200, 100)
        p = fx.build_panel({"A": _hist(close)}, min_names_trading=1)
        assert fx.factor_rev_1m(p)["A"].iloc[60] == pytest.approx(-(close[60] / close[39] - 1.0))

    def test_overnight_persist_is_mean_of_open_over_prev_close(self):
        n = 120
        close = np.full(n, 1000.0)
        open_ = np.full(n, 1010.0)
        p = fx.build_panel({"A": _hist(close, open_=open_)}, min_names_trading=1)
        assert fx.factor_overnight_persist(p)["A"].iloc[100] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Forward returns: D1 convention, dividends, un-fillable entries, bad bars
# ---------------------------------------------------------------------------

class TestForwardReturn:
    def test_next_close_entry_hold1(self):
        close = np.array([100, 110, 121, 133.1, 133.1, 133.1], dtype=float)
        p = fx.build_panel({"A": _hist(close)}, min_names_trading=1)
        f = fx.fwd_total_return(p, 1)["A"]
        # signal day 0 -> enter close_1 (110) -> exit close_2 (121): +10%
        assert f.iloc[0] == pytest.approx(0.10)
        # the day-0 -> day-1 jump (the move the signal could not trade) is excluded
        assert f.iloc[0] != pytest.approx(0.10 + 0.10 + 0.01, abs=0.05)

    def test_next_open_entry(self):
        close = np.array([100, 100, 100, 100, 100], dtype=float)
        open_ = np.array([100, 100, 105, 110, 110], dtype=float)
        p = fx.build_panel({"A": _hist(close, open_=open_)}, min_names_trading=1)
        f = fx.fwd_total_return(p, 1, entry="next_open")["A"]
        assert f.iloc[0] == pytest.approx(105 / 100 - 1.0)  # open_1 -> open_2

    def test_lookahead_entry_not_available(self):
        p = _panel(n=60, n_syms=2)
        with pytest.raises(ValueError):
            fx.fwd_total_return(p, 1, entry="signal_close_LOOKAHEAD")

    def test_dividend_inside_window_added_net_of_tax(self):
        n = 30
        close = np.full(n, 1000.0)
        div = np.zeros(n)
        div[12] = 40.0
        p = fx.build_panel({"A": _hist(close, dividend=div)}, min_names_trading=1)
        f = fx.fwd_total_return(p, 5, div_tax=0.10)["A"]
        # window is closes 11 -> 16 for s=10 and ex-day 12 is inside
        assert f.iloc[10] == pytest.approx(40.0 * 0.9 / 1000.0)
        # ex-day at the entry bar (s+1 == 12) is NOT collected
        assert f.iloc[11] == pytest.approx(0.0)
        # ex-day at the exit bar is collected
        assert f.iloc[7] == pytest.approx(40.0 * 0.9 / 1000.0)

    def test_untradeable_entry_day_is_nan(self):
        close = np.full(20, 100.0)
        vol = np.full(20, 1e6)
        vol[6] = 0.0
        p = fx.build_panel({"A": _hist(close, volume=vol), "B": _hist(close)}, min_names_trading=1)
        f = fx.fwd_total_return(p, 2)["A"]
        assert np.isnan(f.iloc[5])      # entry day 6 had zero volume
        assert not np.isnan(f.iloc[4])

    def test_bad_bar_inside_window_is_nan(self):
        close = np.full(30, 1000.0)
        close[15:] = 400.0               # unadjusted 2.5:1 split, -60%
        p = fx.build_panel({"A": _hist(close)}, min_names_trading=1)
        f = fx.fwd_total_return(p, 3)["A"]
        assert np.isnan(f.iloc[13])      # window days 14..17 contains the jump
        assert np.isnan(f.iloc[14])
        assert not np.isnan(f.iloc[16])  # entirely after the jump
        assert not np.isnan(f.iloc[5])   # entirely before the jump


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------

class TestCosts:
    @pytest.mark.parametrize("price", [50, 199, 200, 499, 500, 1999, 2000, 4999, 5000, 12000])
    def test_tick_bps_matches_screener_ladder(self, price):
        from screener.metrics import min_spread_bps
        assert float(fx.tick_bps(np.array([price], dtype=float))[0]) == pytest.approx(min_spread_bps(price))

    def test_round_trip_fee_matches_harness(self):
        from strategy_fit.run_strategy_fit import ROUND_TRIP_BPS
        assert fx.ROUND_TRIP_FEE_BPS == ROUND_TRIP_BPS

    def test_trade_cost_is_floor_plus_impact(self):
        price = np.array([5000.0])
        adv = np.array([50e9])
        vol = np.array([200.0])
        c = fx.trade_cost_bps(price, adv, vol, 30e6, ["ZZZZ"])
        floor = fx.tick_bps(price) + fx.ROUND_TRIP_FEE_BPS
        assert c[0] > floor[0]
        assert c[0] - floor[0] < 25.0   # impact is small at ~0.06% participation

    def test_impact_grows_with_notional(self):
        args = (np.array([1000.0]), np.array([5e9]), np.array([300.0]))
        lo = fx.trade_cost_bps(*args, 10e6, ["ZZZZ"])[0]
        hi = fx.trade_cost_bps(*args, 1e9, ["ZZZZ"])[0]
        assert hi > lo

    def test_zero_adv_is_degenerate_not_free(self):
        c = fx.trade_cost_bps(np.array([1000.0]), np.array([0.0]), np.array([300.0]), 30e6, ["ZZZZ"])
        assert c[0] > 1000.0


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

class TestEligibility:
    def test_illiquid_and_penny_names_excluded(self):
        n = 300
        rich = _hist(np.full(n, 1000.0), volume=np.full(n, 5e6))          # 5bn/day
        thin = _hist(np.full(n, 1000.0), volume=np.full(n, 1e4))          # 10m/day
        penny = _hist(np.full(n, 50.0), volume=np.full(n, 1e9))
        p = fx.build_panel({"RICH": rich, "THIN": thin, "PENNY": penny}, min_names_trading=1)
        e = fx.eligibility(p, min_adv_idr=2e9, min_price=100.0, min_history=100)
        assert bool(e["RICH"].iloc[250]) is True
        assert bool(e["THIN"].iloc[250]) is False
        assert bool(e["PENNY"].iloc[250]) is False

    def test_needs_min_history(self):
        n = 300
        p = fx.build_panel({"A": _hist(np.full(n, 1000.0), volume=np.full(n, 5e6))}, min_names_trading=1)
        e = fx.eligibility(p, min_adv_idr=1e9, min_history=252)
        assert not e["A"].iloc[200]
        assert e["A"].iloc[280]

    def test_stale_zero_volume_names_excluded(self):
        n = 300
        vol = np.full(n, 5e6)
        vol[230:] = 0.0
        p = fx.build_panel({"A": _hist(np.full(n, 1000.0), volume=vol), "B": _hist(np.full(n, 1000.0))},
                           min_names_trading=1)
        e = fx.eligibility(p, min_adv_idr=1e9, min_history=100, max_zero_vol_days=3)
        assert not e["A"].iloc[290]


# ---------------------------------------------------------------------------
# Limit-class events
# ---------------------------------------------------------------------------

class TestLimitEvents:
    def _one(self, ret, at_high, kind):
        n = 40
        close = np.full(n, 1000.0)
        high = close.copy()
        low = close.copy()
        close[20] = 1000.0 * (1 + ret)
        if kind == "up":
            high[20] = close[20] if at_high else close[20] * 1.05
            low[20] = 1000.0
        else:
            low[20] = close[20] if at_high else close[20] * 0.95
            high[20] = 1000.0
        close[21:] = close[20]
        high[21:] = close[20]
        low[21:] = close[20]
        p = fx.build_panel({"A": _hist(close, high=high, low=low)}, min_names_trading=1)
        return fx.detect_limit_events(p, kind)["A"]

    def test_up_event_requires_big_move_closing_at_high(self):
        assert bool(self._one(0.25, True, "up").iloc[20])
        assert not bool(self._one(0.25, False, "up").iloc[20])      # faded off the high
        assert not bool(self._one(0.10, True, "up").iloc[20])       # not big enough
        assert self._one(0.25, True, "up").sum() == 1

    def test_down_event_requires_big_drop_closing_at_low(self):
        assert bool(self._one(-0.16, True, "down").iloc[20])
        assert not bool(self._one(-0.16, False, "down").iloc[20])
        assert not bool(self._one(-0.05, True, "down").iloc[20])

    def test_unknown_kind_rejected(self):
        p = _panel(n=40, n_syms=2)
        with pytest.raises(ValueError):
            fx.detect_limit_events(p, "sideways")

    def test_locked_entry_flag(self):
        n = 40
        close = np.full(n, 1000.0)
        low = close.copy()
        high = close.copy()
        close[20] = 1250.0       # signal day: limit up
        high[20] = 1250.0
        close[21] = 1560.0       # next day: entire range above +19% of 1250 -> locked
        low[21] = 1500.0
        high[21] = 1560.0
        close[22:] = 1560.0
        low[22:] = 1560.0
        high[22:] = 1560.0
        p = fx.build_panel({"A": _hist(close, low=low, high=high)}, min_names_trading=1)
        locked = fx.locked_up_entry(p)["A"]
        assert bool(locked.iloc[20])     # entry day (s+1 = 21) is locked
        assert not bool(locked.iloc[10])


# ---------------------------------------------------------------------------
# Event trade construction
# ---------------------------------------------------------------------------

class TestEventTrades:
    def _setup(self, n=80):
        rng = np.random.default_rng(2)
        hist = {}
        for k in range(4):
            close = np.round(2000 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), 0)
            hist[f"S{k}"] = _hist(close, volume=np.full(n, 5e6))
        p = fx.build_panel(hist, min_names_trading=2)
        elig = pd.DataFrame(True, index=p.dates, columns=p.close.columns)
        return p, elig

    def test_sequential_no_overlap_within_name(self):
        p, elig = self._setup()
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[[10, 11, 12, 14, 30], 0] = True
        trades = fx.build_event_trades(p, mask, hold=3, eligible=elig)
        pos = sorted(trades["pos"].tolist())
        # 10 taken; 11,12 overlap; 14 (>= 10+3) taken; 30 taken
        assert pos == [10, 14, 30]

    def test_trade_return_matches_forward_return_and_entry_convention(self):
        p, elig = self._setup()
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[20, 1] = True
        t = fx.build_event_trades(p, mask, hold=2, eligible=elig)
        assert len(t) == 1
        s = 20
        expected = p.close["S1"].iloc[s + 3] / p.close["S1"].iloc[s + 1] - 1.0
        assert t["gross"].iloc[0] == pytest.approx(expected)

    def test_ineligible_signal_is_dropped(self):
        p, elig = self._setup()
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[20, 1] = True
        elig.iloc[20, 1] = False
        assert len(fx.build_event_trades(p, mask, hold=2, eligible=elig)) == 0

    def test_trade_without_forward_window_is_dropped(self):
        p, elig = self._setup(n=40)
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[-2, 0] = True
        assert len(fx.build_event_trades(p, mask, hold=2, eligible=elig)) == 0

    def test_null_columns_present_and_name_year_null_is_name_mean(self):
        p, elig = self._setup()
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[20, 2] = True
        t = fx.build_event_trades(p, mask, hold=2, eligible=elig)
        fwd = fx.fwd_total_return(p, 2)["S2"]
        yr = p.dates[20].year
        expect = fwd[fwd.index.year == yr].mean()
        assert t["null_name"].iloc[0] == pytest.approx(expect)
        assert np.isfinite(t["null_mkt"].iloc[0])

    def test_skip_locked_drops_unfillable_entries(self):
        n = 60
        close = np.full(n, 1000.0)
        high = close.copy()
        low = close.copy()
        close[20] = 1250.0
        high[20] = 1250.0
        close[21:] = 1560.0          # locked up from day 21 on
        high[21:] = 1560.0
        low[21:] = 1500.0
        hist = {"A": _hist(close, high=high, low=low), "B": _hist(np.full(n, 1000.0))}
        p = fx.build_panel(hist, min_names_trading=1)
        elig = pd.DataFrame(True, index=p.dates, columns=p.close.columns)
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[20, 0] = True
        assert len(fx.build_event_trades(p, mask, hold=2, eligible=elig, skip_locked=False)) == 1
        assert len(fx.build_event_trades(p, mask, hold=2, eligible=elig, skip_locked=True)) == 0

    def test_recost_scales_impact_not_floor(self):
        p, elig = self._setup()
        mask = pd.DataFrame(False, index=p.dates, columns=p.close.columns)
        mask.iloc[20, 1] = True
        t = fx.build_event_trades(p, mask, hold=2, eligible=elig, notional_idr=10e6)
        big = fx.recost(t, 10e9)
        assert big["cost_bps"].iloc[0] >= t["cost_bps"].iloc[0]
        assert big["floor_bps"].iloc[0] == t["floor_bps"].iloc[0]


# ---------------------------------------------------------------------------
# Dividend + calendar schedules
# ---------------------------------------------------------------------------

class TestDividendSchedule:
    def _panel_with_div(self, x=60, amount=30.0, price=1000.0, n=120):
        div = np.zeros(n)
        div[x] = amount
        hist = {"A": _hist(np.full(n, price), dividend=div), "B": _hist(np.full(n, price))}
        return fx.build_panel(hist, min_names_trading=1), x

    @pytest.mark.parametrize("variant,entry_off,hold", [("runup", -6, 5), ("cum_capture", -1, 2), ("post_ex", 0, 5)])
    def test_schedule_positions(self, variant, entry_off, hold):
        p, x = self._panel_with_div()
        mask, h = fx.dividend_event_mask(p, variant, min_yield=0.02)
        assert h == hold
        # decision day s is entry-1 so that next_close entry lands on the intended bar
        s = x + entry_off - 1
        assert bool(mask["A"].iloc[s])
        assert mask["A"].sum() == 1
        assert mask["B"].sum() == 0

    def test_yield_filter(self):
        p, _ = self._panel_with_div(amount=10.0)      # 1% < 2%
        mask, _ = fx.dividend_event_mask(p, "runup", min_yield=0.02)
        assert mask.values.sum() == 0

    def test_schedule_does_not_use_post_decision_prices(self):
        p, x = self._panel_with_div()
        m1, _ = fx.dividend_event_mask(p, "runup", min_yield=0.02)
        n = 120
        s = x - 6 - 1
        close = np.full(n, 1000.0)
        close[s + 1:] = 1000.0 * 1.4        # post-decision price change must not matter
        div = np.zeros(n)
        div[x] = 30.0
        p2 = fx.build_panel({"A": _hist(close, dividend=div), "B": _hist(np.full(n, 1000.0))},
                            min_names_trading=1)
        m2, _ = fx.dividend_event_mask(p2, "runup", min_yield=0.02)
        assert bool(m1["A"].iloc[s]) and bool(m2["A"].iloc[s])

    def test_unknown_variant(self):
        p, _ = self._panel_with_div()
        with pytest.raises(ValueError):
            fx.dividend_event_mask(p, "nope", min_yield=0.02)


class TestCalendarWindows:
    def _daily_panel(self, start="2019-01-01", periods=700):
        dates = pd.bdate_range(start, periods=periods)
        close = np.linspace(1000, 1500, periods)
        h = _hist(close, start=start)
        return fx.build_panel({"A": h, "B": h.copy()}, min_names_trading=1), dates

    def test_turn_of_month_windows(self):
        p, _ = self._daily_panel()
        w = fx.calendar_windows(p, "turn_of_month")
        assert len(w) > 15
        for entry_pos, hold in w:
            d_in = p.dates[entry_pos]
            d_next = p.dates[entry_pos + 1]
            assert d_in.month != d_next.month          # entry is the month's last trading day
            assert hold == 3
            assert p.dates[entry_pos + 3].month == d_next.month

    def test_pre_lebaran_window_ends_before_closure(self):
        p, _ = self._daily_panel("2019-01-01", 900)
        w = fx.calendar_windows(p, "pre_lebaran", eid_dates=["2019-06-05", "2020-05-24"])
        assert len(w) == 2
        for (entry_pos, hold), eid in zip(w, ["2019-06-05", "2020-05-24"]):
            exit_date = p.dates[entry_pos + hold]
            assert exit_date < pd.Timestamp(eid)
            assert p.dates[entry_pos + hold + 1] >= pd.Timestamp(eid) - pd.Timedelta(days=1)

    def test_windows_without_history_dropped(self):
        p, _ = self._daily_panel("2019-01-01", 100)
        assert fx.calendar_windows(p, "pre_lebaran", eid_dates=["2030-01-01"]) == []

    def test_unknown_kind(self):
        p, _ = self._daily_panel()
        with pytest.raises(ValueError):
            fx.calendar_windows(p, "full_moon")


# ---------------------------------------------------------------------------
# Factor backtest engine
# ---------------------------------------------------------------------------

def _planted_panel(n_days=600, n_syms=30, seed=7, edge=0.004):
    """Each name has a persistent latent 'alpha' that drives its daily mean
    return; factor = trailing realised mean is a legitimate, causal signal."""
    rng = np.random.default_rng(seed)
    alpha = np.linspace(-edge, edge, n_syms)
    hist = {}
    for k in range(n_syms):
        r = alpha[k] + rng.normal(0, 0.01, n_days)
        close = 2000 * np.exp(np.cumsum(r))
        hist[f"S{k:02d}"] = _hist(close, volume=np.full(n_days, 1e7), start="2018-01-01")
    return fx.build_panel(hist, min_names_trading=5)


class TestFactorBacktest:
    def test_columns_and_period_structure(self):
        p = _planted_panel()
        f = fx.factor_mom_12_1(p)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        per = fx.run_factor_backtest(p, f, hold=21, eligible=elig, min_universe=10)
        assert {"date", "pos", "n_elig", "n_top", "gross", "null", "cost_bps", "turnover"} <= set(per.columns)
        assert len(per) > 5
        # contiguous non-overlapping windows
        assert (np.diff(per["pos"].values) == 21).all()

    def test_perfect_persistent_signal_has_positive_excess(self):
        p = _planted_panel(edge=0.006)
        f = fx.factor_mom_12_1(p)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        per = fx.run_factor_backtest(p, f, hold=21, eligible=elig, min_universe=10)
        assert (per["gross"] - per["null"]).mean() > 0.005

    def test_random_walk_has_no_edge(self):
        rng = np.random.default_rng(11)
        n_days, n_syms = 900, 40
        hist = {}
        for k in range(n_syms):
            close = 2000 * np.exp(np.cumsum(rng.normal(0, 0.015, n_days)))
            hist[f"S{k:02d}"] = _hist(close, volume=np.full(n_days, 1e7), start="2015-01-01")
        p = fx.build_panel(hist, min_names_trading=5)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        per = fx.run_factor_backtest(p, fx.factor_mom_12_1(p), hold=21, eligible=elig, min_universe=10)
        ex = (per["gross"] - per["null"]).values
        t = ex.mean() / (ex.std(ddof=1) / math.sqrt(len(ex)))
        assert abs(t) < 3.5

    def test_day_after_signal_jump_is_not_captured_d1(self):
        """Names ranked top at s earn their jump at s+1 in the data; the D1
        entry (close_{s+1}) must NOT collect it."""
        n_days, n_syms = 500, 20
        rng = np.random.default_rng(5)
        base = {k: 2000 * np.exp(np.cumsum(rng.normal(0, 0.002, n_days))) for k in range(n_syms)}
        hist = {}
        for k in range(n_syms):
            hist[f"S{k:02d}"] = _hist(base[k], volume=np.full(n_days, 1e7), start="2018-01-01")
        p = fx.build_panel(hist, min_names_trading=5)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        # cheating factor: knows that close_{s+1}/close_s will be the jump -> after D1
        # the jump is excluded, so a factor equal to the (s -> s+1) return earns ~0
        c = p.close
        cheat = c.shift(-1) / c - 1.0
        per = fx.run_factor_backtest(p, cheat, hold=5, eligible=elig, min_universe=10)
        assert abs((per["gross"] - per["null"]).mean()) < 0.003

    def test_unchanged_holdings_cost_nothing(self):
        n_days, n_syms = 500, 20
        hist = {}
        for k in range(n_syms):
            hist[f"S{k:02d}"] = _hist(np.full(n_days, 2000.0 + k), volume=np.full(n_days, 1e7), start="2018-01-01")
        p = fx.build_panel(hist, min_names_trading=5)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        static = pd.DataFrame(
            np.tile(np.arange(n_syms, dtype=float), (len(p.dates), 1)), index=p.dates, columns=p.close.columns)
        per = fx.run_factor_backtest(p, static, hold=21, eligible=elig, min_universe=10)
        assert per["cost_bps"].iloc[0] > 0           # initial build
        assert (per["cost_bps"].iloc[1:] == 0).all()  # same book -> no trades
        assert (per["turnover"].iloc[1:] == 0).all()

    def test_full_turnover_costs_a_round_trip(self):
        n_days, n_syms = 500, 20
        hist = {f"S{k:02d}": _hist(np.full(n_days, 2000.0), volume=np.full(n_days, 1e7), start="2018-01-01")
                for k in range(n_syms)}
        p = fx.build_panel(hist, min_names_trading=5)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        # alternate the ranking each period so the top book fully rotates
        pos = np.arange(len(p.dates))
        flip = ((pos // 21) % 2).astype(float)
        vals = np.where(flip[:, None] == 0, np.arange(n_syms)[None, :], (n_syms - np.arange(n_syms))[None, :])
        fac = pd.DataFrame(vals.astype(float), index=p.dates, columns=p.close.columns)
        per = fx.run_factor_backtest(p, fac, hold=21, eligible=elig, min_universe=10)
        floor = float(fx.tick_bps(np.array([2000.0]))[0]) + fx.ROUND_TRIP_FEE_BPS
        steady = per["cost_bps"].iloc[2:].mean()
        assert steady >= 0.9 * floor    # sell old half-RT + buy new half-RT = ~1 RT
        assert steady <= 1.6 * floor
        assert per["turnover"].iloc[2:].mean() == pytest.approx(1.0, abs=1e-9)

    def test_phase_shifts_rebalance_dates(self):
        p = _planted_panel()
        f = fx.factor_mom_12_1(p)
        elig = fx.eligibility(p, min_adv_idr=1e8, min_history=100)
        a = fx.run_factor_backtest(p, f, hold=21, eligible=elig, min_universe=10, phase=0)
        b = fx.run_factor_backtest(p, f, hold=21, eligible=elig, min_universe=10, phase=7)
        assert (b["pos"].values[: len(a) - 1] - a["pos"].values[: len(a) - 1] == 7).all()


# ---------------------------------------------------------------------------
# Statistics + verdict
# ---------------------------------------------------------------------------

class TestStats:
    def test_t_and_p(self):
        x = np.array([10.0, 12.0, 8.0, 11.0, 9.0, 10.5, 9.5, 10.0])
        t = fx.t_stat(x)
        assert t == pytest.approx(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))
        assert 0.0 < fx.p_two_sided(t, len(x)) < 0.001

    def test_degenerate_inputs(self):
        assert math.isnan(fx.t_stat(np.array([1.0])))
        assert math.isnan(fx.t_stat(np.array([2.0, 2.0, 2.0])))
        assert fx.p_two_sided(float("nan"), 5) == 1.0

    def test_bonferroni(self):
        assert fx.bonferroni(0.01, 23) == pytest.approx(0.23)
        assert fx.bonferroni(0.2, 23) == 1.0

    def test_chronological_split_is_60_40(self):
        s = pd.Series(np.arange(100.0))
        a, b = fx.chrono_split(s)
        assert len(a) == 60 and len(b) == 40
        assert a.max() < b.min()


def _good():
    return dict(
        n=400, n_clusters=300, net_excess_name_bps=60.0, net_excess_mkt_bps=55.0, t_net_name=4.5,
        p_bonf=0.001, oos_net_excess_bps=40.0, oos_n_clusters=120, ex_top5_net_excess_bps=30.0,
        net_excess_name_10x_bps=20.0, gross_excess_name_bps=110.0, cost_bps=50.0, p_raw=0.00005,
        abs_net_bps=45.0, bear_abs_net_bps=15.0,
    )


class TestVerdict:
    def test_all_gates_pass(self):
        assert fx.verdict(**_good())[0] == "PASS"

    @pytest.mark.parametrize("field,val", [
        ("net_excess_name_bps", -1.0), ("net_excess_mkt_bps", -1.0), ("oos_net_excess_bps", -5.0),
        ("ex_top5_net_excess_bps", -5.0), ("net_excess_name_10x_bps", -5.0),
    ])
    def test_any_failed_gate_blocks_pass(self, field, val):
        g = _good()
        g[field] = val
        assert fx.verdict(**g)[0] != "PASS"

    def test_bonferroni_failure_blocks_pass_but_can_be_promising(self):
        g = _good()
        g["p_bonf"] = 0.30
        g["p_raw"] = 0.01
        assert fx.verdict(**g)[0] == "promising"

    def test_negative_net_is_fail(self):
        g = _good()
        g.update(net_excess_name_bps=-30.0, net_excess_mkt_bps=-30.0, p_raw=0.5, p_bonf=1.0,
                 oos_net_excess_bps=-30.0, ex_top5_net_excess_bps=-60.0, net_excess_name_10x_bps=-40.0)
        assert fx.verdict(**g)[0] == "FAIL"

    def test_thin_oos_blocks_pass(self):
        g = _good()
        g["oos_n_clusters"] = 10
        assert fx.verdict(**g)[0] != "PASS"

    def test_nan_metrics_never_pass(self):
        g = _good()
        g["t_net_name"] = float("nan")
        g["net_excess_name_bps"] = float("nan")
        assert fx.verdict(**g)[0] == "FAIL"


# ---------------------------------------------------------------------------
# Overnight / intraday decomposition (descriptive)
# ---------------------------------------------------------------------------

class TestOvernightDecomposition:
    def test_decomposition_sums_to_close_to_close(self):
        n = 100
        close = np.cumprod(np.full(n, 1.001)) * 1000
        open_ = np.r_[1000.0, close[:-1]] * 1.002
        p = fx.build_panel({"A": _hist(close, open_=open_), "B": _hist(close, open_=open_)}, min_names_trading=1)
        elig = pd.DataFrame(True, index=p.dates, columns=p.close.columns)
        d = fx.overnight_intraday_decomposition(p, elig)
        assert d["overnight_bps"].iloc[-1] == pytest.approx(20.0, abs=1.0)
        total = (1 + d["overnight_bps"].iloc[-1] / 1e4) * (1 + d["intraday_bps"].iloc[-1] / 1e4) - 1
        assert total * 1e4 == pytest.approx(10.0, abs=1.5)


class TestAbsoluteReturnGate:
    def test_beating_always_long_while_losing_money_is_not_a_pass(self):
        g = _good()
        g["abs_net_bps"] = -20.0
        label, failed = fx.verdict(**g)
        assert label != "PASS" and "absolute_net>0" in failed

    def test_negative_in_bear_blocks_pass(self):
        g = _good()
        g["bear_abs_net_bps"] = -40.0
        label, failed = fx.verdict(**g)
        assert label != "PASS" and "absolute_net_in_bear>0" in failed

    def test_not_applicable_gates_are_skipped(self):
        g = _good()
        g["abs_net_bps"] = None
        g["bear_abs_net_bps"] = None
        assert fx.verdict(**g)[0] == "PASS"


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

def _regime_panel(n=700, seed=1):
    """Calm uptrend for 400 days, then a -40% decline over 150 days, then flat."""
    rng = np.random.default_rng(seed)
    r = np.r_[np.full(400, 0.0008), np.full(150, -0.0035), np.full(n - 550, 0.0)]
    hist = {}
    for k in range(8):
        close = 2000 * np.exp(np.cumsum(r + rng.normal(0, 0.003, n)))
        hist[f"S{k}"] = _hist(close, volume=np.full(n, 1e7), start="2018-01-01")
    return fx.build_panel(hist, min_names_trading=3)


class TestRegime:
    def test_bear_flag_off_in_uptrend_on_after_decline(self):
        p = _regime_panel()
        e = fx.eligibility(p, min_adv_idr=1e8, min_history=60)
        reg = fx.market_regime(p, e)
        assert not reg["bear"].iloc[300]
        assert reg["bear"].iloc[560]
        assert reg["drawdown"].iloc[560] < -0.15

    def test_hysteresis_removes_threshold_flicker(self):
        """A series that hovers around the -15% line must not toggle bear daily:
        the state, once on, stays on until drawdown recovers past the exit band."""
        n = 520
        rng = np.random.default_rng(3)
        base = np.r_[np.full(300, 0.0006), np.full(80, -0.0030), rng.normal(-0.0001, 0.012, n - 380)]
        hist = {f"S{k}": _hist(2000 * np.exp(np.cumsum(base + rng.normal(0, 0.001, n))),
                               volume=np.full(n, 1e7), start="2018-01-01") for k in range(6)}
        p = fx.build_panel(hist, min_names_trading=3)
        e = fx.eligibility(p, min_adv_idr=1e8, min_history=60)
        reg = fx.market_regime(p, e)
        raw = (reg["below_ma"] & reg["deep_dd"]).astype(int)
        flips_raw = int(raw.diff().abs().sum())
        flips_state = int(reg["bear"].astype(int).diff().abs().sum())
        assert flips_state <= flips_raw
        assert flips_state < flips_raw or flips_raw <= 2

    def test_state_machine_stays_on_until_exit_band(self):
        below = pd.Series([False, True, True, True, True, False, True, True, True])
        deep = pd.Series([False, True, False, False, False, False, False, False, False])
        dd = pd.Series([0.0, -0.16, -0.12, -0.10, -0.06, -0.05, -0.12, -0.20, -0.20])
        out = fx.bear_state(below, deep, dd, exit_dd=-0.075).tolist()
        # on at 1; stays on at -12%/-10% (not recovered); off at -6%;
        # at index 6/7 below-MA but not "deep" trigger -> stays off
        assert out == [False, True, True, True, False, False, False, False, False]

    def test_state_machine_retriggers(self):
        below = pd.Series([True, True, True, True])
        deep = pd.Series([True, False, False, True])
        dd = pd.Series([-0.2, -0.05, -0.1, -0.2])
        assert fx.bear_state(below, deep, dd, exit_dd=-0.075).tolist() == [True, False, False, True]

    def test_warmup_is_not_bear(self):
        p = _regime_panel()
        reg = fx.market_regime(p, fx.eligibility(p, min_adv_idr=1e8, min_history=60))
        assert not reg["bear"].iloc[:150].any()

    def test_regime_is_causal(self):
        hist = {}
        rng = np.random.default_rng(9)
        for k in range(6):
            hist[f"S{k}"] = _hist(1000 * np.exp(np.cumsum(rng.normal(-0.001, 0.02, 700))),
                                  volume=np.full(700, 1e7), start="2018-01-01")
        kw = dict(min_adv_idr=1e8, min_history=60)
        pf = fx.build_panel(hist, min_names_trading=3)
        pt = fx.build_panel({k: v.iloc[:500].copy() for k, v in hist.items()}, min_names_trading=3)
        a = fx.market_regime(pf, fx.eligibility(pf, **kw)).iloc[:500]
        b = fx.market_regime(pt, fx.eligibility(pt, **kw))
        pd.testing.assert_frame_equal(a, b, check_exact=False, atol=1e-9, rtol=1e-9)


class TestRegimeSplitAndFilter:
    def test_summary_splits_by_regime_with_counts(self):
        dates = pd.bdate_range("2020-01-01", periods=120)
        trades = pd.DataFrame({
            "symbol": "A", "pos": np.arange(120), "date": dates,
            "gross": np.where(np.arange(120) < 60, 0.02, -0.02), "null_name": 0.0, "null_mkt": 0.0,
            "floor_bps": 50.0, "impact_bps": 0.0, "cost_bps": 50.0, "coeff": 0.3, "adv": 1e10,
            "vol_bps": 200.0, "fin_bps": np.nan,
        })
        bear = pd.Series(np.arange(120) >= 60, index=dates)
        res = fx.summarize_event_trades(trades, m_tests=10, bear=bear)
        assert res["regime"]["nonbear"]["n_trades"] == 60 and res["regime"]["bear"]["n_trades"] == 60
        assert res["regime"]["nonbear"]["abs_net_bps"] == pytest.approx(150.0)
        assert res["regime"]["bear"]["abs_net_bps"] == pytest.approx(-250.0)
        assert res["bear_abs_net_bps"] == pytest.approx(-250.0)
        assert res["verdict"] != "PASS"

    def test_filter_scores_avoided_loss_minus_cost(self):
        p = _regime_panel()
        e = fx.eligibility(p, min_adv_idr=1e8, min_history=60)
        reg = fx.market_regime(p, e)
        # add a rebound so the first episode closes
        bear = reg["bear"].copy()
        bear.iloc[600:] = False
        eps, info = fx.regime_filter_episodes(p, e, bear, top_n=5)
        assert len(eps) == 1
        assert eps["gross"].iloc[0] == 0.0
        assert eps["null"].iloc[0] < 0.0          # basket fell while we were in cash
        assert eps["cost_bps"].iloc[0] > 0

    def test_open_episode_not_scored(self):
        p = _regime_panel()
        e = fx.eligibility(p, min_adv_idr=1e8, min_history=60)
        eps, info = fx.regime_filter_episodes(p, e, fx.market_regime(p, e)["bear"], top_n=5)
        assert eps.empty
        assert info["open_episode_since"] is not None
