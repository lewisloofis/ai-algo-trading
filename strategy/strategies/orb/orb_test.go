package orb

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"collector/feed"
	"strategy/engine"
)

// nsAt returns nanoseconds for a specific date + UTC time, used to build test
// event timestamps that represent known trading-session times.
func nsAt(year, month, day, hourUTC, minUTC int) int64 {
	t := time.Date(year, time.Month(month), day, hourUTC, minUTC, 0, 0, time.UTC)
	return t.UnixNano()
}

func liveNs(symbol string, price float64, ns int64) engine.MarketEvent {
	d := &feed.Decoded{Kind: "liveprice", Symbol: symbol, LastPrice: price}
	return engine.MarketEvent{RecvNs: ns, Kind: engine.EventLivePrice, Symbol: symbol, Decoded: d}
}

func openTestStore(t *testing.T) *engine.PostTradeStore {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.db")
	store, err := engine.OpenStore(path)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

func feedCh(evs ...engine.MarketEvent) <-chan engine.MarketEvent {
	ch := make(chan engine.MarketEvent, len(evs))
	for _, e := range evs {
		ch <- e
	}
	close(ch)
	return ch
}

// TestORBEndOfDayExit verifies that a position opened during the day is
// closed when a liveprice tick arrives at or after session close (08:49 UTC =
// 15:49 WIB), matching plan-10's "exit at last bar close" rule.
func TestORBEndOfDayExit(t *testing.T) {
	// Day: 2026-01-02
	orOpen := nsAt(2026, 1, 2, 2, 0)    // 09:00 WIB — opening price
	breakout := nsAt(2026, 1, 2, 2, 30) // 09:30 WIB — breakout entry
	midDay := nsAt(2026, 1, 2, 7, 0)    // 14:00 WIB — mid-day, should hold
	eod := nsAt(2026, 1, 2, 8, 49)      // 15:49 WIB — session close, should exit

	store := openTestStore(t)
	sink := engine.NewPaperSink()
	strat := New()

	events := feedCh(
		liveNs("AAA", 1000, orOpen),   // openPx = 1000, upper = 1005
		liveNs("AAA", 1006, breakout), // breakout (1006 > 1005) → enter long
		liveNs("AAA", 1007, midDay),   // mid-day, hold
		liveNs("AAA", 1010, eod),      // session end → exit
	)

	c, err := engine.Run(context.Background(), events, strat, sink, store, "orb")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if c.RoundTrips != 1 {
		t.Fatalf("RoundTrips = %d, want 1 (end-of-day exit)", c.RoundTrips)
	}
	if c.NetPnlBps >= 0 {
		// Gross should be positive (~39 bps before fees); net will be negative due
		// to fees, but the test mainly checks that exit fired at session close.
		t.Logf("NetPnlBps = %.2f (negative expected after 40bps fee)", c.NetPnlBps)
	}
}

// TestORBMidDayHolds verifies that a position is NOT exited before session
// close: the ORB holds all day, so mid-day price events should not trigger exit.
func TestORBMidDayHolds(t *testing.T) {
	orOpen := nsAt(2026, 1, 2, 2, 0)    // 09:00 WIB
	breakout := nsAt(2026, 1, 2, 2, 30) // 09:30 WIB → entry
	mid1 := nsAt(2026, 1, 2, 5, 0)      // 12:00 WIB (lunch) — should hold
	mid2 := nsAt(2026, 1, 2, 7, 30)     // 14:30 WIB — should hold
	// No session-end event: run ends with position still open.

	store := openTestStore(t)
	sink := engine.NewPaperSink()
	strat := New()

	events := feedCh(
		liveNs("AAA", 1000, orOpen),
		liveNs("AAA", 1006, breakout),
		liveNs("AAA", 1004, mid1), // adverse mid-day move, still holds
		liveNs("AAA", 1008, mid2), // positive mid-day, still holds
	)

	c, err := engine.Run(context.Background(), events, strat, sink, store, "orb")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if c.RoundTrips != 0 {
		t.Fatalf("RoundTrips = %d, want 0 (position held, no session-end event)", c.RoundTrips)
	}
}

// TestORBNewDayForceExit verifies the new-day guard: a position entered on
// day 1 that was not closed by session-end is force-exited on the first
// liveprice event of day 2.
func TestORBNewDayForceExit(t *testing.T) {
	// Day 1: entry only, no session-end event.
	orOpen1 := nsAt(2026, 1, 2, 2, 0)
	breakout1 := nsAt(2026, 1, 2, 2, 30)
	// Day 2: first event triggers new-day guard exit.
	orOpen2 := nsAt(2026, 1, 3, 2, 0)

	store := openTestStore(t)
	sink := engine.NewPaperSink()
	strat := New()

	events := feedCh(
		liveNs("AAA", 1000, orOpen1),   // day 1 opening
		liveNs("AAA", 1006, breakout1), // day 1 entry
		liveNs("AAA", 1012, orOpen2),   // day 2 start → new-day guard fires exit
	)

	c, err := engine.Run(context.Background(), events, strat, sink, store, "orb")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if c.RoundTrips != 1 {
		t.Fatalf("RoundTrips = %d, want 1 (new-day guard must close day-1 position)", c.RoundTrips)
	}
}

// TestORBNewDayResetsOR verifies that the opening range resets at the start of
// a new trading day so the strategy can enter a fresh trade on day 2.
func TestORBNewDayResetsOR(t *testing.T) {
	// Day 1: breakout and EOD exit.
	orOpen1 := nsAt(2026, 1, 2, 2, 0)
	breakout1 := nsAt(2026, 1, 2, 2, 30)
	eod1 := nsAt(2026, 1, 2, 8, 49)
	// Day 2: new OR window + second breakout.
	orOpen2 := nsAt(2026, 1, 3, 2, 0)
	breakout2 := nsAt(2026, 1, 3, 2, 30)
	eod2 := nsAt(2026, 1, 3, 8, 49)

	store := openTestStore(t)
	sink := engine.NewPaperSink()
	strat := New()

	events := feedCh(
		liveNs("AAA", 1000, orOpen1),   // day 1 open
		liveNs("AAA", 1006, breakout1), // day 1 entry (long above 1005)
		liveNs("AAA", 1010, eod1),      // day 1 exit
		liveNs("AAA", 2000, orOpen2),   // day 2 open — new OR at 2000
		liveNs("AAA", 2011, breakout2), // day 2 breakout (2011 > 2000*1.005=2010)
		liveNs("AAA", 2020, eod2),      // day 2 exit
	)

	c, err := engine.Run(context.Background(), events, strat, sink, store, "orb")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if c.RoundTrips != 2 {
		t.Fatalf("RoundTrips = %d, want 2 (one per day)", c.RoundTrips)
	}
}

// TestORBEntryOrderIDTiming verifies the entry-order-ID bug fix: st.entryPx
// must be set by OnFill, which fires synchronously inside SubmitOrder. The
// exit intent must use the actual fill price (not zero).
//
// The PaperSink only seeds a book touch from a liveprice when that side has
// not yet been set. After the opening liveprice at 1000, bid=ask=1000; the
// breakout liveprice at 1006 does not update the book (both sides already set).
// So the entry fill happens at ask=1000, and st.entryPx = 1000. The test
// verifies that st.entryPx is non-zero (bug fix holds) and equals 1000 (the
// actual entry fill price, not the breakout reference price).
func TestORBEntryOrderIDTiming(t *testing.T) {
	orOpen := nsAt(2026, 1, 2, 2, 0)
	breakout := nsAt(2026, 1, 2, 2, 30)
	eod := nsAt(2026, 1, 2, 8, 49)

	store := openTestStore(t)
	sink := engine.NewPaperSink()
	strat := New()

	events := feedCh(
		liveNs("AAA", 1000, orOpen),   // seeds bid=ask=1000
		liveNs("AAA", 1006, breakout), // breakout; fills at ask=1000 (book unchanged)
		liveNs("AAA", 1010, eod),      // session end exit
	)

	_, err := engine.Run(context.Background(), events, strat, sink, store, "orb")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}

	// The exit order intent's price_idr must be the entry fill price (1000).
	// A zero value means the timing bug is still present (st.entryPx was never
	// set because OnFill fired before st.entryOrder was assigned).
	var exitPriceIDR int64
	row := store.QueryRow(
		`SELECT price_idr FROM order_intent WHERE side='sell' LIMIT 1`,
	)
	if err := row.Scan(&exitPriceIDR); err != nil {
		t.Fatalf("scan exit intent: %v", err)
	}
	if exitPriceIDR == 0 {
		t.Fatal("exit intent price_idr = 0: entry order ID timing bug still present")
	}
	if exitPriceIDR != 1000 {
		t.Fatalf("exit intent price_idr = %d, want 1000 (entry fill price at initial ask)", exitPriceIDR)
	}
}

