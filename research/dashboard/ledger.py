"""
dashboard/ledger.py — one live ledger from the bot's filled orders and the owner's manual trades.

Guarantees (each pinned by test_ledger.py):

* **Live only.** Paper orders are refused (ValueError); manual trades are live by definition.
* **FIFO per symbol.** A sell closes the oldest open lots first. Realised profit uses each
  trade's own price and fee: proceeds minus both legs' fees minus cost, fees prorated by lots
  when a sell spans several buys.
* **A sell that would take a position below zero fails the build** (NegativePositionError).
  That is how a double-entered manual sale, or a sale of something never bought, is caught.
* **A link to a bot order must be real** (UnknownOrderError): ``closes_order_id`` names an order
  in the stores, on the same symbol, or the build stops.
* **Where the broker gave its own realised figure, both are kept** and the gap is explained,
  never silently reconciled: neither the store's fee nor the broker's is rewritten.

Ordering inside a day: legs with a known time go by time. A manual trade with no recorded time
goes after the day's timed legs, buys before sells, so a same-day round trip still pairs up.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Sequence

from dashboard.manual_trades import SHARES_PER_LOT, ManualTrade
from dashboard.trades_data import LIVE, OrderLine, Position

BOT = "bot"
MANUAL = "manual"

AGREE_WITHIN_IDR = 0.5


class LedgerError(ValueError):
    """The merged ledger cannot be built from the inputs given. The build stops."""


class NegativePositionError(LedgerError):
    """A sell would take a position below zero (or sells something never bought)."""


class UnknownOrderError(LedgerError):
    """A manual trade names a bot order that is not in the stores."""


@dataclass(frozen=True)
class Leg:
    source: str
    day: str
    symbol: str
    side: str
    lots: int
    price: float
    fee: float
    order_id: str
    trade: ManualTrade | None
    sort_key: tuple


@dataclass(frozen=True)
class Match:
    buy_day: str
    buy_order_id: str
    lots: int
    buy_price: float
    buy_fee: float
    """The share of the buy's fee that belongs to the matched lots."""


@dataclass(frozen=True)
class Closing:
    sell: Leg
    matches: tuple[Match, ...]
    pnl_idr: float
    sell_fee: float


@dataclass(frozen=True)
class Reconciliation:
    symbol: str
    day: str
    ours_idr: float
    broker_idr: float
    difference_idr: float
    """Broker minus ours."""
    implied_buy_fee_idr: float
    recorded_buy_fee_idr: float
    reason: str
    closes_order_id: str | None
    trade: ManualTrade


@dataclass(frozen=True)
class Ledger:
    legs: tuple[Leg, ...]
    closings: tuple[Closing, ...]
    positions: tuple[Position, ...]
    realised_pnl_idr: float
    round_trips_closed: int
    fees_idr: float
    reconciliations: tuple[Reconciliation, ...]


def _bot_leg(x: OrderLine, idx: int) -> Leg:
    return Leg(BOT, x.when.date().isoformat(), x.symbol, x.side, x.filled_lots, float(x.fill_price or 0.0),
               float(x.fees_idr), x.order_id, None, (x.when.date().isoformat(), 0, x.when.strftime("%H:%M:%S"), idx))


def _manual_leg(t: ManualTrade, idx: int) -> Leg:
    if t.time_wib:
        key = (t.date, 0, t.time_wib if len(t.time_wib) > 5 else t.time_wib + ":00", idx)
    else:
        key = (t.date, 1, "buy sell".index(t.side), idx)
    return Leg(MANUAL, t.date, t.symbol, t.side, t.lots, t.price_idr, t.fee_idr, t.closes_order_id or "", t, key)


def _check_links(manual: Sequence[ManualTrade], live: Sequence[OrderLine]) -> None:
    by_id = {x.order_id: x for x in live if x.order_id}
    for t in manual:
        if t.closes_order_id is None:
            continue
        found = by_id.get(t.closes_order_id)
        if found is None:
            raise UnknownOrderError(
                f"manual {t.side} of {t.symbol} on {t.date} says it closes order {t.closes_order_id!r}, "
                "but no live order with that id is in the stores")
        if found.symbol != t.symbol:
            raise UnknownOrderError(
                f"manual {t.side} of {t.symbol} on {t.date} links order {t.closes_order_id!r}, "
                f"which is a {found.symbol} order")


