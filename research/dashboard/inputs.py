"""
dashboard/inputs.py — find everything the site is built from, so the caller gives nothing.

* post-trade stores:  runs/*.db minus config.EXCLUDED_STORES
* bot heartbeat:      the newest runs/*status*.json
* backtest results:   the newest registered corrected run (config.CORRECTED_RUN_DIRS)
* collector health:   run scripts/collector_healthcheck.py --json here and now

The health check is the only thing that may touch the network (it reads S3 with the
caller's own AWS credentials). If it cannot run, the outcome carries the reason and the
page says "Not checked: <reason>. Run: <command>" instead of an unexplained unknown.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Mapping

from dashboard import config


@dataclass(frozen=True)
class Stores:
    kept: tuple[str, ...]
    excluded: tuple[str, ...]


def discover_stores(runs_dir: str, excluded_names: frozenset[str] | None = None) -> Stores:
    """Every runs/*.db (not the -wal/-shm sidecars), split into read and deliberately skipped."""
    skip = config.EXCLUDED_STORES if excluded_names is None else excluded_names
    found = sorted(glob.glob(os.path.join(runs_dir, "*.db")))
    return Stores(
        kept=tuple(p for p in found if os.path.basename(p) not in skip),
        excluded=tuple(p for p in found if os.path.basename(p) in skip),
    )


def latest_status_json(runs_dir: str) -> str | None:
    """The status heartbeat file the runtime wrote most recently (by its own wall clock)."""
    best: tuple[int, str] | None = None
    for path in glob.glob(os.path.join(runs_dir, "*status*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                stamp = int(json.load(f).get("wall_clock_ns") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if best is None or stamp > best[0]:
            best = (stamp, path)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Collector health check
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HealthOutcome:
    """Either the health check's JSON, or the plain reason it could not be had."""

    doc: Mapping | None
    reason: str | None = None
    """Why it was not checked, in a sentence. None when ``doc`` is present."""

    @property
    def checked(self) -> bool:
        return self.doc is not None


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _tidy(text: str) -> str:
    lines = [ln.strip() for ln in text.replace(config.REPO_ROOT + os.sep, "").splitlines() if ln.strip()]
    return (lines[-1] if lines else "no error message")[:400]


def not_checked(reason: str) -> HealthOutcome:
    return HealthOutcome(doc=None, reason=reason)


def run_healthcheck(
    enabled: bool = True,
    script: str = config.HEALTHCHECK_SCRIPT,
    timeout_s: int = config.HEALTHCHECK_TIMEOUT_S,
    runner: Runner = subprocess.run,
) -> HealthOutcome:
    """
    Run ``collector_healthcheck.py --json`` and return its parsed output.

    The script exits 0/1/2 (clear / warnings / failures); all three can carry JSON. No
    JSON on stdout means the check could not run at all (usually: no AWS credentials).
    """
    if not enabled:
        return not_checked("the health check was skipped for this build (--no-healthcheck)")
    if not os.path.exists(script):
        return not_checked(f"{script} was not found")
    try:
        proc = runner(
            [sys.executable, script, "--json", "--quiet"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=config.REPO_ROOT,
        )
    except subprocess.TimeoutExpired:
        return not_checked(f"the health check did not finish within {timeout_s // 60} minutes")
    except OSError as exc:
        return not_checked(f"the health check could not be started ({exc})")
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return not_checked(f"the health check could not run here: {_tidy(proc.stderr)}")
    if not isinstance(doc, dict):
        return not_checked("the health check printed something unexpected")
    return HealthOutcome(doc=doc)


def load_healthcheck_file(path: str) -> HealthOutcome:
    """Use a saved ``collector_healthcheck.py --json`` output instead of running it."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        return not_checked(f"the saved health-check file {path} could not be read ({exc})")
    return HealthOutcome(doc=doc) if isinstance(doc, dict) else not_checked(f"{path} is not a health-check result")
