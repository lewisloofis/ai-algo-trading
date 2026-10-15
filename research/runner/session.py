"""Pre-open start of the sessions and the in-session watchdog.

Paper sessions: started, watched, and restarted once or twice if killed with no open order.
LIVE sessions (only for a strategy modes.decide_mode returned LIVE for): started once, watched,
alerted on, and NEVER restarted, by this module or by a rerun."""

from __future__ import annotations

import glob
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from runner import liveorders, livereport
from runner.alerts import ALERT, LIVE, WARN, Alerter
from runner.arm import read_arm_file
from runner.config import ACCOUNT_ENV, TRADING_TOKEN_ENV, Clock, Config, at
from runner.executor import Executor
from runner.guard import LiveGuardError, build_live_command, child_env
from runner.heartbeat import assess_heartbeat, latest_status, rc_path, read_rc  # noqa: F401  (re-exported)
from runner.modes import LIVE as MODE_LIVE
from runner.modes import PAPER
from runner.state import load_state, phase_lock, save_state
from runner.strategies import build_gate, decide_all
from strategy_fit.paper_stores import store_key

MIN_LEAD = timedelta(seconds=60)           # live-session --min-lead


def paper_day_argv(cfg: Config, sid: str, day: date) -> List[str]:
    return [cfg.python, "-m", "strategy_fit.paper_day", "--strategy", sid, "--as-of", str(day),
            "--rebalance-at", cfg.rebalance_at, "--runs-dir", str(cfg.runs_dir),
            "--data-dir", str(cfg.daily_dir)]


def live_grant_path(cfg: Config, day: date, sid: str) -> Path:
    return cfg.runner_dir / f"{day}-{sid}.live-grant.json"


def open_orders(runs_dir: Path, day: date, sid: str) -> Optional[List[str]]:
    """Order ids submitted today without a fill, from the paper stores. None = could not tell."""
    out: List[str] = []
    pat = str(Path(runs_dir) / f"{day}-{store_key(sid)}-a[0-9]*.db")
    for path in sorted(glob.glob(pat)):
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT oi.order_id FROM order_intent oi LEFT JOIN fill f "
                    "ON f.order_id = oi.order_id AND f.run_id = oi.run_id WHERE f.order_id IS NULL"
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return None
        out.extend(r[0] for r in rows)
    return out


# ---- start -----------------------------------------------------------------------------------

def start_sessions(cfg: Config, day: date, clock: Clock, ex: Executor, alerter: Alerter,
                   preflight_ok: bool = False) -> int:
    """Spawn paper-day for each paper spec and, for a strategy decide_mode says LIVE, one live
    session. Safe to rerun: a paper strategy already started is skipped, and a live strategy with
    ANY state entry or live store today is never started again.

    ``preflight_ok`` is the day's preflight verdict (the dispatcher passes it); it defaults to
    False so a caller that forgets it can never produce a live start."""
    sp = cfg.state_path(day)
    with phase_lock(cfg.runner_dir / f"{day}.start.lock", ex.alive) as owned:
        if not owned:
            alerter.log(f"start {day}: another start is running; nothing to do")
            return 0
        state = load_state(sp, str(day))
        gate = build_gate(cfg, day, clock.now(), preflight_ok, state)
        decisions = decide_all(cfg.registry, gate)
        for spec, mode in decisions:
            if mode.mode != PAPER:
                alerter.log(f"{'LIVE' if mode.mode == MODE_LIVE else 'skip'} {spec.strategy_id}: {mode.reason}")
        for line in livereport.unmanaged_positions(cfg, decisions):
            alerter.send(WARN, f"{day} open live position not managed today", line)
        paper = [s for s, m in decisions if m.mode == PAPER]
        live = [(s, m) for s, m in decisions if m.mode == MODE_LIVE]
        todo = [s for s in paper if state["strategies"].get(s.strategy_id, {}).get("status") not in ("started", "done")]
        for s in paper:
            if s not in todo:
                alerter.log(f"start {s.strategy_id}: already {state['strategies'][s.strategy_id]['status']} today; not starting again")
        if not todo and not live:
            return 0
        if cfg.kill_file.exists():
            alerter.send(ALERT, f"{day}: kill switch present, no sessions started", str(cfg.kill_file))
            return 1
        env = child_env(cfg.env)
        rc = ex.run(["make", "build-strategy"], env, cfg.repo_root, cfg.child_log(day, "build"))
        if rc != 0:
            alerter.send(ALERT, f"{day}: build-strategy failed (rc {rc}); no sessions started",
                         f"see {cfg.child_log(day, 'build')}")
            return 1
        for s in todo:
            sid = s.strategy_id
            pid = ex.spawn(paper_day_argv(cfg, sid, day), env, cfg.research_dir,
                           cfg.child_log(day, sid), rc_path(cfg, day, sid), cfg.python)
            state["strategies"][sid] = {"status": "started", "pid": pid, "restarts": 0,
                                        "started_at": clock.now().isoformat(timespec="seconds")}
            save_state(sp, state)
            alerter.log(f"started paper session {sid} (pid {pid}), rebalance at {cfg.rebalance_at} WIB")
        bad = 0
        for spec, mode in live:
            bad += _start_live(cfg, day, clock, ex, alerter, state, spec, mode, preflight_ok)
        return 1 if bad else 0


