package account

import (
	"context"
	"errors"
	"net/http"
	"path/filepath"
	"testing"
	"time"

	"strategy/engine"
	"strategy/risk"
)

// dailyPnLPortfolioJSON returns a Carina-shaped envelope with one position:
// 10 lots (1000 shares) with average cost-incl-fee 905/share and a latest
// price of 1000/share, so unrealized PnL is (1000*1000) - (905*1000) =
// 95,000 IDR — a fixed, easy-to-check figure for the "unrealized" half of
// DailyPnLIDR.
const dailyPnLPortfolioJSON = `{"data": {
	"summary": {},
	"results": [{
		"symbol": "BBCA",
		"qty": {"balance": {"lot": 10, "share": 1000}},
		"price": {"latest": 1000, "average": {"price": 900, "fee": 905}}
	}],
	"day_trade": []
}}`

// emptyPortfolioJSON is a well-formed, flat (no holdings) portfolio — see
// decodeEnvelope's doc comment on why an empty results is not an error.
const emptyPortfolioJSON = `{"data": {"summary": {}, "results": [], "day_trade": []}}`

// dailyPnLTestStore opens a temp-file PostTradeStore for one test, closing it
// on cleanup — mirrors engine's own newTestStore helper (unexported, so not
// reusable directly from this package).
func dailyPnLTestStore(t *testing.T) *engine.PostTradeStore {
	t.Helper()
	store, err := engine.OpenStore(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

// fixedClock returns a func() time.Time frozen at a WIB-safe midday moment
// on 2026-09-15 (10:00 UTC = 17:00 WIB, comfortably inside the same
// calendar date on both sides of the conversion — no midnight-boundary
// ambiguity to worry about here, since that boundary is JakartaDate's own
// concern and is exercised by engine's tests).
func fixedClock() time.Time {
	return time.Date(2026, 9, 15, 10, 0, 0, 0, time.UTC)
}

const testAsOfDate = "2026-09-15"

// insertClosedRoundTrip writes one closed round-trip into store: an
// order_intent + fill (the entry leg) plus the attribution row that closes
// it, under sinkKind and asOfDate. netPnlBps is the round-trip's net return;
// the entry notional is fixed at priceIDR*lots*100 shares/lot.
func insertClosedRoundTrip(t *testing.T, store *engine.PostTradeStore, seq *int64, sinkKind, symbol string, priceIDR int64, lots int, netPnlBps float64, asOfDate string) {
	t.Helper()
	*seq++
	orderID := symbol + "-entry"
	const recvNs = 100
	if err := store.InsertIntent(engine.IntentRow{
		Seq: *seq, RecvNs: recvNs, StrategyID: "test", Symbol: symbol, Side: "buy",
		PriceIDR: priceIDR, Lots: lots, OrderID: orderID, SinkKind: sinkKind,
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	*seq++
	if err := store.InsertFill(engine.FillRow{
		Seq: *seq, RecvNs: recvNs, OrderID: orderID, FillPriceIDR: priceIDR, FillLots: lots,
		SinkKind: sinkKind,
	}); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}
	*seq++
	if err := store.InsertAttribution(engine.AttributionRow{
		Seq: *seq, Symbol: symbol, StrategyID: "test", EntryRecvNs: recvNs, ExitRecvNs: recvNs + 100,
		GrossPnlBps: netPnlBps, NetPnlBps: netPnlBps, EntryOrderID: orderID, ExitOrderID: symbol + "-exit",
		HoldCalendarDays: 1, AsOfDate: asOfDate,
	}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}
}

func TestNewDailyPnL_RequiresClient(t *testing.T) {
	store := dailyPnLTestStore(t)
	if _, err := NewDailyPnL(nil, store, "live", DefaultFeeSchedule, fixedClock); err == nil {
		t.Fatal("NewDailyPnL(nil client) = nil error, want error")
	}
}

func TestNewDailyPnL_RequiresStore(t *testing.T) {
	c := newTestClient(t, "http://unused.invalid", &fakeTokenSource{token: "t"}, http.DefaultClient)
	if _, err := NewDailyPnL(c, nil, "live", DefaultFeeSchedule, fixedClock); err == nil {
		t.Fatal("NewDailyPnL(nil store) = nil error, want error")
	}
}

func TestNewDailyPnL_RequiresSinkKind(t *testing.T) {
	c := newTestClient(t, "http://unused.invalid", &fakeTokenSource{token: "t"}, http.DefaultClient)
	store := dailyPnLTestStore(t)
	if _, err := NewDailyPnL(c, store, "", DefaultFeeSchedule, fixedClock); err == nil {
		t.Fatal("NewDailyPnL(empty sinkKind) = nil error, want error")
	}
}

func TestNewDailyPnL_DefaultsClockToTimeNow(t *testing.T) {
	c := newTestClient(t, "http://unused.invalid", &fakeTokenSource{token: "t"}, http.DefaultClient)
	store := dailyPnLTestStore(t)
	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, nil)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	if d.now == nil {
		t.Fatal("now was not defaulted when clock was nil")
	}
}

func TestDailyPnLIDR_RealizedOnly_FlatBook(t *testing.T) {
	store := dailyPnLTestStore(t)
	var seq int64
	// 10 lots @ 9000 -> notional 9,000,000; 80bps net -> 72,000 IDR realized.
	insertClosedRoundTrip(t, store, &seq, "live", "BBCA", 9000, 10, 80, testAsOfDate)

	srv := serveJSON(t, http.StatusOK, emptyPortfolioJSON)
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, fixedClock)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	got, err := d.DailyPnLIDR(context.Background())
	if err != nil {
		t.Fatalf("DailyPnLIDR: %v", err)
	}
	if want := 72000.0; got != want {
		t.Errorf("DailyPnLIDR = %v, want %v", got, want)
	}
}

func TestDailyPnLIDR_UnrealizedOnly_NoClosedTrips(t *testing.T) {
	store := dailyPnLTestStore(t)

	srv := serveJSON(t, http.StatusOK, dailyPnLPortfolioJSON)
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, fixedClock)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	got, err := d.DailyPnLIDR(context.Background())
	if err != nil {
		t.Fatalf("DailyPnLIDR: %v", err)
	}
	// unrealized = (1000 shares * 1000 latest) - (1000 shares * 905 avg incl fee) = 95,000
	if want := 95000.0; got != want {
		t.Errorf("DailyPnLIDR = %v, want %v", got, want)
	}
}

