"""Per-day state file and a pid lock file (idempotency: a rerun never double-starts a session)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_state(path: Path, day: str) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("date") == day:
            return doc
    except (OSError, ValueError):
        pass
    return {"date": day, "preflight": None, "strategies": {}, "post_close": None}


def save_state(path: Path, doc: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def phase_lock(path: Path, alive: Callable[[int], bool] = pid_alive) -> Iterator[bool]:
    """Yields True when this process now owns the lock, False when a live process holds it.

    A lock left by a dead pid is taken over. The lock file is removed on exit."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                holder = int(path.read_text().strip() or "0")
            except (OSError, ValueError):
                holder = 0
            if holder and holder != os.getpid() and alive(holder):
                break
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        owned = True
        break
    try:
        yield owned
    finally:
        if owned:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
