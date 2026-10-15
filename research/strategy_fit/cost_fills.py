"""
strategy_fit/cost_fills.py — every fill (live and paper) as one flat record, for the B6 cost report.

Read-only. Sources, all local:

* post-trade stores ``runs/*.db`` (sqlite ``mode=ro``): fill joined to its order intent, so each fill carries
  the decision (limit/target) price the bot sent. The stores hold no book snapshot, so ``arrival_mid_idr`` is
  always None today: slippage against the arrival mid cannot be measured from them (plan 18 B6 gap).
* ``runs/recon/*.json`` (written by ``account-recon --stores``): the broker's own fee for each matched fill,
  and trades the broker shows that no bot fill explains.
* ``runs/broker_trades.json``: the same broker-only trades, imported idempotently by broker id.

The broker fee is the truth where present; otherwise the store's own fee is used and marked ``fee_source=store``.
No token, account number or network access is involved: recon files carry only a short account label.
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

WIB = timezone(timedelta(hours=7))
SHARES_PER_LOT = 100
SOURCE_LIVE = "live"
SOURCE_PAPER = "paper"
SOURCES = (SOURCE_LIVE, SOURCE_PAPER)

# Mislabelled store the dashboard also refuses to read (dashboard/config.py EXCLUDED_STORES).
EXCLUDED_STORES: frozenset = frozenset({"2026-10-14-limitprobe.db"})


@dataclass(frozen=True)
class CostFill:
    source: str                       # "live" | "paper"
    origin: str                       # "store" | "broker_import"
    strategy_id: str
    order_id: str
    symbol: str
    side: str                         # "buy" | "sell"
    day: str                          # Jakarta date of the fill
    time_ns: int
    lots: int
    fill_price_idr: float
    decision_price_idr: Optional[float]
    arrival_mid_idr: Optional[float]
    store_fee_idr: Optional[float]
    broker_fee_idr: Optional[float]
    fee_source: str                   # "broker" | "store"
    store: str

    @property
    def shares(self) -> int:
        return self.lots * SHARES_PER_LOT

    @property
    def notional_idr(self) -> float:
        return self.fill_price_idr * self.shares

    @property
    def fee_idr(self) -> float:
        if self.broker_fee_idr is not None:
            return float(self.broker_fee_idr)
        return float(self.store_fee_idr or 0.0)


def _day(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, WIB).strftime("%Y-%m-%d")


def _empty_cov() -> dict:
    return {s: {"intents": 0, "fills": 0, "rejects": 0} for s in SOURCES}


_FILLS_SQL = """
SELECT f.sink_kind, oi.strategy_id, oi.order_id, oi.symbol, oi.side, oi.price_idr,
       f.fill_price_idr, f.fill_lots, f.fee_idr, f.recv_ns, f.seq, f.run_id
