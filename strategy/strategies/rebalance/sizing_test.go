package rebalance

import (
	"context"
	"testing"
)

// The sizing basis and the submitted price must be the same number.
//
// This is the invariant, stated as a property rather than as expected lot
// counts: the order must commit as much of the target notional as whole
// lots allow AT THE PRICE ACTUALLY SUBMITTED, and one more lot must
// overshoot it. Sizing on the raw observed price while submitting a
// downward-snapped one satisfies the first half and fails the second — it
// leaves a whole lot of room unused on every name whose price is off-tick,
// silently and always in the same direction.
//
// It matters here more than it would in a signal strategy: the null
// portfolio's premise (plan 18 §3.3) is that its weights are known exactly,
// so that deviation from arrival price is attributable to execution and
// nothing else. A tick-rounding underweight would be measured by B6 as
// execution cost that never happened.
func TestSizingUsesTheSubmittedPriceNotTheRawPrice(t *testing.T) {
	const baseNotional = 15_000_000
	names := []string{"AAA"}
	p := nullPortfolio(t, names, baseNotional)

	targetNotional, ok := p.TargetNotionalIDR("AAA")
	if !ok {
		t.Fatal("AAA is not in its own portfolio")
	}

	// One raw price per tick band, each deliberately off the grid so the
	// snap actually moves it.
	for _, raw := range []float64{163, 301, 1003, 2507, 6013, 9999} {
		s, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		eng := &fakeEngine{now: wibTime(9, 30)}
		s.OnEvent(context.Background(), liveEvent("AAA", raw), eng)

		if len(eng.accepted) != 1 {
			t.Fatalf("raw %.0f: expected exactly one order, got %d", raw, len(eng.accepted))
		}
		got := eng.accepted[0]
		submitted := got.PriceIDR
		committed := submitted * int64(got.Lots) * sharesPerLot
		oneMore := submitted * int64(got.Lots+1) * sharesPerLot

		if committed > targetNotional {
			t.Errorf("raw %.0f: committed %d IDR at %d x %d lots, over the %d target",
				raw, committed, submitted, got.Lots, targetNotional)
		}
		if oneMore <= targetNotional {
			t.Errorf("raw %.0f: sized %d lots at %d, but %d lots would still fit in %d — "+
				"the sizing basis is not the submitted price",
				raw, got.Lots, submitted, got.Lots+1, targetNotional)
		}
	}
}

// A price below 1 IDR snaps to 0. Dividing the target notional by it gives
// +Inf, and converting +Inf to int is undefined in Go — it produces a
// garbage lot count rather than an error, which is the worst possible
// outcome for a number that becomes an order. No IDX instrument trades
// below 1 IDR, so such a price is a corrupt frame and the only safe
// response is to place nothing.
func TestSubPriceFrameProducesNoOrder(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 15_000_000)
	for _, raw := range []float64{0.5, 0.01, 0.999} {
		s, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		eng := &fakeEngine{now: wibTime(9, 30)}
		s.OnEvent(context.Background(), liveEvent("AAA", raw), eng)

		if len(eng.calls) != 0 {
			t.Errorf("price %v: expected no order, got %+v", raw, eng.calls)
		}
		if got := s.Positions()["AAA"]; got != 0 {
			t.Errorf("price %v: position moved to %d", raw, got)
		}
	}
}
