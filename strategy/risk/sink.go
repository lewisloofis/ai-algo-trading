package risk

import (
	"context"
	"errors"
	"fmt"
	"time"

	"strategy/engine"
)

// RiskSink decorates an inner engine.OrderSink with every pre-trade gate
// from plan 18 §5 B2. It implements engine.OrderSink itself, but
// engine.Run must be given r.EngineSink(), never a bare *RiskSink — see
// EngineSink's doc comment in forwarding.go for the full reasoning. In
// short: engine.Run reaches past OrderSink with two optional-interface
// type assertions, one for sinks that track book state (observer) and one
// for sinks that fill synchronously (fillProvider). RiskSink answers the
// first unconditionally — see Observe's doc comment — but must answer the
// second only when inner actually fills synchronously; a bare *RiskSink
// would instead make every wrapped sink look like it does, and engine.Run
// would read back a fabricated zero-value fill (price 0, lots 0) as if it
// were real. EngineSink returns a value whose capabilities mirror inner's.
//
// Submit runs, in order: the kill switch (in-process and external file);
// the locally-decidable structural checks (checkStructural); the injected
// MarketGate; the daily loss limit; then the four notional/exposure caps
// (per-order, per-day, per-name, gross). The first failing gate rejects and
// returns without calling inner — see structural_test.go's sibling
// sink_test.go, TestSubmit_RejectedIntentsNeverReachInnerSink.
//
// Concurrency: RiskSink's own fields below (inner, caps, marketGate, pnl,
// clock) are set once by NewRiskSink and never mutated afterward — safe to
// read from any goroutine without a lock. killSwitch and state each guard
// their own mutable data internally; see their doc comments.
type RiskSink struct {
	inner      engine.OrderSink
	caps       resolvedCaps
	marketGate MarketGate
	pnl        PnLProvider
	clock      Clock
	killSwitch *killSwitch
	state      *mutableState
}

// NewRiskSink builds a RiskSink wrapping inner.
//
//   - equity must be sourced by the caller from GET /balance/withdrawable,
//     scoped to the sleeve account (ADR-0017) — never from cash/info. See
//     SleeveEquityIDR's doc comment.
//   - marketGate may be nil, in which case DenyAllGate is used: deny by
//     default until B3/B4 wire a real one.
//   - pnl must not be nil. There is no safe default for "what is today's
//     PnL" — a caller with nothing real yet should pass a PnLProvider that
//     always errors (which still denies, per DailyPnLIDR's doc comment)
//     rather than have this constructor silently skip the daily loss gate.
//   - killSwitchFilePath may be empty, which disables the external kill
//     switch; the in-process trip (on a confirmed daily-loss breach, or via
//     TripKillSwitch) still applies.
//   - clock defaults to time.Now when nil.
func NewRiskSink(inner engine.OrderSink, equity SleeveEquityIDR, limits Limits, marketGate MarketGate, pnl PnLProvider, killSwitchFilePath string, clock Clock) (*RiskSink, error) {
	if inner == nil {
		return nil, errors.New("risk: NewRiskSink: inner sink is required")
	}
	if pnl == nil {
		return nil, errors.New("risk: NewRiskSink: pnl provider is required (pass one that always errors rather than skipping the daily loss gate)")
	}
	if marketGate == nil {
		marketGate = DenyAllGate{}
	}
	if clock == nil {
		clock = time.Now
	}
	return &RiskSink{
		inner:      inner,
		caps:       resolveCaps(equity, limits),
		marketGate: marketGate,
		pnl:        pnl,
		clock:      clock,
		killSwitch: newKillSwitch(killSwitchFilePath),
		state:      newMutableState(),
	}, nil
}

