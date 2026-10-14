"""Research progress page: where candidate strategies die, and the dated story so far."""

from __future__ import annotations

from collections import Counter

from dashboard import results as R
from dashboard.render import Column, banner, esc, page, table
from dashboard.terms import term_link
from lib.stats import MIN_EFF_N

from dashboard.verdicts import TOP_N

# (gate outcome, plain stage name, plain explanation). Order = the order cases are filtered.
FUNNEL: tuple[tuple[str, str, str, str], ...] = (
    (R.GATE_BLOCKED, "Could not be tested yet", "The history or capital the idea needs does not exist yet.", "dead"),
    (R.GATE_DEFERRED, "Postponed", "Deliberately left for later (needs order-book data).", "dead"),
    (R.GATE_LONG_SHORT, "Needs short-selling", "A retail IDX account cannot short, so the idea cannot be run as designed.", "dead"),
    (R.GATE_INSUFFICIENT_N, "Too few trades to judge", f"After removing overlapping trades there were fewer than {MIN_EFF_N} independent ones.", "dead"),
    (R.GATE_DECAYED, "Edge faded over time", "It worked early in the history and stopped working.", "dead"),
    (R.GATE_COST, "Loses after costs", "The average gain per trade does not cover fees, spread and market impact.", "dead"),
    (R.GATE_FDR, "Could easily be luck", "Profitable after costs, but not convincing once the many strategies tried are accounted for.", "dead"),
    (R.GATE_NULL, "Just riding the stock's own rise", "Buying that stock on any day did about as well as following the signal.", "dead"),
    (R.GATE_OOS, "Not confirmed on later data", "No usable result on the later, unseen part of the history.", "dead"),
    (R.GATE_IMPACT_UNPRICED, "Cost of trading not modelled", "Event-based ideas pooled across stocks; their market impact has not been priced yet.", "dead"),
    (R.GATE_PASSED_ALL, "Passed every backtest check", "Cleared every check the backtest applies.", "pass"),
)

COLS = (
    Column("Where it stopped", term="gate"),
    Column("Cases", numeric=True),
    Column("Share", numeric=True),
    Column(""),
    Column("In plain words"),
)


def funnel_counts(cells) -> Counter:
    return Counter(c.gate for c in cells)


def _funnel(d) -> str:
    counts = funnel_counts(d.run.cells)
    total = sum(counts.values())
    known = {g for g, *_ in FUNNEL}
    stray = {g: n for g, n in counts.items() if g not in known}
    rows = []
    biggest = max(counts.values(), default=1)
    for gate, name, why, kind in FUNNEL:
        n = counts.get(gate, 0)
        share = n / total if total else 0
        bar = f'<span class="bar {kind}" style="width:{max(n / biggest * 100, 0.8):.1f}%"></span>' if n else ""
        rows.append([f"<b>{esc(name)}</b>", f"{n:,}", f"{share * 100:.1f}%", bar, esc(why)])
    out = table(COLS, rows, css="funnel")
    if stray:
        out += banner("warn", "Some cases reached a state this page has no wording for: "
                      + ", ".join(f"{esc(g)} ({n})" for g, n in sorted(stray.items())))
    return out


def _after_extra_check(d) -> str:
    passed = [r for r in d.rows if r.cell.gate == R.GATE_PASSED_ALL]
    standing = [r for r in passed if r.verdict.headline == "Candidate"]
    dropped = [r for r in passed if r.verdict.checks[-1].passed is False]
    return (
        f"<p>Of the <b>{len(passed)}</b> cases that passed every backtest check, <b>{len(dropped)}</b> turn out to depend on "
        f"their {TOP_N} best trades (the average falls to zero or below without them), and <b>{len(standing)}</b> remain "
        "candidates. A candidate is a reason to run a "
        f"{term_link('forward-test')}, not to trade. See the <a href=\"strategies.html\">Strategies</a> page for each one.</p>"
    )


def _timeline(d) -> str:
    if not d.timeline:
        return '<p class="empty">No timeline entries in annotations.json.</p>'
    items = "".join(
        f'<li><span class="d">{esc(e.date)}</span> — {esc(e.note)}<div class="small">source: {esc(e.source)}</div></li>'
        for e in d.timeline
    )
    return f'<ul class="timeline">{items}</ul>'


def render(d) -> str:
    if d.run is None:
        funnel = banner("warn", "No backtest results found; the funnel cannot be drawn.")
        extra = ""
        gen = ""
    else:
        funnel = _funnel(d)
        extra = _after_extra_check(d)
        gen = (
            f'<p class="small">Run generated {esc(d.run.generated)} with the corrected next-session entry. '
            f"{sum(1 for _ in d.run.cells):,} cases in total.</p>"
        )
    body = (
        "<h1>Research progress</h1>"
        '<p class="lead">Many strategy ideas were tried on many stocks. This page shows where they were filtered out, '
        "and the dated story of what changed our mind.</p>"
        "<h2>Where candidates die</h2>"
        + gen
        + funnel
        + extra
        + "<h2>What happened, in order</h2>"
        + '<p class="small">Hand-written notes from the operator (annotations.json), not measurements.</p>'
        + _timeline(d)
    )
    from dashboard.site import footer_text

    return page("Research progress", "research.html", body, footer_text(d))
