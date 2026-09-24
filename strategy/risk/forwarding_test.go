package risk

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"collector/feed"
	"strategy/engine"
)

// RiskSink's doc comment promises it "drops into engine.Run in place of the
// sink it wraps with no other change". engine.Run reaches past the
// engine.OrderSink interface twice, with optional-interface type assertions
// that a decorator silently fails:
//
//	engine.go:238  if obs, ok := sink.(observer); ok { obs.Observe(evt) }
//	engine.go:117  fp, ok := h.sink.(fillProvider); if !ok { return id, nil }
//
// Neither degradation surfaces as an error. Without Observe the wrapped
// PaperSink never sees a book, so every Submit fails ErrNoBook; without
// LastFill no fill row is ever written, Counters.Fills stays 0 and
// Strategy.OnFill never fires. Both read exactly like "the strategy found
// nothing to do today" — the failure shape this repo keeps designing
// against (see TriState's doc comment).
//
// These tests pin the forwarding contract end-to-end, through the real
// engine.Run, rather than by asserting on method sets: a structural
// assertion here would have to redeclare engine's unexported interfaces and
// would pass even if Run stopped consulting them.

func liveEvent(symbol string, price float64, recvNs int64) engine.MarketEvent {
	d := &feed.Decoded{Kind: "liveprice", Symbol: symbol, LastPrice: price}
	return engine.MarketEvent{RecvNs: recvNs, Kind: engine.EventLivePrice, Symbol: symbol, Decoded: d}
}

func feedEvents(evs ...engine.MarketEvent) <-chan engine.MarketEvent {
	ch := make(chan engine.MarketEvent, len(evs))
	for _, e := range evs {
		ch <- e
	}
	close(ch)
	return ch
}

