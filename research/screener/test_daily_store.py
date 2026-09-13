"""
screener/test_daily_store.py — TDD tests for the daily accrual store.

Tests:
  A — snapshot_daily: merge/dedupe, idempotency, accrual across pulls
  B — load_daily: round-trip, missing-file -> None, date-range slicing
  C — DailyAccrualSummary dataclass
  D — corporate-action divergence detection (the core correctness requirement:
      chartbit back-adjusts, so a re-fetch can legitimately rewrite history —
      that must be loud, logged, and recorded, never silent)
  E — coverage_for_symbol / store_coverage: trading-day gaps vs the IDX calendar
  F — CLI entrypoint: snapshot + status subcommands
  G — lib.data_inventory wiring: scan_daily_store() must see this store for free,
      with zero changes to data_inventory.py (it already anticipates this store —
      DEFAULT_DAILY_STORE_DIR, scan_daily_store(), granularity="daily_bar" all
      predate this module; see data_inventory.py's own scan_daily_store docstring)
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from lib.market_calendar import MarketCalendar


# ---------------------------------------------------------------------------
# Helpers — build minimal daily DataFrames matching the DataSource.daily_history
# contract (FixtureDataSource / LiveDataSource both return "date" as a STRING).
# ---------------------------------------------------------------------------

def _make_bars(
    n_days: int = 5,
    ref_price: float = 1000.0,
    start_date: str = "2025-09-01",
    symbol: str = "ITMG",
) -> pd.DataFrame:
    """
    Build a synthetic daily DataFrame matching daily_history()'s output shape:
    date (str), open, high, low, close, volume, value, net_foreign.

    One row per trading day (Mon-Fri), skipping weekends, starting at start_date.
    """
    rows = []
    anchor = pd.Timestamp(start_date)
    days_added = 0
    day_offset = 0
    while days_added < n_days:
        day = anchor + pd.Timedelta(days=day_offset)
        if day.weekday() < 5:
            rows.append(
                {
                    "date": str(day.date()),
                    "open": ref_price,
                    "high": ref_price * 1.01,
                    "low": ref_price * 0.99,
                    "close": ref_price,
                    "volume": 10_000,
                    "value": ref_price * 10_000.0,
                    "net_foreign": 0,
                }
            )
            days_added += 1
        day_offset += 1
    return pd.DataFrame(rows)


def _make_mock_ds(df_map: dict[str, pd.DataFrame]) -> Any:
    """Mock data source that returns DataFrames from df_map by symbol."""
    ds = MagicMock()
    ds.daily_history.side_effect = lambda symbol, **kwargs: df_map.get(symbol, pd.DataFrame())
    return ds


# ---------------------------------------------------------------------------
# Section A: snapshot_daily — merge/dedupe/accrual
# ---------------------------------------------------------------------------

class TestSnapshotDaily:
    def test_first_snapshot_creates_store(self, tmp_path) -> None:
        """First snapshot for a symbol creates the store file."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), f"Expected {len(df)} rows, got {len(result)}"

    def test_second_snapshot_dedupes_overlapping_rows(self, tmp_path) -> None:
        """Re-running with the same DataFrame produces no duplicates."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=10, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), f"Expected {len(df)} rows after dedup, got {len(result)}"

    def test_second_snapshot_merges_new_bars(self, tmp_path) -> None:
        """A new pull with later bars extends history without duplicating old bars."""
        from screener.daily_store import snapshot_daily, load_daily
        df_first = _make_bars(n_days=10, symbol="ITMG", start_date="2025-09-01")
        df_second = _make_bars(n_days=10, symbol="ITMG", start_date="2025-09-10")
        ds1 = _make_mock_ds({"ITMG": df_first})
        ds2 = _make_mock_ds({"ITMG": df_second})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds1)
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds2)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        combined_dates = set(df_first["date"]) | set(df_second["date"])
        assert len(result) == len(combined_dates), (
            f"Expected {len(combined_dates)} merged rows, got {len(result)}"
        )

    def test_result_is_ascending_sorted(self, tmp_path) -> None:
        """Stored bars are sorted ascending by date."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=10, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        dates = result["date"].tolist()
        assert dates == sorted(dates), "Store must be sorted ascending by date"

    def test_idempotent_multiple_runs(self, tmp_path) -> None:
        """Running snapshot 5 times with the same data keeps exactly the original count."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=15, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        for _ in range(5):
            snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), f"Idempotent: expected {len(df)} rows, got {len(result)}"

    def test_multi_symbol_snapshot(self, tmp_path) -> None:
        """snapshot_daily handles multiple symbols independently."""
        from screener.daily_store import snapshot_daily, load_daily
        df_itmg = _make_bars(n_days=3, symbol="ITMG", ref_price=27500.0)
        df_bumi = _make_bars(n_days=4, symbol="BUMI", ref_price=168.0)
        ds = _make_mock_ds({"ITMG": df_itmg, "BUMI": df_bumi})
        snapshot_daily(["ITMG", "BUMI"], store_dir=str(tmp_path), data_source=ds)
        r_itmg = load_daily("ITMG", store_dir=str(tmp_path))
        r_bumi = load_daily("BUMI", store_dir=str(tmp_path))
        assert r_itmg is not None and len(r_itmg) == len(df_itmg)
        assert r_bumi is not None and len(r_bumi) == len(df_bumi)

    def test_snapshot_returns_per_symbol_summary(self, tmp_path) -> None:
        """snapshot_daily returns a dict[symbol, DailyAccrualSummary]."""
        from screener.daily_store import snapshot_daily
        df = _make_bars(n_days=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        summaries = snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        assert "ITMG" in summaries
        s = summaries["ITMG"]
        assert s.rows_before == 0
        assert s.rows_after == len(df)
        assert s.new_rows == len(df)
        assert s.divergences == ()

    def test_empty_live_pull_does_not_corrupt_store(self, tmp_path) -> None:
        """If the live pull returns empty, the existing store is preserved."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=8, symbol="ITMG")
        ds1 = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds1)
        ds_empty = _make_mock_ds({"ITMG": pd.DataFrame()})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds_empty)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), "Empty pull must not truncate the store"

    def test_data_source_exception_does_not_corrupt_store(self, tmp_path) -> None:
        """If data_source.daily_history raises, the existing store is preserved."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=5, symbol="ITMG")
        ds1 = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds1)

        ds_raises = MagicMock()
        ds_raises.daily_history.side_effect = RuntimeError("token expired")
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds_raises)

        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df)


# ---------------------------------------------------------------------------
# Section B: load_daily
# ---------------------------------------------------------------------------

class TestLoadDaily:
    def test_load_missing_returns_none(self, tmp_path) -> None:
        """load_daily returns None when no store file exists for the symbol."""
        from screener.daily_store import load_daily
        result = load_daily("NOSUCHSYM", store_dir=str(tmp_path))
        assert result is None

    def test_round_trip_preserves_columns(self, tmp_path) -> None:
        """Stored and reloaded DataFrame retains the daily_history output columns."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        required = {"date", "open", "high", "low", "close", "volume", "value", "net_foreign"}
        missing = required - set(result.columns)
        assert not missing, f"Missing columns after round-trip: {missing}"

    def test_round_trip_date_column_is_date_type(self, tmp_path) -> None:
        """
        The on-disk "date" column must be a real datetime.date (like
        intraday_store's convention), NOT the string that daily_history()
        returns — so lib.data_inventory.scan_daily_store reads it the same
        way it reads the intraday store.
        """
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=3, symbol="ITMG", start_date="2025-09-01")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert isinstance(result["date"].iloc[0], datetime.date)

    def test_round_trip_preserves_row_count(self, tmp_path) -> None:
        """Row count is unchanged after store->load round trip."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=12, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_daily("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df)

    def test_store_dir_created_if_missing(self, tmp_path) -> None:
        """snapshot_daily creates store_dir if it does not exist."""
        from screener.daily_store import snapshot_daily
        nested_dir = str(tmp_path / "a" / "b" / "c")
        df = _make_bars(n_days=2, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=nested_dir, data_source=ds)
        assert os.path.isdir(nested_dir), "store_dir must be created automatically"

    def test_load_daily_filters_by_date_range(self, tmp_path) -> None:
        """load_daily slices to [start, end] inclusive when given."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=10, symbol="ITMG", start_date="2025-09-01")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        full = load_daily("ITMG", store_dir=str(tmp_path))
        sliced = load_daily("ITMG", store_dir=str(tmp_path), start="2025-09-03", end="2025-09-05")

        assert sliced is not None and len(sliced) < len(full)
        assert all(datetime.date(2025, 9, 3) <= d <= datetime.date(2025, 9, 5) for d in sliced["date"])

    def test_load_daily_open_ended_range(self, tmp_path) -> None:
        """load_daily with only `start` leaves the upper bound open, and vice versa."""
        from screener.daily_store import snapshot_daily, load_daily
        df = _make_bars(n_days=10, symbol="ITMG", start_date="2025-09-01")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        from_start = load_daily("ITMG", store_dir=str(tmp_path), start="2025-09-08")
        up_to = load_daily("ITMG", store_dir=str(tmp_path), end="2025-09-03")

        assert from_start is not None and all(d >= datetime.date(2025, 9, 8) for d in from_start["date"])
        assert up_to is not None and all(d <= datetime.date(2025, 9, 3) for d in up_to["date"])


