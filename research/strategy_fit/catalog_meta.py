"""
strategy_fit/catalog_meta.py — Frozen family-metadata catalog for Plan 16 triage.

All fields are static priors; no I/O, no numpy, no pandas.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyMeta:
    family_id: str
    shape: str
    horizon_label: str
    char_hold_days: int
    natural_obs_unit: str
    gross_edge_prior_bps: float
    catalog_status: str
    deferred_note: str


_POST_TRIAGE_NOTE = "added after the plan-16 triage; tested in its own study"

# Gross-edge-prior sources:
# obi_ofi_tick:      Cont et al. (2014) "Order-book dynamics in liquid markets"; aggressive taker net ~5-15 bps
# intraday_momentum: ORB studies; IDX opening-range moves 50-200 bps on breakout sessions
# daily_momentum:    Jegadeesh & Titman (1993); IDX short-term momentum 100-300 bps/swing
# mean_reversion:    Poterba & Summers (1988); emerging-market contrarian 50-150 bps
# event_driven_gap:  Opening-gap magnitude studies; IDX gaps on news/flow events 200-600 bps
# cross_sectional:   Fama-French cross-sectional IC × spread; IDX 50-150 bps/period
# pairs_stat_arb:    Gatev, Goetzmann & Rouwenhorst (2006); convergence edge 50-150 bps/round-trip
# data_blocked families: structural priors for the strategy class

ALL_FAMILIES: list[FamilyMeta] = [
    FamilyMeta("obi_ofi_tick",      "single_name_time_series", "seconds-minutes", 0,  "per_symbol_row",          10.0,  "testable_now", ""),
    FamilyMeta("intraday_momentum", "single_name_time_series", "same-day",        0,  "per_symbol_row",         100.0,  "testable_now", ""),
    FamilyMeta("daily_momentum",    "single_name_time_series", "days",            1,  "per_symbol_row",         200.0,  "testable_now", ""),
    FamilyMeta("mean_reversion",    "single_name_time_series", "days",            1,  "per_symbol_row",         100.0,  "testable_now", ""),
    FamilyMeta("event_driven_gap",  "event_driven",            "days",            1,  "pooled_event",           300.0,  "testable_now", ""),
    FamilyMeta("cross_sectional",   "cross_sectional",         "days",            1,  "cross_sectional_period", 100.0,  "testable_now", ""),
    FamilyMeta("pairs_stat_arb",    "pairs",                   "days",            2,  "spread_obs",             100.0,  "testable_now", ""),
    FamilyMeta(
        "multi_week_trend",
        "single_name_time_series",
        "weeks",
        5,
        "per_symbol_row",
        500.0,
        "data_blocked",
        "de-overlap at hold=5 gives ~48 obs/yr — barely feasible; also capital_blocked (ADR-0011 ≤2 trading-day ceiling); unblocks at 2+ yrs history AND owned cash",
    ),
    FamilyMeta(
        "multi_month_macro",
        "single_name_time_series",
        "months",
        20,
        "per_symbol_row",
        800.0,
        "data_blocked",
        "hold=20 gives ~12 obs/yr — below MIN_EFF_N=30; also capital_blocked; unblocks at 3+ yrs history AND owned cash",
    ),
    FamilyMeta(
        "multi_year_carry",
        "any",
        "years",
        60,
        "per_symbol_row",
        1500.0,
        "data_blocked",
        "requires 5+ yrs history; capital_blocked under current leverage facility",
    ),

    # --- post_triage: families added after the plan-16 triage and tested in their own study.
    # The triage scorer skips them (no gross-edge prior was set before testing, so 0.0 here).
    FamilyMeta(
        "volume_breakout", "single_name_time_series", "days", 2, "per_symbol_row", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "volume_spike_event", "event_driven", "days", 2, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "foreign_flow_spike_event", "event_driven", "days", 5, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "foreign_selling_rebound", "event_driven", "days", 3, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "index_rebalance_deletion", "event_driven", "weeks", 20, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "indicator_rules", "single_name_time_series", "days-weeks", 13, "per_symbol_row", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "factor_books", "cross_sectional", "months", 21, "cross_sectional_period", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "limit_move_events", "event_driven", "days", 5, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "dividend_events", "event_driven", "days", 5, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
    FamilyMeta(
        "calendar_effects", "event_driven", "days", 3, "pooled_event", 0.0, "post_triage", _POST_TRIAGE_NOTE,
    ),
]
