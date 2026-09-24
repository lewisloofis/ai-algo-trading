// Package rebalance implements Track B's consumer end of the seam described
// in plan 18 §3: it drives whatever position the engine currently holds
// toward a portfolio.TargetPortfolio's weights, emitting one parent
// engine.OrderIntent per name per rebalance window.
//
// It deliberately does not slice, pace, or apply a participation cap.
// Arrival-price benchmarking, time/volume slicing, and the participation cap
// are B5's execution algo (plan 18 §5 B5) — a later stage that is expected
// to sit between a strategy like this one and the order sink, not inside
// either. Consequently constraints.max_participation_rate is read by
// nothing in this file; a "rebalance" here means "submit one intent sized
// to close the whole gap", not "work the order over the session". Building
// that slicing logic here before B5 exists would duplicate work plan 18
// already assigns elsewhere, for a strategy whose whole point (§3.3) is to
// isolate execution cost, not to minimize it.
package rebalance

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"collector/calendar"
	"strategy/engine"
	"strategy/portfolio"
)

// ErrInvalidOptions is wrapped by every error New returns for a
// structurally invalid Options value.
var ErrInvalidOptions = errors.New("rebalance: invalid options")

// Schedule reports which rebalance window, if any, now falls in. It
// returns an identifier rather than a bool so the strategy can key its
// per-window "already attempted" bookkeeping off it directly — two calls to
// Window returning the same non-empty string mean "the same window", and a
// change in the returned string (including to/from "") is what starts or
// ends one.
type Schedule interface {
	Window(now time.Time) string
}

// dailyAt is the Schedule DailyAt returns.
type dailyAt struct {
	hour, min int
}

// DailyAt opens one window per calendar date in Jakarta (collector/calendar),
// starting at or after hour:min WIB and running through the rest of that
// calendar date. Window identifies the open window by date
// ("2006-01-02") rather than by a fixed clock tick, so any event arriving
// on or after the opening time — whenever the first one happens to land —
// starts the same window as every later event that day; the strategy does
// not need to see an event at exactly hour:min to rebalance.
func DailyAt(hour, min int) Schedule {
	return dailyAt{hour: hour, min: min}
}

// Window implements Schedule.
func (d dailyAt) Window(now time.Time) string {
	local := now.In(calendar.Jakarta)
	h, m := local.Hour(), local.Minute()
	if h < d.hour || (h == d.hour && m < d.min) {
		return ""
	}
	return local.Format("2006-01-02")
}

// Options configures a Strategy.
type Options struct {
	// Portfolio is the target this strategy drives the position toward.
	Portfolio portfolio.TargetPortfolio
	// Schedule decides which calendar window an event falls in; a symbol is
	// attempted at most once per window (see Strategy's doc comment).
	Schedule Schedule
	// Initial is the opening position per symbol, in lots (1 lot = 100
	// shares). A nil map means every symbol starts flat, which is also
	// what a zero-value entry for an unlisted symbol means — this field
	// exists only to seed a Strategy built mid-position (e.g. after a
	// restart), not to express "no opening position" (which needs no
	// entry at all).
	Initial map[string]int

	// Attempted seeds which (window, symbol) pairs must be treated as
	// already attempted, keyed exactly as this package's own internal
	// bookkeeping keys them: window + "|" + symbol (window is whatever
	// Schedule.Window returns — a Jakarta calendar date for DailyAt). A nil
	// map means nothing is pre-seeded, matching every caller before this
	// field existed.
	//
	// This exists for plan 18 §5 B4's restart-durability gap: OnEvent
	// already guarantees at most one SubmitOrder attempt per (window,
	// symbol) within a process's lifetime, whatever the outcome
	// (TestIdempotentWithinWindow) — but that bookkeeping is the plain
	// in-memory `attempted` map below, which a crash discards. A caller
	// that restarts this strategy mid-day can make that same guarantee
	// durable by seeding Attempted from
	// engine.PostTradeStore.AttemptedWindows(strategyID, sinkKind), which
	// returns keys in exactly this format from the prior run's own store
	// file (see that function's doc comment for why "the prior run's",
	// not "this run's own"). Without it, a restart would re-attempt every
	// symbol in the still-open window, and a strategy that already placed
	// (or was already rejected for) an order today would place a second
	// one under a fresh order id.
	Attempted map[string]bool
}

