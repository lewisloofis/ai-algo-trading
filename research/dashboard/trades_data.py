"""
dashboard/trades_data.py — every order the bot placed, joined to its fills, in plain terms.

Rules that protect the numbers (each is pinned by a test):

* **Live and paper never mix.** Orders are split by the sink that handled them. Money
  totals (realised profit, fees, open positions) are computed from live orders only;
  paper orders are listed in their own table and never summed into anything.
* **Calibration traffic is dropped** (strategy_map.EXCLUDED_STRATEGY_IDS): orders placed to
  measure the market are not trading results.
* **An unmapped strategy_id fails the build** (strategy_map.require_all_mapped): an unknown
  id would otherwise be shown without saying what it is.
* Realised profit is the runtime's own ``fill.net_pnl_idr`` (set on the closing fill), not a
  number recomputed here. An order that closed nothing shows "—".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from dashboard.annotations import Annotation
from dashboard.health import wib
from dashboard.store import FillRow, RejectRow, StoreData
from dashboard.strategy_map import is_excluded, join_for, require_all_mapped

SHARES_PER_LOT = 100
"""IDX board lot: one lot is 100 shares."""

LIVE = "live"
PAPER = "paper"


@dataclass(frozen=True)
class OrderLine:
    when: datetime
    """Jakarta time the order was sent."""
    symbol: str
    side: str
    lots: int
    limit_price: int
    strategy_id: str
    strategy_label: str
    strategy_note: str
    run_id: str
    sink_kind: str
    filled_lots: int
    fill_price: float | None
    fees_idr: int
    realised_pnl_idr: int | None
    status: str
    """filled / partly filled / rejected / not filled."""
    reject_reason: str | None

    @property
    def filled(self) -> bool:
        return self.filled_lots > 0

    @property
    def value_idr(self) -> float:
        """Rupiah value of what actually changed hands."""
        return (self.fill_price or 0.0) * self.filled_lots * SHARES_PER_LOT


@dataclass(frozen=True)
class Position:
    symbol: str
    lots: int
    avg_cost: float
    cost_idr: float


@dataclass(frozen=True)
class LiveSummary:
    orders_sent: int
    orders_filled: int
    orders_not_filled: int
    realised_pnl_idr: int
    round_trips_closed: int
    fees_idr: int
    positions: tuple[Position, ...]


def plain_reason(reason: str) -> str:
    """A broker or risk-gate rejection, in words a person can act on."""
    low = reason.lower()
    if "insufficient trading balance" in low:
        return "The broker refused it: not enough cash available to trade."
    if "no book observed" in low:
        return "No price quote was available, so the practice bot could not simulate a fill."
    if "kill switch" in low:
        return "The emergency stop was switched on, so the bot refused to send it."
    if low.startswith("risk:"):
        return f"The bot's own safety limits blocked it ({reason[5:].strip()})."
    return f"Refused: {reason}"


def _status(filled: int, lots: int, rejected: bool) -> str:
    if rejected and filled == 0:
        return "rejected"
    if filled == 0:
        return "not filled"
    return "filled" if filled >= lots else "partly filled"


def _fills_by_order(fills: Sequence[FillRow]) -> dict[tuple[str, str, str], list[FillRow]]:
    out: dict[tuple[str, str, str], list[FillRow]] = {}
    for f in fills:
        out.setdefault((f.store_path, f.run_id, f.order_id), []).append(f)
    return out


def _rejects_by_order(rejects: Sequence[RejectRow]) -> dict[tuple[str, str, str], RejectRow]:
    return {(r.store_path, r.run_id, r.order_id): r for r in rejects}


def build_orders(data: StoreData, annotations: Mapping[str, Annotation]) -> list[OrderLine]:
    """One line per order intent, newest first. Raises UnmappedStrategyError for unknown ids."""
    intents = [i for i in data.intents if not is_excluded(i.strategy_id)]
    rejected_only = [r for r in data.rejects if not is_excluded(r.strategy_id)]
    require_all_mapped([i.strategy_id for i in intents] + [r.strategy_id for r in rejected_only])

    fills = _fills_by_order(data.fills)
    rejects = _rejects_by_order(rejected_only)
    lines: list[OrderLine] = []
    for i in intents:
        key = (i.store_path, i.run_id, i.order_id)
        mine = fills.get(key, [])
        filled_lots = sum(f.fill_lots for f in mine)
        notional = sum(f.fill_price_idr * f.fill_lots for f in mine)
        pnl = [f.net_pnl_idr for f in mine if f.net_pnl_idr is not None]
        reject = rejects.get(key)
        reason = plain_reason(reject.reason) if reject else None
        note = annotations.get(i.run_id)
        if reason is None and filled_lots == 0 and note and note.reject_reason:
            reason = plain_reason(note.reject_reason)
        join = join_for(i.strategy_id)
        lines.append(
            OrderLine(
                when=wib(i.recv_ns),
                symbol=i.symbol,
                side=i.side,
                lots=i.lots,
                limit_price=i.price_idr,
                strategy_id=i.strategy_id,
                strategy_label=join.label or i.strategy_id,
                strategy_note=join.note,
                run_id=i.run_id,
                sink_kind=i.sink_kind,
                filled_lots=filled_lots,
                fill_price=(notional / filled_lots) if filled_lots else None,
                fees_idr=sum(f.fee_idr for f in mine),
                realised_pnl_idr=sum(pnl) if pnl else None,
                status=_status(filled_lots, i.lots, reason is not None),
                reject_reason=reason,
            )
        )
    seen = {(i.store_path, i.run_id, i.order_id) for i in intents}
    for key, r in rejects.items():
        if key in seen:
            continue  # normal case: the intent line already carries this rejection
        join = join_for(r.strategy_id)
        lines.append(
            OrderLine(
                when=wib(r.recv_ns), symbol=r.symbol, side=r.side, lots=r.lots, limit_price=r.price_idr,
                strategy_id=r.strategy_id, strategy_label=join.label or r.strategy_id,
                strategy_note=join.note, run_id=r.run_id, sink_kind=r.sink_kind, filled_lots=0,
                fill_price=None, fees_idr=0, realised_pnl_idr=None, status="rejected",
                reject_reason=plain_reason(r.reason),
            )
        )
    return sorted(lines, key=lambda x: x.when, reverse=True)


def split_by_sink(lines: Sequence[OrderLine]) -> tuple[list[OrderLine], list[OrderLine]]:
    """(live, everything else). Money is only ever totalled over the first list."""
    live = [x for x in lines if x.sink_kind == LIVE]
    other = [x for x in lines if x.sink_kind != LIVE]
    return live, other


def open_positions(live: Sequence[OrderLine]) -> tuple[Position, ...]:
    """Net lots still held per stock, from live fills only, with their average buy price."""
    lots: dict[str, int] = {}
    bought_lots: dict[str, int] = {}
    bought_value: dict[str, float] = {}
    for x in live:
        if not x.filled:
            continue
        sign = 1 if x.side == "buy" else -1
        lots[x.symbol] = lots.get(x.symbol, 0) + sign * x.filled_lots
        if sign > 0:
            bought_lots[x.symbol] = bought_lots.get(x.symbol, 0) + x.filled_lots
            bought_value[x.symbol] = bought_value.get(x.symbol, 0.0) + (x.fill_price or 0.0) * x.filled_lots
    out = []
    for sym, n in sorted(lots.items()):
        if n <= 0:
            continue
        avg = bought_value[sym] / bought_lots[sym]
        out.append(Position(sym, n, avg, avg * n * SHARES_PER_LOT))
    return tuple(out)


def live_summary(live: Sequence[OrderLine]) -> LiveSummary:
    """Totals over LIVE orders only. Pass paper lines in and the numbers are wrong by design."""
    if any(x.sink_kind != LIVE for x in live):
        raise ValueError("live_summary was given non-live orders; live and paper must never be summed")
    closing = [x.realised_pnl_idr for x in live if x.realised_pnl_idr is not None]
    return LiveSummary(
        orders_sent=len(live),
        orders_filled=sum(1 for x in live if x.filled),
        orders_not_filled=sum(1 for x in live if not x.filled),
        realised_pnl_idr=sum(closing),
        round_trips_closed=len(closing),
        fees_idr=sum(x.fees_idr for x in live),
        positions=open_positions(live),
    )
