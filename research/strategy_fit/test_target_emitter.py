"""
strategy_fit/test_target_emitter.py — tests for the daily target emitter
(plan 18 §8 step 2a): research signal -> target_portfolio.json.

The synthetic bars are built on the real IDX calendar so the hold / holiday
arithmetic under test is the production arithmetic. De-overlap is the backtest's SEQUENTIAL rule
(harness.deoverlap_sequential, D2 fix): when flat and a long signal fires at s,
enter s+1, exit s+1+hold, resume scanning at the exit day. Row numbers below
are signal-frame rows (row r = bar r+1).
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lib.market_calendar import CALENDAR
from strategy_fit import target_emitter as te
from strategy_fit.families_single_name import daily_momentum_signal

SPEC = te.SPECS["bnbr_momo_h2"]
LOCAL_BNBR = Path(__file__).resolve().parent.parent / "data" / "daily" / "BNBR.parquet"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trading_days_ending(last: date, n: int) -> list[date]:
    days = [last]
    while len(days) < n:
        days.append(CALENDAR.prev_trading_day(days[-1]))
    return list(reversed(days))


def _bars(last: date, closes: list[float]) -> pd.DataFrame:
    """Bars on consecutive IDX trading days ending at `last`."""
    days = _trading_days_ending(last, len(closes))
    return pd.DataFrame({
        "date": [d.isoformat() for d in days],
        "close": closes,
        "volume": [1_000_000] * len(closes),
    })


def _closes_with_up_rows(n: int, up_rows: set[int], start: float = 100.0) -> list[float]:
    """n closes; bar k (k >= 1) closes up vs bar k-1 iff k-1 is in up_rows.

    Signal-frame row r corresponds to bar r+1, so up_rows are signal-frame
    (grid) row numbers: row 0 is the second bar.
    """
    closes = [start]
    for k in range(1, n):
        closes.append(closes[-1] + (2.0 if (k - 1) in up_rows else -1.0))
    return closes


def _weight(bars: pd.DataFrame, as_of: date) -> te.Decision:
    return te.decide(bars, as_of, SPEC, CALENDAR)


# 40 bars ending Fri 2026-08-14: signal frame rows 0..38, so row 38 (the last
# bar) is on the grid (even) and rows 37 is off it.
LAST = date(2026, 8, 14)
N = 40


# ---------------------------------------------------------------------------
# Signal reuse
# ---------------------------------------------------------------------------

class TestSignalReuse:
    def test_spec_uses_the_grid_signal_function_object(self):
        assert SPEC.signal_fn is daily_momentum_signal

    def test_spec_matches_grid_cell_parameters(self):
        assert (SPEC.symbols, SPEC.hold) == (("BNBR",), 2)
        assert SPEC.family == "daily_momentum"
        assert SPEC.capital_model == "owned_cash"
        assert SPEC.strategy_id == "bnbr_momo_h2"

    def test_history_start_is_the_grid_constant(self):
        from strategy_fit.run_strategy_fit import HISTORY_START

        assert te.HISTORY_START == HISTORY_START

    def test_reported_signal_value_is_the_signal_functions_value(self):
        closes = _closes_with_up_rows(N, {38})
        bars = _bars(LAST, closes)
        d = _weight(bars, CALENDAR.next_trading_day(LAST))
        expected = daily_momentum_signal(bars, hold=0, drop_unpriced=False)["signal"].iloc[-1]
        assert d.signal_value == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Timing honesty
# ---------------------------------------------------------------------------

class TestTimingHonesty:
    def test_bars_on_or_after_as_of_are_ignored(self):
        as_of = CALENDAR.next_trading_day(LAST)
        closes = _closes_with_up_rows(N, {38})
        base = _bars(LAST, closes)
        future = pd.DataFrame({
            "date": [as_of.isoformat()],
            "close": [9999.0],
            "volume": [1],
        })
        with_future = pd.concat([base, future], ignore_index=True)
        assert _weight(base, as_of) == _weight(with_future, as_of)

    def test_stale_data_raises(self):
        bars = _bars(LAST, _closes_with_up_rows(N, {38}))
        as_of = CALENDAR.add_trading_days(LAST, 3)  # bars end 2 sessions too early
        with pytest.raises(te.StaleDataError):
            _weight(bars, as_of)

    def test_non_trading_day_raises(self):
        bars = _bars(LAST, _closes_with_up_rows(N, {38}))
        with pytest.raises(te.NotATradingDayError):
            _weight(bars, date(2026, 8, 15))  # Saturday

    def test_entry_is_next_session_not_signal_day(self):
        bars = _bars(LAST, _closes_with_up_rows(N, {38}))
        d = _weight(bars, CALENDAR.next_trading_day(LAST))
        assert d.weight == 1.0
        assert d.entry_day == CALENDAR.next_trading_day(LAST)
        assert d.signal_day == LAST


# ---------------------------------------------------------------------------
# Hold and exit
# ---------------------------------------------------------------------------

class TestHold:
    def _bars_through(self, last: date, up_rows: set[int]) -> pd.DataFrame:
        # Bars always end at `last`; the signal bar is fixed at 2026-08-14.
        # Extend bars forward with flat-down closes so later as_ofs are fresh.
        base = _bars(LAST, _closes_with_up_rows(N, up_rows))
        extra_days = []
        d = LAST
        while d < last:
            d = CALENDAR.next_trading_day(d)
            extra_days.append(d)
        close = float(base["close"].iloc[-1])
        extra = pd.DataFrame({
            "date": [x.isoformat() for x in extra_days],
            "close": [close - (i + 1) for i in range(len(extra_days))],
            "volume": [1] * len(extra_days),
        })
        return pd.concat([base, extra], ignore_index=True)

    def test_held_two_sessions_then_flat_across_a_holiday(self):
        # 2026-08-17 is an IDX holiday: entry Tue 08-18, exit leg Thu 08-20.
        assert not CALENDAR.is_trading_day(date(2026, 8, 17))
        up = {38}
        w = {}
        for as_of in (date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)):
            bars = self._bars_through(CALENDAR.prev_trading_day(as_of), up)
            w[as_of] = _weight(bars, as_of)
        assert w[date(2026, 8, 18)].weight == 1.0
        assert w[date(2026, 8, 19)].weight == 1.0
        assert w[date(2026, 8, 20)].weight == 0.0
        assert w[date(2026, 8, 20)].exit_day == date(2026, 8, 20)

    def test_flat_when_no_signal(self):
        bars = _bars(LAST, _closes_with_up_rows(N, set()))
        d = _weight(bars, CALENDAR.next_trading_day(LAST))
        assert d.weight == 0.0
        assert d.entry_day is None


# ---------------------------------------------------------------------------
# De-overlap grid (D2)
# ---------------------------------------------------------------------------

class TestSequentialDeoverlapMirror:
    def test_signal_on_any_row_is_traded_when_flat(self):
        # Last bar is signal-frame row 37 here (39 bars). The pre-fix stride-2
        # grid dropped odd rows; the sequential rule trades it (D2 fixed).
        bars = _bars(LAST, _closes_with_up_rows(N - 1, {37}))
        d = _weight(bars, CALENDAR.next_trading_day(LAST))
        assert d.signal_value > 0
        assert d.weight == 1.0
        assert d.entry_day == CALENDAR.next_trading_day(LAST)

    def test_signal_during_an_open_hold_is_not_a_new_entry(self):
        # Row 36 -> enter bar 38, exit bar 40 (= as_of). Row 38 (the last bar)
        # fires while that entry is open, so it is NOT re-entered at the exit.
        bars = _bars(LAST, _closes_with_up_rows(N, {36, 38}))
        d = _weight(bars, CALENDAR.next_trading_day(LAST))
        assert d.signal_value > 0
        assert d.weight == 0.0
        assert d.exit_day == CALENDAR.next_trading_day(LAST)
        assert "prior entry was still open" in d.reason

    def test_no_long_signal_is_not_traded(self):
        bars = _bars(LAST, _closes_with_up_rows(N, set()))
        d = _weight(bars, CALENDAR.next_trading_day(LAST))
        assert d.weight == 0.0
        assert "signal <= 0" in d.reason

    def test_sequential_chain_enters_resumes_at_exit_day_and_is_flat_on_it(self):
        # Rows 30, 33, 36 all long. Row 30 -> bar 32 on (bars 32, 33), exit
        # bar 34; scanning resumes at row 33 (signal at bar 34's close) ->
        # enter bar 35, exit 37; row 36 -> enter 38, exit 40. The exit day is
        # flat (signal and exit leg land on different closes).
        base = _bars(LAST, _closes_with_up_rows(N, {30, 33, 36}))
        days = [date.fromisoformat(x) for x in base["date"]]
        on_idx = {32, 33, 35, 36, 38, 39}
        for idx in range(32, 40):
            bars = base[base["date"] < days[idx].isoformat()]
            assert _weight(bars, days[idx]).weight == (1.0 if idx in on_idx else 0.0), idx
        after = CALENDAR.next_trading_day(LAST)
        assert _weight(base, after).weight == 0.0

    def test_grid_phase_is_anchored_at_history_start(self):
        # Bars from before HISTORY_START must not shift the phase: the first
        # in-window row is row 0 of the grid, as in the grid run.
        early = pd.DataFrame({
            "date": ["2023-12-27", "2023-12-28", "2023-12-29"],
            "close": [90.0, 91.0, 92.0],
            "volume": [1, 1, 1],
        })
        window = _bars(LAST, _closes_with_up_rows(N, {38}))
        d_plain = _weight(window, CALENDAR.next_trading_day(LAST))
        # splice: early rows precede a window that starts after HISTORY_START
        start = pd.Timestamp(window["date"].iloc[0])
        assert start >= pd.Timestamp(te.HISTORY_START)
        spliced = pd.concat([early, window], ignore_index=True)
        d_spliced = _weight(spliced, CALENDAR.next_trading_day(LAST))
        assert d_plain.weight == d_spliced.weight


# ---------------------------------------------------------------------------
# Target document
# ---------------------------------------------------------------------------

class TestBuildTarget:
    def _doc(self, weight_on: bool):
        up = {38} if weight_on else set()
        bars = _bars(LAST, _closes_with_up_rows(N, up))
        as_of = CALENDAR.next_trading_day(LAST)
        dec = _weight(bars, as_of)
        return te.build_target([dec], SPEC)

    def test_schema_matches_the_go_loader_exactly(self):
        doc = self._doc(True)
        assert set(doc) == {
            "schema_version", "as_of", "strategy_id", "capital_model",
            "base_notional_idr", "targets", "constraints",
        }
        assert set(doc["constraints"]) == {"max_participation_rate", "complete_by", "board"}
        assert doc["targets"] == [{"symbol": "BNBR", "weight": 1.0}]
        assert doc["schema_version"] == 1
        assert doc["strategy_id"] == "bnbr_momo_h2"
        assert doc["capital_model"] == "owned_cash"
        assert doc["constraints"]["board"] == "RG"

    def test_flat_day_still_lists_the_symbol_with_weight_zero(self):
        doc = self._doc(False)
        assert doc["targets"] == [{"symbol": "BNBR", "weight": 0.0}]

    def test_timestamps_are_wib_and_complete_by_follows_as_of(self):
        doc = self._doc(True)
        assert doc["as_of"].endswith("+07:00")
        assert doc["constraints"]["complete_by"].endswith("+07:00")
        assert doc["constraints"]["complete_by"] > doc["as_of"]
        assert doc["as_of"].startswith(CALENDAR.next_trading_day(LAST).isoformat())

    def test_base_notional_is_a_positive_int(self):
        doc = self._doc(True)
        assert isinstance(doc["base_notional_idr"], int)
        assert doc["base_notional_idr"] > 0


# ---------------------------------------------------------------------------
# Weight -> exactly 1 lot
# ---------------------------------------------------------------------------

def _go_snap(price: float) -> int:
    """Mirror of strategy/strategies/rebalance snapTickIDR."""
    p = int(price)
    tick = 1 if p < 200 else 2 if p < 500 else 5 if p < 2000 else 10 if p < 5000 else 25
    return p - (p % tick)


def _go_lots(base: int, weight: float, price: float) -> int:
    target = int(weight * base)
    return int(math.floor(target / (_go_snap(price) * 100)))


class TestOneLot:
    @pytest.mark.parametrize("close", [60.0, 94.0, 100.0, 199.0, 250.0, 1500.0])
    def test_exactly_one_lot_across_the_workable_price_band(self, close):
        base = te.base_notional_for(close)
        for frac in np.linspace(te.ONE_LOT_PRICE_BAND[0], te.ONE_LOT_PRICE_BAND[1], 25):
            price = close * float(frac)
            if _go_snap(price) <= 0:
                continue
            assert _go_lots(base, 1.0, price) == 1, (close, price)

    def test_zero_weight_is_zero_lots_at_any_price(self):
        base = te.base_notional_for(94.0)
        for price in (50, 94, 130, 500):
            assert _go_lots(base, 0.0, price) == 0

    def test_band_covers_a_plus_minus_25_percent_move(self):
        lo, hi = te.ONE_LOT_PRICE_BAND
        assert lo <= 0.75 and hi >= 1.25


# ---------------------------------------------------------------------------
# Output: atomic, idempotent, sidecar
# ---------------------------------------------------------------------------

class TestWrite:
    def _write(self, tmp_path, name="t.json"):
        bars = _bars(LAST, _closes_with_up_rows(N, {38}))
        as_of = CALENDAR.next_trading_day(LAST)
        dec = _weight(bars, as_of)
        doc = te.build_target([dec], SPEC)
        out = tmp_path / name
        te.write_outputs(out, doc, te.build_sidecar([dec], SPEC, doc))
        return out

    def test_idempotent_byte_identical(self, tmp_path):
        out = self._write(tmp_path)
        first = out.read_bytes(), te.sidecar_path(out).read_bytes()
        self._write(tmp_path)
        assert (out.read_bytes(), te.sidecar_path(out).read_bytes()) == first

    def test_no_temp_files_left_behind(self, tmp_path):
        self._write(tmp_path)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["t.json", "t.signal.json"]

    def test_target_file_has_no_unknown_fields_and_sidecar_has_the_reason(self, tmp_path):
        out = self._write(tmp_path)
        assert "reason" not in json.loads(out.read_text())
        side = json.loads(te.sidecar_path(out).read_text())
        assert side["symbols"][0]["reason"] and side["symbols"][0]["signal_value"] > 0
        assert side["strategy_id"] == "bnbr_momo_h2"

    def test_failed_write_leaves_no_partial_target(self, tmp_path, monkeypatch):
        out = tmp_path / "t.json"
        out.write_text("OLD")

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        bars = _bars(LAST, _closes_with_up_rows(N, {38}))
        dec = _weight(bars, CALENDAR.next_trading_day(LAST))
        doc = te.build_target([dec], SPEC)
        with pytest.raises(OSError):
            te.write_outputs(out, doc, te.build_sidecar([dec], SPEC, doc))
        assert out.read_text() == "OLD"
        assert [p.name for p in tmp_path.iterdir()] == ["t.json"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_live_without_token_fails_clearly_and_touches_no_network(self, monkeypatch, tmp_path, capsys):
        monkeypatch.delenv("SB_ACCESS_TOKEN", raising=False)
        rc = te.main([
            "emit", "--strategy", "bnbr_momo_h2", "--as-of", "2026-09-14",
            "--out", str(tmp_path / "t.json"), "--source", "live", "--allow-retired",
        ])
        assert rc != 0
        assert "SB_ACCESS_TOKEN" in capsys.readouterr().err
        assert not (tmp_path / "t.json").exists()

    def test_unknown_strategy_is_rejected(self, tmp_path, capsys):
        rc = te.main(["emit", "--strategy", "nope", "--as-of", "2026-09-14",
                      "--out", str(tmp_path / "t.json")])
        assert rc != 0

    def test_emit_from_local_parquet(self, tmp_path):
        if not LOCAL_BNBR.exists():
            pytest.skip("local BNBR daily parquet absent")
        out = tmp_path / "t.json"
        rc = te.main(["emit", "--strategy", "bnbr_momo_h2", "--as-of", "2026-09-14",
                      "--out", str(out), "--allow-retired"])
        assert rc == 0
        doc = json.loads(out.read_text())
        assert doc["targets"][0]["symbol"] == "BNBR"
        assert doc["targets"][0]["weight"] in (0.0, 1.0)

    def test_stale_local_data_fails_for_a_later_day(self, tmp_path, capsys):
        # as_of is a fixed far-future trading day (not "today"): the local bars
        # are refreshed over time, so a date-of-the-day check is not stable.
        if not LOCAL_BNBR.exists():
            pytest.skip("local BNBR daily parquet absent")
        rc = te.main(["emit", "--strategy", "bnbr_momo_h2", "--as-of", "2026-12-30",
                      "--out", str(tmp_path / "t.json"), "--allow-retired"])
        assert rc != 0
        assert "stale" in capsys.readouterr().err.lower()
        assert not (tmp_path / "t.json").exists()


# ---------------------------------------------------------------------------
# Replay against the backtest
# ---------------------------------------------------------------------------

class TestReplay:
    def _synthetic(self, n=120, seed=7) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
        return _bars(date(2026, 6, 30), [float(round(c, 2)) for c in closes])

    def test_replay_matches_backtest_entries_on_synthetic_walk(self):
        from strategy_fit import target_emitter_replay as rp

        res = rp.replay(self._synthetic(), SPEC, CALENDAR)
        assert res.n_backtest_entries > 10
        assert res.entry_match_rate == 1.0
        assert res.weight_mismatches == []

    def test_backtest_entries_agree_with_the_real_cell_scorer(self):
        from strategy_fit import target_emitter_replay as rp
        from strategy_fit.families_single_name import run_single_name_family

        df = self._synthetic()
        entries = rp.backtest_entries(df, SPEC)
        cell = run_single_name_family(
            df, SPEC.signal_fn, SPEC.symbol, 0.0, SPEC.family, SPEC.hold,
        )
        if cell["status"] == "insufficient_n":
            pytest.skip("synthetic history too short for MIN_EFF_N")
        assert cell["eff_n"] == int((entries["forward_return"].abs() > 0).sum())
        assert cell["gross_edge_bps"] == pytest.approx(entries["forward_return"].mean() * 1e4)

    def test_backtest_entries_agree_with_the_real_cell_scorer_on_bnbr(self):
        if not LOCAL_BNBR.exists():
            pytest.skip("local BNBR daily parquet absent")
        from strategy_fit import target_emitter_replay as rp
        from strategy_fit.families_single_name import run_single_name_family

        df = te.load_local_bars(LOCAL_BNBR)
        window = df[df["date"] >= te.HISTORY_START].reset_index(drop=True)
        entries = rp.backtest_entries(df, SPEC)
        cell = run_single_name_family(
            window, SPEC.signal_fn, SPEC.symbol, 0.0, SPEC.family, SPEC.hold,
        )
        assert cell["status"] != "insufficient_n"
        assert cell["eff_n"] == int((entries["forward_return"].abs() > 0).sum())
        assert cell["gross_edge_bps"] == pytest.approx(entries["forward_return"].mean() * 1e4)

    def test_replay_matches_on_real_bnbr_history(self):
        if not LOCAL_BNBR.exists():
            pytest.skip("local BNBR daily parquet absent")
        from strategy_fit import target_emitter_replay as rp

        df = te.load_local_bars(LOCAL_BNBR)
        res = rp.replay(df, SPEC, rp.calendar_from_bars(df, CALENDAR))
        assert res.days_skipped_gap == 0
        assert res.n_backtest_entries >= res.n_backtest_total - 1
        assert res.n_backtest_entries > 20
        assert res.entry_match_rate == 1.0
