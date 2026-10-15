"""LIVE start, watch (no restart, ever), order-event alerts and redaction, summaries, dry run."""

from __future__ import annotations

import inspect
import json
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from runner import alerts, daily, liveorders, preflight as pf, session, state
from runner.config import at
from runner.conftest import (ACCOUNT, DAY, PREV, FakeExecutor, arm_for, trading_jwt,
                             write_bars, write_live_status, write_live_store)
from runner.guard import LiveGuardError
from runner.postclose import post_close

SID = "cand_x"
BUY = dict(id="rebalance-BBBB-2026-10-15", symbol="BBBB", side="buy", price=4_200, lots=1)


def start(cfg, clock, alerter, ex, preflight_ok=True):
    return session.start_sessions(cfg, DAY, clock, ex, alerter, preflight_ok=preflight_ok)


def entry(cfg):
    return state.load_state(cfg.state_path(DAY), str(DAY))["strategies"][SID]


def texts(post):
    return [p["text"] for _u, p in post.posts]


# ---- start ------------------------------------------------------------------------------------

def test_armed_candidate_starts_one_live_session_beside_paper(live_cfg, clock, live_alerter, ex, post):
    arm_for(live_cfg, SID, 500_000)
    assert start(live_cfg, clock, live_alerter, ex) == 0
    assert len(ex.live_spawns) == 1 and [a[4] for a, _e, _p in ex.spawns] == ["paper_x"]
    spawn = ex.live_spawns[0]
    argv = list(spawn["cmd"].argv)
    assert argv[argv.index("--max-buy-notional-idr") + 1] == "500000" and "--retry" not in argv
    assert "ALGO_LIVE" not in spawn["env"] and "ALGO_CONFIRM" not in spawn["env"]
    assert spawn["preflight_ok"] is True
    e = entry(live_cfg)
    assert e["status"] == "started" and e["live"] is True and e["max_idr"] == 500_000 and e["pid"] == spawn["pid"]
    pushed = " ".join(texts(post))
    assert "LIVE session started" in pushed and "500,000 IDR" in pushed
    assert ACCOUNT not in pushed and ACCOUNT not in live_cfg.log_file.read_text()   # masked everywhere
    assert "[ACCOUNT]" in live_cfg.log_file.read_text()
    for _argv, env in ex.runs:                 # the build step and the like never see arming or the trading token
        assert "ALGO_LIVE" not in env and "SB_TRADING_ACCESS_TOKEN" not in env


def test_not_armed_means_no_live_and_a_logged_reason(live_cfg, clock, live_alerter, ex):
    start(live_cfg, clock, live_alerter, ex)
    assert not ex.live_spawns
    assert "skip cand_x: status 'candidate', not armed" in live_cfg.log_file.read_text()


def test_preflight_verdict_defaults_to_fail_closed(live_cfg, clock, live_alerter, ex):
    arm_for(live_cfg, SID)
    session.start_sessions(live_cfg, DAY, clock, ex, live_alerter)          # caller forgot preflight_ok
    assert not ex.live_spawns
    assert "preflight failed" in live_cfg.log_file.read_text()


def test_too_late_for_the_rebalance_window_is_not_started_and_not_burned(live_cfg, clock, live_alerter, ex, post):
    arm_for(live_cfg, SID)
    clock.t = at(DAY, "09:29") + timedelta(seconds=30)
    assert start(live_cfg, clock, live_alerter, ex) == 1
    assert not ex.live_spawns and any("too late" in t for t in texts(post))
    assert SID not in state.load_state(live_cfg.state_path(DAY), str(DAY))["strategies"]


def test_arming_variables_are_set_in_exactly_one_process_and_one_function():
    root = Path(__file__).resolve().parent
    sources = {p.name: p.read_text() for p in root.glob("*.py") if not p.name.startswith("test_") and p.name != "conftest.py"}
    setters = [n for n, s in sources.items() if 'out["ALGO_LIVE"]' in s or "ALGO_LIVE=" in s]
    assert setters == ["guard.py"]
    callers = [n for n, s in sources.items() if "live_child_env(" in s]
    assert sorted(callers) == ["guard.py", "supervise.py"]
    assert not [n for n, s in sources.items() if '"--retry"' in s and n != "guard.py"]   # only the guard names it (to forbid it)


