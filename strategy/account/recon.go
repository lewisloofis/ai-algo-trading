package account

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"

	"strategy/engine"
)

// DivergenceKind classifies one disagreement between broker-reported and
// derived-from-store holdings. Each kind is its own typed value, not a
// free-text string, so a caller can switch on it.
//
// There is deliberately no "benign settlement lag" kind. An earlier version
// of this package compared the store's total against the broker's
// `available` leg and treated a gap equal to `unsettled` as benign. That
// model was wrong: `available` is net of BOTH the unsettled leg AND shares
// already committed to an open sell order (confirmed live — a resting GTC
// sell for an entire position drives `available` to 0 while `balance` holds
// everything), and comparing against it made a completely normal account
// with a resting sell order look like a break. The comparison basis is now
// `balance` — the true holding, which already nets out both settlement and
// resting orders — so a store that agrees with the broker no longer
// produces a delta to explain away. See position.go's Settlement doc
// comment for the corrected three-way identity.
type DivergenceKind int

const (
	// DivergenceHeldAtBrokerOnly: the broker reports a holding in this
	// symbol that the store has no record of at all.
	DivergenceHeldAtBrokerOnly DivergenceKind = iota
	// DivergenceHeldInStoreOnly: the store's fills imply a holding the
	// broker does not report.
	DivergenceHeldInStoreOnly
	// DivergenceSettlementInconsistent: the broker's own settlement legs
	// don't add up — either a cheap impossibility (Available or Unsettled
	// alone exceeding Balance, which needs no order data to detect) or the
	// full three-way identity Available + Unsettled + reservedSell !=
	// Balance. Neither `available` nor `balance` can be trusted as a
	// comparison basis when this holds, so it is reported directly instead
	// of silently picking one of the disagreeing numbers.
	DivergenceSettlementInconsistent
	// DivergenceQuantityMismatch: both sides report a holding, but the
	// store's total disagrees with the broker's `balance` by more than
	// QuantityToleranceShares. Detail notes when the gap's magnitude
	// happens to match the unsettled or reserved-by-open-sell-order leg —
	// that coincidence is diagnostic (worth checking for a store
	// bookkeeping gap) but does not make the mismatch benign: `balance`
	// already nets both of those out, so a real match would not produce
	// this delta at all.
	DivergenceQuantityMismatch
	// DivergenceCostBasisMismatch: quantities agree (within tolerance) but
	// the cost basis disagrees beyond costBasisTolerance.
	DivergenceCostBasisMismatch
)

// String renders the kind as the lower_snake_case label used in reports.
func (k DivergenceKind) String() string {
	switch k {
	case DivergenceHeldAtBrokerOnly:
		return "held_at_broker_only"
	case DivergenceHeldInStoreOnly:
		return "held_in_store_only"
	case DivergenceSettlementInconsistent:
		return "settlement_inconsistent"
	case DivergenceQuantityMismatch:
		return "quantity_mismatch"
	case DivergenceCostBasisMismatch:
		return "cost_basis_mismatch"
	default:
		return "unknown"
	}
}

// MarshalJSON renders the kind as its String() label.
func (k DivergenceKind) MarshalJSON() ([]byte, error) { return json.Marshal(k.String()) }

// Severity ranks how urgently a divergence needs a human to look at it.
type Severity int

const (
	SeverityInfo Severity = iota
	SeverityWarning
	SeverityCritical
)

func (s Severity) String() string {
	switch s {
	case SeverityInfo:
		return "INFO"
	case SeverityWarning:
		return "WARNING"
	case SeverityCritical:
		return "CRITICAL"
	default:
		return "UNKNOWN"
	}
}

// MarshalJSON renders the severity as its String() label.
func (s Severity) MarshalJSON() ([]byte, error) { return json.Marshal(s.String()) }

// Divergence is one disagreement between the broker and the store for one
// symbol. Neither BrokerShares nor StoreShares is "the" position — the point
// of this type is to state what each side says. BrokerShares is the
// broker's `balance` (the comparison basis, not `available`);
// BrokerAvailableShares/BrokerUnsettledShares/BrokerReservedShares are
// carried alongside it so a reader can see all four numbers without
// re-deriving them.
type Divergence struct {
	Symbol   string         `json:"symbol"`
	Kind     DivergenceKind `json:"kind"`
	Severity Severity       `json:"severity"`

	BrokerShares          float64 `json:"broker_shares"` // broker's balance
	BrokerAvailableShares float64 `json:"broker_available_shares,omitempty"`
	BrokerUnsettledShares float64 `json:"broker_unsettled_shares,omitempty"`
	BrokerReservedShares  float64 `json:"broker_reserved_shares,omitempty"` // committed to an open ACTIVE sell order
	StoreShares           float64 `json:"store_shares"`
	DeltaShares           float64 `json:"delta_shares"` // store - broker balance

	BrokerCostBasisIDR int64 `json:"broker_cost_basis_idr,omitempty"`
	StoreCostBasisIDR  int64 `json:"store_cost_basis_idr,omitempty"`
	DeltaCostBasisIDR  int64 `json:"delta_cost_basis_idr,omitempty"` // store - broker

	Detail string `json:"detail"`
}

