package risk

import (
	"fmt"

	"strategy/engine"
)

// Option tunes a RiskSink beyond the fraction-of-equity Limits.
type Option func(*RiskSink) error

// WithMaxBoughtNotionalIDR adds an ABSOLUTE cap, in IDR, on the cumulative
// notional of every BUY this sink admits: an order that would push the total
// over maxIDR is refused with ErrCapExceeded before it can reach the inner
// sink. seedIDR is what was already bought today before this sink existed
// (earlier stores of the same day), so a restart cannot hand out a fresh cap.
//
// Limits is fractions-only on purpose (ADR-0017: no absolute IDR figure for a
// wrong source to be plugged into). This option is the unattended runner's
// per-day rupiah ceiling, which is a different thing: a number the operator
// chose, never one fetched from an endpoint, enforced here in the executing
// process rather than only by the scheduler that launched it.
//
// Accounting is deliberately one-directional and conservative:
//   - only buys count; a sell never frees budget;
//   - a buy that never reached the market (inner.Submit errored) is refunded;
//   - a CANCELLED buy stays consumed, because it was live in the book and may
//     have partially filled: a hard cap is not refunded on a guess;
//   - it is not reset by a day rollover (the cap is per run, seeded per day).
func WithMaxBoughtNotionalIDR(maxIDR, seedIDR float64) Option {
	return func(r *RiskSink) error {
		switch {
		case !(maxIDR > 0):
			return fmt.Errorf("buy cap must be positive (got %v IDR)", maxIDR)
		case seedIDR < 0:
			return fmt.Errorf("buy cap seed must not be negative (got %v IDR)", seedIDR)
		}
		r.buyCapIDR = maxIDR
		r.state.boughtNotionalIDR = seedIDR
		return nil
	}
}

// boughtNotional is the part of an admitted intent that counts against the
// buy cap: its full notional for a buy, nothing for a sell.
func boughtNotional(intent engine.OrderIntent, notional float64) float64 {
	if intent.Side == "buy" {
		return notional
	}
	return 0
}

// checkBuyCap refuses a buy that would push cumulative bought notional over
// the cap. Caller must hold r.state.mu. A nil error with no cap configured.
func (r *RiskSink) checkBuyCap(intent engine.OrderIntent, notional float64) error {
	if r.buyCapIDR <= 0 || intent.Side != "buy" {
		return nil
	}
	projected := r.state.boughtNotionalIDR + notional
	if projected > r.buyCapIDR {
		return fmt.Errorf("%w: bought notional %.0f IDR would exceed the buy cap %.0f IDR (already bought %.0f)",
			ErrCapExceeded, projected, r.buyCapIDR, r.state.boughtNotionalIDR)
	}
	return nil
}
