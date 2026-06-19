"""
test_metrics.py — Unit tests for screener/metrics.py.

All numbers are hand-worked; see inline comments.
Run with:  python screener/test_metrics.py   (cwd = research/)
Also pytest-discoverable (all test functions named test_* with asserts).
"""

from __future__ import annotations

import math
import sys

import pandas as pd

from screener.metrics import (
    impact_bps,
    max_position_for_budget,
    median_daily_turnover,
    min_spread_bps,
    realised_vol_bps,
    tick_size,
)

# FEE is the single source of truth for fee constants.
from backtest.backtest import FEE

# Round-trip fee gate in bps: buy 15 bps + sell 25 bps = 40 bps.
ROUND_TRIP_BPS = (FEE["buy"] + FEE["sell"]) * 1e4  # 40.0


# ---------------------------------------------------------------------------
# Test: BUMI structural edge-cap
# ---------------------------------------------------------------------------

def test_bumi_tick_and_min_spread() -> None:
    """
    BUMI @ ~168 IDR: tick = 1 IDR (price < 200).

    min_spread_bps = 1/168 * 1e4 ≈ 59.52 bps
    This exceeds the 40 bps round-trip gate → structurally edge-capped.
    """
    price = 168.0

    # Tick size
    assert tick_size(price) == 1, (
        f"Expected tick_size(168) == 1, got {tick_size(price)}"
    )

    # min_spread_bps ≈ 59.52
    ms = min_spread_bps(price)
    expected = 1 / 168 * 1e4  # 59.5238...
    assert abs(ms - expected) < 0.1, (
        f"Expected min_spread_bps(168) ≈ {expected:.4f}, got {ms:.4f}"
    )

    # Exceeds 40 bps fee gate
    assert ms > ROUND_TRIP_BPS, (
        f"Expected min_spread_bps({price})={ms:.2f} > fee gate={ROUND_TRIP_BPS}, "
        "but it does not — BUMI should be structurally edge-capped"
    )

    # Confirm the fee gate constant is exactly 40.0
    assert ROUND_TRIP_BPS == 40.0, (
        f"Expected ROUND_TRIP_BPS == 40.0 from FEE constants, got {ROUND_TRIP_BPS}"
    )


# ---------------------------------------------------------------------------
# Test: tick ladder boundaries
# ---------------------------------------------------------------------------

def test_tick_ladder_boundaries() -> None:
    """
    Verify every band boundary and the first price in each new band.

    Band edges (exclusive upper bound):
        price < 200   → 1
        price < 500   → 2
        price < 2000  → 5
        price < 5000  → 10
        else          → 25
    """
    cases = [
        # (price, expected_tick)
        (199.0, 1),   # last price in <200 band
        (200.0, 2),   # first price in 200-499 band
        (499.0, 2),   # last price in 200-499 band
        (500.0, 5),   # first price in 500-1999 band
        (1999.0, 5),  # last price in 500-1999 band
        (2000.0, 10), # first price in 2000-4999 band
        (4999.0, 10), # last price in 2000-4999 band
        (5000.0, 25), # first price in ≥5000 band
    ]
    for price, expected in cases:
        got = tick_size(price)
        assert got == expected, (
            f"tick_size({price}): expected {expected}, got {got}"
        )


# ---------------------------------------------------------------------------
# Test: min_spread_bps sanity — high price → smaller bps floor
# ---------------------------------------------------------------------------

def test_min_spread_bps_decreases_with_price() -> None:
    """
    A stock at 5000 IDR (tick=25) has a much smaller min_spread_bps
    than BUMI at 168 (tick=1).

    5000 IDR: 25/5000 * 1e4 = 50 bps
    168  IDR:  1/168  * 1e4 ≈ 59.5 bps

    Structural edge-cap is less severe for higher-priced stocks,
    though 50 bps is still above the 40 bps fee gate.
    To find a price well below the gate, try 1000 IDR (tick=5):
    5/1000 * 1e4 = 50 bps.  Try 5000+ where tick stays 25:
    at 10000 IDR: 25/10000*1e4 = 25 bps < 40 bps → not edge-capped.
    """
    # High-price example that clears the fee gate
    high_price = 10_000.0  # tick = 25
    ms_high = min_spread_bps(high_price)
    ms_bumi = min_spread_bps(168.0)

    assert ms_high < ms_bumi, (
        f"Expected min_spread_bps(10000)={ms_high:.2f} < "
        f"min_spread_bps(168)={ms_bumi:.2f}"
    )

    # 10000 IDR should be below the fee gate (not edge-capped)
    assert ms_high < ROUND_TRIP_BPS, (
        f"Expected min_spread_bps(10000)={ms_high:.2f} < "
        f"fee gate={ROUND_TRIP_BPS} (should not be edge-capped)"
    )


# ---------------------------------------------------------------------------
# Test: median_daily_turnover on known data
# ---------------------------------------------------------------------------