// orderMeta is what OnFill needs to resolve a fill back to a symbol and a
// signed lot delta: the direction (buy/sell) is not itself present on
// engine.FillRecord, so the strategy must remember what it asked for under
// each order id it minted.
type orderMeta struct {
	symbol string
	lots   int // signed: positive = bought, negative = sold
}

// Strategy drives the current position toward Options.Portfolio's target
// weights on Options.Schedule's cadence. It is single-goroutine: the engine
// calls OnEvent/OnFill synchronously, so no locking is needed (matching
// strategies/orb's Strategy).
//
// Deliberately absent: a SignalValue method. strategies/orb has one, and
// engine.Run type-asserts for it to populate the decision log's
// signal_value column. The null portfolio (plan 18 §3.3) carries no signal
// by construction — its whole purpose is a target with zero expected alpha,
// derived from nothing but equal weights and a schedule — so this strategy
// has no scalar to report. Adding a SignalValue that returned, say, the
// current deviation from target would put a fabricated number in a column
// the store's readers treat as "the alpha signal that drove this decision".
// The absence is a decision, not an oversight left for later.
type Strategy struct {
	portfolio portfolio.TargetPortfolio
	schedule  Schedule

	lastPrice map[string]float64 // last observed EventLivePrice, per symbol
	confirmed map[string]int     // filled position, per symbol, in lots
	inFlight  map[string]int     // signed lots reserved by an order not yet filled
	attempted map[string]bool    // dedup key: window + "|" + symbol
	orders    map[string]orderMeta

	rejects int
}

// New validates opts and returns a ready Strategy. It rejects a portfolio
// whose StrategyID is blank (the store's partition key must name a
// strategy), a nil Schedule (there would be no way to ever open a window),
// and a portfolio with no targets (nothing to rebalance toward — nb: this
// is defense in depth, since portfolio.Parse/Load/NullPortfolio already
// refuse to produce such a value; it only bites a portfolio.TargetPortfolio
// built by hand, bypassing validate()). opts.Initial may be nil.
func New(opts Options) (*Strategy, error) {
	if strings.TrimSpace(opts.Portfolio.StrategyID) == "" {
		return nil, fmt.Errorf("%w: portfolio.strategy_id must not be blank", ErrInvalidOptions)
	}
	if opts.Schedule == nil {
		return nil, fmt.Errorf("%w: schedule is required", ErrInvalidOptions)
	}
	if len(opts.Portfolio.Targets) == 0 {
		return nil, fmt.Errorf("%w: portfolio has no targets", ErrInvalidOptions)
	}

	confirmed := make(map[string]int, len(opts.Initial))
	for symbol, lots := range opts.Initial {
		confirmed[symbol] = lots
	}

	attempted := make(map[string]bool, len(opts.Attempted))
	for key, v := range opts.Attempted {
		if v {
			attempted[key] = true
		}
	}

	return &Strategy{
		portfolio: opts.Portfolio,
		schedule:  opts.Schedule,
		lastPrice: make(map[string]float64),
		confirmed: confirmed,
		inFlight:  make(map[string]int),
		attempted: attempted,
		orders:    make(map[string]orderMeta),
	}, nil
}

// StrategyID is the portfolio's own strategy_id — "calibration" for the
// null portfolio (portfolio.CalibrationStrategyID), a real strategy's own
// id otherwise.
func (s *Strategy) StrategyID() string { return s.portfolio.StrategyID }

// Positions returns a copy of the confirmed (filled) position per symbol,
// in lots. It is a copy — not the live map — so a caller (tests,
// reconciliation) cannot mutate the strategy's own bookkeeping by holding
// onto the result, matching portfolio.TargetPortfolio.Symbols()'s same
// discipline (rules/common/coding-style.md's immutability principle,
// applied here to a getter).
func (s *Strategy) Positions() map[string]int {
	out := make(map[string]int, len(s.confirmed))
	for symbol, lots := range s.confirmed {
		out[symbol] = lots
	}
	return out
}

// Rejections returns how many SubmitOrder calls this strategy has made
// that came back with an error (a risk-gate reject, a broker 4xx via a
// live sink, or the paper sink's ErrNoBook). Each rejection consumes that
// symbol's one attempt for the window it occurred in (see OnEvent's doc
// comment) rather than retrying — a rejection storm on every subsequent
// event would turn one bad order into hundreds. Counting rejections here,
// rather than only logging them, gives an operator (and a test) something
// to assert against so a silently-eaten reject cannot pass unnoticed.
func (s *Strategy) Rejections() int { return s.rejects }

