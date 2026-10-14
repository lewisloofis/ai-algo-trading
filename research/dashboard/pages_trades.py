"""Trades page: every order the bot placed, newest first. Live, practice and refused are separate."""

from __future__ import annotations

from datetime import date

from dashboard import svg
from dashboard.render import Column, banner, colored, esc, money, page, table
from dashboard.terms import term_link
from dashboard.trades_data import OrderLine

COLS = (
    Column("Date / time (WIB)"),
    Column("Stock"),
    Column("Buy / sell"),
    Column("Lots", numeric=True, term="lot"),
    Column("Limit price", numeric=True, term="limit-price"),
    Column("Filled?", term="filled"),
    Column("Fees", numeric=True, term="fees"),
    Column("Profit when closed", numeric=True, term="realised-pnl"),
    Column("Strategy / run"),
)

REJECT_COLS = (
    Column("Date / time (WIB)"),
    Column("Real money or practice"),
    Column("Stock"),
    Column("What was tried"),
    Column("Why it did not go through"),
    Column("Strategy / run"),
)


def _when(x: OrderLine) -> str:
    return x.when.strftime("%Y-%m-%d %H:%M")


def _label(x: OrderLine) -> str:
    return (
        f'<span title="{esc(x.strategy_note)}">{esc(x.strategy_label)}</span>'
        f'<div class="small">{esc(x.strategy_id)} · run {esc(x.run_id)}</div>'
    )


def _filled(x: OrderLine) -> str:
    if x.filled_lots == 0:
        return "No: sent but never filled"
    word = "Yes" if x.status == "filled" else "Partly"
    return f"{word}: {x.filled_lots} lot{'s' if x.filled_lots != 1 else ''} at {money(x.fill_price)}"


def _row(x: OrderLine) -> list[str]:
    pnl = money(x.realised_pnl_idr, signed=True) if x.realised_pnl_idr is not None else "–"
    return [
        _when(x),
        f"<b>{esc(x.symbol)}</b>",
        x.side.upper(),
        f"{x.lots:,}",
        money(x.limit_price),
        _filled(x),
        money(x.fees_idr) if x.filled else "–",
        colored(pnl, x.realised_pnl_idr),
        _label(x),
    ]


def _reject_row(x: OrderLine) -> list[str]:
    tried = f"{x.side.upper()} {x.lots:,} lot{'s' if x.lots != 1 else ''} at limit {money(x.limit_price)} ({money(x.limit_price * x.lots * 100)})"
    mode = "Real money" if x.sink_kind == "live" else "Practice (paper)"
    return [_when(x), mode, f"<b>{esc(x.symbol)}</b>", esc(tried), esc(x.reject_reason), _label(x)]


def _charts(d) -> str:
    out = []
    for sym in sorted({x.symbol for x in d.live}):
        marks = [
            svg.Mark(
                x.when.date().isoformat(),
                x.fill_price if x.filled else float(x.limit_price),
                x.side,
                x.filled,
                f"{x.side} {x.lots} lot(s) {'filled at' if x.filled else 'NOT filled, limit'} "
                f"{(x.fill_price if x.filled else x.limit_price):,.0f} — {x.when:%Y-%m-%d %H:%M} WIB",
            )
            for x in d.live
            if x.symbol == sym
        ]
        closes = d.closes.get(sym, [])
        chart = svg.price_chart(f"{sym}: daily closes and the bot's orders", closes, marks)
        note = ""
        if closes and marks:
            gap = (date.fromisoformat(max(m.day for m in marks)) - date.fromisoformat(closes[-1][0])).days
            if gap > 4:
                note = (
                    f'<p class="small">Price history on file stops at {esc(closes[-1][0])}; '
                    "orders after that date are placed on the chart at their own price.</p>"
                )
        out.append(f"<h3>{esc(sym)}</h3>{chart}{note}")
    if not out:
        return '<p class="empty">No live orders to chart.</p>'
    legend = (
        '<p class="small">Grey line: daily closing price. Green ▲ = buy, red ▼ = sell. '
        "A solid marker was filled; a hollow one was sent but not filled. Hover a marker for details. "
        "Only real-money orders are marked.</p>"
    )
    return legend + "".join(out)


def render(d) -> str:
    live_ok = [x for x in d.live if x.status != "rejected"]
    refused = [x for x in d.orders if x.status == "rejected"]
    s = d.summary
    intro = (
        f"<p class=\"lead\">Every order the bot has sent, newest first: <b>{s.orders_sent}</b> real-money order(s), "
        f"<b>{s.orders_filled}</b> filled. So far these are 1-lot tests of the order path, not a trading strategy. "
        f"Terms such as {term_link('lot')} and {term_link('filled')} link to the Glossary.</p>"
    )
    totals = banner(
        "ok",
        f"Real money only: realised profit {money(s.realised_pnl_idr, signed=True)} · fees {money(s.fees_idr)} · "
        f"{s.round_trips_closed} round trip(s) closed. Practice orders below are not included.",
    )
    paper_note = banner(
        "paper",
        f"<b>Practice (paper) orders.</b> The bot decided and recorded these but sent nothing to the broker. "
        f"They are shown for completeness and are never added to the real-money figures. ({term_link('paper')})",
    )
    body = (
        "<h1>Trades</h1>"
        + intro
        + totals
        + "<h2>Real-money orders</h2>"
        + table(COLS, [_row(x) for x in live_ok], empty="No real-money orders yet.")
        + "<h2>Refused or failed orders</h2>"
        + '<p class="small">Orders the broker or the bot\'s safety limits turned down, in plain words.</p>'
        + table(REJECT_COLS, [_reject_row(x) for x in refused if x.sink_kind == "live"] +
                [_reject_row(x) for x in refused if x.sink_kind != "live"],
                empty="No refused orders recorded.")
        + "<h2>Practice (paper) orders</h2>"
        + paper_note
        + table(COLS, [_row(x) for x in d.paper if x.status != "rejected"], empty="No practice orders.")
        + "<h2>Price charts with the bot's orders</h2>"
        + _charts(d)
    )
    from dashboard.site import footer_text

    return page("Trades", "trades.html", body, footer_text(d))