def test_kill_file_blocks_live_start(live_cfg, clock, live_alerter, ex):
    arm_for(live_cfg, SID)
    live_cfg.kill_file.write_text("")
    assert start(live_cfg, clock, live_alerter, ex) == 1 and not ex.live_spawns and not ex.spawns


@pytest.mark.parametrize("env_edit", [
    lambda e: {k: v for k, v in e.items() if k != "SB_TRADING_ACCESS_TOKEN"},
    lambda e: {k: v for k, v in e.items() if k != "SB_ACCOUNT_NO"},
    lambda e: {**e, "SB_TRADING_ACCESS_TOKEN": trading_jwt(1.0)},                     # expired
    lambda e: {**e, "SB_TRADING_ACCESS_TOKEN": trading_jwt(1e12, acn="CORE-BOOK-9")},  # the wrong account
    lambda e: {**e, "SB_TRADING_ACCESS_TOKEN": "opaque-not-a-jwt"},
])
def test_a_bad_trading_token_or_account_keeps_it_off_live_but_paper_still_runs(live_cfg, clock, post, ex, env_edit):
    cfg = replace(live_cfg, env=env_edit(live_cfg.env))
    a = alerts.Alerter(cfg.log_file, cfg.env, post=post, now=clock.now)
    arm_for(cfg, SID)
    start(cfg, clock, a, ex)
    assert not ex.live_spawns and len(ex.spawns) == 1 and ex.spawns[0][0][4] == "paper_x"
    assert "live preflight failed" in cfg.log_file.read_text()


def test_rerun_never_starts_a_second_live_session_whatever_happened(live_cfg, clock, live_alerter, ex):
    arm_for(live_cfg, SID)
    start(live_cfg, clock, live_alerter, ex)
    for status in ("started", "done", "failed", "stopped", "starting", "refused"):
        doc = state.load_state(live_cfg.state_path(DAY), str(DAY))
        doc["strategies"][SID]["status"] = status
        state.save_state(live_cfg.state_path(DAY), doc)
        start(live_cfg, clock, live_alerter, ex)
    assert len(ex.live_spawns) == 1


def test_a_live_store_dated_today_also_blocks_a_start(live_cfg, clock, live_alerter, ex):
    arm_for(live_cfg, SID)
    write_live_store(live_cfg, SID, [dict(BUY, fills=[(4_200, 1, 600.0)])])       # ran by hand earlier today
    start(live_cfg, clock, live_alerter, ex)
    assert not ex.live_spawns and "never restarted" in live_cfg.log_file.read_text()


def test_state_entry_is_written_before_the_spawn(live_cfg, clock, live_alerter):
    arm_for(live_cfg, SID)

    class Boom(FakeExecutor):
        def spawn_live(self, *a, **k):
            assert state.load_state(live_cfg.state_path(DAY), str(DAY))["strategies"][SID]["status"] == "starting"
            raise OSError("fork failed")
    ex = Boom()
    with pytest.raises(OSError):
        start(live_cfg, clock, live_alerter, ex)
    again = FakeExecutor()
    start(live_cfg, clock, live_alerter, again)
    assert not again.live_spawns                      # the crash left a 'starting' entry that blocks a second start


def test_guard_refusal_at_spawn_alerts_and_blocks(live_cfg, clock, live_alerter, post):
    arm_for(live_cfg, SID)

    class Refusing(FakeExecutor):
        def spawn_live(self, *a, **k):
            raise LiveGuardError("the arm file is gone")
    ex = Refusing()
    assert start(live_cfg, clock, live_alerter, ex) == 1
    assert entry(live_cfg)["status"] == "refused" and any("refused at the guard" in t for t in texts(post))
    again = FakeExecutor()
    start(live_cfg, clock, live_alerter, again)
    assert not again.live_spawns


# ---- watch: no restart, ever ------------------------------------------------------------------

def live_started(cfg, clock, alerter, ex):
    arm_for(cfg, SID)
    start(cfg, clock, alerter, ex)
    return ex.live_spawns[0]["pid"]


