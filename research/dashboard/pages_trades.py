"""Trades page: every order the bot placed, newest first. Live, practice and refused are separate."""

from __future__ import annotations

from datetime import date

from dashboard import svg
from dashboard.render import Column, banner, colored, esc, fee_money, money, money_exact, page, table
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
        fee_money(x.fees_idr) if x.filled else "–",
        colored(pnl, x.realised_pnl_idr),
        _label(x),
    ]


UNKNOWN_TIME = "time not recorded"

RECON_COLS = (
    Column("Sale"),
    Column("Our realised result", numeric=True, term="realised-pnl"),
    Column("Broker's figure", numeric=True),
    Column("Difference (broker minus ours)", numeric=True),
    Column("Why they differ"),
)


def _manual_when(t) -> str:
    return f"{t.date} {t.time_wib[:5]}" if t.time_wib else f"{t.date} · {UNKNOWN_TIME}"


def _manual_row(t, closing) -> list[str]:
    pnl = "–"
    if closing is not None:
        pnl = colored(money(closing.pnl_idr, signed=True), closing.pnl_idr)
        if t.broker_realised_pnl_idr is not None:
            pnl += f'<div class="small">broker app: {money_exact(t.broker_realised_pnl_idr, signed=True)}</div>'
    note = f'<div class="small">{esc(t.note)}</div>' if t.note else ""
    link = f'<div class="small">closes bot order {esc(t.closes_order_id)}</div>' if t.closes_order_id else ""
    return [
        _manual_when(t),
        f"<b>{esc(t.symbol)}</b>",
        t.side.upper(),
        f"{t.lots:,}",
        f"{money(t.price_idr)} (executed)",
        f"Yes: {t.lots} lot{'s' if t.lots != 1 else ''} at {money(t.price_idr)}",
        fee_money(t.fee_idr),
        pnl,
        f'<span class="src-manual">manual (app)</span>{note}{link}',
    ]


def _reconciliation(d) -> str:
    if not d.manual:
        return ""
    rows = [
        [
            f"{esc(r.symbol)} sell, {r.day}"
            + (f'<div class="small">closes {esc(r.closes_order_id)}</div>' if r.closes_order_id else ""),
            money_exact(r.ours_idr, signed=True),
            money_exact(r.broker_idr, signed=True),
            money_exact(r.difference_idr, signed=True),
            esc(r.reason),
        ]
        for r in d.ledger.reconciliations
    ]
    return (
        "<h2>Manual trades: reconciliation notes</h2>"
        '<p class="small">Our figure uses each trade\'s own price and fees, net of both legs\' fees, matched '
        "first-in-first-out. Where the broker app gave its own realised figure it is shown beside ours "
        f"({term_link('reconciliation-note')}). Neither side is edited to make them agree.</p>"
        + table(RECON_COLS, rows, empty="No manual trade carries a broker figure to compare.")
    )


def _reject_row(x: OrderLine) -> list[str]:
    tried = f"{x.side.upper()} {x.lots:,} lot{'s' if x.lots != 1 else ''} at limit {money(x.limit_price)} ({money(x.limit_price * x.lots * 100)})"
    mode = "Real money" if x.sink_kind == "live" else "Practice (paper)"
    return [_when(x), mode, f"<b>{esc(x.symbol)}</b>", esc(tried), esc(x.reject_reason), _label(x)]


def _charts(d) -> str:
    out = []
    for sym in sorted({x.symbol for x in d.live} | {t.symbol for t in d.manual}):
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
        ] + [
            svg.Mark(t.date, t.price_idr, t.side, True,
                     f"MANUAL (app) {t.side} {t.lots} lot(s) filled at {t.price_idr:,.0f} - {_manual_when(t)}",
                     manual=True)
            for t in d.manual
            if t.symbol == sym
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
        "A solid marker was filled; a hollow one was sent but not filled. A diamond (◆) is a "
        f"{term_link('manual-trade', 'manual trade')} placed by hand in the broker app (green = buy, red = sell). "
        "Hover a marker for details. Only real-money orders are marked.</p>"
    )
    return legend + "".join(out)


def render(d) -> str:
    live_ok = [x for x in d.live if x.status != "rejected"]
    closing_of = {id(c.sell.trade): c for c in d.ledger.closings if c.sell.trade is not None}
    keyed = [((x.when.date().isoformat(), x.when.strftime("%H:%M:%S")), _row(x)) for x in live_ok]
    keyed += [((t.date, t.time_wib or "23:59:59"), _manual_row(t, closing_of.get(id(t)))) for t in d.manual]
    live_rows = [row for _k, row in sorted(keyed, key=lambda kv: kv[0], reverse=True)]
    refused = [x for x in d.orders if x.status == "rejected"]
    s = d.summary
    led = d.ledger
    by_hand = (f" Plus <b>{len(d.manual)}</b> {term_link('manual-trade', 'manual trade')}(s) placed by hand in the "
               "broker app." if d.manual else "")
    intro = (
        f"<p class=\"lead\">Every order the bot has sent, newest first: <b>{s.orders_sent}</b> real-money order(s), "
        f"<b>{s.orders_filled}</b> filled.{by_hand} So far these are 1-lot tests of the order path, not a trading strategy. "
        f"Terms such as {term_link('lot')} and {term_link('filled')} link to the Glossary.</p>"
    )
    totals = banner(
        "ok",
        f"Real money only: realised profit {money(led.realised_pnl_idr, signed=True)} · fees {fee_money(led.fees_idr)} · "
        f"{led.round_trips_closed} sale(s) closed (bot and manual, matched first-in-first-out). Practice orders below are not included.",
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
        + table(COLS, live_rows, empty="No real-money orders yet.")
        + _reconciliation(d)
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