// OnEvent updates the last-observed price for a liveprice tick, then — on
// every event, not just liveprice ticks, since a rejected or late attempt
// for one symbol must still be retried on the next event regardless of
// which symbol that event names — tries to bring every target symbol to
// its target lot count for the current schedule window, at most once each.
//
// Per (window, symbol): if the window hasn't opened (Schedule.Window
// returns ""), or this symbol was already attempted in this window,
// nothing happens. If no price has been observed yet for the symbol, it is
// skipped WITHOUT being marked attempted, specifically so it is retried on
// a later event in the same window — a name whose first tick simply
// arrives late must not silently miss its rebalance for the day. Otherwise
// the symbol is marked attempted (whatever happens next) and, if its
// target lot count differs from its current confirmed+in-flight lots, one
// order is submitted for the difference.
func (s *Strategy) OnEvent(ctx context.Context, e engine.MarketEvent, eng engine.Engine) {
	if e.Kind == engine.EventLivePrice && e.Decoded != nil && e.Decoded.LastPrice > 0 {
		s.lastPrice[e.Symbol] = e.Decoded.LastPrice
	}

	window := s.schedule.Window(eng.Now())
	if window == "" {
		return
	}

	for _, symbol := range s.portfolio.Symbols() {
		s.rebalanceOne(ctx, eng, window, symbol)
	}
}

// rebalanceOne attempts, at most once, to close the gap between symbol's
// target lot count and its current (confirmed + in-flight) lots for the
// given window. See OnEvent's doc comment for the "skip without marking
// attempted" rule this depends on.
func (s *Strategy) rebalanceOne(ctx context.Context, eng engine.Engine, window, symbol string) {
	key := window + "|" + symbol
	if s.attempted[key] {
		return
	}

	price, hasPrice := s.lastPrice[symbol]
	if !hasPrice {
		return // retry on a later event in this same window
	}

	targetNotional, ok := s.portfolio.TargetNotionalIDR(symbol)
	if !ok {
		// Cannot happen given symbol came from s.portfolio.Symbols() itself,
		// but a symbol this package cannot size is one it must not guess
		// about — mark it attempted so a defensive bug here fails closed
		// (no order) rather than retrying forever.
		s.attempted[key] = true
		return
	}

	// Snap BEFORE sizing, and size against the snapped price — not the raw
	// observed one. The two must be the same number or the portfolio
	// systematically misses its own weights: snapTickIDR always rounds
	// down, so sizing on the raw price buys the lot count appropriate to a
	// price strictly higher than the one actually submitted. At a
	// 3,000,000 IDR per-name target, a raw 6013 sizes 4 lots but executes
	// at 6000, committing 2,400,000 — a 20% underweight produced entirely
	// by tick rounding.
	//
	// That is intolerable specifically here. The null portfolio exists so
	// that every basis point of deviation from arrival price is
	// attributable to execution (plan 18 §3.3); a deterministic,
	// one-directional weight error introduced by the sizing basis would be
	// measured by B6 as though it were execution cost.
	priceIDR := snapTickIDR(price)
	if priceIDR <= 0 {
		// A sub-1-IDR price snaps to 0, and dividing by it yields +Inf,
		// whose conversion to int is undefined in Go — a garbage lot count
		// rather than an error. No IDX instrument trades below 1 IDR, so
		// this is a corrupt frame, not a cheap stock: mark it attempted and
		// place nothing, rather than guess.
		s.attempted[key] = true
		return
	}

	targetLots := int(math.Floor(float64(targetNotional) / (float64(priceIDR) * sharesPerLot)))
	delta := targetLots - (s.confirmed[symbol] + s.inFlight[symbol])

	s.attempted[key] = true // one attempt per (window, symbol), whatever follows

	if delta == 0 {
		return
	}

	side := "buy"
	lots := delta
	if delta < 0 {
		side = "sell"
		lots = -delta
	}

	orderID := s.newOrderID(symbol, window)
	intent := engine.OrderIntent{
		Symbol:    symbol,
		Side:      side,
		PriceIDR:  priceIDR,
		Lots:      lots,
		OrderID:   orderID,
		BoardType: s.portfolio.Constraints.Board,
	}

	// Reserve the in-flight lots and remember this order id BEFORE
	// submitting, not after: a synchronous-fill sink (engine.PaperSink) has
	// engine.Run call OnFill from inside eng.SubmitOrder itself, before
	// SubmitOrder returns to this function — see strategies/orb's enter(),
	// which sets its own entryOrder field for the identical reason.
	// Recording the reservation only after the call would make OnFill see
	// an unrecognised order id for every synchronous fill and silently
	// stay flat forever.
	s.inFlight[symbol] += delta
	s.orders[orderID] = orderMeta{symbol: symbol, lots: delta}

	if _, err := eng.SubmitOrder(ctx, intent); err != nil {
		// The order never reached the market: reverse the reservation so a
		// rejection cannot leave phantom exposure behind.
		s.inFlight[symbol] -= delta
		delete(s.orders, orderID)
		s.rejects++
		return
	}
}

