package risk

import (
	"context"
	"sync"

	"strategy/engine"
)

// recordingSink is a test fake inner sink that records every intent handed
// to Submit and never talks to a network. It exists so tests can assert
// that a rejected intent never reaches the inner sink — the whole point of
// RiskSink being a decorator.
type recordingSink struct {
	mu        sync.Mutex
	submitted []engine.OrderIntent
	cancelled []engine.OrderID
}

func (s *recordingSink) Submit(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.submitted = append(s.submitted, intent)
	return engine.OrderID(intent.OrderID), nil
}

func (s *recordingSink) Cancel(ctx context.Context, id engine.OrderID) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.cancelled = append(s.cancelled, id)
	return nil
}

func (s *recordingSink) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.submitted)
}

// intent builds a valid, on-tick OrderIntent for tests.
func intent(symbol, side string, priceIDR int64, lots int) engine.OrderIntent {
	return engine.OrderIntent{Symbol: symbol, Side: side, PriceIDR: priceIDR, Lots: lots, OrderID: symbol + "-" + side, BoardType: engine.BoardRG}
}

// permissiveLimits returns Limits whose every cap is 1x sleeve equity — high
// enough that, at the notionals these tests use, only the gate under test
// can plausibly reject.
func permissiveLimits() Limits {
	return Limits{
		PerOrderNotionalFraction:   1,
		PerDayNotionalFraction:     1,
		MaxGrossExposureFraction:   1,
		MaxPositionPerNameFraction: 1,
		DailyLossLimitFraction:     1,
	}
}

// allowState is a MarketState with every field Allow.
func allowState() MarketState {
	return MarketState{NotHoliday: Allow, MarketOpen: Allow, Tradable: Allow, NotSuspended: Allow, WithinBand: Allow}
}

// allowGate is a MarketGate test fake that always reports allowState.
type allowGate struct{}

func (allowGate) State(ctx context.Context, symbol string, priceIDR int64) (MarketState, error) {
	return allowState(), nil
}

// fixedGate is a MarketGate test fake returning a fixed state and/or error.
type fixedGate struct {
	state MarketState
	err   error
}

func (g fixedGate) State(ctx context.Context, symbol string, priceIDR int64) (MarketState, error) {
	return g.state, g.err
}

// fixedPnL is a PnLProvider test fake returning a fixed value or error.
type fixedPnL struct {
	pnl float64
	err error
}

func (f fixedPnL) DailyPnLIDR(ctx context.Context) (float64, error) {
	if f.err != nil {
		return 0, f.err
	}
	return f.pnl, nil
}

// mutablePnL is a PnLProvider test fake whose value a test can change
// between Submit calls, for boundary/breach sequencing.
type mutablePnL struct{ pnl float64 }

func (m *mutablePnL) DailyPnLIDR(ctx context.Context) (float64, error) { return m.pnl, nil }

// asyncRecordingSink is a test fake inner sink whose fills arrive out of
// band, like a live order router, rather than synchronously from Submit. It
// exists so tests can drive RiskSink.Fills/SinkKind forwarding through the
// real engine.Run without strategy/live, which wires a real router behind
// the same seam.
type asyncRecordingSink struct {
	fillsCh chan engine.AsyncFill
	kind    string
}

func newAsyncRecordingSink(kind string) *asyncRecordingSink {
	return &asyncRecordingSink{fillsCh: make(chan engine.AsyncFill), kind: kind}
}

func (s *asyncRecordingSink) Submit(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	return engine.OrderID(intent.OrderID), nil
}

func (s *asyncRecordingSink) Cancel(ctx context.Context, id engine.OrderID) error { return nil }

// Fills implements the engine package's unexported asyncSink duck type.
func (s *asyncRecordingSink) Fills() <-chan engine.AsyncFill { return s.fillsCh }

// SinkKind implements the engine package's unexported sinkKinder duck type.
func (s *asyncRecordingSink) SinkKind() string { return s.kind }

// push delivers one fill to whichever engine.Run is reading Fills(), through
// RiskSink's forwarding. The channel is unbuffered, so the send only
// completes once Run's select has received it.
func (s *asyncRecordingSink) push(af engine.AsyncFill) { s.fillsCh <- af }
