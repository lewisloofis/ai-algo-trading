package engine

import (
	"context"
	"testing"
	"time"
)

// TestRun_AsyncRejectIsRecordedInOrderReject: a broker rejection arriving
// after a successful Submit must land in order_reject with the client order
// id and a prefixed reason; closing the Rejects channel must not end the run.
func TestRun_AsyncRejectIsRecordedInOrderReject(t *testing.T) {
	store := newTestStore(t)
	sink := newFakeAsyncSink("fake-live")
	strat := &asyncOrderStrategy{filled: make(chan FillRecord, 4)}
	src := make(chan MarketEvent)

	done := make(chan struct{})
	var runErr error
	go func() {
		_, runErr = Run(context.Background(), src, strat, sink, store, "async-reject")
		close(done)
	}()

	src <- liveEvent("AAA", 100, 1) // buy accepted by Submit
	sink.rejectsCh <- AsyncReject{
		OrderID: "o1", Symbol: "AAA", Side: "buy", PriceIDR: 100, Lots: 1,
		Reason: "[C-006] insufficient trading balance", RecvNs: 10,
	}
	close(sink.rejectsCh) // mid-run close: run must continue
	src <- liveEvent("AAA", 100, 2)
	close(src)
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return")
	}
	if runErr != nil {
		t.Fatalf("Run: %v", runErr)
	}

	var n int
	if err := store.QueryRow(`SELECT COUNT(*) FROM order_reject`).Scan(&n); err != nil || n != 1 {
		t.Fatalf("order_reject rows = %d (err %v), want 1", n, err)
	}
	var orderID, symbol, side, reason, kind string
	var price int64
	var lots int
	if err := store.QueryRow(`SELECT order_id, symbol, side, price_idr, lots, reason, sink_kind FROM order_reject`).
		Scan(&orderID, &symbol, &side, &price, &lots, &reason, &kind); err != nil {
		t.Fatalf("query: %v", err)
	}
	if orderID != "o1" || symbol != "AAA" || side != "buy" || price != 100 || lots != 1 || kind != "fake-live" {
		t.Errorf("row = %s %s %s %d %d %s", orderID, symbol, side, price, lots, kind)
	}
	if want := "broker rejected after submit: [C-006] insufficient trading balance"; reason != want {
		t.Errorf("reason = %q, want %q", reason, want)
	}
}
