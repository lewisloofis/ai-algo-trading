"""Which strategies the runner touches today, and in which mode: whatever modes.decide_mode says."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import FrozenSet, List, Mapping, Optional, Tuple

from runner import livepre, liveorders
from runner.alerts import Alerter
from runner.arm import ArmFile, read_arm_file
from runner.config import WIB, Config
from runner.modes import PAPER, LiveGate, Mode, decide_mode


def _registry(registry=None):
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    return registry


def select(registry=None) -> Tuple[list, List[Tuple[str, str]]]:
    """(paper specs sorted by id, [(strategy_id, reason)] of every other spec). No live gate: a
    candidate is never selected here; the live decision is ``decide_all`` with a real gate."""
    registry = _registry(registry)
    paper, other = [], []
    for s in sorted(registry.values(), key=lambda s: s.strategy_id):
        m = decide_mode(s)
        (paper if m.mode == PAPER else other).append(s if m.mode == PAPER else (s.strategy_id, m.reason))
    return paper, other


def armed_candidates(registry, arm: ArmFile) -> list:
    """Candidate specs a VALID arm file names (they will need live-only checks and their bars)."""
    registry = _registry(registry)
    if not arm.valid:
        return []
    return [registry[sid] for sid in sorted(arm.max_idr) if sid in registry and registry[sid].is_candidate]


def started_live(cfg: Config, day: date, state: Optional[Mapping]) -> FrozenSet[str]:
    """Strategies whose live session already started today: recorded in the state file in ANY
    status, or with a live store dated today. A rerun never starts a second one."""
    out = {sid for sid, e in (state or {}).get("strategies", {}).items() if e.get("live")}
    for spec in _registry(cfg.registry).values():
        if spec.is_candidate and liveorders.live_stores(cfg.runs_dir, spec.strategy_id, day):
            out.add(spec.strategy_id)
    return frozenset(out)


def build_gate(cfg: Config, day: date, now: datetime, preflight_ok: bool,
               state: Optional[Mapping] = None) -> LiveGate:
    """Everything decide_mode needs besides the spec, read from files and the clock right now."""
    arm = read_arm_file(cfg.runs_dir, day)
    problems: Tuple[str, ...] = ()
    if armed_candidates(cfg.registry, arm):
        problems = livepre.live_problems(cfg.env, now.timestamp(), cfg.min_access_token_hours)
    return LiveGate(day=day, today=now.astimezone(WIB).date(), arm=arm,
                    kill_present=os.path.lexists(cfg.kill_file), preflight_ok=preflight_ok,
                    live_problems=problems, already_started=started_live(cfg, day, state))


def decide_all(registry, gate: LiveGate) -> List[Tuple[object, Mode]]:
    """(spec, mode) for every spec, sorted by id."""
    return [(s, decide_mode(s, gate)) for s in sorted(_registry(registry).values(), key=lambda s: s.strategy_id)]


def select_and_log(registry, alerter: Alerter, runs_dir: Optional[Path] = None, day: Optional[date] = None) -> list:
    """The paper specs; logs the arm file's state and every skip reason."""
    if runs_dir is not None and day is not None:
        arm = read_arm_file(runs_dir, day)
        if arm.present:
            alerter.log(f"arm file {arm.path.name}: {'valid' if arm.valid else 'INVALID ' + '; '.join(arm.problems)}")
    paper, other = select(registry)
    for sid, reason in other:
        alerter.log(f"skip {sid}: {reason}")
    return paper
