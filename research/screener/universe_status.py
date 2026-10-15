"""
screener/universe_status.py — which companies are eligible and still tradable, on disk.

``make refresh-prices`` builds this before refreshing prices and writes it next to the
parquet store (``data/daily/universe_status.json``). Two consumers:
  * the refresher, to stop fetching names that can no longer trade and to add newly
    eligible ones the store never had;
  * the dashboard's "Daily price history" row, which reads it to tell a delisting
    apart from a stale feed (a fresh delisting is invisible to the 60-day rule).

Built from the two emitten classification calls only. Per symbol: ``in_day_trade`` /
``in_trading_limit`` (the funding gate is their intersection), ``is_tradable`` (the
classification ``tradeable`` flag; False wins if the two lists disagree; ``null`` = not
on a funding list or flag missing) plus ``company_status`` and ``notations`` for the
record. Standard library only.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from screener.emitten import (
    DEFAULT_API_HOST,
    SUBSECTOR_DAY_TRADE,
    SUBSECTOR_TRADING_LIMIT,
    ClassificationError,
    CompanyRecord,
    merge_tradeable,
)
from screener.sb_http import classify_error

logger = logging.getLogger(__name__)

EMITTEN_STEP = "emitten classification"

SCHEMA_VERSION = 2
STATUS_FILENAME = "universe_status.json"


class UniverseStatusError(RuntimeError):
    """A build step failed; ``str()`` is the reason, ``where`` is "(step, host)"."""

    def __init__(self, reason: str, where: str) -> None:
        super().__init__(reason)
        self.where = where


class _Emitten(Protocol):
    def fetch_classification(self, subsector_id: str) -> tuple[CompanyRecord, ...]: ...


@dataclass(frozen=True)
class SymbolStatus:
    in_day_trade: bool
    in_trading_limit: bool
    is_tradable: bool | None
    company_status: str | None = None
    notations: tuple[str, ...] = ()


@dataclass(frozen=True)
class UniverseStatus:
    as_of: datetime.date
    symbols: dict[str, SymbolStatus]

    @property
    def eligible(self) -> set[str]:
        """The funding gate: day-trade AND trading-limit."""
        return {s for s, v in self.symbols.items() if v.in_day_trade and v.in_trading_limit}

    def not_tradable(self) -> set[str]:
        return {s for s, v in self.symbols.items() if v.is_tradable is False}

    def tradable(self) -> set[str]:
        return {s for s, v in self.symbols.items() if v.is_tradable is True}


def _symbol_status(day_trade: CompanyRecord | None, trading_limit: CompanyRecord | None) -> SymbolStatus:
    rows = [r for r in (day_trade, trading_limit) if r is not None]
    if not rows:
        return SymbolStatus(False, False, None)
    return SymbolStatus(
        day_trade is not None,
        trading_limit is not None,
        merge_tradeable(r.tradeable for r in rows),
        rows[0].company_status,
        rows[0].notations,
    )


def build_universe_status(stored: Iterable[str], emitten: _Emitten, *, as_of: datetime.date) -> UniverseStatus:
    """
    Fetch both classification lists (they raise on failure - a half list must never be
    written as fact) and derive per-symbol status for every stored and every
    funding-eligible symbol. A stored symbol on neither list is is_tradable=None.
    """
    try:
        day_trade = {r.symbol: r for r in emitten.fetch_classification(SUBSECTOR_DAY_TRADE)}
        trading_limit = {r.symbol: r for r in emitten.fetch_classification(SUBSECTOR_TRADING_LIMIT)}
    except Exception as exc:  # noqa: BLE001 - relabel with step/host, never swallow
        reason = str(exc) if isinstance(exc, ClassificationError) else classify_error(exc)
        raise UniverseStatusError(reason, f"({EMITTEN_STEP}, {DEFAULT_API_HOST})") from exc
    universe = set(stored) | (day_trade.keys() & trading_limit.keys())
    symbols = {s: _symbol_status(day_trade.get(s), trading_limit.get(s)) for s in sorted(universe)}
    return UniverseStatus(as_of, symbols)


def write_universe_status(status: UniverseStatus, store_dir: str) -> str:
    """Atomic write (temp file in the same dir + rename); returns the file path."""
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "as_of": status.as_of.isoformat(),
        "symbols": {
            s: {"in_day_trade": v.in_day_trade, "in_trading_limit": v.in_trading_limit,
                "is_tradable": v.is_tradable, "company_status": v.company_status,
                "notations": list(v.notations)}
            for s, v in sorted(status.symbols.items())
        },
    }
    os.makedirs(store_dir, exist_ok=True)
    path = os.path.join(store_dir, STATUS_FILENAME)
    fd, tmp = tempfile.mkstemp(dir=store_dir, prefix=".universe_status.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def load_universe_status(store_dir: str) -> UniverseStatus | None:
    """The saved status, or None when the file is missing, unreadable or a different schema."""
    path = os.path.join(store_dir, STATUS_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if doc["schema_version"] != SCHEMA_VERSION:
            return None
        symbols = {
            s: SymbolStatus(bool(v["in_day_trade"]), bool(v["in_trading_limit"]), v["is_tradable"],
                         v.get("company_status"), tuple(v.get("notations") or ()))
            for s, v in doc["symbols"].items()
        }
        return UniverseStatus(datetime.date.fromisoformat(doc["as_of"]), symbols)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
