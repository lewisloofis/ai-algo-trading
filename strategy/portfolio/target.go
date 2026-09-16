// Package portfolio is the Go side of target_portfolio.json (plan 18 §3),
// the single contract joining Track A (alpha research, which produces the
// file) to Track B (trading infrastructure, which consumes it). It owns
// the wire schema, a decode-and-validate loader (this file), and — in
// null.go — the generator for the deliberately zero-alpha calibration
// input Track B develops against before any real strategy is wired up.
//
// Every validation failure Parse returns wraps ErrInvalidPortfolio and
// names the offending field, so a rejected hand-written file tells its
// author exactly what to fix rather than "invalid portfolio". A
// TargetPortfolio value only ever comes from Parse, Load, or
// NullPortfolio — all three run the same validate() — so a value in hand
// is already known-valid and nothing downstream needs to re-check it.
package portfolio

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"strategy/engine"
)

// SchemaVersion is the only schema_version this package accepts. Bumping
// the wire format requires bumping this constant and adding explicit
// migration or rejection logic — see the schema_version check in
// validate() — rather than loosening the check.
const SchemaVersion = 1

// CalibrationStrategyID is the reserved strategy_id for zero-alpha
// execution-cost calibration (ADR-0016 §6). Orders recorded under it are
// accounted separately from strategy performance and never reported as
// PnL. NullPortfolio always stamps it.
const CalibrationStrategyID = "calibration"

// ErrInvalidPortfolio is wrapped by every error Parse, Load, and
// NullPortfolio return for a structurally or semantically invalid
// portfolio. Test with errors.Is; the wrapped message names the specific
// field at fault.
var ErrInvalidPortfolio = errors.New("portfolio: invalid target portfolio")

// weightSumEpsilon absorbs floating-point summation noise in the "weights
// sum to <= 1.0" check, so an equal-weight portfolio cannot fail its own
// schema on representation error alone.
//
// Measured, not assumed (TestProbe_EqualWeightFloatSummation): summing
// 1.0/N N times strictly exceeds 1.0 for N = 9, 11, 18 and 20 in the range
// 2..20. The canonical five-name null portfolio (§3.3) is NOT one of them —
// 0.2 summed five times lands exactly on 1.0 — so anyone checking whether
// this tolerance is still needed by testing N=5 will wrongly conclude it is
// dead code. It is load-bearing for the odd-N cases above, and §3.3 fixes
// the null portfolio at five names only by current convention, not by
// anything that would stop a nine-name one being written tomorrow.
const weightSumEpsilon = 1e-9

// CapitalModel records which capital model (ADR-0015) produced a target
// portfolio's weights. The two are not comparable: a weight computed
// against a leveraged sleeve's larger base implies a different IDR
// notional than the same weight against owned cash, so the file states
// which one applies rather than leaving it to be inferred from
// base_notional_idr's magnitude.
type CapitalModel string

const (
	// CapitalOwnedCash is ADR-0017's model: the sleeve trades its own
	// transferred cash, with no facility behind it. This is what account
	// 008488L currently runs.
	CapitalOwnedCash CapitalModel = "owned_cash"
	// CapitalLeveragedSleeve is ADR-0015's optional leveraged-facility
	// model, superseding ADR-0011's original 300M-facility assumption.
	CapitalLeveragedSleeve CapitalModel = "leveraged_sleeve"
)

// Board is engine's board enum, re-exported rather than redefined so the
// two cannot drift out of sync. strategy/risk already rejects everything
// but engine.BoardRG (reference 06 §8, plan 18 §11); a second,
// independently-typed enum here would let this file's constraint diverge
// from that enforcement without either side noticing.
type Board = engine.BoardType

// BoardRG is the only value Constraints.Board accepts — plan 18 §11: RG is
// the only board this repo routes to.
const BoardRG = engine.BoardRG