# ---------------------------------------------------------------------------
# Section C: DailyAccrualSummary dataclass
# ---------------------------------------------------------------------------

class TestDailyAccrualSummary:
    def test_fields(self) -> None:
        from screener.daily_store import DailyAccrualSummary
        s = DailyAccrualSummary(
            symbol="ITMG",
            rows_before=100,
            rows_after=120,
            new_rows=20,
            date_range=("2025-09-01", "2025-09-30"),
        )
        assert s.symbol == "ITMG"
        assert s.rows_before == 100
        assert s.rows_after == 120
        assert s.new_rows == 20
        assert s.date_range == ("2025-09-01", "2025-09-30")
        assert s.divergences == ()  # default

    def test_is_frozen(self) -> None:
        from screener.daily_store import DailyAccrualSummary
        s = DailyAccrualSummary("X", 0, 0, 0, (None, None))
        with pytest.raises((AttributeError, TypeError)):
            s.rows_after = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Section D: corporate-action divergence detection
# ---------------------------------------------------------------------------

class TestDivergenceDetection:
    def test_no_divergence_on_first_snapshot(self, tmp_path) -> None:
        """Nothing to compare against yet -> zero divergences, however the data looks."""
        from screener.daily_store import snapshot_daily
        df = _make_bars(n_days=5, symbol="ITMG", ref_price=1000.0)
        ds = _make_mock_ds({"ITMG": df})
        summaries = snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        assert summaries["ITMG"].divergences == ()

    def test_no_divergence_on_identical_refetch(self, tmp_path) -> None:
        """Re-fetching byte-identical data (the common case) trips nothing."""
        from screener.daily_store import snapshot_daily
        df = _make_bars(n_days=10, symbol="ITMG", ref_price=1000.0)
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        summaries = snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        assert summaries["ITMG"].divergences == ()

    def test_tiny_diff_within_epsilon_is_not_flagged(self, tmp_path) -> None:
        """A sub-epsilon float wobble is not a corporate action."""
        from screener.daily_store import snapshot_daily
        df_first = _make_bars(n_days=5, symbol="ITMG", ref_price=1000.0)
        ds1 = _make_mock_ds({"ITMG": df_first})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds1)

        df_second = df_first.copy()
        df_second["close"] = df_second["close"] * 1.00001  # 1 bps — well under 5 bps default
        df_second["open"] = df_second["open"] * 1.00001
        df_second["high"] = df_second["high"] * 1.00001
        df_second["low"] = df_second["low"] * 1.00001
        ds2 = _make_mock_ds({"ITMG": df_second})

        summaries = snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds2)
        assert summaries["ITMG"].divergences == ()

    def test_split_like_divergence_is_detected_and_recorded(self, tmp_path) -> None:
        """
        Simulates the RAJA 1:5.26 back-adjustment (plan 12 §5e): a re-fetch
        rewrites every prior close/open/high/low by the same ratio. This must
        be detected, logged, and recorded — never silently applied.
        """
        from screener.daily_store import snapshot_daily, load_daily

        df_first = _make_bars(n_days=10, symbol="RAJA", ref_price=920.0, start_date="2026-07-01")
        ds1 = _make_mock_ds({"RAJA": df_first})
        snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=ds1)

        # Back-adjustment: whole history rescaled by 1/5.26, as chartbit would
        # after the split is applied retroactively.
        split_ratio = 1 / 5.26
        df_second = df_first.copy()
        for col in ("open", "high", "low", "close"):
            df_second[col] = df_second[col] * split_ratio
        ds2 = _make_mock_ds({"RAJA": df_second})

        summaries = snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=ds2)
        s = summaries["RAJA"]

        assert len(s.divergences) > 0, "Split-like rewrite must be detected"
        assert all(d.symbol == "RAJA" for d in s.divergences)
        assert all(d.field in ("open", "high", "low", "close") for d in s.divergences)
        # Every diverging date should show ~81% relative change (1 - split_ratio).
        assert all(d.pct_change > 0.5 for d in s.divergences)

        # The merge is not silent-refusal: the store now holds the NEW
        # (back-adjusted, authoritative) values, not the stale pre-split ones.
        result = load_daily("RAJA", store_dir=str(tmp_path))
        assert result is not None
        stored_close = result.sort_values("date")["close"].iloc[0]
        assert stored_close == pytest.approx(920.0 * split_ratio)

    def test_divergence_is_logged_loudly(self, tmp_path, caplog) -> None:
        """A divergence must produce a WARNING-level log record, not a silent overwrite."""
        import logging
        from screener.daily_store import snapshot_daily

        df_first = _make_bars(n_days=3, symbol="RAJA", ref_price=920.0)
        ds1 = _make_mock_ds({"RAJA": df_first})
        snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=ds1)

        df_second = df_first.copy()
        df_second["close"] = df_second["close"] * 0.19  # ~5.26x reverse-adjustment
        df_second["open"] = df_second["open"] * 0.19
        df_second["high"] = df_second["high"] * 0.19
        df_second["low"] = df_second["low"] * 0.19
        ds2 = _make_mock_ds({"RAJA": df_second})

        with caplog.at_level(logging.WARNING, logger="screener.daily_store"):
            snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=ds2)

        assert any("divergence" in rec.message.lower() or "back-adjust" in rec.message.lower()
                   or "corporate" in rec.message.lower() for rec in caplog.records), (
            "Expected a loud WARNING log mentioning the divergence/back-adjustment"
        )

    def test_divergence_is_recorded_to_disk(self, tmp_path) -> None:
        """Divergences are appended to a JSONL log next to the store — never lost."""
        from screener.daily_store import snapshot_daily, _divergence_log_path

        df_first = _make_bars(n_days=3, symbol="RAJA", ref_price=920.0, start_date="2026-07-01")
        ds1 = _make_mock_ds({"RAJA": df_first})
        snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=ds1)

        df_second = df_first.copy()
        df_second["close"] = df_second["close"] * 0.19
        df_second["open"] = df_second["open"] * 0.19
        df_second["high"] = df_second["high"] * 0.19
        df_second["low"] = df_second["low"] * 0.19
        ds2 = _make_mock_ds({"RAJA": df_second})

        summaries = snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=ds2)

        log_path = _divergence_log_path(str(tmp_path))
        assert os.path.isfile(log_path), "divergence_log.jsonl must be written"

        with open(log_path, encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        assert len(lines) == len(summaries["RAJA"].divergences)
        for rec in lines:
            assert rec["symbol"] == "RAJA"
            assert rec["field"] in ("open", "high", "low", "close")
            assert "old_value" in rec and "new_value" in rec and "pct_change" in rec
            assert "detected_at" in rec
            # Never a credential/token, ever.
            assert "token" not in json.dumps(rec).lower()
            assert "bearer" not in json.dumps(rec).lower()

    def test_divergence_log_accumulates_across_snapshots(self, tmp_path) -> None:
        """A second, later divergence appends rather than overwriting the first."""
        from screener.daily_store import snapshot_daily, _divergence_log_path

        df_v1 = _make_bars(n_days=3, symbol="RAJA", ref_price=920.0, start_date="2026-07-01")
        snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=_make_mock_ds({"RAJA": df_v1}))

        df_v2 = df_v1.copy()
        df_v2["close"] *= 0.19
        df_v2["open"] *= 0.19
        df_v2["high"] *= 0.19
        df_v2["low"] *= 0.19
        snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=_make_mock_ds({"RAJA": df_v2}))

        df_v3 = df_v2.copy()
        df_v3["close"] *= 2.0
        df_v3["open"] *= 2.0
        df_v3["high"] *= 2.0
        df_v3["low"] *= 2.0
        snapshot_daily(["RAJA"], store_dir=str(tmp_path), data_source=_make_mock_ds({"RAJA": df_v3}))

        with open(_divergence_log_path(str(tmp_path)), encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        # Both rounds of divergence are present, not just the most recent.
        assert len({tuple(sorted(l.items())) for l in lines}) >= 2 or len(lines) >= 2

    def test_divergence_epsilon_is_configurable(self, tmp_path) -> None:
        """A caller can loosen/tighten the epsilon explicitly."""
        from screener.daily_store import snapshot_daily

        df_first = _make_bars(n_days=3, symbol="ITMG", ref_price=1000.0)
        ds1 = _make_mock_ds({"ITMG": df_first})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds1)

        df_second = df_first.copy()
        df_second["close"] = df_second["close"] * 1.01  # 1% move
        df_second["open"] = df_second["open"] * 1.01
        df_second["high"] = df_second["high"] * 1.01
        df_second["low"] = df_second["low"] * 1.01
        ds2 = _make_mock_ds({"ITMG": df_second})

        # Loose epsilon (2%) should NOT flag a 1% move.
        loose = snapshot_daily(
            ["ITMG"], store_dir=str(tmp_path), data_source=ds2, divergence_epsilon=0.02
        )
        assert loose["ITMG"].divergences == ()


