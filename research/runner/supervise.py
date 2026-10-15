"""Tiny wrapper that runs ONE guarded command and records its exit code.

    python -m runner.supervise --rc-file F -- <argv...>

The watchdog (a different process from `start`) can then tell "exited with 0" from "killed" by
the presence and content of the rc file. SIGINT/SIGTERM are forwarded to the child."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from runner.guard import LiveGuardError, validate_argv


def main(argv: Optional[Sequence[str]] = None, popen=subprocess.Popen) -> int:
    p = argparse.ArgumentParser(prog="runner.supervise")
    p.add_argument("--rc-file", required=True)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    try:
        validate_argv(cmd)
    except LiveGuardError as exc:
        print(f"supervise: refused: {exc}", file=sys.stderr)
        _write_rc(args.rc_file, 126)
        return 126
    child = popen(cmd)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda s, _f, c=child: c.send_signal(s))
    rc = child.wait()
    _write_rc(args.rc_file, rc)
    return rc


def _write_rc(path: str, rc: int) -> None:
    tmp = Path(path + ".tmp")
    tmp.write_text(json.dumps({"rc": rc, "finished_at": time.time()}))
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
