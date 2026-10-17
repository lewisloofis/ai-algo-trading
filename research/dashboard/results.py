"""
dashboard/results.py — read a strategy-fit run's ``strategy_portfolio.md``.

The corrected re-score (research/results/d1) was published as markdown only: the
machine-readable ``strategy_portfolio.json`` sidecar was not kept. So the
dashboard reads the report's own two tables:

* **Surviving Strategies** — the cells that cleared every gate, with the harness's
  own figures (hit rate, net edge, effective N, hurdle, OOS edge, ...).
* **Coverage Table** — every other cell with its terminal status and the reason
  string the harness wrote (``cost_gated: net_after_cost=-87.3 bps ... hurdle=74.3``).

Nothing is re-judged here. A cell's *gate outcome* is the status word the harness
printed; this module only turns that text into typed fields. Numbers that appear in
reason strings (the cost hurdle, p-values) are parsed out, never recomputed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable

SIDECAR_MD = "strategy_portfolio.md"

# Gate outcomes, exactly the harness's vocabulary (report.py / CellResult.status).
GATE_PASSED_ALL = "passed_all_gates"
GATE_COST = "cost_gated"
GATE_FDR = "fdr_rejected"
GATE_NULL = "loses_to_null"
GATE_OOS = "oos_not_persistent"
GATE_IMPACT_UNPRICED = "impact_not_priced"
GATE_DECAYED = "decayed"
GATE_INSUFFICIENT_N = "insufficient_n"
GATE_LONG_SHORT = "long_short_infeasible"
GATE_BLOCKED = "data_blocked"
GATE_DEFERRED = "deferred"
GATE_OTHER = "other"

_REASON_PREFIXES = (
    ("cost_gated", GATE_COST),
    ("fdr_rejected", GATE_FDR),
    ("loses_to_null", GATE_NULL),
    ("impact_not_priced", GATE_IMPACT_UNPRICED),
    ("invalid split", GATE_OOS),
    ("thin OOS", GATE_OOS),
)

_COST_RE = re.compile(r"hurdle=([\d.]+)")
_P_RE = re.compile(r"p=([\d.]+)")
_NET_RE = re.compile(r"net_after_cost=(-?[\d.]+)")
_GROSS_RE = re.compile(r"gross=(-?[\d.]+)")
_NULL_RE = re.compile(r"(?<![\w])null=(-?[\d.]+)")
_GENERATED_RE = re.compile(r"\*\*Generated:\*\*\s*(\S+)")
_CAPITAL_RE = re.compile(r"\*\*Capital model:\*\*\s*(.+)")


@dataclass(frozen=True)
class ResultCell:
    """One (symbol, family, hold) cell as the harness reported it."""

    symbol: str
    family: str
    hold: int
    shape: str
    gate: str
    """One of the GATE_* constants — the harness's own outcome word."""
    reason: str
    hold_label: str = ""
    """The report's hold text when it is not a number of days (e.g. '5+', '<1min'); hold is then 0."""
    hurdle_bps: float | None = None
    """The harness's full cost hurdle for this cell, parsed from its report."""
    p_value: float | None = None
    gross_edge_bps: float | None = None
    net_edge_bps: float | None = None
    null_edge_bps: float | None = None
    hit_rate: float | None = None
    eff_n: int | None = None
    oos_edge_bps: float | None = None
    oos_eff_n: int | None = None
    oos_underpowered: bool | None = None
    edge_over_null_bps: float | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.symbol, self.family, self.hold)

    @property
    def is_single_name(self) -> bool:
        return self.shape == "single_name_time_series"


@dataclass(frozen=True)
class RunResults:
    """A parsed strategy-fit report."""

    path: str
    generated: str
    capital_model: str
    cells: tuple[ResultCell, ...]
    symbol_hurdles: dict[str, float] = field(default_factory=dict)

    def cell(self, symbol: str, family: str, hold: int) -> ResultCell | None:
        for c in self.cells:
            if c.key == (symbol, family, hold):
                return c
        return None


class ResultsError(RuntimeError):
    """The report is missing or not in the shape this parser understands."""


def _float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def _rows(lines: Iterable[str]) -> Iterable[list[str]]:
    for ln in lines:
        if ln.startswith("|") and not ln.startswith("|-") and not ln.startswith("| -"):
            yield [c.strip() for c in ln.strip().strip("|").split("|")]


def _section(lines: list[str], title: str) -> list[str]:
    out: list[str] = []
    inside = False
    for ln in lines:
        if ln.startswith("## "):
            if inside:
                break
            inside = ln[3:].startswith(title)
            continue
        if inside:
            out.append(ln)
    return out


