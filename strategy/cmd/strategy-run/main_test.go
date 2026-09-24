package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"collector/calendar"
	"collector/feed"
	"strategy/engine"
	"strategy/live"
	"strategy/portfolio"
)

// ---------------------------------------------------------------------------
// --sink / --source validation
// ---------------------------------------------------------------------------

func TestValidateSinkSource_RefusesLiveSinkWithReplaySource(t *testing.T) {
	if err := validateSinkSource("live", "replay"); err == nil {
		t.Fatal("want an error for --sink live --source replay, got nil")
	}
}

func TestValidateSinkSource_AllowsLiveSinkWithLiveSource(t *testing.T) {
	if err := validateSinkSource("live", "live"); err != nil {
		t.Fatalf("--sink live --source live should be allowed: %v", err)
	}
}

func TestValidateSinkSource_AllowsPaperWithEitherSource(t *testing.T) {
	if err := validateSinkSource("paper", "replay"); err != nil {
		t.Fatalf("--sink paper --source replay should be allowed: %v", err)
	}
	if err := validateSinkSource("paper", "live"); err != nil {
		t.Fatalf("--sink paper --source live should be allowed: %v", err)
	}
}

func TestValidatePriorStores_RefusesLiveSinkWithoutPriorStores(t *testing.T) {
	if err := validatePriorStores("live", ""); err == nil {
		t.Fatal("want an error for --sink live without --prior-stores, got nil")
	}
	if err := validatePriorStores("live", "   "); err == nil {
		t.Fatal("want an error for --sink live with a blank --prior-stores, got nil")
	}
}

func TestValidatePriorStores_AllowsLiveWithGlobAndPaperWithout(t *testing.T) {
	if err := validatePriorStores("live", "runs/*.db"); err != nil {
		t.Fatalf("--sink live --prior-stores runs/*.db should be allowed: %v", err)
	}
	if err := validatePriorStores("paper", ""); err != nil {
		t.Fatalf("--sink paper without --prior-stores should be allowed: %v", err)
	}
}

// ---------------------------------------------------------------------------
// newStrategy
// ---------------------------------------------------------------------------

func TestNewStrategy_Orb(t *testing.T) {
	strat, stratID, err := newStrategy("orb", "", nil)
	if err != nil {
		t.Fatalf("newStrategy: %v", err)
	}
	if strat == nil {
		t.Fatal("newStrategy returned a nil strategy")
	}
	if stratID != "orb" {
		t.Fatalf("stratID = %q, want orb", stratID)
	}
}

func TestNewStrategy_UnknownStrategy(t *testing.T) {
	if _, _, err := newStrategy("nope", "", nil); err == nil {
		t.Fatal("want an error for an unknown --strategy, got nil")
	}
}

func TestNewStrategy_RebalanceRequiresTargetPortfolio(t *testing.T) {
	if _, _, err := newStrategy("rebalance", "", nil); err == nil {
		t.Fatal("want an error when --strategy rebalance is given no --target-portfolio")
	}
}

