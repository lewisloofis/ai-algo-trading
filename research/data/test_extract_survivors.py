"""
pytest suite for data/extract_survivors.sh.

Runs the real bash script (not a Python port of its logic) against a
synthetic ``segments/`` tree under ``tmp_path``, with a stub ``sbcap``
executable standing in for the real Go binary. The stub never decodes
protobuf; it just records its argv (for order assertions) and emits a
features/trades CSV with exactly one data row per segment path it was
given (for completeness/no-duplication assertions).

This exercises the roadmap W0.2 requirement directly: the collector's
watchlist_id can change (infra/terraform.tfvars), which forks the on-disk
directory name (``segments/dt=<date>/<watchlist_id>/``) without forking the
date. A reader that filters on one watchlist_id silently truncates every
symbol's history to whichever id happened to be active — the real bug this
suite guards against, since `extract_survivors.sh` used to hardcode
``WATCHLIST="plan10-survivors"``.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "extract_survivors.sh"

# Emits a CSV with a header plus one row per segment-path argument (every
# token up to but not including the first `--`-prefixed flag), so a test can
# assert exactly how many segments were folded into one day's CSV — the
# signal for "no duplicate, no missing session" — without decoding real
# protobuf. Also appends the full argv to $SBCAP_LOG, one call per line, so a
# test can assert the ORDER segments were passed in (real `sbcap
# features`/`trades` carry rolling cross-segment state and require
# chronological input).
_STUB_SBCAP = """#!/usr/bin/env bash
set -euo pipefail
echo "$@" >> "$SBCAP_LOG"
echo "datetime,date,x"
n=0
for a in "$@"; do
  case "$a" in
    --*) break ;;
    *) n=$((n+1)) ;;
  esac
done
n=$((n-1))  # drop the subcommand token itself (features/trades)
for ((i = 0; i < n; i++)); do
  echo "2026-01-01T00:00:00,2026-01-01,$i"
done
"""


def _write_stub_sbcap(path: Path) -> None:
    path.write_text(_STUB_SBCAP)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _touch_segment(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _run_script(segments_root: Path, out_dir: Path, sbcap: Path, log: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SBCAP"] = str(sbcap)
    env["SBCAP_LOG"] = str(log)
    log.write_text("")
    return subprocess.run(
        ["bash", str(SCRIPT), str(segments_root), str(out_dir)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def test_spans_watchlist_ids_with_no_gap_at_the_junction(tmp_path: Path) -> None:
    """Two dates under the old watchlist_id, two more under a new one after a
    capture-config change — the series must not fork: all four dates must
    produce output CSVs, not just the ones under the id the script used to
    hardcode."""
    segments_root = tmp_path / "segments"
    _touch_segment(segments_root / "dt=2026-06-22" / "plan10-survivors" / "seg-090000.binpb.zst")
    _touch_segment(segments_root / "dt=2026-06-23" / "plan10-survivors" / "seg-090000.binpb.zst")
    _touch_segment(segments_root / "dt=2026-06-24" / "watchlist-v2" / "seg-090000.binpb.zst")
    _touch_segment(segments_root / "dt=2026-06-25" / "watchlist-v2" / "seg-090000.binpb.zst")

    sbcap = tmp_path / "sbcap"
    _write_stub_sbcap(sbcap)
    out_dir = tmp_path / "out"

    _run_script(segments_root, out_dir, sbcap, tmp_path / "sbcap.log")

    dates = sorted(
        p.name.removesuffix("_features.csv") for p in (out_dir / "PTRO").glob("*_features.csv")
    )
    assert dates == ["2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25"]


def test_same_day_two_watchlist_ids_all_segments_included_once(tmp_path: Path) -> None:
    """A same-day cutover (both the old and new watchlist_id captured on the
    same date) must combine into ONE date CSV containing every segment from
    both ids — not the old id's segments only, and not double-counted."""
    segments_root = tmp_path / "segments"
    _touch_segment(segments_root / "dt=2026-06-26" / "plan10-survivors" / "seg-090000.binpb.zst")
    _touch_segment(segments_root / "dt=2026-06-26" / "watchlist-v2" / "seg-150000.binpb.zst")

    sbcap = tmp_path / "sbcap"
    _write_stub_sbcap(sbcap)
    out_dir = tmp_path / "out"

    _run_script(segments_root, out_dir, sbcap, tmp_path / "sbcap.log")

    features_csv = out_dir / "PTRO" / "2026-06-26_features.csv"
    rows = features_csv.read_text().strip().splitlines()[1:]  # drop header
    assert len(rows) == 2, "expected exactly one row per segment across both watchlist_ids"

    trades_csv = out_dir / "PTRO" / "2026-06-26_trades.csv"
    trade_rows = trades_csv.read_text().strip().splitlines()[1:]
    assert len(trade_rows) == 2


def test_same_day_cutover_segments_ordered_by_capture_time_not_dir_name(tmp_path: Path) -> None:
    """`sbcap features`/`trades` carry rolling state across segment
    boundaries (delta-volume, last book side), so a same-day cutover must
    still feed segments in chronological (capture-time) order. The
    directory named alphabetically LAST here ("zzz-old") captures the
    EARLIER segment; the one named FIRST ("aaa-new") captures the LATER
    one — a sort keyed on directory name instead of segment filename would
    get this backwards."""
    segments_root = tmp_path / "segments"
    _touch_segment(segments_root / "dt=2026-06-26" / "zzz-old" / "seg-090000.binpb.zst")
    _touch_segment(segments_root / "dt=2026-06-26" / "aaa-new" / "seg-150000.binpb.zst")

    sbcap = tmp_path / "sbcap"
    _write_stub_sbcap(sbcap)
    out_dir = tmp_path / "out"
    log = tmp_path / "sbcap.log"

    _run_script(segments_root, out_dir, sbcap, log)

    calls = [line for line in log.read_text().splitlines() if line.startswith("features ")]
    assert calls, "expected at least one `sbcap features` invocation"
    call = calls[0]
    pos_early = call.find("seg-090000")
    pos_late = call.find("seg-150000")
    assert pos_early != -1 and pos_late != -1
    assert pos_early < pos_late, f"segments passed out of chronological order: {call!r}"


def test_missing_segments_root_exits_nonzero(tmp_path: Path) -> None:
    sbcap = tmp_path / "sbcap"
    _write_stub_sbcap(sbcap)
    env = dict(os.environ)
    env["SBCAP"] = str(sbcap)
    env["SBCAP_LOG"] = str(tmp_path / "sbcap.log")

    result = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path / "does-not-exist"), str(tmp_path / "out")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
