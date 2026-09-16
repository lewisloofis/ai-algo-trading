"""
dashboard/glossary.py — the glossary, extracted from the code that owns each
definition (plan-19 §2 D3).

Nothing here writes a definition.  Entries are read out of:

* ``strategy_fit.harness.CellResult`` — attribute docstrings and the numpydoc
  ``Attributes`` block of the class docstring.
* the Go row structs in ``strategy/engine/store.go`` — the post-trade schema.
* ``engine.Counters``, ``engine.StatusDoc``, ``engine.RiskSnapshot`` and
  ``engine.ProgressSnapshot`` in ``strategy/engine/{engine,status}.go``.

Why generated rather than written: these definitions carry distinctions a
prose glossary flattens, and the flattening has already cost real time —
``net_edge_bps`` counts the friction floor exactly once; ``eff_n`` is
de-overlapped, not a trade count; ``oos_required_eff_n`` is power-derived and
is NOT the ``MIN_OOS_EFF_N`` hard floor; ``oos_underpowered=False`` also covers
"power could not be assessed at all"; ``impact_priced=False`` means impact was
never priced, not that it is zero.  A hand-maintained copy of those sentences
is wrong within two plans.

A column rendered without a glossary entry fails the build — see
``missing_entries`` and test_glossary.py.
"""

from __future__ import annotations

import ast
import functools
import os
import re
import textwrap
from dataclasses import dataclass
from typing import Iterable, Mapping

# ---------------------------------------------------------------------------
# Source locations
# ---------------------------------------------------------------------------

_RESEARCH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_RESEARCH_DIR)

HARNESS_PY = os.path.join(_RESEARCH_DIR, "strategy_fit", "harness.py")
STORE_GO = os.path.join(_REPO_ROOT, "strategy", "engine", "store.go")
ENGINE_GO = os.path.join(_REPO_ROOT, "strategy", "engine", "engine.go")
STATUS_GO = os.path.join(_REPO_ROOT, "strategy", "engine", "status.go")


@dataclass(frozen=True)
class Entry:
    """One glossary entry."""

    key: str
    """Namespaced identifier, e.g. ``cell.net_edge_bps``."""
    term: str
    """The field name as rendered in a column header."""
    source: str
    """Where the definition was read from, shown so a reader can go check it."""
    definition: str
    """The docstring or doc comment, verbatim apart from dedenting."""


# ---------------------------------------------------------------------------
# Python: CellResult attribute docstrings
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return textwrap.dedent(text).strip()