@pytest.mark.parametrize("rc", [None, 1, 2, 137, -9])
def test_dead_live_process_is_alerted_investigated_read_only_and_never_restarted(live_cfg, clock, live_alerter, post, rc):
    ex = FakeExecutor()
    pid = live_started(live_cfg, clock, live_alerter, ex)
    write_live_store(live_cfg, SID, [dict(BUY)])                          # an order with no fill, no reject
    ex.alive_pids.discard(pid)
    if rc is not None:
        session.rc_path(live_cfg, DAY, SID).write_text(json.dumps({"rc": rc}))
    runs_before = len(ex.runs)
    assert session.watch_sessions(live_cfg, DAY, clock, ex, live_alerter) == 1
    assert len(ex.live_spawns) == 1 and len(ex.spawns) == 1               # nothing was started again
    assert entry(live_cfg)["status"] == "failed"
    recon = [a for a, _e in ex.runs[runs_before:] if a[:2] == ["make", "recon"]]
    assert len(recon) == 1                                                # the one read-only broker look
    died = [t for t in texts(post) if "LIVE session died" in t][0]
    assert "NOT restarted" in died and "never restarted" in died and "buy 1 BBBB @ 4,200" in died
    assert ACCOUNT not in died


def test_live_watch_has_no_spawn_path():
    src = inspect.getsource(session._check_live)
    assert "spawn(" not in src and "spawn_live" not in src and "_start_live" not in src and "_restart" not in src
    assert "spawn" not in inspect.getsource(session.Executor.interrupt)


def test_live_restart_is_not_offered_even_with_a_budget_and_time(live_cfg, clock, live_alerter, ex):
    pid = live_started(live_cfg, clock, live_alerter, ex)
    ex.alive_pids.discard(pid)                                            # killed, no rc, no open order, 08:45
    cfg = replace(live_cfg, max_restarts=5, watch_deadline="08:50")
    session.watch_sessions(cfg, DAY, clock, ex, live_alerter)
    assert len(ex.live_spawns) == 1
    paper_pids = [a[4] for a, _e, _p in ex.spawns]
    assert paper_pids.count("cand_x") == 0                                # and it is not started as a paper session


def test_clean_live_finish_is_done_and_alerts_nothing_extra(live_cfg, clock, live_alerter, ex, post):
    pid = live_started(live_cfg, clock, live_alerter, ex)
    ex.alive_pids.clear()
    session.rc_path(live_cfg, DAY, SID).write_text(json.dumps({"rc": 0}))
    write_live_status(live_cfg, SID, clock.now(), 5, outcome="complete")
    write_status_paper(live_cfg, clock)                                   # the paper session ended cleanly too
    post.posts.clear()
    session.watch_sessions(live_cfg, DAY, clock, ex, live_alerter)
    assert entry(live_cfg)["status"] == "done"
    assert not [t for t in texts(post) if "died" in t]


def test_disarming_a_running_live_session_stops_it_once(live_cfg, clock, live_alerter, ex, post):
    pid = live_started(live_cfg, clock, live_alerter, ex)
    write_live_status(live_cfg, SID, clock.now(), 5)
    arm_for(live_cfg, SID).unlink()
    cfg = replace(live_cfg, watch_deadline="08:50")
    session.watch_sessions(cfg, DAY, clock, ex, live_alerter)
    assert ex.interrupts.count(pid) >= 1
    assert len([t for t in texts(post) if "DISARMED" in t]) == 1


def test_stale_live_heartbeat_alerts_once(live_cfg, clock, live_alerter, ex, post):
    live_started(live_cfg, clock, live_alerter, ex)
    write_live_status(live_cfg, SID, clock.now(), 300)
    session.watch_sessions(replace(live_cfg, watch_deadline="08:50"), DAY, clock, ex, live_alerter)
    assert len([t for t in texts(post) if "status file stale" in t]) == 1


# ---- order events ----------------------------------------------------------------------------

def run_watch_once(cfg, clock, alerter, ex):
    """One poll of the live session (what each iteration of watch does), state saved like watch does."""
    sp = cfg.state_path(DAY)
    doc = state.load_state(sp, str(DAY))
    session._check_live(cfg, DAY, clock.now(), SID, doc["strategies"][SID], ex, alerter)
    state.save_state(sp, doc)


def live_lines(post):
    return [t for t in texts(post) if "LIVE ORDER" in t]


