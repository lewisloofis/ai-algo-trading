package rebalance

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"

	"collector/calendar"
	"strategy/engine"
	"strategy/live"
	"strategy/portfolio"
	"strategy/risk"
)

// fakeCarina is a minimal, stateful stand-in for Carina's order-mutation
// endpoints, reachable ONLY via httptest.NewServer. This test never
// addresses a real Stockbit/Carina host — see the repo-wide hard rule
// against that. It exists to prove, at the actual HTTP boundary, that a
// same-day restart submits a given order at most once: the strongest place
// to check, because a bug anywhere upstream (attempted-window gating,
// ui_ref determinism, Carina's own ui_ref dedup) would still show up here
// as a second POST /order/v2/buy.
type fakeCarina struct {
	mu     sync.Mutex
	buys   []map[string]any // one entry per POST /order/v2/buy received
	nextID int
	orders []live.Snapshot // fed back by GET /order/v2/list
}

func (f *fakeCarina) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/order/v2/buy", f.handleBuy)
	mux.HandleFunc("/order/v2/list", f.handleList)
	return mux
}

func (f *fakeCarina) handleBuy(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	f.mu.Lock()
	f.nextID++
	id := strconv.Itoa(f.nextID)
	f.buys = append(f.buys, body)
	symbol, _ := body["symbol"].(string)
	priceF, _ := body["price"].(float64)
	sharesLots, _ := strconv.ParseFloat(anyToString(body["shares"]), 64)
	sharesLots /= 100
	f.orders = append(f.orders, live.Snapshot{
		OrderID:    id,
		Symbol:     symbol,
		Side:       "SIDE_BUY",
		StatusText: "OPEN",
		Qty:        live.SnapshotQty{LotDone: 0, LotOpen: sharesLots, LotTotal: sharesLots},
		Price:      live.SnapshotPrice{Order: priceF},
	})
	f.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"order_id": id}})
}

func anyToString(v any) string {
	s, _ := v.(string)
	return s
}

func (f *fakeCarina) handleList(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	// json.Marshal(nil slice) -> "null", and decodeEnvelope (both
	// strategy/live's and strategy/account's) treats an explicit null data
	// field as an error, not an empty result — see decodeEnvelope's doc
	// comment. A non-nil empty slice keeps "no orders yet" a legitimately
	// empty 200, matching what the real endpoint would send.
	orders := f.orders
	if orders == nil {
		orders = []live.Snapshot{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"data": orders})
}

func (f *fakeCarina) buyCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.buys)
}

// restartFakeTradingToken satisfies live.TokenSource with a fixed value —
// the fake server never validates it.
type restartFakeTradingToken struct{}

func (restartFakeTradingToken) TradingToken() string { return "test-trading-token" }

// armedLiveClient builds a live.Client against srv, arming it via the
// documented double-arm gate (ALGO_LIVE + ALGO_CONFIRM) — set only in this
// process's environment for the duration of the test (t.Setenv restores it
// afterward) and never against anything but srv, an httptest server.
func armedLiveClient(t *testing.T, srv *httptest.Server) *live.Client {
	t.Helper()
	t.Setenv("ALGO_LIVE", "true")
	t.Setenv("ALGO_CONFIRM", live.LiveConfirmPhrase)
	c, err := live.NewClient(srv.URL, "008488L", restartFakeTradingToken{}, srv.Client(), live.ArmingFromEnv())
	if err != nil {
		t.Fatalf("live.NewClient: %v", err)
	}
	return c
}

