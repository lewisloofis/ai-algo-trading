package main

import (
	"context"
	"errors"
	"fmt"
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
	strat, stratID, err := newStrategy("orb", "", nil, 0, defaultAt(t))
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
	if _, _, err := newStrategy("nope", "", nil, 0, defaultAt(t)); err == nil {
		t.Fatal("want an error for an unknown --strategy, got nil")
	}
}

func TestNewStrategy_RebalanceRequiresTargetPortfolio(t *testing.T) {
	if _, _, err := newStrategy("rebalance", "", nil, 0, defaultAt(t)); err == nil {
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
	strat, stratID, err := newStrategy("rebalance", path, nil, 0, defaultAt(t))
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
	if _, _, err := newStrategy("rebalance", filepath.Join(t.TempDir(), "does-not-exist.json"), nil, 0, defaultAt(t)); err == nil {
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

	strat, stratID, err := newStrategy("rebalance", path, &priorStateConfig{glob: filepath.Join(dir, "prior.db"), sinkKind: "live"}, 0, defaultAt(t))
	if err != nil {
		t.Fatalf("newStrategy: %v", err)
	}
	if stratID != portfolio.CalibrationStrategyID {
		t.Fatalf("stratID = %q, want %q", stratID, portfolio.CalibrationStrategyID)
	}

	// The strategy's rebalance window opens at the default --rebalance-at (15:00)
	// WIB (see newStrategy) — this event must land at or after that, on the
	// SAME calendar date the prior store seeded, or the window never opens
	// and nothing is attempted at all (a different failure than the one
	// this test checks for).
	eventRecvNs := time.Date(2026, 9, 24, defaultAt(t).hour, defaultAt(t).min+5, 0, 0, calendar.Jakarta).UnixNano()
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

// writeOneSymbolPortfolio writes a single-target portfolio for the
// --limit-price-idr diagnostic path.
func writeOneSymbolPortfolio(t *testing.T, dir string) string {
	t.Helper()
	p, err := portfolio.NullPortfolio(portfolio.NullOptions{
		AsOf:            time.Date(2026, 9, 24, 9, 0, 0, 0, calendar.Jakarta),
		Names:           []string{"BBCA"},
		BaseNotionalIDR: 1_000_000,
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}
	path := filepath.Join(dir, "one.json")
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

func TestNewStrategy_LimitPriceAcceptedForOneTargetRebalance(t *testing.T) {
	path := writeOneSymbolPortfolio(t, t.TempDir())
	if _, _, err := newStrategy("rebalance", path, nil, 5250, defaultAt(t)); err != nil {
		t.Fatalf("newStrategy: %v", err)
	}
}

func TestNewStrategy_LimitPriceRefusals(t *testing.T) {
	dir := t.TempDir()
	one := writeOneSymbolPortfolio(t, dir)
	two := writeTargetPortfolio(t, dir)
	cases := []struct {
		name     string
		strategy string
		path     string
		limit    int64
	}{
		{"non-rebalance strategy", "orb", "", 5250},
		{"multi-target portfolio", "rebalance", two, 5250},
		{"non-tick-valid price", "rebalance", one, 5251},
		{"negative value", "rebalance", one, -5250},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if _, _, err := newStrategy(c.strategy, c.path, nil, c.limit, defaultAt(t)); err == nil {
				t.Fatalf("want an error for --limit-price-idr=%d with %s", c.limit, c.name)
			}
		})
	}
}

func TestParseRebalanceAt(t *testing.T) {
	cases := []struct {
		in       string
		wantH    int
		wantM    int
		wantFail bool
	}{
		{in: "15:00", wantH: 15, wantM: 0},
		{in: "13:45", wantH: 13, wantM: 45},
		{in: "09:05", wantH: 9, wantM: 5},
		{in: "00:00", wantH: 0, wantM: 0},
		{in: "", wantFail: true},
		{in: "24:00", wantFail: true},
		{in: "13:60", wantFail: true},
		{in: "-1:00", wantFail: true},
		{in: "1345", wantFail: true},
		{in: "13:4", wantFail: true},
		{in: "ab:cd", wantFail: true},
	}
	for _, c := range cases {
		t.Run(c.in, func(t *testing.T) {
			got, err := parseRebalanceAt(c.in)
			if c.wantFail {
				if err == nil {
					t.Fatalf("parseRebalanceAt(%q) = %+v, want error", c.in, got)
				}
				return
			}
			if err != nil {
				t.Fatalf("parseRebalanceAt(%q) error: %v", c.in, err)
			}
			if got.hour != c.wantH || got.min != c.wantM {
				t.Fatalf("parseRebalanceAt(%q) = %02d:%02d, want %02d:%02d", c.in, got.hour, got.min, c.wantH, c.wantM)
			}
		})
	}
}

func TestDefaultRebalanceAtIsUnchanged(t *testing.T) {
	got, err := parseRebalanceAt(defaultRebalanceAtFlag)
	if err != nil {
		t.Fatalf("default %q does not parse: %v", defaultRebalanceAtFlag, err)
	}
	if got.hour != 15 || got.min != 0 {
		t.Fatalf("default rebalance time = %02d:%02d, want 15:00 (existing behaviour)", got.hour, got.min)
	}
}

// defaultAt is the parsed --rebalance-at default, for newStrategy calls that
// are not about the schedule.
func defaultAt(t *testing.T) rebalanceAt {
	t.Helper()
	at, err := parseRebalanceAt(defaultRebalanceAtFlag)
	if err != nil {
		t.Fatalf("default --rebalance-at: %v", err)
	}
	return at
}

func TestRunErrAfterShutdown(t *testing.T) {
	other := errors.New("feed died")
	cases := []struct {
		name        string
		runErr      error
		interrupted bool
		want        error
	}{
		{"clean end", nil, false, nil},
		{"signal cancels the run: clean stop", context.Canceled, true, nil},
		{"signal, wrapped cancel: clean stop", fmt.Errorf("source: %w", context.Canceled), true, nil},
		{"signal but a real error: kept", other, true, other},
		{"cancel without a signal: kept", context.Canceled, false, context.Canceled},
		{"real error, no signal: kept", other, false, other},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := runErrAfterShutdown(tc.runErr, tc.interrupted); !errors.Is(got, tc.want) || (got == nil) != (tc.want == nil) {
				t.Fatalf("runErrAfterShutdown(%v, %v) = %v, want %v", tc.runErr, tc.interrupted, got, tc.want)
			}
		})
	}
}

func TestShutdownContextIsLiveAndBounded(t *testing.T) {
	ctx, cancel := newShutdownContext()
	defer cancel()
	if err := ctx.Err(); err != nil {
		t.Fatalf("shutdown context already done: %v", err)
	}
	deadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("shutdown context has no deadline: a hung reconcile would block exit forever")
	}
	if d := time.Until(deadline); d <= 0 || d > shutdownTimeout {
		t.Fatalf("deadline %v away, want (0, %v]", d, shutdownTimeout)
	}
}

