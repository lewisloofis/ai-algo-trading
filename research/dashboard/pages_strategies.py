"""Strategies page (backtest scorecard) and one detail page per highlighted cell."""

from __future__ import annotations

from dashboard import svg
from dashboard.render import Column, banner, colored, esc, money, page, pct, table, tile
from dashboard.scorecard import STAKE_IDR, ScoreRow
from dashboard.terms import term_link
from dashboard.verdicts import TOP_N

FAMILY_WORDS = {
    "daily_momentum": "Momentum",
    "mean_reversion": "Mean reversion",
    "volume_breakout": "Volume breakout",
}

COLS = (
    Column("Stock"),
    Column("Strategy type", term="strategy-type"),
    Column("Hold (days)", numeric=True, term="holding-days"),
    Column("Verdict"),
    Column("Trades", numeric=True),
    Column("Avg profit per trade, after costs", numeric=True, term="after-costs"),
    Column("Just holding the stock", numeric=True, term="buy-and-hold"),
    Column("Win rate", numeric=True, term="win-rate"),
    Column(f"Avg without best {TOP_N} trades", numeric=True, term="best-five"),
    Column("Detail"),
)


def family_word(family: str) -> str:
    return FAMILY_WORDS.get(family, family.replace("_", " ").capitalize())


def hold_text(r: ScoreRow) -> str:
    return esc(r.cell.hold_label) if r.cell.hold_label else f"{r.cell.hold}"


def _money_pct(pct_value: float | None, idr: float | None) -> str:
    if pct_value is None:
        return "–"
    return colored(f"{pct(pct_value)}<br><span class=\"small\">{money(idr, signed=True)} per Rp 1M</span>", pct_value)


def _holding_cell(st) -> str:
    if st.hold_total_pct is None:
        return "–"
    like = ""
    if st.any_day_avg_pct is not None:
        like = (f'<br><span class="small">Buying any day for the same hold: {pct(st.any_day_avg_pct)} per trade '
                f"({money(st.any_day_avg_idr, signed=True)} per Rp 1M)</span>")
    return colored(f'{pct(st.hold_total_pct, 0)} over the same dates{like}', st.hold_total_pct)


def _verdict_cell(r: ScoreRow) -> str:
    cls = "v-cand" if r.verdict.headline == "Candidate" else "v-no"
    star = ' <span class="small">(previously famous)</span>' if r.famous else ""
    flag = ""
    if r.reproduces is False:
        flag = '<div class="small">⚠ re-derived trades differ from the report (price data changed since the run)</div>'
    return f'<span class="{cls}">{esc(r.verdict.sentence)}</span>{star}{flag}'


def _row(r: ScoreRow) -> list[str]:
    st = r.stats
    link = f'<a href="{r.detail_page}">Chart &amp; trades</a>' if r.detail_page else "–"
    if st is None:
        return [f"<b>{esc(r.cell.symbol)}</b>", esc(family_word(r.cell.family)), hold_text(r),
                _verdict_cell(r), "–", "–", "–", "–", "–", link]
    return [
        f"<b>{esc(r.cell.symbol)}</b>",
        esc(family_word(r.cell.family)),
        hold_text(r),
        _verdict_cell(r),
        f"{st.n_trades}",
        _money_pct(st.net_avg_pct, st.net_avg_idr),
        _holding_cell(st),
        f"{st.win_rate * 100:.0f}%",
        _money_pct(st.net_without_best_pct, st.net_without_best_idr),
        link,
    ]


def _sort_key(r: ScoreRow):
    st = r.stats
    return (0 if r.verdict.headline == "Candidate" else 1, -(st.net_avg_pct if st else -1e9))


