package rebalance

import (
	"context"
	"testing"
)

// TestOrderID_IsDeterministicPerWindowAndSymbol pins the restart-durability
// requirement (plan 18 §5 B4 gap): the client order id must be a pure
// function of (symbol, window) — not a process-local counter — so a
// same-day restart mints the IDENTICAL client order id for the same
// logical rebalance action. strategy/live derives a deterministic ui_ref
// from this id (see live.DeterministicUIRef), so the id itself being
// restart-stable is what makes that mechanism work end to end.
func TestOrderID_IsDeterministicPerWindowAndSymbol(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5)}
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	if len(fe.calls) != 1 {
		t.Fatalf("SubmitOrder called %d times, want 1", len(fe.calls))
	}
	gotID := fe.calls[0].OrderID

	// A second, independently constructed Strategy — simulating a fresh
	// process after a crash — attempting the SAME window and symbol must
	// mint the SAME order id.
	strat2, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New (restart): %v", err)
	}
	fe2 := &fakeEngine{now: wibTime(9, 5)}
	strat2.OnEvent(context.Background(), liveEvent("AAA", 1000), fe2)
	if len(fe2.calls) != 1 {
		t.Fatalf("SubmitOrder (restart) called %d times, want 1", len(fe2.calls))
	}
	if fe2.calls[0].OrderID != gotID {
		t.Fatalf("order id not restart-stable: %q (before) vs %q (after)", gotID, fe2.calls[0].OrderID)
	}

	// A DIFFERENT symbol in the same window must mint a DIFFERENT id — this
	// is not a constant, it is keyed by symbol too.
	p2 := nullPortfolio(t, []string{"BBB"}, 100_000_000)
	strat3, err := New(Options{Portfolio: p2, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New (other symbol): %v", err)
	}
	fe3 := &fakeEngine{now: wibTime(9, 5)}
	strat3.OnEvent(context.Background(), liveEvent("BBB", 1000), fe3)
	if len(fe3.calls) != 1 {
		t.Fatalf("SubmitOrder (BBB) called %d times, want 1", len(fe3.calls))
	}
	if fe3.calls[0].OrderID == gotID {
		t.Fatalf("order id did not vary by symbol: both %q", gotID)
	}
}

// TestOptionsAttempted_SkipsAlreadyAttemptedWindow is the strategy-level
// half of restart durability: a caller that has derived "this (window,
// symbol) was already attempted" from the post-trade store (see
// engine.PostTradeStore.AttemptedWindows) can seed it via Options.Attempted,
// and the strategy must never call SubmitOrder for it again — matching the
// existing single-process rule (TestIdempotentWithinWindow) that one
// attempt per window is final, whatever its outcome, now made durable
// across a restart instead of only within one process's lifetime.
func TestOptionsAttempted_SkipsAlreadyAttemptedWindow(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA", "BBB"}, 100_000_000)
	strat, err := New(Options{
		Portfolio: p,
		Schedule:  DailyAt(9, 0),
		Attempted: map[string]bool{"2026-09-16|AAA": true},
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5)}
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	strat.OnEvent(context.Background(), liveEvent("BBB", 1000), fe)

	if len(fe.calls) != 1 {
		t.Fatalf("SubmitOrder called %d times, want exactly 1 (AAA pre-seeded as attempted)", len(fe.calls))
	}
	if fe.calls[0].Symbol != "BBB" {
		t.Fatalf("SubmitOrder called for %q, want BBB (AAA should have been skipped)", fe.calls[0].Symbol)
	}
}

// TestOptionsAttempted_NilIsFineAndAttemptsEverything is the default case:
// a Strategy built with no Attempted (the zero value, nil map — every
// caller before this change) behaves exactly as before.
func TestOptionsAttempted_NilIsFineAndAttemptsEverything(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	fe := &fakeEngine{now: wibTime(9, 5)}
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	if len(fe.calls) != 1 {
		t.Fatalf("SubmitOrder called %d times, want 1", len(fe.calls))
	}
}
