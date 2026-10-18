"""
dashboard/render.py — HTML primitives shared by every page.

Static HTML with inline CSS. No JavaScript, no CDN, no web fonts: a page that needs a script
or the network to be readable fails exactly when something is broken.

Two safety properties live here because every page passes through here:

* ``scrub`` removes anything shaped like a Stockbit trading-account number from the final
  HTML. Some stored fields (annotations, provenance) carry one; it must never be rendered.
* ``Column(term=...)`` links a column header to its plain-language glossary entry and fails
  the build if that entry does not exist.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from dashboard import hover
from dashboard.terms import require_term, term_anchor

CSS = """
:root {
  color-scheme: dark light;
  --ink: #e7e9ee; --muted: #9097a8; --line: #2a2f3d; --bg: #0f1115; --panel: #161922; --panel-2: #1d2230;
  --green: #4ade80; --green-bg: #14301f; --amber: #f5b454; --amber-bg: #3a2c12;
  --red: #f47174; --red-bg: #3a1b1d; --grey: #9097a8; --grey-bg: #232838;
  --accent: #7aa2ff; --accent-bg: #1c2744; --violet: #b79cff; --violet-bg: #251c3d;
}
@media (prefers-color-scheme: light) {
  :root {
    --ink: #1a1d24; --muted: #5d6779; --line: #e3e6ec; --bg: #fafbfc; --panel: #ffffff; --panel-2: #f3f5f8;
    --green: #10864a; --green-bg: #e3f5ea; --amber: #b46500; --amber-bg: #fdf1d9;
    --red: #c42c34; --red-bg: #fbe6e6; --grey: #5d6779; --grey-bg: #eef1f6;
    --accent: #2e6cf6; --accent-bg: #e6edfc; --violet: #6d28d9; --violet-bg: #f1ecff;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
nav.top { background: var(--panel); border-bottom: 1px solid var(--line); padding: 0 1rem; position: sticky; top: 0; z-index: 5; }
nav.top div { max-width: 1160px; margin: 0 auto; display: flex; gap: 0.25rem; flex-wrap: wrap; }
nav.top a { color: var(--muted); text-decoration: none; padding: 0.7rem 0.9rem; font-weight: 600; font-size: 14px; }
nav.top a:hover { color: var(--ink); }
nav.top a.on { color: var(--ink); border-bottom: 3px solid var(--accent); }
main { max-width: 1160px; margin: 0 auto; padding: 0 1rem 4rem; }
h1 { font-size: 1.6rem; margin: 1.4rem 0 0.2rem; }
h2 { font-size: 1.2rem; margin: 2.2rem 0 0.4rem; padding-top: 0.8rem; border-top: 2px solid var(--line); }
h3 { font-size: 1rem; margin: 1.4rem 0 0.4rem; }
p, li { max-width: 80ch; }
a { color: var(--accent); }
a.term { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--muted); }
a.term:hover { color: var(--accent); }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
.lead { color: var(--muted); margin: 0 0 1rem; max-width: 80ch; }
.small { color: var(--muted); font-size: 12.5px; }
.decision { border: 2px solid var(--muted); border-radius: 8px; padding: 0.9rem 1.1rem; margin: 1.1rem 0; background: var(--panel); }
.decision b { font-size: 1.15rem; display: block; margin-bottom: 0.25rem; }
.banner { padding: 0.6rem 0.9rem; border-radius: 6px; margin: 0.6rem 0; border-left: 4px solid var(--grey); background: var(--panel); }
.banner.warn { background: var(--amber-bg); border-color: var(--amber); color: var(--amber); }
.banner.fail { background: var(--red-bg); border-color: var(--red); color: var(--red); }
.banner.ok { background: var(--green-bg); border-color: var(--green); color: var(--green); }
.banner.paper { background: var(--violet-bg); border-color: var(--violet); color: var(--violet); }
.src-manual { display: inline-block; padding: 0.05rem 0.5rem; border-radius: 4px; font-size: 12px; font-weight: 700; background: var(--accent-bg); color: var(--accent); border: 1px solid var(--accent); }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; font-size: 13.5px; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--panel-2); font-weight: 600; position: sticky; top: 42px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.dim td { color: var(--muted); }
.pill { display: inline-block; padding: 0.08rem 0.55rem; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.pill.green { background: var(--green-bg); color: var(--green); }
.pill.amber { background: var(--amber-bg); color: var(--amber); }
.pill.red { background: var(--red-bg); color: var(--red); }
.pill.not_checked, .pill.grey { background: var(--grey-bg); color: var(--grey); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 0.8rem; margin: 0.8rem 0; }
.tile { border: 1px solid var(--line); border-radius: 8px; padding: 0.8rem 1rem; background: var(--panel); }
.tile .big { font-size: 1.6rem; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.tile .what { color: var(--muted); font-size: 12.5px; }
.pos { color: var(--green); } .neg { color: var(--red); }
.v-no { color: var(--red); font-weight: 600; } .v-cand { color: var(--green); font-weight: 600; }
details { margin: 0.5rem 0; } summary { cursor: pointer; font-weight: 600; padding: 0.3rem 0; }
.funnel td.bar-cell { width: 38%; } .bar { display: block; height: 0.8rem; background: var(--accent); border-radius: 2px; min-width: 1px; }
.bar.dead { background: var(--red); } .bar.pass { background: var(--green); }
dl.gl dt { font-weight: 700; margin-top: 1rem; } dl.gl dd { margin: 0.15rem 0 0 0; max-width: 80ch; }
dl.tech dt { font-family: ui-monospace, Menlo, monospace; font-weight: 600; margin-top: 0.9rem; }
dl.tech dd { margin: 0.15rem 0 0 0; max-width: 80ch; white-space: pre-wrap; }
.empty { color: var(--muted); font-style: italic; }
ul.timeline { list-style: none; padding: 0; } ul.timeline li { padding: 0.5rem 0 0.5rem 1rem; border-left: 3px solid var(--line); }
ul.timeline .d { font-weight: 700; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line); color: var(--muted); font-size: 12.5px; }
"""

NAV: tuple[tuple[str, str], ...] = (
    ("index.html", "Overview"),
    ("trades.html", "Trades"),
    ("strategies.html", "Strategies"),
    ("research.html", "Research progress"),
    ("glossary.html", "Glossary"),
)

_ACCOUNT_RE = re.compile(r"(?<!\w)(?:0\d{5,6}[A-Z]?|\d{6}[A-Z])(?!\w)")
ACCOUNT_PLACEHOLDER = "[account]"


def scrub(text: str) -> str:
    """Remove anything shaped like a trading-account number. Applied to every page."""
    return _ACCOUNT_RE.sub(ACCOUNT_PLACEHOLDER, text)


def glossary_anchor_id(key: str) -> str:
    """Anchor for a technical (code-derived) glossary entry."""
    return "gl-" + key.replace(".", "-")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(idr: float | int | None, signed: bool = False) -> str:
    """Rupiah with thousands separators: 'Rp 1,234,000'. None renders as an en dash."""
    if idr is None:
        return "–"
    v = round(idr)
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}Rp {abs(v):,}"


def money_exact(idr: float | None, signed: bool = False) -> str:
    """Rupiah to the cent, for figures compared line by line with the broker's: 'Rp 110.56'."""
    if idr is None:
        return "–"
    v = round(idr, 2)
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}Rp {abs(v):,.2f}"