def render_list(d) -> str:
    if d.run is None:
        body = (
            "<h1>Strategies</h1>"
            + banner("warn", "No backtest results were found. Expected a corrected run in "
                     "<code>research/out_d1/next_close/strategy_portfolio.md</code> (see config.CORRECTED_RUN_DIRS).")
        )
        from dashboard.site import footer_text

        return page("Strategies", "strategies.html", body, footer_text(d))

    famous = [r for r in d.rows if r.famous]
    highlighted = sorted([r for r in d.rows if r.highlighted and not r.famous], key=_sort_key)
    others = sorted([r for r in d.rows if not r.highlighted], key=lambda r: (r.cell.symbol, r.cell.family, r.cell.hold))
    mismatched = sum(1 for r in d.rows if r.reproduces is False)
    last_dates = sorted({d.daily_last.get(r.cell.symbol, "") for r in highlighted if r.cell.symbol in d.daily_last})
    ends = f"{last_dates[0]} to {last_dates[-1]}" if last_dates else "unknown dates"

    intro = (
        '<p class="lead">A <b>backtest</b> replays each strategy over past prices. Each row is one case: a stock, a '
        "strategy type and a holding period. The verdict says in plain words whether it could be traded, and why not. "
        "Nothing here has been proven in live trading.</p>"
    )
    provenance = banner(
        "ok",
        f"<b>Which results:</b> strategy-fit run generated {esc(d.run.generated)}, capital model "
        f"<code>{esc(d.run.capital_model.split('—')[0].strip(' `'))}</code>. "
        f"All results use the <b>corrected {term_link('next-session-entry')}</b> (fixed 2026-10-14); older result sets "
        "used a same-day price that cannot be traded and are not shown. Trades below were re-derived from local daily "
        f"prices by the backtest's own functions (history from 2024-01-01; price files end {esc(ends)}).",
    )
    warn = (
        banner("warn", f"{mismatched} case(s) do not exactly reproduce the report from today's price files; they are flagged.")
        if mismatched
        else ""
    )
    explain = (
        f'<p class="small">How to read the columns: <b>after costs</b> includes fees, spread and the price '
        f"movement your own order causes. <b>Just holding</b> shows what buying at the first entry and selling at the last exit would have made, and under it "
        f"what buying that stock on any day for the same hold averaged per trade (the fair comparison with the average above). <b>Without best {TOP_N}</b> drops the {TOP_N} most profitable trades: if the average then turns "
        f"negative, the strategy lived on a few big wins. Money is shown per Rp 1,000,000 put into each trade "
        f"(a reading aid, {term_link('rupiah-per-1m', 'see Glossary')}).</p>"
    )
    n_cand = sum(1 for r in highlighted + famous if r.verdict.headline == "Candidate")
    tiles = (
        tile(f"{len(highlighted) + len(famous)}", "Cases shown below", "every case that passed at least the cost check, plus 3 previously famous ones")
        + tile(f"{n_cand}", "Candidates", "pass every check; still need a forward test")
        + tile(f"{len(d.rows):,}", "Cases in the full table", "every stock/strategy/holding case in the run")
    )
    full_rows = [_row(r) for r in others]
    body = (
        "<h1>Strategies: backtest scorecard</h1>"
        + intro
        + provenance
        + warn
        + f'<div class="tiles">{tiles}</div>'
        + explain
        + "<h2>The three cases you may have heard about</h2>"
        + '<p class="small">Before the entry-timing fix these looked like winners.</p>'
        + table(COLS, [_row(r) for r in famous], empty="Not in this run.")
        + "<h2>Every other case that passed at least one check</h2>"
        + table(COLS, [_row(r) for r in highlighted], empty="No case passed any check.")
        + "<details><summary>Full table: every other case in the run "
        + f"({len(others):,} cases, most lost after costs or had too few trades)</summary>"
        + table(COLS, full_rows)
        + "</details>"
    )
    from dashboard.site import footer_text

    return page("Strategies", "strategies.html", body, footer_text(d))


# ---------------------------------------------------------------------------
# Detail pages
# ---------------------------------------------------------------------------

_CHECK_MARK = {True: ("✔", "pos", "passed"), False: ("✘", "neg", "failed"), None: ("–", "", "not judged")}


def _checks(r: ScoreRow) -> str:
    items = []
    for c in r.verdict.checks:
        mark, cls, word = _CHECK_MARK[c.passed]
        items.append(
            f'<li><b class="{cls}">{mark} {esc(c.name)}</b> — {word}. {esc(c.detail)}</li>'
        )
    return "<ul>" + "".join(items) + "</ul>"


def _curves(d, r: ScoreRow):
    ct = d.exported[r.cell.key]
    cost = r.stats.cost_pct / 100
    run, pts = 0.0, [(ct.trades[0].entry_date, 0.0)]
    rows = []
    for t in ct.trades:
        net = t.gross_return - cost
        run += net * STAKE_IDR
        pts.append((t.exit_date, run))
        rows.append((t, net, run))
    first, last = ct.trades[0].entry_date, ct.trades[-1].exit_date
    bars = [(str(x), float(c)) for x, c in zip(ct.bars["date"].astype(str), ct.bars["close"]) if first <= str(x) <= last]
    base = bars[0][1]
    hold = [(x, (c / base - 1.0) * STAKE_IDR) for x, c in bars]
    return pts, hold, rows


