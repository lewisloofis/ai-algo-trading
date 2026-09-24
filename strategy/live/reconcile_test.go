package live

import (
	"context"
	"net/http"
	"testing"
)

func TestReconcileTerminal_LotMismatchWhenBrokerReportsMoreFilledThanEmitted(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"MATCH","qty":{"lot_done":10,"lot_open":0,"lot_total":10}}
		]}`),
	})
	s := newTestSink(t, srv)
	ctx := context.Background()

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 10)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	// Simulate "the sink's poller missed a fill": the order is terminal in
	// this Sink's own bookkeeping, but LotsFilled was never advanced from
	// 0 — exactly the scenario ReconcileTerminal exists to catch.
	s.mu.Lock()
	s.orders["rebalance-BBCA-1"].State = StateFilled
	s.mu.Unlock()

	div, err := s.ReconcileTerminal(ctx)
	if err != nil {
		t.Fatalf("ReconcileTerminal: %v", err)
	}
	if div.Empty() {
		t.Fatal("Divergence is empty, want a LotMismatch (broker lot_done=10, sink emitted 0)")
	}
	if len(div.LotMismatch) != 1 {
		t.Fatalf("LotMismatch = %d entries, want 1: %+v", len(div.LotMismatch), div.LotMismatch)
	}
	lm := div.LotMismatch[0]
	if lm.ClientOrderID != "rebalance-BBCA-1" || lm.BrokerLotDone != 10 || lm.SinkEmittedLots != 0 {
		t.Errorf("LotMismatch = %+v, want {rebalance-BBCA-1 10 0}", lm)
	}
}

func TestReconcileTerminal_ReportsNotTerminalOrders(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy:  jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[]}`),
	})
	s := newTestSink(t, srv)
	ctx := context.Background()

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	// Still StateSending — never polled into a terminal state.

	div, err := s.ReconcileTerminal(ctx)
	if err != nil {
		t.Fatalf("ReconcileTerminal: %v", err)
	}
	if len(div.NotTerminal) != 1 || div.NotTerminal[0] != "rebalance-BBCA-1" {
		t.Errorf("NotTerminal = %v, want [rebalance-BBCA-1]", div.NotTerminal)
	}
}

func TestBaseline_ThenReconcile_FlagsOnlyTheUnbaselinedOrder(t *testing.T) {
	responses := []string{
		// Baseline: one pre-existing order, not ours (e.g. the core book's).
		`{"data":[{"order_id":"core-1","symbol":"BBKP","side":"SIDE_SELL","status_text":"OPEN","qty":{"lot_done":0,"lot_open":1,"lot_total":1}}]}`,
		// ReconcileTerminal: the baseline order is still there, plus a
		// second one that was neither baselined nor placed by this Sink.
		`{"data":[
			{"order_id":"core-1","symbol":"BBKP","side":"SIDE_SELL","status_text":"OPEN","qty":{"lot_done":0,"lot_open":1,"lot_total":1}},
			{"order_id":"stray-1","symbol":"HMSP","side":"SIDE_BUY","status_text":"OPEN","qty":{"lot_done":0,"lot_open":1,"lot_total":1}}
		]}`,
	}
	listCall := 0
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathList: func(w http.ResponseWriter, r *http.Request) {
			body := responses[listCall]
			if listCall < len(responses)-1 {
				listCall++
			}
			jsonHandler(http.StatusOK, body)(w, r)
		},
	})
	s := newTestSink(t, srv)
	ctx := context.Background()

	if err := s.Baseline(ctx); err != nil {
		t.Fatalf("Baseline: %v", err)
	}

	div, err := s.ReconcileTerminal(ctx)
	if err != nil {
		t.Fatalf("ReconcileTerminal: %v", err)
	}
	if len(div.Unbaselined) != 1 || div.Unbaselined[0] != "stray-1" {
		t.Errorf("Unbaselined = %v, want [stray-1]", div.Unbaselined)
	}
}
