"""Shared fakes for the runner tests: a repo skeleton, a fake clock, a recording executor."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from lib.market_calendar import CALENDAR
from runner.alerts import Alerter
from runner.config import WIB, Config
from strategy_fit import emitter_specs as es

DAY = date(2026, 10, 15)                       # a Thursday, IDX trading day
PREV = CALENDAR.prev_trading_day(DAY)
BASE = es.SPECS["wifi_momo_h5"]
PAPER_SPEC = replace(BASE, strategy_id="paper_x", symbols=("AAAA",))
CAND_SPEC = replace(BASE, strategy_id="cand_x", symbols=("BBBB",), status=es.STATUS_CANDIDATE)
RETIRED_SPEC = replace(BASE, strategy_id="dead_x", symbols=("CCCC",), status=es.STATUS_RETIRED)
REGISTRY = {s.strategy_id: s for s in (PAPER_SPEC, CAND_SPEC, RETIRED_SPEC)}


def jwt(exp_epoch: float) -> str:
    import base64
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{b({'alg': 'none'})}.{b({'exp': exp_epoch})}.sigsigsig"


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)


class FakeExecutor:
    """Records every command; `alive_pids` decides who is alive; never starts a process.

    spawn_live runs the REAL guard checks (a fake must not be more permissive than the Executor)."""

    def __init__(self, run_rc=0, registry=None):
        self.runs, self.spawns, self.interrupts, self.live_spawns = [], [], [], []
        self.alive_pids, self.run_rc, self._next = set(), run_rc, 1000
        self.registry = registry if registry is not None else REGISTRY

    def spawn_live(self, cmd, env, cwd, log, rc_file, python, grant_path, preflight_ok):
        from runner.guard import validate_live_command, wrapper_env
        validate_live_command(cmd, self.registry)
        wenv = wrapper_env(cmd, env, self.registry)
        self._next += 1
        self.live_spawns.append({"cmd": cmd, "env": wenv, "pid": self._next, "grant": grant_path,
                                 "preflight_ok": preflight_ok})
        self.alive_pids.add(self._next)
        return self._next

    def run(self, argv, env, cwd, log, timeout=None):
        self.runs.append((list(argv), dict(env)))
        return self.run_rc.get(argv[1], 0) if isinstance(self.run_rc, dict) else self.run_rc

    def spawn(self, argv, env, cwd, log, rc_file, python):
        self._next += 1
        self.spawns.append((list(argv), dict(env), self._next))
        self.alive_pids.add(self._next)
        return self._next

    def alive(self, pid):
        return pid in self.alive_pids

    def interrupt(self, pid):
        self.interrupts.append(pid)


class Recorder:
    def __init__(self):
        self.posts = []

    def __call__(self, url, payload):
        self.posts.append((url, payload))


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """The post-close reviewer must never start a real `claude -p` in a test: its one subprocess
    boundary fails like a missing binary (-> the deterministic fallback). Tests of the reviewer pass
    their own `run=` fake."""
    from runner import review_summary

    def _boom(*_a, **_k):
        raise FileNotFoundError("claude is not available in tests")
    monkeypatch.setattr(review_summary, "_run", _boom)
    monkeypatch.setattr(review_summary, "_which", lambda name: "/fake/bin/claude")


@pytest.fixture
def now():
    return datetime(2026, 10, 15, 8, 45, tzinfo=WIB)


@pytest.fixture
def clock(now):
    return FakeClock(now)


@pytest.fixture
def good_env(now):
    return {"SB_ACCESS_TOKEN": jwt(now.timestamp() + 5 * 3600),
            "TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35, "TELEGRAM_CHAT_ID": "555000111"}


@pytest.fixture
def cfg(tmp_path, good_env):
    (tmp_path / "bin").mkdir()
    for b in ("live-session", "strategy-run"):
        (tmp_path / "bin" / b).write_text("")
    return Config(repo_root=tmp_path, env=good_env, python="python3", registry=REGISTRY)


@pytest.fixture
def post():
    return Recorder()


@pytest.fixture
def alerter(cfg, good_env, post, clock):
    return Alerter(cfg.log_file, good_env, post=post, now=clock.now)


@pytest.fixture
def ex():
    return FakeExecutor()


def write_bars(cfg: Config, symbol: str, last_day: date, volume: float = 1000.0):
    cfg.daily_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"date": [PREV - timedelta(days=3), last_day], "open": [1.0, 1.0], "high": [1.0, 1.0],
                       "low": [1.0, 1.0], "close": [1.0, 1.0], "volume": [500.0, volume], "value": [1.0, 1.0]})
    df.to_parquet(cfg.daily_dir / f"{symbol}.parquet")


ACCOUNT = "SYNTH-ACC1"          # synthetic sleeve account; never a real one


def trading_jwt(exp_epoch: float, acn: str = ACCOUNT) -> str:
    import base64
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{b({'alg': 'none'})}.{b({'exp': exp_epoch, 'data': {'acn': acn}})}.sigsigsigsig"


@pytest.fixture
def live_cfg(cfg, now):
    """cfg whose env can go live: trading token (right account, 5h left) and the account number."""
    env = {**cfg.env, "SB_TRADING_ACCESS_TOKEN": trading_jwt(now.timestamp() + 5 * 3600), "SB_ACCOUNT_NO": ACCOUNT}
    return replace(cfg, env=env)


@pytest.fixture
def live_alerter(live_cfg, post, clock):
    return Alerter(live_cfg.log_file, live_cfg.env, post=post, now=clock.now)


def arm_for(cfg: Config, sid: str = "cand_x", max_idr: int = 500_000, day: date = DAY) -> Path:
    arm_dir = cfg.runs_dir / "arm"
    arm_dir.mkdir(parents=True, exist_ok=True)
    p = arm_dir / f"ARM-{day}.json"
    p.write_text(json.dumps({"date": str(day), "strategies": {sid: {"max_idr": max_idr}}}))
    return p


def write_live_store(cfg: Config, sid: str, orders, day: date = DAY, n: int = 1) -> Path:
    """A live store like strategy-run's: orders = [dict(id, symbol, side, price, lots, fills=[(px, lots, fee)],
    reject=None)]. Only the columns the runner reads."""
    import sqlite3
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.runs_dir / f"{day}-{sid}-a{n}.db"
    path.unlink(missing_ok=True)                 # each call is the store's whole new content
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS order_intent(seq INTEGER, order_id TEXT, run_id TEXT, symbol TEXT, side TEXT,"
        " price_idr INTEGER, lots INTEGER, sink_kind TEXT);"
        "CREATE TABLE IF NOT EXISTS fill(seq INTEGER, order_id TEXT, run_id TEXT, fill_price_idr INTEGER,"
        " fill_lots INTEGER, fee_idr REAL, sink_kind TEXT);"
        "CREATE TABLE IF NOT EXISTS order_reject(seq INTEGER, order_id TEXT, run_id TEXT, reason TEXT, sink_kind TEXT);")
    for i, o in enumerate(orders, start=1):
        con.execute("INSERT INTO order_intent VALUES (?,?,?,?,?,?,?,?)",
                    (i, o["id"], "r1", o["symbol"], o["side"], o["price"], o["lots"], "live"))
        for px, lots, fee in o.get("fills", []):
            con.execute("INSERT INTO fill VALUES (?,?,?,?,?,?,?)", (i, o["id"], "r1", px, lots, fee, "live"))
        if o.get("reject"):
            con.execute("INSERT INTO order_reject VALUES (?,?,?,?,?)", (i, o["id"], "r1", o["reject"], "live"))
    con.commit()
    con.close()
    return path


def write_live_status(cfg: Config, sid: str, now: datetime, age_s: float, outcome="running", n=1, day: date = DAY):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.runs_dir / f"{day}-{sid}-a{n}.status.json"
    p.write_text(json.dumps({"outcome": outcome, "wall_clock_ns": int((now.timestamp() - age_s) * 1e9),
                             "risk": {"kill_switch_tripped": False}}))
    return p


def write_status(cfg: Config, sid: str, now: datetime, age_s: float, outcome="running", n=1):
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.runs_dir / f"{DAY}-{sid}-paper-a{n}.status.json"
    p.write_text(json.dumps({"outcome": outcome, "wall_clock_ns": int((now.timestamp() - age_s) * 1e9)}))
    return p
