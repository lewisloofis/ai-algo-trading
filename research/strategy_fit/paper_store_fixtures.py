"""Test helper: write a minimal strategy-run post-trade store (the subset of
strategy/engine/store.go's schema that paper_stores reads)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

WIB = timezone(timedelta(hours=7))

_DDL = """
CREATE TABLE order_intent (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, strategy_id TEXT NOT NULL,
  symbol TEXT NOT NULL, side TEXT NOT NULL, price_idr INTEGER NOT NULL, lots INTEGER NOT NULL,
  order_id TEXT NOT NULL, sink_kind TEXT NOT NULL DEFAULT 'paper', run_id TEXT NOT NULL DEFAULT '');
CREATE TABLE fill (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, order_id TEXT NOT NULL,
  fill_price_idr INTEGER NOT NULL, fill_lots INTEGER NOT NULL, fee_idr REAL NOT NULL,
  slippage_bps REAL NOT NULL, net_pnl_idr INTEGER, sink_kind TEXT NOT NULL DEFAULT 'paper',
  run_id TEXT NOT NULL DEFAULT '');
"""


def ns(day: str, hhmm: str = "09:30") -> int:
    y, m, d = map(int, day.split("-"))
    h, mi = map(int, hhmm.split(":"))
    return int(datetime(y, m, d, h, mi, tzinfo=WIB).timestamp() * 1_000_000_000)


def write_store(path: Path, fills: list[tuple], sink: str = "paper") -> Path:
    """fills: (day 'YYYY-MM-DD', symbol, side, price_idr, lots). Fee = schedule (15/25 bps)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_DDL)
    run = f"run-{path.name}"
    for i, (day, sym, side, px, lots) in enumerate(fills, start=1):
        oid = f"o-{path.name}-{i}"
        t = ns(day)
        fee = round(px * lots * 100 * (0.0015 if side == "buy" else 0.0025), 2)
        con.execute("INSERT INTO order_intent VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (i, t, "x", sym, side, px, lots, oid, sink, run))
        con.execute("INSERT INTO fill VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (i, t, oid, px, lots, fee, 0.0, None, sink, run))
    con.commit()
    con.close()
    return path
