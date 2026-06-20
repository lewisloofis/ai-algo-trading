"""
screener/test_intraday_store.py — TDD tests for the intraday accrual store.

Tests:
  A — snapshot_intraday: merge/dedupe, idempotency, accrual across pulls
  B — load_intraday: round-trip, missing-file → None
  C — AccrualSummary dataclass
  D — loader-prefers-store-when-longer (strategy-fit integration)
  E — CLI entrypoint parses args and routes correctly
"""
from __future__ import annotations

import datetime
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers — build minimal intraday DataFrames
# ---------------------------------------------------------------------------

def _make_bars(
    n_days: int = 5,
    bars_per_day: int = 4,
    ref_price: float = 1000.0,
    start_date: str = "2025-09-01",
    symbol: str = "ITMG",
) -> pd.DataFrame:
    """
    Build a synthetic intraday DataFrame matching the LiveDataSource schema.

    Columns: datetime (pd.Timestamp, WIB), date (date), open, high, low,
             close, volume, value, net_foreign.
    unix_timestamp is NOT included — we store by datetime, dedupe by it.
    """
    rows = []
    anchor = pd.Timestamp(start_date)
    days_added = 0
    day_offset = 0
    while days_added < n_days:
        day = anchor + pd.Timedelta(days=day_offset)
        if day.weekday() >= 5:
            day_offset += 1
            continue
        for i in range(bars_per_day):
            dt = day.replace(hour=9, minute=i)
            rows.append({
                "datetime": dt,
                "date": day.date(),
                "open": ref_price,
                "high": ref_price * 1.001,
                "low": ref_price * 0.999,
                "close": ref_price,
                "volume": 1000,
                "value": ref_price * 1000.0,
                "net_foreign": 0,
            })
        days_added += 1
        day_offset += 1
    return pd.DataFrame(rows)


def _make_mock_ds(df_map: dict[str, pd.DataFrame]) -> Any:
    """Mock data source that returns DataFrames from df_map by symbol."""
    ds = MagicMock()
    ds.intraday_history.side_effect = lambda symbol, **kwargs: df_map.get(symbol, pd.DataFrame())
    return ds


# ---------------------------------------------------------------------------
# Section A: snapshot_intraday — merge/dedupe/accrual
# ---------------------------------------------------------------------------

