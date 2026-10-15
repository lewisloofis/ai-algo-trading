"""Health rows: honest colours, plain sentences, and never a bare 'unknown'."""
from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from dashboard import config, health, inputs
from dashboard.health import AMBER, GREEN, NOT_CHECKED, RED
from dashboard.inputs import HealthOutcome
from dashboard.store import RunHeader

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 14)


def doc(days, exit_code=0, findings=()):
    return HealthOutcome(doc={"days": days, "exit_code": exit_code, "findings": list(findings)})


def ok(d):
    return {"date": d, "status": "ok"}


def run(outcome="complete", error="", sink="live", started=1791990000_000000000, run_id="r1"):
    return RunHeader(run_id, started, None, outcome, error, "live", "", "taker", sink, "roundtrip-goto",
                     "", "", None, None, None, None, None, "x.db")


def test_not_checked_row_names_the_reason_and_the_command():
    r = health.not_checked_row("Market-data collector", "AWS credentials are missing", "make dashboard")
    assert r.state == NOT_CHECKED
    assert r.summary == "Not checked: AWS credentials are missing. Run: make dashboard"


def test_collector_and_s3_rows_are_not_checked_without_a_health_check():
    out = inputs.not_checked("no AWS credentials in this shell")
    for row in (health.collector_row(out), health.s3_row(out, TODAY)):
        assert row.state == NOT_CHECKED
        assert row.summary.startswith("Not checked: no AWS credentials in this shell. Run: ")


def test_collector_green_when_every_trading_day_has_data():
    days = [ok("2026-10-12"), {"date": "2026-10-11", "status": "skipped_non_trading_day"}, ok("2026-10-13")]
    row = health.collector_row(doc(days))
    assert row.state == GREEN and "2 of the last 2" in row.summary


def test_collector_red_on_a_zero_data_week():
    days = [{"date": f"2026-10-{d:02d}", "status": "no_segments", "message": "m"} for d in (8, 9, 12, 13)]
    row = health.collector_row(doc(days, exit_code=2, findings=[{"message": "m"}]))
    assert row.state == RED
    assert "4 of the last 4" in row.summary and "2026-10-13" in row.summary


def test_collector_amber_on_warnings_only():
    row = health.collector_row(doc([ok("2026-10-13")], exit_code=1, findings=[{"message": "binary is 9 days old"}]))
    assert row.state == AMBER and "binary is 9 days old" in row.summary


def test_s3_freshness_colours():
    assert health.s3_row(doc([ok("2026-10-13")]), TODAY).state == GREEN
    assert health.s3_row(doc([ok("2026-10-08")]), TODAY).state == AMBER
    assert health.s3_row(doc([ok("2026-09-20")]), TODAY).state == RED
    none_ok = health.s3_row(doc([{"date": "2026-10-13", "status": "no_segments"}]), TODAY)
    assert none_ok.state == RED


def _daily(tmp_path, name, last):
    pd.DataFrame({"date": ["2026-01-02", last], "close": [1.0, 2.0]}).to_parquet(tmp_path / f"{name}.parquet")


def test_daily_data_green_when_all_current(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == GREEN and "All 3 trading stocks" in row.summary


def test_daily_data_amber_when_a_few_stocks_lag_and_red_when_most_do(tmp_path):
    for n in "ABCDEFGH":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "Z", "2026-09-11")
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == AMBER and "1 of 9" in row.summary and "2026-09-11" in row.summary
    assert row.run_hint
    for n in "ABCDE":
        _daily(tmp_path, n, "2026-09-11")
    assert health.daily_data_row(str(tmp_path), TODAY).state == RED


def test_daily_data_red_when_everything_is_old_and_not_checked_when_absent(tmp_path):
    _daily(tmp_path, "A", "2026-08-01")
    assert health.daily_data_row(str(tmp_path), TODAY).state == RED
    empty = tmp_path / "empty"
    empty.mkdir()
    row = health.daily_data_row(str(empty), TODAY)
    assert row.state == NOT_CHECKED and "Run: " in row.summary


def test_bot_row_cases():
    assert health.bot_row([], None, NOW).state == AMBER
    good = health.bot_row([run()], None, NOW)
    assert good.state == GREEN and "finished normally" in good.summary and "WIB" in good.summary
    bad = health.bot_row([run(outcome="error", error="401 expired")], None, NOW)
    assert bad.state == RED and "401 expired" in bad.summary
    crashed = health.bot_row([run(outcome="running")], None, NOW)
    assert crashed.state == AMBER and "never recorded an ending" in crashed.summary
    old = health.bot_row([run(started=1700000000_000000000)], None, NOW)
    assert old.state == AMBER and "days ago" in old.summary


def test_bot_row_running_with_a_fresh_heartbeat_is_green():
    r = run(outcome="running", run_id="live-1")
    status = {"run_id": "live-1", "wall_clock_ns": int(NOW.timestamp() * 1e9) - 5_000_000_000}
    assert health.bot_row([r], status, NOW).state == GREEN
    stale = {"run_id": "live-1", "wall_clock_ns": int(NOW.timestamp() * 1e9) - 600_000_000_000}
    assert health.bot_row([r], stale, NOW).state == AMBER


def test_no_row_ever_says_a_bare_unknown(tmp_path):
    rows = health.health_rows(inputs.not_checked("x"), str(tmp_path), [], None, NOW)
    assert len(rows) == 4
    assert all("unknown" not in r.summary.lower() for r in rows)
    assert all(r.state in (GREEN, AMBER, RED, NOT_CHECKED) for r in rows)