// Target is one line of a target portfolio: how much of BaseNotionalIDR to
// hold in Symbol, as a fraction. Weight 0 is valid and deliberate — it is
// the exit leg, "I still track this name and want none of it" (§3.3) —
// and is distinct from the symbol being absent from Targets entirely.
type Target struct {
	Symbol string
	Weight float64
}

// Constraints bounds how a TargetPortfolio may be executed.
type Constraints struct {
	// MaxParticipationRate caps the fraction of a symbol's own volume an
	// execution algo may consume per slice, in (0, 1].
	MaxParticipationRate float64
	// CompleteBy is the deadline for reaching every target weight. Always
	// strictly after the portfolio's AsOf.
	CompleteBy time.Time
	// Board is the venue every resulting order routes to. Must be BoardRG
	// — the zero value ("") is rejected rather than defaulted to it, for
	// the same reason engine.OrderIntent.BoardType's zero value is
	// rejected in strategy/risk: a strategy (here, the file's author) must
	// say which venue it means.
	Board Board
}

// TargetPortfolio is the decoded, validated form of target_portfolio.json
// (plan 18 §3.1).
type TargetPortfolio struct {
	SchemaVersion   int
	AsOf            time.Time
	StrategyID      string
	CapitalModel    CapitalModel
	BaseNotionalIDR int64
	Targets         []Target
	Constraints     Constraints
}

// wireTarget is the on-the-wire form of one targets[] element. Every field
// is a pointer so a field absent from hand-written JSON can be told apart
// from one explicitly set to its zero value — the same discipline
// risk.TriState and engine.OrderIntent.BoardType apply, and for the same
// reason: Weight 0 is a valid, meaningful value (see Target's doc
// comment), so an absent weight must not silently become one.
type wireTarget struct {
	Symbol *string  `json:"symbol"`
	Weight *float64 `json:"weight"`
}

// wireConstraints is the on-the-wire form of "constraints". See
// wireTarget for why every field is a pointer.
type wireConstraints struct {
	MaxParticipationRate *float64 `json:"max_participation_rate"`
	CompleteBy           *string  `json:"complete_by"`
	Board                *string  `json:"board"`
}

// wireTargetPortfolio is the on-the-wire form of the whole document. See
// wireTarget for why every scalar field is a pointer.
type wireTargetPortfolio struct {
	SchemaVersion   *int             `json:"schema_version"`
	AsOf            *string          `json:"as_of"`
	StrategyID      *string          `json:"strategy_id"`
	CapitalModel    *string          `json:"capital_model"`
	BaseNotionalIDR *int64           `json:"base_notional_idr"`
	Targets         []wireTarget     `json:"targets"`
	Constraints     *wireConstraints `json:"constraints"`
}

