package engine

import (
	"context"
	"testing"
)

// rejectingStrategy submits exactly one order on the first event it sees,
// for a symbol whose book this test deliberately never seeds — PaperSink.Submit
// then returns ErrNoBook, which is the failure this test drives through
// SubmitOrder to prove engine.go records it.
type rejectingStrategy struct {
	submitted bool
}

func (s *rejectingStrategy) OnEvent(ctx context.Context, e MarketEvent, eng Engine) {
	if s.submitted {
		return
	}
	s.submitted = true
	_, _ = eng.SubmitOrder(ctx, OrderIntent{
		Symbol: "NOBOOK", Side: "buy", PriceIDR: 1000, Lots: 1, OrderID: "rej-1",
	})
}

func (s *rejectingStrategy) OnFill(ctx context.Context, f FillRecord) {}

// TestSubmitOrder_RejectedIntentIsRecorded is Task 3's proof: a Submit that
// the sink refuses must leave a durable order_reject row, not just a
// returned error the caller may or may not have logged. Before this test,
// InsertIntent already ran (see engine.go), so this specifically pins the
// NEW behaviour — the reject row — rather than re-testing the intent log.
func TestSubmitOrder_RejectedIntentIsRecorded(t *testing.T) {
	store := newTestStore(t)
	sink := NewPaperSink() // no book ever observed for "NOBOOK" -> ErrNoBook
	strat := &rejectingStrategy{}

	// The event names a DIFFERENT symbol than the order (AAA vs NOBOOK) so
	// the paper sink's book truly has nothing for NOBOOK.
	src := feedEvents(liveEvent("AAA", 100, 1))
	if _, err := Run(context.Background(), src, strat, sink, store, "rebalance"); err != nil {
		t.Fatalf("Run: %v", err)
	}

	var count int
	if err := store.QueryRow(`SELECT COUNT(*) FROM order_reject`).Scan(&count); err != nil {
		t.Fatalf("count order_reject: %v", err)
	}
	if count != 1 {
		t.Fatalf("order_reject rows = %d, want 1", count)
	}

	var symbol, side, orderID, reason, sinkKind string
	var priceIDR int64
	var lots int
	err := store.QueryRow(
		`SELECT symbol, side, price_idr, lots, order_id, reason, sink_kind FROM order_reject ORDER BY seq LIMIT 1`,
	).Scan(&symbol, &side, &priceIDR, &lots, &orderID, &reason, &sinkKind)
	if err != nil {
		t.Fatalf("query order_reject: %v", err)
	}
	if symbol != "NOBOOK" || side != "buy" || priceIDR != 1000 || lots != 1 || orderID != "rej-1" {
		t.Fatalf("unexpected reject row: symbol=%q side=%q priceIDR=%d lots=%d orderID=%q", symbol, side, priceIDR, lots, orderID)
	}
	if reason == "" {
		t.Fatal("reason was not recorded")
	}
	if sinkKind != "paper" {
		t.Fatalf("sinkKind = %q, want paper", sinkKind)
	}

	// A rejected intent must still not produce a fill.
	var fillCount int
	if err := store.QueryRow(`SELECT COUNT(*) FROM fill`).Scan(&fillCount); err != nil {
		t.Fatalf("count fill: %v", err)
	}
	if fillCount != 0 {
		t.Fatalf("fill rows = %d, want 0", fillCount)
	}
}

// TestSubmitOrder_AcceptedIntentWritesNoReject is the negative case: a
// successful Submit must not also write a reject row.
func TestSubmitOrder_AcceptedIntentWritesNoReject(t *testing.T) {
	store := newTestStore(t)
	sink := NewPaperSink()
	strat := &recordingStrategy{submitOn: 2}

	src := feedEvents(
		liveEvent("AAA", 100, 1),
		liveEvent("AAA", 100, 2),
	)
	if _, err := Run(context.Background(), src, strat, sink, store, "orb"); err != nil {
		t.Fatalf("Run: %v", err)
	}

	var count int
	if err := store.QueryRow(`SELECT COUNT(*) FROM order_reject`).Scan(&count); err != nil {
		t.Fatalf("count order_reject: %v", err)
	}
	if count != 0 {
		t.Fatalf("order_reject rows = %d, want 0 for an accepted order", count)
	}
}
