"""
dashboard/paper_tests.py — one row per paper / live-candidate emitter spec, for the Overview.

Reuses strategy_fit (registry, paper_stores, paper_scorecard); nothing is re-derived here. Read-only:
the stores are opened ``mode=ro`` by paper_stores, no network, no tokens.

Failure isolation: a spec whose stores cannot be read or scored becomes a row with ``error`` set.
If strategy_fit itself cannot be imported, ``collect`` returns None and the page says "not checked".
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path

_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
SHOWN_STATUSES = ("paper", "candidate")


@dataclass(frozen=True)
class PaperRow:
    strategy_id: str
    symbols: tuple[str, ...]
    hold: int
    status: str                       # "paper" | "candidate"
    days_run: int = 0
    holding: tuple[tuple[str, int], ...] = ()
    n_trips: int = 0
    min_trips: int = 15
    mean_net_bps: float | None = None
    verdict: str = ""
    error: str | None = None

    @property
    def started(self) -> bool:
        return self.days_run > 0


def _store_days(stores) -> int:
    return len({m.group(1) for p in stores if (m := _DATE_PREFIX.match(Path(p).name))})


def _short(exc: Exception) -> str:
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= 120 else text[:117] + "..."


def _row(spec, runs_dir: Path, daily_dir: Path, ps, sc) -> PaperRow:
    base = dict(strategy_id=spec.strategy_id, symbols=tuple(spec.symbols), hold=spec.hold,
                status=spec.status, min_trips=sc.MIN_TRIPS)
    try:
        stores = ps.list_stores(runs_dir, spec.strategy_id)
        if not stores:
            return PaperRow(**base)
        card = sc.build_scorecard(spec, runs_dir, daily_dir)
        verdict = "Not enough trips yet" if card.verdict.startswith("INCOMPLETE") else card.verdict
        return PaperRow(**base, days_run=_store_days(stores),
                        holding=tuple(sorted(card.open_positions.items())),
                        n_trips=card.n_trips, mean_net_bps=card.mean_net_bps, verdict=verdict)
    except Exception as exc:  # noqa: BLE001 - one bad spec must not stop the page
        return PaperRow(**base, error=_short(exc))


def collect(runs_dir: str, daily_dir: str, specs=None) -> list[PaperRow] | None:
    """Rows for every spec with status paper/candidate; None if strategy_fit cannot be imported."""
    try:
        sc = importlib.import_module("strategy_fit.paper_scorecard")
        ps = importlib.import_module("strategy_fit.paper_stores")
        SPECS = importlib.import_module("strategy_fit.emitter_specs").SPECS
    except Exception:  # noqa: BLE001
        return None
    registry = SPECS if specs is None else specs
    wanted = [s for s in registry.values() if s.status in SHOWN_STATUSES]
    return [_row(s, Path(runs_dir), Path(daily_dir), ps, sc) for s in wanted]


def banner_text() -> str:
    try:
        from strategy_fit.paper_scorecard import BANNER
        return BANNER.lower()
    except Exception:  # noqa: BLE001
        return "filter for gross failures, not proof of edge"