// OnFill resolves a fill back to the symbol and signed lot delta the
// strategy recorded when it submitted the order (see rebalanceOne), moves
// that many lots out of in-flight and into confirmed, and forgets the
// order id. An unknown order id — one this strategy never submitted, or
// (defensively) a second fill callback for one already resolved — is
// ignored rather than treated as an error: OnFill runs on a control path
// this strategy does not own the other end of, and a strategy panicking on
// an unrecognised id would let a single unrelated fill (or a resent one)
// take the whole engine down.
func (s *Strategy) OnFill(ctx context.Context, f engine.FillRecord) {
	meta, ok := s.orders[string(f.OrderID)]
	if !ok {
		return
	}
	delete(s.orders, string(f.OrderID))

	s.inFlight[meta.symbol] -= meta.lots
	s.confirmed[meta.symbol] += meta.lots
}

// newOrderID mints the client order id for one (symbol, window) rebalance
// attempt, human-readable for store dumps and logs.
//
// Deliberately a pure function of (symbol, window) — NOT a process-local
// counter, which is what this used to be (a monotonic nextID, matching
// strategies/orb's own newOrderID). A counter restarts at 0 on every
// process start, so a same-day crash-and-restart minted the SAME id
// ("rebalance-TINS-1") for what OnEvent's own attempted-key logic already
// treats as at most one attempt — except the counter's restart silently
// defeated the very durability that guarantee depends on. Keying on window
// instead makes the id itself restart-stable: the same logical action gets
// the same id whether attempted twice in one process or once each on
// either side of a crash, which is what lets strategy/live derive a
// restart-stable ui_ref from it too (live.DeterministicUIRef) and Carina's
// own idempotency guard catch a resubmission that slips past
// Options.Attempted for any reason. See plan 18 §5 B4's restart-durability
// gap.
func (s *Strategy) newOrderID(symbol, window string) string {
	return fmt.Sprintf("rebalance-%s-%s", symbol, window)
}

// sharesPerLot mirrors strategy/engine/sink.go's and
// strategy/risk/local_checks.go's own constant: 1 lot = 100 shares on the
// IDX (docs/reference/06-pre-trade-rules.md §6).
const sharesPerLot = 100

// snapTickIDR truncates priceIDR down to the nearest valid tick under the
// same coarse, price-banded approximation strategy/risk/local_checks.go's
// isOnApproxTickIDR checks against and strategy/broker.go's roundTick
// snaps to (docs/reference/06-pre-trade-rules.md §5). This package cannot
// import either — local_checks.go's table is unexported inside strategy/risk,
// and broker.go's roundTick lives in package main — so this is a third,
// independent copy of the same table that must be kept in sync with both
// by hand. It exists because a strategy's emitted PriceIDR must already be
// on-tick before it ever reaches strategy/risk: checkStructural rejects an
// off-tick intent outright, with no rounding of its own, so a strategy
// that skipped this step would have every order it ever placed rejected
// the moment its raw last-observed price landed off the grid (which is
// most of the time, since a liveprice tick is not tick-quantized to begin
// with).
func snapTickIDR(price float64) int64 {
	p := int64(price)
	var tick int64
	switch {
	case p < 200:
		tick = 1
	case p < 500:
		tick = 2
	case p < 2000:
		tick = 5
	case p < 5000:
		tick = 10
	default:
		tick = 25
	}
	return p - (p % tick)
}