def test_wib_is_utc_plus_seven():
    assert health.wib(int(datetime(2026, 10, 14, 8, 38, tzinfo=timezone.utc).timestamp() * 1e9)).hour == 15


# --- inputs -------------------------------------------------------------

def test_discover_stores_skips_wal_sidecars_and_the_exclude_list(tmp_path):
    for n in ("a.db", "a.db-wal", "a.db-shm", "2026-10-14-limitprobe.db", "b.db"):
        (tmp_path / n).write_text("")
    s = inputs.discover_stores(str(tmp_path))
    assert [p.split("/")[-1] for p in s.kept] == ["a.db", "b.db"]
    assert [p.split("/")[-1] for p in s.excluded] == ["2026-10-14-limitprobe.db"]
    assert "2026-10-14-limitprobe.db" in config.EXCLUDED_STORES


def test_latest_status_json_is_the_newest_by_wall_clock(tmp_path):
    (tmp_path / "old.status.json").write_text('{"wall_clock_ns": 5}')
    (tmp_path / "new.status.json").write_text('{"wall_clock_ns": 9}')
    (tmp_path / "bad.status.json").write_text("{nope")
    assert inputs.latest_status_json(str(tmp_path)).endswith("new.status.json")
    assert inputs.latest_status_json(str(tmp_path / "missing")) is None


class _Done:
    def __init__(self, out="", err=""):
        self.stdout, self.stderr = out, err


def test_healthcheck_json_on_stdout_is_the_result(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("")
    out = inputs.run_healthcheck(script=str(script), runner=lambda *a, **k: _Done('{"exit_code": 1, "days": []}'))
    assert out.checked and out.doc["exit_code"] == 1


def test_healthcheck_that_cannot_run_gives_a_reason_not_an_exception(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("")
    err = "progress...\nCould not create an S3 client: no AWS credentials"
    out = inputs.run_healthcheck(script=str(script), runner=lambda *a, **k: _Done("", err))
    assert not out.checked and "no AWS credentials" in out.reason


def test_healthcheck_timeout_disabled_and_missing_script(tmp_path):
    script = tmp_path / "s.py"
    script.write_text("")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    assert "did not finish" in inputs.run_healthcheck(script=str(script), runner=boom).reason
    assert "--no-healthcheck" in inputs.run_healthcheck(enabled=False).reason
    assert "not found" in inputs.run_healthcheck(script=str(tmp_path / "nope.py")).reason


def test_saved_healthcheck_file(tmp_path):
    p = tmp_path / "h.json"
    p.write_text('{"exit_code": 0, "days": []}')
    assert inputs.load_healthcheck_file(str(p)).checked
    assert not inputs.load_healthcheck_file(str(tmp_path / "nope.json")).checked


def test_daily_data_ended_stocks_are_not_counted_as_behind(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "DEAD", "2021-07-02")
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == GREEN
    assert "All 3 trading stocks" in row.summary and "1 stock stopped trading" in row.summary


def test_daily_data_lag_inside_ended_window_still_amber(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "LAG", "2026-10-01")
    _daily(tmp_path, "DEAD", "2021-07-02")
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == AMBER and "1 of 4" in row.summary and "stopped trading" in row.summary


def _status(tmp_path, as_of, flags):
    import json
    doc = {"schema_version": 2, "as_of": as_of,
           "symbols": {s: {"in_day_trade": True, "in_trading_limit": True, "is_tradable": f} for s, f in flags.items()}}
    (tmp_path / "universe_status.json").write_text(json.dumps(doc))


def test_daily_data_untradable_stock_is_reported_not_counted_as_behind(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "GREN", "2026-10-09")  # fresh delisting: well inside the 60-day window
    _status(tmp_path, "2026-10-14", {"A": True, "B": True, "C": True, "GREN": False})
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == GREEN
    assert "All 3 trading stocks" in row.summary
    assert "1 stock is no longer tradable (delisting/suspension)" in row.summary
    assert "not checked" not in row.summary.lower()


def test_daily_data_without_status_file_says_tradability_was_not_checked(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "GREN", "2026-10-09")
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == AMBER and "1 of 4" in row.summary
    assert "tradability was not checked" in row.summary.lower()


def test_daily_data_stale_status_file_is_ignored(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "GREN", "2026-10-09")
    _status(tmp_path, "2026-10-01", {"GREN": False})  # 13 days old > STALE_DAYS
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == AMBER and "no longer tradable" not in row.summary
    assert "tradability was not checked" in row.summary.lower()


def test_daily_data_unknown_tradability_is_not_treated_as_untradable(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "GREN", "2026-10-09")
    _status(tmp_path, "2026-10-14", {"GREN": None})
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == AMBER and "1 of 4" in row.summary


def test_daily_data_untradable_and_ended_are_both_reported(tmp_path):
    for n in "ABC":
        _daily(tmp_path, n, "2026-10-14")
    _daily(tmp_path, "GREN", "2026-10-09")
    _daily(tmp_path, "DEAD", "2021-07-02")
    _status(tmp_path, "2026-10-14", {"GREN": False})
    row = health.daily_data_row(str(tmp_path), TODAY)
    assert row.state == GREEN
    assert "no longer tradable" in row.summary and "stopped trading" in row.summary


def test_refresh_hint_needs_no_trading_token():
    assert "SB_BEARER_TOKEN" in health.DAILY_REFRESH_HINT
    assert "SB_TRADING_ACCESS_TOKEN" not in health.DAILY_REFRESH_HINT
