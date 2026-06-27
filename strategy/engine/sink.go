package engine

import (
	"context"
	"errors"
	"math"

	"collector/feed"
)

const (
	sharesPerLot = 100
	feeRateBuy   = 0.0015 // 15 bps
	feeRateSell  = 0.0025 // 25 bps
)

// OrderSink accepts order intents and reports executions. PaperSink is the only
// implementation built for plan 17; CarinaSink (live routing) is deferred.
type OrderSink interface {
	Submit(ctx context.Context, intent OrderIntent) (OrderID, error)
	Cancel(ctx context.Context, id OrderID) error
}

// ErrNoBook is returned when a fill is requested for a symbol whose far touch
// has not yet been observed.
var ErrNoBook = errors.New("paper sink: no book observed for symbol")

// touch holds the best bid and ask seen so far for one symbol.
type touch struct {
	bid    int64
	ask    int64
	hasBid bool
	hasAsk bool
}

// FillMode controls how PaperSink derives the fill price from the order book.
type FillMode int

const (
	// FillModeTaker fills at the far touch (ask for buys, bid for sells).
	// This is the default and models a market taker crossing the full spread.
	FillModeTaker FillMode = iota

	// FillModeMid fills at the mid-price when both sides of the book are known,
	// falling back to the far touch when only one side has been observed.
	// This matches plan-10's close-price methodology (bar closes ≈ mid-price)
	// and removes the structural spread cost from gross PnL, making the
	// event-driven results directly comparable to plan-10's CSV backtest.
	FillModeMid
)

// PaperSink simulates immediate fills against the last observed order book.
// Book state is maintained per symbol from Observe and cleared on Reset.
type PaperSink struct {
	books    map[string]*touch
	last     FillRecord
	fillMode FillMode
}

// NewPaperSink returns an empty PaperSink using taker fills (far touch).
func NewPaperSink() *PaperSink {
	return &PaperSink{books: make(map[string]*touch)}
}

// NewMidPriceSink returns a PaperSink that fills at the mid-price when both
// sides of the book are known, falling back to far touch otherwise.
func NewMidPriceSink() *PaperSink {
	return &PaperSink{books: make(map[string]*touch), fillMode: FillModeMid}
}

// Observe updates the last-seen book from a market event. Orderbook frames
// carry one side (BID or OFFER); liveprice frames seed both touches when no
// book has been seen yet so that the very first breakout can still fill.
func (p *PaperSink) Observe(e MarketEvent) {
	switch e.Kind {
	case EventControl:
		if e.Control != nil && (e.Control.Event == "RECONNECT" || e.Control.Event == "SWITCH_RESET") {
			p.Reset(e.Control.Symbol)
		}
	case EventOrderbook:
		if e.Decoded == nil {
			return
		}
		side, ok := feed.ParseOrderbookBody(e.Decoded.Body)
		if !ok || len(side.Levels) == 0 {
			return
		}
		px := int64(math.Round(side.Levels[0].Price))
		t := p.touchFor(e.Symbol)
		if side.Side == "BID" {
			t.bid, t.hasBid = px, true
		} else if side.Side == "OFFER" {
			t.ask, t.hasAsk = px, true
		}
	case EventLivePrice:
		if e.Decoded == nil || e.Decoded.LastPrice <= 0 {
			return
		}
		px := int64(math.Round(e.Decoded.LastPrice))
		t := p.touchFor(e.Symbol)
		if !t.hasBid {
			t.bid, t.hasBid = px, true
		}
		if !t.hasAsk {
			t.ask, t.hasAsk = px, true
		}
	}
}

// Reset clears book state. An empty symbol clears every symbol (a full
// RECONNECT); a specific symbol clears only that one (SWITCH_RESET).
func (p *PaperSink) Reset(symbol string) {
	if symbol == "" {
		p.books = make(map[string]*touch)
		return
	}
	delete(p.books, symbol)
}

func (p *PaperSink) touchFor(symbol string) *touch {
	t, ok := p.books[symbol]
	if !ok {
		t = &touch{}
		p.books[symbol] = t
	}
	return t
}

// Submit fills the intent in full at the current book price and returns the
// resulting FillRecord embedded in the OrderID lookup via LastFill. The fill
// price is determined by the sink's FillMode: taker (far touch) or mid-price.
func (p *PaperSink) Submit(ctx context.Context, intent OrderIntent) (OrderID, error) {
	t, ok := p.books[intent.Symbol]
	if !ok {
		return "", ErrNoBook
	}

	var fillPx int64
	if p.fillMode == FillModeMid && t.hasBid && t.hasAsk {
		fillPx = (t.bid + t.ask) / 2
	} else {
		switch intent.Side {
		case "buy":
			if !t.hasAsk {
				return "", ErrNoBook
			}
			fillPx = t.ask
		case "sell":
			if !t.hasBid {
				return "", ErrNoBook
			}
			fillPx = t.bid
		default:
			return "", errors.New("paper sink: side must be buy or sell")
		}
	}
	// Validate side when using mid fill (the switch above is skipped).
	if p.fillMode == FillModeMid {
		switch intent.Side {
		case "buy", "sell":
		default:
			return "", errors.New("paper sink: side must be buy or sell")
		}
	}

	notional := fillPx * int64(intent.Lots) * sharesPerLot
	rate := feeRateBuy
	if intent.Side == "sell" {
		rate = feeRateSell
	}
	fee := int64(math.Round(float64(notional) * rate))

	var slippage float64
	if intent.PriceIDR > 0 {
		slippage = float64(fillPx-intent.PriceIDR) / float64(intent.PriceIDR) * 10000
	}

	p.last = FillRecord{
		OrderID:      OrderID(intent.OrderID),
		FillPriceIDR: fillPx,
		FillLots:     intent.Lots,
		FeeIDR:       fee,
		SlippageBps:  slippage,
	}
	return OrderID(intent.OrderID), nil
}

// LastFill returns the FillRecord produced by the most recent successful Submit.
func (p *PaperSink) LastFill() FillRecord { return p.last }

// Cancel is a no-op for the paper sink: fills are immediate, so nothing is open.
func (p *PaperSink) Cancel(ctx context.Context, id OrderID) error { return nil }
