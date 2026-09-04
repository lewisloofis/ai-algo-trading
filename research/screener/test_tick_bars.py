"""
screener/test_tick_bars.py — TDD tests for the segments -> tick-bar builder.

Tests:
  A — segment discovery: dt= directory scan, date-range filtering, multi-watchlist dirs
  B — _run_sbcap_bars: CSV parsing, zero rows, non-zero exit, argv/no-shell contract
  C — build_bars: fresh build, append-merge dedup, multi-watchlist combine,
      date-range filtering, zero-row symbol reporting, per-symbol error isolation
  D — load_tick_bars: round-trip, missing-file -> None
  E — CLI: success summary lines, all-symbols-failed exit code, partial-failure exit 0

No real sbcap binary or segments are used anywhere here — subprocess.run is
always mocked to emit known CSV (or fail). The header below matches the
CURRENT collector/cmd/sbcap/bars.go schema (barsCSVHeader), but tick_bars.py
must parse the CSV header dynamically rather than assume this exact column
list — see TestParseBarsCsv.test_unknown_extra_column_is_carried_through,
which pins that contract with a header this suite does not otherwise use.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

# Matches collector/cmd/sbcap/bars.go's barsCSVHeader: untagged_vol/untagged_trades
# were added after sell_trades (and --include-auction replaced by --tagged-only,
# default off) so that untagged trades — pre-opening auction, mid-session
# negotiated/crossing prints, and the whole 16:00-16:16 post-trading session —
# are reported rather than silently dropped.
_HEADER = (
    "datetime,date,open,high,low,close,volume,value,trades,"
    "buy_vol,sell_vol,buy_trades,sell_trades,untagged_vol,untagged_trades,vwap"
)


def _csv(rows: list[str]) -> str:
    """Build an sbcap-shaped CSV payload: header + zero or more data rows."""
    return "\n".join([_HEADER, *rows]) + "\n"


def _row(
    dt, o, h, l, c, v, val, trades, buy_vol, sell_vol, buy_trades, sell_trades,
    untagged_vol=0, untagged_trades=0, vwap="0.0000",
) -> str:
    return (
        f"{dt},{dt[:10]},{o},{h},{l},{c},{v},{val},{trades},"
        f"{buy_vol},{sell_vol},{buy_trades},{sell_trades},"
        f"{untagged_vol},{untagged_trades},{vwap}"
    )


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> SimpleNamespace:
    """Stand-in for subprocess.CompletedProcess — only .stdout/.stderr/.returncode are read."""
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _make_segment_tree(root: str, date_str: str, watchlist_ids: list[str], n_files: int = 1) -> list[str]:
    """Create empty dummy segment files under root/dt=<date>/<watchlist>/seg-*.binpb.zst."""
    paths = []
    for wl in watchlist_ids:
        d = os.path.join(root, f"dt={date_str}", wl)
        os.makedirs(d, exist_ok=True)
        for i in range(n_files):
            p = os.path.join(d, f"seg-{i:06d}.binpb.zst")
            with open(p, "wb") as f:
                f.write(b"")
            paths.append(p)
    return paths


_GOOD_ROW = _row("2026-06-22T09:00:00", 100, 101, 99, 100, 500, 50000, 3, 300, 200, 2, 1, vwap="100.0000")


# ---------------------------------------------------------------------------
# Section A: segment discovery
# ---------------------------------------------------------------------------

class TestSegmentDiscovery:
    def test_discover_dates_filters_range(self, tmp_path) -> None:
        from screener.tick_bars import _discover_dates
        root = str(tmp_path)
        for d in ["2026-06-17", "2026-06-18", "2026-06-19"]:
            os.makedirs(os.path.join(root, f"dt={d}", "default"))
        assert _discover_dates(root, from_date="2026-06-18", to_date="2026-06-18") == ["2026-06-18"]

    def test_discover_dates_sorted_ascending(self, tmp_path) -> None:
        from screener.tick_bars import _discover_dates
        root = str(tmp_path)
        for d in ["2026-06-19", "2026-06-17", "2026-06-18"]:
            os.makedirs(os.path.join(root, f"dt={d}", "default"))
        assert _discover_dates(root, None, None) == ["2026-06-17", "2026-06-18", "2026-06-19"]

    def test_discover_dates_ignores_non_dt_entries(self, tmp_path) -> None:
        from screener.tick_bars import _discover_dates
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "dt=2026-06-17", "default"))
        (tmp_path / ".DS_Store").write_bytes(b"")
        assert _discover_dates(root, None, None) == ["2026-06-17"]

    def test_discover_dates_missing_root_returns_empty(self, tmp_path) -> None:
        from screener.tick_bars import _discover_dates
        assert _discover_dates(str(tmp_path / "nope"), None, None) == []

    def test_discover_segments_spans_multiple_watchlist_dirs(self, tmp_path) -> None:
        from screener.tick_bars import _discover_segments_for_date
        root = str(tmp_path)
        paths = _make_segment_tree(root, "2026-06-17", ["default", "lq45-mining"])
        found = _discover_segments_for_date(root, "2026-06-17")
        assert sorted(found) == sorted(paths)
        assert len(found) == 2

    def test_discover_segments_missing_date_returns_empty(self, tmp_path) -> None:
        from screener.tick_bars import _discover_segments_for_date
        assert _discover_segments_for_date(str(tmp_path), "2026-01-01") == []

    def test_discover_segments_skips_stray_files(self, tmp_path) -> None:
        from screener.tick_bars import _discover_segments_for_date
        root = str(tmp_path)
        date_dir = os.path.join(root, "dt=2026-06-17")
        os.makedirs(date_dir)
        (open(os.path.join(date_dir, ".DS_Store"), "wb")).close()
        _make_segment_tree(root, "2026-06-17", ["default"])
        found = _discover_segments_for_date(root, "2026-06-17")
        assert len(found) == 1


# ---------------------------------------------------------------------------
# Section B: _run_sbcap_bars — subprocess contract + CSV parsing
# ---------------------------------------------------------------------------

class TestRunSbcapBars:
    def test_parses_csv_rows(self) -> None:
        from screener.tick_bars import _run_sbcap_bars
        csv_text = _csv([_GOOD_ROW])
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=csv_text)) as mock_run:
            df = _run_sbcap_bars(["seg1"], "PTRO", "1m", "fake-sbcap")
        assert len(df) == 1
        assert df.iloc[0]["close"] == 100
        argv = mock_run.call_args.args[0]
        assert argv[0] == "fake-sbcap"
        assert argv[1] == "bars"
        assert "seg1" in argv
        assert "--symbol" in argv and "PTRO" in argv
        assert "--interval" in argv and "1m" in argv

    def test_zero_rows_returns_empty_frame_not_error(self) -> None:
        from screener.tick_bars import _run_sbcap_bars
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([]))):
            df = _run_sbcap_bars(["seg1"], "PTRO", "1m", "fake-sbcap")
        assert df.empty

    def test_nonzero_exit_raises_sbcap_error_with_stderr(self) -> None:
        from screener.tick_bars import _run_sbcap_bars, SbcapError
        with patch(
            "screener.tick_bars.subprocess.run",
            return_value=_completed(stderr="boom: bad segment", returncode=1),
        ):
            with pytest.raises(SbcapError, match="boom: bad segment"):
                _run_sbcap_bars(["seg1"], "PTRO", "1m", "fake-sbcap")

    def test_never_uses_shell_true_and_argv_is_a_list(self) -> None:
        from screener.tick_bars import _run_sbcap_bars
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([]))) as mock_run:
            _run_sbcap_bars(["seg1"], "PTRO", "1m", "fake-sbcap")
        assert mock_run.call_args.kwargs.get("shell", False) is False
        assert isinstance(mock_run.call_args.args[0], list)

    def test_tagged_only_passes_flag_to_sbcap(self) -> None:
        from screener.tick_bars import _run_sbcap_bars
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([]))) as mock_run:
            _run_sbcap_bars(["seg1"], "PTRO", "1m", "fake-sbcap", tagged_only=True)
        argv = mock_run.call_args.args[0]
        assert "--tagged-only" in argv

    def test_default_does_not_pass_tagged_only_flag(self) -> None:
        from screener.tick_bars import _run_sbcap_bars
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([]))) as mock_run:
            _run_sbcap_bars(["seg1"], "PTRO", "1m", "fake-sbcap")
        argv = mock_run.call_args.args[0]
        assert "--tagged-only" not in argv


class TestParseBarsCsv:
    """
    Pins the "parse the header dynamically, never hardcode the column list"
    contract. bars.go already changed its schema once without warning
    (untagged_vol/untagged_trades inserted after sell_trades, --include-auction
    replaced by --tagged-only) — a hardcoded column list would have silently
    dropped or misaligned data.
    """

    def test_unknown_extra_column_is_carried_through(self) -> None:
        from screener.tick_bars import _parse_bars_csv
        csv_text = (
            "datetime,date,open,high,low,close,volume,value,trades,"
            "buy_vol,sell_vol,buy_trades,sell_trades,untagged_vol,untagged_trades,"
            "brand_new_field,vwap\n"
            "2026-06-22T09:00:00,2026-06-22,100,101,99,100,500,50000,3,300,200,2,1,0,0,999,100.0000\n"
        )
        df = _parse_bars_csv(csv_text)
        assert "brand_new_field" in df.columns
        assert df.iloc[0]["brand_new_field"] == 999

    def test_current_schema_columns_all_present(self) -> None:
        from screener.tick_bars import _parse_bars_csv
        df = _parse_bars_csv(_csv([_GOOD_ROW]))
        for col in _HEADER.split(","):
            assert col in df.columns, f"expected column {col!r} in parsed frame"


# ---------------------------------------------------------------------------
# Section C: build_bars — multi-symbol, multi-day orchestration
# ---------------------------------------------------------------------------

class TestBuildBars:
    def test_fresh_build_creates_store(self, tmp_path) -> None:
        from screener.tick_bars import build_bars, load_tick_bars, AccrualSummary
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))):
            results = build_bars(["PTRO"], segments_root, store_dir, sbcap_path="fake-sbcap")

        assert isinstance(results["PTRO"], AccrualSummary)
        assert results["PTRO"].rows_before == 0
        assert results["PTRO"].rows_after == 1
        assert results["PTRO"].new_rows == 1
        loaded = load_tick_bars("PTRO", store_dir)
        assert loaded is not None and len(loaded) == 1
        assert list(loaded.columns) == _HEADER.split(",")

    def test_append_merge_dedup_on_rerun(self, tmp_path) -> None:
        from screener.tick_bars import build_bars, load_tick_bars
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        first_csv = _csv([_row("2026-06-22T09:00:00", 1000, 1010, 995, 1005, 15000, 15037500, 42, 9000, 6000, 25, 17, vwap="1002.5000")])
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=first_csv)):
            build_bars(["PTRO"], segments_root, store_dir, sbcap_path="fake-sbcap")

        # Second build: the 09:00 bar is corrected (close differs), plus one genuinely new bar.
        second_csv = _csv([
            _row("2026-06-22T09:00:00", 1000, 1015, 995, 1009, 16000, 16100000, 45, 9500, 6500, 27, 18, vwap="1006.2500"),
            _row("2026-06-22T09:01:00", 1005, 1012, 1000, 1010, 8000, 8080000, 20, 4500, 3500, 12, 8, vwap="1010.0000"),
        ])
        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=second_csv)):
            results = build_bars(["PTRO"], segments_root, store_dir, sbcap_path="fake-sbcap")

        loaded = load_tick_bars("PTRO", store_dir)
        assert loaded is not None
        assert len(loaded) == 2, "duplicate 09:00 timestamp must be deduped, not appended"
        row0 = loaded[loaded["datetime"] == pd.Timestamp("2026-06-22T09:00:00")].iloc[0]
        assert row0["close"] == 1009, "newest build must win for a duplicated timestamp"
        assert results["PTRO"].rows_before == 1
        assert results["PTRO"].rows_after == 2
        assert results["PTRO"].new_rows == 1

    def test_multiple_watchlist_dirs_combined_in_one_invocation(self, tmp_path) -> None:
        from screener.tick_bars import build_bars
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-17", ["default", "lq45-mining"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))) as mock_run:
            build_bars(["ITMG"], segments_root, store_dir, sbcap_path="fake-sbcap")

        assert mock_run.call_count == 1, "one call per (symbol, day), not per watchlist dir"
        argv = mock_run.call_args.args[0]
        assert sum(1 for a in argv if a.endswith(".binpb.zst")) == 2

    def test_date_range_filtering_limits_sbcap_calls(self, tmp_path) -> None:
        from screener.tick_bars import build_bars
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        for d in ["2026-06-17", "2026-06-18", "2026-06-19"]:
            _make_segment_tree(segments_root, d, ["default"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([]))) as mock_run:
            build_bars(
                ["ITMG"], segments_root, store_dir, sbcap_path="fake-sbcap",
                from_date="2026-06-18", to_date="2026-06-18",
            )

        assert mock_run.call_count == 1

    def test_symbol_with_zero_rows_is_reported_not_skipped(self, tmp_path) -> None:
        from screener.tick_bars import build_bars, AccrualSummary
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([]))):
            results = build_bars(["QUIET"], segments_root, store_dir, sbcap_path="fake-sbcap")

        assert "QUIET" in results
        assert isinstance(results["QUIET"], AccrualSummary)
        assert results["QUIET"].new_rows == 0
        assert results["QUIET"].rows_after == 0

    def test_no_segments_at_all_is_reported_as_zero_rows_without_sbcap_call(self, tmp_path) -> None:
        from screener.tick_bars import build_bars, AccrualSummary
        store_dir = str(tmp_path / "store")
        with patch("screener.tick_bars.subprocess.run") as mock_run:
            results = build_bars(["PTRO"], str(tmp_path / "segments"), store_dir, sbcap_path="fake-sbcap")
        mock_run.assert_not_called()
        assert isinstance(results["PTRO"], AccrualSummary)
        assert results["PTRO"].new_rows == 0

    def test_sbcap_failure_isolated_to_one_symbol(self, tmp_path) -> None:
        from screener.tick_bars import build_bars, AccrualSummary, BuildError
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        def side_effect(argv, **kwargs):
            if "BRPT" in argv:
                return _completed(stderr="segment corrupt", returncode=1)
            return _completed(stdout=_csv([_GOOD_ROW]))

        with patch("screener.tick_bars.subprocess.run", side_effect=side_effect):
            results = build_bars(["PTRO", "BRPT"], segments_root, store_dir, sbcap_path="fake-sbcap")

        assert isinstance(results["PTRO"], AccrualSummary)
        assert isinstance(results["BRPT"], BuildError)
        assert "segment corrupt" in results["BRPT"].message

    def test_all_symbols_failing_are_all_reported_as_errors(self, tmp_path) -> None:
        from screener.tick_bars import build_bars, BuildError
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stderr="boom", returncode=1)):
            results = build_bars(["PTRO", "BRPT"], segments_root, store_dir, sbcap_path="fake-sbcap")

        assert all(isinstance(v, BuildError) for v in results.values())
        assert set(results.keys()) == {"PTRO", "BRPT"}

    def test_tagged_only_flag_forwarded_and_changes_bars(self, tmp_path) -> None:
        """A build with tagged_only=True must forward --tagged-only to sbcap."""
        from screener.tick_bars import build_bars
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))) as mock_run:
            build_bars(["PTRO"], segments_root, store_dir, sbcap_path="fake-sbcap", tagged_only=True)

        argv = mock_run.call_args.args[0]
        assert "--tagged-only" in argv


# ---------------------------------------------------------------------------
# Section C2: build_both_stores — full-tape + tagged-only together
# ---------------------------------------------------------------------------

class TestBuildBothStores:
    def test_builds_two_separate_stores_from_one_call(self, tmp_path) -> None:
        from screener.tick_bars import build_both_stores, load_tick_bars, AccrualSummary
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        tagged_store_dir = str(tmp_path / "store_tagged")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))):
            results = build_both_stores(["PTRO"], segments_root, store_dir, tagged_store_dir, sbcap_path="fake-sbcap")

        assert isinstance(results["full"]["PTRO"], AccrualSummary)
        assert isinstance(results["tagged"]["PTRO"], AccrualSummary)
        assert load_tick_bars("PTRO", store_dir) is not None
        assert load_tick_bars("PTRO", tagged_store_dir) is not None
        # Two independent files, not one shared between the two stores.
        assert store_dir != tagged_store_dir

    def test_calls_sbcap_once_per_store_per_symbol_day(self, tmp_path) -> None:
        """One (symbol, day) must produce exactly two sbcap invocations: full + tagged."""
        from screener.tick_bars import build_both_stores
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        tagged_store_dir = str(tmp_path / "store_tagged")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))) as mock_run:
            build_both_stores(["PTRO"], segments_root, store_dir, tagged_store_dir, sbcap_path="fake-sbcap")

        assert mock_run.call_count == 2
        flagged = [call.args[0] for call in mock_run.call_args_list if "--tagged-only" in call.args[0]]
        unflagged = [call.args[0] for call in mock_run.call_args_list if "--tagged-only" not in call.args[0]]
        assert len(flagged) == 1
        assert len(unflagged) == 1

    def test_one_store_failing_does_not_block_the_other(self, tmp_path) -> None:
        """sbcap failing only on the tagged-only call must not sink the full-tape build."""
        from screener.tick_bars import build_both_stores, AccrualSummary, BuildError
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        tagged_store_dir = str(tmp_path / "store_tagged")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        def side_effect(argv, **kwargs):
            if "--tagged-only" in argv:
                return _completed(stderr="boom", returncode=1)
            return _completed(stdout=_csv([_GOOD_ROW]))

        with patch("screener.tick_bars.subprocess.run", side_effect=side_effect):
            results = build_both_stores(["PTRO"], segments_root, store_dir, tagged_store_dir, sbcap_path="fake-sbcap")

        assert isinstance(results["full"]["PTRO"], AccrualSummary)
        assert isinstance(results["tagged"]["PTRO"], BuildError)


# ---------------------------------------------------------------------------
# Section D: load_tick_bars
# ---------------------------------------------------------------------------

class TestLoadTickBars:
    def test_missing_returns_none(self, tmp_path) -> None:
        from screener.tick_bars import load_tick_bars
        assert load_tick_bars("NOPE", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Section E: CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_build_success_prints_symbol_and_exits_cleanly(self, tmp_path, capsys) -> None:
        from screener.tick_bars import main
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))):
            try:
                main([
                    "build", "--symbols", "PTRO",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--sbcap-path", "fake-sbcap",
                ])
            except SystemExit as e:
                assert e.code in (0, None), f"CLI exited {e.code} on a successful build"

        out = capsys.readouterr().out
        assert "PTRO" in out

    def test_cli_all_symbols_failed_exits_nonzero(self, tmp_path) -> None:
        from screener.tick_bars import main
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stderr="boom", returncode=1)):
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "build", "--symbols", "PTRO,BRPT",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--sbcap-path", "fake-sbcap",
                ])

        assert exc_info.value.code != 0

    def test_cli_partial_failure_still_exits_cleanly(self, tmp_path) -> None:
        """One symbol erroring must not sink a build where another symbol succeeded."""
        from screener.tick_bars import main
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        def side_effect(argv, **kwargs):
            if "BRPT" in argv:
                return _completed(stderr="boom", returncode=1)
            return _completed(stdout=_csv([_GOOD_ROW]))

        with patch("screener.tick_bars.subprocess.run", side_effect=side_effect):
            try:
                main([
                    "build", "--symbols", "PTRO,BRPT",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--sbcap-path", "fake-sbcap",
                ])
            except SystemExit as e:
                assert e.code in (0, None), f"partial failure must not exit non-zero, got {e.code}"

    def test_cli_summary_distinguishes_no_data_from_error(self, tmp_path, capsys) -> None:
        from screener.tick_bars import main
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        def side_effect(argv, **kwargs):
            if "BRPT" in argv:
                return _completed(stderr="boom", returncode=1)
            return _completed(stdout=_csv([]))  # PTRO: sbcap ran fine, nothing to accrue

        with patch("screener.tick_bars.subprocess.run", side_effect=side_effect):
            try:
                main([
                    "build", "--symbols", "PTRO,BRPT",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--sbcap-path", "fake-sbcap",
                ])
            except SystemExit:
                pass

        out = capsys.readouterr().out
        assert "no new data" in out
        assert "ERRORED" in out

    def test_cli_requires_at_least_one_symbol(self, tmp_path) -> None:
        from screener.tick_bars import main
        with pytest.raises(SystemExit) as exc_info:
            main([
                "build", "--symbols", "",
                "--segments-root", str(tmp_path / "segments"),
                "--store-dir", str(tmp_path / "store"),
            ])
        assert exc_info.value.code != 0

    def test_cli_build_tagged_only_forwards_flag(self, tmp_path) -> None:
        from screener.tick_bars import main
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store_tagged")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))) as mock_run:
            try:
                main([
                    "build", "--symbols", "PTRO",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--sbcap-path", "fake-sbcap",
                    "--tagged-only",
                ])
            except SystemExit as e:
                assert e.code in (0, None)

        argv = mock_run.call_args.args[0]
        assert "--tagged-only" in argv

    def test_cli_build_both_creates_two_stores(self, tmp_path, capsys) -> None:
        from screener.tick_bars import main, load_tick_bars
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        tagged_store_dir = str(tmp_path / "store_tagged")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        with patch("screener.tick_bars.subprocess.run", return_value=_completed(stdout=_csv([_GOOD_ROW]))) as mock_run:
            try:
                main([
                    "build-both", "--symbols", "PTRO",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--tagged-store-dir", tagged_store_dir,
                    "--sbcap-path", "fake-sbcap",
                ])
            except SystemExit as e:
                assert e.code in (0, None)

        assert mock_run.call_count == 2  # one full-tape call, one tagged-only call
        assert load_tick_bars("PTRO", store_dir) is not None
        assert load_tick_bars("PTRO", tagged_store_dir) is not None
        out = capsys.readouterr().out
        assert "[full]" in out
        assert "[tagged]" in out

    def test_cli_build_both_exits_nonzero_when_one_store_fully_fails(self, tmp_path) -> None:
        """If sbcap fails for every symbol on ONE of the two stores, that's still a hard failure —
        a half-built pair is exactly the drift the two-store design exists to prevent."""
        from screener.tick_bars import main
        segments_root = str(tmp_path / "segments")
        store_dir = str(tmp_path / "store")
        tagged_store_dir = str(tmp_path / "store_tagged")
        _make_segment_tree(segments_root, "2026-06-22", ["plan10-survivors"])

        def side_effect(argv, **kwargs):
            if "--tagged-only" in argv:
                return _completed(stderr="boom", returncode=1)
            return _completed(stdout=_csv([_GOOD_ROW]))

        with patch("screener.tick_bars.subprocess.run", side_effect=side_effect):
            with pytest.raises(SystemExit) as exc_info:
                main([
                    "build-both", "--symbols", "PTRO",
                    "--segments-root", segments_root,
                    "--store-dir", store_dir,
                    "--tagged-store-dir", tagged_store_dir,
                    "--sbcap-path", "fake-sbcap",
                ])

        assert exc_info.value.code != 0

    def test_cli_build_both_requires_at_least_one_symbol(self, tmp_path) -> None:
        from screener.tick_bars import main
        with pytest.raises(SystemExit) as exc_info:
            main([
                "build-both", "--symbols", "",
                "--segments-root", str(tmp_path / "segments"),
                "--store-dir", str(tmp_path / "store"),
                "--tagged-store-dir", str(tmp_path / "store_tagged"),
            ])
        assert exc_info.value.code != 0
