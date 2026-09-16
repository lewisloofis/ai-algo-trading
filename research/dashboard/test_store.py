"""
Store-reader tests.

The schema these run against is extracted from strategy/engine/store.go
(conftest.go_schema_sql), not restated here — so a Go schema change that breaks
the dashboard fails in this suite rather than at render time against a real
938 MB store.
"""
from __future__ import annotations

import pytest

from dashboard import store
from dashboard.conftest import go_schema_sql

LEGACY_SCHEMA = """
CREATE TABLE decision (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL, event_kind TEXT NOT NULL, signal_value REAL);
CREATE TABLE order_intent (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL, side TEXT NOT NULL, price_idr INTEGER NOT NULL, lots INTEGER NOT NULL,
    order_id TEXT NOT NULL, sink_kind TEXT NOT NULL DEFAULT 'paper');
CREATE TABLE fill (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, order_id TEXT NOT NULL,
    fill_price_idr INTEGER NOT NULL, fill_lots INTEGER NOT NULL, fee_idr INTEGER NOT NULL,
    slippage_bps REAL NOT NULL, net_pnl_idr INTEGER, sink_kind TEXT NOT NULL DEFAULT 'paper');
CREATE TABLE attribution (seq INTEGER PRIMARY KEY, symbol TEXT NOT NULL, strategy_id TEXT NOT NULL,
    entry_recv_ns INTEGER NOT NULL, exit_recv_ns INTEGER NOT NULL, gross_pnl_bps REAL NOT NULL,
    net_pnl_bps REAL NOT NULL, entry_order_id TEXT NOT NULL, exit_order_id TEXT NOT NULL,
    hold_calendar_days REAL NOT NULL);
"""


def test_schema_extraction_finds_every_table():
    sql = go_schema_sql()
    for table in ("decision", "order_intent", "fill", "attribution", "run"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_reads_runs_and_attributions(make_store):
    path = make_store(
        runs=[{"run_id": "r1", "round_trips": 2, "net_pnl_bps": -1.5}],
        intents=[{"seq": 1, "order_id": "o1"}, {"seq": 2, "order_id": "o2"}],
        attributions=[{"seq": 3, "net_pnl_bps": -1.5, "entry_order_id": "o1"}],
    )
    data = store.read_store(path)
    assert len(data.runs) == 1
    assert data.runs[0].run_id == "r1"
    assert data.runs[0].round_trips == 2
    assert len(data.attributions) == 1
    assert data.attributions[0].run_id == "r1"
    assert data.attributions[0].as_of_date == "2026-09-16"


def test_sink_kind_comes_from_the_intent(make_store):
    """A PnL row's provenance is the provenance of the order that made it."""
    path = make_store(
        intents=[{"seq": 1, "order_id": "o1", "sink_kind": "live"}],
        attributions=[{"seq": 2, "entry_order_id": "o1"}],
    )
    assert store.read_store(path).attributions[0].sink_kind == "live"


def test_an_orphan_attribution_is_marked_unknown_not_paper(make_store):
    """Guessing 'paper' would put a possibly-live round-trip in a replay total."""
    path = make_store(attributions=[{"seq": 1, "entry_order_id": "missing"}])
    assert store.read_store(path).attributions[0].sink_kind == "unknown"


def test_a_crashed_run_is_visible_as_crashed(make_store):
    path = make_store(runs=[{"run_id": "r1", "outcome": "running", "ended_ns": None}])
    assert store.read_store(path).runs[0].crashed is True


def test_a_completed_run_is_not_crashed(make_store):
    path = make_store(runs=[{"run_id": "r1", "outcome": "complete"}])
    assert store.read_store(path).runs[0].crashed is False


def test_legacy_store_without_run_id_still_reads(make_store):
    """The 938 MB W2.4 replay store predates run_id and must not be unreadable."""
    path = make_store(
        schema=LEGACY_SCHEMA,
        attributions=[],
    )
    data = store.read_store(path)
    assert data.runs == []


def test_legacy_rows_are_labelled_not_attributed(make_store, tmp_path):
    import sqlite3

    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO attribution (seq, symbol, strategy_id, entry_recv_ns, exit_recv_ns,"
        " gross_pnl_bps, net_pnl_bps, entry_order_id, exit_order_id, hold_calendar_days)"
        " VALUES (1, 'AAA', 'orb', 1, 2, 5.0, 3.0, 'o1', 'o2', 0.0)"
    )
    conn.commit()
    conn.close()

    row = store.read_store(path).attributions[0]
    assert row.run_id == store.LEGACY_RUN_ID
    assert row.as_of_date == ""
    assert store.run_label(row.run_id) == store.LEGACY_RUN_LABEL


def test_decision_funnel_counts_each_drop_off(make_store):
    path = make_store(
        decisions=[
            {"seq": 1, "signal_value": 1.0},
            {"seq": 2, "signal_value": 2.0},
            {"seq": 3, "signal_value": None},
        ],
        intents=[{"seq": 4, "order_id": "o1"}, {"seq": 5, "order_id": "o2"}],
        fills=[{"seq": 6, "order_id": "o1"}],
    )
    funnels = store.read_store(path).funnels
    assert len(funnels) == 1
    f = funnels[0]
    assert (f.decisions, f.signals, f.intents, f.fills) == (3, 2, 2, 1)
    assert f.signals_not_submitted == 0
    assert f.intents_unfilled == 1


def test_read_stores_unions_several_files(make_store):
    a = make_store("a.db", runs=[{"run_id": "r1"}], attributions=[{"seq": 1, "run_id": "r1"}])
    b = make_store("b.db", runs=[{"run_id": "r2"}], attributions=[{"seq": 1, "run_id": "r2"}])
    data = store.read_stores([a, b])
    assert {r.run_id for r in data.runs} == {"r1", "r2"}
    assert {a.run_id for a in data.attributions} == {"r1", "r2"}


def test_an_unreadable_store_is_recorded_not_swallowed(make_store, tmp_path):
    """One corrupt file must not silently shrink every total on the page."""
    good = make_store("good.db", attributions=[{"seq": 1}])
    bad = tmp_path / "bad.db"
    bad.write_text("this is not a database")
    data = store.read_stores([good, str(bad)])
    assert len(data.attributions) == 1
    assert len(data.errors) == 1
    assert "bad.db" in data.errors[0]


def test_a_missing_path_yields_nothing_and_no_error():
    data = store.read_stores(["/nonexistent/*.db"])
    assert data.is_empty
    assert data.errors == []


def test_resolve_paths_expands_a_directory(make_store, tmp_path):
    make_store("one.db")
    make_store("two.db")
    assert len(store.resolve_paths([str(tmp_path)])) == 2


def test_the_store_is_opened_read_only(make_store):
    """The dashboard has no business writing to the runtime's record."""
    import sqlite3

    path = make_store()
    conn = store._connect(path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO decision (seq, recv_ns, strategy_id, symbol, event_kind)"
                     " VALUES (99, 1, 'x', 'Y', 'z')")
    conn.close()
