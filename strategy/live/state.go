package live

import (
	"errors"
	"fmt"
	"time"
)

// State is one stage of the regular-limit-order lifecycle
// (docs/reference/04-order-lifecycle.md §3.3).
type State int

const (
	// StateNew is the order record's state the instant Sink.Submit creates
	// it, before the POST to Carina has even been attempted.
	StateNew State = iota
	// StateSending is set once the POST has been accepted (a broker order
	// id came back) but IDX has not yet acknowledged it — mirrors Carina's
	// own STATUS_SENDING (docs/reference/04-order-lifecycle.md §3.1).
	StateSending
	// StateOpen is IDX-acknowledged and resting in the book, unmatched.
	StateOpen
	// StatePartial has matched some, but not all, of its lots.
	StatePartial
	// StateFilled has matched every lot requested. Terminal.
	StateFilled
	// StateCancelled was withdrawn, by request or at end-of-day. Terminal.
	StateCancelled
	// StateRejected was refused by the broker or IDX before ever resting.
	// Terminal.
	StateRejected
	// StateReplaced was superseded by an amend; the broker order id that
	// carried this state is no longer the order of record. Terminal.
	StateReplaced
)

func (s State) String() string {
	switch s {
	case StateNew:
		return "NEW"
	case StateSending:
		return "SENDING"
	case StateOpen:
		return "OPEN"
	case StatePartial:
		return "PARTIAL"
	case StateFilled:
		return "FILLED"
	case StateCancelled:
		return "CANCELLED"
	case StateRejected:
		return "REJECTED"
	case StateReplaced:
		return "REPLACED"
	default:
		return fmt.Sprintf("State(%d)", int(s))
	}
}

// IsTerminal reports whether s is one this order will never leave.
func IsTerminal(s State) bool {
	switch s {
	case StateFilled, StateCancelled, StateRejected, StateReplaced:
		return true
	default:
		return false
	}
}

// ErrIllegalTransition is returned by Transition when from cannot legally
// move to to. It is an error, not a silent overwrite — see Transition's doc
// comment for why that matters specifically for the IDX
// partial-after-full-match quirk.
var ErrIllegalTransition = errors.New("live: illegal order state transition")

// transitions enumerates, for every non-terminal state, the states it may
// legally move to next. A terminal state has no entry (and therefore no
// legal outbound transition at all except to itself — see Transition).
// StatePartial -> StatePartial is listed explicitly because a partially
// filled order legitimately receives more than one partial-match update.
var transitions = map[State][]State{
	StateNew:     {StateSending},
	StateSending: {StateOpen, StatePartial, StateFilled, StateCancelled, StateRejected, StateReplaced},
	StateOpen:    {StatePartial, StateFilled, StateCancelled, StateReplaced, StateRejected},
	StatePartial: {StatePartial, StateFilled, StateCancelled, StateReplaced},
}

// Transition validates moving from -> to against the transitions table and
// returns the resulting state. from == to is always legal and returns to
// unchanged: it is a re-observation, not a state change (Carina's poll
// responses are not edge-triggered, so seeing the same state twice in a
// row is normal, not a transition at all).
//
// An illegal transition returns from UNCHANGED plus a non-nil error — the
// caller keeps whatever state it already had rather than overwriting it
// with an attempted state that failed validation. This is what encodes the
// IDX quirk from docs/reference/04-order-lifecycle.md §3.3: IDX can emit a
// PARTIAL match event after the FULL match event for the same order. Once
// this package has moved an order to StateFilled (decided by lot_done ==
// lot_total, never by seeing a particular status text — see Sink's poll
// loop), StateFilled has no outbound transition to StatePartial in the
// table above, so that stray PARTIAL is rejected here and logged, and the
// order stays FILLED rather than being silently clobbered back to PARTIAL.
func Transition(from, to State) (State, error) {
	if from == to {
		return to, nil
	}
	for _, allowed := range transitions[from] {
		if allowed == to {
			return to, nil
		}
	}
	return from, fmt.Errorf("%w: %s -> %s", ErrIllegalTransition, from, to)
}

// statusToState maps Carina's status_text vocabulary
// (docs/reference/04-order-lifecycle.md §3.2) to a State. It only covers
// the regular-limit-order labels B4 can see (this package never places a
// smart order); an unrecognised label returns ok == false rather than
// guessing, so the caller can log it and leave the tracked state unchanged
// instead of transitioning on a label it does not understand.
func statusToState(statusText string) (State, bool) {
	switch statusText {
	case "OPEN":
		return StateOpen, true
	case "PARTIAL":
		return StatePartial, true
	case "MATCH":
		return StateFilled, true
	case "WITHDRAWN":
		return StateCancelled, true
	case "REJECTED":
		return StateRejected, true
	case "AMENDED":
		return StateReplaced, true
	default:
		return StateNew, false
	}
}

// Order is this package's record of one order it has placed: the client
// id and ui_ref it minted, the broker id Carina assigned, enough of the
// original intent to reconstruct slippage and re-derive fills, and its
// current position in the state machine above.
type Order struct {
	ClientOrderID    string
	UIRef            string
	BrokerOrderID    string
	Symbol           string
	Side             string // "buy" or "sell", engine's convention — see Snapshot.Side for Carina's own
	DecisionPriceIDR int64
	LotsRequested    int
	// LotsFilled is the cumulative lots matched so far, mirrored from the
	// broker's own lot_done. It is what Sink's poll loop diffs against a
	// fresh Snapshot to decide how many lots (if any) a new AsyncFill
	// should carry.
	LotsFilled       int
	MatchedAmountIDR float64
	State            State
	SubmittedAt      time.Time
	UpdatedAt        time.Time
}