def _start_live(cfg: Config, day: date, clock: Clock, ex: Executor, alerter: Alerter, state: dict, spec, mode,
                preflight_ok: bool) -> int:
    """Start ONE live session. The state entry is written BEFORE the spawn, so a crash in between
    leaves a 'starting' entry that blocks any second start. Returns 1 on a refusal."""
    sid, sp = spec.strategy_id, cfg.state_path(day)
    if sid in state["strategies"]:                    # belt and braces: decide_mode already said no
        alerter.log(f"live {sid}: a state entry exists for today; never started again")
        return 0
    latest = at(day, cfg.rebalance_at) - MIN_LEAD
    if clock.now() >= latest:                         # live-session would refuse; say so plainly instead
        alerter.send(ALERT, f"{day} {sid}: LIVE not started, too late",
                     f"live-session needs a start before {latest:%H:%M:%S} WIB (REBALANCE_AT {cfg.rebalance_at} minus 60s)")
        return 1
    try:
        cmd = build_live_command(mode.token, registry=cfg.registry, repo_root=cfg.repo_root,
                                 rebalance_at=cfg.rebalance_at, account=(cfg.env.get(ACCOUNT_ENV) or "").strip(),
                                 data_dir=cfg.daily_dir)
    except LiveGuardError as exc:
        alerter.send(ALERT, f"{day} {sid}: LIVE not started", str(exc))
        return 1
    entry = {"status": "starting", "live": True, "max_idr": cmd.max_idr, "restarts": 0,
             "started_at": clock.now().isoformat(timespec="seconds"), "live_seen": {}}
    state["strategies"][sid] = entry
    save_state(sp, state)
    try:
        pid = ex.spawn_live(cmd, cfg.env, cfg.research_dir, cfg.child_log(day, sid), rc_path(cfg, day, sid),
                            cfg.python, live_grant_path(cfg, day, sid), preflight_ok)
    except LiveGuardError as exc:
        entry["status"] = "refused"
        save_state(sp, state)
        alerter.send(ALERT, f"{day} {sid}: LIVE refused at the guard", str(exc))
        return 1
    entry.update(pid=pid, status="started")
    save_state(sp, state)
    alerter.send(LIVE, f"{day} LIVE session started: {sid}, buys at most {cmd.max_idr:,} IDR",
                 f"rebalance at {cfg.rebalance_at} WIB; no restart; revoke: make runner-disarm; log {cfg.child_log(day, sid)}")
    alerter.log(f"live command: {cmd.display()}")
    return 0


# ---- watch -----------------------------------------------------------------------------------

def _terminal(entry: dict, status: str) -> None:
    entry["status"] = status