# ---------------------------------------------------------------------------
# Section E: coverage_for_symbol / store_coverage
# ---------------------------------------------------------------------------

class TestCoverage:
    def test_coverage_missing_symbol(self, tmp_path) -> None:
        from screener.daily_store import coverage_for_symbol
        cov = coverage_for_symbol("NOSUCHSYM", store_dir=str(tmp_path))
        assert cov.row_count == 0
        assert cov.first_date is None
        assert cov.last_date is None
        assert cov.gap_dates == ()

    def test_coverage_reports_row_count_and_range(self, tmp_path) -> None:
        from screener.daily_store import snapshot_daily, coverage_for_symbol
        df = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        cov = coverage_for_symbol("ITMG", store_dir=str(tmp_path))
        assert cov.symbol == "ITMG"
        assert cov.row_count == 5
        assert cov.first_date == "2025-09-01"
        assert cov.last_date is not None

    def test_coverage_flags_a_real_trading_day_gap(self, tmp_path) -> None:
        """A missing Tuesday (no holiday) between two stored Mondays is a gap."""
        from screener.daily_store import snapshot_daily, coverage_for_symbol

        # 2025-09-01 (Mon), 2025-09-02 (Tue), 2025-09-03 (Wed) — drop Tuesday.
        rows = [
            {"date": "2025-09-01", "open": 1000.0, "high": 1010.0, "low": 990.0,
             "close": 1000.0, "volume": 1000, "value": 1e6, "net_foreign": 0},
            {"date": "2025-09-03", "open": 1000.0, "high": 1010.0, "low": 990.0,
             "close": 1000.0, "volume": 1000, "value": 1e6, "net_foreign": 0},
        ]
        df = pd.DataFrame(rows)
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        empty_calendar = MarketCalendar(holidays=frozenset())  # no IDX holidays injected
        cov = coverage_for_symbol("ITMG", store_dir=str(tmp_path), calendar=empty_calendar)

        assert "2025-09-02" in cov.gap_dates
        assert cov.trading_days_expected == 3  # Mon, Tue, Wed
        assert cov.trading_days_present == 2

    def test_coverage_does_not_flag_a_holiday_as_a_gap(self, tmp_path) -> None:
        """A missing bar on a declared IDX holiday must not count as a gap."""
        from screener.daily_store import snapshot_daily, coverage_for_symbol

        rows = [
            {"date": "2025-09-01", "open": 1000.0, "high": 1010.0, "low": 990.0,
             "close": 1000.0, "volume": 1000, "value": 1e6, "net_foreign": 0},
            {"date": "2025-09-03", "open": 1000.0, "high": 1010.0, "low": 990.0,
             "close": 1000.0, "volume": 1000, "value": 1e6, "net_foreign": 0},
        ]
        df = pd.DataFrame(rows)
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        # 2025-09-02 is declared a holiday -> not expected, not a gap.
        holiday_calendar = MarketCalendar(holidays=frozenset({datetime.date(2025, 9, 2)}))
        cov = coverage_for_symbol("ITMG", store_dir=str(tmp_path), calendar=holiday_calendar)

        assert "2025-09-02" not in cov.gap_dates
        assert cov.trading_days_expected == 2
        assert cov.trading_days_present == 2

    def test_coverage_does_not_flag_weekends(self, tmp_path) -> None:
        """A Friday-to-Monday store has zero gaps (Sat/Sun aren't trading days)."""
        from screener.daily_store import snapshot_daily, coverage_for_symbol

        rows = [
            {"date": "2025-09-05", "open": 1000.0, "high": 1010.0, "low": 990.0,  # Friday
             "close": 1000.0, "volume": 1000, "value": 1e6, "net_foreign": 0},
            {"date": "2025-09-08", "open": 1000.0, "high": 1010.0, "low": 990.0,  # Monday
             "close": 1000.0, "volume": 1000, "value": 1e6, "net_foreign": 0},
        ]
        df = pd.DataFrame(rows)
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        empty_calendar = MarketCalendar(holidays=frozenset())
        cov = coverage_for_symbol("ITMG", store_dir=str(tmp_path), calendar=empty_calendar)

        assert cov.gap_dates == ()
        assert cov.trading_days_expected == 2

    def test_store_coverage_defaults_to_every_symbol_on_disk(self, tmp_path) -> None:
        from screener.daily_store import snapshot_daily, store_coverage
        df_itmg = _make_bars(n_days=3, symbol="ITMG")
        df_bumi = _make_bars(n_days=4, symbol="BUMI")
        ds = _make_mock_ds({"ITMG": df_itmg, "BUMI": df_bumi})
        snapshot_daily(["ITMG", "BUMI"], store_dir=str(tmp_path), data_source=ds)

        cov = store_coverage(None, store_dir=str(tmp_path))
        assert set(cov.keys()) == {"ITMG", "BUMI"}

    def test_store_coverage_restricts_to_requested_symbols(self, tmp_path) -> None:
        from screener.daily_store import snapshot_daily, store_coverage
        df_itmg = _make_bars(n_days=3, symbol="ITMG")
        df_bumi = _make_bars(n_days=4, symbol="BUMI")
        ds = _make_mock_ds({"ITMG": df_itmg, "BUMI": df_bumi})
        snapshot_daily(["ITMG", "BUMI"], store_dir=str(tmp_path), data_source=ds)

        cov = store_coverage(["ITMG"], store_dir=str(tmp_path))
        assert set(cov.keys()) == {"ITMG"}