def python_attribute_docs(path: str, class_name: str) -> dict[str, str]:
    """
    Field -> docstring for a dataclass, read from source.

    Attribute docstrings (a bare string literal after an annotated assignment)
    are not available at runtime — ``CellResult.net_edge_bps.__doc__`` does not
    exist — so this parses the AST.  Fields documented in the class docstring's
    numpydoc ``Attributes`` block instead are picked up by
    ``_numpydoc_attributes`` and merged, with the attribute docstring winning
    when a field has both.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            docs = _numpydoc_attributes(ast.get_docstring(node) or "")
            docs.update(_attribute_docstrings(node))
            return docs
    raise LookupError(f"{class_name} not found in {path}")


def _attribute_docstrings(node: ast.ClassDef) -> dict[str, str]:
    out: dict[str, str] = {}
    body = node.body
    for i, stmt in enumerate(body):
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        if i + 1 >= len(body):
            continue
        nxt = body[i + 1]
        if (
            isinstance(nxt, ast.Expr)
            and isinstance(nxt.value, ast.Constant)
            and isinstance(nxt.value.value, str)
        ):
            out[stmt.target.id] = _clean(nxt.value.value)
    return out


_NUMPYDOC_FIELD = re.compile(r"^(\w+)\s*:\s*(.+)$")


def _numpydoc_attributes(docstring: str) -> dict[str, str]:
    """
    Parse the ``Attributes`` section of a numpydoc class docstring.

    CellResult documents its first seventeen fields there and the rest as
    attribute docstrings; both are the definition, so both are glossary
    sources.
    """
    lines = docstring.splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines)
            if line.strip() == "Attributes" and i + 1 < len(lines) and set(lines[i + 1].strip()) == {"-"}
        )
    except StopIteration:
        return {}

    out: dict[str, str] = {}
    field: str | None = None
    type_hint = ""
    buf: list[str] = []

    def flush() -> None:
        if field:
            body = _clean("\n".join(buf))
            out[field] = f"*{type_hint}* — {body}" if body else f"*{type_hint}*"

    for line in lines[start + 2:]:
        if line.strip() and not line.startswith((" ", "\t")):
            m = _NUMPYDOC_FIELD.match(line.strip())
            if not m:
                break
            flush()
            field, type_hint = m.group(1), m.group(2).strip()
            buf = []
        elif field is not None:
            buf.append(line)
    flush()
    return out


# ---------------------------------------------------------------------------
# Go: struct field doc comments
# ---------------------------------------------------------------------------

_GO_FIELD = re.compile(r"^\s*([A-Z]\w*)\s+[\[\]\*\w\.\{\}]+")
_GO_COMMENT = re.compile(r"^\s*//\s?(.*)$")


def go_struct_docs(path: str, struct_name: str) -> dict[str, str]:
    """
    Exported field -> doc comment for one Go struct, read from source.

    Accepts both leading comment blocks (the idiomatic form, and what the
    post-trade row structs use) and a trailing ``// ...`` on the field line.
    """
    src = open(path, encoding="utf-8").read()
    marker = f"type {struct_name} struct {{"
    try:
        start = src.index(marker) + len(marker)
    except ValueError as exc:
        raise LookupError(f"{struct_name} not found in {path}") from exc
    end = src.index("\n}", start)

    out: dict[str, str] = {}
    pending: list[str] = []
    for line in src[start:end].splitlines():
        if not line.strip():
            pending = []
            continue
        comment = _GO_COMMENT.match(line)
        if comment:
            pending.append(comment.group(1))
            continue
        field = _GO_FIELD.match(line)
        if not field:
            pending = []
            continue
        name = field.group(1)
        doc = " ".join(p.strip() for p in pending).strip()
        if not doc and "//" in line:
            doc = line.split("//", 1)[1].strip()
        if doc:
            out[snake_case(name)] = _reflow(doc)
        pending = []
    return out


def _reflow(text: str) -> str:
    """Collapse a Go comment block's hard wrapping into flowing prose."""
    return re.sub(r"\s+", " ", text).strip()


