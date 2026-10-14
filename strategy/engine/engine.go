package engine

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	"collector/calendar"
)

// Counters summarise a run. They are returned by Run at the end, and
// published continuously through RunProgress while it is still going.
//
// The JSON tags are load-bearing: these fields reach status.json, and the
// dashboard's glossary keys them by the snake_case name derived from the Go
// field. Without the tags the file would carry OrdersAttempted while every
// reader looked for orders_attempted.
type Counters struct {
	// OrdersAttempted is how many intents the strategy submitted, whether or
	// not they filled. The gap between this and Fills is orders the sink
	// refused — no book, a risk gate, a broker rejection.
	OrdersAttempted int `json:"orders_attempted"`
	// Fills is how many executions came back. One round-trip is two fills.
	Fills int `json:"fills"`
	// TotalFeesIDR is the sum of commission paid across all fills, in whole
	// IDR. It does not include slippage, which is priced into the fill price
	// itself, nor financing or impact, which the simulator does not model.
	TotalFeesIDR int64 `json:"total_fees_idr"`
	// RoundTrips is the number of completed entry+exit pairs. An open position
	// at the end of a run is not counted — it has no realised PnL yet.
	RoundTrips int `json:"round_trips"`
	// NetPnlBps is the MEAN net PnL across completed round-trips, in basis
	// points — not a total and not compounded. Zero when RoundTrips is zero,
	// which is "nothing closed", not "broke even".
	NetPnlBps float64 `json:"net_pnl_bps"`
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

// asyncSink is implemented by sinks whose fills arrive out of band — a live
// order router, whose executions come back from the broker on their own
// schedule, rather than synchronously from Submit like PaperSink. Run
// multiplexes this channel alongside the market-event stream; see the
// comment on the `fills` variable in Run for why a sink that does not
// implement this (every sink built before this seam existed) needs no
// special case.
type asyncSink interface {
	Fills() <-chan AsyncFill
}

// asyncRejecter is an OPTIONAL companion to asyncSink: implemented by sinks
// that learn of a broker rejection only after Submit returned success. Run
// records each AsyncReject as an order_reject row. A sink that does not
// implement it yields a nil channel, which is never ready in a select —
// the same no-special-case treatment as fills.
type asyncRejecter interface {
	Rejects() <-chan AsyncReject
}

// sinkKinder is implemented by a sink that names itself for the store's
// sink_kind column (see IntentRow.SinkKind). A sink that does not implement
// it — PaperSink, and every sink that predates this interface — records
// "paper", the hardcoded value every intent and fill row carried before
// sink_kind became a real per-sink property.
type sinkKinder interface {
	SinkKind() string
}

// openLeg is an entry fill awaiting its closing fill, used to build attribution.
type openLeg struct {
	side     string
	priceIDR int64
	lots     int
	feeIDR   int64
	recvNs   int64
	orderID  string
}

// runState is shared across every engineHandle in a single Run. It holds the
// global seq counter, the run counters, and the per-symbol open leg used to
// pair entries with exits into attribution rows.
type runState struct {
	seq      int64
	counters Counters
	open     map[string]*openLeg
	sumBps   float64

	// progress, when non-nil, receives a snapshot after every event. It is
	// the only thing in a run that another goroutine reads, and it is
	// write-only from here: nothing in the event loop ever reads it back, so
	// a slow or absent status writer cannot affect the run.
	progress *RunProgress
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

	// sinkKind is resolved once per Run (see sinkKinder) and stamped onto
	// every intent and fill row this handle writes, whether the fill came
	// back synchronously from Submit or later from the sink's Fills()
	// channel — both paths report the same sink, so both must record the
	// same kind.
	sinkKind string
}

func (h *engineHandle) Now() time.Time { return h.clock.Now() }

// SubmitOrder logs the intent, submits to the sink, and — only for a sink
// that fills synchronously (fillProvider) — applies the resulting fill
// before returning. A sink whose fills arrive later on Fills() returns here
// with no fill at all; Run's select loop applies that fill through the same
// applyFill method once it arrives, so the two paths converge on identical
// bookkeeping rather than each maintaining their own.
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
		SinkKind:   h.sinkKind,
	}); err != nil {
		return "", err
	}

	id, err := h.sink.Submit(ctx, intent)
	if err != nil {
		// The intent row above already recorded the attempt; this records
		// why it went no further — a risk gate, a broker rejection, or (for
		// PaperSink) ErrNoBook. Before this table existed, that reason lived
		// only in the returned error, which a caller could drop on the
		// floor; see RejectRow's doc comment.
		if rejErr := h.recordReject(intent.Symbol, intent.Side, intent.PriceIDR, intent.Lots, intent.OrderID, err.Error()); rejErr != nil {
			return "", fmt.Errorf("submit order: sink rejected (%w) and recording the rejection also failed: %v", err, rejErr)
		}
		return "", err
	}

	fp, ok := h.sink.(fillProvider)
	if !ok {
		// Nothing to apply yet: either the sink never fills (unlikely, but
		// not this method's business to assume) or it fills asynchronously
		// and will report through Fills() instead — see Run.
		return id, nil
	}
	if err := h.applyFill(ctx, intent.Symbol, intent.Side, fp.LastFill()); err != nil {
		return "", err
	}
	return id, nil
}

