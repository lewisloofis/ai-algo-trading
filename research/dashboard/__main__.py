"""
CLI: build the multi-page dashboard.

    python -m dashboard build [--out DIR]

One command builds every page. It finds its own inputs (runs/*.db, the newest status json, the
latest corrected strategy-fit run, research/data/daily) and runs the collector health check
itself. Options exist only to point somewhere else or to skip the (slow, network) health check.
"""

from __future__ import annotations

import argparse
import sys

from dashboard import config
from dashboard.site import SiteOptions, build_site
from dashboard.strategy_map import UnmappedStrategyError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m dashboard", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="build every page (the default command)")
    for q in (b, p):
        q.add_argument("--out", default=config.DEFAULT_OUT_DIR, help=f"output directory (default {config.DEFAULT_OUT_DIR})")
        q.add_argument("--runs", default=config.DEFAULT_RUNS_DIR, help="directory holding the post-trade stores (runs/*.db)")
        q.add_argument("--daily-dir", default=config.DEFAULT_DAILY_DIR, help="daily price files (research/data/daily)")
        q.add_argument("--annotations", default=None, help="annotations JSON (default: the checked-in file)")
        g = q.add_mutually_exclusive_group()
        g.add_argument("--no-healthcheck", action="store_true",
                       help="do not run the collector health check (rows say 'Not checked')")
        g.add_argument("--healthcheck-json", default=None,
                       help="use a saved `collector_healthcheck.py --json` output instead of running it")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(argv)
    healthcheck = "skip" if args.no_healthcheck else (args.healthcheck_json or "run")
    opt = SiteOptions(
        runs_dir=args.runs,
        daily_dir=args.daily_dir,
        out_dir=args.out,
        annotations_path=args.annotations,
        healthcheck=healthcheck,
    )
    try:
        written = build_site(opt, log=lambda m: print(f"dashboard: {m}", file=sys.stderr))
    except UnmappedStrategyError as exc:
        # Join safety: an order from a strategy this dashboard cannot describe is never guessed at.
        print(f"dashboard: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 3
    print(f"dashboard: wrote {len(written)} pages to {args.out}")
    print(f"dashboard: open {args.out}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