func newTestStore(t *testing.T) *engine.PostTradeStore {
	t.Helper()
	store, err := engine.OpenStore(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

// submitOnSecond submits one admissible order on the second event and records
// whether the engine ever delivered a fill back.
type submitOnSecond struct {
	seen      int
	fills     int
	submitErr error
}

func (s *submitOnSecond) OnEvent(ctx context.Context, e engine.MarketEvent, eng engine.Engine) {
	s.seen++
	if s.seen != 2 {
		return
	}
	_, s.submitErr = eng.SubmitOrder(ctx, engine.OrderIntent{
		Symbol: e.Symbol, Side: "buy", PriceIDR: 100, Lots: 1,
		OrderID: "o1", BoardType: engine.BoardRG,
	})
}

func (s *submitOnSecond) OnFill(ctx context.Context, f engine.FillRecord) { s.fills++ }

// runThroughRiskSink wires PaperSink -> RiskSink -> engine.Run with every
// gate open, so the only thing that can stop a fill is the decorator itself.
func runThroughRiskSink(t *testing.T) (engine.Counters, *submitOnSecond) {
	t.Helper()
	store := newTestStore(t)
	paper := engine.NewPaperSink()
	rs, err := NewRiskSink(paper, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	strat := &submitOnSecond{}
	counters, err := engine.Run(context.Background(),
		feedEvents(liveEvent("AAA", 100, 1), liveEvent("AAA", 100, 2)),
		strat, rs.EngineSink(), store, "fwd")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return counters, strat
}

// The book must reach the wrapped sink, or every order dies ErrNoBook.
func TestRiskSink_ForwardsObserveToWrappedSink(t *testing.T) {
	_, strat := runThroughRiskSink(t)
	if strat.submitErr != nil {
		t.Fatalf("SubmitOrder failed — the wrapped PaperSink never observed a book: %v", strat.submitErr)
	}
}

// A fill the engine never learns about is a fill that never reaches the
// store, the counters, or the strategy.
func TestRiskSink_ForwardsLastFillToEngine(t *testing.T) {
	counters, strat := runThroughRiskSink(t)
	if counters.Fills != 1 {
		t.Errorf("Counters.Fills = %d, want 1 — engine.Run could not read the fill back through the decorator", counters.Fills)
	}
	if strat.fills != 1 {
		t.Errorf("Strategy.OnFill fired %d times, want 1", strat.fills)
	}
}

// The fill must be the wrapped sink's own fill, not a zero value that merely
// satisfies the interface.
func TestRiskSink_ForwardedFillCarriesRealValues(t *testing.T) {
	store := newTestStore(t)
	paper := engine.NewPaperSink()
	rs, err := NewRiskSink(paper, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	strat := &submitOnSecond{}
	if _, err := engine.Run(context.Background(),
		feedEvents(liveEvent("AAA", 100, 1), liveEvent("AAA", 100, 2)),
		strat, rs.EngineSink(), store, "fwd"); err != nil {
		t.Fatalf("Run: %v", err)
	}

	var fillPrice int64
	var lots int
	var fee int64
	if err := store.QueryRow(`SELECT fill_price_idr, fill_lots, fee_idr FROM fill WHERE order_id = 'o1'`).
		Scan(&fillPrice, &lots, &fee); err != nil {
		t.Fatalf("no fill row reached the store: %v", err)
	}
	if fillPrice != 100 || lots != 1 {
		t.Errorf("fill row = price %d lots %d, want 100/1", fillPrice, lots)
	}
	// 100 IDR * 1 lot * 100 shares = 10,000 notional; buy fee 15 bps = 15.
	if fee != 15 {
		t.Errorf("fee = %d, want 15 — a zero-value FillRecord would report 0", fee)
	}
}

// A rejected order must not manufacture a fill: the decorator returns before
// reaching the wrapped sink, so there is no LastFill to forward.
func TestRiskSink_RejectedOrderProducesNoFill(t *testing.T) {
	store := newTestStore(t)
	paper := engine.NewPaperSink()
	// A gate that denies everything: the order never reaches PaperSink.
	rs, err := NewRiskSink(paper, SleeveEquityIDR(15_000_000), permissiveLimits(), DenyAllGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	strat := &submitOnSecond{}
	counters, err := engine.Run(context.Background(),
		feedEvents(liveEvent("AAA", 100, 1), liveEvent("AAA", 100, 2)),
		strat, rs.EngineSink(), store, "fwd")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if strat.submitErr == nil {
		t.Fatal("DenyAllGate must reject the order")
	}
	if counters.Fills != 0 {
		t.Errorf("Counters.Fills = %d, want 0 for a rejected order", counters.Fills)
	}
	if strat.fills != 0 {
		t.Errorf("OnFill fired %d times for a rejected order, want 0", strat.fills)
	}
}

// EngineSink's whole point is that a non-fill-provider inner sink must not
// make engine.Run fabricate a fill. recordingSink implements Submit/Cancel
// but not LastFill, so if EngineSink ever returned a value that
// unconditionally satisfied fillProvider, engine.Run would read back a
// zero-value FillRecord (price 0, lots 0) and write it to the store as if
// it were real.
func TestRiskSink_EngineSink_NonFillProviderInnerProducesNoFabricatedFill(t *testing.T) {
	store := newTestStore(t)
	inner := &recordingSink{}
	rs, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	strat := &submitOnSecond{}
	counters, err := engine.Run(context.Background(),
		feedEvents(liveEvent("AAA", 100, 1), liveEvent("AAA", 100, 2)),
		strat, rs.EngineSink(), store, "fwd")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	// recordingSink always accepts, so the order itself must still succeed —
	// the point is that a non-filling sink is handled correctly, not that
	// it is blocked.
	if strat.submitErr != nil {
		t.Fatalf("SubmitOrder failed, want success: %v", strat.submitErr)
	}
	if counters.Fills != 0 {
		t.Errorf("Counters.Fills = %d, want 0 — recordingSink is not a fillProvider, so EngineSink must not fabricate a fill", counters.Fills)
	}
	if strat.fills != 0 {
		t.Errorf("Strategy.OnFill fired %d times, want 0", strat.fills)
	}
	var rows int
	if err := store.QueryRow(`SELECT COUNT(*) FROM fill WHERE order_id = 'o1'`).Scan(&rows); err != nil {
		t.Fatalf("query fill table: %v", err)
	}
	if rows != 0 {
		t.Errorf("fill rows for o1 = %d, want 0 — a fabricated zero-value fill reached the store", rows)
	}
}

// RiskSink.Observe must not panic when the inner sink has no Observe
// method of its own — recordingSink is such a sink.
func TestRiskSink_ObserveForwardingIsConditional(t *testing.T) {
	inner := &recordingSink{}
	rs, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	rs.Observe(liveEvent("AAA", 100, 1)) // must not panic
}

// EngineSink must not have made the decorator transparent: a rejected
// order must never reach the inner sink's Submit at all.
func TestRiskSink_EngineSink_GatesStillRun(t *testing.T) {
	store := newTestStore(t)
	inner := &recordingSink{}
	rs, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), DenyAllGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	strat := &submitOnSecond{}
	if _, err := engine.Run(context.Background(),
		feedEvents(liveEvent("AAA", 100, 1), liveEvent("AAA", 100, 2)),
		strat, rs.EngineSink(), store, "fwd"); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if strat.submitErr == nil {
		t.Fatal("DenyAllGate must reject the order")
	}
	if inner.count() != 0 {
		t.Errorf("inner sink received %d Submit calls through EngineSink(), want 0 — the decorator must not have become transparent", inner.count())
	}
}

// asyncSubmitOnSecond submits one order on the second event, like
// submitOnSecond above, but also signals a channel synchronously from
// OnFill so a test driving engine.Run on its own goroutine can wait for a
// fill to be fully applied (store write, attribution pairing, and the
// callback itself) without a sleep.
type asyncSubmitOnSecond struct {
	seen      int
	submitErr error
	filled    chan engine.FillRecord
}

func (s *asyncSubmitOnSecond) OnEvent(ctx context.Context, e engine.MarketEvent, eng engine.Engine) {
	s.seen++
	if s.seen != 2 {
		return
	}
	_, s.submitErr = eng.SubmitOrder(ctx, engine.OrderIntent{
		Symbol: e.Symbol, Side: "buy", PriceIDR: 100, Lots: 1,
		OrderID: "o1", BoardType: engine.BoardRG,
	})
}

func (s *asyncSubmitOnSecond) OnFill(ctx context.Context, f engine.FillRecord) {
	s.filled <- f
}

// TestRiskSink_ForwardsAsyncFillsAndSinkKind drives an async inner sink
// through RiskSink.EngineSink() and the real engine.Run: the intent must
// still pass every gate, the fill must come back out through EngineSink()
// (i.e. RiskSink.Fills(), since asyncRecordingSink is not a fillProvider so
// EngineSink returns the bare RiskSink — see EngineSink's doc comment), and
// the store's sink_kind must be the inner sink's own, not "paper".
func TestRiskSink_ForwardsAsyncFillsAndSinkKind(t *testing.T) {
	store := newTestStore(t)
	inner := newAsyncRecordingSink("fake-live")
	rs, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	strat := &asyncSubmitOnSecond{filled: make(chan engine.FillRecord)}
	src := make(chan engine.MarketEvent)

	done := make(chan struct{})
	var runErr error
	go func() {
		_, runErr = engine.Run(context.Background(), src, strat, rs.EngineSink(), store, "fwd-async")
		close(done)
	}()

	src <- liveEvent("AAA", 100, 1)
	src <- liveEvent("AAA", 100, 2) // triggers SubmitOrder through the gates

	inner.push(engine.AsyncFill{
		Symbol: "AAA", Side: "buy", RecvNs: 10,
		Fill: engine.FillRecord{OrderID: "o1", FillPriceIDR: 100, FillLots: 1, FeeIDR: 15},
	})
	<-strat.filled // wait for the fill to be fully applied

	close(src)
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return")
	}
	if runErr != nil {
		t.Fatalf("Run: %v", runErr)
	}
	if strat.submitErr != nil {
		t.Fatalf("SubmitOrder failed: %v", strat.submitErr)
	}

	var kind string
	if err := store.QueryRow(`SELECT sink_kind FROM fill WHERE order_id = 'o1'`).Scan(&kind); err != nil {
		t.Fatalf("no fill row reached the store: %v", err)
	}
	if kind != "fake-live" {
		t.Errorf("fill sink_kind = %q, want fake-live — RiskSink.SinkKind did not forward to the async inner sink", kind)
	}
	var intentKind string
	if err := store.QueryRow(`SELECT sink_kind FROM order_intent WHERE order_id = 'o1'`).Scan(&intentKind); err != nil {
		t.Fatalf("no intent row reached the store: %v", err)
	}
	if intentKind != "fake-live" {
		t.Errorf("intent sink_kind = %q, want fake-live", intentKind)
	}
}
