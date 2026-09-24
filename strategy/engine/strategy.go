// Package engine is the event-driven backtesting core for plan 17. It wires
// the plan-02 replayer into a Strategy interface, simulates taker fills through
// an OrderSink, and records every decision, intent, and fill into an
// append-only SQLite post-trade store.
//
// An OrderSink is not required to fill synchronously from Submit. PaperSink
// does — it fills before Submit returns and reports back through the
// unexported fillProvider assertion — but a sink may instead report its
// executions later, out of band, on its own schedule: see AsyncFill and
// Run's handling of the unexported asyncSink and sinkKinder assertions in
// engine.go. This is the seam plan 18 §5 B4's live order router
// (strategy/live) plugs into: Run does not know or care whether a fill came
// from PaperSink's book simulation or a broker's fill feed, only that both
// are applied to the strategy on the same event-loop goroutine, through the
// same bookkeeping, and recorded under the sink's own declared SinkKind.
package engine

import (
	"context"
	"time"

	"collector/feed"
	"collector/replay"
	"collector/segment"
)

// EventKind re-exports the replayer's event categories so strategy code can
// depend on engine alone.
type EventKind = replay.EventKind

const (
	EventLivePrice    = replay.EventLivePrice
	EventOrderbook    = replay.EventOrderbook
	EventRunningTrade = replay.EventRunningTrade
	EventIEPIEV       = replay.EventIEPIEV
	EventControl      = replay.EventControl
)

// MarketEvent is one decoded event delivered to a Strategy. It mirrors
// replay.MarketEvent; the two have identical fields and are converted with
// fromReplay so the engine package owns its public surface.
type MarketEvent struct {
	RecvNs  int64
	Kind    EventKind
	Symbol  string
	Decoded *feed.Decoded
	Control *segment.ControlEvent
}

func fromReplay(e replay.MarketEvent) MarketEvent {
	return MarketEvent{
		RecvNs:  e.RecvNs,
		Kind:    e.Kind,
		Symbol:  e.Symbol,
		Decoded: e.Decoded,
		Control: e.Control,
	}
}

// OrderID identifies an order within a single run.
type OrderID string

// BoardType is the IDX trading venue an order is routed to
// (docs/reference/06-pre-trade-rules.md §8). It exists as a field on
// OrderIntent, rather than as a constant inside the router, because the two
// are different claims: the router knows which board it sends to, and only
// the strategy knows which board it meant. Sending an ordinary-share order
// to TN routes it to a separate book at a different settlement cycle, where
// it may be accepted and then never match — a failure that is silent on
// both sides unless the intent carries the strategy's own answer for
// strategy/risk to check.
type BoardType string

const (
	// BoardRG is the regular continuous-auction board: ordinary shares,
	// T+2. The only board this repo routes to (plan 18 §11), and therefore
	// the only value strategy/risk admits.
	BoardRG BoardType = "RG"
	// BoardTN is the cash/negotiated board (T+0), which rights issues use.
	BoardTN BoardType = "TN"
	// BoardNG is the new/emerging-growth board.
	BoardNG BoardType = "NG"
)

// OrderIntent is a strategy's request to trade. PriceIDR must already be
// tick-snapped by the strategy; OrderID is the client-assigned idempotency key.
type OrderIntent struct {
	Symbol   string
	Side     string // "buy" or "sell"
	PriceIDR int64
	Lots     int // 1 lot = 100 shares
	OrderID  string

	// BoardType must be set explicitly. The zero value is NOT treated as
	// BoardRG: strategy/risk rejects it, for the same reason risk.TriState
	// is not a bool — a zero value that reads as a confident, correct
	// answer is indistinguishable from a field nobody filled in. A
	// strategy that means the regular board says so.
	BoardType BoardType
}

// FillRecord is the result of a simulated execution, handed back to the
// strategy via OnFill and persisted to the store.
type FillRecord struct {
	OrderID      OrderID
	FillPriceIDR int64
	FillLots     int
	FeeIDR       int64
	SlippageBps  float64
}

// Strategy is implemented by trading logic. OnEvent runs synchronously on the
// event-loop goroutine; it must not spawn goroutines or read time.Now directly
// (use engine.Now). OnFill is invoked when the sink reports an execution.
type Strategy interface {
	OnEvent(ctx context.Context, e MarketEvent, engine Engine)
	OnFill(ctx context.Context, f FillRecord)
}

// Engine is the handle a Strategy uses to act on the world during OnEvent.
type Engine interface {
	SubmitOrder(ctx context.Context, intent OrderIntent) (OrderID, error)
	Now() time.Time
}
