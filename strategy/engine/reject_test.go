package engine

import (
	"testing"
	"time"
)

// TestInsertOrderReject_AppendsAndReads pins the schema and the insert path
// in isolation, before engine.go's SubmitOrder is taught to call it on a
// rejected intent (see TestSubmitOrder_RejectedIntentIsRecorded in
// engine_test.go). RED first: RejectRow/InsertOrderReject do not exist yet.
func TestInsertOrderReject_AppendsAndReads(t *testing.T) {
	store := newTestStore(t)
	runID, err := NewRunID(time.Now())
	if err != nil {
		t.Fatalf("NewRunID: %v", err)
	}
	if err := store.BeginRun(testRunRow(runID)); err != nil {
		t.Fatalf("BeginRun: %v", err)
	}

	if err := store.InsertOrderReject(RejectRow{
		Seq:        1,
		RecvNs:     1_000,
		StrategyID: "rebalance",
		Symbol:     "TINS",
		Side:       "buy",
		PriceIDR:   1000,
		Lots:       30,
		OrderID:    "rebalance-TINS-2026-09-24",
		Reason:     "risk: cap exceeded: order notional 3000000 IDR exceeds per-order cap 1500000 IDR",
		SinkKind:   "live",
	}); err != nil {
		t.Fatalf("InsertOrderReject: %v", err)
	}

	var (
		seq                       int64
		recvNs                    int64
		gotRunID, strategyID      string
		symbol, side              string
		priceIDR                  int64
		lots                      int
		orderID, reason, sinkKind string
	)
	err = store.QueryRow(
		`SELECT seq, recv_ns, run_id, strategy_id, symbol, side, price_idr, lots, order_id, reason, sink_kind
		 FROM order_reject WHERE seq = 1`,
	).Scan(&seq, &recvNs, &gotRunID, &strategyID, &symbol, &side, &priceIDR, &lots, &orderID, &reason, &sinkKind)
	if err != nil {
		t.Fatalf("query order_reject: %v", err)
	}
	if seq != 1 || recvNs != 1_000 || gotRunID != runID || strategyID != "rebalance" ||
		symbol != "TINS" || side != "buy" || priceIDR != 1000 || lots != 30 ||
		orderID != "rebalance-TINS-2026-09-24" || sinkKind != "live" {
		t.Fatalf("unexpected row: seq=%d recvNs=%d runID=%q strategyID=%q symbol=%q side=%q priceIDR=%d lots=%d orderID=%q sinkKind=%q",
			seq, recvNs, gotRunID, strategyID, symbol, side, priceIDR, lots, orderID, sinkKind)
	}
	if reason == "" {
		t.Fatal("reason was not recorded")
	}
}

// TestInsertOrderReject_DuplicateSeqRejected pins the same append-only
// guarantee the other four event tables carry (see TestStoreAppendOnly):
// seq is the primary key and a duplicate must error, never overwrite.
func TestInsertOrderReject_DuplicateSeqRejected(t *testing.T) {
	store := newTestStore(t)
	row := RejectRow{Seq: 1, RecvNs: 1, StrategyID: "s", Symbol: "AAA", Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "o1", Reason: "denied", SinkKind: "paper"}
	if err := store.InsertOrderReject(row); err != nil {
		t.Fatalf("first insert: %v", err)
	}
	if err := store.InsertOrderReject(row); err == nil {
		t.Fatal("duplicate seq was accepted, want an error (append-only guarantee)")
	}
}

// TestOrderReject_TableExistsOnFreshStore proves a brand-new store already
// has the table (CREATE TABLE IF NOT EXISTS in schemaSQL) — no migration
// step is needed since this is a wholly new table, not a new column on an
// existing one.
func TestOrderReject_TableExistsOnFreshStore(t *testing.T) {
	store := newTestStore(t)
	var name string
	err := store.QueryRow(`SELECT name FROM sqlite_master WHERE type='table' AND name='order_reject'`).Scan(&name)
	if err != nil {
		t.Fatalf("order_reject table missing from a fresh store: %v", err)
	}
}
