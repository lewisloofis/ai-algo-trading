package market

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"testing"
	"time"

	"strategy/engine"
	"strategy/risk"
)

// This file is the end-to-end proof of what this package was built for:
// plan 18 §5 B2's open box, "wire reference 06 rules as gates, not
// advisories", and ADR-0016 gate G2/G3 behind it. Testing Gate in isolation
// shows it computes the right verdict; only driving it through RiskSink
// shows that the verdict actually stops an order.

// recordingSink is an engine.OrderSink that records what reached it. If a
// rejected intent ever shows up here, the gate did not gate.
type recordingSink struct {
	mu       sync.Mutex
	admitted []engine.OrderIntent
}

func (r *recordingSink) Submit(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.admitted = append(r.admitted, intent)
	return engine.OrderID(intent.OrderID), nil
}

func (r *recordingSink) Cancel(ctx context.Context, id engine.OrderID) error { return nil }

func (r *recordingSink) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.admitted)
}

// flatPnL reports no PnL, so the daily-loss gate never fires and every
// rejection below is attributable to the market gate alone.
type flatPnL struct{}

func (flatPnL) DailyPnLIDR(ctx context.Context) (float64, error) { return 0, nil }

func newRiskSinkWithGate(t *testing.T, g *Gate, inner engine.OrderSink) *risk.RiskSink {
	t.Helper()
	limits := risk.Limits{
		PerOrderNotionalFraction:   0.5,
		PerDayNotionalFraction:     1.0,
		MaxGrossExposureFraction:   1.0,
		MaxPositionPerNameFraction: 1.0,
		DailyLossLimitFraction:     0.1,
	}
	// 100M IDR of sleeve equity against a 100k IDR order: the caps are
	// deliberately far from binding so that nothing below can pass or fail
	// for a reason other than the market gate.
	sink, err := risk.NewRiskSink(inner, risk.SleeveEquityIDR(100_000_000), limits, g, flatPnL{}, "", func() time.Time { return tradingNoon })
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	return sink
}

func testIntent() engine.OrderIntent {
	// Price 1000 is on a valid tick under risk's banded approximation and
	// sits inside the 900..1100 fixture band; 1 lot = 100 shares = 100k IDR.
	return engine.OrderIntent{Symbol: "BBCA", Side: "buy", PriceIDR: 1000, Lots: 1, OrderID: "test-1", BoardType: engine.BoardRG}
}

func TestRiskSink_WithRealGate_AdmitsWhenEverySourceAllows(t *testing.T) {
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(tradingNoon))
	inner := &recordingSink{}
	sink := newRiskSinkWithGate(t, g, inner)

	if _, err := sink.Submit(context.Background(), testIntent()); err != nil {
		t.Fatalf("Submit: %v — a clean market state must admit the order", err)
	}
	if inner.count() != 1 {
		t.Errorf("inner sink saw %d orders, want 1", inner.count())
	}
}

// TestRiskSink_WithRealGate_RejectsEveryClosedCondition walks the five
// reference-06 rules one at a time. Each must stop the order BEFORE it
// reaches the inner sink — that is the difference between a gate and an
// advisory.
func TestRiskSink_WithRealGate_RejectsEveryClosedCondition(t *testing.T) {
	cases := []struct {
		name  string
		setup func(*fakeStack)
		at    func() *fakeClock
	}{
		{
			name:  "holiday (reference 06 §3)",
			setup: func(*fakeStack) {},
			at:    func() *fakeClock { return newFakeClock(saturdayNoon) },
		},
		{
			name:  "lunch break (reference 06 §2)",
			setup: func(f *fakeStack) { f.setBody(pathMarketSession, bodySessionBreak) },
		},
		{
			name:  "not tradable / suspended (reference 06 §7)",
			setup: func(f *fakeStack) { f.setBody(pathStockTradable, bodyTradableNo) },
		},
		{
			name: "price outside the ARA/ARB band (reference 06 §4)",
			setup: func(f *fakeStack) {
				f.setBody(pathOrderBook, `{"data":{"auto_reject_price":{"above":"500","below":"400"}}}`)
			},
		},
		{
			name:  "an endpoint is unreachable",
			setup: func(f *fakeStack) { f.setStatus(pathOrderBook, http.StatusBadGateway) },
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeStack(t)
			tc.setup(f)
			clock := newFakeClock(tradingNoon)
			if tc.at != nil {
				clock = tc.at()
			}
			g := newTestGate(t, f, clock)
			inner := &recordingSink{}
			sink := newRiskSinkWithGate(t, g, inner)

			_, err := sink.Submit(context.Background(), testIntent())
			if !errors.Is(err, risk.ErrMarketUnknown) {
				t.Fatalf("Submit err = %v, want ErrMarketUnknown", err)
			}
			if inner.count() != 0 {
				t.Errorf("inner sink saw %d orders, want 0 — a rejected intent must never reach the sink", inner.count())
			}
		})
	}
}

// TestRiskSink_DenyAllGateStillDenies guards the property that made this
// package necessary: until a Gate is passed, NewRiskSink's nil-gate
// fallback must keep refusing everything.
func TestRiskSink_DenyAllGateStillDenies(t *testing.T) {
	inner := &recordingSink{}
	sink, err := risk.NewRiskSink(inner, risk.SleeveEquityIDR(100_000_000), risk.Limits{
		PerOrderNotionalFraction:   1,
		PerDayNotionalFraction:     1,
		MaxGrossExposureFraction:   1,
		MaxPositionPerNameFraction: 1,
		DailyLossLimitFraction:     1,
	}, nil, flatPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	if _, err := sink.Submit(context.Background(), testIntent()); !errors.Is(err, risk.ErrMarketUnknown) {
		t.Fatalf("Submit err = %v, want ErrMarketUnknown from DenyAllGate", err)
	}
	if inner.count() != 0 {
		t.Errorf("inner sink saw %d orders under DenyAllGate, want 0", inner.count())
	}
}