# ---------------------------------------------------------------------------
# Section F: CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_snapshot_subcommand_accepted(self, tmp_path) -> None:
        """'snapshot' subcommand is parsed without error when given a mock data source."""
        from screener.daily_store import main as store_main

        df = _make_bars(n_days=3, symbol="PTRO")
        mock_ds = _make_mock_ds({"PTRO": df})

        with patch("screener.daily_store._build_live_data_source", return_value=mock_ds):
            try:
                store_main(["snapshot", "--symbols", "PTRO", "--out-dir", str(tmp_path)])
            except SystemExit as e:
                assert e.code == 0 or e.code is None, f"CLI exited {e.code}"

    def test_cli_creates_store_for_symbol(self, tmp_path) -> None:
        """After CLI snapshot, load_daily returns data for the symbol."""
        from screener.daily_store import main as store_main, load_daily

        df = _make_bars(n_days=3, symbol="PTRO")
        mock_ds = _make_mock_ds({"PTRO": df})

        with patch("screener.daily_store._build_live_data_source", return_value=mock_ds):
            store_main(["snapshot", "--symbols", "PTRO", "--out-dir", str(tmp_path)])

        result = load_daily("PTRO", store_dir=str(tmp_path))
        assert result is not None and len(result) == len(df)

    def test_cli_snapshot_requires_symbols(self, tmp_path) -> None:
        """Unlike intraday_store, there is no hardcoded default symbol list."""
        from screener.daily_store import main as store_main

        with pytest.raises(SystemExit) as exc_info:
            store_main(["snapshot", "--out-dir", str(tmp_path)])
        assert exc_info.value.code != 0

    def test_cli_status_subcommand_reports_coverage(self, tmp_path, capsys) -> None:
        """'status' subcommand prints coverage for stored symbols."""
        from screener.daily_store import main as store_main

        df = _make_bars(n_days=5, symbol="PTRO")
        mock_ds = _make_mock_ds({"PTRO": df})
        with patch("screener.daily_store._build_live_data_source", return_value=mock_ds):
            store_main(["snapshot", "--symbols", "PTRO", "--out-dir", str(tmp_path)])

        store_main(["status", "--out-dir", str(tmp_path)])
        captured = capsys.readouterr()
        assert "PTRO" in captured.out

    def test_cli_status_on_empty_store_does_not_raise(self, tmp_path, capsys) -> None:
        from screener.daily_store import main as store_main
        store_main(["status", "--out-dir", str(tmp_path)])
        captured = capsys.readouterr()
        assert captured.out  # some message printed, no traceback


