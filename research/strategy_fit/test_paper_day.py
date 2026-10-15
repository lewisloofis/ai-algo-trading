"""paper_day: trading-day gate, paper-only enforcement, env hygiene, timing, results message."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from lib.market_calendar import CALENDAR
from strategy_fit import emitter_specs as es
from strategy_fit import paper_day as pd_
from strategy_fit import target_emitter as te
from strategy_fit.test_target_emitter import _bars, _closes_with_up_rows

LAST = date(2026, 8, 14)
AS_OF = CALENDAR.next_trading_day(LAST)          # Tue 2026-08-18 (08-17 holiday)
SPEC = replace(es.SPECS["wifi_momo_h5"], strategy_id="paper_x", symbols=("AAAA",))
REG = {"paper_x": SPEC, "cand_x": replace(SPEC, strategy_id="cand_x", status=es.STATUS_CANDIDATE)}
ENV = {"SB_ACCESS_TOKEN": "x", "SB_TRADING_ACCESS_TOKEN": "t", "ALGO_LIVE": "true", "ALGO_CONFIRM": "yes", "PATH": "/bin"}


def _now(day: date, hms: str = "08:50:00"):
    parts = [int(x) for x in hms.split(":")] + [0, 0]
    return lambda: datetime(day.year, day.month, day.day, parts[0], parts[1], parts[2], tzinfo=te.WIB)


class Recorder:
    def __init__(self, rc: int = 0):
        self.calls, self.rc = [], rc

    def __call__(self, cmd, env=None, cwd=None):
        self.calls.append((cmd, env, cwd))
        return subprocess.CompletedProcess(cmd, self.rc)


@pytest.fixture
def world(tmp_path):
    data, bins = tmp_path / "daily", tmp_path / "bin"
    data.mkdir()
    bins.mkdir()
    _bars(LAST, _closes_with_up_rows(40, {38}, start=100.0)).to_parquet(data / "AAAA.parquet")
    for n in ("live-session", "strategy-run"):
        (bins / n).write_text("#!/bin/sh\n")
    return dict(tmp=tmp_path, data=data, bin=bins / "live-session", runs=tmp_path / "runs")


def _go(world, *extra, strategy="paper_x", as_of=AS_OF, now=None, env=ENV, rec=None):
    rec = rec or Recorder()
    rc = pd_.run(["--strategy", strategy, "--as-of", as_of.isoformat(), "--runs-dir", str(world["runs"]),
                  "--data-dir", str(world["data"]), *extra],
                 env=env, now=now or _now(AS_OF), runner=rec, registry=REG,
                 repo_root=world["tmp"], live_session_bin=world["bin"])
    return rc, rec


def test_trading_day_emits_then_runs_live_session_paper(world, capsys):
    rc, rec = _go(world)
    out = capsys.readouterr().out
    assert rc == 0 and len(rec.calls) == 1
    cmd, env, cwd = rec.calls[0]
    assert cmd[0] == str(world["bin"]) and cwd == str(world["tmp"])
    assert "--sink" in cmd and cmd[cmd.index("--sink") + 1] == "paper" and cmd[cmd.index("--source") + 1] == "live"
    assert "--account" not in cmd
    assert (world["runs"] / f"target_paper_x_{AS_OF}.json").exists()
    for want in ("paper-day results", "-paper-a[0-9]*.db", "make paper-scorecard STRATEGY=paper_x"):
        assert want in out


def test_child_env_cannot_arm_or_trade(world):
    _, rec = _go(world)
    env = rec.calls[0][1]
    assert "SB_ACCESS_TOKEN" in env
    for k in ("ALGO_LIVE", "ALGO_CONFIRM", "SB_TRADING_ACCESS_TOKEN"):
        assert k not in env


def test_non_trading_day_is_a_clean_exit_with_a_message(world, capsys):
    sat = date(2026, 8, 22)
    rc, rec = _go(world, as_of=sat, now=_now(sat))
    assert rc == 0 and not rec.calls
    assert "not an IDX trading day" in capsys.readouterr().out
    assert not world["runs"].exists()


def test_holiday_is_a_clean_exit(world, capsys):
    hol = date(2026, 8, 17)
    rc, rec = _go(world, as_of=hol, now=_now(hol))
    assert rc == 0 and not rec.calls and "not an IDX trading day" in capsys.readouterr().out


def test_a_candidate_spec_is_refused(world, capsys):
    rc, rec = _go(world, strategy="cand_x")
    assert rc == te.EXIT_RETIRED and not rec.calls and "only status 'paper'" in capsys.readouterr().err


def test_missing_access_token_is_refused_by_name_without_echoing_values(world, capsys):
    rc, rec = _go(world, env={"PATH": "/bin"})
    err = capsys.readouterr().err
    assert rc == te.EXIT_NO_TOKEN and not rec.calls and "SB_ACCESS_TOKEN" in err


def test_too_late_for_rebalance_at_says_the_start_deadline(world, capsys):
    rc, rec = _go(world, now=_now(AS_OF, "09:29:00"))
    err = capsys.readouterr().err
    assert rc == pd_.EXIT_TOO_LATE and not rec.calls and "before 09:29:00 WIB" in err


def test_one_second_before_the_deadline_is_allowed(world):
    rc, rec = _go(world, now=_now(AS_OF, "09:28:59"))
    assert rc == 0 and rec.calls


def test_live_session_exit_code_is_propagated(world):
    rc, _ = _go(world, rec=Recorder(rc=1))
    assert rc == 1


def test_missing_binaries_point_at_make_build_strategy(world, capsys):
    world["bin"].unlink()
    rc, rec = _go(world)
    assert rc == te.EXIT_DATA_ERROR and "make build-strategy" in capsys.readouterr().err


def test_emit_failure_stops_before_live_session(world):
    (world["data"] / "AAAA.parquet").unlink()
    rc, rec = _go(world)
    assert rc == te.EXIT_DATA_ERROR and not rec.calls