def _reason(t: ManualTrade, ours: float, broker: float, implied_buy_fee: float, recorded_buy_fee: float) -> str:
    diff = broker - ours
    if abs(diff) < AGREE_WITHIN_IDR:
        return f"Our figure and the broker's agree to within Rp {AGREE_WITHIN_IDR:.2f}."
    parts = []
    buy_gap = implied_buy_fee - recorded_buy_fee
    if buy_gap >= AGREE_WITHIN_IDR:
        parts.append(
            f"bot store recorded buy fee {recorded_buy_fee:g} (broker {implied_buy_fee:.2f}): the broker's "
            "cost basis implies that buy fee. This is a store fee-recording discrepancy; neither number "
            "has been changed here.")
    if t.net_amount_idr is not None:
        net_fee = t.notional_idr - t.net_amount_idr
        if abs(t.fee_idr - net_fee) >= AGREE_WITHIN_IDR:
            parts.append(
                f"The sell fee also differs by rounding: the fee entered is Rp {t.fee_idr:,.2f}, "
                f"the broker's net amount implies Rp {net_fee:,.2f}.")
    return " ".join(parts) or "Reason not identified from the figures on file."


def _reconcile(c: Closing) -> Reconciliation | None:
    t = c.sell.trade
    if t is None or t.broker_realised_pnl_idr is None:
        return None
    net = t.net_amount_idr if t.net_amount_idr is not None else t.notional_idr - t.fee_idr
    matched_cost = sum(m.lots * SHARES_PER_LOT * m.buy_price for m in c.matches)
    recorded = sum(m.buy_fee for m in c.matches)
    implied = (net - t.broker_realised_pnl_idr) - matched_cost
    return Reconciliation(
        symbol=t.symbol, day=t.date, ours_idr=c.pnl_idr, broker_idr=t.broker_realised_pnl_idr,
        difference_idr=t.broker_realised_pnl_idr - c.pnl_idr, implied_buy_fee_idr=implied,
        recorded_buy_fee_idr=recorded, reason=_reason(t, c.pnl_idr, t.broker_realised_pnl_idr, implied, recorded),
        closes_order_id=t.closes_order_id, trade=t)


def build_ledger(live: Sequence[OrderLine], manual: Sequence[ManualTrade]) -> Ledger:
    """FIFO-match every live fill (bot and manual) per symbol. See the module docstring."""
    if any(x.sink_kind != LIVE for x in live):
        raise ValueError("build_ledger was given paper orders; paper and live must never be mixed")
    _check_links(manual, live)
    legs = [_bot_leg(x, i) for i, x in enumerate(live) if x.filled]
    legs += [_manual_leg(t, i) for i, t in enumerate(manual)]
    legs.sort(key=lambda g: g.sort_key)

    open_lots: dict[str, deque[list]] = {}  # [day, remaining_lots, price, fee_total, orig_lots, order_id]
    closings: list[Closing] = []
    for g in legs:
        book = open_lots.setdefault(g.symbol, deque())
        if g.side == "buy":
            book.append([g.day, g.lots, g.price, g.fee, g.lots, g.order_id])
            continue
        held = sum(lot[1] for lot in book)
        if g.lots > held:
            raise NegativePositionError(
                f"{g.symbol}: a {g.source} sell of {g.lots} lot(s) on {g.day} would leave the position at "
                f"{held - g.lots} lot(s) (only {held} held). Check for a duplicated manual entry or a missing buy.")
        need, matches, pnl, sell_fee = g.lots, [], 0.0, 0.0
        while need:
            lot = book[0]
            take = min(need, lot[1])
            buy_fee = lot[3] * take / lot[4]
            fee_part = g.fee * take / g.lots
            pnl += take * SHARES_PER_LOT * (g.price - lot[2]) - buy_fee - fee_part
            sell_fee += fee_part
            matches.append(Match(lot[0], lot[5], take, lot[2], buy_fee))
            lot[1] -= take
            need -= take
            if lot[1] == 0:
                book.popleft()
        closings.append(Closing(g, tuple(matches), pnl, sell_fee))

    positions = []
    for sym in sorted(open_lots):
        remaining = [lot for lot in open_lots[sym] if lot[1] > 0]
        n = sum(lot[1] for lot in remaining)
        if n == 0:
            continue
        cost = sum(lot[1] * lot[2] for lot in remaining)
        avg = cost / n
        detail = tuple((lot[0], lot[1], lot[2]) for lot in remaining)
        positions.append(Position(sym, n, avg, avg * n * SHARES_PER_LOT, detail))
    recs = tuple(r for r in (_reconcile(c) for c in closings) if r is not None)
    return Ledger(
        legs=tuple(legs), closings=tuple(closings), positions=tuple(positions),
        realised_pnl_idr=sum(c.pnl_idr for c in closings), round_trips_closed=len(closings),
        fees_idr=sum(g.fee for g in legs), reconciliations=recs)
