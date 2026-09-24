package engine

import (
	"context"
	"fmt"
	"testing"
	"time"
)

// fakeAsyncSink is a test-only OrderSink whose fills arrive on a channel
// instead of synchronously from Submit, standing in for a live order router
// so these tests can exercise Run's asyncSink/sinkKinder handling without
// strategy/live, which plugs into this exact seam (plan 18 §5 B4) but is
// built separately.
type fakeAsyncSink struct {
	fillsCh chan AsyncFill
	kind    string
}

func newFakeAsyncSink(kind string) *fakeAsyncSink {
	return &fakeAsyncSink{fillsCh: make(chan AsyncFill), kind: kind}
}

func (s *fakeAsyncSink) Submit(ctx context.Context, intent OrderIntent) (OrderID, error) {
	return OrderID(intent.OrderID), nil
}

func (s *fakeAsyncSink) Cancel(ctx context.Context, id OrderID) error { return nil }

// Fills implements asyncSink.
func (s *fakeAsyncSink) Fills() <-chan AsyncFill { return s.fillsCh }

// SinkKind implements sinkKinder.
func (s *fakeAsyncSink) SinkKind() string { return s.kind }

// push delivers one fill to whichever Run is reading Fills(). The channel is
// unbuffered, so the send only completes once Run's select has received it —
// tests rely on that handshake, not a sleep, to know the event loop has
// moved on to processing the fill.
func (s *fakeAsyncSink) push(af AsyncFill) { s.fillsCh <- af }

// close closes Fills, exercising Run's mid-run-close handling.
func (s *fakeAsyncSink) close() { close(s.fillsCh) }

// asyncOrderStrategy submits a buy on the first event it sees and a sell on
// the second, modelling one round trip driven entirely by async fills. It
// signals filled synchronously from OnFill so a test driving Run on its own
// goroutine can wait for a fill to be fully applied — the store insert, the
// attribution pairing, and the callback itself, all of which run before
// OnFill is invoked — without sleeping.
type asyncOrderStrategy struct {
	seen   int
	fills  int
	errs   []error
	filled chan FillRecord
}

func (s *asyncOrderStrategy) OnEvent(ctx context.Context, e MarketEvent, eng Engine) {
	s.seen++
	if s.seen > 2 {
		return
	}
	side := "buy"
	if s.seen == 2 {
		side = "sell"
	}
	_, err := eng.SubmitOrder(ctx, OrderIntent{
		Symbol: e.Symbol, Side: side, PriceIDR: 100, Lots: 1,
		OrderID: fmt.Sprintf("o%d", s.seen), BoardType: BoardRG,
	})
	s.errs = append(s.errs, err)
}

func (s *asyncOrderStrategy) OnFill(ctx context.Context, f FillRecord) {
	s.fills++
	s.filled <- f
}

// TestRun_AsyncFillAppliedThroughRealEngineRun drives a fake async sink
// through the real Run and checks that an async fill is booked exactly like
// a synchronous one: the fill row lands in the store under the sink's own
// kind, counters increment, the buy/sell pair produces one attribution row,
// and Strategy.OnFill fires.
func TestRun_AsyncFillAppliedThroughRealEngineRun(t *testing.T) {
	store := newTestStore(t)
	sink := newFakeAsyncSink("fake-live")
	strat := &asyncOrderStrategy{filled: make(chan FillRecord)}
	src := make(chan MarketEvent)

	done := make(chan struct{})
	var counters Counters
	var runErr error
	go func() {
		counters, runErr = Run(context.Background(), src, strat, sink, store, "async")
		close(done)
	}()

	src <- liveEvent("AAA", 100, 1) // triggers the buy SubmitOrder
	sink.push(AsyncFill{
		Symbol: "AAA", Side: "buy", RecvNs: 10,
		Fill: FillRecord{OrderID: "o1", FillPriceIDR: 100, FillLots: 1, FeeIDR: 15},
	})
	<-strat.filled // wait for the buy fill to be fully applied

	src <- liveEvent("AAA", 100, 2) // triggers the sell SubmitOrder
	sink.push(AsyncFill{
		Symbol: "AAA", Side: "sell", RecvNs: 20,
		Fill: FillRecord{OrderID: "o2", FillPriceIDR: 110, FillLots: 1, FeeIDR: 28},
	})
	<-strat.filled // wait for the sell fill — this one closes the round trip

	close(src)
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return after src closed")
	}
	if runErr != nil {
		t.Fatalf("Run: %v", runErr)
	}
	for i, err := range strat.errs {
		if err != nil {
			t.Fatalf("SubmitOrder %d failed: %v", i, err)
		}
	}

	if counters.Fills != 2 {
		t.Errorf("Counters.Fills = %d, want 2", counters.Fills)
	}
	if counters.RoundTrips != 1 {
		t.Errorf("Counters.RoundTrips = %d, want 1 — the buy/sell async fills should have paired", counters.RoundTrips)
	}
	if strat.fills != 2 {
		t.Errorf("Strategy.OnFill fired %d times, want 2", strat.fills)
	}

	var intentKind string
	if err := store.QueryRow(`SELECT sink_kind FROM order_intent WHERE order_id = 'o1'`).Scan(&intentKind); err != nil {
		t.Fatalf("query intent: %v", err)
	}
	if intentKind != "fake-live" {
		t.Errorf("intent sink_kind = %q, want fake-live", intentKind)
	}
	var n int
	if err := store.QueryRow(`SELECT COUNT(*) FROM fill WHERE sink_kind = 'fake-live'`).Scan(&n); err != nil {
		t.Fatalf("query fill: %v", err)
	}
	if n != 2 {
		t.Errorf("fill rows with sink_kind fake-live = %d, want 2", n)
	}
	var rows int
	if err := store.QueryRow(`SELECT COUNT(*) FROM attribution WHERE entry_order_id = 'o1' AND exit_order_id = 'o2'`).Scan(&rows); err != nil {
		t.Fatalf("query attribution: %v", err)
	}
	if rows != 1 {
		t.Errorf("attribution rows pairing o1/o2 = %d, want 1", rows)
	}
}

