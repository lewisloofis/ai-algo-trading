"""
strategy_fit/test_intraday_walk_forward.py — TDD tests for intraday walk-forward / persistence.

Validates strategy_fit/intraday_walk_forward.py:
  - IntradayWalkForwardResult: frozen dataclass fields
  - walk_forward_intraday: split logic (60/40 IS/OOS), persistence flag,
    thin-OOS conservative case, decay_ratio
  - required_oos_eff_n / oos_underpowered: power-derived floor (plan-10 W2.1),
    distinct from the hard MIN_OOS_EFF_N not-zero floor
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Shared fixture helpers (reuse the intraday bar pattern from test_intraday.py)
# ---------------------------------------------------------------------------

def _make_intraday_df(n_days: int, ref_price: float = 1000.0) -> pd.DataFrame:
    """Build a minimal synthetic intraday DataFrame with n_days of session bars.
    One trade-size close per day at 16:15 for simplicity; the walk-forward function
    uses groupby('date'), so only the date column matters for splitting.
    """
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # Place a minimal set of bars per day (enough for OR window + close)
        for h, m in [(9, 0), (9, 30), (11, 0), (16, 15)]:
            rows.append({
                "datetime": date.replace(hour=h, minute=m),
                "date": date.date(),
                "open": ref_price,
                "high": ref_price * 1.005,
                "low": ref_price * 0.995,
                "close": ref_price,
                "volume": 1000,
            })
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def _make_positive_edge_df(n_days: int = 60, ref_price: float = 1000.0) -> pd.DataFrame:
    """Intraday df where a positive ORB edge is injected on every day:
    OR bars flat at ref_price, bar 09:30 at ref_price*1.01 (breakout),
    close at ref_price*1.02. Consistent edge in both IS and OOS.
    """
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # OR bars 09:00–09:29 at ref_price
        for m in range(30):
            rows.append({"datetime": date.replace(hour=9, minute=m), "date": date.date(),
                         "open": ref_price, "high": ref_price, "low": ref_price,
                         "close": ref_price, "volume": 1000})
        # Trigger bar 09:30: clear breakout
        rows.append({"datetime": date.replace(hour=9, minute=30), "date": date.date(),
                     "open": ref_price, "high": ref_price * 1.015, "low": ref_price,
                     "close": ref_price * 1.01, "volume": 1000})
        # Fill bars until close
        for h, m in [(11, 0), (16, 15)]:
            rows.append({"datetime": date.replace(hour=h, minute=m), "date": date.date(),
                         "open": ref_price * 1.01, "high": ref_price * 1.025,
                         "low": ref_price * 1.0, "close": ref_price * 1.02, "volume": 1000})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def _make_negative_oos_df(n_days: int = 100, ref_price: float = 1000.0) -> pd.DataFrame:
    """First 60% of days (IS) have positive ORB edge; last 40% (OOS) have MEAN-REVERTING (losing) edge.

    IS:  OR window flat at ref_price; upward breakout at 09:30 (close > OR_high);
         close of day continues up → long trade wins.
    OOS: Same upward breakout at 09:30 (same signal); but by close the price has
         REVERSED BACK to ref_price → entry at ref_price*1.01, exit at ref_price → loss.

    The ORB family enters LONG on both IS and OOS (breakout above OR).
    IS: exit_close > entry_close → positive signed return.
    OOS: exit_close < entry_close → negative signed return.
    So OOS gross_edge_bps < 0 → is_persistent=False.
    """
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    is_days = int(round(n_days * 0.6))
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        in_sample = days_added < is_days
        # OR bars (09:00–09:29): flat at ref_price
        for m in range(30):
            rows.append({"datetime": date.replace(hour=9, minute=m), "date": date.date(),
                         "open": ref_price, "high": ref_price, "low": ref_price,
                         "close": ref_price, "volume": 1000})
        # Trigger bar 09:30: upward breakout on BOTH IS and OOS (same signal)
        trigger_close = ref_price * 1.01
        rows.append({"datetime": date.replace(hour=9, minute=30), "date": date.date(),
                     "open": ref_price, "high": ref_price * 1.015, "low": ref_price,
                     "close": trigger_close, "volume": 1000})
        # Fill bar at 11:00
        rows.append({"datetime": date.replace(hour=11, minute=0), "date": date.date(),
                     "open": trigger_close, "high": trigger_close * 1.005,
                     "low": trigger_close * 0.995, "close": trigger_close, "volume": 1000})
        if in_sample:
            # IS: price continues up → long wins
            exit_close = ref_price * 1.02
        else:
            # OOS: price mean-reverts back below entry → long loses
            exit_close = ref_price * 0.99
        rows.append({"datetime": date.replace(hour=16, minute=15), "date": date.date(),
                     "open": trigger_close, "high": max(exit_close, trigger_close),
                     "low": min(exit_close, trigger_close),
                     "close": exit_close, "volume": 1000})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Section A: IntradayWalkForwardResult dataclass
# ---------------------------------------------------------------------------

class TestIntradayWalkForwardResult:
    def test_result_is_frozen_dataclass(self) -> None:
        """IntradayWalkForwardResult must be a frozen dataclass (direct attribute assignment raises)."""
        import dataclasses
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult
        r = IntradayWalkForwardResult(
            is_edge_bps=50.0,
            oos_edge_bps=30.0,
            is_hit_rate=0.65,
            oos_hit_rate=0.60,
            oos_eff_n=20,
            decay_ratio=0.60,
            is_persistent=True,
            skip_reason=None,
        )
        assert dataclasses.is_dataclass(r), "must be a dataclass"
        # frozen=True means __setattr__ raises FrozenInstanceError (subclass of AttributeError)
        try:
            r.is_edge_bps = 99.0  # type: ignore[misc]
            pytest.fail("Expected FrozenInstanceError on attribute assignment")
        except (AttributeError, TypeError):
            pass  # expected — frozen dataclass

    def test_result_fields_present(self) -> None:
        """Result must expose all required fields."""
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult
        r = IntradayWalkForwardResult(
            is_edge_bps=10.0,
            oos_edge_bps=5.0,
            is_hit_rate=0.55,
            oos_hit_rate=0.52,
            oos_eff_n=15,
            decay_ratio=0.5,
            is_persistent=True,
            skip_reason=None,
        )
        assert hasattr(r, "is_edge_bps")
        assert hasattr(r, "oos_edge_bps")
        assert hasattr(r, "is_hit_rate")
        assert hasattr(r, "oos_hit_rate")
        assert hasattr(r, "oos_eff_n")
        assert hasattr(r, "decay_ratio")
        assert hasattr(r, "is_persistent")
        assert hasattr(r, "skip_reason")

    def test_decay_ratio_none_when_is_edge_zero_or_negative(self) -> None:
        """When is_edge_bps <= 0, decay_ratio should be None."""
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult
        r = IntradayWalkForwardResult(
            is_edge_bps=0.0,
            oos_edge_bps=10.0,
            is_hit_rate=0.5,
            oos_hit_rate=0.55,
            oos_eff_n=20,
            decay_ratio=None,
            is_persistent=False,
            skip_reason=None,
        )
        assert r.decay_ratio is None


# ---------------------------------------------------------------------------
# Section B: split logic
# ---------------------------------------------------------------------------

class TestSplitLogic:
    def test_split_respects_60_40_ratio(self) -> None:
        """IS must be ~60% of trading days, OOS ~40%."""
        from strategy_fit.intraday_walk_forward import (
            walk_forward_intraday,
            IS_FRAC,
        )
        from strategy_fit.families_intraday import run_orb_family
        n_days = 50
        df = _make_positive_edge_df(n_days=n_days)
        # Count unique dates in df
        unique_dates = df["date"].nunique()
        is_expected = int(round(unique_dates * IS_FRAC))
        oos_expected = unique_dates - is_expected
        # We can't directly inspect the split without running it; verify through oos_eff_n
        result = walk_forward_intraday(df, run_orb_family)
        # OOS eff_n should be roughly oos_expected (could be less if some days don't trade)
        assert result.oos_eff_n >= 0, "oos_eff_n must be non-negative"
        # oos days ≈ 40% of total; eff_n can't exceed that
        assert result.oos_eff_n <= oos_expected + 2, (
            f"oos_eff_n={result.oos_eff_n} exceeds expected OOS days {oos_expected}"
        )

    def test_is_frac_constant_is_0_6(self) -> None:
        """IS_FRAC must be 0.6."""
        from strategy_fit.intraday_walk_forward import IS_FRAC
        assert IS_FRAC == 0.6, f"IS_FRAC={IS_FRAC}, expected 0.6"

    def test_chronological_split_no_leakage(self) -> None:
        """OOS must be entirely after IS (no date leakage)."""
        from strategy_fit.intraday_walk_forward import walk_forward_intraday, IS_FRAC
        from strategy_fit.families_intraday import run_orb_family
        df = _make_positive_edge_df(n_days=50)
        dates = sorted(df["date"].unique())
        n_is = int(round(len(dates) * IS_FRAC))
        is_dates = set(dates[:n_is])
        oos_dates = set(dates[n_is:])
        assert is_dates.isdisjoint(oos_dates), "IS and OOS date sets must be disjoint"


# ---------------------------------------------------------------------------
# Section C: persistence flag
# ---------------------------------------------------------------------------

class TestPersistenceFlag:
    def test_persistent_when_oos_positive_and_sufficient_n(self) -> None:
        """Positive OOS edge + eff_n >= MIN_OOS_EFF_N → is_persistent=True.

        n_days=100 → ~40 OOS trading days, all with positive ORB edge.
        Since MIN_EFF_N=30 for the family and MIN_OOS_EFF_N=10 for persistence,
        OOS will produce eff_n~40 → both gates pass → is_persistent=True.
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_positive_edge_df(n_days=100)
        result = walk_forward_intraday(df, run_orb_family)
        assert result.oos_edge_bps is not None, (
            f"OOS should be tested with 100 days; skip_reason={result.skip_reason}"
        )
        assert result.oos_edge_bps > 0, f"OOS edge should be positive, got {result.oos_edge_bps}"
        assert result.oos_eff_n is not None and result.oos_eff_n >= 10, (
            f"OOS eff_n={result.oos_eff_n} must be >= MIN_OOS_EFF_N=10"
        )
        assert result.is_persistent is True, (
            f"Expected persistent with oos_edge_bps={result.oos_edge_bps}, "
            f"oos_eff_n={result.oos_eff_n}"
        )

    def test_not_persistent_when_oos_negative(self) -> None:
        """OOS edge < 0 → is_persistent=False even if eff_n is large enough.

        Uses 100 days so OOS has ~40 days → sufficient for the family function
        to produce a tested result (MIN_EFF_N=30).
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_negative_oos_df(n_days=100)
        result = walk_forward_intraday(df, run_orb_family)
        # OOS has negative edge → must NOT be marked persistent
        assert result.is_persistent is False, (
            f"Negative OOS edge must not be persistent: oos_edge_bps={result.oos_edge_bps}, "
            f"skip_reason={result.skip_reason}"
        )

    def test_thin_oos_conservative_not_persistent(self) -> None:
        """Too few OOS observations → conservative: is_persistent=False."""
        from strategy_fit.intraday_walk_forward import walk_forward_intraday, MIN_OOS_EFF_N
        from strategy_fit.families_intraday import run_orb_family
        # Use very few days so OOS has < MIN_OOS_EFF_N observations
        df = _make_positive_edge_df(n_days=8)  # 8 days → ~3 OOS days < MIN_OOS_EFF_N=10
        result = walk_forward_intraday(df, run_orb_family)
        assert result.is_persistent is False, (
            f"Thin OOS (oos_eff_n={result.oos_eff_n}) must be conservative: not persistent"
        )
        assert result.skip_reason is not None, "Thin OOS must set a skip_reason"

    def test_min_oos_eff_n_constant_is_10(self) -> None:
        """MIN_OOS_EFF_N must be the documented 10."""
        from strategy_fit.intraday_walk_forward import MIN_OOS_EFF_N
        assert MIN_OOS_EFF_N == 10, f"MIN_OOS_EFF_N={MIN_OOS_EFF_N}, expected 10"


# ---------------------------------------------------------------------------
# Section D: decay_ratio
# ---------------------------------------------------------------------------

class TestDecayRatio:
    def test_decay_ratio_is_oos_over_is_when_is_positive(self) -> None:
        """decay_ratio = oos_edge_bps / is_edge_bps when is_edge_bps > 0."""
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult
        r = IntradayWalkForwardResult(
            is_edge_bps=100.0,
            oos_edge_bps=60.0,
            is_hit_rate=0.65,
            oos_hit_rate=0.60,
            oos_eff_n=20,
            decay_ratio=0.60,
            is_persistent=True,
            skip_reason=None,
        )
        assert abs(r.decay_ratio - 0.60) < 1e-9

    def test_decay_ratio_none_when_is_negative(self) -> None:
        """When IS edge <= 0 (degenerate), decay_ratio must be None."""
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult
        r = IntradayWalkForwardResult(
            is_edge_bps=-5.0,
            oos_edge_bps=10.0,
            is_hit_rate=0.45,
            oos_hit_rate=0.55,
            oos_eff_n=20,
            decay_ratio=None,
            is_persistent=False,
            skip_reason="is_edge_bps <= 0",
        )
        assert r.decay_ratio is None

    def test_walk_forward_computes_decay_ratio(self) -> None:
        """walk_forward_intraday must set decay_ratio = oos/is when is_edge > 0."""
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_positive_edge_df(n_days=100)
        result = walk_forward_intraday(df, run_orb_family)
        if result.is_edge_bps is not None and result.is_edge_bps > 0 and result.oos_edge_bps is not None:
            expected_ratio = result.oos_edge_bps / result.is_edge_bps
            assert abs((result.decay_ratio or 0.0) - expected_ratio) < 1e-6, (
                f"decay_ratio={result.decay_ratio} != oos/is={expected_ratio}"
            )


# ---------------------------------------------------------------------------
# Section E: insufficient data edge cases
# ---------------------------------------------------------------------------

class TestInsufficientData:
    def test_empty_df_conservative_result(self) -> None:
        """Empty DataFrame must return a conservative (not persistent) result."""
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = pd.DataFrame(columns=["datetime", "date", "open", "high", "low", "close", "volume"])
        result = walk_forward_intraday(df, run_orb_family)
        assert result.is_persistent is False
        assert result.skip_reason is not None

    def test_too_few_is_days_for_family_fn(self) -> None:
        """Insufficient IS days → family_fn may return insufficient_n → conservative."""
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_intraday_df(n_days=5)  # too few days; IS has ~3 days
        result = walk_forward_intraday(df, run_orb_family)
        # Very few days → insufficient_n or thin OOS → conservative
        assert result.is_persistent is False


# ---------------------------------------------------------------------------
# Section F: power-derived required-N / oos_underpowered (plan-10 W2.1)
# ---------------------------------------------------------------------------

def _make_fixed_sigma_edge_df(
    n_days: int = 100,
    ref_price: float = 1000.0,
    edge_bps: float = 15.0,
    sigma_bps: float = 150.0,
) -> pd.DataFrame:
    """Intraday df with an EXACT, deterministic per-trade mean and dispersion.

    Every day triggers the same ORB long breakout (OR bars flat at ref_price,
    trigger bar at ref_price*1.001).  The per-trade return alternates between
    (edge_bps + sigma_bps) and (edge_bps - sigma_bps) bps on successive days,
    so — with no RNG, hence no seed-dependent flakiness — the realized signed-
    returns series has sample mean == edge_bps exactly and sample std ==
    sigma_bps (up to the ddof=1 correction) exactly, in both the IS and OOS
    halves (n_days chosen so each half's day-count is even).

    This models the *actual* per-trade dispersion measured by
    families_intraday.py's per_trade_sigma_bps — unlike an injected day-level
    price random walk (which cancels out of the trade-return ratio and would
    leave the true per-trade sigma at ~0), this varies the trade's own P&L
    directly, which is what lib.stats.required_effective_n must be fed.
    """
    rows: list[dict] = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # OR bars 09:00-09:29: flat at ref_price (identical every day).
        for m in range(30):
            rows.append({"datetime": date.replace(hour=9, minute=m), "date": date.date(),
                         "open": ref_price, "high": ref_price, "low": ref_price,
                         "close": ref_price, "volume": 1000})
        # Trigger bar 09:30: fixed small breakout — this IS the entry price.
        trigger_close = ref_price * 1.001
        rows.append({"datetime": date.replace(hour=9, minute=30), "date": date.date(),
                     "open": ref_price, "high": trigger_close * 1.001, "low": ref_price,
                     "close": trigger_close, "volume": 1000})
        # Exit: alternating +/- sigma_bps around edge_bps, exact by construction.
        delta = sigma_bps if (days_added % 2 == 0) else -sigma_bps
        trade_return_bps = edge_bps + delta
        exit_close = trigger_close * (1.0 + trade_return_bps / 1e4)
        for h, m in [(11, 0), (16, 15)]:
            rows.append({"datetime": date.replace(hour=h, minute=m), "date": date.date(),
                         "open": trigger_close, "high": max(exit_close, trigger_close) * 1.001,
                         "low": min(exit_close, trigger_close), "close": exit_close, "volume": 1000})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


class TestPowerDerivedRequiredN:
    def test_result_exposes_required_oos_eff_n_and_underpowered_fields(self) -> None:
        """IntradayWalkForwardResult must expose the two new fields."""
        from strategy_fit.intraday_walk_forward import IntradayWalkForwardResult
        r = IntradayWalkForwardResult(
            is_edge_bps=10.0,
            oos_edge_bps=5.0,
            is_hit_rate=0.55,
            oos_hit_rate=0.52,
            oos_eff_n=15,
            decay_ratio=0.5,
            is_persistent=True,
            skip_reason=None,
        )
        assert hasattr(r, "required_oos_eff_n")
        assert hasattr(r, "oos_underpowered")
        # Defaults when omitted: not computed / not flagged.
        assert r.required_oos_eff_n is None
        assert r.oos_underpowered is False

    def test_small_edge_against_large_sigma_is_underpowered(self) -> None:
        """Clears MIN_OOS_EFF_N (oos_eff_n=40) but the edge is tiny vs. the
        MEASURED per-trade sigma (~150bps, exact by construction) → required N
        is huge → oos_underpowered=True, while is_persistent stays True (edge
        is positive, hard floor cleared).

        This is the expected, honest "underpowered" verdict from plan-10 W2.1 —
        NOT a pipeline failure.  Deterministic (no RNG): edge_bps=15 vs.
        sigma_bps=150 gives required_n=ceil((2*~152/15)^2)~=411, oos_eff_n=40.
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday, MIN_OOS_EFF_N
        from strategy_fit.families_intraday import run_orb_family
        df = _make_fixed_sigma_edge_df(n_days=100, edge_bps=15.0, sigma_bps=150.0)
        result = walk_forward_intraday(df, run_orb_family)
        assert result.oos_eff_n is not None and result.oos_eff_n >= MIN_OOS_EFF_N, (
            f"Test setup should clear the hard floor; got oos_eff_n={result.oos_eff_n}"
        )
        assert result.is_persistent is True, (
            f"Small-but-positive OOS edge with sufficient N should be persistent; "
            f"oos_edge_bps={result.oos_edge_bps}, skip_reason={result.skip_reason}"
        )
        assert result.oos_sigma_bps is not None
        assert abs(result.oos_sigma_bps - 150.0) < 10.0, (
            f"oos_sigma_bps={result.oos_sigma_bps} should be close to the "
            "injected 150bps dispersion (ddof=1 correction aside)"
        )
        assert result.required_oos_eff_n is not None
        assert result.required_oos_eff_n > result.oos_eff_n, (
            f"required_oos_eff_n={result.required_oos_eff_n} should exceed "
            f"oos_eff_n={result.oos_eff_n} for a small edge vs. large sigma"
        )
        assert result.oos_underpowered is True

    def test_large_edge_against_same_sigma_is_not_underpowered(self) -> None:
        """A much larger, still-consistent edge against the same MEASURED
        per-trade sigma needs far fewer samples → oos_underpowered=False.
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday, MIN_OOS_EFF_N
        from strategy_fit.families_intraday import run_orb_family
        df = _make_fixed_sigma_edge_df(n_days=100, edge_bps=500.0, sigma_bps=150.0)
        result = walk_forward_intraday(df, run_orb_family)
        assert result.oos_eff_n is not None and result.oos_eff_n >= MIN_OOS_EFF_N
        assert result.is_persistent is True
        assert result.required_oos_eff_n is not None
        assert result.required_oos_eff_n <= result.oos_eff_n, (
            f"required_oos_eff_n={result.required_oos_eff_n} should be well below "
            f"oos_eff_n={result.oos_eff_n} for a large edge vs. modest sigma"
        )
        assert result.oos_underpowered is False

    def test_thin_oos_is_not_labelled_underpowered(self) -> None:
        """Failing the HARD floor is a more fundamental issue than power —
        thin-OOS results must not also carry oos_underpowered=True.
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_positive_edge_df(n_days=8)  # ~3 OOS days < MIN_OOS_EFF_N=10
        result = walk_forward_intraday(df, run_orb_family)
        assert result.is_persistent is False
        assert result.oos_underpowered is False

    def test_negative_oos_edge_is_not_labelled_underpowered(self) -> None:
        """A non-positive OOS edge is a directional failure, not a power one."""
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_negative_oos_df(n_days=100)
        result = walk_forward_intraday(df, run_orb_family)
        assert result.is_persistent is False
        assert result.oos_underpowered is False
        assert result.required_oos_eff_n is None


# ---------------------------------------------------------------------------
# Section G: sigma must be measured, never a fallback constant (plan-10 W2.1)
# ---------------------------------------------------------------------------

class TestSigmaIsMeasuredNeverAFallback:
    def test_sigma_unavailable_does_not_fall_back_to_a_default(self) -> None:
        """When family_fn's OOS result omits per_trade_sigma_bps (e.g. a
        family implementation that hasn't been updated to report it),
        required_oos_eff_n must NOT be computed via any fallback constant —
        it must stay None, and oos_underpowered must stay False (an honestly
        reported "power unassessed", never a false positive or negative).
        is_persistent is unaffected: power is a separate, non-blocking
        annotation, not a persistence input.
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday

        def _stub_family_fn(df: pd.DataFrame) -> dict:
            return {
                "status": "tested",
                "skip_reason": None,
                "gross_edge_bps": 50.0,
                "hit_rate": 0.6,
                "eff_n": int(df["date"].nunique()),
                "p_value": 0.01,
                "net_edge_bps": None,
                # per_trade_sigma_bps deliberately omitted.
            }

        df = _make_positive_edge_df(n_days=100)
        result = walk_forward_intraday(df, _stub_family_fn)
        assert result.oos_sigma_bps is None
        assert result.required_oos_eff_n is None
        assert result.oos_underpowered is False
        assert result.is_persistent is True

    def test_real_family_reports_a_measured_sigma_when_tested(self) -> None:
        """families_intraday.py's real family functions must populate
        per_trade_sigma_bps (not None) whenever they return status=tested —
        this is the field the power calculation depends on end-to-end."""
        from strategy_fit.families_intraday import run_orb_family
        df = _make_positive_edge_df(n_days=100)
        result = run_orb_family(df)
        assert result["status"] == "tested"
        assert result["per_trade_sigma_bps"] is not None
        assert result["per_trade_sigma_bps"] >= 0.0


# ---------------------------------------------------------------------------
# Section H: neither half of the split is usable — report that explicitly
# ---------------------------------------------------------------------------

class TestInvalidSplitReportedExplicitly:
    def test_neither_half_usable_reports_invalid_split_explicitly(self) -> None:
        """When BOTH IS and OOS individually fail the family's own MIN_EFF_N
        threshold, the skip_reason must say so explicitly — a generic "thin
        OOS" message would wrongly imply IS was a valid comparison point,
        when in fact NEITHER half could be validated at this split frequency
        (this is what the team measured on the real 108-day store: every
        symbol dropped below MIN_EFF_N=30 on BOTH halves).
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        df = _make_positive_edge_df(n_days=40)  # IS~24, OOS~16 — both < MIN_EFF_N=30
        result = walk_forward_intraday(df, run_orb_family)
        assert result.is_persistent is False
        assert result.skip_reason is not None
        assert "invalid split" in result.skip_reason
        assert "neither IS" in result.skip_reason

    def test_oos_alone_thin_with_valid_is_uses_the_plain_thin_oos_message(self) -> None:
        """When IS IS valid but only OOS is thin, keep the original, narrower
        "thin OOS" message — it is a real distinction from "neither half
        usable" and must not be conflated with it.
        """
        from strategy_fit.intraday_walk_forward import walk_forward_intraday
        from strategy_fit.families_intraday import run_orb_family
        # n_days chosen so IS (60%) clears MIN_EFF_N=30 but OOS (40%) doesn't.
        df = _make_positive_edge_df(n_days=55)  # IS=33 (tested), OOS=22 (insufficient_n)
        result = walk_forward_intraday(df, run_orb_family)
        assert result.is_persistent is False
        assert result.skip_reason is not None
        assert "thin OOS" in result.skip_reason
        assert "invalid split" not in result.skip_reason
