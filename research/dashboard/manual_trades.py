"""
dashboard/manual_trades.py — trades the account owner placed by hand in the broker app.

The bot's post-trade stores know nothing about these, yet they change what the account holds.
They are kept in ``runs/manual_trades.json`` (gitignored with the rest of runs/, which carries
the account number); ``manual_trades.example.json`` beside this file documents the schema.

Loading is strict: an unknown field, a bad side, a non-positive lots/price or a bad date fails
the build with a message naming the trade, rather than rendering a quietly wrong ledger. A
missing file is not an error: it means there are no manual trades.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date

SHARES_PER_LOT = 100

REQUIRED = ("date", "time_wib", "symbol", "side", "lots", "price_idr", "fee_idr", "note")
OPTIONAL = ("net_amount_idr", "broker_realised_pnl_idr", "closes_order_id")
SIDES = ("buy", "sell")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,8}$")


class ManualTradesError(ValueError):
    """The manual-trades file is malformed. The build stops; fix the file."""


@dataclass(frozen=True)
class ManualTrade:
    date: str
    """YYYY-MM-DD."""
    time_wib: str | None
    """HH:MM[:SS] Jakarta time, or None when not recorded."""
    symbol: str
    side: str
    lots: int
    price_idr: float
    fee_idr: float
    note: str
    net_amount_idr: float | None = None
    broker_realised_pnl_idr: float | None = None
    closes_order_id: str | None = None

    @property
    def notional_idr(self) -> float:
        return self.price_idr * self.lots * SHARES_PER_LOT


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse_one(raw: object, n: int) -> ManualTrade:
    where = f"trade #{n}"

    def bad(field: str, why: str) -> ManualTradesError:
        return ManualTradesError(f"{where}: {field} {why}")

    if not isinstance(raw, dict):
        raise ManualTradesError(f"{where}: must be a JSON object")
    unknown = sorted(set(raw) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise ManualTradesError(f"{where}: unknown field(s) {', '.join(unknown)}")
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise ManualTradesError(f"{where}: missing field(s) {', '.join(missing)}")

    day = raw["date"]
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise bad("date", f"must be YYYY-MM-DD, got {day!r}")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise bad("date", f"is not a real calendar date: {day!r}") from None

    t = raw["time_wib"]
    if t is not None and not (isinstance(t, str) and _TIME.match(t)):
        raise bad("time_wib", f"must be HH:MM, HH:MM:SS or null, got {t!r}")

    sym = raw["symbol"]
    if not isinstance(sym, str) or not _SYMBOL.match(sym):
        raise bad("symbol", f"must be an upper-case ticker like GOTO, got {sym!r}")
    side = raw["side"]
    if side not in SIDES:
        raise bad("side", f"must be 'buy' or 'sell', got {side!r}")
    lots = raw["lots"]
    if not isinstance(lots, int) or isinstance(lots, bool) or lots <= 0:
        raise bad("lots", f"must be a whole number above 0, got {lots!r}")
    price = raw["price_idr"]
    if not _is_number(price) or price <= 0:
        raise bad("price_idr", f"must be a number above 0, got {price!r}")
    fee = raw["fee_idr"]
    if not _is_number(fee) or fee < 0:
        raise bad("fee_idr", f"must be a number, 0 or more, got {fee!r}")
    note = raw["note"]
    if not isinstance(note, str):
        raise bad("note", f"must be a string, got {note!r}")

    for key in ("net_amount_idr", "broker_realised_pnl_idr"):
        v = raw.get(key)
        if v is not None and not _is_number(v):
            raise bad(key, f"must be a number, got {v!r}")
    closes = raw.get("closes_order_id")
    if closes is not None and (not isinstance(closes, str) or not closes):
        raise bad("closes_order_id", f"must be a non-empty string, got {closes!r}")

    return ManualTrade(
        date=day, time_wib=t, symbol=sym, side=side, lots=lots, price_idr=float(price),
        fee_idr=float(fee), note=note,
        net_amount_idr=raw.get("net_amount_idr"),
        broker_realised_pnl_idr=raw.get("broker_realised_pnl_idr"),
        closes_order_id=closes,
    )


def parse_manual_trades(doc: object) -> list[ManualTrade]:
    if not isinstance(doc, dict) or not isinstance(doc.get("trades"), list):
        raise ManualTradesError('top level must be an object with a "trades" list')
    extra = sorted(set(doc) - {"trades", "_schema"})
    if extra:
        raise ManualTradesError(f"unknown top-level field(s) {', '.join(extra)}")
    return [_parse_one(raw, i) for i, raw in enumerate(doc["trades"], start=1)]


def load_manual_trades(path: str) -> list[ManualTrade]:
    """Every trade in ``path``; [] when the file does not exist. Raises ManualTradesError if invalid."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as exc:
        raise ManualTradesError(f"{os.path.basename(path)} is not valid JSON: {exc}") from exc
    try:
        return parse_manual_trades(doc)
    except ManualTradesError as exc:
        raise ManualTradesError(f"{os.path.basename(path)}: {exc}") from exc