// toDomain converts the decoded wire struct into a TargetPortfolio. Most
// optional pointers resolve to their natural Go zero value when absent —
// not because absence is fine, but because every such zero value is
// *itself* already invalid and validate() rejects it with a message naming
// the field: "" fails a blank or enum check, 0 fails a positivity or range
// check, an empty string fails to parse as RFC3339. That leaves exactly
// two pointers this function must guard explicitly, because their zero
// value would NOT be caught downstream: Constraints itself (nil would
// panic on first field access) and each target's Weight (0.0 is a valid
// weight — see Target's doc comment — so a missing weight cannot be
// allowed to default to it).
func (w wireTargetPortfolio) toDomain() (TargetPortfolio, error) {
	var schemaVersion int
	if w.SchemaVersion != nil {
		schemaVersion = *w.SchemaVersion
	}

	var asOfStr string
	if w.AsOf != nil {
		asOfStr = *w.AsOf
	}
	asOf, err := time.Parse(time.RFC3339, asOfStr)
	if err != nil {
		return TargetPortfolio{}, fmt.Errorf("%w: as_of %q is not a valid RFC3339 timestamp: %s", ErrInvalidPortfolio, asOfStr, err)
	}

	var strategyID string
	if w.StrategyID != nil {
		strategyID = strings.TrimSpace(*w.StrategyID)
	}

	var capitalModel CapitalModel
	if w.CapitalModel != nil {
		capitalModel = CapitalModel(*w.CapitalModel)
	}

	var baseNotional int64
	if w.BaseNotionalIDR != nil {
		baseNotional = *w.BaseNotionalIDR
	}

	targets := make([]Target, len(w.Targets))
	for i, wt := range w.Targets {
		var symbol string
		if wt.Symbol != nil {
			symbol = strings.ToUpper(strings.TrimSpace(*wt.Symbol))
		}
		if wt.Weight == nil {
			return TargetPortfolio{}, fmt.Errorf("%w: targets[%d].weight is required", ErrInvalidPortfolio, i)
		}
		targets[i] = Target{Symbol: symbol, Weight: *wt.Weight}
	}

	if w.Constraints == nil {
		return TargetPortfolio{}, fmt.Errorf("%w: constraints is required", ErrInvalidPortfolio)
	}

	var maxParticipation float64
	if w.Constraints.MaxParticipationRate != nil {
		maxParticipation = *w.Constraints.MaxParticipationRate
	}

	var completeByStr string
	if w.Constraints.CompleteBy != nil {
		completeByStr = *w.Constraints.CompleteBy
	}
	completeBy, err := time.Parse(time.RFC3339, completeByStr)
	if err != nil {
		return TargetPortfolio{}, fmt.Errorf("%w: constraints.complete_by %q is not a valid RFC3339 timestamp: %s", ErrInvalidPortfolio, completeByStr, err)
	}

	var board Board
	if w.Constraints.Board != nil {
		board = Board(*w.Constraints.Board)
	}

	return TargetPortfolio{
		SchemaVersion:   schemaVersion,
		AsOf:            asOf,
		StrategyID:      strategyID,
		CapitalModel:    capitalModel,
		BaseNotionalIDR: baseNotional,
		Targets:         targets,
		Constraints: Constraints{
			MaxParticipationRate: maxParticipation,
			CompleteBy:           completeBy,
			Board:                board,
		},
	}, nil
}

// validate checks every semantic rule a TargetPortfolio must satisfy,
// regardless of how it was built — decoded from JSON by Parse, or
// constructed programmatically by NullPortfolio. Running the identical
// method from both call sites means a bug in NullPortfolio's construction
// is caught here, at the point of generation, rather than only by a test
// that happens to round-trip the output back through Parse: "a generator
// that can emit something its own loader rejects is a latent outage."
func (p TargetPortfolio) validate() error {
	if p.SchemaVersion != SchemaVersion {
		return fmt.Errorf("%w: schema_version %d is not supported (only %d)", ErrInvalidPortfolio, p.SchemaVersion, SchemaVersion)
	}
	if strings.TrimSpace(p.StrategyID) == "" {
		return fmt.Errorf("%w: strategy_id must not be blank", ErrInvalidPortfolio)
	}
	switch p.CapitalModel {
	case CapitalOwnedCash, CapitalLeveragedSleeve:
	default:
		return fmt.Errorf("%w: capital_model %q must be %q or %q", ErrInvalidPortfolio, p.CapitalModel, CapitalOwnedCash, CapitalLeveragedSleeve)
	}
	if p.BaseNotionalIDR <= 0 {
		return fmt.Errorf("%w: base_notional_idr %d must be positive", ErrInvalidPortfolio, p.BaseNotionalIDR)
	}
	if len(p.Targets) == 0 {
		return fmt.Errorf("%w: targets must not be empty", ErrInvalidPortfolio)
	}

	seen := make(map[string]bool, len(p.Targets))
	var weightSum float64
	for i, t := range p.Targets {
		if strings.TrimSpace(t.Symbol) == "" {
			return fmt.Errorf("%w: targets[%d].symbol must not be blank", ErrInvalidPortfolio, i)
		}
		if t.Weight < 0 {
			return fmt.Errorf("%w: targets[%d].weight %v must not be negative (long-only, plan 18 §11)", ErrInvalidPortfolio, i, t.Weight)
		}
		if t.Weight > 1 {
			return fmt.Errorf("%w: targets[%d].weight %v must not exceed 1", ErrInvalidPortfolio, i, t.Weight)
		}
		if seen[t.Symbol] {
			return fmt.Errorf("%w: duplicate symbol %q in targets", ErrInvalidPortfolio, t.Symbol)
		}
		seen[t.Symbol] = true
		weightSum += t.Weight
	}
	if weightSum > 1.0+weightSumEpsilon {
		return fmt.Errorf("%w: sum of weights %v exceeds 1.0", ErrInvalidPortfolio, weightSum)
	}

	if p.Constraints.MaxParticipationRate <= 0 || p.Constraints.MaxParticipationRate > 1 {
		return fmt.Errorf("%w: constraints.max_participation_rate %v must be in (0, 1]", ErrInvalidPortfolio, p.Constraints.MaxParticipationRate)
	}
	if !p.Constraints.CompleteBy.After(p.AsOf) {
		return fmt.Errorf("%w: constraints.complete_by %s must be strictly after as_of %s", ErrInvalidPortfolio, p.Constraints.CompleteBy.Format(time.RFC3339), p.AsOf.Format(time.RFC3339))
	}
	if p.Constraints.Board != BoardRG {
		return fmt.Errorf("%w: constraints.board %q is not routable — this system sends %q only (plan 18 §11)", ErrInvalidPortfolio, p.Constraints.Board, BoardRG)
	}
	return nil
}

