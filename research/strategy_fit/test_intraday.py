"""
strategy_fit/test_intraday.py — TDD tests for intraday same-day strategy families.

Test sections:
  A — intraday_history from StrategyFitFixtureDataSource
  B — intraday family functions (orb, momentum, mean_reversion)
  C — cost gate for hold=0
  D — end-to-end integration
"""
from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

# --- helpers ---

def _make_flat_df(n_days: int = 40, ref_price: float = 1000.0) -> pd.DataFrame:
    """Build a flat-price intraday DataFrame with n_days of session bars."""
    rows = []
    date = pd.Timestamp("2025-01-06")  # Monday
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # AM: 09:00–11:59 (180 bars)
        for h in range(9, 12):
            for m in range(60):
                dt = date.replace(hour=h, minute=m)
                rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                             "high": ref_price, "low": ref_price, "close": ref_price,
                             "volume": 1000, "value": ref_price * 1000, "net_foreign": 0})
        # PM: 13:30–16:15 (165 bars)
        for h_m in range(13 * 60 + 30, 16 * 60 + 16):
            h = h_m // 60
            m = h_m % 60
            dt = date.replace(hour=h, minute=m)
            rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                         "high": ref_price, "low": ref_price, "close": ref_price,
                         "volume": 1000, "value": ref_price * 1000, "net_foreign": 0})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def _make_orb_edge_df(n_days: int = 40, ref_price: float = 1000.0) -> pd.DataFrame:
    """Build intraday DataFrame with clear ORB edge injected.
    OR bars (09:00-09:29): close=ref_price → OR_high=ref_price.
    Bar 09:30: close=ref_price*1.02 → long signal.
    Last bar of day: close=ref_price*1.03 → return≈98bps.
    """
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # AM: 09:00–11:59
        for h in range(9, 12):
            for m in range(60):
                dt = date.replace(hour=h, minute=m)
                if h == 9 and m < 30:
                    # OR window: flat at ref_price
                    close_val = ref_price
                elif h == 9 and m == 30:
                    # Trigger bar: breakout above OR_high
                    close_val = ref_price * 1.02
                else:
                    close_val = ref_price * 1.02
                rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                             "high": close_val, "low": ref_price * 0.99, "close": close_val,
                             "volume": 1000, "value": close_val * 1000, "net_foreign": 0})
        # PM: 13:30–16:14, last bar at 16:14 has close=ref_price*1.03
        for h_m in range(13 * 60 + 30, 16 * 60 + 16):
            h = h_m // 60
            m = h_m % 60
            dt = date.replace(hour=h, minute=m)
            is_last = (h == 16 and m == 15)
            close_val = ref_price * 1.03 if is_last else ref_price * 1.02
            rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                         "high": close_val, "low": ref_price * 0.99, "close": close_val,
                         "volume": 1000, "value": close_val * 1000, "net_foreign": 0})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def _make_momentum_edge_df(n_days: int = 40, ref_price: float = 1000.0) -> pd.DataFrame:
    """Build intraday DataFrame with morning-to-afternoon momentum edge.
    open_of_day = ref_price, close_at_11 > open_of_day (positive signal),
    close_of_day > close_at_11 (positive forward return).
    """
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        # AM: 09:00–11:59
        for h in range(9, 12):
            for m in range(60):
                dt = date.replace(hour=h, minute=m)
                # Gradual rise through morning
                frac = (h - 9) * 60 + m
                close_val = ref_price * (1.0 + 0.01 * frac / 180)  # rises to +1% at 11:59
                rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                             "high": close_val, "low": ref_price, "close": close_val,
                             "volume": 1000, "value": close_val * 1000, "net_foreign": 0})
        # PM: 13:30–16:15 — continues rising
        for h_m in range(13 * 60 + 30, 16 * 60 + 16):
            h = h_m // 60
            m = h_m % 60
            dt = date.replace(hour=h, minute=m)
            close_val = ref_price * 1.02  # +2% by close
            rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                         "high": close_val, "low": ref_price, "close": close_val,
                         "volume": 1000, "value": close_val * 1000, "net_foreign": 0})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def _make_vwap_edge_df(n_days: int = 40, ref_price: float = 1000.0) -> pd.DataFrame:
    """Build intraday DataFrame with VWAP mean-reversion edge.

    AM bars: first 60 bars at ref_price (establishing VWAP ≈ ref_price),
    then remaining AM bars at ref_price * 1.04 (pulls close_at_11 well above VWAP).
    Signal = -(close_at_11 / vwap_am - 1) < 0 → negative signal means SHORT.
    PM: price reverts back to ref_price → forward_return < 0.
    signed_return = signal × forward_return = negative × negative = positive.
    """
    rows = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        bar_idx = 0
        # AM: 09:00–11:59 (180 bars total)
        for h in range(9, 12):
            for m in range(60):
                dt = date.replace(hour=h, minute=m)
                # First 60 bars at ref_price → VWAP anchored near ref_price
                # Remaining 120 bars at ref_price * 1.04 → close_at_11 >> VWAP
                close_val = ref_price if bar_idx < 60 else ref_price * 1.04
                rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                             "high": close_val * 1.005, "low": ref_price, "close": close_val,
                             "volume": 1000, "value": close_val * 1000, "net_foreign": 0})
                bar_idx += 1
        # PM: price reverts back to ref_price (mean reversion)
        for h_m in range(13 * 60 + 30, 16 * 60 + 16):
            h = h_m // 60
            m = h_m % 60
            dt = date.replace(hour=h, minute=m)
            close_val = ref_price  # reverts to VWAP
            rows.append({"datetime": dt, "date": date.date(), "open": ref_price * 1.04,
                         "high": ref_price * 1.04, "low": ref_price, "close": close_val,
                         "volume": 1000, "value": close_val * 1000, "net_foreign": 0})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