class TestSnapshotIntraday:
    def test_first_snapshot_creates_store(self, tmp_path) -> None:
        """First snapshot for a symbol creates the store file."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), f"Expected {len(df)} rows, got {len(result)}"

    def test_second_snapshot_dedupes_overlapping_rows(self, tmp_path) -> None:
        """Re-running with the same DataFrame produces no duplicates."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=5, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), f"Expected {len(df)} rows after dedup, got {len(result)}"

    def test_second_snapshot_merges_new_bars(self, tmp_path) -> None:
        """A new pull with later bars extends history without duplicating old bars."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        # First pull: days 1–5
        df_first = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        # Second pull: days 4–8 (overlap: days 4–5, new: days 6–8)
        df_second = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-04")
        ds1 = _make_mock_ds({"ITMG": df_first})
        ds2 = _make_mock_ds({"ITMG": df_second})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds1)
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds2)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        # Union of both pulls (no duplicates)
        combined = pd.concat([df_first, df_second]).drop_duplicates(subset="datetime")
        assert len(result) == len(combined), (
            f"Expected {len(combined)} merged rows, got {len(result)}"
        )

    def test_result_is_ascending_sorted(self, tmp_path) -> None:
        """Stored bars are sorted ascending by datetime."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=5, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        dts = pd.to_datetime(result["datetime"]).tolist()
        assert dts == sorted(dts), "Store must be sorted ascending by datetime"

    def test_idempotent_multiple_runs(self, tmp_path) -> None:
        """Running snapshot 5 times with the same data keeps exactly the original count."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=10, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        for _ in range(5):
            snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), (
            f"Idempotent: expected {len(df)} rows, got {len(result)}"
        )

    def test_multi_symbol_snapshot(self, tmp_path) -> None:
        """snapshot_intraday handles multiple symbols independently."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df_itmg = _make_bars(n_days=3, symbol="ITMG", ref_price=27500.0)
        df_bumi = _make_bars(n_days=4, symbol="BUMI", ref_price=168.0)
        ds = _make_mock_ds({"ITMG": df_itmg, "BUMI": df_bumi})
        snapshot_intraday(["ITMG", "BUMI"], store_dir=str(tmp_path), data_source=ds)
        r_itmg = load_intraday("ITMG", store_dir=str(tmp_path))
        r_bumi = load_intraday("BUMI", store_dir=str(tmp_path))
        assert r_itmg is not None and len(r_itmg) == len(df_itmg)
        assert r_bumi is not None and len(r_bumi) == len(df_bumi)

    def test_snapshot_returns_per_symbol_summary(self, tmp_path) -> None:
        """snapshot_intraday returns a dict[symbol, AccrualSummary]."""
        from screener.intraday_store import snapshot_intraday
        df = _make_bars(n_days=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        summaries = snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        assert "ITMG" in summaries
        s = summaries["ITMG"]
        assert hasattr(s, "rows_before")
        assert hasattr(s, "rows_after")
        assert hasattr(s, "new_rows")
        assert s.rows_before == 0
        assert s.rows_after == len(df)
        assert s.new_rows == len(df)

    def test_summary_shows_incremental_new_rows(self, tmp_path) -> None:
        """Second snapshot summary reports only truly new rows."""
        from screener.intraday_store import snapshot_intraday
        df_first = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        df_second = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-04")
        ds1 = _make_mock_ds({"ITMG": df_first})
        ds2 = _make_mock_ds({"ITMG": df_second})
        s1 = snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds1)
        s2 = snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds2)
        assert s1["ITMG"].rows_before == 0
        assert s2["ITMG"].rows_before == s1["ITMG"].rows_after
        assert s2["ITMG"].new_rows >= 0  # could be 0 if all overlap, >0 if new bars exist

    def test_empty_live_pull_does_not_corrupt_store(self, tmp_path) -> None:
        """If the live pull returns empty, the existing store is preserved."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=5, symbol="ITMG")
        ds1 = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds1)
        # Second pull returns empty
        ds_empty = _make_mock_ds({"ITMG": pd.DataFrame()})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds_empty)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df), "Empty pull must not truncate the store"


# ---------------------------------------------------------------------------
# Section B: load_intraday
# ---------------------------------------------------------------------------

class TestLoadIntraday:
    def test_load_missing_returns_none(self, tmp_path) -> None:
        """load_intraday returns None when no store file exists for the symbol."""
        from screener.intraday_store import load_intraday
        result = load_intraday("NOSUCHSYM", store_dir=str(tmp_path))
        assert result is None

    def test_round_trip_preserves_columns(self, tmp_path) -> None:
        """Stored and reloaded DataFrame has the required output columns."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        required = {"datetime", "date", "open", "high", "low", "close", "volume", "value", "net_foreign"}
        missing = required - set(result.columns)
        assert not missing, f"Missing columns after round-trip: {missing}"

    def test_round_trip_preserves_row_count(self, tmp_path) -> None:
        """Row count is unchanged after store→load round trip."""
        from screener.intraday_store import snapshot_intraday, load_intraday
        df = _make_bars(n_days=7, bars_per_day=3, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds)
        result = load_intraday("ITMG", store_dir=str(tmp_path))
        assert result is not None
        assert len(result) == len(df)

    def test_store_dir_created_if_missing(self, tmp_path) -> None:
        """snapshot_intraday creates store_dir if it does not exist."""
        from screener.intraday_store import snapshot_intraday
        nested_dir = str(tmp_path / "a" / "b" / "c")
        df = _make_bars(n_days=2, symbol="ITMG")
        ds = _make_mock_ds({"ITMG": df})
        snapshot_intraday(["ITMG"], store_dir=nested_dir, data_source=ds)
        assert os.path.isdir(nested_dir), "store_dir must be created automatically"


# ---------------------------------------------------------------------------
# Section C: AccrualSummary dataclass
# ---------------------------------------------------------------------------