// TestLoadPriorState_PositionsAreScopedToAccount: a prior store holding fills
// from two accounts' runs seeds the strategy with only --account's position.
// Account numbers are synthetic.
func TestLoadPriorState_PositionsAreScopedToAccount(t *testing.T) {
	dir := t.TempDir()
	s, err := engine.OpenStore(filepath.Join(dir, "prior.db"))
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	seq := int64(0)
	for _, r := range []struct{ run, account, symbol string }{
		{"run-sleeve", "TEST-SLEEVE", "GOTO"},
		{"run-core", "TEST-CORE", "BBCA"},
	} {
		if err := s.BeginRun(engine.RunRow{RunID: r.run, StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: r.symbol, Account: r.account}); err != nil {
			t.Fatalf("BeginRun: %v", err)
		}
		orderID := "rebalance-" + r.symbol + "-2026-10-13"
		seq++
		if err := s.InsertIntent(engine.IntentRow{Seq: seq, RecvNs: 1, StrategyID: "s", Symbol: r.symbol, Side: "buy", PriceIDR: 100, Lots: 2, OrderID: orderID, SinkKind: "live"}); err != nil {
			t.Fatalf("InsertIntent: %v", err)
		}
		seq++
		if err := s.InsertFill(engine.FillRow{Seq: seq, RecvNs: 1, OrderID: orderID, FillPriceIDR: 100, FillLots: 2, SinkKind: "live"}); err != nil {
			t.Fatalf("InsertFill: %v", err)
		}
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	_, positions, err := loadPriorState(filepath.Join(dir, "*.db"), "s", "live", "TEST-SLEEVE")
	if err != nil {
		t.Fatalf("loadPriorState: %v", err)
	}
	if len(positions) != 1 || positions["GOTO"] != 2 {
		t.Fatalf("positions = %v, want only GOTO: 2 lots", positions)
	}
}
