package risk

import (
	"context"
	"fmt"
)

// SleeveEquityIDR is the sleeve sub-account's own equity, in IDR, as read
// from the scoped GET /balance/withdrawable endpoint
// (docs/adr/0017-sleeve-is-a-funded-sub-account.md, decision 2). It is a
// distinct type rather than a bare float64 specifically so that
// GET /balance/cash/info's TradeBalance/TradeLimit — which ADR-0017 proved
// answers for the core book regardless of acc_no, roughly 25x the real
// sleeve — cannot be passed to NewRiskSink without an explicit, visible
// conversion. A caller wiring this up must write
// SleeveEquityIDR(withdrawable.Balance.Withdrawable), which is the point
// where a reviewer can ask "is this really the scoped sleeve balance?". A
// bare float64 parameter would accept CashInfo.TradeBalance silently, which
// is exactly the failure ADR-0017 describes.
//
// This value is immutable for the life of a RiskSink: it is measured once
// by the caller before construction and never re-fetched here (this
// package makes no network calls — see structural_test.go). If the sleeve
// is topped up or drawn down, the caller constructs a new RiskSink with the
// new value; nothing in this package refreshes it on its own.
type SleeveEquityIDR float64

// TriState is the answer to a pre-trade question this package cannot always
// confirm for itself: market open, instrument tradable, price within its
// ARA/ARB band, and so on (docs/reference/06-pre-trade-rules.md). It is not
// a bool specifically because a bare bool's zero value (false) would be
// indistinguishable from "confirmed no" — silently turning "haven't checked
// yet" or "the endpoint call failed" into a false that reads as a safe,
// definite answer. Only Allow passes; Unknown (the zero value, so a
// zero-value MarketState rejects everything by construction) and Deny both
// reject.
type TriState int

const (
	Unknown TriState = iota
	Allow
	Deny
)

func (t TriState) String() string {
	switch t {
	case Allow:
		return "allow"
	case Deny:
		return "deny"
	default:
		return "unknown"
	}
}

// MarketState is what a MarketGate reports about one order, on one symbol,
// at one instant. Every field maps to one reference-06 endpoint-backed rule
// this package cannot check locally (constraint: no network calls here —
// see MarketGate's doc comment).
type MarketState struct {
	NotHoliday   TriState // §3: today is not a market holiday
	MarketOpen   TriState // §2: correct session for a regular order
	Tradable     TriState // §7.1: is_tradable == true
	NotSuspended TriState // §7.2: SuspendState != SUSPENDED
	WithinBand   TriState // §4: ARB <= order price <= ARA
}

// allAllow reports whether every field of s is Allow. Anything else — one
// field Unknown, one Deny, or every field Unknown — rejects.
func (s MarketState) allAllow() bool {
	return s.NotHoliday == Allow &&
		s.MarketOpen == Allow &&
		s.Tradable == Allow &&
		s.NotSuspended == Allow &&
		s.WithinBand == Allow
}

// firstReject names the first non-Allow field, in declaration order, for a
// useful rejection message.
func (s MarketState) firstReject() string {
	switch {
	case s.NotHoliday != Allow:
		return fmt.Sprintf("not_holiday=%s", s.NotHoliday)
	case s.MarketOpen != Allow:
		return fmt.Sprintf("market_open=%s", s.MarketOpen)
	case s.Tradable != Allow:
		return fmt.Sprintf("tradable=%s", s.Tradable)
	case s.NotSuspended != Allow:
		return fmt.Sprintf("not_suspended=%s", s.NotSuspended)
	case s.WithinBand != Allow:
		return fmt.Sprintf("within_band=%s", s.WithinBand)
	default:
		return ""
	}
}

// MarketGate answers the pre-trade questions that require a live endpoint:
// holiday calendar, session phase, instrument tradability, suspension
// state, and ARA/ARB price bands (reference 06 §§2-4, 7). This package
// never calls those endpoints itself — B3/B4 supply a real implementation
// (backed by the market-data websocket and/or the REST endpoints reference
// 06 documents); RiskSink only consults the interface and denies on
// anything but Allow on every field, including a returned error.
type MarketGate interface {
	State(ctx context.Context, symbol string, priceIDR int64) (MarketState, error)
}

// DenyAllGate is a MarketGate that reports Deny for every field, on every
// symbol, unconditionally. It is the safe default: an absent or
// not-yet-wired MarketGate must never silently mean "allow every order".
// NewRiskSink uses this when the caller passes a nil MarketGate.
type DenyAllGate struct{}

