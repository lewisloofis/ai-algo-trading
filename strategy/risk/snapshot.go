package risk

import (
	"fmt"

	"strategy/engine"
)

// Snapshot reports the sink's current risk state for an ops surface, without
// mutating anything.
//
// This is the "future ops surface" mutableState's doc comment anticipates: the
// day's cumulative notional, per-symbol positions and kill-switch state are
// held in memory behind a mutex and are, until something reads them out,
// invisible the moment the process exits. engine.StatusWriter writes the
// result to status.json.
//
// Deliberate properties:
//
//   - The kill switch is read through killSwitch.Check, the same call Submit
//     makes, so a status file cannot report "clear" while an order would be
//     rejected. Check's fail-closed behaviour on an unreadable file applies
//     here too: an unreadable switch reports tripped, because it is.
//   - The day's rollover is NOT applied. Snapshot is an observer; making it
//     roll the counter would let the act of looking at the state change it.
//     The reported TradingDay says which day the figure belongs to, so a
//     reader can see for itself that it is yesterday's.
//   - PositionsIDR is copied, not aliased, so the caller cannot mutate the
//     sink's map and the JSON encoder cannot race the event loop.
func (r *RiskSink) Snapshot() engine.RiskSnapshot {
	tripped, reason := r.killSwitch.Check()

	r.state.mu.Lock()
	defer r.state.mu.Unlock()

	positions := make(map[string]float64, len(r.state.positionsIDR))
	for symbol, notional := range r.state.positionsIDR {
		positions[symbol] = notional
	}

	return engine.RiskSnapshot{
		KillSwitchTripped:   tripped,
		KillSwitchReason:    reason,
		TradingDay:          formatTradingDay(r.state.day),
		DayNotionalIDR:      r.state.dayNotionalIDR,
		DayNotionalCapIDR:   r.caps.perDayNotionalIDR,
		GrossExposureIDR:    r.state.grossExposureIDR(),
		GrossExposureCapIDR: r.caps.maxGrossExposureIDR,
		PositionsIDR:        positions,
		OpenReservations:    len(r.state.reservations),

		BoughtNotionalIDR:    r.state.boughtNotionalIDR,
		BoughtNotionalCapIDR: r.buyCapIDR,
	}
}

// formatTradingDay renders a tradingDay as YYYY-MM-DD, or "" for the zero
// value (no order has been admitted yet, so no day has been established).
func formatTradingDay(d tradingDay) string {
	if d == (tradingDay{}) {
		return ""
	}
	return fmt.Sprintf("%04d-%02d-%02d", d.year, int(d.month), d.day)
}

// RiskSink satisfies engine.RiskReporter.
var _ engine.RiskReporter = (*RiskSink)(nil)
