"""
strategy_fit/triage_scorer.py — Pure triage scoring functions for Plan 16.

No I/O.  No numpy.  No pandas.  All functions are fully type-annotated.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Optional

from strategy_fit.catalog_meta import FamilyMeta
from strategy_fit.families_single_name import ROUND_TRIP_BPS
from strategy_fit.financing import financing_bps
from screener.metrics import impact_bps

MIN_EFF_N: int = 30
DEFAULT_POSITION_IDR: float = 50_000_000
FINANCING_ENTRY_DOW: int = 0


@dataclass(frozen=True)
class NameAnnotations:
    symbol: str
    min_spread_bps: float
    min_edge_to_clear_bps: float
    turnover_idr: float
    vol_bps_day: float
    is_edge_capped: bool


@dataclass(frozen=True)
class DataInventory:
    """Static approximation of available data history (plan 16 §6.7).

    Values are conservative priors from plan-09/10 data notes.
    """

    daily_bar_trading_days: int = 239
    intraday_trading_days_core: int = 60
    intraday_trading_days_survivors: int = 5
    universe_size: int = 5


@dataclass(frozen=True)
class DataBlockedRecord:
    gap_id: str
    kind: str
    target: str
    need: str
    have: str
    gated_alpha: float
    provenance: str


@dataclass(frozen=True)
class TriageResult:
    family_id: str
    shape: str
    name: str
    status: str
    gross_edge_prior_bps: float
    friction_floor_bps: float
    feasible_effective_n: int
    score: float
    data_blocked_record: Optional[DataBlockedRecord]
    unblock_note: str


def _gap_id(kind: str, target: str, need: str) -> str:
    raw = f"{kind}|{target}|{need}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


_INTRADAY_CORE_SYMBOLS: frozenset[str] = frozenset({"PTRO", "BRPT", "TPIA", "ENRG"})

# Universe-level conservative medians from plan-09 annotations.
# BUMI 59.5, ITMG 9.0, MDKA 35.7, ANTM 30.9, DEWA 188.7 → median 35.7
_UNIVERSE_MEDIAN_SPREAD_BPS: float = 35.7
_UNIVERSE_MEDIAN_TURNOVER_IDR: float = 100_000_000_000.0
_UNIVERSE_MEDIAN_VOL_BPS: float = 112.0


def _feasible_n(
    family: FamilyMeta,
    name: Optional[NameAnnotations],
    inv: DataInventory,
) -> int:
    hold = max(family.char_hold_days, 1)
    if family.shape == "cross_sectional":
        return inv.daily_bar_trading_days
    elif family.shape == "event_driven":
        return max(1, int(inv.universe_size * inv.daily_bar_trading_days / 20))
    elif family.shape == "pairs":
        return max(1, inv.daily_bar_trading_days // hold)
    else:
        if family.char_hold_days == 0:
            if name is not None and name.symbol in _INTRADAY_CORE_SYMBOLS:
                return inv.intraday_trading_days_core
            return inv.intraday_trading_days_survivors
        return max(1, inv.daily_bar_trading_days // hold)


def score_family(
    family: FamilyMeta,
    name: Optional[NameAnnotations],
    inv: DataInventory,
    fee_bps: float,
    max_hold_days: int,
) -> TriageResult:
    name_label = name.symbol if name is not None else "universe"

    # --- Step 1: capital ceiling check ---
    if family.char_hold_days > max_hold_days and family.catalog_status != "data_blocked":
        need = f"char_hold_days={family.char_hold_days} ≤ max_hold_days={max_hold_days}"
        have = f"max_hold_days={max_hold_days}"
        gid = _gap_id("regime", family.family_id, need)
        rec = DataBlockedRecord(
            gap_id=gid,
            kind="regime",
            target=family.family_id,
            need=need,
            have=have,
            gated_alpha=family.gross_edge_prior_bps,
            provenance=f"triage:{family.family_id}",
        )
        return TriageResult(
            family_id=family.family_id,
            shape=family.shape,
            name=name_label,
            status="data_blocked",
            gross_edge_prior_bps=family.gross_edge_prior_bps,
            friction_floor_bps=0.0,
            feasible_effective_n=0,
            score=0.0,
            data_blocked_record=rec,
            unblock_note=f"capital_blocked: hold {family.char_hold_days}d exceeds {max_hold_days}d ceiling",
        )

    # --- Step 2: taxonomy-level data_blocked families ---
    if family.catalog_status == "data_blocked":
        eff_n = _feasible_n(family, name, inv)
        need = family.deferred_note
        have = f"daily_bar_trading_days={inv.daily_bar_trading_days}"
        gid = _gap_id("length", family.family_id, need)
        rec = DataBlockedRecord(
            gap_id=gid,
            kind="length",
            target=family.family_id,
            need=need,
            have=have,
            gated_alpha=family.gross_edge_prior_bps,
            provenance=f"triage:{family.family_id}",
        )
        return TriageResult(
            family_id=family.family_id,
            shape=family.shape,
            name=name_label,
            status="data_blocked",
            gross_edge_prior_bps=family.gross_edge_prior_bps,
            friction_floor_bps=0.0,
            feasible_effective_n=eff_n,
            score=0.0,
            data_blocked_record=rec,
            unblock_note=family.deferred_note,
        )

    # --- Step 2b: deferred families ---
    if family.catalog_status == "deferred":
        return TriageResult(
            family_id=family.family_id,
            shape=family.shape,
            name=name_label,
            status="deferred",
            gross_edge_prior_bps=family.gross_edge_prior_bps,
            friction_floor_bps=0.0,
            feasible_effective_n=0,
            score=0.0,
            data_blocked_record=None,
            unblock_note=family.deferred_note,
        )

    # --- Step 3: friction floor (computed before N-check so cost-gate takes precedence) ---
    hold_capped = min(family.char_hold_days, max_hold_days)
    fin_bps = financing_bps(FINANCING_ENTRY_DOW, hold_capped)

    if name is not None:
        imp = impact_bps(
            q=DEFAULT_POSITION_IDR,
            adv=name.turnover_idr,
            sigma_bps=name.vol_bps_day,
            half_spread_bps=name.min_spread_bps / 2,
        )
    else:
        imp = impact_bps(
            q=DEFAULT_POSITION_IDR,
            adv=_UNIVERSE_MEDIAN_TURNOVER_IDR,
            sigma_bps=_UNIVERSE_MEDIAN_VOL_BPS,
            half_spread_bps=_UNIVERSE_MEDIAN_SPREAD_BPS / 2,
        )

    # impact_bps already includes half_spread — no separate spread term (plan §3.4)
    friction_floor = fee_bps + fin_bps + imp

    # --- Step 4: cost-gate check (before N-check: unviable strategies are cost_gated, not data_blocked) ---
    if family.gross_edge_prior_bps <= friction_floor:
        eff_n = _feasible_n(family, name, inv)
        return TriageResult(
            family_id=family.family_id,
            shape=family.shape,
            name=name_label,
            status="cost_gated",
            gross_edge_prior_bps=family.gross_edge_prior_bps,
            friction_floor_bps=friction_floor,
            feasible_effective_n=eff_n,
            score=0.0,
            data_blocked_record=None,
            unblock_note=f"gross-edge prior ({family.gross_edge_prior_bps} bps) ≤ friction floor ({friction_floor:.1f} bps)",
        )

    # --- Step 2c: effective-N check for nominally testable families ---
    eff_n = _feasible_n(family, name, inv)
    if eff_n < MIN_EFF_N:
        target_label = f"{family.family_id}:{name_label}"
        need = f"eff_n ≥ {MIN_EFF_N} independent observations"
        have = f"eff_n={eff_n}"
        gid = _gap_id("length", target_label, need)
        rec = DataBlockedRecord(
            gap_id=gid,
            kind="length",
            target=target_label,
            need=need,
            have=have,
            gated_alpha=family.gross_edge_prior_bps,
            provenance=f"triage:{family.family_id}",
        )
        return TriageResult(
            family_id=family.family_id,
            shape=family.shape,
            name=name_label,
            status="data_blocked",
            gross_edge_prior_bps=family.gross_edge_prior_bps,
            friction_floor_bps=friction_floor,
            feasible_effective_n=eff_n,
            score=0.0,
            data_blocked_record=rec,
            unblock_note=f"eff_n={eff_n} < MIN_EFF_N={MIN_EFF_N}; need more history",
        )

    # --- Step 5: score ---
    score = family.gross_edge_prior_bps / friction_floor

    return TriageResult(
        family_id=family.family_id,
        shape=family.shape,
        name=name_label,
        status="testable_now",
        gross_edge_prior_bps=family.gross_edge_prior_bps,
        friction_floor_bps=friction_floor,
        feasible_effective_n=eff_n,
        score=score,
        data_blocked_record=None,
        unblock_note="",
    )