def watch_sessions(cfg: Config, day: date, clock: Clock, ex: Executor, alerter: Alerter) -> int:
    """Poll until every session is terminal or the deadline passes. Returns 0 if all clean, else 1."""
    sp = cfg.state_path(day)
    deadline = at(day, cfg.watch_deadline)
    latest_restart = at(day, cfg.rebalance_at) - MIN_LEAD
    bad = 0
    while True:
        state = load_state(sp, str(day))
        now = clock.now()
        running = [(sid, e) for sid, e in sorted(state["strategies"].items()) if e.get("status") == "started"]
        for sid, e in running:
            if e.get("live"):
                bad += _check_live(cfg, day, now, sid, e, ex, alerter)
            else:
                bad += _check_one(cfg, day, now, sid, e, ex, alerter, latest_restart)
        save_state(sp, state)
        if cfg.kill_file.exists() and not state.get("kill_alerted"):
            state["kill_alerted"] = True
            save_state(sp, state)
            alerter.send(ALERT, f"{day}: kill switch file appeared; live-session stops its own session", str(cfg.kill_file))
        if not any(e.get("status") == "started" for e in state["strategies"].values()):
            break
        if now >= deadline:
            for sid, e in sorted(state["strategies"].items()):
                if e.get("status") == "started":
                    if ex.alive(e["pid"]):
                        ex.interrupt(e["pid"])
                        alerter.send(WARN, f"{day} {sid}: still running at the {cfg.watch_deadline} deadline; sent a graceful stop")
                        bad += 1
                    _terminal(e, "stopped")
                    # A live session's drain (cancelling resting orders) outlasts this poll, so its final
                    # 'cancelled' events are reported by post-close, once the process has really ended.
            save_state(sp, state)
            break
        clock.sleep(cfg.poll_s)
    return 1 if bad else 0


def _alive_alerts(cfg: Config, day: date, now: datetime, sid: str, e: dict, hb: str, age, alerter: Alerter,
                  label: str) -> int:
    """Stale-heartbeat and no-heartbeat alerts for a process that is still alive. 1 if one fired."""
    if hb == "stale" and not e.get("stale_alerted"):
        e["stale_alerted"] = True
        alerter.send(ALERT, f"{day} {sid}: status file stale",
                     f"heartbeat older than {cfg.stale_s}s (age {'unknown' if age is None else int(age)}s) while the {label}process is alive")
        return 1
    started = datetime.fromisoformat(e["started_at"])
    if hb == "none" and (now - started).total_seconds() > cfg.no_heartbeat_s and not e.get("nohb_alerted"):
        e["nohb_alerted"] = True
        alerter.send(WARN, f"{day} {sid}: no status file {cfg.no_heartbeat_s}s after start",
                     f"log: {cfg.child_log(day, sid)}")
        return 1
    return 0


def _check_one(cfg, day, now, sid, e, ex, alerter, latest_restart) -> int:
    """One PAPER session. (A live session goes through _check_live, which has no restart path.)"""
    pid, rc = e["pid"], read_rc(rc_path(cfg, day, sid))
    hb, age = assess_heartbeat(latest_status(cfg.runs_dir, day, sid), now.timestamp(), cfg.stale_s)
    if ex.alive(pid) and rc is None:
        return _alive_alerts(cfg, day, now, sid, e, hb, age, alerter, "")
    # the process is gone
    if rc == 0 or (rc is None and hb == "complete"):
        _terminal(e, "done")
        alerter.log(f"{sid}: session finished cleanly")
        return 0
    restartable = rc is None or rc < 0 or rc >= 128
    why = f"killed or vanished (no exit code)" if rc is None else f"exit code {rc}"
    if hb == "error" or not restartable:
        _terminal(e, "failed")
        alerter.send(ALERT, f"{day} {sid}: session failed, not restarted", f"{why}; log: {cfg.child_log(day, sid)}")
        return 1
    orders = open_orders(cfg.runs_dir, day, sid)
    refusal = None
    if orders is None:
        refusal = "could not read today's paper store to check for open orders"
    elif orders:
        refusal = f"{len(orders)} order(s) submitted without a fill; resolve by hand"
    elif e.get("restarts", 0) >= cfg.max_restarts:
        refusal = f"restart budget ({cfg.max_restarts}) used"
    elif now >= latest_restart:
        refusal = f"too late to restart (needs a start before {latest_restart:%H:%M:%S} WIB)"
    elif cfg.kill_file.exists():
        refusal = "kill switch present"
    if refusal:
        _terminal(e, "failed")
        alerter.send(ALERT, f"{day} {sid}: session died ({why}); NOT restarted", refusal)
        return 1
    new_pid = ex.spawn(paper_day_argv(cfg, sid, day), child_env(cfg.env), cfg.research_dir,
                       cfg.child_log(day, sid), rc_path(cfg, day, sid), cfg.python)
    e.update(pid=new_pid, restarts=e.get("restarts", 0) + 1, started_at=now.isoformat(timespec="seconds"),
             stale_alerted=False, nohb_alerted=False)
    alerter.send(WARN, f"{day} {sid}: session died ({why}); restarted (paper, no open orders)",
                 f"restart {e['restarts']}/{cfg.max_restarts}")
    return 0


