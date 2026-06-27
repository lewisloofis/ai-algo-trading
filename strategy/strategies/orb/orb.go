// Package orb implements an Opening Range Breakout strategy on the plan-17
// engine. For each symbol it captures the first observed liveprice as the
// opening price, then enters one position when price breaks the opening range
// by ORBBreakoutPct, and exits after a fixed hold or on a RECONNECT.
package orb

import (
	"context"
	"fmt"
	"time"

	"strategy/engine"
)

// ORBBreakoutPct is the fractional move above/below the opening price that
// triggers a breakout entry.
const ORBBreakoutPct float64 = 0.005

// holdDuration is the fixed time a position is held before a timed exit.
const holdDuration = 30 * time.Minute

type posState int

const (
	flat posState = iota
	long
	short
)

// symbolState tracks the per-symbol lifecycle of the strategy.
type symbolState struct {
	openPx     float64
	hasOpen    bool
	pos        posState
	entryTime  time.Time
	entryPx    int64
	entryOrder string
}

// Strategy is the ORB implementation. It holds one lot per entry for the
// re-validation. It is single-goroutine: the engine calls OnEvent/OnFill
// synchronously, so no locking is needed.
type Strategy struct {
	states  map[string]*symbolState
	nextID  int
	lastSig float64
}

// New returns an ORB strategy with empty per-symbol state.
func New() *Strategy {
	return &Strategy{states: make(map[string]*symbolState)}
}

func (s *Strategy) stateFor(symbol string) *symbolState {
	st, ok := s.states[symbol]
	if !ok {
		st = &symbolState{}
		s.states[symbol] = st
	}
	return st
}

func (s *Strategy) newOrderID(symbol string) string {
	s.nextID++
	return fmt.Sprintf("orb-%s-%d", symbol, s.nextID)
}

// OnEvent captures the opening price, fires breakout entries, and triggers
// timed or control-driven exits.
func (s *Strategy) OnEvent(ctx context.Context, e engine.MarketEvent, eng engine.Engine) {
	if e.Kind == engine.EventControl {
		if e.Control != nil && e.Control.Event == "RECONNECT" {
			s.closeAll(ctx, eng)
		}
		return
	}
	if e.Kind != engine.EventLivePrice || e.Decoded == nil || e.Decoded.LastPrice <= 0 {
		return
	}

	st := s.stateFor(e.Symbol)
	price := e.Decoded.LastPrice
	if !st.hasOpen {
		st.openPx = price
		st.hasOpen = true
		return
	}

	if st.pos != flat {
		if eng.Now().Sub(st.entryTime) >= holdDuration {
			s.exit(ctx, e.Symbol, st, eng)
		}
		return
	}

	upper := st.openPx * (1 + ORBBreakoutPct)
	lower := st.openPx * (1 - ORBBreakoutPct)
	s.lastSig = (price - st.openPx) / st.openPx

	switch {
	case price >= upper:
		s.enter(ctx, e.Symbol, "buy", price, st, eng)
	case price <= lower:
		s.enter(ctx, e.Symbol, "sell", price, st, eng)
	}
}

func (s *Strategy) enter(ctx context.Context, symbol, side string, refPx float64, st *symbolState, eng engine.Engine) {
	id := s.newOrderID(symbol)
	intent := engine.OrderIntent{
		Symbol:   symbol,
		Side:     side,
		PriceIDR: int64(refPx),
		Lots:     1,
		OrderID:  id,
	}
	if _, err := eng.SubmitOrder(ctx, intent); err != nil {
		return
	}
	if side == "buy" {
		st.pos = long
	} else {
		st.pos = short
	}
	st.entryTime = eng.Now()
	st.entryOrder = id
}

func (s *Strategy) exit(ctx context.Context, symbol string, st *symbolState, eng engine.Engine) {
	side := "sell"
	if st.pos == short {
		side = "buy"
	}
	id := s.newOrderID(symbol)
	intent := engine.OrderIntent{
		Symbol:   symbol,
		Side:     side,
		PriceIDR: st.entryPx,
		Lots:     1,
		OrderID:  id,
	}
	if _, err := eng.SubmitOrder(ctx, intent); err != nil {
		return
	}
	st.pos = flat
	st.entryOrder = ""
}

func (s *Strategy) closeAll(ctx context.Context, eng engine.Engine) {
	for symbol, st := range s.states {
		if st.pos != flat {
			s.exit(ctx, symbol, st, eng)
		}
	}
}

// OnFill records the actual fill price of the most recent entry so the exit
// intent can reference it.
func (s *Strategy) OnFill(ctx context.Context, f engine.FillRecord) {
	for _, st := range s.states {
		if st.entryOrder == string(f.OrderID) {
			st.entryPx = f.FillPriceIDR
			return
		}
	}
}

// SignalValue exposes the latest breakout ratio for the decision log.
func (s *Strategy) SignalValue(e engine.MarketEvent) (float64, bool) {
	if e.Kind == engine.EventLivePrice {
		return s.lastSig, true
	}
	return 0, false
}
