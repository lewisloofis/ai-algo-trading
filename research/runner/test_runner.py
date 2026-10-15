"""Calendar/skip logic, preflight, lock idempotency, watchdog, modes/arm file, alerts, post-close, dry-run."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from runner import alerts, daily, guard, modes, preflight as pf, session, state, supervise
from runner.config import WIB, at
from runner.conftest import (CAND_SPEC, DAY, PAPER_SPEC, PREV, REGISTRY, RETIRED_SPEC, FakeClock, FakeExecutor,
                             jwt, write_bars, write_status)
from runner.executor import Executor
from runner.postclose import post_close

SAT = date(2026, 10, 17)


# ---- modes + arm file --------------------------------------------------------------------------

def test_modes_without_a_live_gate_are_paper_or_skip_only():
    """No gate supplied (preflight, post-close, plan listings): a candidate can never be LIVE."""
    assert modes.decide_mode(PAPER_SPEC).mode == modes.PAPER
    assert modes.decide_mode(RETIRED_SPEC).mode == modes.SKIP
    cand = modes.decide_mode(CAND_SPEC)
    assert cand.mode == modes.SKIP and cand.token is None


@pytest.mark.parametrize("doc", [
    {"date": "2026-10-14", "strategies": {"cand_x": {"max_idr": 5}}},     # wrong date
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": -1}}},
    {"date": str(DAY), "strategies": {}},
    {"date": str(DAY), "strategies": {"cand_x": {"max_idr": True}}},
])
def test_invalid_arm_file(tmp_path, doc):
    (tmp_path / "arm").mkdir()
    (tmp_path / "arm" / f"ARM-{DAY}.json").write_text(json.dumps(doc))
    arm = modes.read_arm_file(tmp_path, DAY)
    assert arm.present and not arm.valid and arm.problems


def test_absent_arm_file(tmp_path):
    assert not modes.read_arm_file(tmp_path, DAY).present


def test_arm_file_state_is_logged(cfg, alerter, tmp_path):
    (cfg.runs_dir / "arm").mkdir(parents=True)
    (cfg.runs_dir / "arm" / f"ARM-{DAY}.json").write_text(json.dumps(
        {"date": str(DAY), "strategies": {"cand_x": {"max_idr": 1}}}))
    from runner.strategies import select_and_log
    paper = select_and_log(REGISTRY, alerter, cfg.runs_dir, DAY)
    assert [s.strategy_id for s in paper] == ["paper_x"]
    log = cfg.log_file.read_text()
    assert "arm file ARM-2026-10-15.json: valid" in log and "skip dead_x" in log


# ---- preflight ---------------------------------------------------------------------------------

def test_preflight_not_trading_day(cfg, now):
    res = pf.run_preflight(cfg, SAT, now.timestamp())
    assert not res.trading_day and not res.ok


def test_preflight_all_good(cfg, now):
    write_bars(cfg, "AAAA", PREV)
    res = pf.run_preflight(cfg, DAY, now.timestamp())
    assert res.ok, pf.format_result(res)


def test_preflight_zero_volume_placeholder_fails(cfg, now):
    write_bars(cfg, "AAAA", PREV, volume=0.0)
    res = pf.run_preflight(cfg, DAY, now.timestamp())
    assert not res.ok and any("zero volume" in c.detail for c in res.failures)


def test_preflight_stale_bar_and_missing_bar_fail(cfg, now):
    write_bars(cfg, "AAAA", PREV - timedelta(days=1))
    assert any("older" in c.detail for c in pf.run_preflight(cfg, DAY, now.timestamp()).failures)
    (cfg.daily_dir / "AAAA.parquet").unlink()
    assert any("no stored" in c.detail for c in pf.run_preflight(cfg, DAY, now.timestamp()).failures)


def test_preflight_missing_and_expiring_token_fail(cfg, now):
    write_bars(cfg, "AAAA", PREV)
    missing = replace(cfg, env={})
    assert not pf.run_preflight(missing, DAY, now.timestamp()).ok
    soon = replace(cfg, env={**cfg.env, "SB_ACCESS_TOKEN": jwt(now.timestamp() + 600)})
    res = pf.run_preflight(soon, DAY, now.timestamp())
    assert not res.ok and any("expires in 0.2h" in c.detail for c in res.failures)


def test_preflight_never_prints_token(cfg, now):
    write_bars(cfg, "AAAA", PREV)
    out = pf.format_result(pf.run_preflight(cfg, DAY, now.timestamp()))
    assert cfg.env["SB_ACCESS_TOKEN"] not in out and "expires in 5.0h" in out


def test_preflight_opaque_token_is_a_warning_not_failure(cfg, now):
    write_bars(cfg, "AAAA", PREV)
    opaque = replace(cfg, env={**cfg.env, "SB_ACCESS_TOKEN": "opaque"})
    res = pf.run_preflight(opaque, DAY, now.timestamp())
    assert res.ok and res.warnings


def test_preflight_kill_switch_fails(cfg, now):
    write_bars(cfg, "AAAA", PREV)
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    cfg.kill_file.write_text("")
    res = pf.run_preflight(cfg, DAY, now.timestamp())
    assert not res.ok and any(c.name == "kill switch" for c in res.failures)


def test_preflight_phase_alerts_and_skips_on_failure(cfg, clock, alerter, post):
    assert daily.run_preflight_phase(cfg, DAY, clock, alerter) is False     # no bars stored
    assert post.posts and "preflight FAILED" in post.posts[0][1]["text"]
    assert state.load_state(cfg.state_path(DAY), str(DAY))["preflight"]["ok"] is False


# ---- lock idempotency --------------------------------------------------------------------------

def test_lock_second_holder_refused_and_stale_taken_over(tmp_path):
    p = tmp_path / "x.lock"
    with state.phase_lock(p, alive=lambda pid: True) as first:
        assert first
        p.write_text("99999")                                  # pretend another live process owns it
        with state.phase_lock(p, alive=lambda pid: True) as second:
            assert not second
    p.write_text("99999")
    with state.phase_lock(p, alive=lambda pid: False) as took:
        assert took


def test_start_is_idempotent(cfg, clock, alerter, ex):
    assert session.start_sessions(cfg, DAY, clock, ex, alerter) == 0
    assert len(ex.spawns) == 1 and ex.spawns[0][0][4] == "paper_x"
    session.start_sessions(cfg, DAY, clock, ex, alerter)
    assert len(ex.spawns) == 1                                  # no double start


def test_start_only_paper_and_env_is_stripped(cfg, clock, alerter, ex):
    cfg = replace(cfg, env={**cfg.env, "ALGO_LIVE": "true", "ALGO_CONFIRM": "I_UNDERSTAND_LIVE_TRADING",
                            "SB_TRADING_ACCESS_TOKEN": "trading-token-value"})
    session.start_sessions(cfg, DAY, clock, ex, alerter)
    started = {a[4] for a, _e, _p in ex.spawns}
    assert started == {"paper_x"}                               # candidate and retired never start
    for argv, env in [(a, e) for a, e, _ in ex.spawns] + ex.runs:
        assert "ALGO_LIVE" not in env and "SB_TRADING_ACCESS_TOKEN" not in env and "ALGO_CONFIRM" not in env
        guard.validate_argv(argv, REGISTRY)


def test_start_refused_with_kill_switch(cfg, clock, alerter, ex):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    cfg.kill_file.write_text("")
    assert session.start_sessions(cfg, DAY, clock, ex, alerter) == 1 and not ex.spawns


def test_start_build_failure_alerts(cfg, clock, alerter, post):
    ex = FakeExecutor(run_rc=2)
    assert session.start_sessions(cfg, DAY, clock, ex, alerter) == 1 and not ex.spawns and post.posts


# ---- heartbeat / watchdog ----------------------------------------------------------------------

def test_assess_heartbeat():
    now = 2_000_000_000.0
    mk = lambda out, age: {"outcome": out, "wall_clock_ns": int((now - age) * 1e9)}  # noqa: E731
    assert session.assess_heartbeat(None, now, 120) == ("none", None)
    assert session.assess_heartbeat(mk("running", 30), now, 120)[0] == "running"
    assert session.assess_heartbeat(mk("running", 121), now, 120)[0] == "stale"
    assert session.assess_heartbeat({"outcome": "running"}, now, 120)[0] == "stale"
    assert session.assess_heartbeat(mk("complete", 999), now, 120)[0] == "complete"
    assert session.assess_heartbeat(mk("error", 5), now, 120)[0] == "error"


def _started(cfg, clock, alerter, ex):
    session.start_sessions(cfg, DAY, clock, ex, alerter)
    return ex.spawns[0][2]


def test_watch_alerts_on_stale_status_once(cfg, clock, alerter, ex, post):
    pid = _started(cfg, clock, alerter, ex)
    write_status(cfg, "paper_x", clock.now(), age_s=200)
    cfg = replace(cfg, watch_deadline="08:50")
    session.watch_sessions(cfg, DAY, clock, ex, alerter)
    stale = [p for _u, p in post.posts if "stale" in p["text"]]
    assert len(stale) == 1
    assert ex.interrupts == [pid]                                # deadline: graceful stop


def test_watch_clean_finish_no_alert(cfg, clock, alerter, ex, post):
    _started(cfg, clock, alerter, ex)
    ex.alive_pids.clear()
    write_status(cfg, "paper_x", clock.now(), 5, outcome="complete")
    assert session.watch_sessions(cfg, DAY, clock, ex, alerter) == 0 and not post.posts
    assert state.load_state(cfg.state_path(DAY), str(DAY))["strategies"]["paper_x"]["status"] == "done"


def test_watch_restarts_killed_paper_process_when_no_open_orders(cfg, clock, alerter, ex, post):
    pid = _started(cfg, clock, alerter, ex)
    ex.alive_pids.discard(pid)                                   # died, no rc file
    cfg = replace(cfg, watch_deadline="08:50")
    session.watch_sessions(cfg, DAY, clock, ex, alerter)
    assert len(ex.spawns) == 2 and any("restarted" in p["text"] for _u, p in post.posts)


def _store_with_open_order(cfg):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.runs_dir / f"{DAY}-paper_x-paper-a1.db")
    con.executescript("CREATE TABLE order_intent(order_id TEXT, run_id TEXT);CREATE TABLE fill(order_id TEXT, run_id TEXT);"
                      "INSERT INTO order_intent VALUES('o1','r');")
    con.commit()
    con.close()


def test_watch_does_not_restart_with_open_order(cfg, clock, alerter, ex, post):
    pid = _started(cfg, clock, alerter, ex)
    _store_with_open_order(cfg)
    ex.alive_pids.discard(pid)
    assert session.watch_sessions(cfg, DAY, clock, ex, alerter) == 1
    assert len(ex.spawns) == 1 and any("NOT restarted" in p["text"] for _u, p in post.posts)


def test_watch_does_not_restart_on_a_real_exit_code(cfg, clock, alerter, ex):
    pid = _started(cfg, clock, alerter, ex)
    ex.alive_pids.discard(pid)
    session.rc_path(cfg, DAY, "paper_x").write_text(json.dumps({"rc": 2}))
    assert session.watch_sessions(cfg, DAY, clock, ex, alerter) == 1 and len(ex.spawns) == 1


def test_watch_restart_budget_and_late_restart(cfg, clock, alerter, ex):
    pid = _started(cfg, clock, alerter, ex)
    ex.alive_pids.discard(pid)
    late = replace(cfg, max_restarts=0)
    assert session.watch_sessions(late, DAY, clock, ex, alerter) == 1 and len(ex.spawns) == 1
    # too late: after rebalance_at - 60s
    state_doc = state.load_state(cfg.state_path(DAY), str(DAY))
    state_doc["strategies"]["paper_x"].update(status="started", restarts=0)
    state.save_state(cfg.state_path(DAY), state_doc)
    clock.t = at(DAY, "09:29") + timedelta(seconds=30)
    assert session.watch_sessions(cfg, DAY, clock, ex, alerter) == 1 and len(ex.spawns) == 1


# ---- alerts ------------------------------------------------------------------------------------

def test_alert_goes_to_log_and_telegram_without_tokens(cfg, good_env, post, clock):
    a = alerts.Alerter(cfg.log_file, good_env, post=post, now=clock.now)
    ch = a.send(alerts.ALERT, f"boom {good_env['SB_ACCESS_TOKEN']}", f"chat {good_env['TELEGRAM_CHAT_ID']}")
    assert ch == ["log", "telegram"]
    sent = post.posts[0][1]["text"]            # chat_id must be in the payload; never in the text
    for v in (good_env["SB_ACCESS_TOKEN"], good_env["TELEGRAM_CHAT_ID"]):
        assert v not in sent and v not in cfg.log_file.read_text()


def test_alert_falls_back_to_webhook_and_survives_failures(cfg, clock):
    calls = []

    def post(url, payload):
        calls.append(url)
        if "telegram" in url:
            raise RuntimeError("down " + url)
    env = {"TELEGRAM_BOT_TOKEN": "123456789:" + "B" * 35, "TELEGRAM_CHAT_ID": "42424242",
           "RUNNER_ALERT_WEBHOOK_URL": "https://hooks.example.com/secret-path"}
    a = alerts.Alerter(cfg.log_file, env, post=post, now=clock.now)
    assert a.send(alerts.WARN, "t") == ["log", "webhook"]
    log = cfg.log_file.read_text()
    assert "B" * 35 not in log and "secret-path" not in log


def test_info_stays_local(cfg, good_env, post, clock):
    a = alerts.Alerter(cfg.log_file, good_env, post=post, now=clock.now)
    assert a.send(alerts.INFO, "x") == ["log"] and not post.posts


def test_no_channel_still_logs(cfg, clock):
    a = alerts.Alerter(cfg.log_file, {}, post=lambda *_: 1 / 0, now=clock.now)
    assert a.send(alerts.ALERT, "x") == ["log"] and "x" in cfg.log_file.read_text()


# ---- executor chokepoint -----------------------------------------------------------------------

def test_executor_refuses_live_before_running(tmp_path):
    called = []
    e = Executor(run=lambda *a, **k: called.append(1), popen=lambda *a, **k: called.append(2))
    with pytest.raises(guard.LiveGuardError):
        e.run(["bin/live-session", "--sink", "live"], {}, tmp_path, tmp_path / "l")
    with pytest.raises(guard.LiveGuardError):
        e.spawn(["make", "emit-target"], {}, tmp_path, tmp_path / "l", tmp_path / "rc", "python3")
    assert not called


def test_supervise_records_exit_code_and_refuses_live(tmp_path):
    rc_file = tmp_path / "rc.json"

    class P:
        def wait(self):
            return 7

        def send_signal(self, s):
            pass
    assert supervise.main(["--rc-file", str(rc_file), "--", "make", "dashboard"], popen=lambda c: P()) == 7
    assert json.loads(rc_file.read_text())["rc"] == 7
    assert supervise.main(["--rc-file", str(rc_file), "--", "bin/live-session"], popen=lambda c: P()) == 126


# ---- post-close --------------------------------------------------------------------------------

def test_post_close_runs_steps_skips_recon_without_account_and_is_idempotent(cfg, clock, alerter, ex, post):
    clock.t = at(DAY, "16:20")
    assert post_close(cfg, DAY, clock, ex, alerter) == 0
    names = [a[1] + (" " + a[2] if a[1] == "paper-scorecard" else "") for a, _e in ex.runs]
    assert names == ["refresh-prices", "paper-scorecard STRATEGY=paper_x", "dashboard"]   # recon skipped
    assert "SB_TRADING_ACCESS_TOKEN" not in ex.runs[0][1]
    assert post.posts and "daily summary" in post.posts[-1][1]["text"]
    n = len(ex.runs)
    post_close(cfg, DAY, clock, ex, alerter)
    assert len(ex.runs) == n


def test_post_close_recon_gets_trading_token_only_for_recon(cfg, clock, alerter, ex):
    env = {**cfg.env, "SB_ACCOUNT_NO": "ACC-SYNTH-1", "SB_TRADING_ACCESS_TOKEN": "t-synthetic-value", "ALGO_LIVE": "true"}
    post_close(replace(cfg, env=env), DAY, clock, ex, alerter)
    for argv, e in ex.runs:
        assert ("SB_TRADING_ACCESS_TOKEN" in e) == (argv[1] == "recon")
        assert "ALGO_LIVE" not in e


def test_post_close_failure_alerts_and_continues(cfg, clock, alerter, post):
    ex = FakeExecutor(run_rc={"refresh-prices": 1})
    assert post_close(cfg, DAY, clock, ex, alerter) == 1
    assert len(ex.runs) == 3 and any("refresh-prices failed" in p["text"] for _u, p in post.posts)


def test_post_close_non_trading_day(cfg, clock, alerter, ex):
    assert post_close(cfg, SAT, clock, ex, alerter) == 0 and not ex.runs


# ---- dry run / CLI -----------------------------------------------------------------------------

def test_dry_run_prints_plan_and_executes_nothing(cfg, capsys):
    ex = FakeExecutor()
    rc = daily.main(["all", "--dry-run", "--date", str(DAY), "--no-env-file"], cfg=cfg, executor=ex)
    out = capsys.readouterr().out
    assert rc == 0 and not ex.runs and not ex.spawns
    assert "paper_x" in out and "paper_day" in out and "NOT live cand_x" in out and "refresh-prices" in out
    assert "SB_ACCESS_TOKEN=set" in out and cfg.env["SB_ACCESS_TOKEN"] not in out
    assert not cfg.runner_dir.exists()


def test_dry_run_non_trading_day(cfg, capsys):
    daily.main(["all", "--dry-run", "--date", str(SAT), "--no-env-file"], cfg=cfg)
    assert "NOT an IDX trading day" in capsys.readouterr().out


def test_all_on_weekend_does_nothing(cfg, clock, ex, alerter):
    assert daily.dispatch("all", cfg, SAT, clock, ex, alerter) == 0 and not ex.runs and not ex.spawns


def test_all_full_day_sequence(cfg, clock, ex, alerter):
    write_bars(cfg, "AAAA", PREV)
    ex.alive_pids = set()                                        # session ends immediately
    ex_spawn = ex.spawn

    def spawn_then_finish(*a, **k):
        pid = ex_spawn(*a, **k)
        ex.alive_pids.discard(pid)
        write_status(cfg, "paper_x", clock.now(), 5, outcome="complete")
        return pid
    ex.spawn = spawn_then_finish
    assert daily.dispatch("all", cfg, DAY, clock, ex, alerter) == 0
    assert clock.now() >= at(DAY, "16:20") and len(ex.spawns) == 1
    assert max(clock.sleeps) <= 60                               # waits in bounded sleeps, never spins


def test_env_file_loaded_at_runtime(cfg, capsys, tmp_path):
    (cfg.repo_root / ".env.local").write_text("SB_ACCESS_TOKEN=\nTELEGRAM_CHAT_ID=1\nEXTRA_FLAG=yes\n")
    lean = replace(cfg, env={})
    daily.main(["all", "--dry-run", "--date", str(DAY)], cfg=lean)
    out = capsys.readouterr().out
    assert "TELEGRAM_CHAT_ID=set" in out and "SB_ACCESS_TOKEN=MISSING" in out
