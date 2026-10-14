package rebalance

import (
	"context"
	"fmt"
	"os"
	"testing"

	"strategy/portfolio"
)

// TestEmitterTargetSizesToExactlyOneLot loads a target written by the Python
// daily target emitter (research/strategy_fit/target_emitter.py) through the
// real loader, then drives the real sizing path at prices around the last
// close. Skipped unless EMITTER_TARGET_JSON names a file; EMITTER_LAST_CLOSE
// is the close the emitter sized against.
//
// weight 1.0 must buy exactly 1 lot across the emitter's guaranteed price
// band (0.70x..1.30x close); weight 0.0 must place no order when flat.
func TestEmitterTargetSizesToExactlyOneLot(t *testing.T) {
	path := os.Getenv("EMITTER_TARGET_JSON")
	if path == "" {
		t.Skip("EMITTER_TARGET_JSON not set")
	}
	var lastClose float64
	if _, err := fmt.Sscan(os.Getenv("EMITTER_LAST_CLOSE"), &lastClose); err != nil || lastClose <= 0 {
		t.Fatalf("EMITTER_LAST_CLOSE must be a positive number: %v", err)
	}

	p, err := portfolio.Load(path)
	if err != nil {
		t.Fatalf("Go loader rejected the emitted target: %v", err)
	}
	if len(p.Targets) != 1 {
		t.Fatalf("want one target, got %d", len(p.Targets))
	}
	want := 0
	if p.Targets[0].Weight == 1.0 {
		want = 1
	} else if p.Targets[0].Weight != 0 {
		t.Fatalf("weight %v is neither 0 nor 1", p.Targets[0].Weight)
	}
	sym := p.Targets[0].Symbol

	for _, frac := range []float64{0.70, 0.85, 1.0, 1.15, 1.30} {
		s, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
		if err != nil {
			t.Fatalf("New: %v", err)
		}
		eng := &fakeEngine{now: wibTime(9, 30)}
		s.OnEvent(context.Background(), liveEvent(sym, lastClose*frac), eng)
		if want == 0 {
			if len(eng.calls) != 0 {
				t.Fatalf("price x%.2f: weight 0 and flat must place no order, got %+v", frac, eng.calls)
			}
			continue
		}
		if len(eng.calls) != 1 || eng.calls[0].Side != "buy" || eng.calls[0].Lots != 1 {
			t.Fatalf("price x%.2f (%.1f): want one buy of exactly 1 lot, got %+v", frac, lastClose*frac, eng.calls)
		}
	}
}
