"""
dashboard/render.py — HTML primitives for the generated page.

One self-contained file, inline CSS, and no JavaScript at all (plan-19 §2 D6):
collapsible sections are ``<details>``, glossary links are anchors. A page that
needs a script to be readable has a runtime dependency that can be broken at
exactly the moment the thing being observed is broken.

Every column header that names a stored field renders as a link into the
glossary, and ``table()`` refuses to render a column whose key has no glossary
entry — that is plan-19 §8's "glossary completeness" check, enforced at the
only place columns are actually produced.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from dashboard.glossary import Entry, build_glossary, require_entries

CSS = """
:root {
  --ink: #1a1a1a; --muted: #6b7280; --line: #e5e7eb; --bg: #ffffff;
  --panel: #f9fafb; --ok: #15803d; --warn: #b45309; --fail: #b91c1c;
  --unknown: #6b7280; --accent: #1d4ed8; --synthetic: #7c3aed;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 1rem 4rem; background: var(--bg); color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
main { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 1.5rem 0 0.25rem; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 0.5rem; padding-top: 0.75rem; border-top: 2px solid var(--line); }
h3 { font-size: 1rem; margin: 1.5rem 0 0.5rem; color: var(--muted); font-weight: 600;
     text-transform: uppercase; letter-spacing: 0.04em; }
p, li { max-width: 78ch; }
a { color: var(--accent); }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
.sub { color: var(--muted); margin: 0 0 1rem; }
.banner { padding: 0.6rem 0.85rem; border-radius: 6px; margin: 0.5rem 0; border-left: 4px solid; }
.banner.synthetic { background: #f5f3ff; border-color: var(--synthetic); color: #5b21b6; }
.banner.live { background: #ecfdf5; border-color: var(--ok); color: #065f46; }
.banner.replay { background: #fffbeb; border-color: var(--warn); color: #92400e; }
.banner.warn { background: #fffbeb; border-color: var(--warn); color: #92400e; }
.banner.fail { background: #fef2f2; border-color: var(--fail); color: #991b1b; }
.banner.info { background: var(--panel); border-color: var(--line); color: var(--muted); }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; font-size: 13px; }
th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--panel); font-weight: 600; white-space: nowrap; }
th a { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--muted); }
th a:hover { color: var(--accent); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, monospace; }
tr:hover td { background: #fcfcfd; }
.pill { display: inline-block; padding: 0.05rem 0.45rem; border-radius: 999px;
        font-size: 11px; font-weight: 600; white-space: nowrap; }
.pill.ok { background: #dcfce7; color: var(--ok); }
.pill.warn { background: #fef3c7; color: var(--warn); }
.pill.fail { background: #fee2e2; color: var(--fail); }
.pill.unknown { background: #f3f4f6; color: var(--unknown); }
.bar { background: #eef2ff; height: 0.85rem; border-radius: 2px; display: block; min-width: 1px; }
.bar.pass { background: #bbf7d0; }
.bar.dead { background: #fecaca; }
details { margin: 0.4rem 0; }
summary { cursor: pointer; font-weight: 600; padding: 0.25rem 0; }
.gl dt { font-weight: 600; font-family: ui-monospace, Menlo, monospace; margin-top: 1rem; }
.gl dd { margin: 0.15rem 0 0 0; max-width: 80ch; white-space: pre-wrap; }
.gl .src { color: var(--muted); font-size: 12px; font-family: ui-monospace, Menlo, monospace; }
.empty { color: var(--muted); font-style: italic; padding: 0.75rem 0; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 12px; }
"""


def esc(value: Any) -> str:
    """HTML-escape any value. Everything user-visible goes through this."""
    return html.escape("" if value is None else str(value), quote=True)


def glossary_anchor(key: str) -> str:
    return "gl-" + key.replace(".", "-")


@dataclass(frozen=True)
class Column:
    """One rendered column, bound to the glossary entry that defines it."""

    key: str
    """Glossary key, e.g. ``attribution.net_pnl_bps``. None-able via ``glossary=False``."""
    header: str
    numeric: bool = False
    glossary: bool = True
    """
    False for presentational columns that name no stored field (a row label, a
    bar chart). Everything that shows a NUMBER a reader might act on must be
    True, so it cannot be rendered without a definition.
    """


def header_cell(col: Column) -> str:
    cls = ' class="num"' if col.numeric else ""
    if not col.glossary:
        return f"<th{cls}>{esc(col.header)}</th>"
    return (
        f'<th{cls}><a href="#{glossary_anchor(col.key)}" '
        f'title="see glossary">{esc(col.header)}</a></th>'
    )


def table(columns: Sequence[Column], rows: Iterable[Sequence[str]], empty: str = "No rows.") -> str:
    """
    Render a table, refusing any column that has no glossary entry.

    The check lives here because this is the one place a column becomes
    visible; putting it in each panel would let a new panel forget it.
    """
    require_entries(c.key for c in columns if c.glossary)
    body = list(rows)
    if not body:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(header_cell(c) for c in columns)
    out = [f"<table><thead><tr>{head}</tr></thead><tbody>"]
    for row in body:
        cells = "".join(
            f'<td class="num">{cell}</td>' if col.numeric else f"<td>{cell}</td>"
            for col, cell in zip(columns, row)
        )
        out.append(f"<tr>{cells}</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def pill(state: str, label: str | None = None) -> str:
    cls = state if state in ("ok", "warn", "fail") else "unknown"
    return f'<span class="pill {cls}">{esc(label or state)}</span>'


def banner(kind: str, text: str) -> str:
    return f'<div class="banner {esc(kind)}">{text}</div>'


def bar(share: float, kind: str = "") -> str:
    """A proportional bar, width as a percentage. Presentational only."""
    width = max(0.0, min(1.0, share)) * 100
    cls = f"bar {kind}".strip()
    return f'<span class="{cls}" style="width:{width:.1f}%"></span>'


def num(value: Any, decimals: int = 1, dash: str = "—") -> str:
    """Format a number, rendering None and NaN identically as an em dash."""
    if value is None:
        return dash
    if isinstance(value, float) and value != value:
        return dash
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.{decimals}f}"


def glossary_section(keys: Iterable[str]) -> str:
    """
    Render glossary entries for *keys*, each with the source that owns it.

    Definitions are printed verbatim (``white-space: pre-wrap``) because the
    distinctions they draw live in their exact wording.
    """
    g = build_glossary()
    wanted = sorted(set(keys))
    out = ['<h2 id="glossary">Glossary</h2>']
    out.append(
        '<p class="sub">Generated from the docstrings and Go doc comments of the code '
        "that produces each number — never hand-written here, so a definition cannot "
        "drift from its implementation (plan-19 §2 D3).</p>"
    )
    out.append('<dl class="gl">')
    for key in wanted:
        entry: Entry = g[key]
        out.append(f'<dt id="{glossary_anchor(key)}">{esc(entry.term)}</dt>')
        out.append(f'<dd><span class="src">{esc(entry.source)}</span></dd>')
        out.append(f"<dd>{esc(entry.definition)}</dd>")
    out.append("</dl>")
    return "\n".join(out)


def page(title: str, sections: Sequence[str], footer: str) -> str:
    """Wrap rendered sections in a complete, self-contained HTML document."""
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{esc(title)}</title>",
            f"<style>{CSS}</style>",
            "</head><body><main>",
            *sections,
            f"<footer>{footer}</footer>",
            "</main></body></html>",
            "",
        ]
    )
