"""
strategy_fit/triage.py — CLI orchestrator for Plan 16 strategy triage.

Usage:
    python -m strategy_fit.triage
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from typing import Optional

from strategy_fit.catalog_meta import ALL_FAMILIES, FamilyMeta
from strategy_fit.families_single_name import ROUND_TRIP_BPS
from strategy_fit.triage_scorer import (
    DataBlockedRecord,
    DataInventory,
    NameAnnotations,
    TriageResult,
    score_family,
)

_UNIVERSE_SCREEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "out", "universe_screen.md"
)
_OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "out")

_MAX_HOLD_DAYS: int = 2


def _parse_turnover(cell: str) -> float:
    """Parse '168.99 B' → IDR float."""
    cell = cell.strip()
    m = re.match(r"([\d.]+)\s*([BMK]?)", cell, re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot parse turnover: {cell!r}")
    value = float(m.group(1))
    suffix = m.group(2).upper()
    multipliers = {"B": 1e9, "M": 1e6, "K": 1e3, "": 1.0}
    return value * multipliers.get(suffix, 1.0)


def _load_name_annotations(path: str) -> list[NameAnnotations]:
    annotations: list[NameAnnotations] = []
    in_table = False
    past_header_sep = False

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")

            # Stop at excluded section
            if line.strip().startswith("## Excluded"):
                break

            if not in_table:
                if line.strip().startswith("| Rank"):
                    in_table = True
                continue

            # Header separator row
            if re.match(r"^\|[-| ]+\|$", line.strip()):
                past_header_sep = True
                continue

            if not past_header_sep:
                continue

            if not line.strip().startswith("|"):
                break

            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) < 9:
                continue

            # | Rank | Symbol | Price | Tick | min_spread_bps | min_edge_to_clear_bps | Median Turnover | Vol | Flags |
            symbol = cols[1].strip()
            try:
                min_spread_bps = float(cols[4])
                min_edge_to_clear_bps = float(cols[5])
                turnover_idr = _parse_turnover(cols[6])
                vol_bps_day = float(cols[7])
            except (ValueError, IndexError):
                continue

            is_edge_capped = "edge-capped" in cols[8].lower()

            annotations.append(
                NameAnnotations(
                    symbol=symbol,
                    min_spread_bps=min_spread_bps,
                    min_edge_to_clear_bps=min_edge_to_clear_bps,
                    turnover_idr=turnover_idr,
                    vol_bps_day=vol_bps_day,
                    is_edge_capped=is_edge_capped,
                )
            )

    return annotations


def _score_family_across_names(
    family: FamilyMeta,
    names: list[NameAnnotations],
    inv: DataInventory,
    fee_bps: float,
) -> TriageResult:
    """Score per-name and return the best (highest score) TriageResult."""
    best: Optional[TriageResult] = None
    for name in names:
        result = score_family(
            family=family,
            name=name,
            inv=inv,
            fee_bps=fee_bps,
            max_hold_days=_MAX_HOLD_DAYS,
        )
        if best is None or result.score > best.score:
            best = result
    if best is None:
        # Fallback: score at universe level
        return score_family(
            family=family,
            name=None,
            inv=inv,
            fee_bps=fee_bps,
            max_hold_days=_MAX_HOLD_DAYS,
        )
    return best


def _render_markdown(
    testable: list[TriageResult],
    blocked: list[TriageResult],
    all_family_ids: set[str],
    represented_ids: set[str],
    n_names: int,
) -> str:
    today = date.today().isoformat()
    n_families = len(all_family_ids)
    lines: list[str] = [
        "# Strategy Triage Report",
        f"Generated: {today} | Universe: {n_names} names | Families: {n_families}",
        "",
        "## Section 1 — Testable Now (ranked by score)",
        "",
        "| Family | Shape | Name / Universe | Score | Gross edge prior (bps) | Friction floor (bps) | Feasible N | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(testable, key=lambda x: x.score, reverse=True):
        lines.append(
            f"| {r.family_id} | {r.shape} | {r.name} "
            f"| {r.score:.2f} | {r.gross_edge_prior_bps:.1f} "
            f"| {r.friction_floor_bps:.1f} | {r.feasible_effective_n} | {r.status} |"
        )

    lines += [
        "",
        "## Section 2 — Blocked / Deferred",
        "",
        "| Family | Status | Unblock condition |",
        "|---|---|---|",
    ]
    for r in blocked:
        note = r.unblock_note or r.data_blocked_record.need if r.data_blocked_record else r.unblock_note
        lines.append(f"| {r.family_id} | {r.status} | {note} |")

    db_records = [r.data_blocked_record for r in blocked if r.data_blocked_record is not None]
    if db_records:
        lines += [
            "",
            "### data_blocked requirement records",
            "",
            "| gap_id | kind | target | need | have |",
            "|---|---|---|---|---|",
        ]
        for rec in db_records:
            lines.append(
                f"| {rec.gap_id} | {rec.kind} | {rec.target} | {rec.need} | {rec.have} |"
            )

    lines += ["", "## Section 3 — Completeness Check", ""]
    missing = all_family_ids - represented_ids
    if not missing:
        lines.append(f"All {n_families} families in catalog accounted for.")
    else:
        for fid in sorted(missing):
            lines.append(f"GAP: family `{fid}` missing from output")

    return "\n".join(lines) + "\n"


def main() -> int:
    annotations = _load_name_annotations(os.path.abspath(_UNIVERSE_SCREEN_PATH))
    inv = DataInventory()
    fee_bps = ROUND_TRIP_BPS

    testable: list[TriageResult] = []
    blocked: list[TriageResult] = []
    gap_records: list[dict] = []
    represented_ids: set[str] = set()

    per_name_shapes = {"single_name_time_series", "pairs"}
    universe_shapes = {"event_driven", "cross_sectional"}
    # "any" shape (multi_year_carry) is already data_blocked; treat as per-name fallback

    for family in ALL_FAMILIES:
        represented_ids.add(family.family_id)

        if family.shape in per_name_shapes and annotations:
            result = _score_family_across_names(family, annotations, inv, fee_bps)
        elif family.shape in universe_shapes:
            result = score_family(
                family=family,
                name=None,
                inv=inv,
                fee_bps=fee_bps,
                max_hold_days=_MAX_HOLD_DAYS,
            )
        else:
            # "any" or unknown: score at universe level (will be data_blocked)
            result = score_family(
                family=family,
                name=None,
                inv=inv,
                fee_bps=fee_bps,
                max_hold_days=_MAX_HOLD_DAYS,
            )

        if result.status == "testable_now":
            testable.append(result)
        else:
            blocked.append(result)
            if result.data_blocked_record is not None:
                rec = result.data_blocked_record
                gap_records.append(
                    {
                        "gap_id": rec.gap_id,
                        "kind": rec.kind,
                        "target": rec.target,
                        "need": rec.need,
                        "have": rec.have,
                        "gated_alpha": rec.gated_alpha,
                        "provenance": rec.provenance,
                    }
                )

    all_family_ids = {f.family_id for f in ALL_FAMILIES}

    md = _render_markdown(testable, blocked, all_family_ids, represented_ids, len(annotations))

    os.makedirs(_OUT_DIR, exist_ok=True)
    md_path = os.path.join(_OUT_DIR, "strategy_triage.md")
    gaps_path = os.path.join(_OUT_DIR, "strategy_triage_gaps.json")

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    with open(gaps_path, "w", encoding="utf-8") as fh:
        json.dump(gap_records, fh, indent=2)

    print(f"Written: {md_path}")
    print(f"Written: {gaps_path}")
    print(f"Testable now: {len(testable)} | Blocked/deferred: {len(blocked)}")

    missing = all_family_ids - represented_ids
    if missing:
        print(f"COMPLETENESS FAIL: missing {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
