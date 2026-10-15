"""Read and atomically update KEY=VALUE pairs in an env file (``.env.local``).

Updates are serialised with an flock, written to a 0600 temp file in the same directory, fsynced
and renamed over the target, so a reader never sees a half-written file and the mode is always
0600. Unrelated lines (comments, other secrets, ordering) are preserved byte for byte. No
function here ever puts a value in an exception message."""

from __future__ import annotations

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Mapping

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BAD_VALUE_CHARS = set(" \t\r\n\"'#\\$`")


def _parse_line(raw: str):
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    key, sep, value = line.partition("=")
    key = key.strip()
    if not sep or not _KEY_RE.match(key):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def read_env(path: Path) -> Dict[str, str]:
    """Parsed pairs (last assignment wins, like sourcing). Missing file -> {}."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        parsed = _parse_line(raw)
        if parsed:
            out[parsed[0]] = parsed[1]
    return out


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    lock_path = Path(str(path) + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _validate(updates: Mapping[str, str]) -> None:
    for key, value in updates.items():
        if not _KEY_RE.match(key):
            raise ValueError("invalid env key name")
        if not value or any(c in _BAD_VALUE_CHARS for c in value):
            raise ValueError(f"refusing to write {key}: empty or contains unsafe characters")


def update_env(path: Path, updates: Mapping[str, str]) -> None:
    """Set each key (replacing every existing assignment, else appending). Atomic, mode 0600."""
    _validate(updates)
    target = Path(os.path.realpath(path))
    with file_lock(target):
        try:
            lines: List[str] = target.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        pending = dict(updates)
        out: List[str] = []
        for raw in lines:
            parsed = _parse_line(raw)
            if parsed and parsed[0] in updates:
                prefix = "export " if raw.lstrip().startswith("export ") else ""
                out.append(f"{prefix}{parsed[0]}={updates[parsed[0]]}")
                pending.pop(parsed[0], None)
            else:
                out.append(raw)
        for key, value in pending.items():
            out.append(f"{key}={value}")
        _atomic_write(target, "\n".join(out) + "\n")


def _atomic_write(target: Path, text: str) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