func TestDailyPnLIDR_CombinesRealizedAndUnrealized(t *testing.T) {
	store := dailyPnLTestStore(t)
	var seq int64
	insertClosedRoundTrip(t, store, &seq, "live", "BBCA", 9000, 10, 80, testAsOfDate)

	srv := serveJSON(t, http.StatusOK, dailyPnLPortfolioJSON)
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, fixedClock)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	got, err := d.DailyPnLIDR(context.Background())
	if err != nil {
		t.Fatalf("DailyPnLIDR: %v", err)
	}
	if want := 72000.0 + 95000.0; got != want {
		t.Errorf("DailyPnLIDR = %v, want %v", got, want)
	}
}

func TestDailyPnLIDR_PaperRunNotCountedWhenScopedToLive(t *testing.T) {
	store := dailyPnLTestStore(t)
	var seq int64
	// Closed under "paper" — must not count toward a "live"-scoped provider.
	insertClosedRoundTrip(t, store, &seq, "paper", "BBCA", 9000, 10, 80, testAsOfDate)

	srv := serveJSON(t, http.StatusOK, emptyPortfolioJSON)
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, fixedClock)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	got, err := d.DailyPnLIDR(context.Background())
	if err != nil {
		t.Fatalf("DailyPnLIDR: %v", err)
	}
	if got != 0 {
		t.Errorf("DailyPnLIDR = %v, want 0 (paper-run round-trip must be excluded from a live-scoped provider)", got)
	}
}

func TestDailyPnLIDR_ExcludesOtherAsOfDate(t *testing.T) {
	store := dailyPnLTestStore(t)
	var seq int64
	insertClosedRoundTrip(t, store, &seq, "live", "BBCA", 9000, 10, 80, "2026-09-14")

	srv := serveJSON(t, http.StatusOK, emptyPortfolioJSON)
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, fixedClock)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	got, err := d.DailyPnLIDR(context.Background())
	if err != nil {
		t.Fatalf("DailyPnLIDR: %v", err)
	}
	if got != 0 {
		t.Errorf("DailyPnLIDR = %v, want 0 (round-trip closed on a different as_of_date than the clock's today)", got)
	}
}

func TestDailyPnLIDR_PortfolioUnauthorizedPropagatesNotZero(t *testing.T) {
	store := dailyPnLTestStore(t)

	srv := serveJSON(t, http.StatusUnauthorized, `{}`)
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "bad-token"}, srv.Client())

	d, err := NewDailyPnL(c, store, "live", DefaultFeeSchedule, fixedClock)
	if err != nil {
		t.Fatalf("NewDailyPnL: %v", err)
	}
	got, err := d.DailyPnLIDR(context.Background())
	if err == nil {
		t.Fatalf("DailyPnLIDR = %v, nil error; want ErrUnauthorized", got)
	}
	if !errors.Is(err, ErrUnauthorized) {
		t.Errorf("DailyPnLIDR error = %v, want it to wrap ErrUnauthorized", err)
	}
	if got != 0 {
		t.Errorf("DailyPnLIDR = %v on error, want the zero value to accompany a non-nil error only", got)
	}
}

// Compile-time assertion that *DailyPnL structurally satisfies
// risk.PnLProvider, without account importing strategy/risk itself (see
// DailyPnL's doc comment for why that import stays one-way).
var _ risk.PnLProvider = (*DailyPnL)(nil)
