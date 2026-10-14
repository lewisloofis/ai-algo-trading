"""
strategy_fit/test_entry_convention.py — D1 (same-close lookahead) and D2
(stride-2 de-overlap before the long-only mask) regression tests.

D1: a signal computed from close_s cannot be entered at close_s. The live bot
enters on the NEXT session (strategy/strategies/rebalance, target_emitter).
D2: the live-feasible de-overlap is sequential — scan in order; when flat and
a long signal fires at s, enter s+1, exit s+1+hold, resume scanning at the
exit day — not "every hold-th row over all rows, then mask".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.entry_convention import (
    DEFAULT_ENTRY,
    ENTRY_NEXT_CLOSE,
    ENTRY_NEXT_OPEN,
    ENTRY_SIGNAL_CLOSE_LOOKAHEAD,
    forward_return_series,
)
from strategy_fit.families_event import EVENT_DETECTORS, run_event_family
from strategy_fit.families_single_name import (
    daily_momentum_signal,
    mean_reversion_signal,
    run_single_name_family,
    volume_breakout_signal,
)
from strategy_fit.harness import deoverlap_daily, deoverlap_sequential


def _bars(n: int = 12) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    close = 100.0 + np.arange(n, dtype=float) ** 2  # strictly increasing
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.arange(n, dtype=float) + 1000.0,
        }
    )


class TestForwardReturnSeries:
    def test_default_is_live_feasible_next_close(self):
        assert DEFAULT_ENTRY == ENTRY_NEXT_CLOSE
        assert DEFAULT_ENTRY != ENTRY_SIGNAL_CLOSE_LOOKAHEAD

    def test_next_close_is_close_s1_to_close_s1_plus_hold(self):
        df = _bars()
        fr = forward_return_series(df, hold=2, entry=ENTRY_NEXT_CLOSE)
        c = df["close"].to_numpy()
        assert fr.iloc[3] == pytest.approx(c[6] / c[4] - 1.0)
        assert fr.iloc[-3:].isna().all()  # needs close_{s+1+hold}

    def test_next_open_is_open_s1_to_open_s1_plus_hold(self):
        df = _bars()
        fr = forward_return_series(df, hold=2, entry=ENTRY_NEXT_OPEN)
        o = df["open"].to_numpy()
        assert fr.iloc[3] == pytest.approx(o[6] / o[4] - 1.0)

    def test_lookahead_flag_reproduces_old_same_close_entry(self):
        df = _bars()
        fr = forward_return_series(df, hold=2, entry=ENTRY_SIGNAL_CLOSE_LOOKAHEAD)
        c = df["close"].to_numpy()
        assert fr.iloc[3] == pytest.approx(c[5] / c[3] - 1.0)

    def test_unknown_entry_rejected(self):
        with pytest.raises(ValueError):
            forward_return_series(_bars(), hold=2, entry="bogus")

    def test_next_open_without_open_column_rejected(self):
        with pytest.raises(ValueError):
            forward_return_series(_bars().drop(columns=["open"]), hold=2, entry=ENTRY_NEXT_OPEN)


@pytest.mark.parametrize(
    "fn", [daily_momentum_signal, mean_reversion_signal, volume_breakout_signal]
)
class TestSignalFunctionsEntry:
    def test_default_forward_return_never_uses_signal_close_as_entry(self, fn):
        df = _bars(40)
        out = fn(df, hold=2).set_index("date")
        close = df.set_index("date")["close"]
        for d, row in out.iterrows():
            i = close.index.get_loc(d)
            assert row["forward_return"] == pytest.approx(
                close.iloc[i + 3] / close.iloc[i + 1] - 1.0
            )

    def test_lookahead_flag_gives_old_numbers(self, fn):
        df = _bars(40)
        out = fn(df, hold=2, entry=ENTRY_SIGNAL_CLOSE_LOOKAHEAD).set_index("date")
        close = df.set_index("date")["close"]
        for d, row in out.iterrows():
            i = close.index.get_loc(d)
            assert row["forward_return"] == pytest.approx(
                close.iloc[i + 2] / close.iloc[i] - 1.0
            )

    def test_drop_unpriced_false_keeps_the_latest_signal_rows(self, fn):
        df = _bars(40)
        out = fn(df, hold=2, drop_unpriced=False)
        assert out["date"].iloc[-1] == df["date"].iloc[-1]
        assert np.isnan(out["forward_return"].iloc[-1])


class TestDeoverlapSequential:
    def _frame(self, flags):
        return pd.DataFrame({"signal": [1.0 if f else -1.0 for f in flags]})

    def test_enters_on_first_signal_then_resumes_after_exit_day(self):
        # hold=2: signal at 1 -> enter 2, exit 4, resume scanning at 4.
        df = self._frame([0, 1, 1, 1, 1, 0, 1])
        kept = deoverlap_sequential(df, hold=2, eligible=df["signal"] > 0)
        assert kept.index.tolist() == [1, 4]

    def test_off_grid_signal_is_not_dropped(self):
        # Old stride-2 grid anchored at row 0 would keep rows 0,2,4 and drop
        # the only signal at row 1.
        df = self._frame([0, 1, 0, 0, 0])
        assert deoverlap_daily(df, hold=2).index.tolist() == [0, 2, 4]
        kept = deoverlap_sequential(df, hold=2, eligible=df["signal"] > 0)
        assert kept.index.tolist() == [1]

    def test_hold_one_has_stride_two(self):
        df = self._frame([1, 1, 1, 1, 1])
        kept = deoverlap_sequential(df, hold=1, eligible=df["signal"] > 0)
        assert kept.index.tolist() == [0, 2, 4]

    def test_uses_pos_column_when_rows_are_sparse(self):
        df = pd.DataFrame({"signal": [1.0, 1.0, 1.0], "pos": [3, 4, 6]})
        kept = deoverlap_sequential(df, hold=2, eligible=df["signal"] > 0, pos_col="pos")
        assert kept["pos"].tolist() == [3, 6]

    def test_returns_new_frame_and_does_not_mutate(self):
        df = self._frame([1, 1, 1])
        before = df.copy()
        kept = deoverlap_sequential(df, hold=1, eligible=df["signal"] > 0)
        assert kept is not df
        pd.testing.assert_frame_equal(df, before)

    def test_unknown_eligible_default_keeps_every_row_slot(self):
        df = self._frame([0, 0, 0, 0, 0])
        kept = deoverlap_sequential(df, hold=1)
        assert kept.index.tolist() == [0, 2, 4]


def _stub(signals, fwd):
    n = len(signals)

    def _fn(df, hold=1, entry=None):
        return pd.DataFrame(
            {
                "date": pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
                "close": np.full(n, 1000.0),
                "signal": np.asarray(signals, dtype=float),
                "forward_return": np.asarray(fwd, dtype=float),
            }
        )

    return _fn


class TestRunSingleNameFamilySequential:
    def _run(self, signals, fwd, hold=2):
        return run_single_name_family(
            df=pd.DataFrame({"date": [], "close": []}),
            signal_fn=_stub(signals, fwd),
            symbol="T",
            min_edge_to_clear_bps=40.0,
            family_name="f",
            horizon_trading_days=hold,
        )

    def test_off_grid_signal_day_is_still_traded(self):
        n = 120
        sig = np.full(n, -1.0)
        fwd = np.full(n, 0.0001)
        # Long signals only on odd rows, spaced 3 apart (>= hold+1): all
        # are tradeable under the sequential rule; the old grid kept only
        # even rows and would have dropped every one of them.
        long_rows = list(range(1, n, 3))[:40]
        sig[long_rows] = 1.0
        fwd[long_rows] = 0.01
        res = self._run(sig, fwd)
        assert res["status"] == "tested"
        assert res["eff_n"] == len(long_rows)
        assert res["gross_edge_bps"] == pytest.approx(100.0)

    def test_null_is_mean_over_every_priced_day(self):
        n = 120
        sig = np.full(n, -1.0)
        fwd = np.full(n, 0.0)
        long_rows = list(range(0, n, 3))[:40]
        sig[long_rows] = 1.0
        fwd[long_rows] = 0.01
        fwd[1::3] = -0.0002
        fwd[2::3] = -0.0002
        res = self._run(sig, fwd)
        assert res["null_edge_bps"] == pytest.approx(float(np.mean(fwd) * 1e4))

    def test_lookahead_entry_flag_restores_legacy_grid(self):
        n = 120
        sig = np.full(n, -1.0)
        fwd = np.full(n, 0.0001)
        long_rows = list(range(1, n, 3))[:40]  # odd rows: all off the stride-2 grid
        sig[long_rows] = 1.0
        fwd[long_rows] = 0.01
        res = run_single_name_family(
            df=pd.DataFrame({"date": [], "close": []}),
            signal_fn=_stub(sig, fwd),
            symbol="T",
            min_edge_to_clear_bps=40.0,
            family_name="f",
            horizon_trading_days=2,
            entry=ENTRY_SIGNAL_CLOSE_LOOKAHEAD,
        )
        # legacy: only even rows of long_rows (row%2==0) survive
        assert res["eff_n"] == sum(1 for r in long_rows if r % 2 == 0)


def _event_df(n=200, up_every=4):
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    close = np.full(n, 100.0)
    open_ = close.copy()
    volume = np.full(n, 1000.0)
    volume[::up_every] = 50000.0
    open_[::up_every] = 99.0  # up-day (close > open)
    return pd.DataFrame(
        {"date": dates, "open": open_, "high": close, "low": open_, "close": close, "volume": volume}
    )


class TestEventFamily:
    def test_detectors_default_to_next_close_entry(self):
        df = _event_df()
        ev = EVENT_DETECTORS["volume_spike_event"](df, symbol="X", hold=2)
        close = df.set_index("date")["close"]
        i = close.index.get_loc(ev["date"].iloc[0])
        assert ev["forward_return"].iloc[0] == pytest.approx(
            close.iloc[i + 3] / close.iloc[i + 1] - 1.0
        )

    def test_min_eff_n_is_gated_on_long_only_count_d3(self):
        # 40 events, all DOWN-day spikes (signal -1): mixed-sign count passes
        # MIN_EFF_N but the long-only count is 0 -> must be insufficient_n.
        df = _event_df(n=400, up_every=8)
        df["open"] = np.where(df["volume"] > 10000, 101.0, df["open"])  # down day
        res = run_event_family({"X": df}, "volume_spike_event", horizon_trading_days=1)
        assert res["status"] == "insufficient_n"
        assert res["eff_n"] == 0

    def test_sequential_deoverlap_uses_calendar_positions_not_event_rows(self, monkeypatch):
        # Events on every 2nd bar, hold=2 (stride 3): positions 0,2,4,6,8 ->
        # sequential takes 0, 4, 8 (each >= prev+3). A stride over sparse
        # event ROWS would take every 2nd event too, but for the wrong reason
        # and with the wrong spacing on irregular events: here 0,1,5,6 -> 0,5.
        def fake(df, symbol, hold, entry=DEFAULT_ENTRY):
            pos = np.array(list(range(0, 400, 2)) + [401, 402, 407, 408])
            return pd.DataFrame(
                {
                    "date": [str(p) for p in pos],
                    "symbol": symbol,
                    "signal": 1.0,
                    "forward_return": 0.01,
                    "pos": pos,
                }
            )

        monkeypatch.setitem(EVENT_DETECTORS, "fake_event", fake)
        res = run_event_family({"X": _event_df(n=10)}, "fake_event", horizon_trading_days=2)
        # 0,4,8,...,396 -> 100 events; then 401 (>=399), 407 (>=404) -> 102
        assert res["status"] == "tested"
        assert res["eff_n"] == 102
