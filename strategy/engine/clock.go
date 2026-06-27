package engine

import "time"

// Clock is the source of "now" for a Strategy. Backtests use replayClock, which
// reports the recorded arrival time of the event being processed, so that two
// runs over the same stream observe identical timestamps.
type Clock interface {
	Now() time.Time
}

// replayClock reports the RecvNs of the current event as wall time.
type replayClock struct {
	ns int64
}

func (c replayClock) Now() time.Time { return time.Unix(0, c.ns) }