// Submit runs every pre-trade gate and, only if all pass, forwards intent
// to the inner sink unchanged. See the RiskSink doc comment for gate order.
func (r *RiskSink) Submit(ctx context.Context, intent engine.OrderIntent) (engine.OrderID, error) {
	if tripped, reason := r.killSwitch.Check(); tripped {
		return "", fmt.Errorf("%w: %s", ErrKillSwitchTripped, reason)
	}

	if err := checkStructural(intent); err != nil {
		return "", err
	}

	mkt, err := r.marketGate.State(ctx, intent.Symbol, intent.PriceIDR)
	if err != nil {
		return "", fmt.Errorf("%w: market gate error: %v", ErrMarketUnknown, err)
	}
	if !mkt.allAllow() {
		return "", fmt.Errorf("%w: %s", ErrMarketUnknown, mkt.firstReject())
	}

	if err := r.checkDailyLoss(ctx); err != nil {
		return "", err
	}

	notional := notionalIDR(intent)

	r.state.mu.Lock()
	r.state.rolloverIfNewDay(r.clock())

	if notional > r.caps.perOrderNotionalIDR {
		r.state.mu.Unlock()
		return "", fmt.Errorf("%w: order notional %.0f IDR exceeds per-order cap %.0f IDR", ErrCapExceeded, notional, r.caps.perOrderNotionalIDR)
	}

	projectedDayNotional := r.state.dayNotionalIDR + notional
	if projectedDayNotional > r.caps.perDayNotionalIDR {
		r.state.mu.Unlock()
		return "", fmt.Errorf("%w: day notional %.0f IDR would exceed per-day cap %.0f IDR", ErrCapExceeded, projectedDayNotional, r.caps.perDayNotionalIDR)
	}

	curPos := r.state.positionsIDR[intent.Symbol]
	newPos := applySide(curPos, intent.Side, notional)
	if newPos > r.caps.maxPositionPerNameIDR {
		r.state.mu.Unlock()
		return "", fmt.Errorf("%w: %s position %.0f IDR would exceed per-name cap %.0f IDR", ErrCapExceeded, intent.Symbol, newPos, r.caps.maxPositionPerNameIDR)
	}

	projectedGross := r.state.grossExposureIDR() - curPos + newPos
	if projectedGross > r.caps.maxGrossExposureIDR {
		r.state.mu.Unlock()
		return "", fmt.Errorf("%w: gross exposure %.0f IDR would exceed cap %.0f IDR", ErrCapExceeded, projectedGross, r.caps.maxGrossExposureIDR)
	}

	// Every gate passed: reserve the accounting under this order's id, then
	// forward. The reservation is reversed if inner.Submit reports the order
	// never reached the market, and its exposure is reversed on Cancel — see
	// mutableState.reservations for why deriving exposure from intents alone
	// is wrong in BOTH directions.
	r.state.reserve(intent.OrderID, intent.Symbol, newPos-curPos, notional)
	r.state.mu.Unlock()

	id, err := r.inner.Submit(ctx, intent)
	if err != nil {
		// The order never reached the market: it consumed neither exposure
		// nor the day's notional budget.
		r.state.mu.Lock()
		r.state.releaseAll(intent.OrderID)
		r.state.mu.Unlock()
		return "", err
	}

	// Some sinks mint their own id rather than echoing the intent's; Cancel
	// is called with whatever Submit returned, so make the reservation
	// reachable under that id too.
	r.state.mu.Lock()
	r.state.alias(intent.OrderID, string(id))
	r.state.mu.Unlock()

	return id, nil
}

// checkDailyLoss consults the injected PnLProvider and enforces the daily
// loss limit. A confirmed breach (pnl strictly worse than -dailyLossLimitIDR)
// trips the kill switch in-process: this is the one gate whose failure is
// treated as bad enough to end the session, not just reject one order,
// because a realized loss beyond the configured limit is exactly the
// scenario a kill switch exists for.
//
// A PnLProvider error is treated differently: it rejects this one order
// (deny by default — unconfirmed PnL must not read as "no loss so far")
// but does NOT trip the kill switch. A transient query failure against the
// post-trade store should not permanently end the trading session; only a
// confirmed breach should.
func (r *RiskSink) checkDailyLoss(ctx context.Context) error {
	pnl, err := r.pnl.DailyPnLIDR(ctx)
	if err != nil {
		return fmt.Errorf("%w: daily pnl could not be confirmed: %v", ErrDailyLossLimitBreached, err)
	}
	if pnl < -r.caps.dailyLossLimitIDR {
		reason := fmt.Sprintf("daily pnl %.0f IDR breaches loss limit %.0f IDR", pnl, r.caps.dailyLossLimitIDR)
		r.killSwitch.Trip(reason)
		return fmt.Errorf("%w: %s", ErrDailyLossLimitBreached, reason)
	}
	return nil
}

// Cancel forwards to inner and, on success, releases the cancelled order's
// exposure reservation while leaving the day's notional consumed: a
// cancelled order did reach the market, so it spent the day's order-flow
// budget, but it no longer holds exposure. Cancelling reduces risk rather
// than increasing it, so it needs no gate of its own.
//
// Known limitation, and the reason B4/B6 must supply fill-driven positions:
// OrderSink hands this package intents and ids only, never FillRecords (see
// strategy/engine/sink.go), so a cancel that races a partial fill releases
// the whole reservation when only the unfilled remainder should be
// released. The reservation model removes the two unbounded errors — a
// never-placed order holding exposure forever, and a never-placed sell
// freeing exposure the real position still holds — but it cannot be exact
// about partial fills without fill feedback.
func (r *RiskSink) Cancel(ctx context.Context, id engine.OrderID) error {
	if err := r.inner.Cancel(ctx, id); err != nil {
		return err
	}
	r.state.mu.Lock()
	r.state.releasePosition(string(id))
	r.state.mu.Unlock()
	return nil
}

// TripKillSwitch trips the kill switch in-process immediately, for a future
// ops surface (plan 18 §5 B7) or a manual operator action. Safe to call
// from any goroutine. A no-op if already tripped.
func (r *RiskSink) TripKillSwitch(reason string) {
	r.killSwitch.Trip(reason)
}

// KillSwitchTripped reports whether the kill switch is currently tripped,
// and why, without attempting a Submit. Safe to call from any goroutine.
func (r *RiskSink) KillSwitchTripped() (bool, string) {
	return r.killSwitch.Check()
}