// recordingPaperSink records every intent while still filling it, by
// embedding the real PaperSink — so engine.Run's fillProvider and observer
// assertions still find their methods and the run behaves exactly as it
// otherwise would.
type recordingPaperSink struct {
	*engine.PaperSink
	intents []engine.OrderIntent
}

func (s *recordingPaperSink) Submit(ctx context.Context, in engine.OrderIntent) (engine.OrderID, error) {
	s.intents = append(s.intents, in)
	return s.PaperSink.Submit(ctx, in)
}

// TestORBDeclaresTheRegularBoard pins the board type on the intents this
// strategy emits. engine.OrderIntent.BoardType has no safe default —
// strategy/risk rejects the zero value rather than assuming RG (reference
// 06 §8) — so a strategy that stops setting it stops trading, and it should
// fail here rather than at the first live order.
func TestORBDeclaresTheRegularBoard(t *testing.T) {
	orOpen := nsAt(2026, 1, 2, 2, 0)
	breakout := nsAt(2026, 1, 2, 2, 30)
	eod := nsAt(2026, 1, 2, 8, 49)

	store := openTestStore(t)
	sink := &recordingPaperSink{PaperSink: engine.NewPaperSink()}

	events := feedCh(
		liveNs("AAA", 1000, orOpen),
		liveNs("AAA", 1006, breakout), // entry
		liveNs("AAA", 1010, eod),      // exit
	)
	if _, err := engine.Run(context.Background(), events, New(), sink, store, "orb"); err != nil {
		t.Fatalf("Run: %v", err)
	}

	if len(sink.intents) != 2 {
		t.Fatalf("recorded %d intents, want 2 (entry + exit) — the assertions below would be vacuous", len(sink.intents))
	}
	for i, in := range sink.intents {
		if in.BoardType != engine.BoardRG {
			t.Errorf("intent %d (%s %s) BoardType = %q, want %q", i, in.Side, in.Symbol, in.BoardType, engine.BoardRG)
		}
	}
}
