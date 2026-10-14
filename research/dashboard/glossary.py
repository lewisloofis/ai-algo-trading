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
    ("order_reject", "strategy/engine/store.go — RejectRow", "go", STORE_GO, "RejectRow"),
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
    return entries