def test_order_events_are_pushed_once_each(live_cfg, clock, live_alerter, ex, post):
    live_started(live_cfg, clock, live_alerter, ex)
    post.posts.clear()
    write_live_store(live_cfg, SID, [dict(BUY, lots=3)])
    run_watch_once(live_cfg, clock, live_alerter, ex)
    assert len(live_lines(post)) == 1 and "submitted: BUY 3 lots BBBB @ 4,200 (1,260,000 IDR)" in live_lines(post)[0]
    run_watch_once(live_cfg, clock, live_alerter, ex)
    assert len(live_lines(post)) == 1                                     # nothing repeated
    write_live_store(live_cfg, SID, [dict(BUY, lots=3, fills=[(4_200, 1, 630.0)])])
    run_watch_once(live_cfg, clock, live_alerter, ex)
    assert "partial: BUY 1/3 lots BBBB @ avg 4,200" in live_lines(post)[-1]
    write_live_store(live_cfg, SID, [dict(BUY, lots=3, fills=[(4_200, 1, 630.0), (4_210, 2, 1_270.0)])])
    run_watch_once(live_cfg, clock, live_alerter, ex)
    assert "filled: BUY 3/3 lots BBBB" in live_lines(post)[-1] and len(live_lines(post)) == 3
    run_watch_once(live_cfg, clock, live_alerter, ex)
    assert len(live_lines(post)) == 3


def test_rejected_and_cancelled_events(live_cfg, clock, live_alerter, ex, post):
    pid = live_started(live_cfg, clock, live_alerter, ex)
    post.posts.clear()
    other = dict(id="rebalance-CCCC-2026-10-15", symbol="CCCC", side="buy", price=1_000, lots=2,
                 reject="[C-006] insufficient trading balance")
    write_live_store(live_cfg, SID, [dict(BUY), other])
    run_watch_once(live_cfg, clock, live_alerter, ex)
    lines = live_lines(post)
    assert any("rejected: BUY 2 lots CCCC @ 1,000: [C-006] insufficient trading balance" in t for t in lines)
    assert any("submitted: BUY 1 lot BBBB" in t for t in lines) and not any("submitted: BUY 2 lots CCCC" in t for t in lines)
    ex.alive_pids.discard(pid)                                            # the session ends with BBBB still unfilled
    session.watch_sessions(live_cfg, DAY, clock, ex, live_alerter)
    assert any("cancelled: BUY 1 lot BBBB @ 4,200 ended without a fill" in t for t in live_lines(post))


def test_events_survive_a_watch_restart_without_repeating(live_cfg, clock, live_alerter, ex, post):
    live_started(live_cfg, clock, live_alerter, ex)
    write_live_store(live_cfg, SID, [dict(BUY)])
    run_watch_once(live_cfg, clock, live_alerter, ex)
    n = len(live_lines(post))
    run_watch_once(live_cfg, clock, live_alerter, ex)                     # a new process re-reads the state file
    assert len(live_lines(post)) == n


def test_event_alerts_never_carry_tokens_or_the_account_number(live_cfg, clock, live_alerter, ex, post):
    pid = live_started(live_cfg, clock, live_alerter, ex)
    token = live_cfg.env["SB_TRADING_ACCESS_TOKEN"]
    nasty = dict(id="rebalance-DDDD-2026-10-15", symbol="DDDD", side="buy", price=500, lots=1,
                 reject=f"denied for account {ACCOUNT}: Bearer {token} chat 555000111 token {live_cfg.env['SB_ACCESS_TOKEN']}")
    write_live_store(live_cfg, SID, [dict(BUY), nasty])
    run_watch_once(live_cfg, clock, live_alerter, ex)
    ex.alive_pids.discard(pid)
    post_close(live_cfg, DAY, clock, ex, live_alerter)
    everything = "\n".join(texts(post)) + live_cfg.log_file.read_text()
    for secret in (token, ACCOUNT, live_cfg.env["SB_ACCESS_TOKEN"], live_cfg.env["SB_TRADING_ACCESS_TOKEN"], "555000111"):
        assert secret not in everything, secret[:6]
    assert "[REDACTED]" in everything


