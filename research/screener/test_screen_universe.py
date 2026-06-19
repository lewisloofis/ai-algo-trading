"""
test_screen_universe.py — Unit tests for the rank_universe pure function.

Builds hand-crafted SymbolMetrics records and asserts the ranking output.
No file I/O, no live network calls.

Run with:  python screener/test_screen_universe.py   (cwd = research/)
Also pytest-discoverable (all test functions named test_* with asserts).
"""

from __future__ import annotations

import sys

from screener.screen_universe import (
    EDGE_CAP_THRESHOLD_BPS,
    TOO_THIN_TURNOVER_IDR,
    SymbolMetrics,
    TAG_EDGE_CAPPED,
    TAG_TOO_THIN,
    TAG_TRADEABLE,
    rank_universe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make(
    symbol: str,
    price: float,
    turnover: float,
    vol_bps: float = 200.0,
    fundable: bool = True,
) -> SymbolMetrics:
    """
    Convenience builder for SymbolMetrics.

    tick_sz and min_spread_bps_val are derived from price using the same
    functions as production code (imported via screener.metrics).
    """
    from screener.metrics import min_spread_bps, tick_size as ts
    return SymbolMetrics(
        symbol=symbol,
        latest_price=price,
        tick_sz=ts(price),
        min_spread_bps_val=min_spread_bps(price),
        median_daily_turnover_idr=turnover,
        realised_vol_bps_val=vol_bps,
        fundable=fundable,
    )


# ---------------------------------------------------------------------------
# Test: non-fundable symbols are excluded entirely
# ---------------------------------------------------------------------------

def test_non_fundable_excluded() -> None:
    """
    A non-fundable symbol must not appear in tradeable or dropped with a
    structural reason — it is excluded with reason NOT-FUNDABLE.
    """
    records = [
        _make("GHOST", price=5000.0, turnover=50_000_000_000.0, fundable=False),
        _make("ITMG", price=27500.0, turnover=150_000_000_000.0, fundable=True),
    ]
    result = rank_universe(records)

    tradeable_symbols = [r.symbol for r in result.tradeable]
    dropped_symbols = [d.symbol for d in result.dropped]
    dropped_reasons = {d.symbol: d.reason for d in result.dropped}

    assert "GHOST" not in tradeable_symbols, "Non-fundable GHOST should not be tradeable"
    assert "GHOST" in dropped_symbols, "Non-fundable GHOST should appear in dropped"
    assert dropped_reasons["GHOST"] == "NOT-FUNDABLE", (
        f"Expected reason NOT-FUNDABLE, got {dropped_reasons['GHOST']}"
    )
    # ITMG at 27500 IDR should be tradeable
    assert "ITMG" in tradeable_symbols, "ITMG should be tradeable"


# ---------------------------------------------------------------------------
# Test: BUMI is EDGE-CAPPED
# ---------------------------------------------------------------------------

def test_bumi_edge_capped() -> None:
    """
    BUMI at 168 IDR: min_spread_bps ≈ 59.5 > 40 bps gate → EDGE-CAPPED.
    """
    records = [
        _make("BUMI", price=168.0, turnover=170_000_000_000.0),  # high turnover, still capped
    ]
    result = rank_universe(records)

    assert not result.tradeable, "BUMI should not be in tradeable list"
    assert len(result.dropped) == 1
    assert result.dropped[0].symbol == "BUMI"
    assert result.dropped[0].reason == TAG_EDGE_CAPPED, (
        f"Expected EDGE-CAPPED, got {result.dropped[0].reason}"
    )


# ---------------------------------------------------------------------------
# Test: TOO-THIN symbol
# ---------------------------------------------------------------------------

def test_thin_name_flagged_too_thin() -> None:
    """
    A name with turnover well below the floor is TOO-THIN.
    Price is set high enough to avoid edge-cap (price=3000 → tick=10, spread≈33 bps).
    """
    records = [
        _make("THIN", price=3000.0, turnover=100_000_000.0),  # 100 M IDR << 5 B floor
    ]
    result = rank_universe(records)

    assert not result.tradeable, "THIN should not be in tradeable list"
    assert len(result.dropped) == 1
    assert result.dropped[0].reason == TAG_TOO_THIN, (
        f"Expected TOO-THIN, got {result.dropped[0].reason}"
    )


# ---------------------------------------------------------------------------
# Test: TRADEABLE name ranked first is the highest-turnover name
# ---------------------------------------------------------------------------

def test_tradeable_ranked_by_turnover_descending() -> None:
    """
    Three tradeable names at high prices (above gate), different turnovers.
    The highest-turnover name must be rank 1.

    ITMG  (27500 IDR, tick=25, spread≈9.1 bps, turnover=150 B) → rank 1
    MDKA  ( 2800 IDR, tick=10, spread≈35.7 bps, turnover=120 B) → rank 2
    ANTM  ( 1600 IDR, tick= 5, spread≈31.3 bps, turnover= 35 B) → rank 3
    """
    records = [
        _make("ANTM",  price=1600.0,  turnover=35_000_000_000.0),
        _make("MDKA",  price=2800.0,  turnover=120_000_000_000.0),
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0),
    ]
    result = rank_universe(records)

    assert len(result.tradeable) == 3, f"Expected 3 tradeable, got {len(result.tradeable)}"
    assert result.tradeable[0].symbol == "ITMG", (
        f"Expected ITMG at rank 1, got {result.tradeable[0].symbol}"
    )
    assert result.tradeable[1].symbol == "MDKA", (
        f"Expected MDKA at rank 2, got {result.tradeable[1].symbol}"
    )
    assert result.tradeable[2].symbol == "ANTM", (
        f"Expected ANTM at rank 3, got {result.tradeable[2].symbol}"
    )
    # Ranks must be 1, 2, 3
    assert [r.rank for r in result.tradeable] == [1, 2, 3], (
        f"Expected ranks [1,2,3], got {[r.rank for r in result.tradeable]}"
    )
    # All tagged TRADEABLE
    for r in result.tradeable:
        assert r.tag == TAG_TRADEABLE, f"{r.symbol}: expected TRADEABLE tag, got {r.tag}"