def render_detail(d, r: ScoreRow) -> str:
    st, c = r.stats, r.cell
    pts, hold, rows = _curves(d, r)
    chart = svg.line_chart(
        f"{c.symbol} {family_word(c.family)} {c.hold}-day hold: cumulative profit vs just holding",
        [
            svg.Series("Strategy, after costs", pts, svg.BLUE),
            svg.Series("Just holding the stock", hold, svg.MUTED, dashed=True),
        ],
    )
    cls = "v-cand" if r.verdict.headline == "Candidate" else "v-no"
    tiles = (
        tile(f"{st.n_trades}", "Trades in the backtest", f"{st.first_entry} to {st.last_exit}")
        + tile(colored(pct(st.net_avg_pct), st.net_avg_pct), f"Average per trade, {term_link('after-costs')}",
               f"{money(st.net_avg_idr, signed=True)} per Rp 1M; cost used {st.cost_pct:.2f}% per trade")
        + tile(f"{st.win_rate * 100:.0f}%", term_link("win-rate"), "of trades profitable after costs")
        + tile(colored(pct(st.hold_total_pct), st.hold_total_pct), term_link("buy-and-hold"),
               f"{money(st.hold_total_idr, signed=True)} per Rp 1M over the same span. Buying on any day for the same hold "
               f"averaged {pct(st.any_day_avg_pct)} per trade after the same costs.")
        + tile(colored(pct(st.net_without_best_pct), st.net_without_best_pct), term_link("best-five", f"Average without best {TOP_N}"),
               f"{money(st.net_without_best_idr, signed=True)} per Rp 1M")
    )
    trade_cols = (
        Column("Bought (next-session close)"),
        Column("Sold"),
        Column("Buy price", numeric=True),
        Column("Sell price", numeric=True),
        Column("Move", numeric=True),
        Column("After costs", numeric=True, term="after-costs"),
        Column("Per Rp 1M", numeric=True, term="rupiah-per-1m"),
        Column("Running total per Rp 1M", numeric=True),
    )
    trade_rows = [
        [t.entry_date, t.exit_date, f"{t.entry_price:,.0f}", f"{t.exit_price:,.0f}",
         colored(pct(t.gross_return * 100), t.gross_return), colored(pct(net * 100), net),
         colored(money(net * STAKE_IDR, signed=True), net), money(run, signed=True)]
        for t, net, run in rows
    ]
    repro = ""
    if r.reproduces is False:
        repro = banner("warn", "These trades do not exactly reproduce the figures in the strategy-fit report "
                       "(the price files changed after that run). Treat the numbers as approximate.")
    elif r.reproduces is True:
        repro = '<p class="small">✔ Re-derived trades reproduce the strategy-fit report\'s own average for this case.</p>'
    detail_bits = f"Report outcome: <code>{esc(c.reason[:160])}</code>" if c.reason else ""
    body = (
        '<p><a href="strategies.html">&larr; All strategies</a></p>'
        f"<h1>{esc(c.symbol)}: {esc(family_word(c.family))}, hold {c.hold} day{'s' if c.hold != 1 else ''}</h1>"
        f'<p class="{cls}" style="font-size:1.15rem">{esc(r.verdict.sentence)}</p>'
        + banner(
            "ok",
            f"Backtest from strategy-fit run {esc(d.run.generated)}, using the corrected "
            f"{term_link('next-session-entry')} (bought at the close of the day after the signal).",
        )
        + repro
        + f'<div class="tiles">{tiles}</div>'
        + "<h2>Profit over time</h2>"
        + f'<p class="small">Each trade stakes Rp {STAKE_IDR:,}, after costs, added up. The dashed line is Rp {STAKE_IDR:,} '
        "bought at the first entry and simply held.</p>"
        + chart
        + "<h2>What it was tested against</h2>"
        + _checks(r)
        + f'<p class="small">{detail_bits}</p>'
        + "<h2>Every trade in the backtest</h2>"
        + table(trade_cols, trade_rows)
    )
    from dashboard.site import footer_text

    return page(f"{c.symbol} {family_word(c.family)} {c.hold}d", "strategies.html", body, footer_text(d))


def render_details(d) -> dict[str, str]:
    return {r.detail_page: render_detail(d, r) for r in d.rows if r.detail_page}
