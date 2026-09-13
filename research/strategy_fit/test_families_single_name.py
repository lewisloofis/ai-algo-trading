"""
strategy_fit/test_families_single_name.py — Long-only regression tests for
families_single_name.py.

families_single_name.py's `run_single_name_family` is the shared scorer
behind daily_momentum, mean_reversion, and volume_breakout (the BREN
daily_momentum and RATU volume_breakout survivors flow through it). Before
this fix it scored BOTH directions: gross_edge_bps and hit_rate were computed
from `sign(signal) * forward_return`, which silently includes a short trade
whenever signal < 0. IDX retail accounts cannot short-sell (the constraint
families_intraday.py:run_orb_family established in commit c62eba8), so a
negative-signal observation must be SKIPPED — not sign-flipped into a
synthetic long return — and must not count toward eff_n.

These tests use a stub `signal_fn` that bypasses the three real signal
functions (daily_momentum_signal / mean_reversion_signal /
volume_breakout_signal) so the signal/forward_return series fed into the
scorer is fully controlled, independent of any particular signal's math.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.stats import MIN_EFF_N


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_signal_fn(signals: np.ndarray, forward_returns: np.ndarray):
    """Build a signal_fn compatible with run_single_name_family's contract
    (called as signal_fn(df, hold=int) -> DataFrame with signal/forward_return
    columns) that ignores its input df and returns a fixed series.
    """
    n = len(signals)
    assert len(forward_returns) == n

    def _fn(df: pd.DataFrame, hold: int = 1) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
                "close": np.full(n, 1000.0),
                "signal": np.asarray(signals, dtype=float),
                "forward_return": np.asarray(forward_returns, dtype=float),
            }
        )

    return _fn


def _run(signals: np.ndarray, forward_returns: np.ndarray) -> dict:
    from strategy_fit.families_single_name import run_single_name_family
    return run_single_name_family(
        df=pd.DataFrame({"date": [], "close": []}),  # unused by the stub signal_fn
        signal_fn=_stub_signal_fn(signals, forward_returns),
        symbol="TEST",
        min_edge_to_clear_bps=40.0,
        family_name="test_family",
        horizon_trading_days=1,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunSingleNameFamilyLongOnly:

    def test_import_succeeds(self):
        """families_single_name module must be importable."""
        from strategy_fit import families_single_name  # noqa: F401

    def test_all_long_signal_strong_edge_is_tested(self):
        """All-long-signal, strongly-positive-forward-return fixture must be
        scored 'tested' with a positive gross edge and eff_n == n (no rows
        filtered out)."""
        n = 40
        signals = np.full(n, 1.0)
        forward_returns = np.full(n, 0.02)  # +200 bps every day
        result = _run(signals, forward_returns)
        assert result["status"] == "tested", (
            f"Expected tested, got {result['status']}: {result.get('skip_reason')}"
        )
        assert result["eff_n"] == n
        assert result["gross_edge_bps"] > 0
        assert result["hit_rate"] == 1.0

    def test_all_short_signal_is_never_traded(self):
        """Long-only contract: when EVERY observation has a negative signal
        (all would require a short leg), run_single_name_family must yield
        zero tradeable rows rather than silently trading them as synthetic
        longs.

        Regression: before this fix, signal=-1 with forward_return=-0.02
        (a losing long, but a WINNING short) produced signed_return =
        sign(-1) * -0.02 = +0.02 — a fabricated positive edge no retail
        account can actually realize. This is the families_single_name.py
        counterpart of families_intraday.py's
        TestOrbTradesMostDays.test_orb_down_breaks_are_never_traded (c62eba8).
        """
        n = 40
        signals = np.full(n, -1.0)
        forward_returns = np.full(n, -0.02)  # would be a lucrative SHORT
        result = _run(signals, forward_returns)
        assert result["status"] == "insufficient_n", (
            f"All-short-signal fixture must never be traded; got status={result['status']}"
        )
        assert result["gross_edge_bps"] is None
        assert (result["eff_n"] or 0) == 0

    def test_mixed_signal_only_long_side_is_scored(self):
        """Half the observations carry a profitable LONG signal, half carry a
        signal that would only be profitable SHORT. Only the long half may be
        scored: eff_n must equal the long-only count (not the full count),
        and gross_edge_bps must reflect only the long trades.

        Regression: this is exactly the families_single_name.py shape of the
        bug the team lead measured on real data (BREN daily_momentum, RATU
        volume_breakout) — the old `np.sign(sig) * fwd` scorer blended both
        halves into one inflated, partly-fictional edge number.
        """
        n_long = 30
        n_short = 30
        signals = np.concatenate([np.full(n_long, 1.0), np.full(n_short, -1.0)])
        # Long half: genuinely profitable long trades (+150 bps).
        # Short half: would be profitable if shorted (signal=-1, fwd=-0.10 →
        # sign(-1)*-0.10 = +0.10 under the old buggy scorer) — must be excluded.
        forward_returns = np.concatenate([
            np.full(n_long, 0.015),
            np.full(n_short, -0.10),
        ])
        result = _run(signals, forward_returns)
        assert result["status"] == "tested", (
            f"Expected tested, got {result['status']}: {result.get('skip_reason')}"
        )
        assert result["eff_n"] == n_long, (
            f"eff_n must count only the long-signal rows; got {result['eff_n']}"
        )
        assert result["gross_edge_bps"] == pytest.approx(150.0, rel=1e-6), (
            f"gross_edge_bps must reflect only the long half (+150bps); "
            f"got {result['gross_edge_bps']} (a blended value would be far larger "
            f"and would prove the short-signal rows leaked in)"
        )
        assert result["hit_rate"] == 1.0

    def test_hit_rate_excludes_negative_signal_rows(self):
        """Unit test of _compute_hit_rate: a negative-signal row must not
        contribute to hit_rate or eff_n, even if its forward_return would
        make it a 'hit' under sign-flipping."""
        from strategy_fit.families_single_name import _compute_hit_rate
        df = pd.DataFrame({
            "signal": [1.0, 1.0, -1.0, -1.0],
            "forward_return": [0.01, -0.01, -0.01, 0.01],
        })
        hit_rate, eff_n = _compute_hit_rate(df)
        # Only the two signal=+1 rows are tradeable: one hit (+0.01), one
        # miss (-0.01) → hit_rate = 0.5, eff_n = 2 (the two signal=-1 rows,
        # which a sign-flipped scorer would have counted as 2 additional
        # hits, must be excluded entirely).
        assert eff_n == 2, f"Expected eff_n=2 (long-only rows), got {eff_n}"
        assert hit_rate == pytest.approx(0.5), f"Expected hit_rate=0.5, got {hit_rate}"

    def test_min_eff_n_applies_to_long_only_count(self):
        """A fixture with MIN_EFF_N long rows plus many short rows must not
        be starved by MIN_EFF_N — the check applies to the long-only count."""
        n_long = MIN_EFF_N
        n_short = 200  # far more short rows than long rows
        signals = np.concatenate([np.full(n_long, 1.0), np.full(n_short, -1.0)])
        forward_returns = np.concatenate([
            np.full(n_long, 0.01),
            np.full(n_short, -0.05),
        ])
        result = _run(signals, forward_returns)
        assert result["status"] == "tested", (
            f"Expected tested (long-only count meets MIN_EFF_N); "
            f"got {result['status']}: {result.get('skip_reason')}"
        )
        assert result["eff_n"] == n_long

    def test_below_min_eff_n_long_only_is_insufficient(self):
        """A fixture with fewer long rows than MIN_EFF_N must be
        insufficient_n even though the total row count (long + short) would
        clear MIN_EFF_N on its own."""
        n_long = MIN_EFF_N - 1
        n_short = 200
        signals = np.concatenate([np.full(n_long, 1.0), np.full(n_short, -1.0)])
        forward_returns = np.concatenate([
            np.full(n_long, 0.01),
            np.full(n_short, -0.05),
        ])
        result = _run(signals, forward_returns)
        assert result["status"] == "insufficient_n", (
            f"Long-only count is below MIN_EFF_N; got {result['status']}"
        )


class TestNullEdge:
    """Always-long null (see strategy_fit/report.py's null-edge blocking
    gate): null_edge_bps is the mean forward_return over the SAME
    de-overlapped population with the long-only signal filter removed —
    i.e. what buying every de-overlapped slot unconditionally would have
    earned. With hold=1 (as `_run` always uses), deoverlap_daily keeps every
    row, so the null population here is simply every row `_run` was given.
    """

    def test_null_edge_is_mean_over_both_signal_directions(self):
        """30 long rows at +150bps, 30 short-signal rows at -1000bps (the
        same fixture as test_mixed_signal_only_long_side_is_scored). gross
        edge is long-only (+150bps); the null must average over ALL 60 rows
        regardless of signal direction, and edge_over_null is the
        difference — computed by hand below."""
        n_long = 30
        n_short = 30
        signals = np.concatenate([np.full(n_long, 1.0), np.full(n_short, -1.0)])
        forward_returns = np.concatenate([
            np.full(n_long, 0.015),   # +150 bps
            np.full(n_short, -0.10),  # -1000 bps
        ])
        result = _run(signals, forward_returns)
        assert result["status"] == "tested"
        assert result["gross_edge_bps"] == pytest.approx(150.0, rel=1e-6)

        expected_null_bps = (
            (n_long * 0.015 + n_short * (-0.10)) / (n_long + n_short) * 1e4
        )
        assert expected_null_bps == pytest.approx(-425.0, rel=1e-6), (
            "sanity-check the hand computation itself"
        )
        assert result["null_edge_bps"] == pytest.approx(expected_null_bps, rel=1e-6)
        assert result["edge_over_null_bps"] == pytest.approx(
            result["gross_edge_bps"] - expected_null_bps, rel=1e-6
        )
        assert result["edge_over_null_bps"] == pytest.approx(575.0, rel=1e-6)

    def test_null_edge_matches_gross_when_all_signals_are_long(self):
        """When every row is long-signal, the null population IS the
        strategy population, so null_edge_bps == gross_edge_bps and
        edge_over_null_bps == 0 — the signal explains none of the edge over
        what an unconditional buy would have earned, correctly."""
        n = 40
        signals = np.full(n, 1.0)
        forward_returns = np.full(n, 0.02)
        result = _run(signals, forward_returns)
        assert result["status"] == "tested"
        assert result["null_edge_bps"] == pytest.approx(result["gross_edge_bps"], rel=1e-6)
        assert result["edge_over_null_bps"] == pytest.approx(0.0, abs=1e-6)

    def test_null_edge_none_on_non_tested_status(self):
        """insufficient_n / error paths must report null_edge_bps=None,
        edge_over_null_bps=None — never a stale or fabricated value."""
        n = 10  # below MIN_EFF_N
        signals = np.full(n, 1.0)
        forward_returns = np.full(n, 0.02)
        result = _run(signals, forward_returns)
        assert result["status"] == "insufficient_n"
        assert result["null_edge_bps"] is None
        assert result["edge_over_null_bps"] is None
