"""--all-stored refresh: discovery, incremental window, per-symbol isolation, summary.

No network: data sources are fakes, time.sleep is patched.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from screener import daily_store as ds_mod
from screener.daily_store import (
    INCREMENTAL_OVERLAP_DAYS,
    REQUEST_DELAY_SECONDS,
    discover_store_symbols,
    incremental_start,
    load_daily,
    main as store_main,
    snapshot_daily,
)
from screener.sb_http import SBHttpError
from screener.test_daily_store import _make_bars

END = "2026-10-14"


class FakeSource:
    """Serves a full per-symbol history, honouring start/end; records calls."""

    def __init__(self, full: dict[str, pd.DataFrame], errors: dict[str, Exception] | None = None):
        self.full = full
        self.errors = errors or {}
        self.calls: list[tuple[str, str, str]] = []

    def daily_history(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol in self.errors:
            raise self.errors[symbol]
        df = self.full.get(symbol, pd.DataFrame())
        if df.empty:
            return df
        return df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)


def _seed(tmp_path, symbols, n_days=10, start="2026-08-31", end="2026-09-11"):
    full = {s: _make_bars(n_days=40, start_date=start, symbol=s) for s in symbols}
    seed_src = FakeSource({s: full[s].head(n_days) for s in symbols})
    snapshot_daily(list(symbols), str(tmp_path), seed_src, end=end)
    return full


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("screener.daily_store.time.sleep") as m:
        yield m


class TestDiscovery:
    def test_derives_sorted_symbols_from_parquet_names(self, tmp_path):
        _seed(tmp_path, ["BBRI", "ITMG"])
        assert discover_store_symbols(str(tmp_path)) == ["BBRI", "ITMG"]

    def test_ignores_non_symbol_files(self, tmp_path):
        _seed(tmp_path, ["BBRI"])
        (tmp_path / "divergence_log.jsonl").write_text("{}")
        (tmp_path / "notes.txt").write_text("x")
        (tmp_path / "bad name.parquet").write_bytes(b"")
        (tmp_path / "lower.parquet").write_bytes(b"")
        (tmp_path / ".hidden.parquet").write_bytes(b"")
        assert discover_store_symbols(str(tmp_path)) == ["BBRI"]

    def test_missing_dir_is_empty(self, tmp_path):
        assert discover_store_symbols(str(tmp_path / "nope")) == []


class TestIncrementalStart:
    def test_last_date_minus_overlap(self, tmp_path):
        _seed(tmp_path, ["BBRI"])
        existing = load_daily("BBRI", str(tmp_path))
        last = max(existing["date"])
        expect = str(last - datetime.timedelta(days=INCREMENTAL_OVERLAP_DAYS))
        assert incremental_start(existing, "2000-01-01") == expect

    def test_no_store_uses_full_start(self):
        assert incremental_start(None, "2000-01-01") == "2000-01-01"
        assert incremental_start(pd.DataFrame(), "2000-01-01") == "2000-01-01"

    def test_never_earlier_than_requested_start(self, tmp_path):
        _seed(tmp_path, ["BBRI"])
        existing = load_daily("BBRI", str(tmp_path))
        assert incremental_start(existing, "2026-09-10") == "2026-09-10"


class TestIncrementalSnapshot:
    def test_requests_short_window_and_merges_without_duplicates(self, tmp_path):
        full = _seed(tmp_path, ["BBRI"])
        src = FakeSource(full)
        out = snapshot_daily(["BBRI"], str(tmp_path), src, end=END, incremental=True)
        (_, start, _) = src.calls[0]
        assert start > "2026-08-31"
        df = load_daily("BBRI", str(tmp_path))
        assert df["date"].is_unique
        assert len(df) > 10
        assert out["BBRI"].new_rows == len(df) - 10
        assert out["BBRI"].rows_before == 10
        # earliest stored rows are kept even though the fetch never returned them
        assert str(df["date"].min()) == "2026-08-31"

    def test_unchanged_overlap_means_no_divergence(self, tmp_path):
        full = _seed(tmp_path, ["BBRI"])
        out = snapshot_daily(["BBRI"], str(tmp_path), FakeSource(full), end=END, incremental=True)
        assert out["BBRI"].divergences == ()

    def test_revised_overlap_bar_replaces_stored(self, tmp_path):
        full = _seed(tmp_path, ["BBRI"])
        revised = full["BBRI"].copy()
        revised.loc[revised["date"] == "2026-09-11", "close"] = 1100.0
        snapshot_daily(["BBRI"], str(tmp_path), FakeSource({"BBRI": revised}), end=END, incremental=True)
        df = load_daily("BBRI", str(tmp_path))
        assert df.loc[df["date"] == datetime.date(2026, 9, 11), "close"].iloc[0] == 1100.0

    def test_corporate_action_in_overlap_triggers_full_refetch(self, tmp_path):
        full = _seed(tmp_path, ["BBRI"])
        adjusted = full["BBRI"].copy()
        for c in ("open", "high", "low", "close"):
            adjusted[c] = adjusted[c] / 5.0  # a split back-adjusts ALL history
        src = FakeSource({"BBRI": adjusted})
        out = snapshot_daily(["BBRI"], str(tmp_path), src, end=END, incremental=True)
        assert len(src.calls) == 2
        assert src.calls[1][1] == ds_mod.DEFAULT_DAILY_HISTORY_START
        df = load_daily("BBRI", str(tmp_path))
        # whole history rewritten consistently, not just the overlap
        assert (df["close"] == 200.0).all()
        assert out["BBRI"].divergences


class TestRefreshAll:
    def _run(self, tmp_path, src, capsys, extra=()):
        with patch("screener.daily_store._build_live_data_source", return_value=src):
            try:
                store_main(["snapshot", "--all-stored", "--out-dir", str(tmp_path), "--end", END, *extra])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, capsys.readouterr().out

    def test_mutually_exclusive_with_symbols(self, tmp_path):
        with pytest.raises(SystemExit) as e:
            store_main(["snapshot", "--all-stored", "--symbols", "BBRI", "--out-dir", str(tmp_path)])
        assert e.value.code == 2

    def test_one_of_them_required(self, tmp_path):
        with pytest.raises(SystemExit) as e:
            store_main(["snapshot", "--out-dir", str(tmp_path)])
        assert e.value.code == 2

    def test_empty_store_fails_clearly(self, tmp_path, capsys):
        code, out = self._run(tmp_path, FakeSource({}), capsys)
        assert code != 0
        assert "no stored symbols" in out.lower()

    def test_all_ok_exit_zero_with_progress_and_summary(self, tmp_path, capsys):
        full = _seed(tmp_path, ["AAAA", "BBRI"])
        src = FakeSource(full)
        code, out = self._run(tmp_path, src, capsys)
        assert code == 0
        assert "[1/2] AAAA ok (+" in out
        assert "[2/2] BBRI ok (+" in out
        assert "refreshed: 2" in out and "failed: 0" in out
        assert all(c[1] != ds_mod.DEFAULT_DAILY_HISTORY_START for c in src.calls)

    def test_unchanged_symbol_is_reported_unchanged(self, tmp_path, capsys):
        full = _seed(tmp_path, ["BBRI"])
        src = FakeSource({"BBRI": full["BBRI"].head(10)})
        code, out = self._run(tmp_path, src, capsys)
        assert code == 0
        assert "[1/1] BBRI unchanged" in out
        assert "unchanged: 1" in out

    def test_one_failure_does_not_abort_others_but_exits_nonzero(self, tmp_path, capsys):
        full = _seed(tmp_path, ["AAAA", "BADD", "ZZZZ"])
        src = FakeSource(full, errors={"BADD": SBHttpError(403, "https://x/y?z=1")})
        code, out = self._run(tmp_path, src, capsys)
        assert code == 1
        assert [c[0] for c in src.calls] == ["AAAA", "BADD", "ZZZZ"]
        assert "[2/3] BADD FAILED" in out
        assert "refreshed: 2" in out and "failed: 1" in out
        tail = out.split("failed: 1")[1]
        assert "BADD" in tail and "403" in tail
        assert len(load_daily("BADD", str(tmp_path))) == 10
        assert len(load_daily("ZZZZ", str(tmp_path))) > 10

    def test_stale_empty_response_counts_as_failed_not_unchanged(self, tmp_path, capsys):
        _seed(tmp_path, ["BBRI"])
        code, out = self._run(tmp_path, FakeSource({}), capsys)
        assert code == 1
        assert "failed: 1" in out and "unchanged: 0" in out
        assert "empty response" in out

    def test_delay_between_requests_not_after_last(self, tmp_path, capsys, _no_sleep):
        full = _seed(tmp_path, ["AAAA", "BBRI", "ZZZZ"])
        self._run(tmp_path, FakeSource(full), capsys)
        assert _no_sleep.call_count == 2
        _no_sleep.assert_called_with(REQUEST_DELAY_SECONDS)

    def test_token_not_printed(self, tmp_path, capsys, monkeypatch):
        full = _seed(tmp_path, ["BBRI"])
        monkeypatch.setenv("SB_BEARER_TOKEN", "sekret-sentinel-value")
        _, out = self._run(tmp_path, FakeSource(full), capsys)
        assert "sekret-sentinel-value" not in out
