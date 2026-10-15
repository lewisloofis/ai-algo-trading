"""The single chokepoint for child processes. Nothing runs without passing runner.guard."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Dict, Mapping, Sequence

from runner.guard import validate_argv
from runner.state import pid_alive

TIMEOUT_RC = 124


class Executor:
    def __init__(self, run=subprocess.run, popen=subprocess.Popen, registry=None):
        self._run, self._popen, self._registry = run, popen, registry
        self._children: Dict[int, "subprocess.Popen"] = {}

    def run(self, argv: Sequence[str], env: Mapping[str, str], cwd: Path, log: Path,
            timeout: float | None = None) -> int:
        validate_argv(argv, self._registry)
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with open(log, "ab") as f:
            try:
                return self._run(list(argv), env=dict(env), cwd=str(cwd), stdout=f,
                                 stderr=subprocess.STDOUT, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                return TIMEOUT_RC

    def spawn(self, argv: Sequence[str], env: Mapping[str, str], cwd: Path, log: Path,
              rc_file: Path, python: str) -> int:
        """Start argv under runner.supervise (which records its exit code in rc_file). Returns the pid."""
        validate_argv(argv, self._registry)
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(rc_file).unlink(missing_ok=True)
        wrapper = [python, "-m", "runner.supervise", "--rc-file", str(rc_file), "--", *argv]
        with open(log, "ab") as f:
            proc = self._popen(wrapper, env=dict(env), cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True)
        self._children[proc.pid] = proc
        return proc.pid

    def alive(self, pid: int) -> bool:
        child = self._children.get(pid)
        if child is not None:
            return child.poll() is None       # also reaps, so a finished child is not a zombie "alive"
        return pid_alive(pid)

    def interrupt(self, pid: int) -> None:
        """Graceful stop (SIGINT); the supervise wrapper forwards it to live-session."""
        try:
            os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