class TestAccrualSummary:
    def test_accrual_summary_fields(self, tmp_path) -> None:
        """AccrualSummary exposes rows_before, rows_after, new_rows, date_range."""
        from screener.intraday_store import AccrualSummary
        s = AccrualSummary(
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

    def test_accrual_summary_is_frozen(self) -> None:
        """AccrualSummary must not be mutatable."""
        from screener.intraday_store import AccrualSummary
        s = AccrualSummary("X", 0, 0, 0, (None, None))
        try:
            s.rows_after = 99  # type: ignore[misc]
            pytest.fail("Expected frozen dataclass to raise on assignment")
        except (AttributeError, TypeError):
            pass


# ---------------------------------------------------------------------------
# Section D: loader-prefers-store-when-longer
# ---------------------------------------------------------------------------

class TestLoaderPrefersStore:
    def test_store_used_when_longer_than_live(self, tmp_path) -> None:
        """When store has more distinct trading days than live, store is preferred."""
        from screener.intraday_store import IntraStorePrefDataSource, snapshot_intraday

        # Store: 20 trading days of history
        df_store = _make_bars(n_days=20, symbol="ITMG", start_date="2025-06-01")
        ds_for_store = _make_mock_ds({"ITMG": df_store})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds_for_store)

        # Live: only 5 trading days (simulating the 90-day cap on a fresh pull)
        df_live = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        live_ds = _make_mock_ds({"ITMG": df_live})

        wrapped = IntraStorePrefDataSource(live_ds=live_ds, store_dir=str(tmp_path))
        result = wrapped.intraday_history("ITMG")

        # Must return the store (more days)
        assert result is not None
        store_days = df_store["date"].nunique() if "date" in df_store.columns else 0
        result_days = pd.to_datetime(result["datetime"]).dt.date.nunique()
        assert result_days >= store_days, (
            f"Store-preferred loader returned {result_days} days, expected >= {store_days}"
        )

    def test_live_used_when_store_absent(self, tmp_path) -> None:
        """When no store exists, live pull is returned."""
        from screener.intraday_store import IntraStorePrefDataSource

        df_live = _make_bars(n_days=5, symbol="ITMG", start_date="2025-09-01")
        live_ds = _make_mock_ds({"ITMG": df_live})
        wrapped = IntraStorePrefDataSource(live_ds=live_ds, store_dir=str(tmp_path))
        result = wrapped.intraday_history("ITMG")
        assert result is not None
        assert len(result) == len(df_live), (
            f"No store: expected live ({len(df_live)} rows), got {len(result)}"
        )

    def test_live_used_when_store_is_shorter(self, tmp_path) -> None:
        """When live has more days than the store, live is returned."""
        from screener.intraday_store import IntraStorePrefDataSource, snapshot_intraday

        # Store: 3 trading days
        df_store = _make_bars(n_days=3, symbol="ITMG", start_date="2025-06-01")
        ds_for_store = _make_mock_ds({"ITMG": df_store})
        snapshot_intraday(["ITMG"], store_dir=str(tmp_path), data_source=ds_for_store)

        # Live: 10 trading days (more than store)
        df_live = _make_bars(n_days=10, symbol="ITMG", start_date="2025-09-01")
        live_ds = _make_mock_ds({"ITMG": df_live})
        wrapped = IntraStorePrefDataSource(live_ds=live_ds, store_dir=str(tmp_path))
        result = wrapped.intraday_history("ITMG")
        assert result is not None
        result_days = pd.to_datetime(result["datetime"]).dt.date.nunique()
        live_days = df_live["date"].nunique()
        assert result_days >= live_days, (
            f"Live-preferred: expected >= {live_days} days, got {result_days}"
        )

    def test_other_datasource_methods_passthrough(self, tmp_path) -> None:
        """funding_universe and daily_history delegate to the underlying live_ds."""
        from screener.intraday_store import IntraStorePrefDataSource

        live_ds = MagicMock()
        live_ds.funding_universe.return_value = {"ITMG", "BUMI"}
        live_ds.daily_history.return_value = _make_bars(n_days=2)
        live_ds.intraday_history.return_value = _make_bars(n_days=2)

        wrapped = IntraStorePrefDataSource(live_ds=live_ds, store_dir=str(tmp_path))
        uni = wrapped.funding_universe()
        assert uni == {"ITMG", "BUMI"}
        dh = wrapped.daily_history("ITMG", "2025-01-01", "2025-12-31")
        live_ds.daily_history.assert_called_once()

    def test_store_aware_loader_does_not_break_fixture_source(self, tmp_path) -> None:
        """Using IntraStorePrefDataSource with a fixture-like ds and no store works."""
        from screener.intraday_store import IntraStorePrefDataSource
        from strategy_fit.fixtures_generator import StrategyFitFixtureDataSource

        fixture_ds = StrategyFitFixtureDataSource()
        wrapped = IntraStorePrefDataSource(live_ds=fixture_ds, store_dir=str(tmp_path))
        # StrategyFitFixtureDataSource has intraday_history for ITMG
        result = wrapped.intraday_history("ITMG")
        assert result is not None and len(result) > 0, (
            "Fixture source must still work through the store-aware wrapper"
        )


# ---------------------------------------------------------------------------
# Section E: CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_snapshot_subcommand_accepted(self, tmp_path) -> None:
        """'snapshot' subcommand is parsed without error when given a mock data source."""
        from screener.intraday_store import main as store_main

        df = _make_bars(n_days=3, symbol="PTRO")
        mock_ds = _make_mock_ds({"PTRO": df})

        with patch("screener.intraday_store._build_live_data_source", return_value=mock_ds):
            try:
                store_main([
                    "snapshot",
                    "--symbols", "PTRO",
                    "--store-dir", str(tmp_path),
                ])
            except SystemExit as e:
                assert e.code == 0 or e.code is None, f"CLI exited {e.code}"

    def test_cli_creates_store_for_symbol(self, tmp_path) -> None:
        """After CLI snapshot, load_intraday returns data for the symbol."""
        from screener.intraday_store import main as store_main, load_intraday

        df = _make_bars(n_days=3, symbol="PTRO")
        mock_ds = _make_mock_ds({"PTRO": df})

        with patch("screener.intraday_store._build_live_data_source", return_value=mock_ds):
            store_main([
                "snapshot",
                "--symbols", "PTRO",
                "--store-dir", str(tmp_path),
            ])

        result = load_intraday("PTRO", store_dir=str(tmp_path))
        assert result is not None and len(result) == len(df)

    def test_cli_default_symbols_are_orb_survivors(self, tmp_path) -> None:
        """Default symbols include the 4 ORB survivors: PTRO, BRPT, TPIA, ENRG."""
        from screener.intraday_store import DEFAULT_SNAPSHOT_SYMBOLS
        for sym in ("PTRO", "BRPT", "TPIA", "ENRG"):
            assert sym in DEFAULT_SNAPSHOT_SYMBOLS, (
                f"Expected {sym} in DEFAULT_SNAPSHOT_SYMBOLS"
            )