// recordReject writes one order_reject row, bumping the run's seq. It is the
// single implementation behind both the synchronous Submit-error path and
// Run's asynchronous Rejects() path.
func (h *engineHandle) recordReject(symbol, side string, priceIDR int64, lots int, orderID, reason string) error {
	st := h.state
	st.seq++
	return h.store.InsertOrderReject(RejectRow{
		Seq:        st.seq,
		RecvNs:     h.recvNs,
		StrategyID: h.stratID,
		Symbol:     symbol,
		Side:       side,
		PriceIDR:   priceIDR,
		Lots:       lots,
		OrderID:    orderID,
		Reason:     reason,
		SinkKind:   h.sinkKind,
	})
}

// asyncRejectReasonPrefix marks an order_reject row as a post-submission
// broker rejection, distinguishing it from a Submit-time refusal.
const asyncRejectReasonPrefix = "broker rejected after submit: "

// applyFill records one execution — counters, the fill row, attribution
// pairing, and the Strategy.OnFill callback — and is the single
// implementation both SubmitOrder's synchronous fillProvider path and Run's
// asynchronous Fills() path call. A live fill and a paper fill are booked
// through the exact same sequence of steps; duplicating this per path would
// let one silently drift from the other (a missed counter, a skipped store
// insert) with nothing to catch it.
//
// Both call sites run applyFill from the event-loop goroutine — SubmitOrder
// directly, and Run's select loop for an async fill — so Strategy.OnFill
// keeps its documented single-goroutine contract regardless of which path
// produced the execution.
func (h *engineHandle) applyFill(ctx context.Context, symbol, side string, fill FillRecord) error {
	st := h.state
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
		SinkKind:     h.sinkKind,
	}); err != nil {
		return err
	}

	if err := h.recordRoundTrip(symbol, side, fill); err != nil {
		return err
	}

	h.strat.OnFill(ctx, fill)
	return nil
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
		AsOfDate:         JakartaDate(h.recvNs),
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
//
// Optional behaviour is passed as RunOptions; a call with none behaves exactly
// as before, which is why the parameter is variadic rather than a new required
// argument threaded through every call site.
func Run(ctx context.Context, src <-chan MarketEvent, strat Strategy, sink OrderSink, store *PostTradeStore, stratID string, opts ...RunOption) (Counters, error) {
	cfg := runOptions{}
	for _, o := range opts {
		o(&cfg)
	}
	st := &runState{open: make(map[string]*openLeg), progress: cfg.progress}
	st.publish(0)

	// sinkKind is resolved once for the whole run, not per intent or fill:
	// it is a property of which sink this run wired up, and every row the
	// run writes must agree on it. "paper" is the pre-existing hardcoded
	// default for any sink that does not name itself via sinkKinder.
	sinkKind := "paper"
	if sk, ok := sink.(sinkKinder); ok {
		sinkKind = sk.SinkKind()
	}

	// fills is nil unless sink reports its executions out of band
	// (asyncSink) — a live order router rather than PaperSink. A nil
	// channel is never ready in a select, so the fills case below simply
	// never fires for every sink that predates this seam; that is exactly
	// the correct paper-path behaviour, not a special case to guard against.
	var fills <-chan AsyncFill
	if as, ok := sink.(asyncSink); ok {
		fills = as.Fills()
	}

	// rejects mirrors fills: nil unless the sink implements asyncRejecter.
	var rejects <-chan AsyncReject
	if ar, ok := sink.(asyncRejecter); ok {
		rejects = ar.Rejects()
	}

	newHandle := func(recvNs int64) *engineHandle {
		return &engineHandle{
			sink:     sink,
			store:    store,
			clock:    replayClock{ns: recvNs},
			strat:    strat,
			stratID:  stratID,
			recvNs:   recvNs,
			state:    st,
			sinkKind: sinkKind,
		}
	}

	for {
		select {
		case <-ctx.Done():
			return finalize(st), ctx.Err()

		case af, ok := <-fills:
			if !ok {
				// The sink closed its Fills channel (e.g. on Close() during
				// shutdown). That is not the run ending — the market-event
				// channel alone decides that — so stop selecting on this
				// one and keep going.
				fills = nil
				continue
			}
			// Applied on this goroutine, exactly like a synchronous fill,
			// so Strategy.OnFill's single-goroutine contract holds
			// regardless of which path produced the execution.
			h := newHandle(af.RecvNs)
			if err := h.applyFill(ctx, af.Symbol, af.Side, af.Fill); err != nil {
				return finalize(st), err
			}
			st.publish(af.RecvNs)

		case ar, ok := <-rejects:
			if !ok {
				// Closed on sink shutdown; like fills, not the run ending.
				rejects = nil
				continue
			}
			h := newHandle(ar.RecvNs)
			if err := h.recordReject(ar.Symbol, ar.Side, ar.PriceIDR, ar.Lots, ar.OrderID, asyncRejectReasonPrefix+ar.Reason); err != nil {
				return finalize(st), err
			}
			st.publish(ar.RecvNs)

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

			h := newHandle(evt.RecvNs)
			strat.OnEvent(ctx, evt, h)
			st.publish(evt.RecvNs)
		}
	}
}