func writeTargetPortfolio(t *testing.T, dir string) string {
	t.Helper()
	p, err := portfolio.NullPortfolio(portfolio.NullOptions{
		AsOf:            time.Date(2026, 9, 24, 9, 0, 0, 0, calendar.Jakarta),
		Names:           []string{"TINS", "AMMN"},
		BaseNotionalIDR: 15_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	path := filepath.Join(dir, "target_portfolio.json")
	f, err := os.Create(path)
	if err != nil {
		t.Fatalf("create %s: %v", path, err)
	}
	defer f.Close()
	if err := p.Encode(f); err != nil {
		t.Fatalf("Encode: %v", err)
	}
	return path
}

func TestNewStrategy_RebalanceLoadsPortfolioAndUsesItsOwnStrategyID(t *testing.T) {
	path := writeTargetPortfolio(t, t.TempDir())
	strat, stratID, err := newStrategy("rebalance", path, nil)
	if err != nil {
		t.Fatalf("newStrategy: %v", err)
	}
	if strat == nil {
		t.Fatal("newStrategy returned a nil strategy")
	}
	// NullPortfolio always stamps portfolio.CalibrationStrategyID.
	if stratID != portfolio.CalibrationStrategyID {
		t.Fatalf("stratID = %q, want %q", stratID, portfolio.CalibrationStrategyID)
	}
}

func TestNewStrategy_RebalanceMissingFile(t *testing.T) {
	if _, _, err := newStrategy("rebalance", filepath.Join(t.TempDir(), "does-not-exist.json"), nil); err == nil {
		t.Fatal("want an error for a missing --target-portfolio file")
	}
}

// TestNewStrategy_RebalanceRecoversAttemptedFromPriorStores is the CLI-level
// half of Task 2's restart durability: a prior store file recorded under
// the portfolio's own strategy_id must gate the newly-constructed
// Strategy's first attempt for that (window, symbol) — proven here by
// driving OnEvent through a fake engine.Engine and observing no
// SubmitOrder call for the seeded symbol.
func TestNewStrategy_RebalanceRecoversAttemptedFromPriorStores(t *testing.T) {
	dir := t.TempDir()
	path := writeTargetPortfolio(t, dir)

	// Seed a prior store: TINS was already attempted on 2026-09-24 under
	// sink_kind "live".
	priorPath := filepath.Join(dir, "prior.db")
	prior, err := engine.OpenStore(priorPath)
	if err != nil {
		t.Fatalf("OpenStore (prior): %v", err)
	}
	// 2026-09-24 09:05 WIB — any time that day; AttemptedWindows keys on
	// the Jakarta calendar DATE only (see its doc comment).
	seedRecvNs := time.Date(2026, 9, 24, 9, 5, 0, 0, calendar.Jakarta).UnixNano()
	if err := prior.InsertIntent(engine.IntentRow{
		Seq: 1, RecvNs: seedRecvNs, StrategyID: portfolio.CalibrationStrategyID, Symbol: "TINS",
		Side: "buy", PriceIDR: 1000, Lots: 30, OrderID: "rebalance-TINS-2026-09-24", SinkKind: "live",
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	if err := prior.Close(); err != nil {
		t.Fatalf("close prior: %v", err)
	}

	strat, stratID, err := newStrategy("rebalance", path, &priorStateConfig{glob: filepath.Join(dir, "prior.db"), sinkKind: "live"})
	if err != nil {
		t.Fatalf("newStrategy: %v", err)
	}
	if stratID != portfolio.CalibrationStrategyID {
		t.Fatalf("stratID = %q, want %q", stratID, portfolio.CalibrationStrategyID)
	}

	// The strategy's rebalance window opens at defaultRebalanceHour:defaultRebalanceMinute
	// WIB (see newStrategy) — this event must land at or after that, on the
	// SAME calendar date the prior store seeded, or the window never opens
	// and nothing is attempted at all (a different failure than the one
	// this test checks for).
	eventRecvNs := time.Date(2026, 9, 24, defaultRebalanceHour, defaultRebalanceMinute+5, 0, 0, calendar.Jakarta).UnixNano()
	fe := &fakeStrategyEngine{now: time.Unix(0, eventRecvNs)}
	strat.OnEvent(context.Background(), liveEventFor("TINS", 1000, eventRecvNs), fe)
	strat.OnEvent(context.Background(), liveEventFor("AMMN", 2500, eventRecvNs), fe)

	if len(fe.calls) != 1 {
		t.Fatalf("SubmitOrder called %d times, want 1 (TINS should have been skipped as already attempted): %+v", len(fe.calls), fe.calls)
	}
	if fe.calls[0].Symbol != "AMMN" {
		t.Fatalf("SubmitOrder called for %q, want AMMN", fe.calls[0].Symbol)
	}
}

// fakeStrategyEngine is a minimal engine.Engine for unit-testing
// newStrategy's rebalance wiring without going through engine.Run.
type fakeStrategyEngine struct {
	now   time.Time
	calls []engine.OrderIntent
}

func (f *fakeStrategyEngine) Now() time.Time { return f.now }
func (f *fakeStrategyEngine) SubmitOrder(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	f.calls = append(f.calls, intent)
	return engine.OrderID(intent.OrderID), nil
}

func liveEventFor(symbol string, price float64, recvNs int64) engine.MarketEvent {
	d := &feed.Decoded{Kind: "liveprice", Symbol: symbol, LastPrice: price}
	return engine.MarketEvent{RecvNs: recvNs, Kind: engine.EventLivePrice, Symbol: symbol, Decoded: d}
}

// ---------------------------------------------------------------------------
// newInnerSink
// ---------------------------------------------------------------------------

func TestNewInnerSink_Paper(t *testing.T) {
	sink, liveSink, err := newInnerSink("paper", "taker", "", envTokens{}, "", nil)
	if err != nil {
		t.Fatalf("newInnerSink: %v", err)
	}
	if sink == nil {
		t.Fatal("newInnerSink returned a nil sink")
	}
	if liveSink != nil {
		t.Fatal("newInnerSink returned a non-nil liveSink for --sink paper")
	}
}

func TestNewInnerSink_UnknownFillMode(t *testing.T) {
	if _, _, err := newInnerSink("paper", "bogus", "", envTokens{}, "", nil); err == nil {
		t.Fatal("want an error for an unknown --fill-mode")
	}
}

func TestNewInnerSink_UnknownSink(t *testing.T) {
	if _, _, err := newInnerSink("bogus", "taker", "", envTokens{}, "", nil); err == nil {
		t.Fatal("want an error for an unknown --sink")
	}
}

func TestNewInnerSink_LiveRefusesUnarmed(t *testing.T) {
	// ALGO_LIVE/ALGO_CONFIRM deliberately left unset: this must fail closed
	// with live.ErrNotArmed, never construct a working live sink.
	t.Setenv("ALGO_LIVE", "")
	t.Setenv("ALGO_CONFIRM", "")
	_, _, err := newInnerSink("live", "taker", "008488L", envTokens{trading: "t"}, "http://127.0.0.1:0", nil)
	if err == nil {
		t.Fatal("want an error constructing a live sink while unarmed")
	}
}

func TestNewInnerSink_LiveRequiresAccount(t *testing.T) {
	t.Setenv("ALGO_LIVE", "true")
	t.Setenv("ALGO_CONFIRM", live.LiveConfirmPhrase)
	_, _, err := newInnerSink("live", "taker", "", envTokens{trading: "t"}, "http://127.0.0.1:0", nil)
	if err == nil {
		t.Fatal("want an error constructing a live sink with no account number")
	}
}

func TestNewInnerSink_LiveArmedConstructsSink(t *testing.T) {
	t.Setenv("ALGO_LIVE", "true")
	t.Setenv("ALGO_CONFIRM", live.LiveConfirmPhrase)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	sink, liveSink, err := newInnerSink("live", "taker", "008488L", envTokens{trading: "t"}, srv.URL, srv.Client())
	if err != nil {
		t.Fatalf("newInnerSink: %v", err)
	}
	if sink == nil || liveSink == nil {
		t.Fatal("newInnerSink did not return a live sink")
	}
	if liveSink.SinkKind() != "live" {
		t.Fatalf("SinkKind() = %q, want live", liveSink.SinkKind())
	}
}

// ---------------------------------------------------------------------------
// wrapWithRisk
// ---------------------------------------------------------------------------

func TestWrapWithRisk_RejectsNilInner(t *testing.T) {
	if _, err := wrapWithRisk(nil, 1, riskLimits(0.25, 1, 1, 0.25, 0.05), nil, nil, "", nil); err == nil {
		t.Fatal("want an error wrapping a nil inner sink")
	}
}

func TestWrapWithRisk_WrapsPaperSink(t *testing.T) {
	inner := engine.NewPaperSink()
	pnl := zeroPnLProvider{}
	wrapped, err := wrapWithRisk(inner, 15_000_000, riskLimits(0.25, 1, 1, 0.25, 0.05), nil, pnl, "", nil)
	if err != nil {
		t.Fatalf("wrapWithRisk: %v", err)
	}
	if wrapped == nil {
		t.Fatal("wrapWithRisk returned a nil sink")
	}
}

// zeroPnLProvider is a risk.PnLProvider test fake reporting a flat,
// break-even day — NewRiskSink requires a non-nil provider even when a
// test has no interest in the daily-loss gate.
type zeroPnLProvider struct{}

func (zeroPnLProvider) DailyPnLIDR(ctx context.Context) (float64, error) { return 0, nil }
