package live

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"strategy/engine"
)

// newTestSink builds a Sink against srv with a fixed clock, failing the
// test on construction error.
func newTestSink(t *testing.T, srv *httptest.Server) *Sink {
	t.Helper()
	c := newTestClient(t, srv)
	s, err := NewSink(Options{Client: c, Clock: func() time.Time { return time.Unix(1_700_000_000, 0) }})
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	return s
}

func testIntent(clientOrderID string, lots int) engine.OrderIntent {
	return engine.OrderIntent{
		Symbol:    "BBCA",
		Side:      "buy",
		PriceIDR:  9500,
		Lots:      lots,
		OrderID:   clientOrderID,
		BoardType: engine.BoardRG,
	}
}

// pollOnceDrain runs one pollOnce, in a goroutine, and reads exactly want
// fills from s.Fills() — pollOnce's fill sends are unlocked but still
// blocking (see pollOnce's doc comment), so a poll that is expected to
// produce fills must be drained concurrently or the test deadlocks.
func pollOnceDrain(t *testing.T, s *Sink, ctx context.Context, want int) []engine.AsyncFill {
	t.Helper()
	done := make(chan struct{})
	go func() {
		s.pollOnce(ctx)
		close(done)
	}()

	var got []engine.AsyncFill
	timeout := time.After(2 * time.Second)
	for len(got) < want {
		select {
		case f := <-s.fills:
			got = append(got, f)
		case <-timeout:
			t.Fatalf("timed out waiting for %d fill(s), got %d", want, len(got))
		}
	}
	select {
	case <-done:
	case <-timeout:
		t.Fatal("pollOnce did not return after producing its expected fills")
	}
	return got
}

// pollOnceExpectNoFill runs pollOnce synchronously and fails the test if it
// tries to send a fill — for scenarios (a stray duplicate status update, an
// OpenTimeout check) where nothing should ever reach the channel.
func pollOnceExpectNoFill(t *testing.T, s *Sink, ctx context.Context) {
	t.Helper()
	done := make(chan struct{})
	go func() {
		s.pollOnce(ctx)
		close(done)
	}()
	select {
	case f := <-s.fills:
		t.Fatalf("unexpected fill emitted: %+v", f)
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("pollOnce hung")
	}
}

func TestSubmit_SharesIsLotsTimesHundred(t *testing.T) {
	var gotBody map[string]any
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: func(w http.ResponseWriter, r *http.Request) {
			gotBody = readBody(t, r)
			jsonHandler(http.StatusOK, `{"data":{"order_id":"501"}}`)(w, r)
		},
	})
	s := newTestSink(t, srv)

	id, err := s.Submit(context.Background(), testIntent("rebalance-BBCA-1", 3))
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	if string(id) != "rebalance-BBCA-1" {
		t.Errorf("Submit returned %q, want the CLIENT order id rebalance-BBCA-1", id)
	}
	if gotBody["shares"] != "300" {
		t.Errorf("shares sent = %v, want \"300\" (3 lots * 100)", gotBody["shares"])
	}
}

