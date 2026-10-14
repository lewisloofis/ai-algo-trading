package rebalance

import (
	"context"
	"testing"

	"strategy/portfolio"
)

// TestSellsToZeroWhenSymbolLeavesTarget covers plan 18 §1: a held symbol that
// is absent from the target (or targeted at weight 0) is sold in full.
func TestSellsToZeroWhenSymbolLeavesTarget(t *testing.T) {
	tests := []struct {
		name     string
		names    []string // null portfolio names
		zeroOld  bool     // build target with OLD at weight 0 instead of absent
		initial  map[string]int
		wantSell int // lots of OLD sold; 0 = no OLD intent
	}{
		{"held absent from target", []string{"AAA"}, false, map[string]int{"OLD": 7}, 7},
		{"held with weight zero", []string{"AAA"}, true, map[string]int{"OLD": 7}, 7},
		{"absent and flat", []string{"AAA"}, false, nil, 0},
		{"absent with explicit zero lots", []string{"AAA"}, false, map[string]int{"OLD": 0}, 0},
		{"absent and short is never bought", []string{"AAA"}, false, map[string]int{"OLD": -3}, 0},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			p := nullPortfolio(t, tc.names, 100_000_000)
			if tc.zeroOld {
				p.Targets = append(p.Targets, portfolio.Target{Symbol: "OLD", Weight: 0})
			}
			strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0), Initial: tc.initial})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			fe := &fakeEngine{now: wibTime(9, 5)}
			ctx := context.Background()
			strat.OnEvent(ctx, liveEvent("AAA", 1000), fe)
			strat.OnEvent(ctx, liveEvent("OLD", 1000), fe)
			// Second event in the same window must not re-emit.
			strat.OnEvent(ctx, liveEvent("OLD", 1000), fe)

			var old int
			for _, c := range fe.calls {
				if c.Symbol != "OLD" {
					continue
				}
				old++
				if c.Side != "sell" || c.Lots != tc.wantSell || c.PriceIDR != 1000 {
					t.Fatalf("OLD intent = %+v, want sell %d lots @1000", c, tc.wantSell)
				}
			}
			wantCalls := 0
			if tc.wantSell > 0 {
				wantCalls = 1
			}
			if old != wantCalls {
				t.Fatalf("OLD intents = %d, want %d (calls=%+v)", old, wantCalls, fe.calls)
			}
		})
	}
}

// TestOrphanSellSkippedUnderDiagnosticLimit: the fixed limit price is a
// single-order diagnostic and must not be applied to non-target symbols.
func TestOrphanSellSkippedUnderDiagnosticLimit(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0), LimitPriceIDR: 1000, Initial: map[string]int{"OLD": 5}})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	fe := &fakeEngine{now: wibTime(9, 5)}
	strat.OnEvent(context.Background(), liveEvent("OLD", 1000), fe)
	if len(fe.calls) != 0 {
		t.Fatalf("calls = %+v, want none", fe.calls)
	}
}
