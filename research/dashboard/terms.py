"""
dashboard/terms.py — the plain-language glossary. Pages link to these anchors instead of
explaining terms inline.

These are written for a reader who is technically strong but has never read this project's
reports. They explain what a word means *on this site*. The exact, code-derived definitions
of every stored field are on the same page, further down (glossary.py).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    key: str
    name: str
    plain: str


TERMS: tuple[Term, ...] = (
    # --- the money -------------------------------------------------------
    Term("rupiah-per-1m", "Rupiah per 1M traded",
         "A reading aid. Percent results are also shown as the rupiah you would make or lose per "
         "trade if you put Rp 1,000,000 into it. It is not your account size; the live sub-account "
         "is about Rp 14.9 million."),
    Term("bps", "bps (basis points)",
         "One hundredth of a percent: 100 bps = 1%. Shown only as secondary detail."),
    Term("lot", "Lot",
         "The smallest board lot on the Indonesia Stock Exchange: 100 shares. '2 lots of GOTO at "
         "Rp 28' is 200 shares costing Rp 5,600."),
    Term("limit-price", "Limit price",
         "The worst price the order accepts: a buy limit of Rp 28 will not pay more than Rp 28."),
    Term("filled", "Filled",
         "The exchange actually matched the order. An order can be sent and never filled (price "
         "moved away, or the broker refused it)."),
    Term("fees", "Fees",
         "Broker and exchange charges in rupiah, as recorded when the order filled. A figure of 0 "
         "can mean the fee was not captured for that fill."),
    Term("realised-pnl", "Realised profit / loss",
         "Money actually made or lost on a position that has been closed (bought and then sold). "
         "A position that is still open has no realised profit yet."),
    Term("round-trip", "Round trip",
         "One complete buy followed by the matching sell."),
    Term("open-position", "Open position",
         "Shares bought and not yet sold. Shown at what they cost, not at today's price."),
    # --- where an order ran ---------------------------------------------
    Term("live", "Live",
         "A real order sent to the broker with real money."),
    Term("manual-trade", "Manual trade",
         "A trade the account owner placed by hand in the Stockbit app, not the bot. The bot's records "
         "do not contain it, so it is typed into runs/manual_trades.json and merged with the bot's real-money "
         "fills. It is real money and never counts as practice. Labelled 'manual (app)' on the Trades page."),
    Term("reconciliation-note", "Reconciliation note",
         "A line that sets our figure beside the broker's for the same sale, shows the difference and says "
         "why they differ (for example a fee the bot's records did not capture). Both numbers are kept as "
         "reported; nothing is silently corrected."),
    Term("paper", "Paper / shadow",
         "A practice run: the bot decides and records orders as if it traded, but nothing is sent "
         "to the broker. Paper results never count towards real profit and are never added to it."),
    Term("sub-account", "Sub-account (sleeve)",
         "The small account the bot is allowed to trade, about Rp 14.9 million, separate from the "
         "rest of the portfolio."),
    # --- the backtest ----------------------------------------------------
    Term("backtest", "Backtest",
         "Replaying a strategy's rules over past prices to see what it would have earned. It is "
         "evidence, not proof: the future can differ."),
    Term("cell", "Cell",
         "One backtest case: a particular stock, a particular strategy type and a particular "
         "holding period. For example 'BIPI, momentum, 5 days'."),
    Term("strategy-type", "Strategy type",
         "The rule that decides when to buy. Three are tested on daily prices: "
         "momentum (yesterday's rise continues), mean reversion (a stretched price snaps back) and "
         "volume breakout (a volume spike marks a real move)."),
    Term("holding-days", "Holding days",
         "How many trading days a position is kept before selling."),
    Term("next-session-entry", "Next-session entry",
         "A signal appears when a day's closing price is known, so the earliest real purchase is the "
         "next day's close. An earlier version of the backtest bought at the very price that "
         "created the signal, which nobody can do; that flattering mistake was fixed on 2026-10-14 "
         "and all results on this site use the corrected entry."),
    Term("after-costs", "After costs",
         "Profit once trading costs are taken off: fees, the gap between buy and sell prices, and "
         "the price movement your own order causes. Costs here are the backtest's own full cost figure "
         "for that stock, not a guess made by this site."),
    Term("win-rate", "Win rate",
         "The share of trades that made a profit after costs."),
    Term("buy-and-hold", "Just holding the stock",
         "What you would have made by buying at the strategy's first entry and doing nothing until "
         "its last exit. A strategy only earns its keep if it beats this."),
    Term("best-five", "Without the best 5 trades",
         "The average profit per trade after removing the five most profitable trades. If it turns "
         "negative, the strategy's profit came from a handful of lucky trades."),
    Term("luck-test", "Could be luck",
         "With hundreds of stock-and-strategy cases tried, some look good by chance. The backtest "
         "corrects for that (a statistical test called false-discovery-rate control); a case that "
         "fails could easily be chance."),
    Term("beats-holding", "Beats just holding",
         "The backtest also tests the same stock with no signal at all, buying on every possible day. "
         "If that does about as well, the stock simply went up and the signal added nothing."),
    Term("later-data", "Later, unseen data",
         "The history is split: the strategy is judged on the first 60% and then checked on the "
         "last 40%, which it was not tuned on. Passing the check means the edge held up there."),
    Term("candidate", "Candidate",
         "A case that passed every backtest check. It is a reason to run a forward test, not "
         "permission to trade."),
    Term("not-tradeable", "Not tradeable",
         "A case that failed at least one backtest check; the sentence says which."),
    Term("forward-test", "Forward test",
         "Trading a strategy in real time (usually on paper first) with its rules fixed in advance, "
         "to see whether the backtest result holds on data it could not have been fitted to."),
    Term("market-impact", "Market impact",
         "Your own order moves the price against you a little; for thinly traded stocks this can "
         "be as large as the fee."),
    Term("gate", "Gate",
         "A pass/fail check a case must clear. The funnel on the Research page shows how many cases "
         "stop at each one."),
    # --- the machinery ---------------------------------------------------
    Term("collector", "Market-data collector",
         "The program on a cloud server that records the exchange's live price feed all day so "
         "that strategies can be tested on real intraday data later."),
    Term("s3", "S3",
         "Amazon's cloud file storage, where the collector's recordings are kept."),
    Term("not-checked", "Not checked",
         "The dashboard could not run that check from where it was built, so it says so and gives the "
         "exact command to run, instead of guessing."),
)

_BY_KEY = {t.key: t for t in TERMS}


def term_anchor(key: str) -> str:
    return f"t-{key}"


def require_term(key: str) -> Term:
    """Look a term up or fail the build: a link to a missing definition is a bug."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"glossary term {key!r} is not defined in dashboard/terms.py; add it. "
            f"Known: {', '.join(sorted(_BY_KEY))}"
        ) from None


def term_link(key: str, text: str | None = None) -> str:
    """An anchor into glossary.html for a plain term."""
    t = require_term(key)
    label = text or t.name
    return f'<a class="term" href="glossary.html#{term_anchor(key)}">{label}</a>'
