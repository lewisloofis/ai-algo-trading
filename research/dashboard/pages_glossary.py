"""Glossary page: plain-language terms first, then the code-derived technical definitions."""

from __future__ import annotations

from dashboard.glossary import build_glossary
from dashboard.render import esc, glossary_anchor_id, page
from dashboard.terms import TERMS, term_anchor


def render(d) -> str:
    plain = "".join(
        f'<dt id="{term_anchor(t.key)}">{esc(t.name)}</dt><dd>{esc(t.plain)}</dd>' for t in sorted(TERMS, key=lambda t: t.name.lower())
    )
    g = build_glossary()
    tech = "".join(
        f'<dt id="{glossary_anchor_id(e.key)}">{esc(e.term)}</dt>'
        f'<dd class="small">{esc(e.source)}</dd><dd>{esc(e.definition)}</dd>'
        for e in sorted(g.values(), key=lambda e: (e.source, e.term))
    )
    body = (
        "<h1>Glossary</h1>"
        '<p class="lead">Plain-language meanings of the words used on this site. Other pages link here; nothing is '
        "explained twice.</p>"
        f'<dl class="gl">{plain}</dl>'
        "<h2>Technical definitions, verbatim from the code</h2>"
        '<p class="small">For anyone checking the backtest harness or the order store. Each entry is the doc comment of the code '
        "that owns the field, so it cannot drift from the implementation.</p>"
        f'<dl class="tech">{tech}</dl>'
    )
    from dashboard.site import footer_text

    return page("Glossary", "glossary.html", body, footer_text(d))
