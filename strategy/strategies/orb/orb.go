// Package orb implements an Opening Range Breakout strategy on the plan-17
// engine. For each symbol it captures the first observed liveprice as the
// opening price, then enters one position when price breaks the opening range
// by ORBBreakoutPct, and exits at end of session (15:49 WIB = 08:49 UTC) or
// when a new trading day's events arrive. This matches plan-10's run_orb_family
// exit rule: hold until the last bar of the trading day.
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

// IDX session 2 closes at 15:49 WIB = 08:49 UTC. Any liveprice tick at or
// after this UTC hour:minute triggers an end-of-day exit.
const sessionCloseHourUTC = 8
const sessionCloseMinUTC  = 49

// isSessionEnd reports whether t is at or after the IDX session-2 close.
func isSessionEnd(t time.Time) bool {
	u := t.UTC()
	h, m := u.Hour(), u.Minute()
	return h > sessionCloseHourUTC || (h == sessionCloseHourUTC && m >= sessionCloseMinUTC)
}

// sameDate reports whether a and b fall on the same calendar date (UTC).
func sameDate(a, b time.Time) bool {
	ay, am, ad := a.UTC().Date()
	by, bm, bd := b.UTC().Date()
	return ay == by && am == bm && ad == bd
}

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
	entryDate  time.Time // UTC date the position was entered
	lastDate   time.Time // UTC date of last liveprice seen (zero until first event)
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
// session-end or new-day exits.
func (s *Strategy) OnEvent(ctx context.Context, e engine.MarketEvent, eng engine.Engine) {
	// Control events (RECONNECT, SUBSCRIBE, etc.) are ignored. Session-end and
	// new-day exits are handled via timestamp checks on liveprice events below,
	// matching plan-10's hold-until-last-bar-of-day semantics.
	if e.Kind != engine.EventLivePrice || e.Decoded == nil || e.Decoded.LastPrice <= 0 {
		return
	}

	st := s.stateFor(e.Symbol)
	price := e.Decoded.LastPrice
	now := eng.Now()

	// New trading day: force-close any lingering position and reset OR state so
	// the strategy is ready to capture a fresh opening range.
	if !st.lastDate.IsZero() && !sameDate(now, st.lastDate) {
		if st.pos != flat {
			s.exit(ctx, e.Symbol, st, eng)
		}
		st.openPx = 0
		st.hasOpen = false
	}
	st.lastDate = now

	if !st.hasOpen {
		st.openPx = price
		st.hasOpen = true
		return
	}

	if st.pos != flat {
		if isSessionEnd(now) {
			s.exit(ctx, e.Symbol, st, eng)
		}
		return
	}

	// No new entries at or after session close — the strategy holds all day
	// and exits at close, not re-enters in the closing auction.
	if isSessionEnd(now) {
		return
	}

	upper := st.openPx * (1 + ORBBreakoutPct)
	lower := st.openPx * (1 - ORBBreakoutPct)
	s.lastSig = (price - st.openPx) / st.openPx

	switch {
	case price >= upper:
		s.enter(ctx, e.Symbol, "buy", price, st, eng, now)
	case price <= lower:
		s.enter(ctx, e.Symbol, "sell", price, st, eng, now)
	}
}

func (s *Strategy) enter(ctx context.Context, symbol, side string, refPx float64, st *symbolState, eng engine.Engine, now time.Time) {
	id := s.newOrderID(symbol)
	// Set entryOrder before SubmitOrder: OnFill fires synchronously inside
	// SubmitOrder and must see the ID to record the fill price in st.entryPx.
	st.entryOrder = id
	intent := engine.OrderIntent{
		Symbol:   symbol,
		Side:     side,
		PriceIDR: int64(refPx),
		Lots:     1,
		OrderID:  id,
	}
	if _, err := eng.SubmitOrder(ctx, intent); err != nil {
		st.entryOrder = ""
		return
	}
	if side == "buy" {
		st.pos = long
	} else {
		st.pos = short
	}
	st.entryDate = now
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
