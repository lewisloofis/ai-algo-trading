"""
test_backtest.py — Unit tests for backtest.py fill simulator.

Run with: python test_backtest.py  (no pytest required)
"""

from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd

from backtest import (
    Trade,
    leg_cost,
    _long_pnl,
    _passive_entry_fill,
    FEE,
    LOT_SHARES,
    TARGET_LOTS,
)


# ---------------------------------------------------------------------------
# Test 1: Fee matches reference worked example
# ---------------------------------------------------------------------------

def test_fee_matches_reference_worked_example() -> None:
    """
    Reference: sell 2113 lots @ 2000
    shares = 2113 * 100 = 211,300
    value  = 211,300 * 2000 = 422,600,000 IDR
    sell cost = 0.0025 * 422,600,000 = 1,056,500
    buy  cost = 0.0015 * 422,600,000 =   633,900
    round-trip = 1,690,400
    """
    value = 422_600_000

    sell_cost = leg_cost(value, "sell")
    buy_cost = leg_cost(value, "buy")
    round_trip = sell_cost + buy_cost

    assert int(round(sell_cost)) == 1_056_500, (
        f"sell cost: expected 1_056_500, got {sell_cost}"
    )
    assert int(round(buy_cost)) == 633_900, (
        f"buy cost: expected 633_900, got {buy_cost}"
    )
    assert int(round(round_trip)) == 1_690_400, (
        f"round-trip: expected 1_690_400, got {round_trip}"
    )


# ---------------------------------------------------------------------------
# Test 2: Taker trade PnL hand-checked
# ---------------------------------------------------------------------------

def test_taker_trade_pnl_hand_checked() -> None:
    """
    Buy 10,000 shares at ask=200, exit at bid=202.
    gross       = (202-200) * 10,000 = 20,000
    entry_value = 200 * 10,000 = 2,000,000
    fee_buy     = 0.0015 * 2,000,000 = 3,000
    exit_value  = 202 * 10,000 = 2,020,000
    fee_sell    = 0.0025 * 2,020,000 = 5,050
    fee_total   = 8,050
    net         = 20,000 - 8,050 = 11,950
    net_bps     = 11,950 / 2,000,000 * 10,000 = 59.75
    """
    entry_px = 200.0
    exit_px = 202.0
    shares = 10_000

    gross, fee, net, net_bps = _long_pnl(entry_px, exit_px, shares)

    assert gross == 20_000.0, f"gross: expected 20000, got {gross}"
    assert fee == 8_050.0, f"fee: expected 8050, got {fee}"
    assert net == 11_950.0, f"net: expected 11950, got {net}"
    assert abs(net_bps - 59.75) < 1e-6, f"net_bps: expected 59.75, got {net_bps}"

    # Build a Trade object and verify fields
    entry_value = entry_px * shares
    exit_value = exit_px * shares
    fee_buy = leg_cost(entry_value, "buy")
    fee_sell = leg_cost(exit_value, "sell")
    total_fee = fee_buy + fee_sell

    assert fee_buy == 3_000.0, f"fee_buy: expected 3000, got {fee_buy}"
    assert fee_sell == 5_050.0, f"fee_sell: expected 5050, got {fee_sell}"

    t = Trade(
        entry_ns=1_000_000_000,
        exit_ns=6_000_000_000,
        entry_px=entry_px,
        exit_px=exit_px,
        shares=shares,
        side="long",
        gross_pnl=gross,
        fee=total_fee,
        net_pnl=net,
        net_bps=net_bps,
        forced_exit=False,
    )
    assert t.gross_pnl == 20_000.0
    assert t.fee == 8_050.0
    assert t.net_pnl == 11_950.0
    assert abs(t.net_bps - 59.75) < 1e-6


# ---------------------------------------------------------------------------
# Test 3: Loss when move too small
# ---------------------------------------------------------------------------

def test_loss_when_move_too_small() -> None:
    """
    Buy at ask=169, exit at bid=169 (flat move).
    Gross PnL = 0, but fees eat the position → net_bps < 0.
    """
    entry_px = 169.0
    exit_px = 169.0
    shares = 10_000

    gross, fee, net, net_bps = _long_pnl(entry_px, exit_px, shares)

    assert gross == 0.0, f"gross should be 0 for flat move, got {gross}"
    assert fee > 0, f"fee should be positive, got {fee}"
    assert net < 0, f"net should be negative (fee drag), got {net}"
    assert net_bps < 0, f"net_bps should be negative when move is too small, got {net_bps}"


# ---------------------------------------------------------------------------
# Test 4: Passive entry fills after queue clears
# ---------------------------------------------------------------------------

def test_passive_entry_fills_after_queue_clears() -> None:
    """
    Resting buy at bid=100, queue_ahead=500 shares.
    Trades tape:
        trade 1: S@100 vol=200  → cumvol=200, not filled
        trade 2: S@100 vol=200  → cumvol=400, not filled
        trade 3: S@100 vol=200  → cumvol=600 > 500, FILLED at trade 3 ts
    Also verify B-side trades and price > bid do NOT count.
    """
    base_ns = 1_000_000_000

    trades_ns = np.array([
        base_ns + 100,   # trade 1: S@100 vol=200
        base_ns + 200,   # trade 2: S@100 vol=200
        base_ns + 300,   # trade 3: S@100 vol=200  → fills here
        base_ns + 400,   # trade 4: B@100 vol=999  → should NOT count (wrong side)
        base_ns + 500,   # trade 5: S@101 vol=999  → should NOT count (price > bid)
    ], dtype=np.int64)

    trades_price = np.array([100.0, 100.0, 100.0, 100.0, 101.0])
    trades_vol   = np.array([200.0, 200.0, 200.0, 999.0, 999.0])
    trades_side  = np.array(["S",   "S",   "S",   "B",   "S"])

    bid_px = 100.0
    queue_ahead = 500.0
    start_ns = base_ns
    end_ns = base_ns + 10_000

    result = _passive_entry_fill(
        trades_ns, trades_price, trades_vol, trades_side,
        start_ns=start_ns,
        end_ns=end_ns,
        bid_px=bid_px,
        queue_ahead=queue_ahead,
    )

    assert result is not None, "Expected a fill, got None"
    fill_ns, fill_idx = result

    # Should fill at trade 3 (index 2)
    assert fill_idx == 2, f"Expected fill at trade index 2, got {fill_idx}"
    assert fill_ns == base_ns + 300, (
        f"Expected fill_ns={base_ns + 300}, got {fill_ns}"
    )

    # Verify that without trade 3, no fill occurs (queue_ahead=600 → needs >600)
    result_no_fill = _passive_entry_fill(
        trades_ns, trades_price, trades_vol, trades_side,
        start_ns=start_ns,
        end_ns=end_ns,
        bid_px=bid_px,
        queue_ahead=600.0,  # requires > 600, only 600 available from S@100
    )
    assert result_no_fill is None, (
        "Expected no fill with queue_ahead=600 (cumvol only reaches 600, need > 600), "
        f"got {result_no_fill}"
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    all_tests = [
        test_fee_matches_reference_worked_example,
        test_taker_trade_pnl_hand_checked,
        test_loss_when_move_too_small,
        test_passive_entry_fills_after_queue_clears,
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
