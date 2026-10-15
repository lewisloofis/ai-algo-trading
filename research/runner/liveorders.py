"""Read-only view of a strategy's LIVE order events, from live-session's own stores.

live-session / strategy-run write ``runs/<date>-<id>-aN.db`` (sink_kind 'live') and a status file
beside it. This module only reads them (sqlite ``mode=ro``) and turns rows into short alert lines
(submitted / partial / filled / rejected / cancelled) and the end-of-day summary. It never writes,
never calls the broker, and never touches an account number: the stores' rows hold symbols, lots,
prices and a broker reject reason, nothing else.

A store has no 'cancelled' row. live-session cancels what is still resting when it stops, so an
order with no (full) fill and no reject once the session has ended is reported as ended
unfilled/cancelled, with a pointer to confirm it in the app."""

from __future__ import annotations

import glob
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from strategy_fit.paper_stores import SHARES_PER_LOT, store_key

SINK_LIVE = "live"
MAX_REASON = 160


def live_store_glob(runs_dir: Path, sid: str, day: Optional[date] = None) -> str:
    return str(Path(runs_dir) / f"{day if day else '*'}-{store_key(sid, SINK_LIVE)}-a[0-9]*.db")


def live_stores(runs_dir: Path, sid: str, day: Optional[date] = None) -> List[Path]:
    return [Path(p) for p in sorted(glob.glob(live_store_glob(runs_dir, sid, day)))]


@dataclass(frozen=True)
class OrderState:
    key: str                  # <store>:<order_id>, unique across stores
    order_id: str
    symbol: str
    side: str                 # buy | sell
    price_idr: int
    lots: int
    filled_lots: int
    fill_value_idr: float     # sum of price * shares over this order's fills
    fee_idr: float
    reject_reason: Optional[str]

    @property
    def notional_idr(self) -> int:
        return self.price_idr * self.lots * SHARES_PER_LOT

    @property
    def avg_fill_idr(self) -> float:
        return self.fill_value_idr / (self.filled_lots * SHARES_PER_LOT) if self.filled_lots else 0.0

    @property
    def rejected(self) -> bool:
        return self.reject_reason is not None

    @property
    def fully_filled(self) -> bool:
        return self.filled_lots >= self.lots


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def read_orders(paths: Iterable[Path]) -> List[OrderState]:
    """Every live order in the given stores, oldest first. Raises sqlite3.Error if a store is unreadable."""
    out: List[OrderState] = []
    for path in paths:
        con = _ro(Path(path))
        try:
            intents = con.execute(
                "SELECT order_id, run_id, symbol, side, price_idr, lots FROM order_intent "
                "WHERE sink_kind = ? ORDER BY seq", (SINK_LIVE,)).fetchall()
            fills: Dict[Tuple[str, str], List[Tuple[int, int, float]]] = {}
            for oid, rid, px, lots, fee in con.execute(
                    "SELECT order_id, run_id, fill_price_idr, fill_lots, fee_idr FROM fill WHERE sink_kind = ?",
                    (SINK_LIVE,)):
                fills.setdefault((oid, rid), []).append((int(px), int(lots), float(fee)))
            rejects = {(oid, rid): reason for oid, rid, reason in con.execute(
                "SELECT order_id, run_id, reason FROM order_reject WHERE sink_kind = ?", (SINK_LIVE,))}
        finally:
            con.close()
        for oid, rid, symbol, side, px, lots in intents:
            fl = fills.get((oid, rid), [])
            out.append(OrderState(
                key=f"{Path(path).name}:{oid}", order_id=oid, symbol=symbol, side=side, price_idr=int(px),
                lots=int(lots), filled_lots=sum(f[1] for f in fl),
                fill_value_idr=sum(f[0] * f[1] * SHARES_PER_LOT for f in fl), fee_idr=sum(f[2] for f in fl),
                reject_reason=rejects.get((oid, rid))))
    return out


def live_net_lots(runs_dir: Path, sid: str) -> Dict[str, int]:
    """Net live lots per symbol across EVERY day's stores of this strategy (a multi-day hold)."""
    pos: Dict[str, int] = {}
    for o in read_orders(live_stores(runs_dir, sid)):
        pos[o.symbol] = pos.get(o.symbol, 0) + (o.filled_lots if o.side == "buy" else -o.filled_lots)
    return {s: n for s, n in pos.items() if n != 0}


def bought_idr(orders: Sequence[OrderState]) -> int:
    """Notional of the day's buy orders that were not rejected (what the cap counts)."""
    return sum(o.notional_idr for o in orders if o.side == "buy" and not o.rejected)


def _fmt(o: OrderState) -> str:
    lot = "lot" if o.lots == 1 else "lots"
    return f"{o.side.upper()} {o.lots} {lot} {o.symbol} @ {o.price_idr:,}"


def _clean(reason: str) -> str:
    one_line = " ".join(str(reason).split())
    return one_line[:MAX_REASON] + ("..." if len(one_line) > MAX_REASON else "")


def new_events(orders: Sequence[OrderState], seen: Dict[str, dict], final: bool) -> Tuple[List[str], Dict[str, dict]]:
    """(alert lines for what is new since ``seen``, the updated ``seen``). ``final``: the session has
    ended, so an order that is neither fully filled nor rejected is reported as ended/cancelled."""
    lines: List[str] = []
    nxt = {k: dict(v) for k, v in seen.items()}
    for o in orders:
        s = nxt.setdefault(o.key, {"sub": False, "fl": 0, "rej": False, "end": False})
        if o.rejected:
            if not s["rej"]:
                lines.append(f"LIVE ORDER rejected: {_fmt(o)}: {_clean(o.reject_reason or '')}")
                s["rej"] = s["sub"] = True
            continue
        if not s["sub"]:
            lines.append(f"LIVE ORDER submitted: {_fmt(o)} ({o.notional_idr:,} IDR)")
            s["sub"] = True
        if o.filled_lots > s["fl"]:
            kind = "filled" if o.fully_filled else "partial"
            lines.append(f"LIVE ORDER {kind}: {o.side.upper()} {o.filled_lots}/{o.lots} lots {o.symbol} "
                         f"@ avg {o.avg_fill_idr:,.0f} (fees so far {o.fee_idr:,.0f} IDR)")
            s["fl"] = o.filled_lots
        if final and not o.fully_filled and not s["end"]:
            how = ("without a fill" if o.filled_lots == 0 else f"partially filled {o.filled_lots}/{o.lots} lots")
            lines.append(f"LIVE ORDER cancelled: {_fmt(o)} ended {how}; live-session cancels what is still resting "
                         "when it stops. Confirm in the Stockbit app.")
            s["end"] = True
    return lines, nxt


def unresolved(orders: Sequence[OrderState]) -> List[OrderState]:
    """Orders neither fully filled nor rejected: what to check by hand after a crash."""
    return [o for o in orders if not o.rejected and not o.fully_filled]


def read_status(runs_dir: Path, day: date, sid: str) -> Optional[dict]:
    """The newest live status file's risk snapshot, if any (bought vs cap, kill switch)."""
    pat = str(Path(runs_dir) / f"{day}-{store_key(sid, SINK_LIVE)}-a[0-9]*.status.json")
    files = sorted(glob.glob(pat), key=lambda p: Path(p).stat().st_mtime)
    for path in reversed(files):
        try:
            doc = json.loads(Path(path).read_text())
            if isinstance(doc, dict):
                return doc
        except (OSError, ValueError):
            continue
    return None
