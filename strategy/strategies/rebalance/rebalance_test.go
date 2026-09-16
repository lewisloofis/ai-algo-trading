package rebalance

import (
	"context"
	"errors"
	"testing"
	"time"

	"collector/calendar"
	"collector/feed"
	"strategy/engine"
	"strategy/portfolio"
)

// fakeEngine is a minimal engine.Engine for unit-testing Strategy.OnEvent in
// isolation, without going through engine.Run or a real sink — the chain
// test (chain_test.go) covers the real wiring end-to-end. calls records
// every SubmitOrder invocation regardless of outcome (used to assert "one
// attempt per window, whatever happens"); accepted records only the ones
// this fake let through. reject names symbols this fake rejects, mimicking
// a risk-gate or broker refusal.
type fakeEngine struct {
	now      time.Time
	calls    []engine.OrderIntent
	accepted []engine.OrderIntent
	reject   map[string]bool
}

func (f *fakeEngine) Now() time.Time { return f.now }

func (f *fakeEngine) SubmitOrder(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	f.calls = append(f.calls, intent)
	if f.reject[intent.Symbol] {
		return "", errors.New("fakeEngine: rejected")
	}
	f.accepted = append(f.accepted, intent)
	return engine.OrderID(intent.OrderID), nil
}

// liveEvent builds a liveprice MarketEvent for symbol at price.
func liveEvent(symbol string, price float64) engine.MarketEvent {
	d := &feed.Decoded{Kind: "liveprice", Symbol: symbol, LastPrice: price}
	return engine.MarketEvent{Kind: engine.EventLivePrice, Symbol: symbol, Decoded: d}
}

// wibTime is a shorthand for a WIB wall-clock time on a fixed test date.
func wibTime(hour, min int) time.Time {
	return time.Date(2026, 9, 16, hour, min, 0, 0, calendar.Jakarta)
}

// nullPortfolio builds a valid equal-weight portfolio over names, at
// baseNotional, using DailyAt(9,0)'s own opening date as as_of. Tests that
// need a hand-shaped (non-equal-weight, or oddly-valued) portfolio build
// one directly instead.
func nullPortfolio(t *testing.T, names []string, baseNotional int64) portfolio.TargetPortfolio {
	t.Helper()
	p, err := portfolio.NullPortfolio(portfolio.NullOptions{
		AsOf:            wibTime(9, 0),
		Names:           names,
		BaseNotionalIDR: baseNotional,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	return p
}

func TestReachesTargetsFromFlat(t *testing.T) {
	// Five names, equal weight 0.2 each, base notional 100,000,000 IDR ->
	// 20,000,000 IDR target per name. Prices are chosen already on-tick so
	// this test isolates the lot-sizing arithmetic from tick snapping
	// (covered separately in TestOnEvent_EmitsTickSnappedPricesAcrossBands).
	names := []string{"AAA", "BBB", "CCC", "DDD", "EEE"}
	p := nullPortfolio(t, names, 100_000_000)

	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	prices := map[string]float64{
		"AAA": 1000, // tick 5  -> lots = floor(20,000,000/100,000) = 200
		"BBB": 2500, // tick 10 -> lots = floor(20,000,000/250,000) = 80
		"CCC": 6000, // tick 25 -> lots = floor(20,000,000/600,000) = 33
		"DDD": 300,  // tick 2  -> lots = floor(20,000,000/30,000)  = 666
		"EEE": 150,  // tick 1  -> lots = floor(20,000,000/15,000)  = 1333
	}
	wantLots := map[string]int{"AAA": 200, "BBB": 80, "CCC": 33, "DDD": 666, "EEE": 1333}

	fe := &fakeEngine{now: wibTime(9, 5)}
	for _, sym := range names {
		strat.OnEvent(context.Background(), liveEvent(sym, prices[sym]), fe)
	}

	if len(fe.accepted) != len(names) {
		t.Fatalf("accepted = %d orders, want %d", len(fe.accepted), len(names))
	}
	byName := make(map[string]engine.OrderIntent, len(fe.accepted))
	for _, in := range fe.accepted {
		byName[in.Symbol] = in
	}
	for _, sym := range names {
		in, ok := byName[sym]
		if !ok {
			t.Fatalf("no order submitted for %s", sym)
		}
		if in.Side != "buy" {
			t.Errorf("%s: side = %q, want buy", sym, in.Side)
		}
		if in.Lots != wantLots[sym] {
			t.Errorf("%s: lots = %d, want %d", sym, in.Lots, wantLots[sym])
		}
		if in.BoardType != engine.BoardRG {
			t.Errorf("%s: board = %q, want %q", sym, in.BoardType, engine.BoardRG)
		}
		if in.PriceIDR != int64(prices[sym]) {
			t.Errorf("%s: price = %d, want %d (already on-tick)", sym, in.PriceIDR, int64(prices[sym]))
		}
	}
}

func TestIdempotentWithinWindow(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5)}
	// Three events in the same window, two at the same price and one at a
	// different price: only the first may ever produce an attempt.
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	strat.OnEvent(context.Background(), liveEvent("AAA", 1005), fe)

	if len(fe.calls) != 1 {
		t.Fatalf("SubmitOrder called %d times, want exactly 1", len(fe.calls))
	}
}