// TestRun_AsyncFillsChannelCloseKeepsRunAlive pins the contract that closing
// Fills mid-run is not the same as the run ending: only the market-event
// channel decides that (see Run's comment on the fills case).
func TestRun_AsyncFillsChannelCloseKeepsRunAlive(t *testing.T) {
	store := newTestStore(t)
	sink := newFakeAsyncSink("fake-live")
	strat := &asyncOrderStrategy{filled: make(chan FillRecord, 1)}
	src := make(chan MarketEvent)

	done := make(chan struct{})
	var counters Counters
	var runErr error
	go func() {
		counters, runErr = Run(context.Background(), src, strat, sink, store, "async-close")
		close(done)
	}()

	src <- liveEvent("AAA", 100, 1) // triggers the buy SubmitOrder; no fill ever arrives for it
	sink.close()                    // Fills closes mid-run

	// The run must still be alive and still process events after Fills
	// closed.
	src <- liveEvent("AAA", 100, 2) // triggers the sell SubmitOrder
	close(src)

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return after src closed — closing Fills mid-run wedged the loop")
	}
	if runErr != nil {
		t.Fatalf("Run: %v", runErr)
	}
	if counters.OrdersAttempted != 2 {
		t.Errorf("OrdersAttempted = %d, want 2 — Run should keep processing events after Fills closed", counters.OrdersAttempted)
	}
	if counters.Fills != 0 {
		t.Errorf("Counters.Fills = %d, want 0 — no fill was ever sent in this test", counters.Fills)
	}
}

// TestRun_SinkKindDefaultsToPaperWhenSinkDoesNotImplementSinkKinder pins the
// pre-existing hardcoded default: a sink that does not implement sinkKinder
// (PaperSink, and everything before this seam existed) still records
// "paper" on every intent and fill row.
func TestRun_SinkKindDefaultsToPaperWhenSinkDoesNotImplementSinkKinder(t *testing.T) {
	store := newTestStore(t)
	sink := NewPaperSink()
	strat := &recordingStrategy{submitOn: 2}
	src := feedEvents(liveEvent("AAA", 100, 1), liveEvent("AAA", 100, 2))
	if _, err := Run(context.Background(), src, strat, sink, store, "sk1"); err != nil {
		t.Fatalf("Run: %v", err)
	}

	var intentKind string
	if err := store.QueryRow(`SELECT sink_kind FROM order_intent WHERE order_id = 'o1'`).Scan(&intentKind); err != nil {
		t.Fatalf("query intent: %v", err)
	}
	if intentKind != "paper" {
		t.Errorf("intent sink_kind = %q, want paper", intentKind)
	}
	var fillKind string
	if err := store.QueryRow(`SELECT sink_kind FROM fill WHERE order_id = 'o1'`).Scan(&fillKind); err != nil {
		t.Fatalf("query fill: %v", err)
	}
	if fillKind != "paper" {
		t.Errorf("fill sink_kind = %q, want paper", fillKind)
	}
}