// RunOption configures an optional Run behaviour.
type RunOption func(*runOptions)

type runOptions struct {
	progress *RunProgress
}

// WithProgress publishes a snapshot of the run to p after every event, so an
// out-of-process observer (strategy-run's status.json writer) can see a run
// that is still going. Passing nil is a no-op.
func WithProgress(p *RunProgress) RunOption {
	return func(o *runOptions) { o.progress = p }
}

// publish pushes the current counters to the progress sink, if there is one.
// lastEventNs is 0 for the pre-loop publish, which exists so a status file
// written before the first event says "0 events" rather than not existing.
func (st *runState) publish(lastEventNs int64) {
	if st.progress == nil {
		return
	}
	c := st.counters
	if c.RoundTrips > 0 {
		c.NetPnlBps = st.sumBps / float64(c.RoundTrips)
	}
	st.progress.set(lastEventNs, c)
}

// JakartaDate renders a Unix-nanosecond timestamp as the exchange's calendar
// date (YYYY-MM-DD) in WIB. Used for attribution.as_of_date: a UTC host and
// the exchange disagree about the date for the seven hours after Jakarta
// midnight, and a per-day PnL keyed on the host's date would silently split a
// session in two. calendar.Jakarta is the same zone the live source's
// trading-day check uses.
func JakartaDate(ns int64) string {
	if ns == 0 {
		return ""
	}
	return time.Unix(0, ns).In(calendar.Jakarta).Format("2006-01-02")
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
