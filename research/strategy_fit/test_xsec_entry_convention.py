"""
strategy_fit/test_xsec_entry_convention.py — D1 (lookahead entry) for the
cross-sectional family.

The ranking signal at session s uses close_s, so a live bot can only enter at
session s+1. These tests pin: forward returns route through entry_convention
(default next_close), the LOOKAHEAD flag reproduces the pre-fix numbers, the
equal-weight null uses the same timing as the strategy leg, and the periodic
rebalance re-ranks only at the exit day (s+1+hold).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.test_families_cross_sectional import _make_cross_sectional_hist

LOOKAHEAD = "signal_close_LOOKAHEAD"


def _hand_hist() -> dict[str, pd.DataFrame]:
    """5 names, 5 sessions. Only A moves; ranking at d1 puts A on top.

    A close: 100, 110, 121, 100, 100   (opens 100, 108, 120, 105, 100)
    B..E   : flat 100 (opens flat 100)
    """
    dates = [f"2025-03-0{i}" for i in range(3, 8)]
    a_close = [100.0, 110.0, 121.0, 100.0, 100.0]
    a_open = [100.0, 108.0, 120.0, 105.0, 100.0]
    out = {}
    for sym in ["A", "B", "C", "D", "E"]:
        out[sym] = pd.DataFrame(
            {
                "date": dates,
                "open": a_open if sym == "A" else [100.0] * 5,
                "close": a_close if sym == "A" else [100.0] * 5,
                "volume": [1e6] * 5,
            }
        )
    return out


class TestEntryConvention:
    SIGNAL_DAY = "2025-03-04"  # A's trailing 1d return (+10%) is the top rank

    def _row(self, entry):
        from strategy_fit.families_cross_sectional import compute_period_returns

        df = compute_period_returns(_hand_hist(), 1, entry)
        return df.set_index("date").loc[self.SIGNAL_DAY]

    def test_next_close_enters_session_after_signal(self):
        # close_{s+1}=121 -> close_{s+2}=100 : -17.355%; the other four are flat.
        row = self._row("next_close")
        assert row["long_ret"] == pytest.approx(100.0 / 121.0 - 1.0)
        assert row["null_ret"] == pytest.approx((100.0 / 121.0 - 1.0) / 5.0)

    def test_same_close_lookahead_reproduces_old_numbers(self):
        # close_s=110 -> close_{s+1}=121 : +10% (the unattainable pre-fix number).
        row = self._row(LOOKAHEAD)
        assert row["long_ret"] == pytest.approx(0.10)
        assert row["null_ret"] == pytest.approx(0.02)

    def test_next_open_uses_opens(self):
        # open_{s+1}=120 -> open_{s+2}=105 : -12.5%
        row = self._row("next_open")
        assert row["long_ret"] == pytest.approx(105.0 / 120.0 - 1.0)

    def test_default_entry_is_next_close(self):
        from strategy_fit.families_cross_sectional import compute_period_returns

        pd.testing.assert_frame_equal(
            compute_period_returns(_hand_hist(), 1),
            compute_period_returns(_hand_hist(), 1, "next_close"),
        )

    def test_unknown_entry_rejected(self):
        from strategy_fit.families_cross_sectional import compute_period_returns

        with pytest.raises(ValueError):
            compute_period_returns(_hand_hist(), 1, "close_to_close")

    def test_lookahead_ic_matches_pre_fix_formula(self):
        """Independent re-derivation of the old wide-frame IC (pct_change(h).shift(-h))."""
        from scipy import stats as sps

        from strategy_fit.families_cross_sectional import compute_period_returns

        hist = _make_cross_sectional_hist(n_symbols=8, n_days=40, signal_strength=0.5, seed=3)
        wide = pd.concat(
            [d.set_index("date")[["close"]].rename(columns={"close": s}) for s, d in hist.items()],
            axis=1,
        ).sort_index()
        factor = wide.pct_change(1)
        fwd = wide.pct_change(2).shift(-2)
        expected = {}
        for date in factor.index:
            f, r = factor.loc[date].dropna(), fwd.loc[date].dropna()
            common = f.index.intersection(r.index)
            if len(common) >= 5:
                ic, _ = sps.spearmanr(f[common], r[common])
                if np.isfinite(ic):
                    expected[date] = ic
        got = compute_period_returns(hist, 2, LOOKAHEAD).set_index("date")["ic"]
        assert set(got.index) == set(expected)
        for d, v in expected.items():
            assert got[d] == pytest.approx(v)

    def test_null_uses_same_timing_as_strategy_leg(self):
        from strategy_fit.entry_convention import forward_return_series
        from strategy_fit.families_cross_sectional import compute_period_returns

        hist = _make_cross_sectional_hist(n_symbols=8, n_days=40, signal_strength=0.5, seed=4)
        fwd = pd.concat(
            [
                pd.Series(forward_return_series(d, 3, "next_close").to_numpy(), index=d["date"], name=s)
                for s, d in hist.items()
            ],
            axis=1,
        )
        got = compute_period_returns(hist, 3, "next_close").set_index("date")["null_ret"]
        assert len(got) > 10
        for d in got.index:
            assert got[d] == pytest.approx(fwd.loc[d].dropna().mean())


class TestRebalanceSchedule:
    def test_periodic_rebalance_reranks_only_at_exit_day(self):
        from strategy_fit.families_cross_sectional import compute_period_returns, select_rebalance_periods

        hist = _make_cross_sectional_hist(n_symbols=8, n_days=60, signal_strength=0.5, seed=5)
        allp = compute_period_returns(hist, 2, "next_close")
        sel = select_rebalance_periods(allp, 2, "next_close")
        dates = list(allp["date"])
        pos = [dates.index(d) for d in sel["date"]]
        assert len(pos) > 5
        # rank on s, hold s+1..s+3, re-rank on exit day s+3
        assert all(b - a == 3 for a, b in zip(pos, pos[1:]))

    def test_lookahead_keeps_every_day(self):
        from strategy_fit.families_cross_sectional import compute_period_returns, select_rebalance_periods

        hist = _make_cross_sectional_hist(n_symbols=8, n_days=60, signal_strength=0.5, seed=5)
        allp = compute_period_returns(hist, 2, LOOKAHEAD)
        assert len(select_rebalance_periods(allp, 2, LOOKAHEAD)) == len(allp)


class TestFamilyPlumbing:
    def test_family_accepts_entry_and_stays_long_short_infeasible(self):
        from strategy_fit.families_cross_sectional import run_cross_sectional_family
        from strategy_fit.long_only import STATUS_LONG_SHORT_INFEASIBLE

        hist = _make_cross_sectional_hist(n_symbols=10, n_days=120, signal_strength=0.6)
        res = run_cross_sectional_family(hist, 1, entry="next_open")
        assert res["status"] == STATUS_LONG_SHORT_INFEASIBLE
        assert res["diagnostic"]["entry"] == "next_open"

    def test_diagnostic_long_leg_and_null_share_periods(self):
        from strategy_fit.families_cross_sectional import run_cross_sectional_family

        hist = _make_cross_sectional_hist(n_symbols=10, n_days=150, signal_strength=0.6)
        res = run_cross_sectional_family(hist, 2)
        d = res["diagnostic"]
        assert d["entry"] == "next_close"
        assert d["n_periods"] == res["eff_n"]
        assert d["edge_over_null_bps"] == pytest.approx(d["long_gross_bps"] - d["null_bps"])

    def test_grid_passes_entry_to_cross_sectional(self, monkeypatch):
        from strategy_fit import run_strategy_fit as rsf
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource

        seen = []
        real = rsf.run_cross_sectional_family

        def spy(*a, **kw):
            seen.append(kw.get("entry"))
            return real(*a, **kw)

        monkeypatch.setattr(rsf, "run_cross_sectional_family", spy)
        rsf.run_grid(StrategyFitFixtureDataSource(), entry="next_open")
        assert seen and set(seen) == {"next_open"}
