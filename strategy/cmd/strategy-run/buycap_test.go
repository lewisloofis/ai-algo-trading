package main

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"strategy/engine"
	"strategy/live"
	"strategy/risk"
)

func writeLiveStore(t *testing.T, path string, recv int64, side string, price int64, lots int, orderID string) {
	t.Helper()
	s, err := engine.OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.InsertIntent(engine.IntentRow{Seq: 1, RecvNs: recv, StrategyID: "s", Symbol: "AAAA", Side: side,
		PriceIDR: price, Lots: lots, OrderID: orderID, SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}
}

func TestSeedBoughtToday_SumsEarlierStoresOfTheSameDay(t *testing.T) {
	dir := t.TempDir()
	now := time.Date(2026, 10, 15, 9, 5, 0, 0, time.FixedZone("WIB", 7*3600))
	writeLiveStore(t, filepath.Join(dir, "a1.db"), now.UnixNano(), "buy", 1_000, 3, "o1")                    // 300,000
	writeLiveStore(t, filepath.Join(dir, "a2.db"), now.UnixNano(), "buy", 500, 2, "o2")                      // 100,000
	writeLiveStore(t, filepath.Join(dir, "old.db"), now.Add(-48*time.Hour).UnixNano(), "buy", 9_000, 9, "x") // other day
	got, err := seedBoughtToday(filepath.Join(dir, "*.db"), "live", now)
	if err != nil {
		t.Fatalf("seedBoughtToday: %v", err)
	}
	if got != 400_000 {
		t.Fatalf("seed = %v, want 400000", got)
	}
}

func TestSeedBoughtToday_NoStoresIsZero(t *testing.T) {
	got, err := seedBoughtToday(filepath.Join(t.TempDir(), "*.db"), "live", time.Now())
	if err != nil || got != 0 {
		t.Fatalf("got %v, %v", got, err)
	}
}

func TestBuyCapOptions(t *testing.T) {
	if opts, err := buyCapOptions(0, "", "live", time.Now()); err != nil || len(opts) != 0 {
		t.Fatalf("cap 0 must be off: %v %v", opts, err)
	}
	if _, err := buyCapOptions(-5, "", "live", time.Now()); err == nil || !strings.Contains(err.Error(), "--max-buy-notional-idr") {
		t.Fatalf("negative cap must be refused, got %v", err)
	}
	opts, err := buyCapOptions(1_000_000, "", "live", time.Now())
	if err != nil || len(opts) != 1 {
		t.Fatalf("cap 1M: %v %v", opts, err)
	}
}

func TestBuyCapOptions_AlreadyOverTheCapStillStartsAndRefusesFurtherBuys(t *testing.T) {
	dir := t.TempDir()
	now := time.Now()
	writeLiveStore(t, filepath.Join(dir, "a1.db"), now.UnixNano(), "buy", 10_000, 2, "o1") // 2,000,000
	opts, err := buyCapOptions(1_000_000, filepath.Join(dir, "*.db"), "live", now)
	if err != nil || len(opts) != 1 {
		t.Fatalf("a spent day must still construct the cap (sells are uncapped): %v %v", opts, err)
	}
	sink, err := wrapWithRisk(engine.NewPaperSink(), 15_000_000, riskLimits(1, 1, 1, 1, 0.05), allowAllGate{}, zeroPnLProvider{}, "", nil, opts...)
	if err != nil {
		t.Fatal(err)
	}
	_, err = sink.Submit(context.Background(), engine.OrderIntent{Symbol: "AAAA", Side: "buy", PriceIDR: 1_000, Lots: 1, OrderID: "n1", BoardType: engine.BoardRG})
	if err == nil || !errors.Is(err, risk.ErrCapExceeded) {
		t.Fatalf("a buy on a spent day must be refused with ErrCapExceeded, got %v", err)
	}
}

func TestNotPlacedClassifier(t *testing.T) {
	live4xx := &live.APIError{Path: "/buy", Status: 400, Body: "x"}
	cases := []struct {
		name string
		sink string
		err  error
		want bool
	}{
		{"typed invalid parameter", "live", fmt.Errorf("w: %w", live.ErrInvalidParameter), true},
		{"suspended", "live", live.ErrUserSuspended, true},
		{"token rejected", "live", live.ErrUnauthorized, true},
		{"shutting down", "live", live.ErrShuttingDown, true},
		{"generic 400", "live", live4xx, true},
		{"422 wrapped", "live", fmt.Errorf("w: %w", &live.APIError{Status: 422}), true},
		{"408 timeout status", "live", &live.APIError{Status: 408}, false},
		{"500", "live", &live.APIError{Status: 500}, false},
		{"503", "live", &live.APIError{Status: 503}, false},
		{"deadline exceeded", "live", fmt.Errorf("w: %w", context.DeadlineExceeded), false},
		{"canceled", "live", context.Canceled, false},
		{"unknown transport error", "live", errors.New("connection reset by peer"), false},
		{"paper never places", "paper", errors.New("no book"), true},
	}
	for _, c := range cases {
		if got := notPlacedClassifier(c.sink)(c.err); got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, got, c.want)
		}
	}
}

// allowAllGate is a risk.MarketGate that allows everything (test only).
type allowAllGate struct{}

func (allowAllGate) State(context.Context, string, int64) (risk.MarketState, error) {
	return risk.MarketState{NotHoliday: risk.Allow, MarketOpen: risk.Allow, Tradable: risk.Allow, NotSuspended: risk.Allow, WithinBand: risk.Allow}, nil
}
