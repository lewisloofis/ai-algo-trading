"""
dashboard/strategy_sections.py — HTML for the Strategies page's sections (data comes from strategy_status.py).

Cells passed to ``table`` are trusted HTML, so every value is escaped here.
"""

from __future__ import annotations

from dashboard import paper_tests as PT
from dashboard import strategy_families as F
from dashboard import strategy_status as S
from dashboard import strategy_words as W
from dashboard.render import Column, banner, esc, money, pill, table, tile

NOT_CHECKED_SHORTLIST = (
    "Not checked: the shortlist file could not be used ({why}). Produce it with "
    "<code>" + W.REGENERATE_SHORTLIST + "</code> (about 2.5 minutes; add <code>--skip-pooled</code> "
    "for the name rows only)."
)


def _not_checked(msg: str) -> str:
    return f'<p class="empty">{msg}</p>'


# ---------------------------------------------------------------------------
# A. Where each strategy stands
# ---------------------------------------------------------------------------

STAGE_COLS = (Column("Strategy"), Column("Stage"), Column("Where it stands"))


def _stage_row(r: S.StageRow, detail_links: dict[str, str]) -> list[str]:
    lines = "".join(f"<div>{esc(x)}</div>" for x in r.lines)
    name = f'<b>{esc(r.name)}</b><div class="small">{esc(r.sub)}</div>'
    return [name, pill(W.STAGE_TONES[r.stage], W.STAGE_NAMES[r.stage]), lines]


def stand_section(rows: list[S.StageRow] | None, paper_rows) -> str:
    head = "<h2>Where each strategy stands</h2>"
    if rows is None:
        return head + _not_checked("Not checked: the strategy research package could not be loaded, so the "
                                   "strategy list is unknown. Run the dashboard from <code>research/</code>.")
    counts = S.stage_counts(rows)
    tiles = "".join(tile(str(counts[s]), W.STAGE_NAMES[s], hint) for s, hint in (
        ("live", "running with real money now"),
        ("live_candidate", "cleared the bar, not started"),
        ("paper", "practice runs with pretend money"),
        ("study", "close, failed a check"),
        ("retired", "ruled out")))
    paper_note = ""
    if paper_rows is not None:
        ids = sorted(p.strategy_id for p in paper_rows)
        cmd = " or ".join(f"<code>make paper-scorecard STRATEGY={esc(i)}</code>" for i in ids[:2])
        paper_note = (f'<p class="small">The paper scorecard is a {esc(PT.banner_text())}. Practice orders never '
                      f"count in the account numbers. Full scorecard: {cmd}.</p>" if ids else "")
    return (
        head
        + '<p class="lead">Every strategy we have an exact definition for, and how far along it is.</p>'
        + f'<div class="tiles">{tiles}</div>'
        + table(STAGE_COLS, [_stage_row(r, {}) for r in rows], empty="No strategy is defined yet.")
        + paper_note
    )


# ---------------------------------------------------------------------------
# B. Families
# ---------------------------------------------------------------------------

FAMILY_COLS = (Column("Strategy family"), Column("What it bets on"), Column("Holding", term="holding-days"),
               Column("Can we test it?"), Column("Our result so far"))


def _family_status(f: F.FamilyRow) -> str:
    if f.status == "testable_now":
        return esc("Yes" + (f" ({f.status_note})" if f.status_note else ""))
    label = "Blocked by data" if f.status == "data_blocked" else f.status.replace("_", " ").capitalize()
    return esc(label) + (f'<div class="small">{esc(f.status_note)}</div>' if f.status_note else "")


def families_section(rows: list[F.FamilyRow]) -> str:
    head = "<h2>Strategy families we consider</h2>"
    if not rows:
        return head + _not_checked("Not checked: the family catalog (strategy_fit/catalog_meta.py) could not be loaded.")
    body = []
    for f in rows:
        ev = "".join(f'<div class="small">{esc(x)}</div>' for x in f.evidence)
        body.append([
            f"<b>{esc(f.family_id.replace('_', ' '))}</b>",
            f'{esc(f.bet)}<div class="small">Rule: {esc(f.rule)}</div>',
            esc(f.horizon), _family_status(f), pill(f.tone, f.headline) + ev,
        ])
    return (head + '<p class="lead">Each family is a different idea about when to buy. Results are the latest '
            "backtest and shortlist; money is after trading costs.</p>" + table(FAMILY_COLS, body))


