package engine

import (
	"database/sql"
	"math"

	"collector/feed"
)

// quoteBook tracks, per symbol, the best bid, best ask and last price seen on
// the market-event stream, each with the time it was observed. It exists so
// the order path can record the market at the moment an order is submitted
// (order_intent.arrival_*), independent of which sink handles the order: the
// engine's Run loop feeds it, so paper and live runs record identically.
//
// It never fabricates: a side that has not been observed is absent, and a
// liveprice frame never seeds a bid or ask (PaperSink does that for fills;
// recording it as a quote would make the arrival mid equal the last trade).
type quoteBook struct {
	syms map[string]*quoteState
}

type quoteState struct {
	bid, ask, last       int64
	bidNs, askNs, lastNs int64
	hasBid, hasAsk       bool
	hasLast              bool
}

func newQuoteBook() *quoteBook { return &quoteBook{syms: make(map[string]*quoteState)} }

// observe folds one market event into the book. Control RECONNECT / SWITCH_RESET
// clear the affected symbol (all symbols when none is named), as PaperSink does:
// a quote from before a reconnect is not the market now.
func (q *quoteBook) observe(e MarketEvent) {
	switch e.Kind {
	case EventControl:
		if e.Control == nil || (e.Control.Event != "RECONNECT" && e.Control.Event != "SWITCH_RESET") {
			return
		}
		if e.Control.Symbol == "" {
			q.syms = make(map[string]*quoteState)
		} else {
			delete(q.syms, e.Control.Symbol)
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
		s := q.state(e.Symbol)
		switch side.Side {
		case "BID":
			s.bid, s.bidNs, s.hasBid = px, e.RecvNs, true
		case "OFFER":
			s.ask, s.askNs, s.hasAsk = px, e.RecvNs, true
		}
	case EventLivePrice:
		if e.Decoded == nil || e.Decoded.LastPrice <= 0 {
			return
		}
		s := q.state(e.Symbol)
		s.last, s.lastNs, s.hasLast = int64(math.Round(e.Decoded.LastPrice)), e.RecvNs, true
	}
}

func (q *quoteBook) state(symbol string) *quoteState {
	s, ok := q.syms[symbol]
	if !ok {
		s = &quoteState{}
		q.syms[symbol] = s
	}
	return s
}

// ArrivalQuote is the market as last observed when an order was submitted.
// Every field is nullable: NULL means "not observed", never zero.
type ArrivalQuote struct {
	BidIDR  sql.NullInt64
	AskIDR  sql.NullInt64
	MidIDR  sql.NullFloat64 // (bid+ask)/2, only when both sides are known and not crossed
	LastIDR sql.NullInt64
	// QuoteNs is when the book was last refreshed: the OLDER of the bid and
	// ask observation times, so the age is the pessimistic one.
	QuoteNs sql.NullInt64
	// AgeMs is submitNs - QuoteNs in milliseconds, never negative.
	AgeMs sql.NullInt64
}

func ageMs(submitNs, quoteNs int64) sql.NullInt64 {
	age := (submitNs - quoteNs) / 1_000_000
	if age < 0 {
		age = 0
	}
	return sql.NullInt64{Int64: age, Valid: true}
}

// snapshot returns the quote for symbol as of submitNs, or nil when nothing
// has been observed. A crossed book (bid > ask, seen around auctions) yields
// no bid/ask/mid: its midpoint is not a price anyone could trade at. A
// one-sided book records the side it has and no mid.
func (q *quoteBook) snapshot(symbol string, submitNs int64) *ArrivalQuote {
	s, ok := q.syms[symbol]
	if !ok {
		return nil
	}
	out := &ArrivalQuote{}
	if s.hasLast {
		out.LastIDR = sql.NullInt64{Int64: s.last, Valid: true}
	}
	crossed := s.hasBid && s.hasAsk && s.bid > s.ask
	if !crossed {
		var quoteNs int64
		switch {
		case s.hasBid && s.hasAsk:
			out.MidIDR = sql.NullFloat64{Float64: float64(s.bid+s.ask) / 2, Valid: true}
			quoteNs = s.bidNs
			if s.askNs < quoteNs {
				quoteNs = s.askNs
			}
		case s.hasBid:
			quoteNs = s.bidNs
		case s.hasAsk:
			quoteNs = s.askNs
		}
		if s.hasBid {
			out.BidIDR = sql.NullInt64{Int64: s.bid, Valid: true}
		}
		if s.hasAsk {
			out.AskIDR = sql.NullInt64{Int64: s.ask, Valid: true}
		}
		if s.hasBid || s.hasAsk {
			out.QuoteNs = sql.NullInt64{Int64: quoteNs, Valid: true}
			out.AgeMs = ageMs(submitNs, quoteNs)
		}
	}
	if !out.LastIDR.Valid && !out.BidIDR.Valid && !out.AskIDR.Valid {
		return nil
	}
	return out
}
