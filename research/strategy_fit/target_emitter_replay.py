"""
strategy_fit/target_emitter_replay.py — replay the emitter over history and
check it trades the same entries as the backtest cell.

``backtest_entries`` re-runs the three steps run_single_name_family applies
(signal_fn -> harness.deoverlap_daily -> long_only_mask) and keeps the rows;
the scorer itself only returns aggregates. A test cross-checks the rows
against the scorer's eff_n / gross_edge_bps so the two cannot drift.

``replay`` then calls ``target_emitter.decide`` for every trading day D using
only bars before D, and compares with the backtest, shifted by the D1 timing
difference: the backtest enters at close_s, the emitter on the next session
E = next_trading_day(s).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from lib.market_calendar import MarketCalendar
from strategy_fit.harness import deoverlap_daily
from strategy_fit.long_only import long_only_mask
from strategy_fit.target_emitter import (
    HISTORY_START,
    EmitterError,
    EmitterSpec,
    decide_normalised,
    normalise_bars,
)


@dataclass(frozen=True)
class ReplayResult:
    n_backtest_total: int
    n_backtest_entries: int
    n_emitter_entries: int
    n_matched: int
    entry_match_rate: float
    days_checked: int
    days_skipped_gap: int
    weight_mismatches: list[date] = field(default_factory=list)
    unmatched_backtest: list[date] = field(default_factory=list)
    unmatched_emitter: list[date] = field(default_factory=list)


def calendar_from_bars(bars: pd.DataFrame, base: MarketCalendar) -> MarketCalendar:
    """`base` plus every weekday inside the bars' span that has no bar.

    calendar/idx_holidays.json only covers 2026, so replaying 2024-25 bars
    against it would treat those years' holidays as missing sessions. For a
    replay the bars themselves are the record of which days traded.
    """
    dates = {date.fromisoformat(d) for d in normalise_bars(bars)["date"]}
    d, last = min(dates), max(dates)
    missing = set()
    while d <= last:
        if d.weekday() < 5 and d not in dates:
            missing.add(d)
        d += timedelta(days=1)
    return MarketCalendar(frozenset(base._holidays) | frozenset(missing), base.provisional)


def backtest_entries(df: pd.DataFrame, spec: EmitterSpec) -> pd.DataFrame:
    """The cell's traded rows: date (= signal day s), signal, forward_return."""
    window = normalise_bars(df)
    window = window[window["date"] >= HISTORY_START].reset_index(drop=True)
    sig = spec.signal_fn(window, hold=spec.hold)
    kept = deoverlap_daily(sig, hold=spec.hold)
    mask = long_only_mask(kept["signal"].to_numpy(dtype=float))
    return kept[mask].reset_index(drop=True)


def replay(df: pd.DataFrame, spec: EmitterSpec, calendar: MarketCalendar) -> ReplayResult:
    bars = normalise_bars(df)
    bars = bars[bars["date"] >= HISTORY_START].reset_index(drop=True)
    dates = [date.fromisoformat(d) for d in bars["date"]]
    bar_set = set(dates)

    # The backtest has no forward return for the last `hold` bars, so rows
    # entered at or after bars[-hold] are not comparable.
    cutoff = dates[-spec.hold]
    expected_entry_days: set[date] = set()
    expected_on: set[date] = set()
    n_total = 0
    for s in backtest_entries(bars, spec)["date"]:
        n_total += 1
        e = calendar.next_trading_day(date.fromisoformat(s))
        expected_entry_days.add(e)
        d = e
        for _ in range(spec.hold):
            expected_on.add(d)
            d = calendar.next_trading_day(d)

    checked: list[date] = []
    skipped = 0
    d = calendar.next_trading_day(dates[1])
    while d <= cutoff:
        if calendar.prev_trading_day(d) in bar_set:
            checked.append(d)
        else:
            skipped += 1
        d = calendar.next_trading_day(d)

    emitter_entries: set[date] = set()
    weight_mismatches: list[date] = []
    for d in checked:
        try:
            dec = decide_normalised(bars, d, spec, calendar)
        except EmitterError:
            skipped += 1
            continue
        if dec.weight == 1.0 and dec.entry_day == d:
            emitter_entries.add(d)
        if (dec.weight == 1.0) != (d in expected_on):
            weight_mismatches.append(d)

    checked_set = set(checked)
    expected = expected_entry_days & checked_set
    matched = expected & emitter_entries
    union = expected | emitter_entries
    rate = len(matched) / len(union) if union else 1.0
    return ReplayResult(
        n_backtest_total=n_total,
        n_backtest_entries=len(expected),
        n_emitter_entries=len(emitter_entries),
        n_matched=len(matched),
        entry_match_rate=rate,
        days_checked=len(checked),
        days_skipped_gap=skipped,
        weight_mismatches=sorted(weight_mismatches),
        unmatched_backtest=sorted(expected - emitter_entries),
        unmatched_emitter=sorted(emitter_entries - expected),
    )


def format_result(r: ReplayResult, spec: EmitterSpec) -> str:
    return "\n".join([
        f"replay {spec.strategy_id} ({spec.symbol} {spec.family} hold={spec.hold}) "
        f"from {HISTORY_START}",
        f"  days checked            {r.days_checked} (skipped for bar gaps: {r.days_skipped_gap})",
        f"  backtest entries (cell) {r.n_backtest_total}, comparable {r.n_backtest_entries}  (entry day = next session after signal close, D1)",
        f"  emitter entries         {r.n_emitter_entries}",
        f"  matched                 {r.n_matched}",
        f"  entry match rate        {r.entry_match_rate:.4f}",
        f"  weight-by-day mismatches {len(r.weight_mismatches)}",
        f"  unmatched backtest      {[str(x) for x in r.unmatched_backtest]}",
        f"  unmatched emitter       {[str(x) for x in r.unmatched_emitter]}",
    ])
