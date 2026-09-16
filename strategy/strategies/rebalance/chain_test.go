package rebalance

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"collector/calendar"
	"collector/feed"
	"strategy/engine"
	"strategy/portfolio"
	"strategy/risk"
)

// chainAllowGate is a risk.MarketGate test fake that allows every order.
// strategy/risk's own allow fake (fakes_test.go) is unexported inside that
// package and cannot be imported here, so this is a second, independent
// copy for this seam test — see rebalance.go's snapTickIDR doc comment for
// the same "cannot import, must duplicate" situation with the tick table.
type chainAllowGate struct{}

func (chainAllowGate) State(ctx context.Context, symbol string, priceIDR int64) (risk.MarketState, error) {
	return risk.MarketState{
		NotHoliday:   risk.Allow,
		MarketOpen:   risk.Allow,
		Tradable:     risk.Allow,
		NotSuspended: risk.Allow,
		WithinBand:   risk.Allow,
	}, nil
}

// chainZeroPnL is a risk.PnLProvider test fake reporting a flat, break-even day.
type chainZeroPnL struct{}

func (chainZeroPnL) DailyPnLIDR(ctx context.Context) (float64, error) { return 0, nil }

// chainNs returns the Unix-nanosecond timestamp for a WIB wall-clock time on
// 2026-09-16 plus dayOffset calendar days, so events land inside (or
// outside, for the cutoff) a DailyAt(9, 0) window and each phase of the
// round trip gets its own calendar date.
func chainNs(hour, min, dayOffset int) int64 {
	t := time.Date(2026, 9, 16, hour, min, 0, 0, calendar.Jakarta).AddDate(0, 0, dayOffset)
	return t.UnixNano()
}

func chainLiveEvent(symbol string, price float64, ns int64) engine.MarketEvent {
	d := &feed.Decoded{Kind: "liveprice", Symbol: symbol, LastPrice: price}
	return engine.MarketEvent{RecvNs: ns, Kind: engine.EventLivePrice, Symbol: symbol, Decoded: d}
}

func chainFeed(evs ...engine.MarketEvent) <-chan engine.MarketEvent {
	ch := make(chan engine.MarketEvent, len(evs))
	for _, e := range evs {
		ch <- e
	}
	close(ch)
	return ch
}

// chainPrices are deliberately OFF-tick prices, one per null-portfolio
// default name (portfolio.DefaultNullNames), sweeping every tick band
// (docs/reference/06-pre-trade-rules.md §5).
//
// Off-tick is the point. An earlier version of this table used prices that
// were already on-tick, which made the chain test blind to the coupling it
// exists to pin: deleting snapTickIDR entirely left this test green, and
// only the unit-level tick sweep noticed. Feeding raw prices the exchange
// would refuse means the risk layer rejects every order unless this package
// snaps them, so Rejections() == 0 below is now real evidence rather than a
// restatement of the fixture.
//
// Each price snaps down to the value in the trailing comment, and those
// snapped values divide the 3,000,000 IDR per-name target notional (5-way
// equal weight over a 15,000,000 IDR sleeve, ADR-0017) into a whole number
// of lots — so chainWantLots is unchanged by the off-tick raw inputs.
//
// ANTM is the exception and cannot test the snap: below 200 IDR the tick is
// 1, so every integer price is trivially on-tick. It is here for band
// coverage, not as a guard.
var chainPrices = map[string]float64{
	"TINS": 1003, // tick 5  -> 1000
	"AMMN": 2507, // tick 10 -> 2500
	"ENRG": 6013, // tick 25 -> 6000
	"PTRO": 301,  // tick 2  -> 300
	"ANTM": 150,  // tick 1  -> 150 (band is trivially on-tick)
}

// chainWantLots is the expected filled lot count per name at chainPrices,
// against the same 3,000,000 IDR target notional.
var chainWantLots = map[string]int{
	"TINS": 30,
	"AMMN": 12,
	"ENRG": 5,
	"PTRO": 100,
	"ANTM": 200,
}

