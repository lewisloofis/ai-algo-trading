"""Which strategies the runner may touch: whatever modes.decide_mode says is 'paper'."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from runner.alerts import Alerter
from runner.modes import PAPER, decide_mode, read_arm_file


def select(registry=None) -> Tuple[list, List[Tuple[str, str]]]:
    """(paper specs sorted by id, [(strategy_id, reason)] of every other spec)."""
    if registry is None:
        from strategy_fit.emitter_specs import SPECS as registry  # noqa: N811
    paper, other = [], []
    for s in sorted(registry.values(), key=lambda s: s.strategy_id):
        m = decide_mode(s)
        (paper if m.mode == PAPER else other).append(s if m.mode == PAPER else (s.strategy_id, m.reason))
    return paper, other


def select_and_log(registry, alerter: Alerter, runs_dir: Optional[Path] = None, day: Optional[date] = None) -> list:
    if runs_dir is not None and day is not None:
        arm = read_arm_file(runs_dir, day)
        if arm.present:
            alerter.log(f"arm file {arm.path.name}: {'valid' if arm.valid else 'INVALID ' + '; '.join(arm.problems)}; "
                        "ignored (live not supported in this version)")
    paper, other = select(registry)
    for sid, reason in other:
        alerter.log(f"skip {sid}: {reason}")
    return paper