def test_unreadable_store_is_logged_not_fatal(live_cfg, clock, live_alerter, ex):
    live_started(live_cfg, clock, live_alerter, ex)
    (live_cfg.runs_dir / f"{DAY}-{SID}-a1.db").write_text("this is not sqlite")
    run_watch_once(live_cfg, clock, live_alerter, ex)
    assert "live store not readable" in live_cfg.log_file.read_text()


def test_new_events_unit():
    o = liveorders.OrderState("s:o", "o", "AAAA", "sell", 100, 2, 0, 0.0, 0.0, None)
    lines, seen = liveorders.new_events([o], {}, final=False)
    assert lines == ["LIVE ORDER submitted: SELL 2 lots AAAA @ 100 (20,000 IDR)"]
    assert liveorders.new_events([o], seen, final=False)[0] == []
    assert "cancelled" in liveorders.new_events([o], seen, final=True)[0][0]
    part = replace(o, filled_lots=1, fill_value_idr=10_000.0)
    final_lines, _ = liveorders.new_events([part], seen, final=True)
    assert any("partial" in line for line in final_lines) and any("partially filled 1/2" in line for line in final_lines)


# ---- 08:45 summary ---------------------------------------------------------------------------

def push_morning(cfg, clock, post, preflight_ok=True):
    a = alerts.Alerter(cfg.log_file, cfg.env, post=post, now=clock.now)
    daily.send_morning_summary(cfg, DAY, clock, a, preflight_ok)
    return texts(post)[-1]


def test_summary_says_armed_with_id_and_cap(live_cfg, clock, post):
    arm_for(live_cfg, SID, 500_000)
    msg = push_morning(live_cfg, clock, post)
    first = msg.splitlines()[1]
    assert "ARMED: cand_x, max 500,000 IDR" in first
    assert "LIVE cand_x: buys at most 500,000 IDR" in msg and "paper paper_x" in msg


def test_summary_says_not_armed(live_cfg, clock, post):
    msg = push_morning(live_cfg, clock, post)
    assert msg.splitlines()[1].endswith("not armed") and "NOT live cand_x" in msg


def test_summary_armed_but_blocked_says_why(live_cfg, clock, post):
    arm_for(live_cfg, SID, 500_000)
    msg = push_morning(live_cfg, clock, post, preflight_ok=False)
    assert "ARMED: cand_x, max 500,000 IDR" in msg and "NOT live cand_x: armed, but preflight failed" in msg


def test_summary_invalid_arm_file_is_not_armed(live_cfg, clock, post):
    p = arm_for(live_cfg, SID)
    p.write_text("{broken")
    assert "not armed (arm file INVALID" in push_morning(live_cfg, clock, post)


def test_summary_warns_about_an_unmanaged_live_position(live_cfg, clock, post):
    write_live_store(live_cfg, SID, [dict(BUY, fills=[(4_200, 2, 1_200.0)])], day=PREV)
    msg = push_morning(live_cfg, clock, post)
    assert "WARNING cand_x: holds BBBB 2 lots LIVE and is NOT going live today" in msg


def test_morning_dispatch_sends_the_summary_even_when_preflight_fails(live_cfg, clock, live_alerter, ex, post):
    assert daily.dispatch("morning", live_cfg, DAY, clock, ex, live_alerter) == 1      # no bars: preflight fails
    assert any("morning plan: not armed" in t for t in texts(post))
    assert not ex.live_spawns and not ex.spawns


def test_morning_dispatch_goes_live_when_everything_holds(live_cfg, clock, live_alerter, ex, post):
    write_bars(live_cfg, "AAAA", PREV)
    write_bars(live_cfg, "BBBB", PREV)
    arm_for(live_cfg, SID, 250_000)
    ex.alive_pids = set()

    def finish(*a, **k):
        pid = ex.live_spawns[-1]["pid"]
        ex.alive_pids.discard(pid)
        session.rc_path(live_cfg, DAY, SID).write_text(json.dumps({"rc": 0}))
        write_live_status(live_cfg, SID, clock.now(), 5, outcome="complete")
    orig = ex.spawn_live

    def spawn_then_end(*a, **k):
        pid = orig(*a, **k)
        finish()
        return pid
    ex.spawn_live = spawn_then_end
    ex_spawn = ex.spawn

    def paper_spawn(*a, **k):
        pid = ex_spawn(*a, **k)
        ex.alive_pids.discard(pid)
        write_status_paper(live_cfg, clock)
        return pid
    ex.spawn = paper_spawn
    assert daily.dispatch("morning", live_cfg, DAY, clock, ex, live_alerter) == 0
    assert len(ex.live_spawns) == 1
    assert any("ARMED: cand_x, max 250,000 IDR" in t for t in texts(post))