def test_median_daily_turnover_known_values() -> None:
    """
    Hand-built 5-row DataFrame.

    Row values (close * volume):
        100 * 1000 = 100_000
        200 * 500  = 100_000
        150 * 2000 = 300_000
        120 * 1000 = 120_000
        180 *  500 =  90_000

    Sorted: 90_000, 100_000, 100_000, 120_000, 300_000
    Median = 100_000.
    """
    df = pd.DataFrame({
        "close":  [100.0, 200.0, 150.0, 120.0, 180.0],
        "volume": [1000,   500,  2000,  1000,   500],
    })
    result = median_daily_turnover(df)
    assert result == 100_000.0, (
        f"Expected median_daily_turnover = 100_000.0, got {result}"
    )


def test_median_daily_turnover_uses_value_column_when_present() -> None:
    """
    When a ``value`` column is present it takes precedence over close*volume.

    Values: [50_000, 200_000, 150_000]  → sorted: 50k, 150k, 200k → median = 150_000.
    """
    df = pd.DataFrame({
        "close":  [100.0, 200.0, 150.0],
        "volume": [1, 1, 1],        # close*volume would be 100/200/150 — wrong
        "value":  [50_000, 200_000, 150_000],
    })
    result = median_daily_turnover(df)
    assert result == 150_000.0, (
        f"Expected median_daily_turnover (using value col) = 150_000.0, got {result}"
    )


# ---------------------------------------------------------------------------
# Test: realised_vol_bps on known data
# ---------------------------------------------------------------------------

def test_realised_vol_bps_known_values() -> None:
    """
    Three-bar series: closes = [100, 110, 99].

    log returns:
        r1 = ln(110/100) = ln(1.1) ≈ 0.09531
        r2 = ln(99/110)  = ln(0.9) ≈ -0.10536

    mean = (0.09531 + (-0.10536)) / 2 = -0.00503
    std  = std([0.09531, -0.10536], ddof=1)
         = sqrt(((0.09531 - (-0.00503))^2 + (-0.10536 - (-0.00503))^2) / 1)

    Let d1 = 0.09531 + 0.00503 = 0.10034
        d2 = -0.10536 + 0.00503 = -0.10033
    std = sqrt((0.10034^2 + 0.10033^2) / 1) ≈ sqrt(0.020136) ≈ 0.141903

    In bps: 0.141903 * 1e4 ≈ 1419.03
    """
    df = pd.DataFrame({"close": [100.0, 110.0, 99.0]})
    result = realised_vol_bps(df)

    r1 = math.log(110.0 / 100.0)
    r2 = math.log(99.0 / 110.0)
    import statistics
    expected_bps = statistics.stdev([r1, r2]) * 1e4

    assert abs(result - expected_bps) < 0.01, (
        f"Expected realised_vol_bps ≈ {expected_bps:.4f}, got {result:.4f}"
    )


# ---------------------------------------------------------------------------
# Test: impact_bps round-trip with max_position_for_budget
# ---------------------------------------------------------------------------

def test_impact_round_trip() -> None:
    """
    Round-trip: max_position_for_budget → impact_bps should recover budget_bps.

    Setup:
        adv           = 1_000_000  (IDR or shares — units just need to match)
        sigma_bps     = 200.0
        budget_bps    = 10.0
        half_spread   = 2.0
        y             = 1.0

    q* = adv * ((budget - hs) / (y * sigma))^2
       = 1e6 * ((10 - 2) / (1.0 * 200))^2
       = 1e6 * (8/200)^2
       = 1e6 * 0.04^2
       = 1e6 * 0.0016
       = 1600

    impact_bps(1600, 1e6, 200, 2) = 2 + 1 * 200 * sqrt(1600/1e6)
                                   = 2 + 200 * sqrt(0.0016)
                                   = 2 + 200 * 0.04
                                   = 2 + 8 = 10.0  ✓
    """
    adv = 1_000_000.0
    sigma_bps = 200.0
    budget_bps = 10.0
    half_spread = 2.0

    q_star = max_position_for_budget(adv, sigma_bps, budget_bps, half_spread)
    recovered = impact_bps(q_star, adv, sigma_bps, half_spread)

    assert abs(recovered - budget_bps) < 1e-9, (
        f"Round-trip failed: impact_bps(q*)={recovered:.6f} != budget_bps={budget_bps}"
    )


def test_max_position_zero_when_budget_leq_half_spread() -> None:
    """
    When budget_bps <= half_spread_bps no position is viable.

    budget = 5.0, half_spread = 5.0 → exactly at boundary → 0.0
    budget = 3.0, half_spread = 5.0 → below boundary → 0.0
    """
    adv = 1_000_000.0
    sigma_bps = 200.0

    assert max_position_for_budget(adv, sigma_bps, 5.0, 5.0) == 0.0, (
        "Expected 0.0 when budget == half_spread"
    )
    assert max_position_for_budget(adv, sigma_bps, 3.0, 5.0) == 0.0, (
        "Expected 0.0 when budget < half_spread"
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    all_tests = [
        test_bumi_tick_and_min_spread,
        test_tick_ladder_boundaries,
        test_min_spread_bps_decreases_with_price,
        test_median_daily_turnover_known_values,
        test_median_daily_turnover_uses_value_column_when_present,
        test_realised_vol_bps_known_values,
        test_impact_round_trip,
        test_max_position_zero_when_budget_leq_half_spread,
    ]
    passed = 0
    failed = 0
    for t in all_tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