// State always returns every field Deny and a nil error.
func (DenyAllGate) State(ctx context.Context, symbol string, priceIDR int64) (MarketState, error) {
	return MarketState{
		NotHoliday:   Deny,
		MarketOpen:   Deny,
		Tradable:     Deny,
		NotSuspended: Deny,
		WithinBand:   Deny,
	}, nil
}

// PnLProvider supplies the running realized+unrealized PnL for the current
// trading day, in IDR, so RiskSink can enforce the daily loss limit without
// reaching into the post-trade store or strategy/account itself (keeping
// this package pure and synchronously testable).
//
// "Daily" here is whatever day boundary the provider itself uses — this
// package asks once per Submit for "PnL so far today" and compares the
// answer to the limit; it does not recompute or second-guess the boundary.
// A real implementation should use the repo's holiday-aware IDX trading-day
// boundary (collector/internal/calendar) rather than process-local
// midnight, but wiring that is the caller's job (B4/B6), not this
// package's. Contrast with state.go's tradingDay, which this package DOES
// define itself, for the (unrelated) per-day notional cap's rollover — see
// its doc comment for why that one is a plain calendar-date extraction
// rather than a calendar-aware boundary.
type PnLProvider interface {
	// DailyPnLIDR returns the current day's realized+unrealized PnL, in IDR.
	// A negative value is a loss. An error is treated as a breach for that
	// one order (deny by default: unconfirmed PnL must not read as "no
	// loss so far") but does not trip the kill switch — see
	// RiskSink.checkDailyLoss for why a confirmed breach and an unconfirmed
	// query error are handled differently.
	DailyPnLIDR(ctx context.Context) (float64, error)
}

// Limits is the risk layer's configuration: every cap expressed as a
// fraction of sleeve equity, never as an absolute IDR figure. Constructed
// once by the caller and never mutated — see RiskSink's doc comment for the
// concurrency contract that separates this from the mutable counters in
// state.go and killswitch.go.
//
// Fractions, not absolute IDR, are the point. ADR-0017 requires caps to be
// "denominated in sleeve equity, injected". An absolute-IDR field here
// would invite a caller to fill it from GET /balance/cash/info (which
// reports the core book's ~377M limit regardless of acc_no) instead of the
// scoped withdrawable endpoint. Expressing every cap as a fraction, resolved
// only against the SleeveEquityIDR passed to NewRiskSink, removes that
// failure mode by construction: there is no absolute number here for the
// wrong source to be plugged into.
type Limits struct {
	// PerOrderNotionalFraction caps a single order's notional (price * lots
	// * 100 shares/lot) as a fraction of equity.
	PerOrderNotionalFraction float64
	// PerDayNotionalFraction caps the sum of every admitted order's notional
	// within one trading day (see tradingDay in state.go for the boundary).
	PerDayNotionalFraction float64
	// MaxGrossExposureFraction caps the sum of the tracked notional position
	// across every symbol. Long-only (plan 18 §11), so every symbol's
	// tracked position is >= 0 and this is a plain sum, not a sum of
	// absolute values.
	MaxGrossExposureFraction float64
	// MaxPositionPerNameFraction caps the tracked notional position in any
	// single symbol.
	MaxPositionPerNameFraction float64
	// DailyLossLimitFraction caps the day's realized+unrealized loss, as a
	// positive fraction of equity. Breaching it trips the kill switch
	// in-process — see RiskSink.checkDailyLoss.
	DailyLossLimitFraction float64
}

// resolvedCaps are Limits' fractions turned into absolute IDR figures
// against one SleeveEquityIDR, computed once at construction time (equity
// is immutable for a RiskSink's lifetime — see SleeveEquityIDR's doc
// comment) and never recomputed afterward.
type resolvedCaps struct {
	perOrderNotionalIDR   float64
	perDayNotionalIDR     float64
	maxGrossExposureIDR   float64
	maxPositionPerNameIDR float64
	dailyLossLimitIDR     float64
}

func resolveCaps(equity SleeveEquityIDR, limits Limits) resolvedCaps {
	e := float64(equity)
	return resolvedCaps{
		perOrderNotionalIDR:   e * limits.PerOrderNotionalFraction,
		perDayNotionalIDR:     e * limits.PerDayNotionalFraction,
		maxGrossExposureIDR:   e * limits.MaxGrossExposureFraction,
		maxPositionPerNameIDR: e * limits.MaxPositionPerNameFraction,
		dailyLossLimitIDR:     e * limits.DailyLossLimitFraction,
	}
}
