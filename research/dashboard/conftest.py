"""Shared fixtures for the dashboard tests."""
from __future__ import annotations

import os
import re
import sqlite3

import pytest

from strategy_fit.harness import (
    CellResult,
    SHAPE_INTRADAY_SAME_DAY,
    SHAPE_SINGLE_NAME,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE_GO = os.path.join(REPO_ROOT, "strategy", "engine", "store.go")


def go_schema_sql() -> str:
    """
    The post-trade schema, read out of strategy/engine/store.go.

    Extracting the DDL rather than restating it means a schema change in Go
    flows straight into these tests: a column the dashboard reads that Go stops
    writing fails here, instead of at render time against a real store.
    """
    src = open(STORE_GO, encoding="utf-8").read()
    m = re.search(r"const schemaSQL = `(.*?)`", src, re.DOTALL)
    assert m, "schemaSQL not found in store.go — the extraction regex needs updating"
    return m.group(1)


@pytest.fixture
def make_store(tmp_path):
    """
    Build a post-trade store file with the real Go schema and seed it.

    Returns a callable: ``make_store(name, runs=[...], decisions=[...], ...)``.
    """

    def _make(
        name: str = "run.db",
        runs=(),
        decisions=(),
        intents=(),
        fills=(),
        attributions=(),
        rejects=(),
        schema: str | None = None,
    ) -> str:
        path = str(tmp_path / name)
        conn = sqlite3.connect(path)
        conn.executescript(schema if schema is not None else go_schema_sql())
        for r in runs:
            conn.execute(
                "INSERT INTO run (run_id, started_ns, ended_ns, outcome, error, source, pacing,"
                " fill_mode, sink_kind, strategy_id, input_spec, binary_hash, orders_attempted,"
                " fills, total_fees_idr, round_trips, net_pnl_bps)"
                " VALUES (:run_id, :started_ns, :ended_ns, :outcome, :error, :source, :pacing,"
                " :fill_mode, :sink_kind, :strategy_id, :input_spec, :binary_hash,"
                " :orders_attempted, :fills, :total_fees_idr, :round_trips, :net_pnl_bps)",
                {
                    "run_id": "r1", "started_ns": 1, "ended_ns": 2, "outcome": "complete",
                    "error": "", "source": "replay", "pacing": "fast", "fill_mode": "taker",
                    "sink_kind": "paper", "strategy_id": "orb", "input_spec": "segments/*",
                    "binary_hash": "abc123", "orders_attempted": 0, "fills": 0,
                    "total_fees_idr": 0, "round_trips": 0, "net_pnl_bps": 0.0, **r,
                },
            )
        for d in decisions:
            conn.execute(
                "INSERT INTO decision (seq, recv_ns, strategy_id, symbol, event_kind, signal_value, run_id)"
                " VALUES (:seq, :recv_ns, :strategy_id, :symbol, :event_kind, :signal_value, :run_id)",
                {"recv_ns": 1, "strategy_id": "orb", "symbol": "AAA",
                 "event_kind": "liveprice", "signal_value": None, "run_id": "r1", **d},
            )
        for i in intents:
            conn.execute(
                "INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr,"
                " lots, order_id, sink_kind, run_id)"
                " VALUES (:seq, :recv_ns, :strategy_id, :symbol, :side, :price_idr, :lots,"
                " :order_id, :sink_kind, :run_id)",
                {"recv_ns": 1, "strategy_id": "orb", "symbol": "AAA", "side": "buy",
                 "price_idr": 1000, "lots": 1, "sink_kind": "paper", "run_id": "r1", **i},
            )
        for f in fills:
            conn.execute(
                "INSERT INTO fill (seq, recv_ns, order_id, fill_price_idr, fill_lots, fee_idr,"
                " slippage_bps, net_pnl_idr, sink_kind, run_id)"
                " VALUES (:seq, :recv_ns, :order_id, :fill_price_idr, :fill_lots, :fee_idr,"
                " :slippage_bps, :net_pnl_idr, :sink_kind, :run_id)",
                {"recv_ns": 1, "fill_price_idr": 1000, "fill_lots": 1, "fee_idr": 1,
                 "slippage_bps": 0.0, "net_pnl_idr": None, "sink_kind": "paper",
                 "run_id": "r1", **f},
            )
        for j in rejects:
            conn.execute(
                "INSERT INTO order_reject (seq, recv_ns, run_id, strategy_id, symbol, side,"
                " price_idr, lots, order_id, reason, sink_kind)"
                " VALUES (:seq, :recv_ns, :run_id, :strategy_id, :symbol, :side, :price_idr,"
                " :lots, :order_id, :reason, :sink_kind)",
                {"recv_ns": 1, "run_id": "r1", "strategy_id": "orb", "symbol": "AAA",
                 "side": "buy", "price_idr": 1000, "lots": 1, "order_id": "o1",
                 "reason": "risk: cap exceeded", "sink_kind": "paper", **j},
            )
        for a in attributions:
            conn.execute(
                "INSERT INTO attribution (seq, symbol, strategy_id, entry_recv_ns, exit_recv_ns,"
                " gross_pnl_bps, net_pnl_bps, entry_order_id, exit_order_id, hold_calendar_days,"
                " run_id, as_of_date)"
                " VALUES (:seq, :symbol, :strategy_id, :entry_recv_ns, :exit_recv_ns,"
                " :gross_pnl_bps, :net_pnl_bps, :entry_order_id, :exit_order_id,"
                " :hold_calendar_days, :run_id, :as_of_date)",
                {"symbol": "AAA", "strategy_id": "orb", "entry_recv_ns": 1, "exit_recv_ns": 2,
                 "gross_pnl_bps": 0.0, "net_pnl_bps": 0.0, "entry_order_id": "o1",
                 "exit_order_id": "o2", "hold_calendar_days": 0.0, "run_id": "r1",
                 "as_of_date": "2026-09-16", **a},
            )
        conn.commit()
        conn.close()
        return path

    return _make


def make_tested_cell(**overrides) -> CellResult:
    """A cell that clears every gate unless an override breaks one."""
    base = dict(
        symbol="ITMG",
        family_name="intraday_momentum",
        shape=SHAPE_INTRADAY_SAME_DAY,
        horizon_trading_days=0,
        status="tested",
        hit_rate=0.62,
        eff_n=60,
        gross_edge_bps=120.0,
        net_edge_bps=40.0,
        p_value=0.002,
        passes_cost_gate=True,
        min_edge_hurdle_bps=80.0,
        fdr_accepted=True,
        modelled_impact_bps=15.0,
        impact_priced=True,
        impact_priced_coeff=0.3,
        impact_priced_position_notional_idr=3_000_000.0,
        oos_eff_n=25,
        null_edge_bps=20.0,
        edge_over_null_bps=100.0,
    )
    base.update(overrides)
    return CellResult(**base)


def make_blocked_cell(**overrides) -> CellResult:
    # family_name is a RUNNER family name (run_strategy_fit's vocabulary), not
    # a catalog family_id — the two differ, which is what dashboard/catalog_map
    # exists to join. Using a catalog id here would make these fixtures test a
    # mapping that never happens in a real run.
    base = dict(
        symbol="ANTM",
        family_name="mean_reversion",
        shape=SHAPE_SINGLE_NAME,
        horizon_trading_days=20,
        status="data_blocked",
        skip_reason="needs 3+ yrs history",
    )
    base.update(overrides)
    return CellResult(**base)
