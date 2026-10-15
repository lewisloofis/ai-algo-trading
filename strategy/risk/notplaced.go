package risk

import (
	"errors"
	"fmt"
)

// ErrNotPlaced marks an inner.Submit error as proof that the order never
// reached the market (the request was never sent, or the broker refused it
// with a definite 4xx). Only such an error refunds the order's reservation
// (exposure, day notional, buy-cap consumption). Wrap it, or supply a
// classifier with WithNotPlacedClassifier.
var ErrNotPlaced = errors.New("risk: order was not placed")

// NotPlacedFunc reports whether err proves the order never reached the market.
// It must answer false for anything ambiguous: a timeout, a transport error
// after the request may have been written, a 5xx, an unrecognised error. A
// buy that timed out may have been accepted by the broker; refunding it would
// let later buys exceed the caps (a timed-out-but-accepted buy refunded from a
// buy cap doubles the worst-case spend).
type NotPlacedFunc func(error) bool

func defaultNotPlaced(err error) bool { return errors.Is(err, ErrNotPlaced) }

// WithNotPlacedClassifier replaces the default classifier. The default
// refunds only errors wrapping ErrNotPlaced; a sink with typed definite
// refusals (a broker's 4xx) supplies a function that recognises them.
func WithNotPlacedClassifier(f NotPlacedFunc) Option {
	return func(r *RiskSink) error {
		if f == nil {
			return fmt.Errorf("not-placed classifier must not be nil")
		}
		r.notPlaced = func(err error) bool { return defaultNotPlaced(err) || f(err) }
		return nil
	}
}
