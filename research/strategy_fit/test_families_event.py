"""
strategy_fit/test_families_event.py — Long-only regression tests for
families_event.py.

run_event_family pools gap / volume-spike / foreign-flow-spike events across
the universe. Before this fix it scored BOTH directions: gross_edge_bps was
mean(forward_return) over ALL events (a down-gap "continuation" event is a
short bet — profitable exactly when forward_return is negative), and hit_rate
used sign(signal)==sign(forward_return), so a negative-signal event with a
negative forward return counted as a "hit". IDX retail accounts cannot
short-sell (the constraint families_intraday.py:run_orb_family established
in commit c62eba8), so a negative-signal event must be SKIPPED — not
sign-flipped into a synthetic long return — and must not count toward eff_n.

These tests exercise the "gap_event" detector directly (constructing a
synthetic OHLCV series with a controlled overnight gap and forward return per
day) since it is the simplest detector to drive precisely.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lib.stats import MIN_EFF_N


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gap_df(n: int = 100, ref_price: float = 1000.0) -> pd.DataFrame:
    """Build a daily OHLCV series where every day t (1 <= t <= n-2) has an
    overnight gap and a forward return controlled independently:

    - Even t: up-gap day. gap = +5% (>> GAP_THRESHOLD_PCT=2%) → signal=+1
      (long). forward_return = +1% (a winning long).
    - Odd t: down-gap day. gap = -5% → signal=-1 (short). forward_return =
      -1% (a LOSING long, but a WINNING short — this is exactly what a
      sign-flipping scorer would silently trade).

    close[0] anchors the series; close[t+1] = close[t] * (1 + ret_t) where
    ret_t alternates +1%/-1% by t's parity, so forward_return_t (hold=1)
    matches the day-t classification above by construction. open[t] is set
    independently as a scaled multiple of close[t-1] to hit the gap target.
    """
    dates = pd.bdate_range("2025-01-01", periods=n)
    closes = np.empty(n)
    closes[0] = ref_price
    for t in range(0, n - 1):
        ret_t = 0.01 if t % 2 == 0 else -0.01
        closes[t + 1] = closes[t] * (1.0 + ret_t)

    opens = np.empty(n)
    opens[0] = closes[0]  # day 0's gap is undefined (no prior close) and dropped
    for t in range(1, n):
        gap_target = 0.05 if t % 2 == 0 else -0.05
        opens[t] = closes[t - 1] * (1.0 + gap_target)

    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens,
            "high": np.maximum(opens, closes) * 1.001,
            "low": np.minimum(opens, closes) * 0.999,
            "close": closes,
            "volume": np.full(n, 1_000_000),
            "net_foreign": np.zeros(n),
        }
    )


def _up_gap_event_count(n: int) -> int:
    """Number of even t in [1, n-2] — the long-tradeable gap events _make_gap_df
    produces (gap events require t >= 1 for a defined prior close, and
    forward_return requires t <= n-2 for a defined next close)."""
    return sum(1 for t in range(1, n - 1) if t % 2 == 0)


def _down_gap_event_count(n: int) -> int:
    """Number of odd t in [1, n-2] — the short-only gap events."""
    return sum(1 for t in range(1, n - 1) if t % 2 == 1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunEventFamilyLongOnly:

    def test_import_succeeds(self):
        """families_event module must be importable."""
        from strategy_fit import families_event  # noqa: F401

    def test_gap_family_all_long_gaps_is_tested(self):
        """A universe where every gap event is up (long) and profitable must
        be scored 'tested' with eff_n equal to the number of qualifying
        events and a positive gross edge."""
        from strategy_fit.families_event import run_event_family
        n = 100
        hist = {"SYM": _make_gap_df(n=n)}
        result = run_event_family(hist, event_detector_name="gap_event", horizon_trading_days=1)
        up_n = _up_gap_event_count(n)
        assert up_n >= MIN_EFF_N, "test fixture must produce enough up-gap events"
        assert result["status"] == "tested", (
            f"Expected tested, got {result['status']}: {result.get('skip_reason')}"
        )
        assert result["eff_n"] == up_n
        assert result["gross_edge_bps"] > 0
        assert result["hit_rate"] == 1.0

    def test_gap_family_all_short_gaps_is_never_traded(self):
        """Long-only contract: when EVERY gap event is a down-gap (would
        require shorting to act on), run_event_family must yield zero
        tradeable events rather than silently trading them as synthetic
        longs.

        Regression: before this fix, gross_edge_bps = mean(forward_return)
        over ALL events regardless of signal direction, so a down-gap event
        with forward_return=-1% contributed a POSITIVE number to the
        "continuation" edge it cannot actually realize long-only. This is
        the families_event.py counterpart of families_intraday.py's
        TestOrbTradesMostDays.test_orb_down_breaks_are_never_traded (c62eba8).
        """
        from strategy_fit.families_event import run_event_family
        n = 100
        # Dedicated all-down-gap fixture: every day gaps down (signal=-1) and
        # is a losing long (forward_return=-1%) — i.e. a winning short, which
        # a sign-flipping scorer would trade as if it were a long.
        dates = pd.bdate_range("2025-01-01", periods=n)
        ref_price = 1000.0
        closes = np.empty(n)
        closes[0] = ref_price
        for t in range(0, n - 1):
            closes[t + 1] = closes[t] * 0.99  # every day is a losing long (-1%)
        opens = np.empty(n)
        opens[0] = closes[0]
        for t in range(1, n):
            opens[t] = closes[t - 1] * 0.95  # down-gap every day (open <<< prior close)
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens,
            "high": np.maximum(opens, closes) * 1.001,
            "low": np.minimum(opens, closes) * 0.999,
            "close": closes,
            "volume": np.full(n, 1_000_000),
            "net_foreign": np.zeros(n),
        })
        hist = {"SYM": df}
        result = run_event_family(hist, event_detector_name="gap_event", horizon_trading_days=1)
        assert result["status"] == "insufficient_n", (
            f"All-down-gap fixture must never be traded; got status={result['status']}"
        )
        assert result["gross_edge_bps"] is None
        assert (result["eff_n"] or 0) == 0

    def test_gap_family_mixed_only_long_side_is_scored(self):
        """With both up-gap (long) and down-gap (short-only) events present,
        eff_n and gross_edge_bps must reflect only the up-gap events.

        Regression: the down-gap half here has forward_return=-1% — under
        the old sign(signal)==sign(forward_return) hit-rate and unsigned
        mean(forward_return) gross-edge computation, these would-be-short
        events leaked into both metrics.
        """
        from strategy_fit.families_event import run_event_family
        n = 100
        hist = {"SYM": _make_gap_df(n=n)}
        result = run_event_family(hist, event_detector_name="gap_event", horizon_trading_days=1)
        up_n = _up_gap_event_count(n)
        down_n = _down_gap_event_count(n)
        assert down_n > 0, "test fixture must also produce down-gap events to exercise the filter"
        assert result["status"] == "tested"
        assert result["eff_n"] == up_n, (
            f"eff_n must count only up-gap (long) events, not up+down "
            f"({up_n + down_n}); got {result['eff_n']}"
        )
        # Every up-gap day has forward_return exactly +1% by construction.
        assert result["gross_edge_bps"] == pytest.approx(100.0, rel=1e-6), (
            f"gross_edge_bps must reflect only the long side (+100bps); got "
            f"{result['gross_edge_bps']} (a blended value would be diluted "
            f"toward 0 by the down-gap −1% events, proving they leaked in)"
        )
        assert result["hit_rate"] == 1.0


class TestNullEdge:
    """Always-long null (see strategy_fit/report.py's null-edge blocking
    gate): null_edge_bps is the mean forward_return over ALL pooled
    de-overlapped events regardless of sign — same detection, same
    per-symbol de-overlap, same pooling as the strategy leg, only the
    long_only_mask direction filter removed.
    """

    def test_null_edge_averages_over_up_and_down_gap_events(self):
        """_make_gap_df(n=100) produces up-gap events at forward_return=+1%
        and down-gap events at forward_return=-1% in equal counts (49 each,
        by construction — see the by-hand count below), so the null must
        average to exactly 0.0 bps: the strategy's long-only +100bps gross
        edge is then entirely edge_over_null."""
        from strategy_fit.families_event import run_event_family
        n = 100
        hist = {"SYM": _make_gap_df(n=n)}
        result = run_event_family(hist, event_detector_name="gap_event", horizon_trading_days=1)
        up_n = _up_gap_event_count(n)
        down_n = _down_gap_event_count(n)
        assert up_n == down_n == 49, (
            "precondition for the hand-computed null below: equal up/down counts"
        )
        assert result["status"] == "tested"
        expected_null_bps = (up_n * 0.01 + down_n * (-0.01)) / (up_n + down_n) * 1e4
        assert expected_null_bps == pytest.approx(0.0, abs=1e-9)
        assert result["null_edge_bps"] == pytest.approx(expected_null_bps, abs=1e-6)
        assert result["edge_over_null_bps"] == pytest.approx(
            result["gross_edge_bps"] - expected_null_bps, rel=1e-6
        )
        assert result["edge_over_null_bps"] == pytest.approx(100.0, rel=1e-6)

    def test_null_edge_none_on_non_tested_status(self):
        """The all-down-gap fixture (no long-side events at all) must report
        null_edge_bps=None, edge_over_null_bps=None — never a stale or
        fabricated value."""
        from strategy_fit.families_event import run_event_family
        n = 100
        dates = pd.bdate_range("2025-01-01", periods=n)
        ref_price = 1000.0
        closes = np.empty(n)
        closes[0] = ref_price
        for t in range(0, n - 1):
            closes[t + 1] = closes[t] * 0.99
        opens = np.empty(n)
        opens[0] = closes[0]
        for t in range(1, n):
            opens[t] = closes[t - 1] * 0.95
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": opens,
            "high": np.maximum(opens, closes) * 1.001,
            "low": np.minimum(opens, closes) * 0.999,
            "close": closes,
            "volume": np.full(n, 1_000_000),
            "net_foreign": np.zeros(n),
        })
        hist = {"SYM": df}
        result = run_event_family(hist, event_detector_name="gap_event", horizon_trading_days=1)
        assert result["status"] == "insufficient_n"
        assert result["null_edge_bps"] is None
        assert result["edge_over_null_bps"] is None
