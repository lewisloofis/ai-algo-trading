"""--all-stored refresh: discovery, incremental window, per-symbol isolation, summary.

No network: data sources are fakes, time.sleep is patched.
"""
from __future__ import annotations

import datetime
import json
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
from screener.emitten import SUBSECTOR_DAY_TRADE, SUBSECTOR_TRADING_LIMIT, ClassificationError, CompanyRecord
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
                store_main(["snapshot", "--all-stored", "--out-dir", str(tmp_path), "--end", END,
                            "--no-tradable-check", *extra])
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
        monkeypatch.setenv("SB_ACCESS_TOKEN", "sekret-sentinel-value")
        _, out = self._run(tmp_path, FakeSource(full), capsys)
        assert "sekret-sentinel-value" not in out


class TestEndedSymbols:
    """Delisted names stay in the store (survivorship tests) but must not be re-fetched."""

    def _run(self, tmp_path, src, capsys, extra=()):
        return TestRefreshAll._run(self, tmp_path, src, capsys, extra)

    def _seed_live_and_dead(self, tmp_path):
        full = _seed(tmp_path, ["AAAA"])
        dead = _seed(tmp_path, ["DEAD"], start="2025-01-02", end="2025-01-15")
        return {**full, **dead}

    def test_ended_symbol_is_skipped_and_named(self, tmp_path, capsys):
        src = FakeSource(self._seed_live_and_dead(tmp_path))
        code, out = self._run(tmp_path, src, capsys)
        assert code == 0
        assert [c[0] for c in src.calls] == ["AAAA"]
        assert "skipped 1 ended" in out.lower() and "DEAD" in out

    def test_include_ended_fetches_them_too(self, tmp_path, capsys):
        src = FakeSource(self._seed_live_and_dead(tmp_path))
        self._run(tmp_path, src, capsys, extra=("--include-ended",))
        assert [c[0] for c in src.calls] == ["AAAA", "DEAD"]


def _rec(symbol, tradeable=True):
    return CompanyRecord(symbol, tradeable, "Active", (), False)


class FakeEmitten:
    """Serves classification rows per subsector id, like EmittenClient.fetch_classification."""

    def __init__(self, day_trade=(), trading_limit=()):
        self._rows = {SUBSECTOR_DAY_TRADE: tuple(day_trade), SUBSECTOR_TRADING_LIMIT: tuple(trading_limit)}

    def fetch_classification(self, subsector_id):
        return self._rows[subsector_id]


def _both(*pairs):
    """FakeEmitten with every (symbol, tradeable) on both lists."""
    rows = [_rec(s, t) for s, t in pairs]
    return FakeEmitten(rows, rows)


