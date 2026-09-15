package risk

import "errors"

// Sentinel errors returned by RiskSink.Submit. Each is meant to be tested
// with errors.Is; the wrapped message carries the specific reason.
var (
	// ErrKillSwitchTripped is returned once the kill switch has tripped, by
	// either path (in-process or the external file). It is permanent for
	// the life of the process: there is no untrip. Plan 18 §5 requires "no
	// auto-recovery" — a human restarts the process deliberately rather
	// than the bot silently resuming.
	ErrKillSwitchTripped = errors.New("risk: kill switch is tripped")

	// ErrInvalidIntent is returned for a locally-decidable structural
	// defect: non-positive lots, non-positive price, an unrecognised side,
	// or a price that fails the banded tick approximation (isOnApproxTickIDR).
	ErrInvalidIntent = errors.New("risk: invalid order intent")

	// ErrMarketUnknown is returned when the injected MarketGate reports
	// anything other than Allow on every field, or errors outright. Deny by
	// default: an unconfirmed market state is not a pass.
	ErrMarketUnknown = errors.New("risk: market state unknown, unfavorable, or unconfirmed")

	// ErrCapExceeded is returned by any of the four notional/exposure gates:
	// per-order notional, per-day cumulative notional, max gross exposure,
	// max position per name.
	ErrCapExceeded = errors.New("risk: cap exceeded")

	// ErrDailyLossLimitBreached is returned when the injected PnLProvider
	// reports a loss beyond the configured daily loss limit, or when the
	// provider itself errors (deny by default: unconfirmed PnL is treated
	// as a breach for that one order, though — unlike a confirmed breach —
	// it does not trip the kill switch; see RiskSink.checkDailyLoss).
	ErrDailyLossLimitBreached = errors.New("risk: daily loss limit breached")
)
