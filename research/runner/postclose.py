"""~16:20 post-close chain: refresh-prices, recon, paper-scorecard, dashboard, then a summary."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Tuple

from lib.market_calendar import CALENDAR
from runner import livereport
from runner.alerts import SUMMARY, WARN, Alerter
from runner.config import ACCOUNT_ENV, BEARER_TOKEN_ENV, TRADING_TOKEN_ENV, Clock, Config
from runner.executor import Executor
from runner.guard import child_env
from runner.state import load_state, save_state
from runner.strategies import select_and_log
from strategy_fit.paper_stores import list_stores, net_lots, read_fills


def post_close_steps(cfg: Config, paper_ids: List[str]) -> List[dict]:
    """The ordered steps. `argv` None means skipped, with the reason in `skip`."""
    py = f"DASH_PY={cfg.python}"
    steps = [
        {"name": "refresh-prices", "argv": ["make", "refresh-prices", py],
         "skip": None if cfg.env.get(BEARER_TOKEN_ENV) else f"{BEARER_TOKEN_ENV} not set", "trading": False},
        {"name": "recon", "argv": ["make", "recon", py],
         "skip": None if (cfg.env.get(ACCOUNT_ENV) and cfg.env.get(TRADING_TOKEN_ENV))
         else f"account env absent ({ACCOUNT_ENV} / {TRADING_TOKEN_ENV}); recon needs the sleeve account", "trading": True},
    ]
    for sid in paper_ids:
        steps.append({"name": f"paper-scorecard {sid}", "argv": ["make", "paper-scorecard", f"STRATEGY={sid}", py],
                      "skip": None, "trading": False})
    steps.append({"name": "dashboard", "argv": ["make", "dashboard", py, "HEALTHCHECK=skip"], "skip": None, "trading": False})
    return steps


def day_summary(cfg: Config, day: date, paper_ids: List[str]) -> List[str]:
    lines = []
    for sid in paper_ids:
        try:
            stores = [p for p in list_stores(cfg.runs_dir, sid) if p.name.startswith(str(day))]
            fills = [f for f in read_fills(stores)]
            lines.append(f"{sid}: {len(stores)} store(s) today, {len(fills)} fill(s), paper position {net_lots(read_fills(list_stores(cfg.runs_dir, sid))) or 'flat'}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{sid}: could not read stores ({type(exc).__name__})")
    return lines


def post_close(cfg: Config, day: date, clock: Clock, ex: Executor, alerter: Alerter, force: bool = False) -> int:
    if not CALENDAR.is_trading_day(day):
        alerter.log(f"post-close {day}: not a trading day, nothing to do")
        return 0
    sp = cfg.state_path(day)
    state = load_state(sp, str(day))
    if state.get("post_close") and not force:
        alerter.log(f"post-close {day}: already done at {state['post_close']['at']}; use --force to rerun")
        return 0
    paper_ids = [s.strategy_id for s in select_and_log(cfg.registry, alerter, cfg.runs_dir, day)]
    results: List[Tuple[str, str]] = []
    for st in post_close_steps(cfg, paper_ids):
        if st["skip"]:
            alerter.log(f"post-close {st['name']}: skipped, {st['skip']}")
            results.append((st["name"], "skipped"))
            continue
        env = child_env(cfg.env, allow_trading_token=st["trading"])
        rc = ex.run(st["argv"], env, cfg.repo_root, cfg.child_log(day, "post-close"))
        results.append((st["name"], "ok" if rc == 0 else f"FAILED rc={rc}"))
        if rc != 0:
            alerter.send(WARN, f"{day} post-close: {st['name']} failed (rc {rc})", f"log: {cfg.child_log(day, 'post-close')}")
    failed = [n for n, r in results if r.startswith("FAILED")]
    sess = [f"{sid}: {e.get('status')}" + (f", {e.get('restarts')} restart(s)" if e.get("restarts") else "")
            for sid, e in sorted(state["strategies"].items())]
    body = "\n".join(["sessions: " + ("; ".join(sess) or "none started")]
                     + day_summary(cfg, day, paper_ids)
                     + [f"{n}: {r}" for n, r in results])
    for sid, entry in sorted(state["strategies"].items()):     # last order events not yet reported
        if entry.get("live"):
            livereport.flush_events(cfg, day, sid, entry, alerter, final=True)
    state["post_close"] = {"at": clock.now().isoformat(timespec="seconds"), "failed": failed}
    save_state(sp, state)
    alerter.send(SUMMARY, f"{day} daily summary ({'ALL OK' if not failed else str(len(failed)) + ' step(s) failed'})", body)
    live_summary = livereport.day_summary(cfg, day, state)
    if live_summary is not None:
        alerter.send(SUMMARY, *live_summary)
    return 1 if failed else 0