class TestTradableStep:
    """The universe-status step: skip untradable, add newly eligible, fail clearly on emitten errors."""

    def _run(self, tmp_path, src, capsys, emitten, extra=()):
        with patch("screener.daily_store._build_live_data_source", return_value=src), \
             patch("screener.daily_store._build_status_source", return_value=emitten):
            try:
                store_main(["snapshot", "--all-stored", "--out-dir", str(tmp_path), "--end", END, *extra])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, capsys.readouterr().out

    def test_untradable_symbol_is_skipped_named_and_recorded(self, tmp_path, capsys):
        src = FakeSource(_seed(tmp_path, ["AAAA", "GREN"]))
        code, out = self._run(tmp_path, src, capsys, _both(("AAAA", True), ("GREN", False)))
        assert code == 0
        assert [c[0] for c in src.calls] == ["AAAA"]
        assert "skipped 1 not-tradable" in out.lower() and "GREN" in out
        status = json.loads((tmp_path / "universe_status.json").read_text())
        assert status["schema_version"] == 2
        assert status["symbols"]["GREN"]["is_tradable"] is False
        assert status["symbols"]["AAAA"]["is_tradable"] is True

    def test_stored_symbol_off_both_lists_is_null_and_still_refreshed(self, tmp_path, capsys):
        src = FakeSource(_seed(tmp_path, ["AAAA"]))
        code, _ = self._run(tmp_path, src, capsys, FakeEmitten())
        assert code == 0 and [c[0] for c in src.calls] == ["AAAA"]
        status = json.loads((tmp_path / "universe_status.json").read_text())
        assert status["symbols"]["AAAA"]["is_tradable"] is None

    def test_disagreement_between_lists_false_wins_and_skips(self, tmp_path, capsys):
        src = FakeSource(_seed(tmp_path, ["AAAA", "SPLT"]))
        emitten = FakeEmitten([_rec("AAAA"), _rec("SPLT", True)], [_rec("AAAA"), _rec("SPLT", False)])
        code, out = self._run(tmp_path, src, capsys, emitten)
        assert code == 0 and [c[0] for c in src.calls] == ["AAAA"] and "SPLT" in out

    def test_eligible_and_tradable_missing_symbol_is_added_with_full_history(self, tmp_path, capsys):
        full = _seed(tmp_path, ["AAAA"])
        full["NEWW"] = _make_bars(n_days=40, start_date="2026-08-31", symbol="NEWW")
        src = FakeSource(full)
        code, out = self._run(tmp_path, src, capsys, _both(("AAAA", True), ("NEWW", True)))
        assert code == 0
        assert "added 1 new" in out.lower() and "NEWW" in out
        assert ("NEWW", ds_mod.DEFAULT_DAILY_HISTORY_START, END) in src.calls
        assert len(load_daily("NEWW", str(tmp_path))) > 0

    def test_missing_flag_or_untradable_or_single_list_is_not_added(self, tmp_path, capsys):
        src = FakeSource(_seed(tmp_path, ["AAAA"]))
        emitten = FakeEmitten(
            [_rec("AAAA"), _rec("UNKN", None), _rec("DEAD", False), _rec("ONLY")],
            [_rec("AAAA"), _rec("UNKN", None), _rec("DEAD", False)])
        code, _ = self._run(tmp_path, src, capsys, emitten)
        assert code == 0 and [c[0] for c in src.calls] == ["AAAA"]

    def test_ended_fallback_still_applies(self, tmp_path, capsys):
        full = {**_seed(tmp_path, ["AAAA"]), **_seed(tmp_path, ["DEAD"], start="2025-01-02", end="2025-01-15")}
        src = FakeSource(full)
        _, out = self._run(tmp_path, src, capsys, FakeEmitten())
        assert [c[0] for c in src.calls] == ["AAAA"] and "ended" in out.lower()

    def test_no_trading_token_is_needed(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("SB_TRADING_ACCESS_TOKEN", raising=False)
        src = FakeSource(_seed(tmp_path, ["AAAA"]))
        code, out = self._run(tmp_path, src, capsys, _both(("AAAA", True)))
        assert code == 0 and "SB_TRADING_ACCESS_TOKEN" not in out

    def test_missing_bearer_exits_2_before_any_fetch(self, tmp_path, capsys, monkeypatch):
        src = FakeSource(_seed(tmp_path, ["AAAA"]))
        monkeypatch.delenv("SB_ACCESS_TOKEN", raising=False)
        with patch("screener.daily_store._build_live_data_source", return_value=src):
            with pytest.raises(SystemExit) as e:
                store_main(["snapshot", "--all-stored", "--out-dir", str(tmp_path), "--end", END])
        assert e.value.code == 2
        assert "SB_ACCESS_TOKEN" in capsys.readouterr().out
        assert src.calls == [] and not (tmp_path / "universe_status.json").exists()

    def test_emitten_http_failure_exits_2_without_writing_status(self, tmp_path, capsys):
        src = FakeSource(_seed(tmp_path, ["AAAA"]))

        class Boom(FakeEmitten):
            def fetch_classification(self, subsector_id):
                raise SBHttpError(403, "https://x/y?z=1")

        code, out = self._run(tmp_path, src, capsys, Boom())
        assert code == 2 and "403" in out
        assert src.calls == [] and not (tmp_path / "universe_status.json").exists()

    def test_url_error_in_emitten_prints_step_host_and_reason(self, tmp_path, capsys):
        import urllib.error
        src = FakeSource(_seed(tmp_path, ["AAAA"]))

        class Boom(FakeEmitten):
            def fetch_classification(self, subsector_id):
                raise urllib.error.URLError("timed out")

        code, out = self._run(tmp_path, src, capsys, Boom())
        assert code == 2
        assert ("Tradable check FAILED (emitten classification, exodus.stockbit.com): "
                "URLError (timed out)") in out
        assert src.calls == []

    def test_paginated_response_exits_2_with_clear_reason(self, tmp_path, capsys):
        src = FakeSource(_seed(tmp_path, ["AAAA"]))

        class Paged(FakeEmitten):
            def fetch_classification(self, subsector_id):
                raise ClassificationError("classification response is paginated (2 pages); "
                                          "pagination not implemented")

        code, out = self._run(tmp_path, src, capsys, Paged())
        assert code == 2 and "pagination not implemented" in out and src.calls == []

    def test_no_tradable_check_flag_bypasses_everything(self, tmp_path, capsys, monkeypatch):
        src = FakeSource(_seed(tmp_path, ["AAAA"]))
        monkeypatch.delenv("SB_ACCESS_TOKEN", raising=False)
        code, out = TestRefreshAll._run(self, tmp_path, src, capsys)
        assert code == 0 and not (tmp_path / "universe_status.json").exists()
        assert "tradable check skipped" in out.lower()