def classify_reason(status: str, reason: str) -> str:
    """Map the harness's status word and reason text to a GATE_* outcome."""
    if status != "tested":
        known = {
            "decayed": GATE_DECAYED,
            "insufficient_n": GATE_INSUFFICIENT_N,
            "long_short_infeasible": GATE_LONG_SHORT,
            "data_blocked": GATE_BLOCKED,
            "deferred": GATE_DEFERRED,
        }
        return known.get(status, GATE_OTHER)
    for prefix, gate in _REASON_PREFIXES:
        if reason.startswith(prefix):
            return gate
    return GATE_OTHER


def _survivor(cols: list[str]) -> ResultCell | None:
    # Symbol | Strategy | Hold | Shape | Hit Rate | Net Edge | Eff-N | Best Entry Day |
    # Friction Hurdle | Impact | Null Edge | Edge over Null | OOS Edge | OOS Eff-N |
    # OOS Persistent | Decay Metric
    if len(cols) < 15 or not cols[2].isdigit():
        return None
    net = _float(cols[5])
    null = _float(cols[10])
    return ResultCell(
        symbol=cols[0],
        family=cols[1],
        hold=int(cols[2]),
        shape=cols[3],
        gate=GATE_PASSED_ALL,
        reason="cleared every gate",
        hurdle_bps=_float(cols[8]),
        net_edge_bps=net,
        null_edge_bps=null,
        hit_rate=_float(cols[4]),
        eff_n=_int(cols[6]),
        edge_over_null_bps=_float(cols[11]),
        oos_edge_bps=_float(cols[12]),
        oos_eff_n=_int(cols[13]),
        oos_underpowered="UNDERPOWERED" in cols[14],
        gross_edge_bps=(net + _float(cols[8])) if net is not None and _float(cols[8]) is not None else None,
    )


def _coverage(cols: list[str]) -> ResultCell | None:
    # Symbol | Strategy | Hold | Shape | Status | Reason / Unblock
    if len(cols) < 5:
        return None
    status = cols[4]
    reason = " | ".join(cols[5:]) if len(cols) > 5 else ""
    hurdle = _COST_RE.search(reason)
    p = _P_RE.search(reason) if reason.startswith("fdr_rejected") else None
    net = _NET_RE.search(reason)
    gross = _GROSS_RE.search(reason)
    null = _NULL_RE.search(reason)
    return ResultCell(
        symbol=cols[0],
        family=cols[1],
        hold=int(cols[2]) if cols[2].isdigit() else 0,
        hold_label="" if cols[2].isdigit() else cols[2],
        shape=cols[3],
        gate=classify_reason(status, reason),
        reason=reason,
        hurdle_bps=_float(hurdle.group(1)) if hurdle else None,
        p_value=_float(p.group(1)) if p else None,
        gross_edge_bps=_float(gross.group(1)) if gross else None,
        net_edge_bps=_float(net.group(1)) if net else None,
        null_edge_bps=_float(null.group(1)) if null else None,
    )


def parse_report(text: str, path: str = "") -> RunResults:
    """Parse the text of a strategy_portfolio.md."""
    lines = text.splitlines()
    gen = _GENERATED_RE.search(text)
    cap = _CAPITAL_RE.search(text)
    if not gen:
        raise ResultsError(f"{path or 'report'}: no '**Generated:**' line; not a strategy_portfolio.md")

    cells: list[ResultCell] = []
    for cols in _rows(_section(lines, "Surviving Strategies")):
        cell = _survivor(cols)
        if cell:
            cells.append(cell)
    for cols in _rows(_section(lines, "Coverage Table")):
        cell = _coverage(cols)
        if cell:
            cells.append(cell)
    if not cells:
        raise ResultsError(f"{path or 'report'}: no table rows found")

    hurdles: dict[str, float] = {}
    for c in cells:
        if c.hurdle_bps is not None and c.is_single_name:
            hurdles.setdefault(c.symbol, c.hurdle_bps)
    return RunResults(
        path=path,
        generated=gen.group(1),
        capital_model=(cap.group(1).strip() if cap else ""),
        cells=tuple(cells),
        symbol_hurdles=hurdles,
    )


def load_report(path: str) -> RunResults:
    """Read and parse one report file; raises ResultsError if unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return parse_report(f.read(), path)
    except OSError as exc:
        raise ResultsError(f"cannot read {path}: {exc}") from exc


def latest_report(research_dir: str, run_dirs: Iterable[str]) -> RunResults | None:
    """
    The newest-generated report among the registered corrected runs.

    *run_dirs* are paths relative to *research_dir* (config.CORRECTED_RUN_DIRS).
    Only registered directories are considered: older ``out_*`` dirs predate the
    entry-timing fix and must never be shown as current.
    """
    best: RunResults | None = None
    for rel in run_dirs:
        path = os.path.join(research_dir, rel, SIDECAR_MD)
        if not os.path.exists(path):
            continue
        res = load_report(path)
        if best is None or res.generated > best.generated:
            best = res
    return best
