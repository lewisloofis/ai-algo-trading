"""
strategy_fit/test_daily_walk_forward.py — TDD tests for the daily single-name
walk-forward / persistence split.

Validates strategy_fit/daily_walk_forward.py:
  - DailyWalkForwardResult: frozen dataclass, field-name parity with
    IntradayWalkForwardResult
  - walk_forward_daily: row-position IS/OOS split (60/40), persistence flag,
    thin-OOS / invalid-split conservative cases, decay_ratio,
    required_oos_eff_n / oos_underpowered (plan-10 W2.1 power floor),
    no-leakage guarantee, input immutability

These tests use date-keyed stub signal_fn's (same spirit as
test_families_single_name.py's stub) so the signal/forward_return series is
fully controlled and independent of any particular signal's math, while
still exercising walk_forward_daily's REAL row-position split and its REAL
calls into run_single_name_family.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N
from lib.stats import MIN_EFF_N


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_daily_df(n_days: int, ref_price: float = 1000.0) -> pd.DataFrame:
    """Minimal synthetic daily-bar DataFrame: date/close/volume/open.

    Only the ``date`` column is read by the date-keyed stub signal_fn's
    below; close/volume/open are included to make the shape a realistic
    daily-bar frame (and to give the immutability test something to check).
    """
    dates = pd.bdate_range("2025-01-06", periods=n_days)
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": np.full(n_days, ref_price),
        "volume": np.full(n_days, 1_000.0),
        "open": np.full(n_days, ref_price),
    })


def _fixed_signal_fn(signal: float, forward_return: float):
    """signal_fn stub: constant signal/forward_return for every row of
    whatever slice it is called with (mirrors the input df's row count and
    date column exactly, so it behaves correctly under any split).
    """
    def _fn(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
        n = len(df)
        return pd.DataFrame({
            "date": df["date"].to_numpy(),
            "close": df["close"].to_numpy() if "close" in df.columns else np.full(n, 1000.0),
            "signal": np.full(n, signal),
            "forward_return": np.full(n, forward_return),
        })
    return _fn


def _step_signal_fn(is_frac_for_step: float, forward_is: float, forward_oos: float, total_days: int):
    """signal_fn stub whose forward_return depends on POSITION within the
    full date range: rows in the chronologically-first ``is_frac_for_step``
    fraction of ``total_days`` get ``forward_is``, the remainder get
    ``forward_oos``. signal is always +1 (long-tradeable) throughout.

    This models "an edge present only in the IS period" without relying on
    walk_forward_daily's own split fraction — the step boundary is computed
    independently here from the full date range, and the stub is invoked on
    whatever slice walk_forward_daily hands it (each row keeps its
    row-position-implied membership because dates are unique and ordered).
    """
    all_dates = pd.bdate_range("2025-01-06", periods=total_days).strftime("%Y-%m-%d").tolist()
    n_step = int(round(total_days * is_frac_for_step))
    step_is_dates = set(all_dates[:n_step])

    def _fn(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
        dates = df["date"].astype(str).to_numpy()
        fwd = np.array([forward_is if d in step_is_dates else forward_oos for d in dates])
        return pd.DataFrame({
            "date": dates,
            "close": df["close"].to_numpy() if "close" in df.columns else np.full(len(dates), 1000.0),
            "signal": np.full(len(dates), 1.0),
            "forward_return": fwd,
        })
    return _fn


def _alternating_sigma_signal_fn(edge_bps: float, sigma_bps: float, total_days: int):
    """signal_fn stub with an EXACT, deterministic per-trade mean/dispersion.

    signal is always +1.0 (long-tradeable). forward_return alternates
    between (edge_bps + sigma_bps) and (edge_bps - sigma_bps) bps by GLOBAL
    day index (not per-slice index), so both the IS half and the OOS half
    (each an even count of days, by construction of total_days) have sample
    mean == edge_bps and sample std ~= sigma_bps exactly (up to the ddof=1
    finite-sample correction) — no RNG, hence no seed-dependent flakiness.
    Mirrors intraday_walk_forward's test_intraday_walk_forward.py
    _make_fixed_sigma_edge_df technique, applied directly to returns instead
    of an injected price path.
    """
    all_dates = pd.bdate_range("2025-01-06", periods=total_days).strftime("%Y-%m-%d").tolist()
    idx_by_date = {d: i for i, d in enumerate(all_dates)}

    def _fn(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
        dates = df["date"].astype(str).to_numpy()
        fwd = np.array([
            (edge_bps + sigma_bps) / 1e4 if idx_by_date[d] % 2 == 0 else (edge_bps - sigma_bps) / 1e4
            for d in dates
        ])
        return pd.DataFrame({
            "date": dates,
            "close": df["close"].to_numpy() if "close" in df.columns else np.full(len(dates), 1000.0),
            "signal": np.full(len(dates), 1.0),
            "forward_return": fwd,
        })
    return _fn


def _run(df: pd.DataFrame, signal_fn, horizon_trading_days: int = 1):
    from strategy_fit.daily_walk_forward import walk_forward_daily
    return walk_forward_daily(
        df=df,
        signal_fn=signal_fn,
        symbol="TEST",
        min_edge_to_clear_bps=40.0,
        family_name="test_family",
        horizon_trading_days=horizon_trading_days,
    )


# ---------------------------------------------------------------------------
# Section A: persistent edge
# ---------------------------------------------------------------------------

class TestPersistentEdge:
    def test_edge_present_across_whole_sample_is_persistent(self) -> None:
        """A consistent +200bps long edge across all 100 days → both IS
        (60 rows) and OOS (40 rows) clear MIN_EFF_N=30 → tested, and OOS
        edge is positive with eff_n >= MIN_OOS_EFF_N → is_persistent=True.
        """
        df = _make_daily_df(n_days=100)
        signal_fn = _fixed_signal_fn(signal=1.0, forward_return=0.02)
        result = _run(df, signal_fn)
        assert result.is_edge_bps is not None and result.is_edge_bps > 0
        assert result.oos_edge_bps is not None and result.oos_edge_bps > 0
        assert result.oos_eff_n is not None and result.oos_eff_n >= MIN_OOS_EFF_N
        assert result.is_persistent is True, (
            f"Expected persistent; skip_reason={result.skip_reason}"
        )
        assert result.skip_reason is None


# ---------------------------------------------------------------------------
# Section B: THE regression test — this is what would have caught the ORB
# in-sample-only-edge failure (plan-10 W2) if it had existed for daily cells.
# ---------------------------------------------------------------------------

class TestSelectionBiasRegression:
    def test_edge_only_in_first_60pct_is_not_persistent(self) -> None:
        """Edge present ONLY in the IS period (first ~60% of days) and
        NEGATIVE afterward must be marked NOT persistent, with a skip_reason
        naming the non-positive OOS edge.

        This is the daily-family counterpart of the exact defect that
        produced a fake in-sample-only edge in the intraday ORB family
        (plan-10 Wave 2): before this module existed, a DAILY cell's
        "edge" was only ever re-tested on rolling windows drawn from the
        SAME range it was screened on, so a selection-biased, IS-only edge
        like this one would have sailed through as a PASS. This test
        exists specifically to make sure walk_forward_daily catches it.
        """
        n = 100
        df = _make_daily_df(n_days=n)
        signal_fn = _step_signal_fn(
            is_frac_for_step=0.60, forward_is=0.02, forward_oos=-0.01, total_days=n
        )
        result = _run(df, signal_fn)
        assert result.is_edge_bps is not None and result.is_edge_bps > 0, (
            "IS half must show the (fake, selection-biased) positive edge"
        )
        assert result.oos_edge_bps is not None and result.oos_edge_bps < 0, (
            "OOS half must show the edge has NOT persisted (negative here)"
        )
        assert result.is_persistent is False
        assert result.skip_reason is not None
        assert "non-positive" in result.skip_reason, (
            f"skip_reason must name the non-positive OOS edge; got {result.skip_reason!r}"
        )


# ---------------------------------------------------------------------------
# Section C: thin OOS / invalid split
# ---------------------------------------------------------------------------

class TestThinAndInvalidSplits:
    def test_thin_oos_with_valid_is_uses_the_thin_oos_message(self) -> None:
        """IS clears MIN_EFF_N=30 (33 rows) but OOS does not (22 rows) →
        conservative False, with the plain "thin OOS" wording (not the
        "invalid split" wording — IS was perfectly usable)."""
        n = 55  # IS=round(55*0.6)=33 (tested), OOS=22 (insufficient_n)
        n_is = int(round(n * 0.6))
        assert n_is >= MIN_EFF_N, "test setup assumption: IS must clear MIN_EFF_N"
        assert (n - n_is) < MIN_EFF_N, "test setup assumption: OOS must miss MIN_EFF_N"
        df = _make_daily_df(n_days=n)
        signal_fn = _fixed_signal_fn(signal=1.0, forward_return=0.02)
        result = _run(df, signal_fn)
        assert result.is_persistent is False
        assert result.skip_reason is not None
        assert "thin OOS" in result.skip_reason
        assert "invalid split" not in result.skip_reason

    def test_neither_half_usable_reports_invalid_split(self) -> None:
        """Both IS (24 rows) and OOS (16 rows) fall below MIN_EFF_N=30 →
        skip_reason must say "invalid split" explicitly rather than
        implying IS was a valid comparison point."""
        n = 40  # IS=24, OOS=16 — both < MIN_EFF_N=30
        n_is = int(round(n * 0.6))
        assert n_is < MIN_EFF_N and (n - n_is) < MIN_EFF_N, (
            "test setup assumption: both halves must miss MIN_EFF_N"
        )
        df = _make_daily_df(n_days=n)
        signal_fn = _fixed_signal_fn(signal=1.0, forward_return=0.02)
        result = _run(df, signal_fn)
        assert result.is_persistent is False
        assert result.skip_reason is not None
        assert "invalid split" in result.skip_reason
        assert "neither IS" in result.skip_reason


# ---------------------------------------------------------------------------
# Section D: power-derived required-N / oos_underpowered (plan-10 W2.1)
# ---------------------------------------------------------------------------

class TestUnderpowered:
    def test_positive_but_underpowered_oos_edge_coexists_with_persistence(self) -> None:
        """A small (15bps) but genuinely positive OOS edge against a large
        (~150bps) measured per-trade dispersion clears the hard floor
        (oos_eff_n=40 >= MIN_OOS_EFF_N=10) so is_persistent=True, but the
        power-derived required_oos_eff_n (~(2*150/15)**2 ~= 400) is far
        above oos_eff_n=40 → oos_underpowered=True.

        is_persistent=True AND oos_underpowered=True coexisting is the
        point of this test: "persistent" answers a directional question
        (edge positive, floor cleared); "underpowered" answers a separate
        precision question (is there enough data to trust the SIZE of this
        edge). Both can be true at once, and that is an honest, expected
        outcome — not a contradiction.
        """
        n = 100
        df = _make_daily_df(n_days=n)
        signal_fn = _alternating_sigma_signal_fn(edge_bps=15.0, sigma_bps=150.0, total_days=n)
        result = _run(df, signal_fn)
        assert result.oos_eff_n is not None and result.oos_eff_n >= MIN_OOS_EFF_N
        assert result.is_persistent is True, (
            f"Small-but-positive OOS edge with sufficient N should be persistent; "
            f"oos_edge_bps={result.oos_edge_bps}, skip_reason={result.skip_reason}"
        )
        assert result.oos_sigma_bps is not None
        assert abs(result.oos_sigma_bps - 150.0) < 10.0, (
            f"oos_sigma_bps={result.oos_sigma_bps} should be close to the injected 150bps"
        )
        assert result.required_oos_eff_n is not None
        assert result.required_oos_eff_n > result.oos_eff_n, (
            f"required_oos_eff_n={result.required_oos_eff_n} should exceed "
            f"oos_eff_n={result.oos_eff_n} for a small edge vs. large sigma"
        )
        assert result.oos_underpowered is True


# ---------------------------------------------------------------------------
# Section E: no leakage
# ---------------------------------------------------------------------------

class TestNoLeakage:
    def test_is_and_oos_slices_the_function_builds_are_disjoint(self) -> None:
        """Record the dates walk_forward_daily's OWN internal IS and OOS
        calls into signal_fn actually saw (not a recomputation of the split
        formula elsewhere) and assert those two real slices are disjoint and
        together cover every row exactly once."""
        n = 100
        df = _make_daily_df(n_days=n)
        calls: list[np.ndarray] = []

        def _recording_signal_fn(inner_df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
            dates = inner_df["date"].to_numpy()
            calls.append(dates)
            return pd.DataFrame({
                "date": dates,
                "close": inner_df["close"].to_numpy(),
                "signal": np.full(len(dates), 1.0),
                "forward_return": np.full(len(dates), 0.02),
            })

        _run(df, _recording_signal_fn)
        assert len(calls) == 2, "signal_fn must be invoked exactly once for IS and once for OOS"
        is_dates, oos_dates = set(calls[0].tolist()), set(calls[1].tolist())
        assert is_dates.isdisjoint(oos_dates), "IS and OOS date sets must be disjoint"
        assert len(is_dates) + len(oos_dates) == n, (
            "IS and OOS together must cover every row exactly once"
        )


# ---------------------------------------------------------------------------
# Section F: insufficient data edge cases
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_empty_df_is_conservative(self) -> None:
        df = pd.DataFrame(columns=["date", "close", "volume", "open"])
        signal_fn = _fixed_signal_fn(signal=1.0, forward_return=0.02)
        result = _run(df, signal_fn)
        assert result.is_persistent is False
        assert result.skip_reason is not None
        assert "empty" in result.skip_reason

    def test_single_row_df_is_conservative(self) -> None:
        df = _make_daily_df(n_days=1)
        signal_fn = _fixed_signal_fn(signal=1.0, forward_return=0.02)
        result = _run(df, signal_fn)
        assert result.is_persistent is False
        assert result.skip_reason is not None


# ---------------------------------------------------------------------------
# Section G: immutability
# ---------------------------------------------------------------------------

class TestImmutability:
    def test_input_dataframe_is_not_mutated(self) -> None:
        df = _make_daily_df(n_days=100)
        original = df.copy(deep=True)
        signal_fn = _fixed_signal_fn(signal=1.0, forward_return=0.02)
        _run(df, signal_fn)
        pd.testing.assert_frame_equal(df, original)


# ---------------------------------------------------------------------------
# Section H: DailyWalkForwardResult dataclass shape
# ---------------------------------------------------------------------------

class TestDailyWalkForwardResult:
    def test_result_is_frozen_dataclass_with_intraday_field_parity(self) -> None:
        """Field names must exactly match IntradayWalkForwardResult — the
        caller threads both result types into the same CellResult fields."""
        import dataclasses
        from strategy_fit.daily_walk_forward import DailyWalkForwardResult
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult

        daily_fields = {f.name for f in dataclasses.fields(DailyWalkForwardResult)}
        intraday_fields = {f.name for f in dataclasses.fields(IntradayWalkForwardResult)}
        assert daily_fields == intraday_fields

        r = DailyWalkForwardResult(
            is_edge_bps=10.0,
            oos_edge_bps=5.0,
            is_hit_rate=0.55,
            oos_hit_rate=0.52,
            oos_eff_n=15,
            decay_ratio=0.5,
            is_persistent=True,
            skip_reason=None,
        )
        assert dataclasses.is_dataclass(r)
        try:
            r.is_edge_bps = 99.0  # type: ignore[misc]
            pytest.fail("Expected FrozenInstanceError on attribute assignment")
        except (AttributeError, TypeError):
            pass  # expected — frozen dataclass
