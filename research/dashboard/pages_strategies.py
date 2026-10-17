"""Strategies page (backtest scorecard) and one detail page per highlighted cell."""

from __future__ import annotations

from dashboard import strategy_sections as SEC
from dashboard import strategy_families as F
from dashboard import strategy_status as S
from dashboard import svg
from dashboard.render import Column, banner, colored, esc, money, page, pct, table, tile
from dashboard.scorecard import STAKE_IDR, ScoreRow, cumulative_series
from dashboard.terms import term_link
from dashboard.verdicts import TOP_N

FAMILY_WORDS = {
    "daily_momentum": "Momentum",
    "mean_reversion": "Mean reversion",
    "volume_breakout": "Volume breakout",
}

def family_word(family: str) -> str:
    return FAMILY_WORDS.get(family, family.replace("_", " ").capitalize())


def _detail_links(d) -> dict[tuple[str, str, int], str]:
    return {r.cell.key: r.detail_page for r in d.rows if r.detail_page}


def _case_pages(d) -> str:
    rows = sorted((r for r in d.rows if r.detail_page), key=lambda r: (r.cell.symbol, r.cell.family, r.cell.hold))
    if not rows:
        return ""
    items = "".join(
        f'<li><a href="{esc(r.detail_page)}">{esc(r.cell.symbol)} {esc(family_word(r.cell.family))}, hold '
        f"{r.cell.hold} day{'s' if r.cell.hold != 1 else ''}</a> — {esc(r.verdict.sentence)}</li>" for r in rows)
    return (f"<details><summary>Backtest chart pages ({len(rows)} cases with a chart and every trade)</summary>"
            f"<ul>{items}</ul></details>")


def render_list(d) -> str:
    from dashboard.site import footer_text

    inp = d.strategy_inputs or S.Inputs(None, "not loaded", None, (), None)
    run_cells = d.run.cells if d.run else None
    stages = None if inp.specs is None else S.stage_rows(inp.specs, d.paper_tests, d.online, inp.shortlist)
    combos = S.promising_combos(inp.shortlist, inp.specs)
    runs = None if d.run_history is None else S.recent_runs(d.run_history)
    provenance = (
        f'<p class="small">Backtest results: strategy-fit run {esc(d.run.generated)}, using the corrected '
        f'{term_link("next-session-entry")}.</p>' if d.run else
        banner("warn", "No backtest results were found. Expected a corrected run in "
               "<code>research/results/d1/next_close/strategy_portfolio.md</code> (see config.CORRECTED_RUN_DIRS). "
               "Family results below use the shortlist only."))
    body = (
        "<h1>Strategies</h1>"
        '<p class="lead">The current condition of our strategy work: what is running, what we are considering, '
        "which stocks are in the screen and which combinations look promising. Nothing here has been proven in live trading.</p>"
        + stand_wrap(stages, d)
        + SEC.families_section(F.family_rows(run_cells, inp.shortlist, inp.specs)) + provenance
        + SEC.screen_section(inp)
        + SEC.combos_section(inp.shortlist, inp.shortlist_problem, combos, _detail_links(d), d.now_wib.date())
        + SEC.runs_section(runs)
        + _case_pages(d)
    )
    return page("Strategies", "strategies.html", body, footer_text(d))


def stand_wrap(stages, d) -> str:
    return SEC.stand_section(stages, d.paper_tests)


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
    """Strategy and like-for-like baseline (same trade dates), and the per-trade rows."""
    ct = d.exported[r.cell.key]
    return cumulative_series(ct.trades, r.stats.cost_pct, ct.always_long_return)


RANDOM_DAYS = "Same trades on random days (fair comparison)"


def chart_caption(c, st) -> str:
    """One plain sentence stating both per-trade numbers, what 'beats' means, and why holding is not comparable."""
    if st.any_day_avg_pct is None:
        return ""
    t = f" (t = {st.t_stat:.1f})" if st.t_stat is not None else ""
    out = (
        f"Per trade, the strategy made {pct(st.net_avg_pct)} vs {pct(st.any_day_avg_pct)} for entering on random days "
        f"with the same hold, after the same costs. “Beats” means a higher average per trade than that; it only counts "
        f"as real if the gap is bigger than luck{t}."
    )
    if st.hold_total_pct is not None:
        mkt = f" (the strategy is in the market {st.in_market_pct:.0f}% of days)" if st.in_market_pct is not None else ""
        out += (
            f" Holding {c.symbol} the whole time made {pct(st.hold_total_pct, 0)} over the same dates, but that leaves "
            f"everything invested and compounding through the stock's whole move{mkt}, with more risk. "
            "It is not a fair comparison, so it is left off the chart."
        )
    return out


def render_detail(d, r: ScoreRow) -> str:
    st, c = r.stats, r.cell
    pts, base, rows = _curves(d, r)
    chart = svg.line_chart(
        f"{c.symbol} {family_word(c.family)} {c.hold}-day hold: cumulative profit vs the same trades on random days",
        [
            svg.Series("Strategy, after costs", pts, svg.BLUE),
            svg.Series(RANDOM_DAYS, base, svg.AMBER),
        ],
        diff=(0, 1),
        diff_label="Strategy minus random days",
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
        + f'<p class="small">Both lines stake Rp {STAKE_IDR:,} per trade, after the same costs, added up trade by trade. '
        "The orange line is the average result of entering the same number of trades, with the same hold, on random days "
        "instead of on the signal. Hover the chart for the date and both values.</p>"
        + chart
        + f'<p>{esc(chart_caption(c, st))}</p>'
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