// TestChain_CalibrationRoundTrip runs the full plan-18 §3 seam end to end
// through the real components — portfolio.NullPortfolio, this package's
// Strategy, risk.RiskSink, and engine.PaperSink, driven by engine.Run — with
// no fakes standing in for any of them except the two small risk.MarketGate
// / risk.PnLProvider dependencies RiskSink requires an operator to supply.
//
// Phase 1 buys the null portfolio's five names up to their target weights
// from flat. Phase 2 feeds a Flattened copy of the same portfolio and
// drives every position back to zero — one full calibration round trip,
// the unit plan 18 §9 counts toward Track B's ~100-round-trip exit
// condition.
func TestChain_CalibrationRoundTrip(t *testing.T) {
	asOf := time.Date(2026, 9, 16, 9, 0, 0, 0, calendar.Jakarta)
	base, err := portfolio.NullPortfolio(portfolio.NullOptions{
		AsOf:            asOf,
		BaseNotionalIDR: 15_000_000, // the sleeve's real funded equity, ADR-0017
	})
	if err != nil {
		t.Fatalf("NullPortfolio: %v", err)
	}

	store, err := engine.OpenStore(filepath.Join(t.TempDir(), "chain.db"))
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	defer store.Close()

	paper := engine.NewPaperSink()
	limits := risk.Limits{
		PerOrderNotionalFraction: 1,
		// 3x equity: a full buy round-trip into the sleeve, then a full
		// sell back to flat, sums to ~2x equity of same-day notional flow.
		// The extra headroom keeps this test about the seam, not about
		// re-deriving B2's own notional-cap tests.
		PerDayNotionalFraction:     3,
		MaxGrossExposureFraction:   1,
		MaxPositionPerNameFraction: 1,
		DailyLossLimitFraction:     1,
	}
	rs, err := risk.NewRiskSink(paper, risk.SleeveEquityIDR(15_000_000), limits, chainAllowGate{}, chainZeroPnL{}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}

	// --- Phase 1: flat -> target ---
	strat1, err := New(Options{Portfolio: base, Schedule: DailyAt(9, 0)})
	if err != nil {
		t.Fatalf("New (phase 1): %v", err)
	}

	names := base.Symbols()
	events1 := make([]engine.MarketEvent, len(names))
	for i, sym := range names {
		events1[i] = chainLiveEvent(sym, chainPrices[sym], chainNs(9, 5, 0))
	}

	// rs.EngineSink() is mandatory here: engine.Run reaches past OrderSink
	// for a synchronous fill via a fillProvider type assertion, and a bare
	// *RiskSink does not satisfy it (see strategy/risk/forwarding.go) — every
	// fill would silently vanish and this test would see zero fills instead
	// of catching a real coupling bug.
	c1, err := engine.Run(context.Background(), chainFeed(events1...), strat1, rs.EngineSink(), store, strat1.StrategyID())
	if err != nil {
		t.Fatalf("Run (phase 1): %v", err)
	}

	if strat1.Rejections() != 0 {
		t.Fatalf("phase 1: Rejections() = %d, want 0 (a rejection here would mean an off-tick price or missing board type slipped past this package)", strat1.Rejections())
	}
	if c1.Fills != len(names) {
		t.Fatalf("phase 1: Fills = %d, want %d (one per name)", c1.Fills, len(names))
	}
	if c1.OrdersAttempted != len(names) {
		t.Fatalf("phase 1: OrdersAttempted = %d, want %d", c1.OrdersAttempted, len(names))
	}

	var fillCount int
	if err := store.QueryRow(`SELECT COUNT(*) FROM fill`).Scan(&fillCount); err != nil {
		t.Fatalf("query fill count: %v", err)
	}
	if fillCount != len(names) {
		t.Fatalf("store fill rows = %d, want %d", fillCount, len(names))
	}

	gotPositions := strat1.Positions()
	for _, sym := range names {
		if gotPositions[sym] != chainWantLots[sym] {
			t.Errorf("phase 1: Positions()[%s] = %d, want %d", sym, gotPositions[sym], chainWantLots[sym])
		}
	}

	// --- Phase 2: target -> flat ---
	// A second store, not the first: store.go's own documented convention is
	// ONE STORE FILE PER RUN — seq is a bare INTEGER PRIMARY KEY that every
	// run restarts at 1, so a second engine.Run against the same store
	// collides on it by design (see store.go's StorePathConvention doc
	// comment). The paper sink and risk sink are reused across both phases;
	// only the post-trade store is per-run.
	store2, err := engine.OpenStore(filepath.Join(t.TempDir(), "chain2.db"))
	if err != nil {
		t.Fatalf("OpenStore (phase 2): %v", err)
	}
	defer store2.Close()

	flat := base.Flattened(asOf.AddDate(0, 0, 1), asOf.AddDate(0, 0, 1).Add(6*time.Hour))
	strat2, err := New(Options{Portfolio: flat, Schedule: DailyAt(9, 0), Initial: gotPositions})
	if err != nil {
		t.Fatalf("New (phase 2): %v", err)
	}

	events2 := make([]engine.MarketEvent, len(names))
	for i, sym := range names {
		events2[i] = chainLiveEvent(sym, chainPrices[sym], chainNs(9, 5, 1))
	}

	c2, err := engine.Run(context.Background(), chainFeed(events2...), strat2, rs.EngineSink(), store2, strat2.StrategyID())
	if err != nil {
		t.Fatalf("Run (phase 2): %v", err)
	}

	if strat2.Rejections() != 0 {
		t.Fatalf("phase 2: Rejections() = %d, want 0", strat2.Rejections())
	}
	if c2.Fills != len(names) {
		t.Fatalf("phase 2: Fills = %d, want %d", c2.Fills, len(names))
	}

	if err := store2.QueryRow(`SELECT COUNT(*) FROM fill`).Scan(&fillCount); err != nil {
		t.Fatalf("query fill count in phase 2's store: %v", err)
	}
	if fillCount != len(names) {
		t.Fatalf("phase 2 store fill rows = %d, want %d", fillCount, len(names))
	}

	final := strat2.Positions()
	for _, sym := range names {
		if final[sym] != 0 {
			t.Errorf("phase 2: Positions()[%s] = %d, want 0 (flattened)", sym, final[sym])
		}
	}
}