def write_status_paper(cfg, clock):
    from runner.conftest import write_status
    write_status(cfg, "paper_x", clock.now(), 5, outcome="complete")


# ---- preflight --------------------------------------------------------------------------------

def test_preflight_armed_needs_the_candidates_bar_but_unarmed_does_not(live_cfg, now):
    write_bars(live_cfg, "AAAA", PREV)
    assert pf.run_preflight(live_cfg, DAY, now.timestamp()).ok
    arm_for(live_cfg, SID)
    res = pf.run_preflight(live_cfg, DAY, now.timestamp())
    assert not res.ok and any("BBBB" in c.name for c in res.failures)
    write_bars(live_cfg, "BBBB", PREV)
    res = pf.run_preflight(live_cfg, DAY, now.timestamp())
    assert res.ok and any(c.name == "live preflight" and c.level == pf.OK for c in res.checks)


def test_preflight_bad_trading_token_warns_and_does_not_fail_the_day(live_cfg, now):
    write_bars(live_cfg, "AAAA", PREV)
    write_bars(live_cfg, "BBBB", PREV)
    arm_for(live_cfg, SID)
    cfg = replace(live_cfg, env={**live_cfg.env, "SB_TRADING_ACCESS_TOKEN": trading_jwt(now.timestamp() + 600)})
    res = pf.run_preflight(cfg, DAY, now.timestamp())
    assert res.ok and any(c.name == "live preflight" and c.level == pf.WARN for c in res.checks)
    out = pf.format_result(res)
    assert cfg.env["SB_TRADING_ACCESS_TOKEN"] not in out and ACCOUNT not in out


def test_preflight_with_nothing_armed_does_not_check_the_trading_token(cfg, now):
    write_bars(cfg, "AAAA", PREV)
    res = pf.run_preflight(cfg, DAY, now.timestamp())
    assert res.ok and not any(c.name == "live preflight" for c in res.checks)


# ---- post-close live summary ------------------------------------------------------------------

def test_day_summary_reports_orders_cap_fees_and_position(live_cfg, clock, live_alerter, ex, post):
    pid = live_started(live_cfg, clock, live_alerter, ex)
    write_live_store(live_cfg, SID, [dict(BUY, lots=2, fills=[(4_200, 2, 1_260.0)]),
                                     dict(id="r-CCCC", symbol="CCCC", side="buy", price=500, lots=1, reject="nope")])
    write_live_status(live_cfg, SID, clock.now(), 5, outcome="complete")
    ex.alive_pids.discard(pid)
    session.rc_path(live_cfg, DAY, SID).write_text(json.dumps({"rc": 0}))
    session.watch_sessions(live_cfg, DAY, clock, ex, live_alerter)
    clock.t = at(DAY, "16:20")
    post.posts.clear()
    post_close(live_cfg, DAY, clock, ex, live_alerter)
    summary = [t for t in texts(post) if "LIVE summary" in t][0]
    assert "ARMED: cand_x, max 500,000 IDR" in summary
    assert "1 submitted: 1 filled, 0 partial, 0 unfilled/cancelled; 1 rejected" in summary
    assert "bought 840,000 of the 500,000 IDR cap" in summary or "bought 840,000 of the 500,000" in summary
    assert "position now: BBBB 2 lots" in summary and "exit code 0" in summary
    assert ACCOUNT not in summary


def test_no_live_summary_when_live_had_nothing_to_do(live_cfg, clock, live_alerter, ex, post):
    clock.t = at(DAY, "16:20")
    post_close(live_cfg, DAY, clock, ex, live_alerter)
    assert not [t for t in texts(post) if "LIVE summary" in t]