func TestNewWindowProducesFreshAttempt(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	// Window 1: the fake rejects AAA, so the attempt is made and consumed,
	// but nothing is confirmed or in flight afterward.
	fe := &fakeEngine{now: wibTime(9, 5), reject: map[string]bool{"AAA": true}}
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	if len(fe.calls) != 1 || len(fe.accepted) != 0 {
		t.Fatalf("window 1: calls=%d accepted=%d, want 1 and 0", len(fe.calls), len(fe.accepted))
	}

	// Window 2 (a new calendar date): the same symbol, still with the same
	// unmet target, must be attempted again rather than staying skipped
	// because of window 1's dedup entry.
	fe2 := &fakeEngine{now: wibTime(9, 5).AddDate(0, 0, 1)}
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe2)
	if len(fe2.calls) != 1 || len(fe2.accepted) != 1 {
		t.Fatalf("window 2: calls=%d accepted=%d, want 1 and 1 (fresh attempt, this time accepted)", len(fe2.calls), len(fe2.accepted))
	}
}

func TestLatePriceStillTradesSameWindow(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA", "BBB"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	now := wibTime(9, 5)
	window := strat.schedule.Window(now)

	// Event 1: only BBB has ever ticked. AAA has no price yet and must be
	// skipped WITHOUT being marked attempted, so a later event in this same
	// window still tries it — the rule this test exists to pin.
	fe := &fakeEngine{now: now}
	strat.OnEvent(context.Background(), liveEvent("BBB", 2500), fe)

	if len(fe.calls) != 1 || fe.calls[0].Symbol != "BBB" {
		t.Fatalf("after event 1: calls = %+v, want exactly one call for BBB", fe.calls)
	}
	if strat.attempted[window+"|AAA"] {
		t.Fatalf("AAA marked attempted after an event where it had no price yet")
	}

	// Event 2: AAA's price finally arrives, same window.
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)

	if len(fe.calls) != 2 {
		t.Fatalf("after event 2: calls = %d, want 2 (AAA retried and attempted)", len(fe.calls))
	}
	if fe.calls[1].Symbol != "AAA" {
		t.Fatalf("second call = %+v, want AAA", fe.calls[1])
	}
	if !strat.attempted[window+"|AAA"] {
		t.Fatalf("AAA not marked attempted after its price arrived and it was attempted")
	}
}

