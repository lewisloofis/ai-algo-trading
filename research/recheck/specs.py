"""
recheck/specs.py — the frozen spec of every re-checkable closed family (plan 26 §1), and the ones that are not.

A spec points at the existing family code (nothing is re-implemented here) and freezes ONE hold per family,
so a re-check cannot go hunting across holds. The hold is the one the verdict's own pooled table singled
out: the cell with the highest cluster-robust t for edge over the always-long null in
``research/results/shortlist/shortlist.md`` (family-level, pooled over the universe, 2024-01-01..2026-10-15),
or, for the families scored elsewhere, the pre-registered cell. Why each hold:

  daily_momentum            h5   pooled t 0.36 (h1 -0.14, h2 -0.11, h20 0.15, h60 0.04); also the hold of every
                                 name-level survivor (WIFI, PTRO, DEWA, RAJA, TAPG, BIPI).
  mean_reversion            h1   pooled t 0.01 (h2 -1.02, h5 -0.52, h20 -0.25, h60 -0.10); nothing is positive,
                                 h1 is simply the highest.
  event_driven_gap          h2   gap_event pooled t 0.54 (h1 0.31, h5 0.37, h20 0.42, h60 0.51). h60 is the only
                                 cell with positive edge-net, but the verdict records it as not robust to the top
                                 5 trades, so the rule (highest t) is kept rather than chasing the net.
  volume_breakout           h2   pooled t 0.15, the verdict's "t <= 0.15".
  volume_spike_event        h2   pooled t 1.23, the verdict's "best t 1.23 at h2".
  foreign_flow_spike_event  h5   pooled t 1.97 (Holm p 0.74), the verdict's "+58.6 bps at h5".
  cross_sectional           h5   next_close paired t -0.23 (h1 -0.26, h2 -1.25, h20 -1.15; h60 has no eff_n) in
                                 results/d1_xsec/xsec_production.md.
  factor_books              h21  mom_12_1 only, the pre-registered cell (plan 26 §1.1: "mom_12_1 h21 only"); the
                                 h63 look and the other six factors are not re-checked.
  limit_move_events         h5   ARB (down) events, next_close entry, the cell the verdict named; ARA is not.
                                 The bear / non-bear split is reported, never used as a filter.
  dividend_events           h5   the "runup" variant (buy close(x-6), sell close(x-1)), dividend yield >= 2%.
  calendar_effects          h3   turn_of_month, the verdict's "+27 vs 87 bps cost"; pre_lebaran is not re-checked.

All specs use entry ``next_close`` (the plan-20 D1 convention every one of these verdicts was decided under),
the always-long null of their own study, and the CURRENT cost stack (``strategy_fit/impact_coeffs.json``,
tick spread, 40 bps fees, financing). Effective N is the number of independent observation units (see
``recheck.stats``): for families whose trades overlap, a non-overlapping block of ``hold`` consecutive trading
days by entry date (``unit_hold`` = hold; all trades in a block count once, and the t-stat is HAC); for families
that already emit one non-overlapping period per observation (cross_sectional, factor_books, the monthly
calendar_effects windows), one unit per period (``unit_hold`` = 1).

``lookback_bars`` is the most history, in bars before the first post-verdict signal, that the family's signal
and costs read. It is what the poison test leaves intact: signal DATES and trades are always post-verdict;
only the trailing windows that compute a signal may look back.

cross_sectional universe: the verdict was scored on 115 names from a symbols file (``S.txt``) that is not in the
repo. The re-check uses every name that traded within 7 days of as_of (roughly 199), so its ranks, null and
median floor are not on exactly the verdict's universe. Accepted as a documented deviation (owner, 2026-10-17);
``run`` prints it as a note line in the output.

``K`` is frozen as a literal: adding or removing a spec changes the alert bar (0.10 / (3 * K)), so it must be
a deliberate edit of both, and a test pins them together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd

from recheck import daily_pooled, idx, xsec
from recheck.stats import ALPHA_FAMILY, MILESTONES, N_LOOKS  # noqa: F401  (re-exported: the frozen bar)

K = 11
"""Number of families re-checked; the Bonferroni divisor is N_LOOKS * K. Pinned to len(SPECS) by a test."""

ENTRY = "next_close"
UNIT_BLOCK = "non-overlapping block of `hold` trading days by entry date (pooled trades in a block count once)"
UNIT_PERIOD = "rebalance period"

Builder = Callable[[str, date, date], pd.DataFrame]


@dataclass(frozen=True)
class RecheckSpec:
    family_id: str
    source: str
    """Plan / doc section holding the frozen rule."""
    hold: int
    entry: str
    unit: str
    """What one effective-N observation is."""
    lookback_bars: int
    build: Builder
    """(daily_dir, signal_start_exclusive, as_of) -> records: signal_date, entry_date, gross_bps, null_bps,
    cost_bps (current cost stack); optional symbol / regime_bear."""
    unit_hold: int
    """Trading days per independent observation unit (recheck.stats): the hold for families whose trades overlap
    (one unit per non-overlapping block of that many trading days), 1 for families that already emit one
    non-overlapping period per observation. Always explicit, never inferred."""
    hold_note: str = ""


SPECS: tuple[RecheckSpec, ...] = (
    RecheckSpec(
        "daily_momentum", "docs/research/2026-10-15-first-candidate-shortlist.md (pooled table)", 5, ENTRY, UNIT_BLOCK, daily_pooled.LOOKBACK_BARS,
        daily_pooled.pooled_builder("daily_momentum", 5), unit_hold=5, hold_note="highest pooled t (0.36)"),
    RecheckSpec(
        "mean_reversion", "docs/research/2026-10-15-first-candidate-shortlist.md (pooled table)", 1, ENTRY, UNIT_BLOCK, daily_pooled.LOOKBACK_BARS,
        daily_pooled.pooled_builder("mean_reversion", 1), unit_hold=1, hold_note="highest pooled t (0.01)"),
    RecheckSpec(
        "event_driven_gap", "docs/research/2026-10-15-first-candidate-shortlist.md (pooled table, gap_event)", 2, ENTRY, UNIT_BLOCK, daily_pooled.LOOKBACK_BARS,
        daily_pooled.pooled_builder("gap_event", 2), unit_hold=2, hold_note="highest pooled t (0.54)"),
    RecheckSpec(
        "volume_breakout", "docs/research/2026-10-15-first-candidate-shortlist.md (pooled table)", 2, ENTRY, UNIT_BLOCK, daily_pooled.LOOKBACK_BARS,
        daily_pooled.pooled_builder("volume_breakout", 2), unit_hold=2, hold_note="highest pooled t (0.15)"),
    RecheckSpec(
        "volume_spike_event", "docs/research/2026-10-15-first-candidate-shortlist.md (pooled table)", 2, ENTRY, UNIT_BLOCK, daily_pooled.LOOKBACK_BARS,
        daily_pooled.pooled_builder("volume_spike_event", 2), unit_hold=2, hold_note="highest pooled t (1.23)"),
    RecheckSpec(
        "foreign_flow_spike_event", "docs/research/2026-10-15-first-candidate-shortlist.md (pooled table)", 5, ENTRY, UNIT_BLOCK, daily_pooled.LOOKBACK_BARS,
        daily_pooled.pooled_builder("foreign_flow_spike_event", 5), unit_hold=5, hold_note="highest pooled t (1.97)"),
    RecheckSpec(
        "cross_sectional", "research/results/d1_xsec/xsec_production.md (next_close rows)", 5, ENTRY, UNIT_PERIOD,
        xsec.IMPACT_LOOKBACK_BARS, xsec.xsec_builder(5), unit_hold=1, hold_note="highest paired t (-0.23)"),
    RecheckSpec(
        "factor_books", "docs/research/alpha-search-2026-10.md (mom_12_1)", 21, ENTRY, UNIT_PERIOD, idx.LOOKBACK_BARS,
        idx.BUILDERS["factor_books"], unit_hold=1, hold_note="pre-registered cell: mom_12_1 h21 only"),
    RecheckSpec(
        "limit_move_events", "docs/research/alpha-search-2026-10.md (ARB next_close)", 5, ENTRY, UNIT_BLOCK, idx.LOOKBACK_BARS,
        idx.BUILDERS["limit_move_events"], unit_hold=5, hold_note="ARB 5d next_close"),
    RecheckSpec(
        "dividend_events", "docs/research/alpha-search-2026-10.md (runup, yield >= 2%)", 5, ENTRY, UNIT_BLOCK, idx.LOOKBACK_BARS,
        idx.BUILDERS["dividend_events"], unit_hold=5, hold_note="runup variant"),
    RecheckSpec(
        "calendar_effects", "docs/research/alpha-search-2026-10.md (turn_of_month)", 3, ENTRY, UNIT_PERIOD, idx.LOOKBACK_BARS,
        idx.BUILDERS["calendar_effects"], unit_hold=1, hold_note="turn_of_month window (one per month)"),
)

EXCLUDED: dict[str, str] = {
    "foreign_selling_rebound": "excluded (owner, 2026-10-17): unspent holdout overlaps post-verdict data",
    "indicator_rules": "excluded (owner, 2026-10-17): unspent holdout overlaps post-verdict data",
    "index_rebalance_deletion": "waiting: needs new MSCI_ST deletion events; event ingestion not built",
    "obi_ofi_tick": "waiting: needs post-2026-06-19 tick captures of the original names",
    "intraday_momentum": "waiting: needs 1-minute bars after the verdict; not wired",
    "pairs_stat_arb": "skipped: fails structurally (no shorting)",
}

UNTESTED_NOTE = "not re-checked: untested"


def spec_for(family_id: str) -> RecheckSpec | None:
    return next((s for s in SPECS if s.family_id == family_id), None)