# ---------------------------------------------------------------------------
# C. The screen
# ---------------------------------------------------------------------------

def _criteria() -> list[str]:
    out = ["Funding gate: the stock is on both Stockbit's day-trade list and its trading-limit list "
           "(every funding option unlocked)."]
    try:
        from screener import screen_universe as SU
        from screener.emitten import SUBSECTOR_DAY_TRADE, SUBSECTOR_TRADING_LIMIT  # noqa: F401
        out.append(f"Cost check: a round trip costs {SU.ROUND_TRIP_BPS:.0f} bps in fees ({SU.BUY_BPS:.0f} buying, "
                   f"{SU.SELL_BPS:.0f} selling). A stock whose one-tick price gap already exceeds "
                   f"{SU.EDGE_CAP_THRESHOLD_BPS:.0f} bps is flagged edge-capped.")
        out.append(f"Liquidity: median daily turnover below Rp {SU.TOO_THIN_TURNOVER_IDR / 1e9:.0f} billion is "
                   "flagged too thin (a placeholder floor, not yet derived).")
    except Exception:  # noqa: BLE001
        out.append("Cost and liquidity flags: not shown (the screener module could not be loaded).")
    out.append("Tradeable flag: Stockbit does not mark the stock untradeable (for example suspended).")
    try:
        from lib.stats import MIN_EFF_N
        out.append(f"Enough history: at least {MIN_EFF_N} independent trades before a result is judged.")
    except Exception:  # noqa: BLE001
        pass
    return out


def _symbol_grid(symbols) -> str:
    cells = "".join(f"<code>{esc(s)}</code> " for s in symbols)
    return f'<p style="max-width:none;line-height:1.9">{cells}</p>'