func TestZeroDeltaEmitsNoOrder(t *testing.T) {
	names := []string{"AAA"}
	p := nullPortfolio(t, names, 100_000_000)
	// weight 1.0 at 100,000,000 base, price 1000 -> target lots = floor(100,000,000/100,000) = 1000.
	strat, err := New(Options{
		Portfolio: p,
		Schedule:  DailyAt(9, 0),
		Initial:   map[string]int{"AAA": 1000},
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5)}
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)

	if len(fe.calls) != 0 {
		t.Fatalf("calls = %+v, want none (delta is already zero)", fe.calls)
	}
}

func TestFlattenedTargetsProduceSellsToZero(t *testing.T) {
	names := []string{"AAA", "BBB", "CCC", "DDD", "EEE"}
	base := nullPortfolio(t, names, 100_000_000)
	flat := base.Flattened(wibTime(9, 0).AddDate(0, 0, 1), wibTime(15, 0).AddDate(0, 0, 1))

	initial := map[string]int{"AAA": 50, "BBB": 50, "CCC": 50, "DDD": 50, "EEE": 50}
	strat, err := New(Options{Portfolio: flat, Schedule: DailyAt(9, 0), Initial: initial})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5).AddDate(0, 0, 1)}
	for _, sym := range names {
		strat.OnEvent(context.Background(), liveEvent(sym, 1000), fe)
	}

	if len(fe.accepted) != len(names) {
		t.Fatalf("accepted = %d, want %d", len(fe.accepted), len(names))
	}
	for _, in := range fe.accepted {
		if in.Side != "sell" {
			t.Errorf("%s: side = %q, want sell", in.Symbol, in.Side)
		}
		if in.Lots != initial[in.Symbol] {
			t.Errorf("%s: lots = %d, want %d (the whole initial position)", in.Symbol, in.Lots, initial[in.Symbol])
		}
		// Simulate the fill so Positions() reflects the round trip's end state.
		strat.OnFill(context.Background(), engine.FillRecord{
			OrderID:      engine.OrderID(in.OrderID),
			FillPriceIDR: in.PriceIDR,
			FillLots:     in.Lots,
		})
	}

	pos := strat.Positions()
	for _, sym := range names {
		if pos[sym] != 0 {
			t.Errorf("%s: position after flatten = %d, want 0", sym, pos[sym])
		}
	}
}

func TestRejectedSubmitDoesNotAdvanceInFlightOrRetry(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5), reject: map[string]bool{"AAA": true}}
	// Two events in the same window: the reject must not trigger a retry.
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)
	strat.OnEvent(context.Background(), liveEvent("AAA", 1000), fe)

	if len(fe.calls) != 1 {
		t.Fatalf("calls = %d, want exactly 1 (no retry after rejection)", len(fe.calls))
	}
	if strat.Rejections() != 1 {
		t.Fatalf("Rejections() = %d, want 1", strat.Rejections())
	}
	if got := strat.inFlight["AAA"]; got != 0 {
		t.Fatalf("inFlight[AAA] = %d, want 0 (a rejected submit must not reserve exposure)", got)
	}
	if pos := strat.Positions()["AAA"]; pos != 0 {
		t.Fatalf("Positions()[AAA] = %d, want 0", pos)
	}
}

func TestOnFillUnknownOrderIDIsNoOp(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	// No SubmitOrder has ever been made, so every order id is unknown.
	strat.OnFill(context.Background(), engine.FillRecord{
		OrderID:      "never-submitted",
		FillPriceIDR: 1000,
		FillLots:     10,
	})

	pos := strat.Positions()
	if len(pos) != 0 {
		t.Fatalf("Positions() = %+v, want empty (unknown fill must be ignored)", pos)
	}
}

