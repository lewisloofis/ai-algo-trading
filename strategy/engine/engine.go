package engine

import (
	"context"
	"database/sql"
	"time"
)

// Counters summarise a run for the CLI.
type Counters struct {
	OrdersAttempted int
	Fills           int
	TotalFeesIDR    int64   // sum of fees paid across all fills
	RoundTrips      int     // completed entry+exit pairs
	NetPnlBps       float64 // mean net PnL across completed round-trips, in bps
}

// fillProvider is implemented by sinks (PaperSink) that produce a fill
// synchronously on Submit and expose it for delivery to the strategy.
type fillProvider interface {
	LastFill() FillRecord
}

// observer is implemented by sinks that track book state from the event stream.
type observer interface {
	Observe(e MarketEvent)
}

// openLeg is an entry fill awaiting its closing fill, used to build attribution.
type openLeg struct {
	side      string
	priceIDR  int64
	lots      int
	feeIDR    int64
	recvNs    int64
	orderID   string
}

// runState is shared across every engineHandle in a single Run. It holds the
// global seq counter, the run counters, and the per-symbol open leg used to
// pair entries with exits into attribution rows.
type runState struct {
	seq      int64
	counters Counters
	open     map[string]*openLeg
	sumBps   float64
}

// engineHandle is the per-event Engine passed to a Strategy. It carries the
// clock for the event being processed and routes SubmitOrder through the sink
// and store.
type engineHandle struct {
	sink    OrderSink
	store   *PostTradeStore
	clock   Clock
	strat   Strategy
	stratID string
	recvNs  int64
	state   *runState
}

func (h *engineHandle) Now() time.Time { return h.clock.Now() }

// SubmitOrder logs the intent, submits to the sink, and on a successful fill
// logs the fill, pairs it into an attribution round-trip, and delivers it to
// the strategy via OnFill — all synchronously on the event-loop goroutine.
func (h *engineHandle) SubmitOrder(ctx context.Context, intent OrderIntent) (OrderID, error) {
	st := h.state
	st.counters.OrdersAttempted++

	st.seq++
	if err := h.store.InsertIntent(IntentRow{
		Seq:        st.seq,
		RecvNs:     h.recvNs,
		StrategyID: h.stratID,
		Symbol:     intent.Symbol,
		Side:       intent.Side,
		PriceIDR:   intent.PriceIDR,
		Lots:       intent.Lots,
		OrderID:    intent.OrderID,
		SinkKind:   "paper",
	}); err != nil {
		return "", err
	}

	id, err := h.sink.Submit(ctx, intent)
	if err != nil {
		return "", err
	}

	fp, ok := h.sink.(fillProvider)
	if !ok {
		return id, nil
	}
	fill := fp.LastFill()

	st.counters.Fills++
	st.counters.TotalFeesIDR += fill.FeeIDR

	st.seq++
	if err := h.store.InsertFill(FillRow{
		Seq:          st.seq,
		RecvNs:       h.recvNs,
		OrderID:      string(fill.OrderID),
		FillPriceIDR: fill.FillPriceIDR,
		FillLots:     fill.FillLots,
		FeeIDR:       fill.FeeIDR,
		SlippageBps:  fill.SlippageBps,
		NetPnlIDR:    sql.NullInt64{},
		SinkKind:     "paper",
	}); err != nil {
		return "", err
	}

	if err := h.recordRoundTrip(intent.Symbol, intent.Side, fill); err != nil {
		return "", err
	}

	h.strat.OnFill(ctx, fill)
	return id, nil
}

