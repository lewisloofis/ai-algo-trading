#!/usr/bin/env python3
"""
Reads calendar/deadlines.yaml and prints items due within the lead window.

Usage:
    python calendar/check_deadlines.py [--lead N]

Exit code 1 if any item is DUE SOON or OVERDUE.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

_DEADLINES_PATH = Path(__file__).parent / "deadlines.yaml"


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore[import]
        with path.open() as f:
            return yaml.safe_load(f)
    except ImportError:
        return _parse_yaml_simple(path)


def _parse_yaml_simple(path: Path) -> dict:
    """Minimal YAML parser covering only the deadlines.yaml schema."""
    lines = path.read_text().splitlines()
    result: dict = {"deadlines": []}
    current: dict | None = None
    desc_lines: list[str] = []
    in_desc = False

    def _flush_desc() -> None:
        nonlocal desc_lines, in_desc
        if current is not None and desc_lines:
            current["desc"] = " ".join(desc_lines).strip()
        desc_lines = []
        in_desc = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "deadlines:":
            continue

        if stripped.startswith("- id:"):
            _flush_desc()
            if current is not None:
                result["deadlines"].append(current)
            current = {"id": stripped[len("- id:"):].strip()}
            in_desc = False
            continue

        if current is None:
            continue

        if stripped.startswith("due:"):
            _flush_desc()
            current["due"] = stripped[len("due:"):].strip().strip('"')
        elif stripped.startswith("lead_days:"):
            _flush_desc()
            current["lead_days"] = int(stripped[len("lead_days:"):].strip())
        elif stripped.startswith("desc:"):
            _flush_desc()
            in_desc = True
            rest = stripped[len("desc:"):].strip().lstrip(">").strip()
            if rest:
                desc_lines.append(rest)
        elif in_desc:
            desc_lines.append(stripped)

    _flush_desc()
    if current is not None:
        result["deadlines"].append(current)

    return result


def _check(lead_days: int) -> int:
    data = _load_yaml(_DEADLINES_PATH)
    today = datetime.date.today()
    any_alert = False

    for item in data.get("deadlines", []):
        item_id = item.get("id", "?")
        due_str = item.get("due", "")
        item_lead = int(item.get("lead_days", lead_days))
        desc = item.get("desc", "").strip()

        try:
            due = datetime.date.fromisoformat(due_str)
        except ValueError:
            print(f"  SKIP  {item_id}: unparseable due date {due_str!r}")
            continue

        days_until = (due - today).days

        if days_until < 0:
            status = "OVERDUE"
            any_alert = True
        elif days_until <= item_lead:
            status = "DUE SOON"
            any_alert = True
        else:
            status = "OK"

        print(f"  {status:<9} {item_id}  (due {due_str}, {days_until}d away)")
        if status != "OK":
            first_line = desc.split(".")[0] if desc else ""
            if first_line:
                print(f"             {first_line}.")

    return 1 if any_alert else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check operational deadlines.")
    parser.add_argument("--lead", type=int, default=30, help="Alert window in days (default: 30)")
    args = parser.parse_args()
    sys.exit(_check(args.lead))


if __name__ == "__main__":
    main()