def screen_section(inp: S.Inputs) -> str:
    head = "<h2>Stocks in the screen</h2>"
    crit = "<ul>" + "".join(f"<li>{esc(c)}</li>" for c in _criteria()) + "</ul>"
    sl, uni = inp.shortlist, inp.universe
    parts = [head, '<p class="lead">The screen decides which stocks we test strategies on.</p>',
             "<h3>Criteria</h3>", crit]
    if uni is not None:
        parts.append(f'<p>Last ran <b>{esc(uni.as_of)}</b>: <b>{len(uni.eligible)}</b> stocks pass the funding gate'
                     + (f", <b>{len(uni.not_tradable)}</b> flagged untradeable and excluded" if uni.not_tradable else "")
                     + ".</p>")
        passing = [s for s in uni.eligible if s not in set(uni.not_tradable)]
        parts.append(f"<details open><summary>The {len(passing)} stocks that pass</summary>{_symbol_grid(passing)}</details>")
        if uni.not_tradable:
            parts.append(f"<details><summary>Excluded as untradeable ({len(uni.not_tradable)})</summary>"
                         f"{_symbol_grid(uni.not_tradable)}</details>")
        return "".join(parts)
    parts.append(banner("warn", "Not checked: the funding-gate file <code>data/daily/universe_status.json</code> was not "
                        f"found, so the funding and tradeable gates cannot be confirmed. Produce it with "
                        f"<code>{W.REGENERATE_UNIVERSE}</code> (needs your Stockbit token)."))
    if inp.stored_symbols:
        n = f" The latest shortlist tested <b>{sl.n_names}</b> of them." if sl else ""
        parts.append(f"<p>Stocks we hold daily price history for: <b>{len(inp.stored_symbols)}</b>.{n}"
                     + (f" Shortlist prices end {esc(sl.as_of)}." if sl else "") + "</p>"
                     f"<details><summary>List of stocks with price history ({len(inp.stored_symbols)})</summary>"
                     f"{_symbol_grid(inp.stored_symbols)}</details>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# D. Combinations
# ---------------------------------------------------------------------------

COMBO_COLS = (
    Column("Stock"),
    Column("Strategy and hold", term="holding-days"),
    Column("Trades in backtest", numeric=True),
    Column("Edge after costs (bps)", numeric=True, term="edge-after-costs"),
    Column("Survives dropping best 5?", term="best-five"),
    Column("Out-of-sample?", term="later-data"),
    Column("Checks passed", term="shortlist-gates"),
    Column("Now"),
    Column("Cost per lot"),
)


def _yes_no(ok: bool | None) -> str:
    return "–" if ok is None else ("Yes" if ok else "No")


def _combo_row(c: S.Combo, links: dict[tuple[str, str, int], str]) -> list[str]:
    key = (c.symbol, c.family, c.hold)
    name = f"<b>{esc(c.symbol)}</b>" + (f' {pill("grey", "On paper")}' if c.on_paper else "")
    if key in links:
        name += f'<div class="small"><a href="{esc(links[key])}">Chart &amp; trades</a></div>'
    oos = "–" if c.oos_edge_bps is None else (
        f"{_yes_no(c.oos_edge_bps > 0)}<div class=\"small\">{c.oos_edge_bps:+.0f} bps, {c.oos_n or '?'} trades</div>")
    drop = _yes_no(c.drop5_ok) + ("" if c.drop5_bps is None else f'<div class="small">{c.drop5_bps:+.0f} bps</div>')
    words = "; ".join(c.gates_words) or "none"
    cost = "–" if c.lot_notional is None else (
        f"1 lot = {money(c.lot_notional)}<div class=\"small\">{money(c.trip_cost)} per round trip, "
        f"about {money(c.month_cost)} a month</div>")
    return [name, f"{esc(S.family_label(c.family))}<div class=\"small\">hold {esc(S.hold_text(c.hold))}</div>",
            f"{c.n_trades}", f"{c.edge_after_costs_bps:+.0f}", drop, oos,
            f'{c.gates_passed} of {c.gates_total}<div class="small">{esc(words)}</div>',
            esc(c.state), cost]


def combos_section(sl: S.Shortlist | None, why: str, combos: list[S.Combo], links, today) -> str:
    head = "<h2>Promising strategy + stock combinations</h2>"
    if sl is None:
        return head + _not_checked(NOT_CHECKED_SHORTLIST.format(why=esc(why)))
    age = S.shortlist_age_days(sl, today)
    stale = ""
    if age is not None and age > S.STALE_AFTER_DAYS:
        stale = banner("warn", f"The shortlist is {age} days old (prices up to {esc(sl.as_of)}). Regenerate: "
                       f"<code>{W.REGENERATE_SHORTLIST}</code>")
    intro = (f'<p class="lead">Pairs where the strategy beat simply holding the stock, by more than one lot of trading '
             f"costs. Shortlist as of <b>{esc(sl.as_of)}</b>, {sl.n_names} stocks tested. No pair passes every check, "
             "so none is ready for real money; the two on paper are the closest.</p>")
    rows = [_combo_row(c, links) for c in combos]
    top, rest = rows[:S.TOP_COMBOS], rows[S.TOP_COMBOS:]
    body = table(COMBO_COLS, top, empty="No pair has a positive edge after costs.")
    if rest:
        body += (f"<details><summary>Show all ({len(rows)})</summary>{table(COMBO_COLS, rest)}</details>")
    return head + stale + intro + body


# ---------------------------------------------------------------------------
# Recent runs
# ---------------------------------------------------------------------------

RUN_COLS = (Column("State"), Column("Strategy"), Column("Mode"), Column("Last heartbeat"),
            Column("Events seen", numeric=True), Column("Positions held"), Column("Error"))


def runs_section(runs: list | None) -> str:
    head = "<h2>Recent runs</h2>"
    if runs is None:
        return head + _not_checked("Not checked: heartbeat files could not be read.")
    body = [[pill(r.tone, r.state_label), f"<b>{esc(r.strategy_id)}</b>", esc(r.mode),
             f"{esc(r.written_wib)}<div class=\"small\">{esc(r.heartbeat_text)}</div>",
             esc("–" if r.events_seen is None else r.events_seen), esc(r.positions_text), esc(r.error or "–")]
            for r in runs]
    return (head + f'<p class="small">Sessions that are no longer running, from the last {S.RUN_HISTORY_DAYS} days. '
            "Sessions running right now are on the Overview.</p>"
            + table(RUN_COLS, body, empty=f"No finished, stopped or failed runs in the last {S.RUN_HISTORY_DAYS} days."))