// Parse decodes and validates a TargetPortfolio from r. Unknown fields —
// at either the top level or inside a target element — are rejected: the
// file is hand-written and hand-edited, and a typo'd key that decoded
// silently would leave the intended constraint unapplied.
func Parse(r io.Reader) (TargetPortfolio, error) {
	dec := json.NewDecoder(r)
	dec.DisallowUnknownFields()

	var w wireTargetPortfolio
	if err := dec.Decode(&w); err != nil {
		return TargetPortfolio{}, fmt.Errorf("%w: %s", ErrInvalidPortfolio, err)
	}

	p, err := w.toDomain()
	if err != nil {
		return TargetPortfolio{}, err
	}
	if err := p.validate(); err != nil {
		return TargetPortfolio{}, err
	}
	return p, nil
}

// Load reads and validates a TargetPortfolio from the file at path.
func Load(path string) (TargetPortfolio, error) {
	f, err := os.Open(path)
	if err != nil {
		return TargetPortfolio{}, err
	}
	defer f.Close()
	return Parse(f)
}

// TotalWeight returns the sum of every target's weight.
func (p TargetPortfolio) TotalWeight() float64 {
	var sum float64
	for _, t := range p.Targets {
		sum += t.Weight
	}
	return sum
}

// TargetNotionalIDR returns the IDR notional implied by symbol's weight
// against BaseNotionalIDR. ok is false when symbol is not present in
// Targets at all — untargeted is not the same as a zero notional target,
// which is exactly what Weight 0 means (see Target's doc comment), so this
// distinction is load-bearing and reported explicitly rather than folded
// into a zero return value.
func (p TargetPortfolio) TargetNotionalIDR(symbol string) (int64, bool) {
	symbol = strings.ToUpper(strings.TrimSpace(symbol))
	for _, t := range p.Targets {
		if t.Symbol == symbol {
			return int64(t.Weight * float64(p.BaseNotionalIDR)), true
		}
	}
	return 0, false
}

// Symbols returns every target's symbol, in file order, as a copy: the
// caller may freely mutate the returned slice without affecting p (this
// repo prefers immutable data — see rules/common/coding-style.md's
// immutability principle, applied here to a getter rather than a mutator).
func (p TargetPortfolio) Symbols() []string {
	out := make([]string, len(p.Targets))
	for i, t := range p.Targets {
		out[i] = t.Symbol
	}
	return out
}