def test_armed_but_not_run_is_reported_in_the_summary(live_cfg, clock, live_alerter, ex, post):
    arm_for(live_cfg, SID, 100_000)
    clock.t = at(DAY, "16:20")
    post_close(live_cfg, DAY, clock, ex, live_alerter)
    assert any("armed (max 100,000 IDR) but NO live session ran today" in t for t in texts(post))


# ---- dry run --------------------------------------------------------------------------------

def test_dry_run_shows_exactly_what_would_run_live_and_why(live_cfg, capsys):
    arm_for(live_cfg, SID, 500_000)
    live_ex = FakeExecutor()
    rc = daily.main(["all", "--dry-run", "--date", str(DAY), "--no-env-file"], cfg=live_cfg, executor=live_ex)
    out = capsys.readouterr().out
    assert rc == 0 and not live_ex.runs and not live_ex.spawns and not live_ex.live_spawns
    assert "arm status: ARMED: cand_x, max 500,000 IDR" in out
    assert "LIVE cand_x: status 'candidate', armed for 2026-10-15 with max 500,000 IDR" in out
    assert "--sink live" in out and "--max-buy-notional-idr 500000" in out and "--max-restarts 0" in out
    assert "target_emitter" in out and "no restart of any kind" in out and "--retry" not in out.replace("--retry forbidden", "")
    assert "[ACCOUNT]" in out and ACCOUNT not in out
    assert live_cfg.env["SB_TRADING_ACCESS_TOKEN"] not in out and live_cfg.env["SB_ACCESS_TOKEN"] not in out
    assert not live_cfg.runner_dir.exists()


def test_dry_run_unarmed_and_blocked_cases_say_why(live_cfg, capsys):
    daily.main(["all", "--dry-run", "--date", str(DAY), "--no-env-file"], cfg=live_cfg)
    out = capsys.readouterr().out
    assert "arm status: not armed" in out and "NOT live cand_x" in out and "LIVE cand_x:" not in out
    arm_for(live_cfg, SID)
    live_cfg.kill_file.parent.mkdir(parents=True, exist_ok=True)
    live_cfg.kill_file.write_text("")
    daily.main(["all", "--dry-run", "--date", str(DAY), "--no-env-file"], cfg=live_cfg)
    out = capsys.readouterr().out
    assert "NOT live cand_x: armed, but the kill switch" in out


def test_dry_run_a_wrong_account_token_is_not_live(live_cfg, capsys):
    arm_for(live_cfg, SID)
    cfg = replace(live_cfg, env={**live_cfg.env, "SB_TRADING_ACCESS_TOKEN": trading_jwt(1e12, acn="CORE-BOOK-9")})
    daily.main(["all", "--dry-run", "--date", str(DAY), "--no-env-file"], cfg=cfg)
    out = capsys.readouterr().out
    assert "NOT live cand_x: armed, but live preflight failed" in out and "CORE-BOOK-9" not in out


# ---- the operator commands --------------------------------------------------------------------

def test_arm_and_disarm_through_the_runner_cli(live_cfg, capsys):
    rc = daily.main(["arm", "--strategy", "paper_x", "--max-idr", "1000", "--date", str(DAY)], cfg=live_cfg)
    assert rc == 2 and not (live_cfg.runs_dir / "arm" / f"ARM-{DAY}.json").exists()
    assert daily.main(["disarm", "--date", str(DAY)], cfg=live_cfg) == 0


def test_make_targets_exist_and_pass_their_arguments():
    root = Path(__file__).resolve().parents[2]
    arm_cmd = subprocess.run(["make", "-n", "runner-arm", "STRATEGY=cand_x", "MAX_IDR=500000", "DATE=2026-10-16"],
                             cwd=root, capture_output=True, text=True)
    assert arm_cmd.returncode == 0
    assert "runner arm --strategy cand_x --max-idr 500000 --date 2026-10-16" in arm_cmd.stdout
    disarm_cmd = subprocess.run(["make", "-n", "runner-disarm", "DATE=2026-10-16"], cwd=root, capture_output=True, text=True)
    assert disarm_cmd.returncode == 0 and "runner disarm --date 2026-10-16" in disarm_cmd.stdout
    missing = subprocess.run(["make", "runner-arm", "STRATEGY=cand_x"], cwd=root, capture_output=True, text=True)
    assert missing.returncode != 0 and "MAX_IDR" in (missing.stdout + missing.stderr)
