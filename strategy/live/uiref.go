package live

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"time"
)

// NewUIRef mints a fresh Carina ui_ref: "A" + epoch milliseconds + 6 hex
// characters, matching Android's generateUiRef
// (docs/reference/03-order-placement.md §0). The result is always well
// inside the 40-char cap docs/reference/04-order-lifecycle.md §5.3 warns
// about (ErrorInvalidParameter fires above it): "A" (1) + millis (13 digits
// until the year 2286) + 6 hex = 20 chars today.
//
// Why the sink mints this itself rather than reusing OrderIntent.OrderID:
// strategies mint human-readable, per-process-monotonic order ids (e.g.
// "rebalance-TINS-1" — strategy/strategies/rebalance/rebalance.go:337) that
// repeat on the next run. Carina's ui_ref de-duplication guard (§0: "de-
// duplicates accidental resubmits") would then reject the next session's
// first order as a duplicate of yesterday's. Sink instead keeps a
// clientOrderID -> ui_ref map (see sink.go): a retry of the same client
// order id reuses its already-minted ui_ref, which is real idempotency —
// the second POST is recognisably "the same action" to Carina — while a
// new run gets a fresh one every time. NewUIRef itself is unconditional;
// it is Sink's job, not this function's, to decide when a fresh one is
// warranted versus reusing an existing mapping.
//
// crypto/rand, not math/rand: a ui_ref only needs to not collide with the
// account's own recent orders, but anything that ends up in a request to a
// brokerage backend gets crypto/rand by default in this repo rather than a
// per-call judgment call on how much unpredictability actually matters.
func NewUIRef() string {
	var b [3]byte
	if _, err := rand.Read(b[:]); err != nil {
		// crypto/rand.Read does not fail in practice on the platforms this
		// runs on (linux/darwin); panicking here would be a worse failure
		// than a slightly weaker fallback for what is, after all, just a
		// de-duplication key. Mixing in the nanosecond clock keeps ui_refs
		// minted back-to-back in the same millisecond distinct even here.
		return fmt.Sprintf("A%d%06x", time.Now().UnixMilli(), time.Now().UnixNano()&0xffffff)
	}
	return fmt.Sprintf("A%d%s", time.Now().UnixMilli(), hex.EncodeToString(b[:]))
}

// DeterministicUIRef derives a ui_ref from clientOrderID and tradingDay
// (a "2006-01-02" Jakarta calendar date) instead of minting a fresh random
// one (plan 18 §5 B4's restart-durability gap: ui_refs are in-memory today,
// so a same-day crash-and-restart resubmits under a brand-new ui_ref and
// Carina cannot tell the retry apart from a genuinely new order).
//
// Sink.Submit calls this — not NewUIRef — the first time it sees a given
// clientOrderID, keyed by the trading day at submission time (see
// Sink.tradingDay). Because the result is a pure function of those two
// inputs, the SAME logical order gets the SAME ui_ref whether the second
// Submit call happens later in the same process or in a fresh process after
// a crash: a resubmission collides with the first attempt at Carina's own
// ui_ref idempotency guard (ErrDuplicateOrder) instead of silently placing
// a second order.
//
// tradingDay is a required, separate input rather than something this
// function infers from clientOrderID, because most strategies' order ids
// are NOT themselves day-scoped (strategies/orb mints "orb-BBCA-1", a
// process-local counter that legitimately repeats on the next trading day).
// Without tradingDay in the hash, two distinct orders placed on different
// days under the same order id text would collide — reintroducing exactly
// the cross-day collision NewUIRef's own time-based randomness was built to
// avoid (see its doc comment). Folding the day into the hash keeps that
// same safety property while making same-day retries deterministic.
func DeterministicUIRef(clientOrderID, tradingDay string) string {
	h := sha256.Sum256([]byte(clientOrderID + "|" + tradingDay))
	// "D" (deterministic, as opposed to NewUIRef's "A") + 18 hex chars = 19
	// total, comfortably under the 40-char cap.
	return "D" + hex.EncodeToString(h[:9])
}
