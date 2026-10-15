"""Test helpers for the cost report: synthetic post-trade stores, recon files and daily bars.

Everything here is invented (symbols AAAA/BBBB, account label ``TEST001``); nothing is read from runs/.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from strategy_fit.paper_store_fixtures import _DDL, ns


def write_cost_store(path: Path, rows: list[dict], rejects: int = 0, intents_only: int = 0) -> Path:
    """rows: day, symbol, side, decision, fill, lots, fee, sink ('live'|'paper'), order_id?, hhmm?, strategy?,
    arrival? ({mid,bid,ask,last,age_ms}; any row with it gives the store the arrival_* columns, else old schema)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(_DDL)
    with_arrival = any("arrival" in r for r in rows)
    if with_arrival:
        for col, typ in (("arrival_bid_idr", "INTEGER"), ("arrival_ask_idr", "INTEGER"), ("arrival_mid_idr", "REAL"),
                         ("arrival_last_idr", "INTEGER"), ("arrival_quote_ns", "INTEGER"),
                         ("arrival_quote_age_ms", "INTEGER")):
            con.execute(f"ALTER TABLE order_intent ADD COLUMN {col} {typ}")
    con.execute("CREATE TABLE order_reject (seq INTEGER PRIMARY KEY, recv_ns INTEGER, run_id TEXT, strategy_id TEXT,"
                " symbol TEXT, side TEXT, price_idr INTEGER, lots INTEGER, order_id TEXT, reason TEXT, sink_kind TEXT)")
    seq = 0
    for r in rows:
        seq += 1
        sink = r.get("sink", "live")
        run = f"run-{path.name}"
        oid = r.get("order_id", f"o-{path.name}-{seq}")
        t = ns(r["day"], r.get("hhmm", "09:30"))
        con.execute("INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id,"
                    " sink_kind, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (seq, t, r.get("strategy", "strat1"), r["symbol"], r["side"], r["decision"], r["lots"], oid, sink, run))
        a = r.get("arrival")
        if a:
            con.execute("UPDATE order_intent SET arrival_bid_idr=?, arrival_ask_idr=?, arrival_mid_idr=?,"
                        " arrival_last_idr=?, arrival_quote_age_ms=? WHERE seq=?",
                        (a.get("bid"), a.get("ask"), a.get("mid"), a.get("last"), a.get("age_ms"), seq))
        con.execute("INSERT INTO fill VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (seq, t + 1_000_000, oid, r["fill"], r["lots"], r["fee"], 0.0, None, sink, run))
    for i in range(intents_only):
        seq += 1
        con.execute("INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id,"
                    " sink_kind, run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (seq, ns("2026-01-05"), "strat1", "AAAA", "buy", 1000, 1, f"unfilled-{i}", "live", "r"))
    for i in range(rejects):
        con.execute("INSERT INTO order_reject VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (i + 1, ns("2026-01-05"), "r", "strat1", "AAAA", "buy", 1000, 1, f"rej-{i}", "limit", "live"))
    con.commit()
    con.close()
    return path


def write_recon(runs_dir: Path, name: str, matched: list[dict], broker_only: list[dict]) -> Path:
    d = runs_dir / "recon"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "date": name, "account_label": "TEST001", "status": "match", "positions": [],
           "trade_check": {"checked": True, "matched_trades": matched, "broker_only_trades": broker_only,
                           "pending_history_trades": []}}
    p = d / f"{name}.json"
    p.write_text(json.dumps(doc))
    return p


def write_broker_trades(runs_dir: Path, trades: list[dict]) -> Path:
    p = runs_dir / "broker_trades.json"
    p.write_text(json.dumps({"_schema": "synthetic", "trades": trades}))
    return p


def write_daily(data_dir: Path, symbol: str, n: int = 80, close: float = 1000.0, value: float = 50e9,
                end: str = "2026-01-30") -> Path:
    """n business-day bars ending at ``end``; alternating +/-1% closes so vol > 0."""
    data_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=end, periods=n)
    closes = [close * (1.01 if i % 2 else 0.99) for i in range(n)]
    df = pd.DataFrame({"date": [d.strftime("%Y-%m-%d") for d in dates], "close": closes,
                       "volume": [value / close] * n, "value": [value] * n})
    p = data_dir / f"{symbol}.parquet"
    df.to_parquet(p)
    return p
