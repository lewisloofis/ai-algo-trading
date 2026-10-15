package risk

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"strategy/engine"
)

// failingSink is an inner sink whose Submit always fails with err.
type failingSink struct {
	recordingSink
	err error
}

func (f *failingSink) Submit(ctx context.Context, in engine.OrderIntent) (engine.OrderID, error) {
	return "", f.err
}

func cappedWith(t *testing.T, inner engine.OrderSink, opts ...Option) *RiskSink {
	t.Helper()
	opts = append([]Option{WithMaxBoughtNotionalIDR(1_000_000, 0)}, opts...)
	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil, opts...)
	if err != nil {
		t.Fatal(err)
	}
	return sink
}

// httpStatusErr stands in for a typed broker error (a 4xx the classifier knows).
type httpStatusErr struct{ status int }

func (e httpStatusErr) Error() string { return fmt.Sprintf("HTTP %d", e.status) }

func definite4xx(err error) bool {
	var h httpStatusErr
	return errors.As(err, &h) && h.status >= 400 && h.status < 500 && h.status != 408
}

func TestBuyCap_TimedOutSubmitStaysConsumed(t *testing.T) {
	// The broker may have accepted this buy before the client gave up.
	inner := &failingSink{err: fmt.Errorf("live: POST: %w", context.DeadlineExceeded)}
	sink := cappedWith(t, inner, WithNotPlacedClassifier(definite4xx))
	if _, err := sink.Submit(context.Background(), intent("AAAA", "buy", 6_000, 1)); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("want the timeout back, got %v", err)
	}
	snap := sink.Snapshot()
	if snap.BoughtNotionalIDR != 600_000 || snap.OpenReservations != 1 {
		t.Fatalf("a timed-out buy must stay reserved: bought %v, reservations %d", snap.BoughtNotionalIDR, snap.OpenReservations)
	}
	if _, err := sink.Submit(context.Background(), intent("BBBB", "buy", 6_000, 1)); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("a second 600k buy must be refused (600k may already be live), got %v", err)
	}
}

func TestBuyCap_ServerErrorAndUnknownErrorStayConsumed(t *testing.T) {
	for _, e := range []error{httpStatusErr{500}, httpStatusErr{502}, httpStatusErr{408}, errors.New("connection reset by peer")} {
		sink := cappedWith(t, &failingSink{err: e}, WithNotPlacedClassifier(definite4xx))
		_, _ = sink.Submit(context.Background(), intent("AAAA", "buy", 6_000, 1))
		if got := sink.Snapshot().BoughtNotionalIDR; got != 600_000 {
			t.Fatalf("%v: bought %v, want 600000 (kept consumed)", e, got)
		}
	}
}

func TestBuyCap_Definite4xxIsRefundedAndTheNextBuyFits(t *testing.T) {
	inner := &failingSink{err: httpStatusErr{400}}
	sink := cappedWith(t, inner, WithNotPlacedClassifier(definite4xx))
	if _, err := sink.Submit(context.Background(), intent("AAAA", "buy", 6_000, 1)); err == nil {
		t.Fatal("want the 4xx back")
	}
	if got := sink.Snapshot().BoughtNotionalIDR; got != 0 {
		t.Fatalf("a refused order bought nothing: %v", got)
	}
	ok := &recordingSink{}
	sink.inner = ok
	mustAdmit(t, sink, intent("BBBB", "buy", 10_000, 1)) // the full 1M still fits
	if _, err := sink.Submit(context.Background(), intent("CCCC", "buy", 100, 1)); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("and then the cap holds, got %v", err)
	}
}

func TestBuyCap_ExposureAndDayNotionalFollowTheSameRule(t *testing.T) {
	lim := permissiveLimits()
	lim.PerDayNotionalFraction = 0.1 // 1.5M on a 15M sleeve
	inner := &failingSink{err: context.DeadlineExceeded}
	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), lim, allowGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = sink.Submit(context.Background(), intent("AAAA", "buy", 10_000, 1)) // 1M, ambiguous
	snap := sink.Snapshot()
	if snap.DayNotionalIDR != 1_000_000 || snap.GrossExposureIDR != 1_000_000 {
		t.Fatalf("ambiguous failure must keep day notional and exposure: %v / %v", snap.DayNotionalIDR, snap.GrossExposureIDR)
	}
	if _, err := sink.Submit(context.Background(), intent("BBBB", "buy", 10_000, 1)); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("day cap must see the possibly-live 1M, got %v", err)
	}
}

func TestNotPlacedClassifier_RejectsNil(t *testing.T) {
	if _, err := NewRiskSink(&recordingSink{}, SleeveEquityIDR(1), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil,
		WithNotPlacedClassifier(nil)); err == nil {
		t.Fatal("nil classifier must be refused")
	}
}