# ---------------------------------------------------------------------------
# Test: edge-cap wins over too-thin when both conditions hold
# ---------------------------------------------------------------------------

def test_edge_cap_wins_when_also_thin() -> None:
    """
    A symbol that is both edge-capped (low price) and thin (low turnover)
    should be flagged EDGE-CAPPED, not TOO-THIN.
    """
    # BUMI at 168: edge-capped AND thin
    records = [
        _make("BUMI", price=168.0, turnover=50_000_000.0),  # thin too
    ]
    result = rank_universe(records)

    assert result.dropped[0].reason == TAG_EDGE_CAPPED, (
        f"Expected EDGE-CAPPED to win over TOO-THIN, got {result.dropped[0].reason}"
    )


# ---------------------------------------------------------------------------
# Test: mixed bag — ITMG tradeable + BUMI edge-capped + INDY thin
# ---------------------------------------------------------------------------

def test_mixed_universe() -> None:
    """
    Full scenario matching the fixture data:
      ITMG  27500 IDR — TRADEABLE (rank 1 by turnover)
      MDKA   2800 IDR — TRADEABLE (rank 2)
      ANTM   1600 IDR — TRADEABLE (rank 3)
      INDY   1400 IDR — TOO-THIN  (turnover below floor)
      BUMI    168 IDR — EDGE-CAPPED
      DEWA     52 IDR — EDGE-CAPPED
    """
    records = [
        _make("BUMI",  price=168.0,   turnover=170_000_000_000.0),
        _make("DEWA",  price=52.0,    turnover=27_000_000_000.0),
        _make("INDY",  price=1400.0,  turnover=1_100_000_000.0),   # < 5 B floor
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0),
        _make("MDKA",  price=2800.0,  turnover=117_000_000_000.0),
        _make("ANTM",  price=1600.0,  turnover=35_000_000_000.0),
    ]
    result = rank_universe(records)

    tradeable_symbols = [r.symbol for r in result.tradeable]
    dropped_reasons = {d.symbol: d.reason for d in result.dropped}

    # Tradeable
    assert "ITMG" in tradeable_symbols
    assert "MDKA" in tradeable_symbols
    assert "ANTM" in tradeable_symbols
    assert result.tradeable[0].symbol == "ITMG", "ITMG should be rank 1"

    # Dropped
    assert dropped_reasons.get("BUMI") == TAG_EDGE_CAPPED
    assert dropped_reasons.get("DEWA") == TAG_EDGE_CAPPED
    assert dropped_reasons.get("INDY") == TAG_TOO_THIN


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    all_tests = [
        test_non_fundable_excluded,
        test_bumi_edge_capped,
        test_thin_name_flagged_too_thin,
        test_tradeable_ranked_by_turnover_descending,
        test_edge_cap_wins_when_also_thin,
        test_mixed_universe,
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