// TestRestartDurability_CrashThenRestart_DoesNotDoubleSubmit is plan 18 §5
// B4's idempotency-gap proof: run 1 places one order for TINS and then
// "crashes" (the process simply stops — no Close, no ReconcileTerminal, no
// graceful anything, the way a real crash would). A restart is simulated
// with entirely fresh in-memory state (a new Strategy, a new live.Sink, a
// new risk.RiskSink) but the SAME fake Carina and a NEW store file (see
// engine.PostTradeStore's own "one store file per run" convention) seeded
// from the FIRST run's store via AttemptedWindows. The restarted run
// processes the identical trading-day event and must not place a second
// order.
func TestRestartDurability_CrashThenRestart_DoesNotDoubleSubmit(t *testing.T) {
	asOf := time.Date(2026, 9, 16, 9, 0, 0, 0, calendar.Jakarta)
	base, err := portfolio.NullPortfolio(portfolio.NullOptions{
		AsOf:            asOf,
		Names:           []string{"TINS"},
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}

	carina := &fakeCarina{}
	srv := httptest.NewServer(carina.handler())
	t.Cleanup(srv.Close)

	limits := risk.Limits{
		PerOrderNotionalFraction:   1,
		PerDayNotionalFraction:     1,
		MaxGrossExposureFraction:   1,
		MaxPositionPerNameFraction: 1,
		DailyLossLimitFraction:     1,
	}
	dir := t.TempDir()

	// --- Run 1: place the order, then crash (no clean shutdown at all). ---
	store1, err := engine.OpenStore(filepath.Join(dir, "run1.db"))
	if err != nil {
		t.Fatalf("OpenStore (run1): %v", err)
	}

	client1 := armedLiveClient(t, srv)
	sink1, err := live.NewSink(live.Options{Client: client1})
	if err != nil {
		t.Fatalf("live.NewSink (run1): %v", err)
	}
	if err := sink1.Baseline(context.Background()); err != nil {
		t.Fatalf("Baseline (run1): %v", err)
	}

	rs1, err := risk.NewRiskSink(sink1, risk.SleeveEquityIDR(15_000_000), limits, chainAllowGate{}, chainZeroPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink (run1): %v", err)
	}

	strat1, err := New(Options{Portfolio: base, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New (run1): %v", err)
	}

	events1 := chainFeed(chainLiveEvent("TINS", 1003, chainNs(9, 5, 0)))
	c1, err := engine.Run(context.Background(), events1, strat1, rs1.EngineSink(), store1, strat1.StrategyID())
	if err != nil {
		t.Fatalf("Run (run1): %v", err)
	}
	if c1.OrdersAttempted != 1 {
		t.Fatalf("run1: OrdersAttempted = %d, want 1", c1.OrdersAttempted)
	}
	if strat1.Rejections() != 0 {
		t.Fatalf("run1: Rejections() = %d, want 0", strat1.Rejections())
	}
	if got := carina.buyCount(); got != 1 {
		t.Fatalf("run1: fake Carina saw %d buy POST(s), want 1", got)
	}
	// Simulate a crash: no sink1.Close(), no ReconcileTerminal. Just stop.
	if err := store1.Close(); err != nil {
		t.Fatalf("close store1: %v", err)
	}

	// --- Restart: fresh Strategy/Sink/RiskSink, recovery state read from
	// run 1's own store file, a NEW store file for this run. ---
	priorStore, err := engine.OpenStore(filepath.Join(dir, "run1.db"))
	if err != nil {
		t.Fatalf("re-open run1.db for recovery: %v", err)
	}
	attempted, err := priorStore.AttemptedWindows(base.StrategyID, "live")
	if err != nil {
		t.Fatalf("AttemptedWindows: %v", err)
	}
	if err := priorStore.Close(); err != nil {
		t.Fatalf("close priorStore: %v", err)
	}
	if len(attempted) != 1 {
		t.Fatalf("AttemptedWindows = %v, want exactly 1 entry recovered from run1", attempted)
	}

	store2, err := engine.OpenStore(filepath.Join(dir, "run2.db"))
	if err != nil {
		t.Fatalf("OpenStore (run2): %v", err)
	}
	defer store2.Close()

	client2 := armedLiveClient(t, srv)
	sink2, err := live.NewSink(live.Options{Client: client2})
	if err != nil {
		t.Fatalf("live.NewSink (run2): %v", err)
	}
	if err := sink2.Baseline(context.Background()); err != nil {
		t.Fatalf("Baseline (run2): %v", err)
	}

	rs2, err := risk.NewRiskSink(sink2, risk.SleeveEquityIDR(15_000_000), limits, chainAllowGate{}, chainZeroPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink (run2): %v", err)
	}

	strat2, err := New(Options{Portfolio: base, Schedule: DailyAt(9, 0), Attempted: attempted})
	if err != nil {
		t.Fatalf("New (run2): %v", err)
	}

	// A later tick on the SAME trading day (day offset 0, like run1) — the
	// restarted process picking back up mid-session.
	events2 := chainFeed(chainLiveEvent("TINS", 1003, chainNs(9, 6, 0)))
	c2, err := engine.Run(context.Background(), events2, strat2, rs2.EngineSink(), store2, strat2.StrategyID())
	if err != nil {
		t.Fatalf("Run (run2): %v", err)
	}

	if c2.OrdersAttempted != 0 {
		t.Fatalf("run2 (restart): OrdersAttempted = %d, want 0 — TINS was already attempted today", c2.OrdersAttempted)
	}
	if got := carina.buyCount(); got != 1 {
		t.Fatalf("after restart: fake Carina saw %d buy POST(s) total, want 1 (no double submit)", got)
	}
}
