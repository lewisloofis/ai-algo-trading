"""Overview page: the decision, the health table, and three headline numbers."""

from __future__ import annotations

from dashboard import health as H
from dashboard import results as R
from dashboard.render import Column, banner, colored, esc, money, page, pill, table, tile
from dashboard.terms import term_link
from dashboard.trades_data import SHARES_PER_LOT

_STATE_LABEL = {H.GREEN: "OK", H.AMBER: "Watch", H.RED: "Problem", H.NOT_CHECKED: "Not checked"}

HEALTH_COLS = (
    Column("Check"),
    Column("Status"),
    Column("What it means"),
)


def decision_block(d) -> str:
    dec = d.decision
    if dec is None:
        return banner(
            "warn",
            "<b>Current decision not recorded.</b> Add a <code>_current_decision</code> entry "
            "(headline, reason, as_of, source) to <code>research/dashboard/annotations.json</code>.",
        )
    return (
        f'<div class="decision"><b>{esc(dec.headline)}</b>'
        f"<span>Reason: {esc(dec.reason)}</span>"
        f'<div class="small">As of {esc(dec.as_of)} · source: {esc(dec.source)} (written by hand, not measured)</div></div>'
    )


def health_table(rows) -> str:
    body = []
    for r in rows:
        extra = ""
        if r.run_hint and r.state != H.NOT_CHECKED:
            extra = f'<div class="small">To refresh: <code>{esc(r.run_hint)}</code></div>'
        if r.state == H.NOT_CHECKED and r.run_hint and r.summary.endswith(r.run_hint):
            summary = esc(r.summary[: -len(r.run_hint)]) + f"<code>{esc(r.run_hint)}</code>"
        else:
            summary = esc(r.summary)
        body.append([f"<b>{esc(r.name)}</b>", pill(r.state, _STATE_LABEL[r.state]), summary + extra])
    return table(HEALTH_COLS, body)


def _positions_text(d) -> tuple[str, str]:
    pos = d.summary.positions
    if not pos:
        return "None", "Nothing is currently held with real money."
    big = ", ".join(f"{p.lots} lot{'s' if p.lots != 1 else ''} {esc(p.symbol)}" for p in pos)
    cost = sum(p.cost_idr for p in pos)
    shares = sum(p.lots for p in pos) * SHARES_PER_LOT
    return big, f"{shares:,} shares in total, bought for {money(cost)} (cost, not today's value)."


def _counts(d) -> tuple[int, int, int, int]:
    cells = d.run.cells if d.run else ()
    scored = [c for c in cells if c.gate in (
        R.GATE_PASSED_ALL, R.GATE_COST, R.GATE_FDR, R.GATE_NULL, R.GATE_OOS, R.GATE_IMPACT_UNPRICED)]
    passed = [c for c in cells if c.gate == R.GATE_PASSED_ALL]
    standing = [r for r in d.rows if r.cell.gate == R.GATE_PASSED_ALL and r.verdict.headline == "Candidate"]
    return len(cells), len(scored), len(passed), len(standing)


def render(d) -> str:
    s = d.summary
    pnl_text = colored(money(s.realised_pnl_idr, signed=True), s.realised_pnl_idr)
    pnl_what = (
        "No position has been sold yet, so nothing is realised."
        if s.round_trips_closed == 0
        else f"Profit from {s.round_trips_closed} closed round trip(s)."
    ) + f" Fees paid so far: {money(s.fees_idr)}. Real-money orders only."
    pos_big, pos_what = _positions_text(d)
    total, scored, passed, standing = _counts(d)

    if d.run:
        tested_big = f"{scored:,}"
        tested_what = f"of {total:,} stock/strategy/holding cases were actually scored; the rest lacked enough trades or data."
        pass_big = f"{passed}"
        pass_what = (
            f"passed every backtest check. After the extra “best 5 trades” check, {standing} still stand "
            "(none has been forward-tested)."
        )
    else:
        tested_big = tested_what = pass_big = pass_what = "–"

    tiles = (
        tile(pnl_text, f"Account profit to date ({term_link('realised-pnl', 'realised')})", pnl_what)
        + tile(pos_big, term_link("open-position"), pos_what)
        + tile(tested_big, f"Strategies tested (by {term_link('backtest', 'backtest')})", tested_what)
        + tile(pass_big, "Strategies passing", pass_what)
    )

    body = (
        "<h1>Overview</h1>"
        '<p class="lead">What the trading bot is doing, whether the machinery is working, and how the strategy research stands. '
        "Everything is in rupiah and plain words; unfamiliar terms link to the Glossary.</p>"
        + decision_block(d)
        + "<h2>Is the machinery working?</h2>"
        + health_table(d.health)
        + "<h2>Headline numbers</h2>"
        + f'<div class="tiles">{tiles}</div>'
        + '<p class="small">The live account is a small sub-account (about Rp 14.9 million). Practice (paper) orders are never counted in '
        'these numbers. See <a href="trades.html">Trades</a> for every order and '
        '<a href="strategies.html">Strategies</a> for the backtest scorecard.</p>'
    )
    from dashboard.site import footer_text

    return page("Overview", "index.html", body, footer_text(d))
