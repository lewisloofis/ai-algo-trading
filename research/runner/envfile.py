"""Load KEY=VALUE pairs (a .env.local) into an environment mapping at runtime.

Values are never printed or logged; a parse problem reports the line NUMBER only. Existing
variables win (the shell's environment beats the file)."""

from __future__ import annotations

from pathlib import Path
from typing import List, MutableMapping, Tuple


def load_env_file(path: Path, env: MutableMapping[str, str]) -> Tuple[int, List[str]]:
    """Returns (keys added, problems). A missing file is not a problem: (0, [])."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0, []
    except OSError as exc:
        return 0, [f"could not read env file ({type(exc).__name__})"]
    added, problems = 0, []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.replace("_", "").isalnum() or key[0].isdigit():
            problems.append(f"env file line {n} is not KEY=VALUE")
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key not in env:
            env[key] = value
            added += 1
    return added, problems
