package risk

import (
	"context"
	"errors"
	"testing"
	"time"

	"strategy/engine"
)

// rejectingSink is an inner sink that reports every Submit as failed, the way
// a broker 4xx does: the order never reaches the market.
type rejectingSink struct{ recordingSink }

func (s *rejectingSink) Submit(ctx context.Context, i engine.OrderIntent) (engine.OrderID, error) {
	s.recordingSink.Submit(ctx, i)
	return "", errors.New("broker rejected")
}

// exposureSink builds a RiskSink whose only binding gate is exposure: the
// per-day notional cap is set far out of reach so these tests isolate the
// position/gross-exposure accounting.
func exposureSink(t *testing.T, inner engine.OrderSink, equityIDR float64) *RiskSink {
	t.Helper()
	lim := permissiveLimits()
	lim.PerDayNotionalFraction = 1000 // never binds
	r, err := NewRiskSink(inner, SleeveEquityIDR(equityIDR), lim,
		fixedGate{state: allowState()}, fixedPnL{pnl: 0}, "",
		func() time.Time { return time.Date(2026, 9, 15, 10, 0, 0, 0, time.UTC) })
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	return r
}

// buy3M is a 3,000,000 IDR buy: 3,000 IDR x 10 lots x 100 shares.
func buy3M(orderID string) engine.OrderIntent {
	return engine.OrderIntent{Symbol: "AAAA", Side: "buy", PriceIDR: 3000, Lots: 10, OrderID: orderID, BoardType: engine.BoardRG}
}

func sell3M(orderID string) engine.OrderIntent {
	return engine.OrderIntent{Symbol: "AAAA", Side: "sell", PriceIDR: 3000, Lots: 10, OrderID: orderID, BoardType: engine.BoardRG}
}

// An order the broker rejects never reached the market, so it must not hold
// exposure. Before the reservation model this ratcheted permanently: five
// rejected buys locked the name out despite a zero real position.
func TestSubmit_BrokerRejectedOrderReleasesItsExposure(t *testing.T) {
	inner := &rejectingSink{}
	r := exposureSink(t, inner, 15_000_000)
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		if _, err := r.Submit(ctx, buy3M("rejected")); err == nil {
			t.Fatalf("submit %d: want broker error", i)
		}
	}

	ok := &recordingSink{}
	r.inner = ok
	if _, err := r.Submit(ctx, buy3M("real")); err != nil {
		t.Errorf("after 5 broker-rejected buys the real position is 0, so a 3M buy must be admitted; got %v", err)
	}
}

// A sell the broker rejects must NOT free exposure the real position still
// holds. This is the unsafe direction: under-counting exposure admits orders
// that breach the cap.
func TestSubmit_BrokerRejectedSellDoesNotFreeExposure(t *testing.T) {
	ok := &recordingSink{}
	r := exposureSink(t, ok, 15_000_000)
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		if _, err := r.Submit(ctx, buy3M(string(rune('a'+i)))); err != nil {
			t.Fatalf("buy %d: %v", i, err)
		}
	}
	if _, err := r.Submit(ctx, buy3M("over")); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("want cap exceeded at 15M, got %v", err)
	}

	r.inner = &rejectingSink{}
	if _, err := r.Submit(ctx, sell3M("failed-sell")); err == nil {
		t.Fatal("want broker rejection on the sell")
	}

	r.inner = ok
	if _, err := r.Submit(ctx, buy3M("after")); !errors.Is(err, ErrCapExceeded) {
		t.Errorf("a sell that never reached the market must not free headroom; got %v", err)
	}
}

// Cancel releases the cancelled order's exposure but leaves the day's
// notional consumed — the order did reach the market.
func TestCancel_ReleasesExposureButNotDayNotional(t *testing.T) {
	inner := &recordingSink{}
	r := exposureSink(t, inner, 15_000_000)
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		id, err := r.Submit(ctx, buy3M("c"+string(rune('0'+i))))
		if err != nil {
			t.Fatalf("buy %d: %v", i, err)
		}
		if err := r.Cancel(ctx, id); err != nil {
			t.Fatalf("cancel %d: %v", i, err)
		}
	}

	if _, err := r.Submit(ctx, buy3M("after-cancels")); err != nil {
		t.Errorf("five cancelled buys hold no position, so a 3M buy must be admitted; got %v", err)
	}

	r.state.mu.Lock()
	day := r.state.dayNotionalIDR
	r.state.mu.Unlock()
	if day != 18_000_000 {
		t.Errorf("day notional = %.0f, want 18000000: cancelled orders still spent the day's order-flow budget", day)
	}
}

// A failed Cancel must not release exposure: the order is still live.
func TestCancel_FailureLeavesExposureInPlace(t *testing.T) {
	inner := &cancelFailSink{}
	r := exposureSink(t, inner, 15_000_000)
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		if _, err := r.Submit(ctx, buy3M("k"+string(rune('0'+i)))); err != nil {
			t.Fatalf("buy %d: %v", i, err)
		}
	}
	if err := r.Cancel(ctx, engine.OrderID("k0")); err == nil {
		t.Fatal("want cancel error")
	}
	if _, err := r.Submit(ctx, buy3M("after")); !errors.Is(err, ErrCapExceeded) {
		t.Errorf("a failed cancel leaves the order live, so exposure must stand; got %v", err)
	}
}

type cancelFailSink struct{ recordingSink }

func (s *cancelFailSink) Cancel(ctx context.Context, id engine.OrderID) error {
	return errors.New("cancel rejected")
}

// A sink that mints its own order id must still have its reservation
// reachable by Cancel.
func TestCancel_ResolvesSinkMintedOrderID(t *testing.T) {
	inner := &mintingSink{}
	r := exposureSink(t, inner, 15_000_000)
	ctx := context.Background()

	id, err := r.Submit(ctx, buy3M("intent-id"))
	if err != nil {
		t.Fatalf("submit: %v", err)
	}
	if string(id) == "intent-id" {
		t.Fatal("test fake did not mint a distinct id")
	}
	if err := r.Cancel(ctx, id); err != nil {
		t.Fatalf("cancel: %v", err)
	}

	r.state.mu.Lock()
	pos := r.state.positionsIDR["AAAA"]
	r.state.mu.Unlock()
	if pos != 0 {
		t.Errorf("position = %.0f, want 0: cancel by the sink-minted id must release the reservation", pos)
	}
}

type mintingSink struct{ recordingSink }

func (s *mintingSink) Submit(ctx context.Context, i engine.OrderIntent) (engine.OrderID, error) {
	s.recordingSink.Submit(ctx, i)
	return engine.OrderID("broker-" + i.OrderID), nil
}
