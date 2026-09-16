"""
CLI: render the observability dashboard to a single HTML file.

    python -m dashboard --portfolio out_live/strategy_portfolio.json \
                        --store 'out_live/runs/*.db' \
                        --status out_live/status.json \
                        --out out_live/dashboard.html

Every input is optional. A panel with nothing to read says what is missing and
how to supply it, rather than rendering an empty table that looks like a
result.
"""

from __future__ import annotations

import argparse
import os
import sys

from dashboard.build import Inputs, write_dashboard
from dashboard.strategy_map import UnmappedStrategyError

DEFAULT_OUT = "out_live/dashboard.html"


def _default_previous(portfolio: str | None) -> str | None:
    """
    The prior sidecar, if one was kept beside the current one as
    ``strategy_portfolio.prev.json``. Convention over configuration: the run
    diff is the most valuable view here and should not need a flag to appear.
    """
    if not portfolio:
        return None
    candidate = portfolio.replace(".json", ".prev.json")
    return candidate if os.path.exists(candidate) else None


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--portfolio", default=None, help="strategy_portfolio.json sidecar (Panel A)")
    p.add_argument("--previous", default=None, help="prior sidecar for the run diff")
    p.add_argument("--store", action="append", default=[],
                   help="post-trade store path, glob, or directory (repeatable; Panel C)")
    p.add_argument("--status", default=None, help="status.json heartbeat (Panel B)")
    p.add_argument("--healthcheck", default=None,
                   help="output of `collector_healthcheck.py --json` (Panel B)")
    p.add_argument("--eligibility", default=None, help="a universe eligibility.json (Panel B)")
    p.add_argument("--out", default=DEFAULT_OUT, help=f"output HTML path (default {DEFAULT_OUT})")
    args = p.parse_args(argv)

    inputs = Inputs(
        portfolio_json=args.portfolio,
        previous_portfolio_json=args.previous or _default_previous(args.portfolio),
        store_globs=list(args.store),
        status_json=args.status,
        healthcheck_json=args.healthcheck,
        eligibility_json=args.eligibility,
    )
    try:
        path = write_dashboard(inputs, args.out)
    except UnmappedStrategyError as exc:
        # Plan-19 §8 join safety: fail the render rather than guess which
        # research family an unknown strategy id corresponds to.
        print(f"dashboard: {exc}", file=sys.stderr)
        return 2
    except KeyError as exc:
        # Glossary completeness: a rendered column with no definition.
        print(f"dashboard: {exc}", file=sys.stderr)
        return 3

    print(f"dashboard: wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