func newCappedSink(t *testing.T, maxIDR, seedIDR float64) (*RiskSink, *recordingSink) {
	t.Helper()
	inner := &recordingSink{}
	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil,
		WithMaxBoughtNotionalIDR(maxIDR, seedIDR))
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	return sink, inner
}

func TestBuyCap_AdmitsExactlyAtTheCapAndRefusesTheNextRupiah(t *testing.T) {
	sink, inner := newCappedSink(t, 1_000_000, 0)
	mustAdmit(t, sink, intent("AAAA", "buy", 10_000, 1))                      // exactly 1,000,000
	_, err := sink.Submit(context.Background(), intent("BBBB", "buy", 50, 1)) // +5,000
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("want ErrCapExceeded, got %v", err)
	}
	if !strings.Contains(err.Error(), "bought notional") {
		t.Fatalf("error should name the buy cap: %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("a refused buy reached the inner sink (count=%d)", inner.count())
	}
}

func TestBuyCap_IsCumulativeAcrossOrders(t *testing.T) {
	sink, inner := newCappedSink(t, 1_000_000, 0)
	mustAdmit(t, sink, intent("AAAA", "buy", 6_000, 1)) // 600k
	if _, err := sink.Submit(context.Background(), intent("BBBB", "buy", 6_000, 1)); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("second 600k buy should breach a 1M cap, got %v", err)
	}
	mustAdmit(t, sink, intent("CCCC", "buy", 4_000, 1)) // 400k -> exactly 1M
	if inner.count() != 2 {
		t.Fatalf("count=%d, want 2", inner.count())
	}
}

func TestBuyCap_SellsAreNeverCounted(t *testing.T) {
	sink, _ := newCappedSink(t, 1_000_000, 0)
	mustAdmit(t, sink, intent("AAAA", "buy", 10_000, 1)) // cap fully used
	mustAdmit(t, sink, intent("AAAA", "sell", 10_000, 1))
}

func TestBuyCap_SeedCountsAlreadyBoughtToday(t *testing.T) {
	sink, _ := newCappedSink(t, 1_000_000, 900_000)
	if _, err := sink.Submit(context.Background(), intent("AAAA", "buy", 2_000, 1)); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("200k on top of a 900k seed must breach 1M, got %v", err)
	}
	mustAdmit(t, sink, intent("AAAA", "buy", 1_000, 1)) // 100k -> exactly 1M
}

func TestBuyCap_CancelledOrderStaysConsumed(t *testing.T) {
	sink, _ := newCappedSink(t, 1_000_000, 0)
	mustAdmit(t, sink, intent("AAAA", "buy", 10_000, 1))
	if err := sink.Cancel(context.Background(), engine.OrderID("AAAA-buy")); err != nil {
		t.Fatal(err)
	}
	// It reached the market and may have partially filled: a hard cap must not be refunded.
	if _, err := sink.Submit(context.Background(), intent("BBBB", "buy", 1_000, 1)); !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("a cancelled buy must keep consuming the cap, got %v", err)
	}
}

func TestBuyCap_OrderThatNeverReachedTheMarketIsRefunded(t *testing.T) {
	inner := &failingSink{err: fmt.Errorf("never sent: %w", ErrNotPlaced)}
	sink := cappedWith(t, inner)
	if _, err := sink.Submit(context.Background(), intent("AAAA", "buy", 10_000, 1)); err == nil {
		t.Fatal("expected the inner error")
	}
	if got := sink.Snapshot().BoughtNotionalIDR; got != 0 {
		t.Fatalf("bought notional after a failed submit = %v, want 0", got)
	}
}

func TestBuyCap_NoOptionMeansNoCap(t *testing.T) {
	sink, _ := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, nil)
	mustAdmit(t, sink, intent("AAAA", "buy", 30_000, 1))
	if got := sink.Snapshot().BoughtNotionalCapIDR; got != 0 {
		t.Fatalf("cap reported %v with no option, want 0 (off)", got)
	}
}

func TestBuyCap_OptionValidation(t *testing.T) {
	for _, tc := range []struct{ max, seed float64 }{{0, 0}, {-1, 0}, {1_000_000, -1}} {
		_, err := NewRiskSink(&recordingSink{}, SleeveEquityIDR(1), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil,
			WithMaxBoughtNotionalIDR(tc.max, tc.seed))
		if err == nil {
			t.Fatalf("max=%v seed=%v: want an error", tc.max, tc.seed)
		}
	}
}

func TestBuyCap_SnapshotReportsUsageAndCap(t *testing.T) {
	sink, _ := newCappedSink(t, 1_000_000, 100_000)
	mustAdmit(t, sink, intent("AAAA", "buy", 2_000, 1))
	snap := sink.Snapshot()
	if snap.BoughtNotionalIDR != 300_000 || snap.BoughtNotionalCapIDR != 1_000_000 {
		t.Fatalf("snapshot = %v / %v, want 300000 / 1000000", snap.BoughtNotionalIDR, snap.BoughtNotionalCapIDR)
	}
}