// Verdict is the reconciliation report's overall outcome.
type Verdict int

const (
	VerdictMatch Verdict = iota
	VerdictDivergent
)

func (v Verdict) String() string {
	if v == VerdictDivergent {
		return "DIVERGENT"
	}
	return "MATCH"
}

// MarshalJSON renders the verdict as its String() label.
func (v Verdict) MarshalJSON() ([]byte, error) { return json.Marshal(v.String()) }

// Report is the full reconciliation output for one sink_kind. sink_kind is
// carried on the report itself so nobody reads a paper reconciliation as a
// live one.
type Report struct {
	SinkKind      string       `json:"sink_kind"`
	Verdict       Verdict      `json:"verdict"`
	Divergences   []Divergence `json:"divergences"`
	BrokerSymbols int          `json:"broker_symbols"`
	StoreSymbols  int          `json:"store_symbols"`
}

// ExitCode returns a process exit code suitable for use as a gate: 0 on
// MATCH, 1 on DIVERGENT.
func (r Report) ExitCode() int {
	if r.Verdict == VerdictDivergent {
		return 1
	}
	return 0
}

const (
	// QuantityToleranceShares absorbs float round-trip noise in share
	// counts; a genuine mismatch is a whole share or more.
	QuantityToleranceShares = 0.5
	// CostBasisToleranceBps is how far the two sides' cost basis may
	// disagree, relative to the broker's cost basis, before it is flagged.
	CostBasisToleranceBps = 50 // 0.5%
	// CostBasisFloorIDR is the minimum absolute tolerance, so a small
	// position's rounding noise (two independently-computed cost bases will
	// never agree to the rupiah) doesn't produce a false positive.
	CostBasisFloorIDR = 1000
)

// costBasisTolerance returns the larger of the relative and floor
// tolerances for a broker cost basis of brokerCost IDR.
func costBasisTolerance(brokerCost int64) int64 {
	rel := int64(math.Round(math.Abs(float64(brokerCost)) * CostBasisToleranceBps / 10000))
	if rel < CostBasisFloorIDR {
		return CostBasisFloorIDR
	}
	return rel
}

// closeTo reports whether a and b agree within QuantityToleranceShares.
func closeTo(a, b float64) bool {
	return math.Abs(a-b) <= QuantityToleranceShares
}

// Reconcile compares broker-reported holdings (broker.Results) against the
// position derived from our own post-trade store (derived) for sinkKind, and
// returns a typed divergence report. Neither side is treated as the
// authority — the report states what each says and classifies the
// difference (docs/plans/18-alpha-and-infrastructure-split.md §5, B1).
//
// openOrders is the account's open orders (Client.OpenOrders) — needed to
// compute how many of each symbol's shares are already committed to a
// resting sell order, which the broker's `available` figure nets out (see
// position.go's Settlement doc comment). Pass nil if the caller has no
// open-orders data; every symbol's reservedSell is then treated as 0, which
// will read as DivergenceSettlementInconsistent for any symbol that
// actually has a resting sell — that is the correct, conservative failure
// mode (flag it rather than guess) and not a reason to skip fetching orders.
func Reconcile(sinkKind string, broker PortfolioList, openOrders []Order, derived []engine.DerivedPosition) Report {
	reservedBySymbol := ReservedSellShares(openOrders)

	brokerBySymbol := make(map[string]Position, len(broker.Results))
	for _, p := range broker.Results {
		brokerBySymbol[p.Symbol] = p
	}
	storeBySymbol := make(map[string]engine.DerivedPosition, len(derived))
	for _, d := range derived {
		storeBySymbol[d.Symbol] = d
	}

	symbols := make(map[string]struct{}, len(brokerBySymbol)+len(storeBySymbol))
	for s := range brokerBySymbol {
		symbols[s] = struct{}{}
	}
	for s := range storeBySymbol {
		symbols[s] = struct{}{}
	}

	var divergences []Divergence
	for symbol := range symbols {
		b, hasBroker := brokerBySymbol[symbol]
		d, hasStore := storeBySymbol[symbol]

		if dv, ok := onlyOneSideHolds(symbol, b, hasBroker, d, hasStore); ok {
			divergences = append(divergences, dv)
			continue
		}
		divergences = append(divergences, compareHeldByBoth(symbol, b, reservedBySymbol[symbol], d)...)
	}

	sort.Slice(divergences, func(i, j int) bool { return divergences[i].Symbol < divergences[j].Symbol })

	verdict := VerdictMatch
	if len(divergences) > 0 {
		verdict = VerdictDivergent
	}

	return Report{
		SinkKind:      sinkKind,
		Verdict:       verdict,
		Divergences:   divergences,
		BrokerSymbols: len(brokerBySymbol),
		StoreSymbols:  len(storeBySymbol),
	}
}