def fee_money(idr: float | None) -> str:
    """A fee: whole rupiah when it is whole (Rp 4), to the cent when it is not (Rp 4.20). The broker charges 4.05, 4.2, 6.5."""
    if idr is None:
        return "–"
    return money_exact(idr) if abs(idr - round(idr)) > 0.005 else money(idr)


def pct(value: float | None, decimals: int = 2, signed: bool = True) -> str:
    if value is None:
        return "–"
    return f"{value:+.{decimals}f}%" if signed else f"{value:.{decimals}f}%"


def signed_class(value: float | None) -> str:
    if value is None or value == 0:
        return ""
    return "pos" if value > 0 else "neg"


def colored(text: str, value: float | None) -> str:
    cls = signed_class(value)
    return f'<span class="{cls}">{text}</span>' if cls else text


@dataclass(frozen=True)
class Column:
    header: str
    numeric: bool = False
    term: str | None = None
    """Glossary term key (dashboard/terms.py); the header links to it."""


def header_cell(col: Column) -> str:
    cls = ' class="num"' if col.numeric else ""
    if col.term is None:
        return f"<th{cls}>{esc(col.header)}</th>"
    require_term(col.term)
    return f'<th{cls}><a class="term" href="glossary.html#{term_anchor(col.term)}">{esc(col.header)}</a></th>'


def table(columns: Sequence[Column], rows: Iterable[Sequence[str]], empty: str = "None.", css: str = "") -> str:
    """Cells are trusted HTML (callers escape their own text)."""
    body = list(rows)
    if not body:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(header_cell(c) for c in columns)
    out = [f'<table class="{css}"><thead><tr>{head}</tr></thead><tbody>']
    for row in body:
        cells = "".join(
            f'<td class="num">{cell}</td>' if col.numeric else f"<td>{cell}</td>"
            for col, cell in zip(columns, row)
        )
        out.append(f"<tr>{cells}</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def pill(state: str, label: str) -> str:
    return f'<span class="pill {esc(state)}">{esc(label)}</span>'


def banner(kind: str, text: str) -> str:
    """*text* is trusted HTML."""
    return f'<div class="banner {esc(kind)}">{text}</div>'


def tile(big: str, label: str, what: str) -> str:
    return f'<div class="tile"><div class="what">{label}</div><div class="big">{big}</div><div class="what">{what}</div></div>'


def nav(active: str) -> str:
    links = "".join(
        f'<a href="{href}"{" class=on" if href == active else ""}>{esc(label)}</a>' for href, label in NAV
    )
    return f'<nav class="top"><div>{links}</div></nav>'


def page(title: str, active: str, body: str, footer: str) -> str:
    """A complete, self-contained HTML document with the shared top nav, scrubbed of account ids."""
    doc = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{esc(title)} — trading dashboard</title>",
            f"<style>{CSS}</style>",
            "</head><body>",
            nav(active),
            f"<main>{body}",
            f"<footer>{footer}</footer></main>",
            hover.script_tag() if "data-hv=" in body else "",
            "</body></html>",
            "",
        ]
    )
    return scrub(doc)