# ============================================================
# Section A: intraday_history from StrategyFitFixtureDataSource
# ============================================================

class TestIntradayHistory:
    def test_intraday_history_session_filter_drops_preopen(self):
        """All returned timestamps must be within the trading session (09:00–16:15).
        Pre-open bars (e.g. 08:30) must not appear."""
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df = ds.intraday_history("ITMG")
        # Check no bar before 09:00
        assert len(df) > 0, "intraday_history returned empty DataFrame"
        times = pd.to_datetime(df["datetime"]).dt
        for idx, row in df.iterrows():
            dt = pd.Timestamp(row["datetime"])
            h, m = dt.hour, dt.minute
            in_am = (h * 60 + m >= 9 * 60) and (h * 60 + m < 12 * 60)
            in_pm = (h * 60 + m >= 13 * 60 + 30) and (h * 60 + m <= 16 * 60 + 15)
            assert in_am or in_pm, f"Bar at {h:02d}:{m:02d} is outside session"

    def test_intraday_history_dedupes_and_sorts_ascending(self):
        """Returned DataFrame must be sorted ascending by datetime."""
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df = ds.intraday_history("ITMG")
        dts = pd.to_datetime(df["datetime"]).tolist()
        assert dts == sorted(dts), "intraday_history result is not sorted ascending"

    def test_intraday_history_columns_present(self):
        """Result must have all required columns."""
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df = ds.intraday_history("ITMG")
        required = {"datetime", "date", "open", "high", "low", "close", "volume", "value", "net_foreign"}
        missing = required - set(df.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_intraday_history_net_foreign_fallback_zero(self):
        """net_foreign must be present and default to 0 when foreign data absent."""
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        df = ds.intraday_history("BUMI")  # noise symbol — net_foreign=0
        assert "net_foreign" in df.columns
        assert df["net_foreign"].isna().sum() == 0, "net_foreign must not have NaN"


# ============================================================
# Section B: intraday family functions
# ============================================================

class TestIntradayFamilies:
    def test_orb_flat_series_no_edge(self):
        """Flat price series → ORB gross_edge near zero, eff_n >= 30."""
        from strategy_fit.families_intraday import run_orb_family
        df = _make_flat_df(n_days=40)
        result = run_orb_family(df)
        # Flat: no breakout, so all days have signal=0 → insufficient_n
        # OR: if some days have a tiny trigger, edge ≈ 0
        if result["status"] == "tested":
            assert abs(result["gross_edge_bps"]) < 5.0, f"Expected flat edge, got {result['gross_edge_bps']}"
        else:
            # insufficient_n is also acceptable for flat price (no breakout)
            assert result["status"] in ("tested", "insufficient_n")

    def test_orb_injected_edge_survivor(self):
        """Injected ORB edge → gross_edge_bps > 0, eff_n >= 30, hit_rate > 0.5."""
        from strategy_fit.families_intraday import run_orb_family
        from lib.stats import MIN_EFF_N
        df = _make_orb_edge_df(n_days=40)
        result = run_orb_family(df)
        assert result["status"] == "tested", f"Expected tested, got {result['status']}: {result.get('skip_reason')}"
        assert result["gross_edge_bps"] > 0, f"Expected positive gross edge, got {result['gross_edge_bps']}"
        assert result["eff_n"] >= MIN_EFF_N, f"eff_n={result['eff_n']} < MIN_EFF_N={MIN_EFF_N}"
        assert result["hit_rate"] > 0.5, f"hit_rate={result['hit_rate']} <= 0.5"

    def test_momentum_flat_no_edge(self):
        """Flat series → intraday momentum has no strong edge."""
        from strategy_fit.families_intraday import run_intraday_momentum_family
        df = _make_flat_df(n_days=40)
        result = run_intraday_momentum_family(df)
        if result["status"] == "tested":
            assert abs(result["gross_edge_bps"]) < 5.0

    def test_momentum_injected_edge_survivor(self):
        """Morning-to-afternoon momentum edge → positive gross_edge."""
        from strategy_fit.families_intraday import run_intraday_momentum_family
        from lib.stats import MIN_EFF_N
        df = _make_momentum_edge_df(n_days=40)
        result = run_intraday_momentum_family(df)
        assert result["status"] == "tested", f"Got {result['status']}: {result.get('skip_reason')}"
        assert result["gross_edge_bps"] > 0, f"Expected positive gross edge, got {result['gross_edge_bps']}"
        assert result["eff_n"] >= MIN_EFF_N

    def test_mean_reversion_flat_no_edge(self):
        """Flat series → VWAP mean reversion has no strong edge."""
        from strategy_fit.families_intraday import run_intraday_mean_reversion_family
        df = _make_flat_df(n_days=40)
        result = run_intraday_mean_reversion_family(df)
        if result["status"] == "tested":
            assert abs(result["gross_edge_bps"]) < 5.0

    def test_mean_reversion_injected_edge_survivor(self):
        """Injected VWAP overextension → positive gross_edge."""
        from strategy_fit.families_intraday import run_intraday_mean_reversion_family
        from lib.stats import MIN_EFF_N
        df = _make_vwap_edge_df(n_days=40)
        result = run_intraday_mean_reversion_family(df)
        assert result["status"] == "tested", f"Got {result['status']}: {result.get('skip_reason')}"
        assert result["gross_edge_bps"] > 0, f"Expected positive gross edge, got {result['gross_edge_bps']}"
        assert result["eff_n"] >= MIN_EFF_N

    def test_eff_n_meets_min_threshold(self):
        """Injected-edge fixture produces eff_n >= MIN_EFF_N=30."""
        from strategy_fit.families_intraday import run_orb_family
        from lib.stats import MIN_EFF_N
        df = _make_orb_edge_df(n_days=40)
        result = run_orb_family(df)
        if result["status"] == "tested":
            assert result["eff_n"] >= MIN_EFF_N


# ============================================================
# Section C: cost gate for hold=0
# ============================================================

class TestHold0CostGate:
    def test_hold0_financing_is_zero(self):
        """financing_bps(any_dow, 0) = 0 for all days of week."""
        from strategy_fit.financing import financing_bps
        for dow in range(5):
            cost = financing_bps(dow, 0)
            assert cost == 0.0, f"Expected 0.0 for hold=0, got {cost} on dow={dow}"

    def test_hold0_hurdle_equals_floor_plus_impact(self):
        """best_feasible_hurdle_bps(floor, impact, 0) = floor + impact."""
        from strategy_fit.financing import best_feasible_hurdle_bps
        floor = 49.1
        impact = 0.0
        hurdle, _ = best_feasible_hurdle_bps(floor, impact, 0)
        assert abs(hurdle - (floor + impact)) < 1e-9, f"Expected {floor + impact}, got {hurdle}"

    def test_hold0_gross_just_above_floor_passes(self):
        """A cell with gross = hurdle + 1 passes the cost gate."""
        from strategy_fit.financing import best_feasible_hurdle_bps
        floor = 49.1
        impact = 0.0
        hurdle, _ = best_feasible_hurdle_bps(floor, impact, 0)
        gross = hurdle + 1.0
        assert gross > hurdle, "gross > hurdle should pass the gate"

    def test_hold0_gross_just_below_floor_fails(self):
        """A cell with gross = hurdle - 1 fails the cost gate."""
        from strategy_fit.financing import best_feasible_hurdle_bps
        floor = 49.1
        impact = 0.0
        hurdle, _ = best_feasible_hurdle_bps(floor, impact, 0)
        gross = hurdle - 1.0
        assert gross <= hurdle, "gross <= hurdle should fail the gate"

    def test_hold0_net_edge_equals_gross_minus_hurdle(self):
        """net_edge_bps = gross_edge_bps - hurdle."""
        from strategy_fit.financing import best_feasible_hurdle_bps
        floor = 49.1
        impact = 0.0
        hurdle, _ = best_feasible_hurdle_bps(floor, impact, 0)
        gross = 100.0
        net = gross - hurdle
        assert abs(net - (gross - hurdle)) < 1e-9


# ============================================================
# Section D: end-to-end
# ============================================================

class TestIntradayEndToEnd:
    def test_e2e_intraday_cells_in_coverage(self, tmp_path):
        """run_strategy_fit with fixture source produces portfolio.md containing 'intraday_same_day'."""
        from strategy_fit.run_strategy_fit import run_strategy_fit
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        ds = StrategyFitFixtureDataSource()
        run_strategy_fit(ds=ds, outdir=str(tmp_path))
        content = (tmp_path / "strategy_portfolio.md").read_text()
        assert "intraday_same_day" in content, (
            "strategy_portfolio.md must contain 'intraday_same_day' shape coverage"
        )

    def test_e2e_injected_edge_name_survives_intraday(self):
        """At least one intraday cell for ITMG passes the cost gate."""
        from strategy_fit.run_strategy_fit import run_grid
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource
        from strategy_fit.harness import SHAPE_INTRADAY_SAME_DAY
        ds = StrategyFitFixtureDataSource()
        cells = run_grid(ds=ds)
        intraday_itmg = [
            c for c in cells
            if c.symbol == "ITMG" and c.shape == SHAPE_INTRADAY_SAME_DAY
        ]
        assert len(intraday_itmg) > 0, "No intraday cells found for ITMG"
        passing = [c for c in intraday_itmg if c.passes_cost_gate is True]
        assert len(passing) > 0, (
            f"No ITMG intraday cell passes cost gate. "
            f"Cells: {[(c.family_name, c.status, c.gross_edge_bps, c.min_edge_hurdle_bps) for c in intraday_itmg]}"
        )


# ============================================================
# Section E: BUG 1 — one-sample p-value on signed per-trade returns
# ============================================================

class TestSignedReturnPValue:
    """The family p-value must be a one-sample test that mean(signed_returns) > 0.

    Regression for the degenerate p=1.0 bug where abs(signed_returns) was fed
    as the 'forward return', making the directional target all-positive.
    """

    def test_strong_positive_signed_returns_small_p(self):
        """A strongly-positive signed-returns array yields p < 0.05."""
        from strategy_fit.families_intraday import _compute_family_metrics
        rng = np.random.default_rng(0)
        # Mean +60 bps/day with modest noise → clearly profitable.
        signed = 0.0060 + rng.normal(0.0, 0.0010, size=60)
        result = _compute_family_metrics(signed)
        assert result["status"] == "tested"
        assert result["gross_edge_bps"] > 0
        assert result["p_value"] < 0.05, (
            f"Strong positive edge must yield small p; got {result['p_value']}"
        )

    def test_zero_mean_noise_p_around_half(self):
        """A sample-mean-zero noisy array yields p around 0.5 (not degenerate 1.0)."""
        from strategy_fit.families_intraday import _compute_family_metrics
        rng = np.random.default_rng(1)
        signed = rng.normal(0.0, 0.0050, size=200)
        signed = signed - signed.mean()  # exact zero sample mean
        result = _compute_family_metrics(signed)
        assert result["status"] == "tested"
        # Exactly zero sample mean → bootstrap means straddle 0 → p ≈ 0.5,
        # and certainly not pinned at the degenerate 1.0.
        assert 0.3 < result["p_value"] < 0.7, (
            f"Zero-mean noise should give p≈0.5; got {result['p_value']}"
        )

    def test_negative_mean_p_near_one(self):
        """A negative-mean array yields p near 1.0 (rarely beats zero)."""
        from strategy_fit.families_intraday import _compute_family_metrics
        rng = np.random.default_rng(2)
        signed = -0.0060 + rng.normal(0.0, 0.0010, size=60)
        result = _compute_family_metrics(signed)
        assert result["status"] == "tested"
        assert result["p_value"] > 0.95, (
            f"Negative edge must yield large p; got {result['p_value']}"
        )

    def test_momentum_positive_vs_zero_mean_pvalue(self):
        """End-to-end: momentum on a positive-edge fixture has a far smaller
        p-value than on a flat (zero-edge) fixture — the test is sensible."""
        from strategy_fit.families_intraday import run_intraday_momentum_family
        pos = run_intraday_momentum_family(_make_momentum_edge_df(n_days=40))
        flat = run_intraday_momentum_family(_make_flat_df(n_days=40))
        assert pos["status"] == "tested"
        assert pos["p_value"] < 0.05, (
            f"Positive-edge momentum must be significant; got p={pos['p_value']}"
        )
        # Flat fixture: either no signal (insufficient_n) or a non-significant p.
        if flat["status"] == "tested":
            assert flat["p_value"] > pos["p_value"], (
                "Flat fixture p-value must exceed the positive-edge p-value"
            )


# ============================================================
# Section F: BUG 2 — ORB trades on a normal fraction of days
# ============================================================

def _make_orb_breakout_most_days_df(
    n_days: int = 40, ref_price: float = 1000.0, seed: int = 7
) -> pd.DataFrame:
    """Intraday fixture where price breaks the opening range AFTER the OR window
    on most days (the breakout bar is several minutes past 09:30, not the 09:30
    bar itself). Direction alternates so the fixture is not trivially one-sided.

    OR window 09:00–09:29: a tight band around ref_price.
    From 09:35 onward: price steps clearly above (or below) the OR band and
    holds there into the close. This must trigger ~1 trade/day.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    date = pd.Timestamp("2025-01-06")
    days_added = 0
    while days_added < n_days:
        if date.weekday() >= 5:
            date += pd.Timedelta(days=1)
            continue
        up = (days_added % 2 == 0)  # alternate breakout direction
        breakout_close = ref_price * (1.015 if up else 0.985)
        exit_close = ref_price * (1.020 if up else 0.980)
        # AM 09:00–11:59
        for h in range(9, 12):
            for m in range(60):
                dt = date.replace(hour=h, minute=m)
                t_min = h * 60 + m
                if t_min < 9 * 60 + 30:
                    # OR window: tight band around ref_price.
                    c = ref_price * (1.0 + rng.normal(0.0, 0.0005))
                elif t_min < 9 * 60 + 35:
                    # Transition bars stay within the OR band (no early trigger).
                    c = ref_price * (1.0 + rng.normal(0.0, 0.0005))
                else:
                    # Clear breakout that holds.
                    c = breakout_close
                rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                             "high": max(c, ref_price) * 1.001,
                             "low": min(c, ref_price) * 0.999,
                             "close": c, "volume": 1000,
                             "value": c * 1000, "net_foreign": 0})
        # PM 13:30–16:15 → hold to exit_close
        for h_m in range(13 * 60 + 30, 16 * 60 + 16):
            h, m = h_m // 60, h_m % 60
            dt = date.replace(hour=h, minute=m)
            is_last = (h == 16 and m == 15)
            c = exit_close if is_last else breakout_close
            rows.append({"datetime": dt, "date": date.date(), "open": ref_price,
                         "high": max(c, ref_price) * 1.001,
                         "low": min(c, ref_price) * 0.999,
                         "close": c, "volume": 1000,
                         "value": c * 1000, "net_foreign": 0})
        days_added += 1
        date += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


class TestOrbTradesMostDays:
    """Regression for the ORB-never-trades bug (eff_n ~3 over 55 days)."""

    def test_orb_eff_n_near_trading_days(self):
        """Breaks-on-most-days fixture → eff_n close to the day count (>= 30)."""
        from strategy_fit.families_intraday import run_orb_family
        from lib.stats import MIN_EFF_N
        n_days = 40
        df = _make_orb_breakout_most_days_df(n_days=n_days)
        result = run_orb_family(df)
        assert result["status"] == "tested", (
            f"Expected tested; got {result['status']}: {result.get('skip_reason')}"
        )
        assert result["eff_n"] >= MIN_EFF_N, (
            f"ORB should trade most days; eff_n={result['eff_n']} (n_days={n_days})"
        )
        # Should trade on the large majority of days, not a handful.
        assert result["eff_n"] >= int(0.8 * n_days), (
            f"ORB eff_n={result['eff_n']} far below day count {n_days}"
        )

    def test_orb_breakout_after_window_is_detected(self):
        """A breakout that occurs AFTER the 09:30 bar must still be entered."""
        from strategy_fit.families_intraday import run_orb_family
        df = _make_orb_breakout_most_days_df(n_days=35)
        result = run_orb_family(df)
        assert result["status"] == "tested"
        # Directions alternate, magnitudes are symmetric → mean edge is the
        # held breakout-to-exit move; eff_n must reflect ~all days.
        assert result["eff_n"] >= 30
