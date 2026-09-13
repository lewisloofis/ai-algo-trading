"""
screener/test_universe_snapshot.py — point-in-time eligibility snapshots.

The funding endpoints report what is tradeable *now*: there is no history and
``effective_date`` comes back null, so the point-in-time universe cannot be
reconstructed — only recorded going forward (ADR-0015, plan 18 A-3). Every day
without a snapshot is a day of universe history that is permanently gone, which
makes correctness here worth more than convenience.
"""
from __future__ import annotations

import json

import pytest

from screener.universe_snapshot import (
    SCHEMA_VERSION,
    EmptySnapshotError,
    build_snapshot,
    s3_key_for,
)


class _Source:
    def __init__(self, day_trade: set[str], trading_limit: set[str]) -> None:
        self._dt, self._tl = day_trade, trading_limit

    def fetch_day_trade(self) -> set[str]:
        return self._dt

    def fetch_trading_limit(self) -> set[str]:
        return self._tl


class TestBuildSnapshot:
    def test_records_both_legs_and_their_intersection(self) -> None:
        src = _Source({"AALI", "BBCA", "PTRO"}, {"BBCA", "PTRO", "TLKM"})
        snap = build_snapshot(src, as_of="2026-09-13")
        assert snap["eligible"] == ["BBCA", "PTRO"]
        assert snap["day_trade"] == ["AALI", "BBCA", "PTRO"]
        assert snap["trading_limit"] == ["BBCA", "PTRO", "TLKM"]
        assert snap["counts"] == {"day_trade": 3, "trading_limit": 3, "eligible": 2}

    def test_keeps_both_legs_not_just_the_intersection(self) -> None:
        """
        The legs are the evidence. A name can leave the eligible set by falling
        off either list, and which one it was is not recoverable from the
        intersection alone.
        """
        snap = build_snapshot(_Source({"A", "B"}, {"B", "C"}), as_of="2026-09-13")
        assert set(snap) >= {"day_trade", "trading_limit", "eligible"}

    def test_symbols_are_sorted_so_snapshots_diff_cleanly(self) -> None:
        snap = build_snapshot(_Source({"ZZZZ", "AAAA"}, {"ZZZZ", "AAAA"}), as_of="2026-09-13")
        assert snap["eligible"] == ["AAAA", "ZZZZ"]

    def test_carries_schema_version_and_provenance(self) -> None:
        snap = build_snapshot(_Source({"A"}, {"A"}), as_of="2026-09-13")
        assert snap["schema_version"] == SCHEMA_VERSION
        assert snap["as_of"] == "2026-09-13"
        assert "1000004658" in json.dumps(snap)
        assert "1000005139" in json.dumps(snap)

    def test_empty_leg_aborts_rather_than_recording_a_false_universe(self) -> None:
        """
        An expired token makes the endpoints 401 and _fetch_subsector_symbols
        returns an empty set rather than raising. Writing that would record
        "nothing was tradeable today" as fact, and the day cannot be re-recorded.
        """
        with pytest.raises(EmptySnapshotError, match="day_trade"):
            build_snapshot(_Source(set(), {"A"}), as_of="2026-09-13")
        with pytest.raises(EmptySnapshotError, match="trading_limit"):
            build_snapshot(_Source({"A"}, set()), as_of="2026-09-13")

    def test_empty_intersection_aborts_too(self) -> None:
        with pytest.raises(EmptySnapshotError, match="intersection"):
            build_snapshot(_Source({"A"}, {"B"}), as_of="2026-09-13")


class TestS3Key:
    def test_key_is_date_partitioned_like_the_market_data(self) -> None:
        assert s3_key_for("2026-09-13") == "universe/dt=2026-09-13/eligibility.json"

    def test_key_rejects_a_malformed_date(self) -> None:
        with pytest.raises(ValueError):
            s3_key_for("13-09-2026")
