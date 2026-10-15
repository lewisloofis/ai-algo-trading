"""Pre-open start of the paper sessions and the in-session watchdog."""

from __future__ import annotations

import glob
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from runner.alerts import ALERT, WARN, Alerter
from runner.config import Clock, Config, at
from runner.executor import Executor
from runner.guard import child_env
from runner.state import load_state, phase_lock, save_state
from runner.strategies import select_and_log
from strategy_fit.paper_stores import store_key

MIN_LEAD = timedelta(seconds=60)           # live-session --min-lead


def paper_day_argv(cfg: Config, sid: str, day: date) -> List[str]:
    return [cfg.python, "-m", "strategy_fit.paper_day", "--strategy", sid, "--as-of", str(day),
            "--rebalance-at", cfg.rebalance_at, "--runs-dir", str(cfg.runs_dir),
            "--data-dir", str(cfg.daily_dir)]


def rc_path(cfg: Config, day: date, sid: str) -> Path:
    return cfg.runner_dir / f"{day}-{sid}.rc.json"


def read_rc(path: Path) -> Optional[int]:
    try:
        return int(json.loads(Path(path).read_text())["rc"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---- heartbeat -------------------------------------------------------------------------------

def latest_status(runs_dir: Path, day: date, sid: str) -> Optional[dict]:
    pat = str(Path(runs_dir) / f"{day}-{store_key(sid)}-a[0-9]*.status.json")
    files = sorted(glob.glob(pat), key=lambda p: Path(p).stat().st_mtime)
    for path in reversed(files):
        try:
            doc = json.loads(Path(path).read_text())
            if isinstance(doc, dict):
                return doc
        except (OSError, ValueError):
            continue
    return None


def assess_heartbeat(doc: Optional[dict], now_epoch: float, stale_s: int) -> Tuple[str, Optional[float]]:
    """('none'|'running'|'stale'|'complete'|'error', age seconds). Unreadable timestamp is never 'running'."""
    if doc is None:
        return "none", None
    outcome = doc.get("outcome")
    ns = doc.get("wall_clock_ns")
    age = None
    if isinstance(ns, (int, float)) and not isinstance(ns, bool) and ns > 0:
        age = max(0.0, now_epoch - ns / 1e9)
    if outcome == "error":
        return "error", age
    if outcome != "running":
        return "complete", age
    if age is None or age > stale_s:
        return "stale", age
    return "running", age


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

def start_sessions(cfg: Config, day: date, clock: Clock, ex: Executor, alerter: Alerter) -> int:
    """Spawn paper-day for each paper spec. Safe to rerun: a strategy already started is skipped."""
    sp = cfg.state_path(day)
    with phase_lock(cfg.runner_dir / f"{day}.start.lock", ex.alive) as owned:
        if not owned:
            alerter.log(f"start {day}: another start is running; nothing to do")
            return 0
        state = load_state(sp, str(day))
        paper = select_and_log(cfg.registry, alerter, cfg.runs_dir, day)
        todo = [s for s in paper if state["strategies"].get(s.strategy_id, {}).get("status") not in ("started", "done")]
        for s in paper:
            if s not in todo:
                alerter.log(f"start {s.strategy_id}: already {state['strategies'][s.strategy_id]['status']} today; not starting again")
        if not todo:
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
            save_state(sp, state)
            break
        clock.sleep(cfg.poll_s)
    return 1 if bad else 0


def _check_one(cfg, day, now, sid, e, ex, alerter, latest_restart) -> int:
    pid, rc = e["pid"], read_rc(rc_path(cfg, day, sid))
    hb, age = assess_heartbeat(latest_status(cfg.runs_dir, day, sid), now.timestamp(), cfg.stale_s)
    if ex.alive(pid) and rc is None:
        if hb == "stale" and not e.get("stale_alerted"):
            e["stale_alerted"] = True
            alerter.send(ALERT, f"{day} {sid}: status file stale",
                         f"heartbeat older than {cfg.stale_s}s (age {'unknown' if age is None else int(age)}s) while the process is alive")
            return 1
        started = datetime.fromisoformat(e["started_at"])
        if hb == "none" and (now - started).total_seconds() > cfg.no_heartbeat_s and not e.get("nohb_alerted"):
            e["nohb_alerted"] = True
            alerter.send(WARN, f"{day} {sid}: no status file {cfg.no_heartbeat_s}s after start",
                         f"log: {cfg.child_log(day, sid)}")
            return 1
        return 0
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
