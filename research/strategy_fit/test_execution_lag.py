"""
strategy_fit/test_execution_lag.py — Tests for execution_lag.py.

Covers: daily-bar aggregation from 1-minute bars, each execution convention's
price indexing (hand-computed on a small fixture — this is where an
off-by-one hides), the date-keyed join in conventional_signal_fn, the
MIN_EFF_N gate / hurdle-arithmetic wiring in score_cell, and the empty/short
-series edge cases.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.stats import MIN_EFF_N
from strategy_fit.execution_lag import (
    CONVENTIONS,
    PTRO_CELLS,
    aggregate_daily_bars,
    conventional_forward_return,
    conventional_signal_fn,
    load_daily_bars,
    min_edge_to_clear_bps_for,
    run_execution_lag_grid,
    score_cell,
)
from strategy_fit.families_single_name import ROUND_TRIP_BPS

# ---------------------------------------------------------------------------
# aggregate_daily_bars
# ---------------------------------------------------------------------------

class TestAggregateDailyBars:

    def test_empty_input_returns_empty_frame_with_expected_columns(self):
        result = aggregate_daily_bars(pd.DataFrame(columns=["datetime", "date", "open", "high", "low", "close", "volume", "value"]))
        assert result.empty
        assert list(result.columns) == ["date", "open", "high", "low", "close", "volume", "value"]

    def test_none_input_returns_empty_frame(self):
        result = aggregate_daily_bars(None)
        assert result.empty

    def test_single_day_ohlcv(self):
        """open=first, high=max, low=min, close=last, volume/value=sum."""
        intraday = pd.DataFrame({
            "datetime": pd.to_datetime([
                "2026-03-25 09:00", "2026-03-25 09:01", "2026-03-25 09:02",
            ]),
            "date": [pd.Timestamp("2026-03-25").date()] * 3,
            "open": [100, 105, 103],
            "high": [102, 110, 104],
            "low": [99, 104, 101],
            "close": [101, 106, 102],
            "volume": [10, 20, 5],
            "value": [1_000, 2_100, 500],
        })
        result = aggregate_daily_bars(intraday)
        assert len(result) == 1
        row = result.iloc[0]
        assert row["open"] == 100          # first bar's open
        assert row["high"] == 110          # max across bars
        assert row["low"] == 99            # min across bars
        assert row["close"] == 102         # last bar's close
        assert row["volume"] == 35         # sum
        assert row["value"] == 3_600       # sum

    def test_two_days_sorted_ascending_even_if_input_unsorted(self):
        """Rows are sorted by datetime before aggregation; output sorted by date."""
        intraday = pd.DataFrame({
            "datetime": pd.to_datetime([
                "2026-03-26 09:01", "2026-03-25 09:01", "2026-03-26 09:00", "2026-03-25 09:00",
            ]),
            "date": [
                pd.Timestamp("2026-03-26").date(), pd.Timestamp("2026-03-25").date(),
                pd.Timestamp("2026-03-26").date(), pd.Timestamp("2026-03-25").date(),
            ],
            "open": [220, 110, 210, 100],
            "high": [225, 115, 215, 105],
            "low": [205, 95, 200, 90],
            "close": [222, 112, 212, 102],
            "volume": [2, 1, 2, 1],
            "value": [200, 100, 200, 100],
        })
        result = aggregate_daily_bars(intraday)
        assert len(result) == 2
        assert list(result["date"]) == [pd.Timestamp("2026-03-25").date(), pd.Timestamp("2026-03-26").date()]
        first = result.iloc[0]
        # 2026-03-25's earliest bar (09:00) has open=100, its latest (09:01) has close=112.
        assert first["open"] == 100
        assert first["close"] == 112


# ---------------------------------------------------------------------------
# conventional_forward_return — hand-computed fixture
# ---------------------------------------------------------------------------

def _hand_built_frame() -> pd.DataFrame:
    """
    6 rows, hold=2. open_i = 10*(i+1), close_i = open_i + 1, i.e.:
        i:      0   1   2   3   4   5
        open:  10  20  30  40  50  60
        close: 11  21  31  41  51  61
    Chosen so every entry/exit price is distinct and traceable by hand.
    """
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-01", periods=6),
        "open": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        "close": [11.0, 21.0, 31.0, 41.0, 51.0, 61.0],
    })


class TestConventionalForwardReturn:
    """Hand-computed row-by-row expectations for hold=2 on _hand_built_frame()."""

    def test_c1_close_to_close(self):
        fwd = conventional_forward_return(_hand_built_frame(), hold=2, convention="C1")
        expected = [31 / 11 - 1, 41 / 21 - 1, 51 / 31 - 1, 61 / 41 - 1, np.nan, np.nan]
        np.testing.assert_allclose(fwd.to_numpy(), expected, equal_nan=True)

    def test_c2_next_open_to_next_open(self):
        fwd = conventional_forward_return(_hand_built_frame(), hold=2, convention="C2")
        expected = [40 / 20 - 1, 50 / 30 - 1, 60 / 40 - 1, np.nan, np.nan, np.nan]
        np.testing.assert_allclose(fwd.to_numpy(), expected, equal_nan=True)

    def test_c3_next_close_to_next_close(self):
        fwd = conventional_forward_return(_hand_built_frame(), hold=2, convention="C3")
        expected = [41 / 21 - 1, 51 / 31 - 1, 61 / 41 - 1, np.nan, np.nan, np.nan]
        np.testing.assert_allclose(fwd.to_numpy(), expected, equal_nan=True)

    def test_c4_next_open_to_close(self):
        fwd = conventional_forward_return(_hand_built_frame(), hold=2, convention="C4")
        expected = [31 / 20 - 1, 41 / 30 - 1, 51 / 40 - 1, 61 / 50 - 1, np.nan, np.nan]
        np.testing.assert_allclose(fwd.to_numpy(), expected, equal_nan=True)

    def test_c1_and_c4_share_the_same_tail_length(self):
        """C1 and C4 both exit at close_{t+hold}; only entry differs, so both
        have exactly 4 valid (non-NaN) rows out of 6 for hold=2."""
        c1 = conventional_forward_return(_hand_built_frame(), hold=2, convention="C1")
        c4 = conventional_forward_return(_hand_built_frame(), hold=2, convention="C4")
        assert c1.notna().sum() == c4.notna().sum() == 4

    def test_c2_and_c3_need_one_more_day_than_c1(self):
        """C2/C3 exit at t+hold+1, one trading day later than C1/C4 — so they
        have exactly one fewer valid row at the tail for the same hold."""
        c1 = conventional_forward_return(_hand_built_frame(), hold=2, convention="C1")
        c2 = conventional_forward_return(_hand_built_frame(), hold=2, convention="C2")
        c3 = conventional_forward_return(_hand_built_frame(), hold=2, convention="C3")
        assert c2.notna().sum() == c1.notna().sum() - 1
        assert c3.notna().sum() == c1.notna().sum() - 1

    def test_unknown_convention_raises(self):
        with pytest.raises(ValueError, match="Unknown execution convention"):
            conventional_forward_return(_hand_built_frame(), hold=2, convention="C99")

    def test_zero_hold_raises(self):
        with pytest.raises(ValueError, match="hold must be >= 1"):
            conventional_forward_return(_hand_built_frame(), hold=0, convention="C1")


# ---------------------------------------------------------------------------
# conventional_signal_fn — date-keyed join + re-applied dropna
# ---------------------------------------------------------------------------

def _stub_signal_fn(kept_dates: pd.DatetimeIndex, signals: list[float]):
    """A signal_fn stub (mirrors test_families_single_name.py's pattern) that
    returns only a subset of dates with an arbitrary signal — the caller
    controls exactly which head-side rows this "family" would have dropped,
    independent of any convention. Its own forward_return is a placeholder;
    conventional_signal_fn must replace it, not use it."""

    def _fn(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
        return pd.DataFrame({
            "date": kept_dates,
            "close": [0.0] * len(kept_dates),
            "signal": signals,
            "forward_return": [999.0] * len(kept_dates),  # must be overwritten
        })

    return _fn


class TestConventionalSignalFn:

    def test_forward_return_is_replaced_with_convention_value(self):
        df = _hand_built_frame()
        stub = _stub_signal_fn(df["date"], signals=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        wrapped = conventional_signal_fn(stub, "C1")
        out = wrapped(df, hold=2)
        expected = conventional_forward_return(df, hold=2, convention="C1").dropna()
        assert len(out) == 4
        np.testing.assert_allclose(out["forward_return"].to_numpy(), expected.to_numpy())
        assert (out["signal"] == 1.0).all()

    def test_tail_rows_unpriceable_under_c2_are_dropped_even_though_c1_kept_them(self):
        """The off-by-one this is designed to catch: under C1 the family's
        own dropna kept i=0..3 (index 3's forward_return is finite under C1).
        Under C2, index 3 needs open_6, which does not exist — the wrapper
        must drop that row too, not silently keep a NaN forward_return."""
        df = _hand_built_frame()
        stub = _stub_signal_fn(df["date"], signals=[1.0] * 6)
        wrapped_c1 = conventional_signal_fn(stub, "C1")
        wrapped_c2 = conventional_signal_fn(stub, "C2")
        out_c1 = wrapped_c1(df, hold=2)
        out_c2 = wrapped_c2(df, hold=2)
        assert len(out_c1) == 4
        assert len(out_c2) == 3
        assert out_c2["forward_return"].notna().all()

    def test_join_is_by_date_not_position(self):
        """A signal_fn that already dropped a head-side row (e.g. a lookback)
        must still be joined correctly by date, not by positional offset."""
        df = _hand_built_frame()
        kept = df["date"].iloc[1:]          # simulate a 1-row lookback drop
        stub = _stub_signal_fn(kept, signals=[2.0] * len(kept))
        wrapped = conventional_signal_fn(stub, "C1")
        out = wrapped(df, hold=2)
        # Row i=1..3 are the valid C1 rows once i=0 is already absent from the stub.
        expected = conventional_forward_return(df, hold=2, convention="C1").iloc[1:4]
        np.testing.assert_allclose(out["forward_return"].to_numpy(), expected.to_numpy())

    def test_empty_signal_fn_output_passes_through_untouched(self):
        df = _hand_built_frame()
        stub = _stub_signal_fn(df["date"].iloc[:0], signals=[])
        wrapped = conventional_signal_fn(stub, "C2")
        out = wrapped(df, hold=2)
        assert len(out) == 0


# ---------------------------------------------------------------------------
# min_edge_to_clear_bps_for
# ---------------------------------------------------------------------------

class TestMinEdgeToClearBps:

    def test_matches_min_spread_bps_plus_round_trip(self):
        from screener.metrics import min_spread_bps
        df = pd.DataFrame({"close": [1000.0, 2000.0, 5375.0]})
        expected = min_spread_bps(5375.0) + ROUND_TRIP_BPS
        assert min_edge_to_clear_bps_for(df) == pytest.approx(expected)

    def test_uses_the_last_row_not_the_first(self):
        from screener.metrics import min_spread_bps
        df = pd.DataFrame({"close": [100.0, 6000.0]})  # different tick buckets
        expected = min_spread_bps(6000.0) + ROUND_TRIP_BPS
        assert min_edge_to_clear_bps_for(df) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# score_cell — MIN_EFF_N gate and hurdle-arithmetic wiring
# ---------------------------------------------------------------------------

def _synthetic_daily_df(n: int, daily_pct_move: float, start_price: float = 1000.0) -> pd.DataFrame:
    """
    A deterministic daily bar series with a constant daily % move, so
    daily_momentum_signal is always positive (long-only keeps every row)
    and forward_return is exactly computable.
    """
    closes = start_price * (1.0 + daily_pct_move) ** np.arange(n + 1)
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=n + 1),
        "open": closes,
        "high": closes * 1.001,
        "low": closes * 0.999,
        "close": closes,
        "volume": np.full(n + 1, 1_000_000),
        "value": closes * 1_000_000,
    })


class TestScoreCell:

    def test_short_series_is_insufficient_n_for_every_convention(self):
        df = _synthetic_daily_df(n=10, daily_pct_move=0.01)
        for convention in CONVENTIONS:
            result = score_cell(
                daily_df=df,
                signal_fn=PTRO_CELLS["daily_momentum"],
                symbol="PTRO",
                family_name="daily_momentum",
                hold=2,
                convention=convention,
                min_edge_to_clear_bps=80.0,
            )
            assert result["status"] == "insufficient_n"
            assert result["gross_edge_bps"] is None
            assert result["net_edge_bps"] is None
            assert result["verdict"] == "insufficient_n"

    def test_clear_positive_edge_passes_and_net_equals_gross_minus_hurdle(self):
        df = _synthetic_daily_df(n=120, daily_pct_move=0.03)  # steady +3%/day
        result = score_cell(
            daily_df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=2,
            convention="C1",
            min_edge_to_clear_bps=80.0,
        )
        assert result["status"] == "tested"
        assert result["eff_n"] >= MIN_EFF_N
        assert result["gross_edge_bps"] == pytest.approx(result["net_edge_bps"] + result["hurdle_bps"])
        assert result["verdict"] == "PASS"

    def test_clear_negative_edge_fails(self):
        """A steady down-trend still generates long-only signal>0 rows
        (momentum keeps sign of the most recent move), so build a series
        whose long trades are consistently on the losing side instead:
        alternating strong-down / weak-up, so signal>0 (the weak-up day)
        is always followed by a reversal."""
        n = 120
        closes = [1000.0]
        for i in range(n):
            move = 0.05 if i % 2 == 0 else -0.06  # up day, then a bigger down day
            closes.append(closes[-1] * (1.0 + move))
        df = pd.DataFrame({
            "date": pd.bdate_range("2024-01-01", periods=len(closes)),
            "open": closes,
            "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "value": [c * 1_000_000 for c in closes],
        })
        result = score_cell(
            daily_df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=1,
            convention="C1",
            min_edge_to_clear_bps=80.0,
        )
        assert result["status"] == "tested"
        assert result["gross_edge_bps"] < 0
        assert result["verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# run_execution_lag_grid
# ---------------------------------------------------------------------------

class TestRunExecutionLagGrid:

    def test_raises_on_empty_frame(self):
        with pytest.raises(ValueError, match="No daily bars"):
            run_execution_lag_grid(pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "value"]))

    def test_produces_one_row_per_cell_per_convention(self):
        df = _synthetic_daily_df(n=120, daily_pct_move=0.02)
        rows = run_execution_lag_grid(df)
        assert len(rows) == len(PTRO_CELLS) * len(CONVENTIONS)
        seen = {(r["family_name"], r["convention"]) for r in rows}
        assert seen == {(name, conv) for name in PTRO_CELLS for conv in CONVENTIONS}


# ---------------------------------------------------------------------------
# load_daily_bars — store-absent and short-series edge cases
# ---------------------------------------------------------------------------

class TestLoadDailyBars:

    def test_absent_store_returns_empty_frame(self, tmp_path):
        result = load_daily_bars("NOPE", store_dir=str(tmp_path))
        assert result.empty

    def test_loads_and_aggregates_a_real_store_file(self, tmp_path):
        intraday = pd.DataFrame({
            "datetime": pd.to_datetime(["2026-01-06 09:00", "2026-01-06 09:01"]),
            "date": [pd.Timestamp("2026-01-06").date()] * 2,
            "open": [500, 510],
            "high": [520, 515],
            "low": [495, 505],
            "close": [505, 512],
            "volume": [100, 200],
            "value": [50_000, 102_400],
            "net_foreign": [0, 0],
        })
        intraday.to_parquet(tmp_path / "PTRO.parquet", engine="pyarrow", index=False)
        result = load_daily_bars("PTRO", store_dir=str(tmp_path))
        assert len(result) == 1
        assert result.iloc[0]["open"] == 500
        assert result.iloc[0]["close"] == 512
        assert result.iloc[0]["volume"] == 300


# ---------------------------------------------------------------------------
# Ordering B: hand-computed divergence from production ordering A
# ---------------------------------------------------------------------------

from strategy_fit.execution_lag import (  # noqa: E402  (kept near point of use)
    CONTROL_CELLS,
    ORDERINGS,
    SURVIVOR_CELLS,
    _score_signal_df_ordering_b,
    _split_is_oos,
    load_chartbit_daily_bars,
    score_cell_is_oos,
)
from strategy_fit.harness import deoverlap_daily
from strategy_fit.long_only import long_only_mask


def _uneven_long_only_signal_df() -> pd.DataFrame:
    """
    8 rows, signal long at positions 0, 3, 4, 6 (NOT evenly spaced) — chosen
    so ordering A (de-overlap raw stream, then filter) and ordering B
    (filter, then de-overlap the survivors) keep different rows at hold=2:

        ordering A: deoverlap_daily on all 8 rows keeps positions [0,2,4,6]
                    (row-position skip on the FULL stream); long-only filter
                    then keeps [0,4,6] (index 2's signal is -1).
        ordering B: long-only filter first keeps [0,3,4,6]; de-overlap on
                    THAT 4-row survivor list treats it as 4 sequential
                    positions and keeps sub-positions [0,2] -> original
                    indices [0,4]. Original index 6 (sub-position 3) is
                    dropped, even though nothing excludes it under A.
    """
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=8),
        "close": [0.0] * 8,
        "signal": [1.0, -1.0, -1.0, 1.0, 1.0, -1.0, 1.0, -1.0],
        "forward_return": [0.001 * (i + 1) for i in range(8)],
    })


class TestScoreSignalDfOrderingB:

    def test_ordering_a_kept_rows_by_hand(self):
        df = _uneven_long_only_signal_df()
        deoverlapped_a = deoverlap_daily(df, hold=2)
        assert list(deoverlapped_a.index) == [0, 2, 4, 6]
        mask_a = long_only_mask(deoverlapped_a["signal"].to_numpy())
        assert mask_a.sum() == 3  # index 2 (signal=-1) is excluded

    def test_ordering_b_kept_rows_diverge_from_ordering_a_by_hand(self):
        """Ordering A (previous test) keeps 3 long rows (0, 4, 6) at hold=2.
        Ordering B -- filter long-only FIRST, then de-overlap the 4-row
        survivor list [0,3,4,6] -- treats that list as 4 sequential
        positions and keeps sub-positions 0 and 2 (original indices 0, 4).
        Original index 6 (sub-position 3) is dropped even though nothing
        excludes it under ordering A. Verified directly against the same
        primitives _score_signal_df_ordering_b calls (deoverlap_daily,
        long_only_mask), independent of the MIN_EFF_N gate."""
        df = _uneven_long_only_signal_df()
        long_only_df = df[long_only_mask(df["signal"].to_numpy())]
        assert list(long_only_df.index) == [0, 3, 4, 6]
        deoverlapped_b = deoverlap_daily(long_only_df, hold=2)
        assert list(deoverlapped_b.index) == [0, 4]
        assert float(deoverlapped_b["forward_return"].mean() * 1e4) == pytest.approx(30.0)

    def test_ordering_b_below_min_eff_n_reports_insufficient_n(self):
        """This 8-row fixture is far below MIN_EFF_N=30, so the gated public
        function correctly reports insufficient_n with gross_edge_bps=None
        -- the raw arithmetic is verified separately, above."""
        df = _uneven_long_only_signal_df()
        result = _score_signal_df_ordering_b(df, hold=2)
        assert result["status"] == "insufficient_n"
        assert result["eff_n"] == 2
        assert result["gross_edge_bps"] is None

    def test_ordering_a_and_b_gross_edge_differ_on_the_same_signal_df(self):
        """The point of the exercise: same signal_df, same hold, materially
        different eff_n and gross_edge_bps depending on ordering alone
        (computed directly from the shared primitives, bypassing the
        MIN_EFF_N gate so both raw numbers are available to compare)."""
        df = _uneven_long_only_signal_df()

        deoverlapped_a = deoverlap_daily(df, hold=2)
        mask_a = long_only_mask(deoverlapped_a["signal"].to_numpy())
        eff_n_a = int(mask_a.sum())
        gross_a = float(np.mean(deoverlapped_a["forward_return"].to_numpy()[mask_a]) * 1e4)

        long_only_df = df[long_only_mask(df["signal"].to_numpy())]
        deoverlapped_b = deoverlap_daily(long_only_df, hold=2)
        eff_n_b = len(deoverlapped_b)
        gross_b = float(deoverlapped_b["forward_return"].mean() * 1e4)

        assert eff_n_a != eff_n_b
        assert gross_a != pytest.approx(gross_b)

    def test_empty_input_is_insufficient_n(self):
        empty = pd.DataFrame(columns=["date", "close", "signal", "forward_return"])
        result = _score_signal_df_ordering_b(empty, hold=2)
        assert result["status"] == "insufficient_n"
        assert result["gross_edge_bps"] is None

    def test_short_series_is_insufficient_n(self):
        df = _uneven_long_only_signal_df()  # only 2 long-only survivors after B
        result = _score_signal_df_ordering_b(df, hold=2)
        assert result["status"] == "insufficient_n"  # 2 < MIN_EFF_N=30


# ---------------------------------------------------------------------------
# score_cell — ordering parameter dispatch
# ---------------------------------------------------------------------------

class TestScoreCellOrdering:

    def test_invalid_ordering_raises(self):
        df = _synthetic_daily_df(n=120, daily_pct_move=0.02)
        with pytest.raises(ValueError, match="Unknown ordering"):
            score_cell(
                daily_df=df,
                signal_fn=PTRO_CELLS["daily_momentum"],
                symbol="PTRO",
                family_name="daily_momentum",
                hold=2,
                convention="C1",
                min_edge_to_clear_bps=80.0,
                ordering="Z",
            )

    def test_ordering_b_is_tested_and_carries_the_ordering_tag(self):
        df = _synthetic_daily_df(n=150, daily_pct_move=0.02)
        result = score_cell(
            daily_df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=2,
            convention="C1",
            min_edge_to_clear_bps=80.0,
            ordering="B",
        )
        assert result["ordering"] == "B"
        assert result["status"] == "tested"
        assert result["gross_edge_bps"] == pytest.approx(result["net_edge_bps"] + result["hurdle_bps"])

    def test_default_ordering_is_a(self):
        df = _synthetic_daily_df(n=120, daily_pct_move=0.02)
        result = score_cell(
            daily_df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=2,
            convention="C1",
            min_edge_to_clear_bps=80.0,
        )
        assert result["ordering"] == "A"


# ---------------------------------------------------------------------------
# _split_is_oos
# ---------------------------------------------------------------------------

class TestSplitIsOos:

    def test_sixty_forty_split_by_row_position(self):
        df = pd.DataFrame({
            "date": pd.bdate_range("2024-01-01", periods=10),
            "close": range(10),
        })
        is_df, oos_df = _split_is_oos(df, is_frac=0.6)
        assert len(is_df) == 6
        assert len(oos_df) == 4
        assert is_df["date"].iloc[-1] < oos_df["date"].iloc[0]

    def test_matches_daily_walk_forward_is_frac_default(self):
        from strategy_fit.daily_walk_forward import IS_FRAC
        df = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=100), "close": range(100)})
        is_df, oos_df = _split_is_oos(df)
        assert len(is_df) == round(100 * IS_FRAC)
        assert len(is_df) + len(oos_df) == 100


# ---------------------------------------------------------------------------
# score_cell_is_oos
# ---------------------------------------------------------------------------

class TestScoreCellIsOos:

    def test_invalid_ordering_raises(self):
        df = _synthetic_daily_df(n=150, daily_pct_move=0.02)
        with pytest.raises(ValueError, match="Unknown ordering"):
            score_cell_is_oos(
                daily_df=df,
                signal_fn=PTRO_CELLS["daily_momentum"],
                symbol="PTRO",
                family_name="daily_momentum",
                hold=2,
                convention="C1",
                min_edge_to_clear_bps=80.0,
                ordering="Z",
            )

    def test_ordering_a_delegates_to_walk_forward_daily(self):
        from strategy_fit.daily_walk_forward import walk_forward_daily
        df = _synthetic_daily_df(n=200, daily_pct_move=0.02)
        result = score_cell_is_oos(
            daily_df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=2,
            convention="C1",
            min_edge_to_clear_bps=80.0,
            ordering="A",
        )
        expected = walk_forward_daily(
            df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            min_edge_to_clear_bps=80.0,
            family_name="daily_momentum",
            horizon_trading_days=2,
        )
        assert result["is_edge_bps"] == pytest.approx(expected.is_edge_bps)
        assert result["oos_edge_bps"] == pytest.approx(expected.oos_edge_bps)
        assert result["oos_eff_n"] == expected.oos_eff_n

    def test_ordering_b_splits_and_scores_independently(self):
        df = _synthetic_daily_df(n=200, daily_pct_move=0.02)
        result = score_cell_is_oos(
            daily_df=df,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=2,
            convention="C1",
            min_edge_to_clear_bps=80.0,
            ordering="B",
        )
        assert result["ordering"] == "B"
        assert result["oos_edge_bps"] is not None
        assert result["oos_eff_n"] is not None and result["oos_eff_n"] > 0

    def test_empty_frame_reports_empty_splits(self):
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "value"])
        result = score_cell_is_oos(
            daily_df=empty,
            signal_fn=PTRO_CELLS["daily_momentum"],
            symbol="PTRO",
            family_name="daily_momentum",
            hold=2,
            convention="C1",
            min_edge_to_clear_bps=80.0,
            ordering="B",
        )
        assert result["oos_edge_bps"] is None
        assert result["skip_reason"] == "OOS split is empty"


# ---------------------------------------------------------------------------
# load_chartbit_daily_bars
# ---------------------------------------------------------------------------

class TestLoadChartbitDailyBars:

    def _write_store(self, tmp_path, symbol: str, dates: list[str]) -> None:
        n = len(dates)
        df = pd.DataFrame({
            "date": dates,
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1000 + i for i in range(n)],
            "value": [100_000 + i for i in range(n)],
        })
        df.to_parquet(tmp_path / f"{symbol}.parquet", engine="pyarrow", index=False)

    def test_absent_file_returns_empty_frame(self, tmp_path):
        result = load_chartbit_daily_bars("NOPE", str(tmp_path))
        assert result.empty
        assert list(result.columns) == ["date", "open", "high", "low", "close", "volume", "value"]

    def test_loads_full_history_when_no_bounds_given(self, tmp_path):
        self._write_store(tmp_path, "PTRO", ["2024-01-01", "2024-01-02", "2024-01-03"])
        result = load_chartbit_daily_bars("PTRO", str(tmp_path))
        assert len(result) == 3
        assert result["date"].is_monotonic_increasing

    def test_start_truncates_inclusive(self, tmp_path):
        self._write_store(tmp_path, "PTRO", ["2024-01-01", "2024-01-02", "2024-01-03"])
        result = load_chartbit_daily_bars("PTRO", str(tmp_path), start="2024-01-02")
        assert list(result["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]

    def test_end_truncates_inclusive(self, tmp_path):
        self._write_store(tmp_path, "PTRO", ["2024-01-01", "2024-01-02", "2024-01-03"])
        result = load_chartbit_daily_bars("PTRO", str(tmp_path), end="2024-01-02")
        assert list(result["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-01", "2024-01-02"]

    def test_start_and_end_together(self, tmp_path):
        self._write_store(tmp_path, "PTRO", ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"])
        result = load_chartbit_daily_bars("PTRO", str(tmp_path), start="2024-01-02", end="2024-01-03")
        assert list(result["date"].dt.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-03"]


# ---------------------------------------------------------------------------
# Cell registries — sanity
# ---------------------------------------------------------------------------

class TestCellRegistries:

    def test_survivor_and_control_cells_are_well_formed(self):
        for cell in SURVIVOR_CELLS + CONTROL_CELLS:
            assert cell.symbol
            assert cell.family_name
            assert callable(cell.signal_fn)
            assert cell.hold >= 1

    def test_orderings_constant_is_a_and_b(self):
        assert ORDERINGS == ("A", "B")
