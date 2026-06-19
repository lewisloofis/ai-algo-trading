"""
test_screen_universe.py — Unit tests for the rank_universe pure function.

Builds hand-crafted SymbolMetrics records and asserts the characterizer output.
No file I/O, no live network calls.

Run with:  python screener/test_screen_universe.py   (cwd = research/)
Also pytest-discoverable (all test functions named test_* with asserts).
"""

from __future__ import annotations

import sys

import pytest

from screener.screen_universe import (
    EDGE_CAP_THRESHOLD_BPS,
    ROUND_TRIP_BPS,
    TOO_THIN_TURNOVER_IDR,
    SymbolMetrics,
    TAG_EDGE_CAPPED,
    TAG_TOO_THIN,
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
# Test: all fundable names appear in .ranked; none dropped for edge-cap/thin
# ---------------------------------------------------------------------------

def test_all_fundable_names_in_ranked() -> None:
    """
    Every fundable symbol — regardless of edge-cap or thinness — must appear
    in .ranked.  None are dropped for structural reasons.
    """
    records = [
        _make("BUMI",  price=168.0,   turnover=170_000_000_000.0),  # edge-capped
        _make("THIN",  price=3000.0,  turnover=100_000_000.0),      # thin
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0),  # clean
    ]
    result = rank_universe(records)

    ranked_symbols = [r.symbol for r in result.ranked]
    assert "BUMI" in ranked_symbols, "BUMI (edge-capped) must still appear in ranked"
    assert "THIN" in ranked_symbols, "THIN (thin) must still appear in ranked"
    assert "ITMG" in ranked_symbols, "ITMG must appear in ranked"
    assert result.dropped == [], "No fundable names should be dropped"


# ---------------------------------------------------------------------------
# Test: BUMI @168 — in .ranked, is_edge_capped, correct min_edge_to_clear
# ---------------------------------------------------------------------------

def test_bumi_edge_capped_annotation() -> None:
    """
    BUMI at 168 IDR: min_spread_bps ≈ 59.5 > 40 bps gate → is_edge_capped is True.
    Must still appear in .ranked with correct min_edge_to_clear_bps.
    """
    from screener.metrics import min_spread_bps
    records = [
        _make("BUMI", price=168.0, turnover=170_000_000_000.0),
    ]
    result = rank_universe(records)

    assert len(result.ranked) == 1, "BUMI must be in ranked"
    r = result.ranked[0]
    assert r.symbol == "BUMI"
    assert r.is_edge_capped is True, "BUMI at 168 should be edge-capped"
    assert r.min_edge_to_clear_bps == pytest.approx(
        min_spread_bps(168.0) + ROUND_TRIP_BPS
    ), f"min_edge_to_clear_bps mismatch: {r.min_edge_to_clear_bps}"
    assert result.dropped == [], "No dropped names expected"


# ---------------------------------------------------------------------------
# Test: thin high-priced name — in .ranked, is_thin, not is_edge_capped
# ---------------------------------------------------------------------------

def test_thin_high_price_annotation() -> None:
    """
    A thin high-priced name (price=3000, turnover=100_000_000):
    price=3000 → tick=10 → min_spread≈33 bps (below 40 bps gate, so NOT edge-capped).
    turnover=100 M IDR << 5 B floor → is_thin is True.
    Must still appear in .ranked.
    """
    records = [
        _make("THIN", price=3000.0, turnover=100_000_000.0),
    ]
    result = rank_universe(records)

    assert len(result.ranked) == 1, "THIN must be in ranked"
    r = result.ranked[0]
    assert r.is_thin is True, "THIN should be flagged thin"
    assert r.is_edge_capped is False, "THIN at 3000 should not be edge-capped"
    assert result.dropped == [], "No dropped names expected"


# ---------------------------------------------------------------------------
# Test: high-price liquid name — not edge-capped, not thin
# ---------------------------------------------------------------------------

def test_high_price_liquid_clean_flags() -> None:
    """
    ITMG at 27500, turnover=150 B:
    min_spread ≈ 9.1 bps → is_edge_capped is False.
    turnover >> 5 B floor → is_thin is False.
    """
    records = [
        _make("ITMG", price=27500.0, turnover=150_000_000_000.0),
    ]
    result = rank_universe(records)

    assert len(result.ranked) == 1
    r = result.ranked[0]
    assert r.is_edge_capped is False, "ITMG at 27500 should not be edge-capped"
    assert r.is_thin is False, "ITMG at 150 B IDR should not be thin"


# ---------------------------------------------------------------------------
# Test: ranking by turnover descending holds (contiguous ranks 1..N)
# ---------------------------------------------------------------------------

def test_ranked_by_turnover_descending() -> None:
    """
    Three fundable names at different turnovers — rank order is turnover desc.
    Ranks must be contiguous 1..N.

    ITMG  (27500 IDR, turnover=150 B) → rank 1
    MDKA  ( 2800 IDR, turnover=120 B) → rank 2
    ANTM  ( 1600 IDR, turnover= 35 B) → rank 3
    """
    records = [
        _make("ANTM",  price=1600.0,  turnover=35_000_000_000.0),
        _make("MDKA",  price=2800.0,  turnover=120_000_000_000.0),
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0),
    ]
    result = rank_universe(records)

    assert len(result.ranked) == 3, f"Expected 3 ranked, got {len(result.ranked)}"
    assert result.ranked[0].symbol == "ITMG", (
        f"Expected ITMG at rank 1, got {result.ranked[0].symbol}"
    )
    assert result.ranked[1].symbol == "MDKA", (
        f"Expected MDKA at rank 2, got {result.ranked[1].symbol}"
    )
    assert result.ranked[2].symbol == "ANTM", (
        f"Expected ANTM at rank 3, got {result.ranked[2].symbol}"
    )
    assert [r.rank for r in result.ranked] == [1, 2, 3], (
        f"Expected contiguous ranks [1,2,3], got {[r.rank for r in result.ranked]}"
    )


