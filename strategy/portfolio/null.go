package portfolio

import (
	"encoding/json"
	"fmt"
	"io"
	"time"

	"collector/calendar"
)

// DefaultNullNames is the null portfolio's default constituent set
// (plan 18 §3.3): five liquid names, equal-weighted, with zero expected
// alpha by construction. The selection criteria, most load-bearing first:
//
//  1. Tape coverage. All five are in the live capture watchlist
//     (infra/terraform.tfvars), so B6's cross-check of realized-fill
//     coefficients against Track C's tape-derived ones has data to check
//     against — that cross-check only exists for names the collector
//     actually captures.
//  2. Calibration confidence. All five carry "confirmed" confidence in the
//     high_liq bucket of research/strategy_fit/impact_coeffs.json, with a
//     high effective sample size (eff_N: TINS 772, AMMN 742, ENRG 632,
//     PTRO 453, ANTM 362) — an uncalibrated or thinly-sampled name would
//     make the execution measurement noisy rather than a clean read on
//     Track B's own cost.
//
// Honest caveat: all five are miners or energy names, so this set is
// sector-concentrated. That does not bias a zero-alpha portfolio's
// expected return — the alpha is zero by construction regardless of
// sector — but it does raise the variance of the execution measurement,
// since these names' liquidity and spreads move together more than a
// sector-diverse set would.
var DefaultNullNames = []string{"TINS", "AMMN", "ENRG", "PTRO", "ANTM"}

// Defaults NullOptions applies when the corresponding field is left zero.
const (
	defaultMaxParticipationRate = 0.05
	defaultCompleteByHourWIB    = 15 // 15:00 WIB
)

// NullOptions configures NullPortfolio. AsOf and BaseNotionalIDR have no
// safe default and must be supplied by the caller; every other field
// falls back to a documented default when left zero.
type NullOptions struct {
	// AsOf is the portfolio's as-of timestamp. Required — also anchors
	// CompleteBy's default date.
	AsOf time.Time
	// Names defaults to DefaultNullNames when empty.
	Names []string
	// BaseNotionalIDR is required and must be positive. It is
	// caller-supplied rather than defaulted because it must not exceed the
	// sleeve's actual equity: account 008488L (plan 18 §3.4) was funded
	// with 15,000,000 IDR of owned cash (ADR-0017) — not the 50,000,000
	// IDR in plan 18 §3.1's example, and not a leverage facility (the
	// trading limit did not follow the transferred cash; see ADR-0017).
	// Baking either number in here would silently detach the null
	// portfolio from the real, measured sleeve equity the moment that
	// equity changes.
	BaseNotionalIDR int64
	// MaxParticipationRate defaults to 0.05 when zero.
	MaxParticipationRate float64
	// CompleteBy defaults to 15:00 WIB on AsOf's own calendar date (in
	// calendar.Jakarta) when zero.
	CompleteBy time.Time
}

// NullPortfolio builds the plan 18 §3.3 null portfolio: equal weight
// across opts.Names (or DefaultNullNames), strategy_id
// CalibrationStrategyID, and capital_model CapitalOwnedCash — this sleeve
// currently holds no leverage facility (ADR-0017). The result is run
// through the package's own validate() before being returned, so a caller
// never receives a TargetPortfolio this package's own Parse would reject.
func NullPortfolio(opts NullOptions) (TargetPortfolio, error) {
	if opts.AsOf.IsZero() {
		return TargetPortfolio{}, fmt.Errorf("%w: as_of is required", ErrInvalidPortfolio)
	}
	if opts.BaseNotionalIDR <= 0 {
		return TargetPortfolio{}, fmt.Errorf("%w: base_notional_idr %d must be positive", ErrInvalidPortfolio, opts.BaseNotionalIDR)
	}

	names := opts.Names
	if len(names) == 0 {
		names = DefaultNullNames
	}

	maxParticipation := opts.MaxParticipationRate
	if maxParticipation == 0 {
		maxParticipation = defaultMaxParticipationRate
	}

	completeBy := opts.CompleteBy
	if completeBy.IsZero() {
		y, m, d := opts.AsOf.In(calendar.Jakarta).Date()
		completeBy = time.Date(y, m, d, defaultCompleteByHourWIB, 0, 0, 0, calendar.Jakarta)
	}

	weight := 1.0 / float64(len(names))
	targets := make([]Target, len(names))
	for i, name := range names {
		targets[i] = Target{Symbol: name, Weight: weight}
	}

	p := TargetPortfolio{
		SchemaVersion:   SchemaVersion,
		AsOf:            opts.AsOf,
		StrategyID:      CalibrationStrategyID,
		CapitalModel:    CapitalOwnedCash,
		BaseNotionalIDR: opts.BaseNotionalIDR,
		Targets:         targets,
		Constraints: Constraints{
			MaxParticipationRate: maxParticipation,
			CompleteBy:           completeBy,
			Board:                BoardRG,
		},
	}

	if err := p.validate(); err != nil {
		return TargetPortfolio{}, err
	}
	return p, nil
}