def snake_case(name: str) -> str:
    """``NetPnlIDR`` -> ``net_pnl_idr``; matches the SQL column names."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

# (namespace, human source label, loader)
_SOURCES: tuple[tuple[str, str, str, str, str], ...] = (
    ("cell", "strategy_fit/harness.py — CellResult", "py", HARNESS_PY, "CellResult"),
    ("decision", "strategy/engine/store.go — DecisionRow", "go", STORE_GO, "DecisionRow"),
    ("order_intent", "strategy/engine/store.go — IntentRow", "go", STORE_GO, "IntentRow"),
    ("fill", "strategy/engine/store.go — FillRow", "go", STORE_GO, "FillRow"),
    ("attribution", "strategy/engine/store.go — AttributionRow", "go", STORE_GO, "AttributionRow"),
    ("run", "strategy/engine/store.go — RunRow", "go", STORE_GO, "RunRow"),
    ("counters", "strategy/engine/engine.go — Counters", "go", ENGINE_GO, "Counters"),
    ("status", "strategy/engine/status.go — StatusDoc", "go", STATUS_GO, "StatusDoc"),
    ("progress", "strategy/engine/status.go — ProgressSnapshot", "go", STATUS_GO, "ProgressSnapshot"),
    ("risk", "strategy/engine/status.go — RiskSnapshot", "go", STATUS_GO, "RiskSnapshot"),
)


@functools.lru_cache(maxsize=1)
def build_glossary() -> dict[str, Entry]:
    """
    Every entry, keyed ``<namespace>.<field>``.

    Cached: the sources are files on disk that do not change inside one render.
    """
    entries: dict[str, Entry] = {}
    for namespace, label, kind, path, symbol in _SOURCES:
        docs = (
            python_attribute_docs(path, symbol) if kind == "py"
            else go_struct_docs(path, symbol)
        )
        for field, definition in docs.items():
            key = f"{namespace}.{field}"
            entries[key] = Entry(key=key, term=field, source=label, definition=definition)
    entries.update(DERIVED_ENTRIES)
    return entries


# Columns the dashboard renders that are a named aggregation of a stored field
# rather than a field of their own.  Each one names the code that owns the
# underlying definition, and says plainly what it is NOT — the flattening these
# would otherwise invite is the whole reason D3 exists.
DERIVED_ENTRIES: Mapping[str, Entry] = {
    e.key: e
    for e in (
        Entry(
            key="panel_c.win_rate",
            term="win_rate",
            source="dashboard/panel_c.py (aggregate of attribution.net_pnl_bps)",
            definition=(
                "Fraction of completed round-trips with net_pnl_bps > 0. This is a "
                "COUNT over stored rows, not lib.stats.nonoverlap_hit_rate: the "
                "de-overlapping that function performs is unnecessary here because "
                "engine.recordRoundTrip closes a symbol's open leg before opening "
                "another, so round-trips for one symbol never overlap by "
                "construction. It is also not comparable to a research cell's "
                "hit_rate, which is measured per forward-return window."
            ),
        ),
        Entry(
            key="panel_c.mean_net_pnl_bps",
            term="mean_net_pnl_bps",
            source="dashboard/panel_c.py (aggregate of attribution.net_pnl_bps)",
            definition=(
                "Mean net_pnl_bps across the group's round-trips — deliberately the "
                "same arithmetic as engine.finalize's Counters.NetPnlBps "
                "(sum of net bps / round-trips), so the dashboard and the run's own "
                "printed counter cannot disagree. Fees are included; financing and "
                "market impact are NOT — those are modelled in research, never by "
                "the fill simulator."
            ),
        ),
        Entry(
            key="panel_c.fee_slippage_gap_bps",
            term="fee_slippage_gap_bps",
            source="dashboard/panel_c.py (attribution.gross_pnl_bps − net_pnl_bps)",
            definition=(
                "Mean gross_pnl_bps minus mean net_pnl_bps: what execution cost, in "
                "bps, between the two legs' fees. It does not include impact or "
                "financing, so it is a floor on realised friction, not the full "
                "cost hurdle a research cell is measured against "
                "(cell.min_edge_hurdle_bps)."
            ),
        ),
        Entry(
            key="panel_c.required_effective_n",
            term="required_effective_n",
            source="lib/stats.py — required_effective_n (imported, not reimplemented)",
            definition=(
                "Power-derived minimum number of independent round-trips needed to "
                "resolve the observed mean net edge at z=2 standard errors, given "
                "the observed per-trade dispersion. Plan-11 §7.8's deployability "
                "screen: a strategy whose realised N is far below this number has "
                "not yet produced a falsifiable result, however good its mean looks. "
                "It is a power calculation, NOT a not-zero floor."
            ),
        ),
        Entry(
            key="panel_a.funnel_stage",
            term="funnel_stage",
            source="strategy_fit/harness.py — CellResult.status, plus report.py's gates",
            definition=(
                "Where a catalog cell died. The first five stages are "
                "CellResult.status verbatim (data_blocked, skipped_horizon, "
                "insufficient_n, cost_gated, decayed); the remaining three split the "
                "'tested' survivors by the gates report.py applies — FDR rejection "
                "(fdr_accepted), the always-long null gate (report._clears_null_gate), "
                "and PASS (report._is_pass_eligible). Every cell reaches exactly one "
                "stage; see panel_a.funnel_conservation_error."
            ),
        ),
        Entry(
            key="panel_b.heartbeat_age_s",
            term="heartbeat_age_s",
            source="dashboard/panel_b.py (now − status.wall_clock_ns)",
            definition=(
                "Seconds since strategy-run last rewrote status.json, measured "
                "against WALL CLOCK, never against progress.last_event_ns. In a "
                "replay the event clock is historical — a healthy replay of the "
                "2026-06-22 session has an event age of months and a heartbeat age "
                "of seconds."
            ),
        ),
    )
}


def missing_entries(keys: Iterable[str]) -> list[str]:
    """
    Which of *keys* have no glossary entry.

    Plan-19 §8: a rendered column without a definition fails the build. The
    check is here rather than in each panel so there is one answer.
    """
    g = build_glossary()
    return sorted(k for k in keys if k not in g)


def require_entries(keys: Iterable[str]) -> None:
    """Raise if any of *keys* lacks a glossary entry."""
    missing = missing_entries(keys)
    if missing:
        raise KeyError(
            "glossary has no entry for rendered column(s): "
            + ", ".join(missing)
            + " — add the definition to the code that owns the field "
              "(a docstring or Go doc comment), not to the dashboard"
        )
