"""
dashboard/strategy_words.py — plain-English wording for the Strategies page.

Prose only: what each family bets on, what it does in one sentence, and what each shortlist gate
means. No numbers are decided here; every figure on the page comes from the research outputs.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyWords:
    bet: str
    rule: str
    run_families: tuple[str, ...] = ()
    """Names the backtest report (strategy_portfolio.md) uses for this family's cases."""
    pooled_families: tuple[str, ...] = ()
    """Names the shortlist's pooled (all-stocks) rows use."""


FAMILY_WORDS: dict[str, FamilyWords] = {
    "obi_ofi_tick": FamilyWords(
        "Lopsided buy/sell orders in the order book predict the next few seconds of price.",
        "Trade in the direction of the order-book imbalance, holding seconds to minutes.",
        ("order_book_obi",)),
    "intraday_momentum": FamilyWords(
        "A stock that breaks out of its morning range keeps going until the close.",
        "Buy when the price closes above the opening range high, sell at the end of the same day.",
        ()),
    "daily_momentum": FamilyWords(
        "A stock that rose yesterday keeps rising for a few days.",
        "Buy after an up day (close higher than the day before), hold a fixed number of days.",
        ("daily_momentum",), ("daily_momentum",)),
    "mean_reversion": FamilyWords(
        "A price pushed far from its recent average snaps back.",
        "Buy when the price is well below its 5-day average, hold a fixed number of days.",
        ("mean_reversion",), ("mean_reversion",)),
    "event_driven_gap": FamilyWords(
        "A big overnight jump carries information and the move continues.",
        "Buy after the open gaps more than 2% in our direction, hold a fixed number of days.",
        ("gap_event",), ("gap_event",)),
    "cross_sectional": FamilyWords(
        "Among all stocks, yesterday's winners beat yesterday's losers tomorrow.",
        "Rank every stock by its last-day return and buy the top fifth (long only: we cannot short).",
        ("cross_sectional_momentum",)),
    "pairs_stat_arb": FamilyWords(
        "Two stocks that move together drift apart and then come back together.",
        "Trade the gap between a pair when it stretches. Needs a short leg, which we do not have.",
        ("pairs_cointegration",)),
    "multi_week_trend": FamilyWords(
        "Trends that last weeks can be ridden.",
        "Hold for a week or more in the direction of the trend.",
        ("multi_week_trend",)),
    "multi_month_macro": FamilyWords(
        "Months-long moves follow slow-changing fundamentals.",
        "Hold for about a month or more in the direction of the longer trend.",
        ("multi_month_momentum",)),
    "multi_year_carry": FamilyWords(
        "Owning cheap, high-yield stocks for years pays off.",
        "Hold for years; needs much more price history than we have.",
        ()),
}

EXTRA_FAMILIES: dict[str, FamilyWords] = {
    "volume_breakout": FamilyWords(
        "A day of unusually heavy trading marks a real move that continues.",
        "Buy after a volume spike on an up day, hold a fixed number of days.",
        ("volume_breakout",), ("volume_breakout",)),
    "volume_spike_event": FamilyWords(
        "Abnormal volume precedes a continuation of the day's direction.",
        "Buy when volume is far above normal and the day closed up, hold a fixed number of days.",
        ("volume_spike_event",), ("volume_spike_event",)),
    "foreign_flow_spike_event": FamilyWords(
        "Heavy net buying by foreign investors pushes the price further.",
        "Buy after an unusually large foreign net-buy day, hold a fixed number of days.",
        ("foreign_flow_spike_event",), ("foreign_flow_spike_event",)),
}

GATE_FAIL_WORDS: dict[str, str] = {
    "G2": "the edge over simply holding the stock could easily be luck (t-stat below 2)",
    "G3": "the edge does not cover trading costs under every cost estimate",
    "G4": "the average turns negative once the 5 best trades are removed",
    "G5": "it did not hold up in the independent 2015-2023 prices",
    "G7": "no strategy of this type works across all stocks (so one stock looking good is probably luck)",
}
GATE_PASS_WORDS: dict[str, str] = {
    "G2": "edge is statistically clear",
    "G3": "edge covers costs",
    "G4": "survives dropping the best 5 trades",
    "G5": "holds in 2015-2023 prices",
    "G7": "works across all stocks",
}

STAGE_NAMES = {
    "live": "Live",
    "live_candidate": "Live candidate",
    "paper": "Paper trading",
    "study": "Needs more study",
    "retired": "Retired",
}
STAGE_TONES = {"live": "green", "live_candidate": "amber", "paper": "grey", "study": "amber", "retired": "red"}
STAGE_ORDER = ("live", "live_candidate", "paper", "study", "retired")

REGENERATE_SHORTLIST = "cd research && python3 -m strategy_fit.shortlist_2026_10 --out-dir out_shortlist"
REGENERATE_UNIVERSE = "make refresh-prices"
