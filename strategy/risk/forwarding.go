package risk

import "strategy/engine"

// engine.Run reaches past the engine.OrderSink interface with two
// optional-interface type assertions rather than requiring either
// capability of every sink:
//
//	strategy/engine/engine.go:238  if obs, ok := sink.(observer); ok { obs.Observe(evt) }
//	strategy/engine/engine.go:117  fp, ok := h.sink.(fillProvider); if !ok { return id, nil }
//
// fillProvider and observer below are declared structurally identical to
// engine's own unexported interfaces so RiskSink can participate in the
// same duck typing without engine exporting anything new. If engine.Run
// ever stops consulting either assertion, nothing here breaks at compile
// time — forwarding_test.go, which runs intents through the real
// engine.Run rather than asserting on method sets, is what would notice.

// fillProvider mirrors engine's fillProvider: sinks that produce a fill
// synchronously on Submit and expose it for engine.Run to read back.
type fillProvider interface {
	LastFill() engine.FillRecord
}

// observer mirrors engine's observer: sinks that track book state from the
// event stream.
type observer interface {
	Observe(e engine.MarketEvent)
}

// Observe forwards e to the inner sink when inner tracks book state
// (implements observer), and is a no-op otherwise. It lives on *RiskSink
// itself, unconditionally, because forwarding a market event has no
// zero-value hazard the way forwarding a fill does: an inner sink that
// ignores Observe already fails its own way when asked to fill (PaperSink
// returns ErrNoBook rather than fabricating a price), so RiskSink answering
// observer unconditionally never turns "inner doesn't track books" into a
// wrong answer — only EngineSink's fillProvider mirroring needs to be
// conditional. See EngineSink's doc comment for that asymmetry.
func (r *RiskSink) Observe(e engine.MarketEvent) {
	if obs, ok := r.inner.(observer); ok {
		obs.Observe(e)
	}
}

// EngineSink returns the engine.OrderSink to hand to engine.Run. This is
// the only supported way to run a RiskSink through engine.Run — see
// RiskSink's doc comment — because the returned value's dynamic type
// mirrors exactly the optional capabilities inner itself implements,
// rather than a bare *RiskSink claiming a capability it cannot back.
//
// Concretely: if inner implements fillProvider (produces a fill
// synchronously on Submit, like PaperSink), EngineSink returns a value that
// also implements fillProvider, forwarding LastFill to inner. If inner does
// not — e.g. a live order-routing sink whose fills arrive later, out of
// band, on order-update callbacks rather than synchronously from Submit —
// EngineSink returns r itself, which has Observe but no LastFill method and
// so does not satisfy engine's fillProvider assertion at all.
//
// This matters because the alternative — declaring LastFill() on
// *RiskSink directly, unconditionally — would make every RiskSink satisfy
// fillProvider regardless of what it wraps. engine.Run would then call
// LastFill() on a sink that never set one, read back a zero-value
// FillRecord (price 0, lots 0, fee 0), and insert that as a real fill row
// into the post-trade store: "no fill information yet" silently becoming
// "a confident, wrong answer" — exactly the failure shape TriState's doc
// comment describes this package as designed against, just relocated from
// a market-data gate to a fill-reporting one.
func (r *RiskSink) EngineSink() engine.OrderSink {
	if fp, ok := r.inner.(fillProvider); ok {
		return &fillForwardingSink{RiskSink: r, inner: fp}
	}
	return r
}

// fillForwardingSink is what EngineSink returns when inner implements
// fillProvider. Embedding *RiskSink promotes Submit, Cancel, and Observe
// unchanged; LastFill is the only method this type adds, and it forwards
// straight to inner so engine.Run reads back inner's real fill rather than
// anything RiskSink itself could fabricate.
type fillForwardingSink struct {
	*RiskSink
	inner fillProvider
}

func (f *fillForwardingSink) LastFill() engine.FillRecord {
	return f.inner.LastFill()
}