# ---------------------------------------------------------------------------
# Test: non-fundable symbol excluded with reason NOT-FUNDABLE
# ---------------------------------------------------------------------------

def test_non_fundable_excluded() -> None:
    """
    A non-fundable symbol must not appear in .ranked and must be in .dropped
    with reason NOT-FUNDABLE.
    """
    records = [
        _make("GHOST", price=5000.0, turnover=50_000_000_000.0, fundable=False),
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0, fundable=True),
    ]
    result = rank_universe(records)

    ranked_symbols = [r.symbol for r in result.ranked]
    dropped_reasons = {d.symbol: d.reason for d in result.dropped}

    assert "GHOST" not in ranked_symbols, "Non-fundable GHOST should not be ranked"
    assert "GHOST" in dropped_reasons, "Non-fundable GHOST should appear in dropped"
    assert dropped_reasons["GHOST"] == "NOT-FUNDABLE", (
        f"Expected reason NOT-FUNDABLE, got {dropped_reasons['GHOST']}"
    )
    assert "ITMG" in ranked_symbols, "ITMG should be ranked"


# ---------------------------------------------------------------------------
# Test: min_edge_to_clear_bps == min_spread_bps_val + ROUND_TRIP_BPS for all rows
# ---------------------------------------------------------------------------

def test_min_edge_to_clear_equals_spread_plus_roundtrip() -> None:
    """
    For every ranked symbol, min_edge_to_clear_bps must equal
    min_spread_bps_val + ROUND_TRIP_BPS exactly.
    """
    records = [
        _make("BUMI",  price=168.0,   turnover=170_000_000_000.0),
        _make("DEWA",  price=52.0,    turnover=27_000_000_000.0),
        _make("INDY",  price=1400.0,  turnover=1_100_000_000.0),
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0),
        _make("MDKA",  price=2800.0,  turnover=117_000_000_000.0),
        _make("ANTM",  price=1600.0,  turnover=35_000_000_000.0),
    ]
    result = rank_universe(records)

    for r in result.ranked:
        expected = r.min_spread_bps_val + ROUND_TRIP_BPS
        assert r.min_edge_to_clear_bps == pytest.approx(expected), (
            f"{r.symbol}: min_edge_to_clear_bps={r.min_edge_to_clear_bps} "
            f"!= min_spread_bps_val + ROUND_TRIP_BPS={expected}"
        )


# ---------------------------------------------------------------------------
# Test: full mixed scenario matching fixture data
# ---------------------------------------------------------------------------

def test_mixed_universe_all_fundable_ranked() -> None:
    """
    Full scenario mirroring the fixture intersection (INDY excluded at funding gate,
    so this test models what rank_universe sees: only the intersection members).

    All six names are marked fundable=True here to test rank_universe's behavior
    in isolation from the funding gate.  Edge-cap and thin names must all appear
    in .ranked with correct flags.

    ITMG  27500 IDR — rank 1, not capped, not thin
    BUMI    168 IDR — edge-capped, not thin
    MDKA   2800 IDR — not capped, not thin
    DEWA     52 IDR — edge-capped
    ANTM   1600 IDR — not capped, not thin
    INDY   1400 IDR — not capped, thin (turnover < 5 B)
    """
    records = [
        _make("BUMI",  price=168.0,   turnover=170_000_000_000.0),
        _make("DEWA",  price=52.0,    turnover=27_000_000_000.0),
        _make("INDY",  price=1400.0,  turnover=1_100_000_000.0),
        _make("ITMG",  price=27500.0, turnover=150_000_000_000.0),
        _make("MDKA",  price=2800.0,  turnover=117_000_000_000.0),
        _make("ANTM",  price=1600.0,  turnover=35_000_000_000.0),
    ]
    result = rank_universe(records)

    ranked_symbols = [r.symbol for r in result.ranked]
    ranked_map = {r.symbol: r for r in result.ranked}

    # All fundable names are ranked
    for sym in ["BUMI", "DEWA", "INDY", "ITMG", "MDKA", "ANTM"]:
        assert sym in ranked_symbols, f"{sym} should be in ranked"
    assert result.dropped == [], "No non-fundable names in this batch"

    # Check flags
    assert ranked_map["BUMI"].is_edge_capped is True
    assert ranked_map["DEWA"].is_edge_capped is True
    assert ranked_map["INDY"].is_thin is True
    assert ranked_map["INDY"].is_edge_capped is False
    assert ranked_map["ITMG"].is_edge_capped is False
    assert ranked_map["ITMG"].is_thin is False

    # Rank 1 is the highest-turnover name (BUMI at 170 B)
    assert result.ranked[0].symbol == "BUMI", (
        f"Expected BUMI at rank 1 (170 B turnover), got {result.ranked[0].symbol}"
    )


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    all_tests = [
        test_all_fundable_names_in_ranked,
        test_bumi_edge_capped_annotation,
        test_thin_high_price_annotation,
        test_high_price_liquid_clean_flags,
        test_ranked_by_turnover_descending,
        test_non_fundable_excluded,
        test_min_edge_to_clear_equals_spread_plus_roundtrip,
        test_mixed_universe_all_fundable_ranked,
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