// recordRoundTrip pairs an entry fill with its later closing fill. The first
// fill for a symbol opens a leg; the next opposite-side fill closes it and
// writes one attribution row.
func (h *engineHandle) recordRoundTrip(symbol, side string, fill FillRecord) error {
	st := h.state
	leg, isOpen := st.open[symbol]
	// A leg closes only on the opposite side. A same-side fill (or the first
	// fill) opens/replaces the leg rather than pairing into a bogus round-trip.
	if !isOpen || leg.side == side {
		st.open[symbol] = &openLeg{
			side:     side,
			priceIDR: fill.FillPriceIDR,
			lots:     fill.FillLots,
			feeIDR:   fill.FeeIDR,
			recvNs:   h.recvNs,
			orderID:  string(fill.OrderID),
		}
		return nil
	}

	grossBps := signedReturnBps(leg.side, leg.priceIDR, fill.FillPriceIDR)
	notional := float64(leg.priceIDR * int64(leg.lots) * sharesPerLot)
	feeBps := 0.0
	if notional > 0 {
		feeBps = float64(leg.feeIDR+fill.FeeIDR) / notional * 10000
	}
	netBps := grossBps - feeBps
	holdDays := float64(h.recvNs-leg.recvNs) / float64(24*time.Hour)

	st.seq++
	if err := h.store.InsertAttribution(AttributionRow{
		Seq:              st.seq,
		Symbol:           symbol,
		StrategyID:       h.stratID,
		EntryRecvNs:      leg.recvNs,
		ExitRecvNs:       h.recvNs,
		GrossPnlBps:      grossBps,
		NetPnlBps:        netBps,
		EntryOrderID:     leg.orderID,
		ExitOrderID:      string(fill.OrderID),
		HoldCalendarDays: holdDays,
	}); err != nil {
		return err
	}

	st.counters.RoundTrips++
	st.sumBps += netBps
	delete(st.open, symbol)
	return nil
}

// signedReturnBps returns the entry-direction return in bps. A long earns when
// the exit price is higher; a short earns when it is lower.
func signedReturnBps(entrySide string, entryPx, exitPx int64) float64 {
	if entryPx <= 0 {
		return 0
	}
	raw := float64(exitPx-entryPx) / float64(entryPx) * 10000
	if entrySide == "sell" {
		return -raw
	}
	return raw
}

// Run is the event loop. It consumes MarketEvents from src, feeds each to any
// book-tracking sink, logs a decision row, and dispatches OnEvent. It returns
// nil at a clean end of stream and ctx.Err() on cancellation.
func Run(ctx context.Context, src <-chan MarketEvent, strat Strategy, sink OrderSink, store *PostTradeStore, stratID string) (Counters, error) {
	st := &runState{open: make(map[string]*openLeg)}

	for {
		select {
		case <-ctx.Done():
			return finalize(st), ctx.Err()
		case evt, ok := <-src:
			if !ok {
				return finalize(st), nil
			}

			if obs, ok := sink.(observer); ok {
				obs.Observe(evt)
			}

			st.seq++
			if err := store.InsertDecision(DecisionRow{
				Seq:         st.seq,
				RecvNs:      evt.RecvNs,
				StrategyID:  stratID,
				Symbol:      evt.Symbol,
				EventKind:   string(evt.Kind),
				SignalValue: signalValue(strat, evt),
			}); err != nil {
				return finalize(st), err
			}

			h := &engineHandle{
				sink:    sink,
				store:   store,
				clock:   replayClock{ns: evt.RecvNs},
				strat:   strat,
				stratID: stratID,
				recvNs:  evt.RecvNs,
				state:   st,
			}
			strat.OnEvent(ctx, evt, h)
		}
	}
}

func finalize(st *runState) Counters {
	c := st.counters
	if c.RoundTrips > 0 {
		c.NetPnlBps = st.sumBps / float64(c.RoundTrips)
	}
	return c
}

// SignalReporter lets a Strategy expose a scalar signal for the decision log.
type SignalReporter interface {
	SignalValue(e MarketEvent) (float64, bool)
}

func signalValue(strat Strategy, e MarketEvent) sql.NullFloat64 {
	if sr, ok := strat.(SignalReporter); ok {
		if v, present := sr.SignalValue(e); present {
			return sql.NullFloat64{Float64: v, Valid: true}
		}
	}
	return sql.NullFloat64{}
}