def _check_live(cfg: Config, day: date, now: datetime, sid: str, e: dict, ex: Executor, alerter: Alerter) -> int:
    """One LIVE session. It is reported on, stopped if disarmed, and, when it dies, investigated
    read-only and left alone. There is deliberately no spawn call in this function."""
    pid, rc = e["pid"], read_rc(rc_path(cfg, day, sid))
    hb, age = assess_heartbeat(latest_status(cfg.runs_dir, day, sid, sink="live"), now.timestamp(), cfg.stale_s)
    alive = ex.alive(pid) and rc is None
    livereport.flush_events(cfg, day, sid, e, alerter, final=not alive)
    if alive:
        arm = read_arm_file(cfg.runs_dir, day)
        if not (arm.valid and sid in arm.max_idr) and not e.get("disarm_alerted"):
            e["disarm_alerted"] = True
            ex.interrupt(pid)
            alerter.send(ALERT, f"{day} {sid}: DISARMED while live; stopping the session",
                         "the arm file is gone or invalid: sent a graceful stop (resting orders are cancelled, "
                         "orders already filled stay). Check the app.")
            return 1
        return _alive_alerts(cfg, day, now, sid, e, hb, age, alerter, "LIVE ")
    if rc == 0 or (rc is None and hb == "complete"):
        _terminal(e, "done")
        alerter.log(f"{sid}: live session finished cleanly")
        return 0
    _terminal(e, "failed")
    why = "killed or vanished (no exit code)" if rc is None else f"exit code {rc}"
    body = [f"{why}; log: {cfg.child_log(day, sid)}", "NOT restarted: a live session is never restarted by the runner."]
    try:
        stuck = liveorders.unresolved(liveorders.read_orders(liveorders.live_stores(cfg.runs_dir, sid, day)))
        body.append("orders neither filled nor rejected in the store: "
                    + (", ".join(f"{o.side} {o.lots} {o.symbol} @ {o.price_idr:,}" for o in stuck) or "none")
                    + ". Check the Stockbit app for resting orders.")
    except sqlite3.Error:
        body.append("the live store could not be read: check the Stockbit app for resting orders.")
    if cfg.env.get(ACCOUNT_ENV) and cfg.env.get(TRADING_TOKEN_ENV):
        rrc = ex.run(["make", "recon", f"DASH_PY={cfg.python}"], child_env(cfg.env, allow_trading_token=True),
                     cfg.repo_root, cfg.child_log(day, "post-close"))
        e["recon"] = "ok (read-only, see runs/recon)" if rrc == 0 else f"FAILED rc={rrc}"
        body.append(f"read-only broker recon: {e['recon']}")
    else:
        body.append("broker recon skipped: account env absent")
    alerter.send(ALERT, f"{day} {sid}: LIVE session died; NOT restarted", "\n".join(body))
    return 1
