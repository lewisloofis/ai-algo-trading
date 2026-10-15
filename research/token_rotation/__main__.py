"""python -m token_rotation <status|ensure|refresh> [--env-file F] [--min-hours N]

status   per-token 'expires in Xh' / 'expired' / 'unknown' / 'not set'. Never a value.
ensure   refresh only sessions with < --min-hours left (no-op otherwise). What the runner calls.
refresh  force-rotate now (the first real run).
Exit: 0 ok · 1 main session needs a manual re-login · 2 transient error · 3 ok but action/warning pending."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from token_rotation.rotate import DEFAULT_MIN_HOURS, ensure_tokens, token_status

REPO_ROOT = Path(__file__).resolve().parents[2]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="token_rotation", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("status", "ensure", "refresh"))
    ap.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env.local")
    ap.add_argument("--min-hours", type=float, default=DEFAULT_MIN_HOURS)
    args = ap.parse_args(argv)

    if args.command == "status":
        for st in token_status(args.env_file):
            print(st.line())
        return 0
    result = ensure_tokens(args.env_file, min_hours=args.min_hours, force=args.command == "refresh")
    print(result.summary)
    if args.command == "refresh" or result.refreshed:
        for st in token_status(args.env_file):
            print(st.line())
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
