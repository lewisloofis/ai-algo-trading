package risk

import (
	"sync"
	"time"
)

// Clock supplies the current time RiskSink uses for the per-day notional
// cap's rollover. It is a plain func type, not an interface, so a test can
// inject a closure directly. NewRiskSink defaults it to time.Now when nil.
type Clock func() time.Time

// tradingDay is the calendar-date key the per-day notional counter rolls
// over on: the Year/Month/Day of whatever time.Time Clock returns, taken
// as-is with no timezone shift. This mirrors collector/internal/calendar's
// own convention (its dateKey/norm helpers: a plain date extraction from
// whatever time.Time they are handed) rather than this package re-deriving
// the IDX WIB trading-day boundary itself.
//
// A caller running against the real IDX session must supply a Clock that
// already reports time in WIB (UTC+7) for this boundary to line up with the
// actual trading day; this package does not shift timezones and does not
// consult the holiday calendar itself — a holiday still rolls the counter
// over at whatever local midnight the Clock crosses, it is simply a day
// RiskSink should never see an admitted order for in the first place, if
// the injected MarketGate's NotHoliday field is doing its job.
type tradingDay struct {
	year  int
	month time.Month
	day   int
}

func dayOf(t time.Time) tradingDay {
	y, m, d := t.Date()
	return tradingDay{year: y, month: m, day: d}
}

// mutableState is every piece of RiskSink data that changes after
// construction: the day's cumulative notional and each symbol's tracked
// notional position. (Kill-switch tripped-ness is the third mutable piece,
// held separately in killSwitch — see its own doc comment.) Held in one
// place, guarded by mu, apart from RiskSink's immutable config fields.
//
// Concurrency contract: engine.Run drives Submit from a single goroutine
// (strategy/engine/strategy.go's Strategy interface doc comment), but the
// external kill-switch file and any future ops surface are not on that
// goroutine, so mu makes every read and write here safe from any goroutine.
type mutableState struct {
	mu sync.Mutex

	day            tradingDay
	dayNotionalIDR float64
	positionsIDR   map[string]float64 // symbol -> tracked notional, >= 0 (long-only)

	// reservations records, per order id, the accounting that order applied,
	// so it can be reversed when the order turns out never to have reached
	// the market (inner.Submit errored) or is later cancelled. Without this,
	// exposure is derived from intents rather than order outcomes, which is
	// wrong in both directions: a broker-rejected sell would free headroom
	// the real position still holds (unsafe), and a placed-then-cancelled
	// buy would consume headroom forever (locks the bot out). See
	// RiskSink.Submit and RiskSink.Cancel.
	reservations map[string]reservation
	// aliases maps a sink-minted order id to the intent order id its
	// reservation is filed under, for sinks that do not echo the intent's id.
	aliases map[string]string
}

// reservation is the accounting one admitted OrderIntent applied. posDeltaIDR
// is the ACTUAL signed change made to positionsIDR[symbol] — not the nominal
// notional — because applySide clamps a sell at zero, so the two differ
// whenever a sell exceeds the tracked position. day is recorded so a release
// that arrives after a day rollover does not subtract this order's notional
// from a counter that has since been zeroed.
type reservation struct {
	symbol      string
	posDeltaIDR float64
	notionalIDR float64
	day         tradingDay
}

func newMutableState() *mutableState {
	return &mutableState{
		positionsIDR: make(map[string]float64),
		reservations: make(map[string]reservation),
		aliases:      make(map[string]string),
	}
}

// reserve applies an admitted intent's accounting and records it under
// orderID so it can be reversed. Caller must hold mu.
func (s *mutableState) reserve(orderID, symbol string, posDeltaIDR, notionalIDR float64) {
	s.dayNotionalIDR += notionalIDR
	s.positionsIDR[symbol] += posDeltaIDR
	s.reservations[orderID] = reservation{
		symbol:      symbol,
		posDeltaIDR: posDeltaIDR,
		notionalIDR: notionalIDR,
		day:         s.day,
	}
}

// alias makes an existing reservation reachable under a second order id, for
// the case where the inner sink mints an id of its own rather than echoing
// the intent's. Caller must hold mu.
func (s *mutableState) alias(from, to string) {
	if from == to || to == "" {
		return
	}
	if _, ok := s.reservations[from]; ok {
		s.aliases[to] = from
	}
}

// resolve maps an order id (which may be a sink-minted alias) to the key its
// reservation is filed under. Caller must hold mu.
func (s *mutableState) resolve(orderID string) string {
	if canonical, ok := s.aliases[orderID]; ok {
		return canonical
	}
	return orderID
}

// releaseAll fully reverses a reservation: the order never reached the
// market, so it consumed neither exposure nor the day's notional budget.
// Caller must hold mu.
func (s *mutableState) releaseAll(orderID string) {
	orderID = s.resolve(orderID)
	r, ok := s.reservations[orderID]
	if !ok {
		return
	}
	s.reversePosition(r)
	if r.day == s.day {
		s.dayNotionalIDR -= r.notionalIDR
		if s.dayNotionalIDR < 0 {
			s.dayNotionalIDR = 0
		}
	}
	s.forget(orderID)
}

// releasePosition reverses only a reservation's exposure, leaving the day's
// notional consumed: a cancelled order DID reach the market, so it spent the
// day's order-flow budget even though it no longer holds exposure. Caller
// must hold mu.
func (s *mutableState) releasePosition(orderID string) {
	orderID = s.resolve(orderID)
	r, ok := s.reservations[orderID]
	if !ok {
		return
	}
	s.reversePosition(r)
	s.forget(orderID)
}

// reversePosition undoes r's position delta, clamping at zero (long-only).
// Caller must hold mu.
func (s *mutableState) reversePosition(r reservation) {
	next := s.positionsIDR[r.symbol] - r.posDeltaIDR
	if next < 0 {
		next = 0
	}
	s.positionsIDR[r.symbol] = next
}

// forget drops a reservation and any alias pointing at it. Caller must hold
// mu. orderID must already be the canonical key (see resolve).
func (s *mutableState) forget(orderID string) {
	delete(s.reservations, orderID)
	for alias, canonical := range s.aliases {
		if canonical == orderID {
			delete(s.aliases, alias)
		}
	}
}

// rolloverIfNewDay resets the day's cumulative notional when now falls on a
// different tradingDay than the one currently tracked. Positions are NOT
// reset on rollover — a position carries across days until sold; only the
// per-day notional counter is calendar-scoped. Caller must hold mu.
func (s *mutableState) rolloverIfNewDay(now time.Time) {
	d := dayOf(now)
	if d != s.day {
		s.day = d
		s.dayNotionalIDR = 0
	}
}

// grossExposureIDR sums the tracked notional position across every symbol.
// Caller must hold mu. Long-only, so every value is >= 0 and this is a
// plain sum.
func (s *mutableState) grossExposureIDR() float64 {
	var sum float64
	for _, v := range s.positionsIDR {
		sum += v
	}
	return sum
}

// applySide returns the new tracked notional position after applying side
// to cur. Long-only (plan 18 §11): a sell larger than the tracked position
// clamps to 0 rather than go negative, mirroring
// strategy/engine/positions.go's lotAccount.applySell — this package does
// not model shorting. side is assumed already validated to be "buy" or
// "sell" by checkStructural; any other value leaves cur unchanged.
func applySide(cur float64, side string, notional float64) float64 {
	switch side {
	case "buy":
		return cur + notional
	case "sell":
		next := cur - notional
		if next < 0 {
			return 0
		}
		return next
	default:
		return cur
	}
}