// TestOnEvent_EmitsTickSnappedPricesAcrossBands sweeps one representative,
// deliberately off-tick price per pricing band (docs/reference/06-pre-trade-
// rules.md §5) through the real OnEvent path and asserts every emitted
// intent price lands on that band's tick — proving rebalanceOne actually
// calls snapTickIDR rather than forwarding the raw observed price (a
// mutation that skips the call would still pass a test that only exercised
// snapTickIDR directly).
func TestOnEvent_EmitsTickSnappedPricesAcrossBands(t *testing.T) {
	type band struct {
		symbol    string
		price     float64
		wantPrice int64
	}
	bands := []band{
		{"P1", 187, 187},     // <200, tick 1: already on tick
		{"P2", 483, 482},     // <500, tick 2: 483 % 2 == 1 -> 482
		{"P3", 1234, 1230},   // <2000, tick 5: 1234 % 5 == 4 -> 1230
		{"P4", 4567, 4560},   // <5000, tick 10: 4567 % 10 == 7 -> 4560
		{"P5", 12345, 12325}, // >=5000, tick 25: 12345 % 25 == 20 -> 12325
	}

	names := make([]string, len(bands))
	for i, b := range bands {
		names[i] = b.symbol
	}
	p := nullPortfolio(t, names, 500_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	fe := &fakeEngine{now: wibTime(9, 5)}
	for _, b := range bands {
		strat.OnEvent(context.Background(), liveEvent(b.symbol, b.price), fe)
	}

	if len(fe.accepted) != len(bands) {
		t.Fatalf("accepted = %d orders, want %d", len(fe.accepted), len(bands))
	}
	byName := make(map[string]engine.OrderIntent, len(fe.accepted))
	for _, in := range fe.accepted {
		byName[in.Symbol] = in
	}
	for _, b := range bands {
		in, ok := byName[b.symbol]
		if !ok {
			t.Fatalf("no order submitted for %s", b.symbol)
		}
		if in.PriceIDR != b.wantPrice {
			t.Errorf("%s: PriceIDR = %d, want %d (tick-snapped from raw %v)", b.symbol, in.PriceIDR, b.wantPrice, b.price)
		}
	}
}

func TestNew_RejectsBlankStrategyID(t *testing.T) {
	_, err := New(Options{
		Portfolio: portfolio.TargetPortfolio{}, // StrategyID is the zero value: ""
		Schedule:  DailyAt(9, 0),
	})
	if !errors.Is(err, ErrInvalidOptions) {
		t.Fatalf("err = %v, want wrapping ErrInvalidOptions", err)
	}
}

func TestNew_RejectsNilSchedule(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	_, err := New(Options{Portfolio: p, Schedule: nil})
	if !errors.Is(err, ErrInvalidOptions) {
		t.Fatalf("err = %v, want wrapping ErrInvalidOptions", err)
	}
}

func TestNew_RejectsPortfolioWithNoTargets(t *testing.T) {
	_, err := New(Options{
		Portfolio: portfolio.TargetPortfolio{StrategyID: "x"}, // Targets is nil
		Schedule:  DailyAt(9, 0),
	})
	if !errors.Is(err, ErrInvalidOptions) {
		t.Fatalf("err = %v, want wrapping ErrInvalidOptions", err)
	}
}

func TestNew_AllowsNilInitial(t *testing.T) {
	p := nullPortfolio(t, []string{"AAA"}, 100_000_000)
	strat, err := New(Options{Portfolio: p, Schedule: DailyAt(9, 0), Initial: nil})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if pos := strat.Positions(); len(pos) != 0 {
		t.Fatalf("Positions() = %+v, want empty when Initial is nil", pos)
	}
}

func TestDailyAt_OpensAtOrAfterCutoffWIB(t *testing.T) {
	sched := DailyAt(9, 0)

	cases := []struct {
		name string
		now  time.Time
		want string
	}{
		{"one minute before cutoff", wibTime(8, 59), ""},
		{"exactly at cutoff", wibTime(9, 0), "2026-09-16"},
		{"well after cutoff", wibTime(14, 30), "2026-09-16"},
		{"next calendar date", wibTime(9, 0).AddDate(0, 0, 1), "2026-09-17"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := sched.Window(c.now); got != c.want {
				t.Errorf("Window(%v) = %q, want %q", c.now, got, c.want)
			}
		})
	}
}