// Flattened returns a copy of p with every weight set to 0 — the exit leg
// of a round trip (§3.3): "I still track this name and want none of it".
// It does not mutate the receiver (this repo prefers immutable
// transforms — see rules/common/coding-style.md). asOf and completeBy
// become the flattened copy's own AsOf/Constraints.CompleteBy, since a
// flatten is issued at a new point in time against a new deadline, not the
// original portfolio's.
func (p TargetPortfolio) Flattened(asOf, completeBy time.Time) TargetPortfolio {
	targets := make([]Target, len(p.Targets))
	for i, t := range p.Targets {
		targets[i] = Target{Symbol: t.Symbol, Weight: 0}
	}
	return TargetPortfolio{
		SchemaVersion:   p.SchemaVersion,
		AsOf:            asOf,
		StrategyID:      p.StrategyID,
		CapitalModel:    p.CapitalModel,
		BaseNotionalIDR: p.BaseNotionalIDR,
		Targets:         targets,
		Constraints: Constraints{
			MaxParticipationRate: p.Constraints.MaxParticipationRate,
			CompleteBy:           completeBy,
			Board:                p.Constraints.Board,
		},
	}
}

// wireEncodeTarget, wireEncodeConstraints and wireEncodeTargetPortfolio
// are the write-side mirror of wireTarget/wireConstraints/
// wireTargetPortfolio in target.go. The read side uses pointer fields so
// an absent field is distinguishable from an explicit zero value; the
// write side has no such ambiguity to preserve — a TargetPortfolio in hand
// is already valid, so every field is always present — so plain value
// types keep Encode simple.
type wireEncodeTarget struct {
	Symbol string  `json:"symbol"`
	Weight float64 `json:"weight"`
}

type wireEncodeConstraints struct {
	MaxParticipationRate float64 `json:"max_participation_rate"`
	CompleteBy           string  `json:"complete_by"`
	Board                string  `json:"board"`
}

type wireEncodeTargetPortfolio struct {
	SchemaVersion   int                   `json:"schema_version"`
	AsOf            string                `json:"as_of"`
	StrategyID      string                `json:"strategy_id"`
	CapitalModel    string                `json:"capital_model"`
	BaseNotionalIDR int64                 `json:"base_notional_idr"`
	Targets         []wireEncodeTarget    `json:"targets"`
	Constraints     wireEncodeConstraints `json:"constraints"`
}

// Encode writes p back out in the §3.1 wire format. Timestamps are
// formatted with RFC3339, which preserves whatever offset each time.Time
// carries — see Parse's handling of as_of for why the offset itself is
// load-bearing and must not be silently normalised to UTC.
func (p TargetPortfolio) Encode(w io.Writer) error {
	wire := wireEncodeTargetPortfolio{
		SchemaVersion:   p.SchemaVersion,
		AsOf:            p.AsOf.Format(time.RFC3339),
		StrategyID:      p.StrategyID,
		CapitalModel:    string(p.CapitalModel),
		BaseNotionalIDR: p.BaseNotionalIDR,
		Targets:         make([]wireEncodeTarget, len(p.Targets)),
		Constraints: wireEncodeConstraints{
			MaxParticipationRate: p.Constraints.MaxParticipationRate,
			CompleteBy:           p.Constraints.CompleteBy.Format(time.RFC3339),
			Board:                string(p.Constraints.Board),
		},
	}
	for i, t := range p.Targets {
		wire.Targets[i] = wireEncodeTarget{Symbol: t.Symbol, Weight: t.Weight}
	}
	return json.NewEncoder(w).Encode(wire)
}