FROM fill f JOIN order_intent oi ON oi.order_id = f.order_id AND oi.run_id = f.run_id
ORDER BY f.recv_ns, f.seq
"""


def _count_by_sink(con: sqlite3.Connection, table: str) -> dict:
    try:
        return {k: int(n) for k, n in con.execute(f"SELECT sink_kind, COUNT(*) FROM {table} GROUP BY sink_kind")}
    except sqlite3.Error:
        return {}


def _read_one(path: Path, seen: set, cov: dict) -> list:
    out = []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for sink, strat, oid, sym, side, dec, px, lots, fee, ns, seq, run in con.execute(_FILLS_SQL):
            if sink not in SOURCES or (sink, run, oid, seq) in seen:
                continue
            seen.add((sink, run, oid, seq))
            out.append(CostFill(
                source=sink, origin="store", strategy_id=strat, order_id=oid, symbol=sym, side=side, day=_day(ns),
                time_ns=int(ns), lots=int(lots), fill_price_idr=float(px),
                decision_price_idr=float(dec) if dec else None, arrival_mid_idr=None,
                store_fee_idr=float(fee), broker_fee_idr=None, fee_source="store", store=path.name))
        for key, field in (("intents", "order_intent"), ("rejects", "order_reject")):
            for sink, n in _count_by_sink(con, field).items():
                if sink in cov:
                    cov[sink][key] += n
    finally:
        con.close()
    return out


def load_store_fills(runs_dir, exclude: Iterable[str] = EXCLUDED_STORES) -> tuple:
    """(fills oldest first, coverage). Unreadable and excluded stores are listed, never silently dropped."""
    skip = frozenset(exclude)
    cov = _empty_cov()
    cov.update(unreadable_stores=[], excluded_stores=[], stores_read=0)
    fills: list = []
    seen: set = set()
    for p in sorted(glob.glob(os.path.join(str(runs_dir), "*.db"))):
        path = Path(p)
        if path.name in skip:
            cov["excluded_stores"].append(path.name)
            continue
        try:
            fills.extend(_read_one(path, seen, cov))
            cov["stores_read"] += 1
        except sqlite3.Error:
            cov["unreadable_stores"].append(path.name)
    for sink in SOURCES:
        cov[sink]["fills"] = sum(1 for f in fills if f.source == sink)
    fills.sort(key=lambda f: f.time_ns)
    return fills, cov


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _broker_fill(t: dict) -> Optional[CostFill]:
    try:
        hh, mm, ss = (int(x) for x in str(t.get("time_wib") or "23:59:59").split(":"))
        y, mo, d = (int(x) for x in str(t["date"]).split("-"))
        ns = int(datetime(y, mo, d, hh, mm, ss, tzinfo=WIB).timestamp() * 1e9)
        return CostFill(
            source=SOURCE_LIVE, origin="broker_import", strategy_id="(broker import)", order_id=f"broker:{t['broker_id']}",
            symbol=t["symbol"], side=t["side"], day=str(t["date"]), time_ns=ns, lots=int(t["lots"]),
            fill_price_idr=float(t["price_idr"]), decision_price_idr=None, arrival_mid_idr=None,
            store_fee_idr=None, broker_fee_idr=float(t["fee_idr"]), fee_source="broker", store="broker_trades")
    except (KeyError, ValueError, TypeError):
        return None


def attach_broker_data(runs_dir, fills: list) -> tuple:
    """Overlay broker fees from recon onto store fills, and add broker-only trades as live fills."""
    runs = str(runs_dir)
    recon_paths = sorted(glob.glob(os.path.join(runs, "recon", "*.json")))
    fee_by_order: dict = {}
    matched_ids: set = set()
    broker_only: dict = {}
    for p in recon_paths:
        tc = (_load_json(p) or {}).get("trade_check") or {}
        for m in tc.get("matched_trades") or []:
            fee_by_order[m.get("store_order_id")] = float(m["broker_fee_idr"])
            matched_ids.add(str(m.get("broker_id")))
        for t in tc.get("broker_only_trades") or []:
            broker_only[str(t.get("broker_id"))] = t
    imports = _load_json(os.path.join(runs, "broker_trades.json")) or {}
    for t in imports.get("trades") or []:
        broker_only.setdefault(str(t.get("broker_id")), t)

    merged = []
    n_fee = 0
    for f in fills:
        fee = fee_by_order.get(f.order_id) if f.source == SOURCE_LIVE else None
        if fee is not None:
            n_fee += 1
            f = replace(f, broker_fee_idr=fee, fee_source="broker")
        merged.append(f)
    added = 0
    for bid, t in broker_only.items():
        if bid in matched_ids:
            continue
        bf = _broker_fill(dict(t, broker_id=bid))
        if bf is not None:
            merged.append(bf)
            added += 1
    merged.sort(key=lambda f: f.time_ns)
    return merged, {"recon_files": len(recon_paths), "fills_with_broker_fee": n_fee, "broker_only_fills": added}