# ---------------------------------------------------------------------------
# Section G: lib.data_inventory wiring
#
# data_inventory.py already anticipates this store (DEFAULT_DAILY_STORE_DIR =
# research/data/daily, scan_daily_store() -> _scan_bar_store(..., granularity=
# "daily_bar")) and needs NO changes: its own docstring says it "starts
# reporting real coverage the moment such a store is added". This proves that
# promise against a real snapshot_daily()-written store, not just the
# synthetic pyarrow fixtures in lib/test_data_inventory.py.
# ---------------------------------------------------------------------------

class TestDataInventoryWiring:
    def test_scan_daily_store_sees_a_real_snapshot(self, tmp_path) -> None:
        """A store written by snapshot_daily is picked up by scan_daily_store as-is."""
        from screener.daily_store import snapshot_daily
        from lib.data_inventory import scan_daily_store

        df = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=ds)

        result = scan_daily_store(tmp_path)

        assert len(result) == 1
        cov = result[0]
        assert cov.symbol == "ITMG"
        assert cov.granularity == "daily_bar"  # NOT "1day_bar" -- one spelling, one source of truth
        assert cov.row_count == 5
        assert cov.distinct_trading_days == 5
        assert cov.first_date == datetime.date(2025, 9, 1)
        assert isinstance(cov.first_date, datetime.date), (
            "scan_daily_store's SourceCoverage.first_date must be a real date, "
            "which only holds if daily_store normalises the 'date' column before writing"
        )

    def test_scan_daily_store_reflects_accrual_across_snapshots(self, tmp_path) -> None:
        """Row/day counts grow as snapshot_daily accrues more history — no rewrite needed."""
        from screener.daily_store import snapshot_daily
        from lib.data_inventory import scan_daily_store

        df_first = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=_make_mock_ds({"ITMG": df_first}))
        before = scan_daily_store(tmp_path)[0]

        df_second = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-08")
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=_make_mock_ds({"ITMG": df_second}))
        after = scan_daily_store(tmp_path)[0]

        assert after.row_count > before.row_count
        assert after.last_date > before.last_date

    def test_build_inventory_reports_daily_bars_on_disk_from_a_real_store(self, tmp_path) -> None:
        """End-to-end: build_inventory() surfaces this store via daily_bars_on_disk."""
        from screener.daily_store import snapshot_daily
        from lib.data_inventory import build_inventory

        df = _make_bars(n_days=3, symbol="ITMG")
        snapshot_daily(["ITMG"], store_dir=str(tmp_path), data_source=_make_mock_ds({"ITMG": df}))

        # Isolate from the real research/data/intraday and segments/ trees —
        # this test asserts only what build_inventory does with OUR daily store.
        report = build_inventory(
            intraday_store_dir=tmp_path / "no-intraday",
            segments_root=tmp_path / "no-segments",
            daily_store_dir=tmp_path,
        )

        assert len(report.daily_bars_on_disk) == 1
        assert report.daily_bars_on_disk[0].symbol == "ITMG"
        # A source fact, independent of the on-disk store, is still populated too.
        assert report.daily_bar_capability is not None