// onlyOneSideHolds reports the held-at-broker-only / held-in-store-only
// divergence when exactly one side has a record for symbol.
func onlyOneSideHolds(symbol string, b Position, hasBroker bool, d engine.DerivedPosition, hasStore bool) (Divergence, bool) {
	switch {
	case hasBroker && !hasStore:
		shares := b.Qty.Balance.Shares()
		return Divergence{
			Symbol: symbol, Kind: DivergenceHeldAtBrokerOnly, Severity: SeverityCritical,
			BrokerShares: shares,
			Detail:       fmt.Sprintf("%s: held at broker (%.0f shares) but absent from the store", symbol, shares),
		}, true
	case hasStore && !hasBroker:
		return Divergence{
			Symbol: symbol, Kind: DivergenceHeldInStoreOnly, Severity: SeverityCritical,
			StoreShares: d.Shares,
			Detail:      fmt.Sprintf("%s: in store (%.0f shares) but absent at broker", symbol, d.Shares),
		}, true
	default:
		return Divergence{}, false
	}
}

// compareHeldByBoth compares quantity and, if quantity agrees, cost basis
// for a symbol both sides report. It returns zero or one Divergence.
//
// Quantity is compared against the broker's `balance` — the true holding —
// not `available`. Before that comparison, it checks the broker's own
// settlement legs for internal consistency: the cheap, order-data-free
// impossibility check first (Settlement.QuantitiesPossible), then the full
// three-way identity once reservedSell is known
// (Settlement.SettlementConsistent). Either failing is
// DivergenceSettlementInconsistent — neither `available` nor `balance` can
// be trusted as a comparison basis in that case, so quantity/cost-basis
// comparison is skipped rather than computed against a number known to be
// wrong.
func compareHeldByBoth(symbol string, b Position, reservedSell float64, d engine.DerivedPosition) []Divergence {
	available := b.Qty.Available.Shares()
	balance := b.Qty.Balance.Shares()
	unsettled := b.Qty.Unsettled.Shares()

	if !b.Qty.QuantitiesPossible() {
		return []Divergence{{
			Symbol: symbol, Kind: DivergenceSettlementInconsistent, Severity: SeverityCritical,
			BrokerShares: balance, BrokerAvailableShares: available, BrokerUnsettledShares: unsettled, BrokerReservedShares: reservedSell,
			StoreShares: d.Shares,
			Detail:      fmt.Sprintf("%s: impossible settlement legs — available=%.0f or unsettled=%.0f exceeds balance=%.0f on its own", symbol, available, unsettled, balance),
		}}
	}
	if !b.Qty.SettlementConsistent(reservedSell) {
		return []Divergence{{
			Symbol: symbol, Kind: DivergenceSettlementInconsistent, Severity: SeverityCritical,
			BrokerShares: balance, BrokerAvailableShares: available, BrokerUnsettledShares: unsettled, BrokerReservedShares: reservedSell,
			StoreShares: d.Shares,
			Detail: fmt.Sprintf("%s: available=%.0f + unsettled=%.0f + reserved=%.0f = %.0f, want balance=%.0f",
				symbol, available, unsettled, reservedSell, available+unsettled+reservedSell, balance),
		}}
	}

	deltaShares := d.Shares - balance
	if math.Abs(deltaShares) > QuantityToleranceShares {
		detail := fmt.Sprintf("%s: quantity mismatch, store=%.0f broker balance=%.0f (delta %.0f; available=%.0f unsettled=%.0f reserved=%.0f)",
			symbol, d.Shares, balance, deltaShares, available, unsettled, reservedSell)
		if closeTo(math.Abs(deltaShares), unsettled) || closeTo(math.Abs(deltaShares), reservedSell) {
			detail += " — delta matches the unsettled or reserved leg; balance already nets both out, so check for a store bookkeeping gap rather than treating this as settlement lag"
		}
		return []Divergence{{
			Symbol: symbol, Kind: DivergenceQuantityMismatch, Severity: SeverityCritical,
			BrokerShares: balance, BrokerAvailableShares: available, BrokerUnsettledShares: unsettled, BrokerReservedShares: reservedSell,
			StoreShares: d.Shares, DeltaShares: deltaShares,
			Detail: detail,
		}}
	}

	brokerCost := roundIDR(b.Asset.AmountInvested)
	deltaCost := d.CostBasisExFeeIDR - brokerCost
	if int64(math.Abs(float64(deltaCost))) > costBasisTolerance(brokerCost) {
		return []Divergence{{
			Symbol: symbol, Kind: DivergenceCostBasisMismatch, Severity: SeverityWarning,
			BrokerShares: balance, StoreShares: d.Shares,
			BrokerCostBasisIDR: brokerCost, StoreCostBasisIDR: d.CostBasisExFeeIDR, DeltaCostBasisIDR: deltaCost,
			Detail: fmt.Sprintf("%s: cost basis mismatch, store=%d broker=%d IDR (delta %d)", symbol, d.CostBasisExFeeIDR, brokerCost, deltaCost),
		}}
	}
	return nil
}