func TestSubmit_UIRefStableAcrossRetrySameClientID_DiffersAcrossIDs(t *testing.T) {
	var uiRefs []string
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: func(w http.ResponseWriter, r *http.Request) {
			body := readBody(t, r)
			uiRefs = append(uiRefs, body["ui_ref"].(string))
			jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`)(w, r)
		},
	})
	s := newTestSink(t, srv)
	ctx := context.Background()

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit (first): %v", err)
	}
	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit (retry, same client id): %v", err)
	}
	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-2", 1)); err != nil {
		t.Fatalf("Submit (new client id): %v", err)
	}

	if len(uiRefs) != 3 {
		t.Fatalf("got %d ui_refs, want 3", len(uiRefs))
	}
	if uiRefs[0] != uiRefs[1] {
		t.Errorf("ui_ref changed across a retry of the same client id: %q vs %q", uiRefs[0], uiRefs[1])
	}
	if uiRefs[0] == uiRefs[2] {
		t.Errorf("ui_ref did not change for a new client id (both %q)", uiRefs[0])
	}
}

func TestSubmit_DuplicateOrderIsAdoptedNotFailed(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusConflict, `{"message":"ErrorDuplicateOrder"}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"42","symbol":"BBCA","side":"SIDE_BUY","status_text":"OPEN","price":{"order":9500},"qty":{"lot_done":0,"lot_open":1,"lot_total":1}}
		]}`),
	})
	s := newTestSink(t, srv)

	id, err := s.Submit(context.Background(), testIntent("rebalance-BBCA-1", 1))
	if err != nil {
		t.Fatalf("Submit: %v, want ErrDuplicateOrder to be resolved rather than surfaced", err)
	}
	if string(id) != "rebalance-BBCA-1" {
		t.Errorf("Submit returned %q, want the client order id", id)
	}

	s.mu.Lock()
	tracked := s.orders["rebalance-BBCA-1"]
	s.mu.Unlock()
	if tracked == nil {
		t.Fatal("order not tracked after duplicate resolution")
	}
	if tracked.BrokerOrderID != "42" {
		t.Errorf("adopted BrokerOrderID = %q, want 42", tracked.BrokerOrderID)
	}
}

// TestSubmit_DuplicateDoesNotAdoptABaselinedOrder guards the core-book/sleeve
// boundary plan 18 §3.4 and this package's doc comment both worry about:
// GET /order/v2/list is not account-scoped, so a symbol+side+price+shares
// match found while resolving ErrDuplicateOrder could just as easily be the
// core book's own pre-existing resting order as this run's own retried
// submission. Baseline() exists precisely to tell the two apart; a match
// must be excluded if it was already resting before this run started, the
// same way an already-tracked match is excluded — adopting a baselined
// order here would make Sink believe a core-book order belongs to the
// sleeve, corrupting every fill and reconciliation this package produces
// for it from that point on.
func TestSubmit_DuplicateDoesNotAdoptABaselinedOrder(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusConflict, `{"message":"ErrorDuplicateOrder"}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"core-1","symbol":"BBCA","side":"SIDE_BUY","status_text":"OPEN","price":{"order":9500},"qty":{"lot_done":0,"lot_open":1,"lot_total":1}}
		]}`),
	})
	s := newTestSink(t, srv)
	ctx := context.Background()

	// core-1 pre-dates this run — e.g. the core book's own resting order,
	// coincidentally sharing symbol/side/price/shares with the sleeve's
	// intent below — and is captured by Baseline before any submission.
	if err := s.Baseline(ctx); err != nil {
		t.Fatalf("Baseline: %v", err)
	}

	_, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1))
	if !errors.Is(err, ErrDuplicateOrder) {
		t.Fatalf("Submit error = %v, want errors.Is(_, ErrDuplicateOrder): the only List() match is a baselined order and must not be adopted", err)
	}

	s.mu.Lock()
	_, tracked := s.orders["rebalance-BBCA-1"]
	s.mu.Unlock()
	if tracked {
		t.Error("order was tracked after duplicate resolution despite the only List() match being a pre-existing baseline order")
	}
}

func TestSubmit_DuplicateWithNoMatchIsAnError(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy:  jsonHandler(http.StatusConflict, `{"message":"ErrorDuplicateOrder"}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[]}`),
	})
	s := newTestSink(t, srv)

	_, err := s.Submit(context.Background(), testIntent("rebalance-BBCA-1", 1))
	if !errors.Is(err, ErrDuplicateOrder) {
		t.Fatalf("Submit error = %v, want errors.Is(_, ErrDuplicateOrder) when no matching order is found", err)
	}
}

// TestPoll_PartialThenPartialThenFullEmitsThreeFillsSummingToLotsRequested
// drives a 10-lot order through two partial fills and a final full match,
// asserting the three AsyncFills sum to the lots requested and every one
// carries the CLIENT order id.
func TestPoll_PartialThenPartialThenFullEmitsThreeFillsSummingToLotsRequested(t *testing.T) {
	responses := []string{
		`{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"PARTIAL","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":19000000},"qty":{"lot_done":2,"lot_open":8,"lot_total":10}}]}`,
		`{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"PARTIAL","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":47500000},"qty":{"lot_done":5,"lot_open":5,"lot_total":10}}]}`,
		`{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"MATCH","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":95000000},"qty":{"lot_done":10,"lot_open":0,"lot_total":10}}]}`,
	}
	listCall := 0
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
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

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 10)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	var all []engine.AsyncFill
	for i := 0; i < 3; i++ {
		fills := pollOnceDrain(t, s, ctx, 1)
		all = append(all, fills...)
	}

	total := 0
	for _, f := range all {
		total += f.Fill.FillLots
		if string(f.Fill.OrderID) != "rebalance-BBCA-1" {
			t.Errorf("Fill.OrderID = %q, want the client order id rebalance-BBCA-1", f.Fill.OrderID)
		}
	}
	if total != 10 {
		t.Errorf("sum of FillLots = %d, want 10 (lots requested)", total)
	}
}

// TestPoll_LatePartialAfterFullMatchDoesNotUnfillOrDoubleCount is the IDX
// quirk (docs/reference/04-order-lifecycle.md §3.3) exercised through the
// full poll path, not just Transition directly.
func TestPoll_LatePartialAfterFullMatchDoesNotUnfillOrDoubleCount(t *testing.T) {
	responses := []string{
		`{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"MATCH","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":95000000},"qty":{"lot_done":10,"lot_open":0,"lot_total":10}}]}`,
		// Stray PARTIAL arriving after the full match, same lot_done.
		`{"data":[{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"PARTIAL","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":95000000},"qty":{"lot_done":10,"lot_open":0,"lot_total":10}}]}`,
	}
	listCall := 0
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
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

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 10)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	fills := pollOnceDrain(t, s, ctx, 1)
	if fills[0].Fill.FillLots != 10 {
		t.Fatalf("first poll FillLots = %d, want 10", fills[0].Fill.FillLots)
	}

	// Second poll: the stray PARTIAL. Must emit nothing (delta is 0) and
	// must not move the order back to PARTIAL.
	pollOnceExpectNoFill(t, s, ctx)

	s.mu.Lock()
	state := s.orders["rebalance-BBCA-1"].State
	lotsFilled := s.orders["rebalance-BBCA-1"].LotsFilled
	s.mu.Unlock()
	if state != StateFilled {
		t.Errorf("state after stray PARTIAL = %s, want unchanged StateFilled", state)
	}
	if lotsFilled != 10 {
		t.Errorf("LotsFilled after stray PARTIAL = %d, want unchanged 10", lotsFilled)
	}
}

func TestPoll_OpenTimeoutTriggersCancel(t *testing.T) {
	var cancelHit bool
	clock := time.Unix(1_700_000_000, 0)
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"OPEN","price":{"order":9500},"qty":{"lot_done":0,"lot_open":1,"lot_total":1}}
		]}`),
		pathCancel: func(w http.ResponseWriter, r *http.Request) {
			cancelHit = true
			jsonHandler(http.StatusOK, `{"data":{}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)
	s, err := NewSink(Options{Client: c, OpenTimeout: time.Second, Clock: func() time.Time { return clock }})
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	ctx := context.Background()

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	pollOnceExpectNoFill(t, s, ctx) // becomes OPEN; openSince = clock

	clock = clock.Add(2 * time.Second)
	pollOnceExpectNoFill(t, s, ctx) // OpenTimeout elapsed

	if !cancelHit {
		t.Error("OpenTimeout elapsed but /order/v2/cancel was never called")
	}
}

// TestPoll_OpenTimeoutCancelFailureIsRetriedOnNextPoll guards against a
// silently-abandoned resting order: checkTimeouts latches cancelRequested
// BEFORE the actual (unlocked) HTTP cancel call happens, so a transient
// failure of that one call must not permanently disable every future
// OpenTimeout cancel attempt for this order — otherwise a single flaky
// /order/v2/cancel response leaves a resting, unfilled order exposed to the
// market indefinitely, with no further attempt and no repeated alert.
func TestPoll_OpenTimeoutCancelFailureIsRetriedOnNextPoll(t *testing.T) {
	clock := time.Unix(1_700_000_000, 0)
	cancelAttempts := 0
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"OPEN","price":{"order":9500},"qty":{"lot_done":0,"lot_open":1,"lot_total":1}}
		]}`),
		pathCancel: func(w http.ResponseWriter, r *http.Request) {
			cancelAttempts++
			if cancelAttempts == 1 {
				jsonHandler(http.StatusInternalServerError, `{"message":"boom"}`)(w, r)
				return
			}
			jsonHandler(http.StatusOK, `{"data":{}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)
	s, err := NewSink(Options{Client: c, OpenTimeout: time.Second, Clock: func() time.Time { return clock }})
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	ctx := context.Background()

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	pollOnceExpectNoFill(t, s, ctx) // becomes OPEN; openSince = clock

	clock = clock.Add(2 * time.Second)
	pollOnceExpectNoFill(t, s, ctx) // OpenTimeout elapsed; cancel attempt #1 fails

	clock = clock.Add(time.Second)
	pollOnceExpectNoFill(t, s, ctx) // must retry, not stay silently latched

	if cancelAttempts != 2 {
		t.Errorf("/order/v2/cancel called %d time(s), want 2 (the failed attempt must be retried on the next poll)", cancelAttempts)
	}
}

func TestCancel_TerminalOrderIsANoOp(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathCancel: func(w http.ResponseWriter, r *http.Request) {
			t.Error("Cancel must not call /order/v2/cancel for an order already in a terminal state")
		},
	})
	s := newTestSink(t, srv)
	ctx := context.Background()

	id, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1))
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	s.mu.Lock()
	s.orders[string(id)].State = StateFilled
	s.mu.Unlock()

	if err := s.Cancel(ctx, id); err != nil {
		t.Errorf("Cancel on a terminal order returned %v, want nil (no-op)", err)
	}
}

func TestSinkKind(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{})
	s := newTestSink(t, srv)
	if got := s.SinkKind(); got != "live" {
		t.Errorf("SinkKind() = %q, want \"live\"", got)
	}
}

func TestClose_ClosesFillsAndIsIdempotent(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathList: jsonHandler(http.StatusOK, `{"data":[]}`),
	})
	s := newTestSink(t, srv)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx)

	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	if _, open := <-s.Fills(); open {
		t.Error("Fills() channel still open after Close")
	}
}

// TestPoll_UndeliveredFillIsNotCommittedToBookkeeping is the shutdown race
// pollOnce's own doc comment on releasing mu before sending exists to make
// safe: a fill it has already computed under the lock must not be folded
// into LotsFilled/MatchedAmountIDR until it has actually been handed off on
// Fills(). If it were committed eagerly (as if delivery always succeeds),
// then a send abandoned by Close() — nobody is draining Fills(), so
// pollOnce's select falls through to <-s.done — would leave the order's own
// bookkeeping claiming a fill that engine.Run, the post-trade store, and
// every downstream position/PnL figure never actually saw. Worse,
// ReconcileTerminal compares LotsFilled against the broker's own lot_done:
// if LotsFilled was bumped regardless of delivery, that comparison agrees
// and reports a clean run, even though a real execution was silently lost.
// This test proves the fill is NOT committed until delivered, so that exact
// divergence is exactly what ReconcileTerminal flags instead.
func TestPoll_UndeliveredFillIsNotCommittedToBookkeeping(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`),
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"MATCH","price":{"order":9500,"average":{"price":9500}},"amount":{"matched_total":950000},"qty":{"lot_done":1,"lot_open":0,"lot_total":1}}
		]}`),
	})
	c := newTestClient(t, srv)
	s, err := NewSink(Options{
		Client:       c,
		PollInterval: 5 * time.Millisecond,
		Clock:        func() time.Time { return time.Unix(1_700_000_000, 0) },
	})
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	ctx := context.Background()

	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	// Start the poller but never drain Fills(): the first tick computes the
	// fill and then blocks trying to hand it off, with nobody listening.
	s.Start(ctx)
	time.Sleep(100 * time.Millisecond) // let a poll tick reach the blocked send

	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	s.mu.Lock()
	tracked := s.orders["rebalance-BBCA-1"]
	s.mu.Unlock()
	if tracked.State != StateFilled {
		t.Fatalf("state = %s, want StateFilled (the state transition is independent of delivery)", tracked.State)
	}
	if tracked.LotsFilled != 0 {
		t.Errorf("LotsFilled = %d, want 0: the fill was never delivered on Fills(), so bookkeeping must not claim it was applied", tracked.LotsFilled)
	}

	div, err := s.ReconcileTerminal(ctx)
	if err != nil {
		t.Fatalf("ReconcileTerminal: %v", err)
	}
	if div.Empty() {
		t.Fatal("ReconcileTerminal reported no divergence, want a LotMismatch flagging the undelivered fill")
	}
}
