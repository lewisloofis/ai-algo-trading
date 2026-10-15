"""The single chokepoint for child processes. Nothing runs without passing runner.guard."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from typing import Dict, Mapping, Sequence

from runner.guard import LiveCommand, validate_argv, validate_live_command, wrapper_env
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

    def spawn_live(self, cmd: LiveCommand, env: Mapping[str, str], cwd: Path, log: Path, rc_file: Path,
                   python: str, grant_path: Path, preflight_ok: bool) -> int:
        """Start ONE live-session under runner.supervise. The only door to a live process.

        ``cmd`` must be a sealed LiveCommand (so it came from modes.decide_mode via guard); it is
        re-verified here. The wrapper's environment carries the trading token but NOT ALGO_LIVE /
        ALGO_CONFIRM: supervise sets those, for live-session alone, after it has re-authorised the
        grant written next to the log. No restart logic exists for this path."""
        validate_live_command(cmd, self._registry)
        wenv = wrapper_env(cmd, env, self._registry)
        grant = {"strategy_id": cmd.strategy_id, "day": str(cmd.token.day), "max_idr": cmd.max_idr,
                 "repo_root": str(cmd.repo_root), "rebalance_at": cmd.rebalance_at, "data_dir": str(cmd.data_dir),
                 "preflight_ok": bool(preflight_ok)}
        Path(grant_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(grant_path) + ".tmp")
        tmp.write_text(json.dumps(grant, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, grant_path)
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(rc_file).unlink(missing_ok=True)
        wrapper = [python, "-m", "runner.supervise", "--rc-file", str(rc_file), "--live-grant", str(grant_path),
                   "--", *cmd.argv]
        with open(log, "ab") as f:
            proc = self._popen(wrapper, env=wenv, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
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
