"""
strategy_fit/paper_stores.py — naming and read-only access for PAPER stores.

live-session names a store ``<runs>/<date>-<key>-aN.db`` where ``key`` is the
strategy id for a live sink and ``<strategy id>-paper`` for a paper sink
(strategy/livesession/stores.go StoreKey). Live and paper therefore never share
a glob: a live glob ``*-<id>-a[0-9]*.db`` cannot match ``<date>-<id>-paper-aN.db``.

The glob is livesession.attemptGlob's, verbatim (``*-<key>-a[0-9]*.db``).

This module only READS stores (sqlite ``mode=ro``): fills joined to their order
intent, the net lots a strategy's paper stores imply, and completed round trips.
"""

from __future__ import annotations

import glob
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

SINK_PAPER = "paper"
PAPER_KEY_SUFFIX = "-paper"
SHARES_PER_LOT = 100
_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def safe_name(s: str) -> str:
    """Mirror of livesession.SafeName."""
    s = _UNSAFE.sub("-", s.lower()).strip("-")
    return s or "strategy"


def store_key(strategy_id: str, sink: str = SINK_PAPER) -> str:
    """The name component after the date: id for live, id-paper for paper."""
    key = safe_name(strategy_id)
    return key + PAPER_KEY_SUFFIX if sink == SINK_PAPER else key


def store_glob(runs_dir: Path | str, strategy_id: str, sink: str = SINK_PAPER) -> str:
    """Every attempt store of this strategy and sink, on any date."""
    return str(Path(runs_dir) / f"*-{store_key(strategy_id, sink)}-a[0-9]*.db")


def list_stores(runs_dir: Path | str, strategy_id: str, sink: str = SINK_PAPER) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(store_glob(runs_dir, strategy_id, sink)))]


@dataclass(frozen=True)
class Fill:
    recv_ns: int
    symbol: str
    side: str          # "buy" | "sell"
    price_idr: int
    lots: int
    fee_idr: float
    store: str

    @property
    def shares(self) -> int:
        return self.lots * SHARES_PER_LOT


@dataclass(frozen=True)
class RoundTrip:
    symbol: str
    entry_ns: int
    exit_ns: int
    shares: int
    buy_notional_idr: float
    sell_notional_idr: float
    fees_idr: float        # as recorded by the sink (schedule fee on each leg)
    avg_buy_idr: float


_FILLS_SQL = """
SELECT f.recv_ns, oi.symbol, oi.side, f.fill_price_idr, f.fill_lots, f.fee_idr, f.seq
FROM fill f
JOIN order_intent oi ON oi.order_id = f.order_id AND oi.run_id = f.run_id
WHERE f.sink_kind = 'paper' AND oi.sink_kind = 'paper'
ORDER BY f.recv_ns, f.seq
"""


def read_fills(paths: Iterable[Path | str]) -> list[Fill]:
    """All paper fills in the given stores, oldest first. Read-only."""
    out: list[Fill] = []
    for p in paths:
        p = Path(p)
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            for recv_ns, symbol, side, px, lots, fee, _seq in con.execute(_FILLS_SQL):
                out.append(Fill(int(recv_ns), symbol, side, int(px), int(lots), float(fee), p.name))
        finally:
            con.close()
    out.sort(key=lambda f: f.recv_ns)
    return out


def net_lots(fills: Sequence[Fill]) -> dict[str, int]:
    """Net lots per symbol (buys minus sells); zero positions omitted."""
    pos: dict[str, int] = {}
    for f in fills:
        pos[f.symbol] = pos.get(f.symbol, 0) + (f.lots if f.side == "buy" else -f.lots)
    return {s: n for s, n in pos.items() if n != 0}


def paper_net_lots(runs_dir: Path | str, strategy_id: str) -> dict[str, int]:
    """Net paper lots implied by every paper store of this strategy."""
    return net_lots(read_fills(list_stores(runs_dir, strategy_id)))


def round_trips(fills: Sequence[Fill]) -> list[RoundTrip]:
    """Completed round trips: first buy of a flat symbol until its position returns to zero.

    An open (unexited) position is not a completed trip and is not returned.
    """
    trips: list[RoundTrip] = []
    state: dict[str, dict] = {}
    for f in sorted(fills, key=lambda x: x.recv_ns):
        st = state.get(f.symbol)
        if f.side == "buy":
            if st is None:
                st = state[f.symbol] = dict(entry=f.recv_ns, shares=0, buy=0.0, sell=0.0, fees=0.0, bought=0)
            st["shares"] += f.shares
            st["bought"] += f.shares
            st["buy"] += f.price_idr * f.shares
            st["fees"] += f.fee_idr
        elif st is not None:
            st["shares"] -= f.shares
            st["sell"] += f.price_idr * f.shares
            st["fees"] += f.fee_idr
            if st["shares"] <= 0:
                trips.append(RoundTrip(
                    symbol=f.symbol, entry_ns=st["entry"], exit_ns=f.recv_ns, shares=st["bought"],
                    buy_notional_idr=st["buy"], sell_notional_idr=st["sell"], fees_idr=st["fees"],
                    avg_buy_idr=st["buy"] / st["bought"],
                ))
                del state[f.symbol]
    return trips
