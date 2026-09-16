package risk

import (
	"fmt"

	"strategy/engine"
)

// sharesPerLot mirrors strategy/engine/sink.go's own constant: 1 lot = 100
// shares on the IDX (docs/reference/06-pre-trade-rules.md §6).
const sharesPerLot = 100

// notionalIDR is price * lots * shares-per-lot — the notional value of one
// OrderIntent, in IDR.
func notionalIDR(intent engine.OrderIntent) float64 {
	return float64(intent.PriceIDR) * float64(intent.Lots) * sharesPerLot
}

// approxTickLimitationNote is embedded in every tick-rejection message so a
// human reading a reject log sees the caveat, not just a bare "invalid
// tick".
const approxTickLimitationNote = "banded approximation per reference 06 §5 / strategy/broker.go:roundTick, not the full per-instrument ITCH PriceTickSizeTable"

// isOnApproxTickIDR reports whether priceIDR falls on a valid tick under
// the same coarse, price-banded approximation strategy/broker.go's
// roundTick uses (docs/reference/06-pre-trade-rules.md §5). The real rule
// is delivered per-instrument via the ITCH PriceTickSizeTable, which this
// package cannot fetch (it makes no network calls at all — see
// structural_test.go) and does not attempt to replicate. This approximation
// both under- and over-rejects relative to the true per-instrument table;
// it exists only to catch a grossly off-tick price locally (e.g. an odd
// single-rupiah price on a 5,000+ stock) rather than always waiting for
// Carina's own 4xx. See NewRiskSink's doc comment and this file's package
// doc for the "checkable locally vs. needs an endpoint" split plan 18 §5
// asks for.
func isOnApproxTickIDR(priceIDR int64) bool {
	p := priceIDR
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
	return p%tick == 0
}

// checkStructural runs every pre-trade rule this package can decide from the
// OrderIntent alone, with no endpoint and no stored state:
//
//   - Lots must be positive. OrderIntent.Lots is already a lot count (1 lot
//     = 100 shares), not a raw share quantity, so "quantity is a whole
//     number of lots" is guaranteed by the type itself; what remains to
//     check is that it is not zero or negative (reference 06 §6's
//     "quantity_shares % lot_size == 0 and shares > 0", reduced to its
//     Lots-typed equivalent).
//
//   - PriceIDR must be positive.
//
//   - Side must be "buy" or "sell" (engine.OrderIntent's only documented
//     values); anything else is rejected rather than silently forwarded.
//
//   - PriceIDR must fall on a valid tick under isOnApproxTickIDR.
//
//   - BoardType must be engine.BoardRG (reference 06 §8). Two distinct
//     rejections, because they are different mistakes: an UNSET board is a
//     strategy that never said which venue it meant, and a set-but-other
//     board (TN, NG, or anything else) is a strategy asking for a venue
//     this system cannot route to — B4's LiveSink sends RG, as
//     strategy/broker.go's payload already does. Silently defaulting the
//     zero value to RG would route an order the strategy believed was going
//     to the cash board onto the regular book, where the only symptom is a
//     fill the strategy did not expect (or, the other way round, an order
//     that rests forever against a book it will never match in). The check
//     is one comparison; the failure it prevents is invisible from both
//     ends.
func checkStructural(intent engine.OrderIntent) error {
	if intent.Lots <= 0 {
		return fmt.Errorf("%w: lots %d must be positive", ErrInvalidIntent, intent.Lots)
	}
	if intent.PriceIDR <= 0 {
		return fmt.Errorf("%w: price %d IDR must be positive", ErrInvalidIntent, intent.PriceIDR)
	}
	switch intent.Side {
	case "buy", "sell":
	default:
		return fmt.Errorf("%w: side %q must be \"buy\" or \"sell\"", ErrInvalidIntent, intent.Side)
	}
	if !isOnApproxTickIDR(intent.PriceIDR) {
		return fmt.Errorf("%w: price %d IDR is not on a valid tick (%s)", ErrInvalidIntent, intent.PriceIDR, approxTickLimitationNote)
	}
	switch intent.BoardType {
	case engine.BoardRG:
	case "":
		return fmt.Errorf("%w: board type is unset — a strategy must name the venue it means, and the zero value is not %q (reference 06 §8)", ErrInvalidIntent, engine.BoardRG)
	default:
		return fmt.Errorf("%w: board type %q is not routable — this system sends %q only (reference 06 §8)", ErrInvalidIntent, intent.BoardType, engine.BoardRG)
	}
	return nil
}
