"""
dashboard/broker_import.py — trades the broker's own history shows that no bot fill explains.

``account-recon --stores`` finds trades in the broker's history with no matching bot fill (an order the
owner placed by hand in the app) and records them, keyed by the broker's id so that re-running adds
nothing new, in ``runs/broker_trades.json``. They are real fills, so the live ledger must count them.

They are folded into the same list as the hand-entered ``runs/manual_trades.json`` (``manual_trades.py``),
under three rules that keep every trade counted exactly once:

* an import the bot's own store already explains (same symbol, side, date, price and lots, within
  minutes) is dropped: the bot fill is the record;
* a hand-entered manual trade for the same symbol, side, date, price and lots is superseded by the
  import: the broker's figures are the truth;
* each bot fill and each manual entry explains at most one import.

The file holds no account number or name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from dashboard.manual_trades import ManualTrade
from dashboard.trades_data import OrderLine

SOURCE = "broker-import"
MATCH_WITHIN = timedelta(minutes=5)
PRICE_EPS = 0.005

_FIELDS = ("broker_id", "source", "date", "time_wib", "symbol", "side", "lots", "price_idr", "fee_idr")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,8}$")


class BrokerImportError(ValueError):
    """runs/broker_trades.json is malformed. The build stops; re-run the reconciliation."""


@dataclass(frozen=True)
class BrokerImport:
    broker_id: str
    date: str
    time_wib: str | None
    symbol: str
    side: str
    lots: int
    price_idr: float
    fee_idr: float


def _num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse_one(raw: object, n: int) -> BrokerImport:
    where = f"broker trade #{n}"
    if not isinstance(raw, dict):
        raise BrokerImportError(f"{where}: must be a JSON object")
    unknown = sorted(set(raw) - set(_FIELDS))
    if unknown:
        raise BrokerImportError(f"{where}: unknown field(s) {', '.join(unknown)}")
    if raw.get("source") != SOURCE:
        raise BrokerImportError(f"{where}: source must be {SOURCE!r}, got {raw.get('source')!r}")
    bid = raw.get("broker_id")
    if not isinstance(bid, str) or not bid:
        raise BrokerImportError(f"{where}: broker_id must be a non-empty string")
    day = raw.get("date")
    try:
        date.fromisoformat(day)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise BrokerImportError(f"{where}: date must be YYYY-MM-DD, got {day!r}") from None
    t = raw.get("time_wib")
    if t is not None and t != "" and not (isinstance(t, str) and _TIME.match(t)):
        raise BrokerImportError(f"{where}: time_wib must be HH:MM[:SS] or empty, got {t!r}")
    sym = raw.get("symbol")
    if not isinstance(sym, str) or not _SYMBOL.match(sym):
        raise BrokerImportError(f"{where}: symbol must be an upper-case ticker, got {sym!r}")
    side = raw.get("side")
    if side not in ("buy", "sell"):
        raise BrokerImportError(f"{where}: side must be 'buy' or 'sell', got {side!r}")
    lots = raw.get("lots")
    if not isinstance(lots, int) or isinstance(lots, bool) or lots <= 0:
        raise BrokerImportError(f"{where}: lots must be a whole number above 0, got {lots!r}")
    price, fee = raw.get("price_idr"), raw.get("fee_idr")
    if not _num(price) or price <= 0:
        raise BrokerImportError(f"{where}: price_idr must be above 0, got {price!r}")
    if not _num(fee) or fee < 0:
        raise BrokerImportError(f"{where}: fee_idr must be 0 or more, got {fee!r}")
    return BrokerImport(bid, day, t or None, sym, side, lots, float(price), float(fee))


def load_broker_imports(path: str) -> list[BrokerImport]:
    """Every import in ``path``; [] when the file does not exist."""
    if not os.path.exists(path):
        return []
    name = os.path.basename(path)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise BrokerImportError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("trades"), list):
        raise BrokerImportError(f'{name}: top level must be an object with a "trades" list')
    extra = sorted(set(doc) - {"trades", "_schema"})
    if extra:
        raise BrokerImportError(f"{name}: unknown top-level field(s) {', '.join(extra)}")
    try:
        return [_parse_one(r, i) for i, r in enumerate(doc["trades"], start=1)]
    except BrokerImportError as exc:
        raise BrokerImportError(f"{name}: {exc}") from exc


def _when(day: str, clock: str | None) -> datetime | None:
    if not clock:
        return None
    clock = clock if len(clock) > 5 else clock + ":00"
    return datetime.fromisoformat(f"{day}T{clock}")


def _explains(fill: OrderLine, i: BrokerImport) -> bool:
    if (fill.symbol, fill.side, fill.filled_lots) != (i.symbol, i.side, i.lots):
        return False
    if fill.fill_price is None or abs(fill.fill_price - i.price_idr) > PRICE_EPS:
        return False
    at = fill.when.replace(tzinfo=None)  # OrderLine.when is Jakarta wall-clock time
    if at.date().isoformat() != i.date:
        return False
    t = _when(i.date, i.time_wib)
    return t is None or abs(at - t) <= MATCH_WITHIN


def _same_trade(m: ManualTrade, i: BrokerImport) -> bool:
    return ((m.symbol, m.side, m.date, m.lots) == (i.symbol, i.side, i.date, i.lots)
            and abs(m.price_idr - i.price_idr) <= PRICE_EPS)


def fold_into_manual(
    manual: Sequence[ManualTrade], imports: Sequence[BrokerImport], live: Sequence[OrderLine],
) -> list[ManualTrade]:
    """Manual trades plus broker imports, each real trade once. See the module docstring."""
    free_fills = [x for x in live if x.filled]
    keep_manual = list(manual)
    folded: list[ManualTrade] = []
    for i in imports:
        hit = next((f for f in free_fills if _explains(f, i)), None)
        if hit is not None:
            free_fills.remove(hit)
            continue
        dup = next((m for m in keep_manual if _same_trade(m, i)), None)
        if dup is not None:
            keep_manual.remove(dup)
        folded.append(ManualTrade(
            date=i.date, time_wib=i.time_wib, symbol=i.symbol, side=i.side, lots=i.lots, price_idr=i.price_idr,
            fee_idr=i.fee_idr, note=f"Imported from the broker's own history (broker id {i.broker_id})."))
    return keep_manual + folded
