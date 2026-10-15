"""
dashboard/cost_view.py — the Trades page's "What trading really costs" block.

Shows, and never computes: it reads ``research/out_cost_report/cost_report.json`` (written by
``make cost-report``, strategy_fit/cost_report.py) and prints its counts and averages. Real fills and
practice fills are kept in separate rows; the practice row only checks the simulator.
"""

from __future__ import annotations

import json
import os

from dashboard.render import Column, banner, esc, table

REPORT_REL = os.path.join("out_cost_report", "cost_report.json")
RUN_HINT = "make cost-report"

COLS = (
    Column("Fills"),
    Column("Number of fills", numeric=True),
    Column("Round trips", numeric=True),
    Column("Broker fee minus the 15/25 bps schedule", numeric=True),
    Column("Slippage against the price when the order was sent", numeric=True),
    Column("Enough data to conclude?"),
)


def load_cost_report(research_dir: str) -> dict | None:
    """The report, or None when it was never built or cannot be read."""
    try:
        with open(os.path.join(research_dir, REPORT_REL), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    ok = isinstance(doc, dict) and isinstance(doc.get("aggregates"), dict) and isinstance(doc.get("coverage"), dict)
    return doc if ok else None


def _mean_cell(stat: dict) -> str:
    if not stat.get("n"):
        return "not measured"
    return f"{stat['mean']:+.1f} bps (n={stat['n']})"


def _row(doc: dict, source: str, label: str) -> list[str]:
    overall = doc["aggregates"][source]["overall"]
    trips = doc.get("round_trip_aggregates", {}).get(source, {}).get("overall", {})
    n_trips = (trips.get("realised_fee_bps") or {}).get("n", 0)
    enough = overall["fee_resid_bps"].get("enough", False)
    return [esc(label), str(doc["coverage"][source]["fills"]), str(n_trips),
            esc(_mean_cell(overall["fee_resid_bps"])), esc(_mean_cell(overall["slippage_decision_bps"])),
            "yes" if enough else "No: too few to conclude"]


def cost_section(doc: dict | None) -> str:
    if doc is None:
        return ("<h2>What trading really costs</h2>"
                f'<p class="small">Not measured yet. Run: <code>{esc(RUN_HINT)}</code></p>')
    rows = [_row(doc, "live", "Real money"), _row(doc, "paper", "Practice (simulated)")]
    return (
        "<h2>What trading really costs, against what the backtests assume</h2>"
        + banner("paper", "Practice fills are simulated, so their row only checks the simulator, not the market. "
                          "Only the real-money row is evidence, and it is too small to conclude anything about the "
                          "market. Spread and market impact are not measured: no order records the price on the "
                          "book when it was sent.")
        + table(COLS, rows, empty="No fills recorded.")
        + f'<p class="small">Built {esc(str(doc.get("generated", "?"))[:16])} by <code>{esc(RUN_HINT)}</code>. '
          "Full tables by stock, side and size: <code>research/out_cost_report/cost_report.md</code>.</p>"
    )
