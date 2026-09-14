package account

import (
	"testing"

	"strategy/engine"
)

func brokerPosition(symbol string, available, balance, unsettled float64, amountInvested float64) Position {
	return Position{
		Symbol: symbol,
		Qty: Settlement{
			Available: LotShare{Share: available},
			Balance:   LotShare{Share: balance},
			Unsettled: LotShare{Share: unsettled},
		},
		Asset: Asset{AmountInvested: amountInvested},
	}
}

// activeSell builds an ACTIVE SIDE_SELL Order reserving lots*LotSize shares
// of symbol — the shape ReservedSellShares filters for.
func activeSell(symbol string, lots float64) Order {
	return Order{Symbol: symbol, Side: activeSellSide, StatusText: activeSellStatus, Qty: OrderQty{LotOpen: lots}}
}

func TestReconcile_CleanMatch(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1000, 0, 9200000),
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 1000, CostBasisExFeeIDR: 9200000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictMatch {
		t.Fatalf("Verdict = %v, want MATCH; divergences=%+v", report.Verdict, report.Divergences)
	}
	if len(report.Divergences) != 0 {
		t.Errorf("Divergences = %+v, want none", report.Divergences)
	}
	if report.ExitCode() != 0 {
		t.Errorf("ExitCode() = %d, want 0", report.ExitCode())
	}
}

func TestReconcile_HeldAtBrokerOnly(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1000, 0, 9200000),
	}}
	var derived []engine.DerivedPosition // store has nothing

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %d, want 1", len(report.Divergences))
	}
	dv := report.Divergences[0]
	if dv.Kind != DivergenceHeldAtBrokerOnly {
		t.Errorf("Kind = %v, want DivergenceHeldAtBrokerOnly", dv.Kind)
	}
	if dv.Severity != SeverityCritical {
		t.Errorf("Severity = %v, want SeverityCritical", dv.Severity)
	}
	if report.ExitCode() != 1 {
		t.Errorf("ExitCode() = %d, want 1", report.ExitCode())
	}
}

func TestReconcile_HeldInStoreOnly(t *testing.T) {
	broker := PortfolioList{} // broker reports nothing
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 1000, CostBasisExFeeIDR: 9200000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 || report.Divergences[0].Kind != DivergenceHeldInStoreOnly {
		t.Fatalf("Divergences = %+v, want one DivergenceHeldInStoreOnly", report.Divergences)
	}
}

func TestReconcile_QuantityMismatch_GenuineBreak(t *testing.T) {
	// Broker shows 1000 shares fully settled (no unsettled leg, no resting
	// order); store thinks we hold 1500. Nothing explains the 500-share
	// gap, so this must be a genuine mismatch.
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1000, 0, 9200000),
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 1500, CostBasisExFeeIDR: 13800000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %+v, want 1", report.Divergences)
	}
	dv := report.Divergences[0]
	if dv.Kind != DivergenceQuantityMismatch {
		t.Errorf("Kind = %v, want DivergenceQuantityMismatch", dv.Kind)
	}
	if dv.Severity != SeverityCritical {
		t.Errorf("Severity = %v, want SeverityCritical", dv.Severity)
	}
}

// TestReconcile_QuantityShortfall guards against the sign-blind bug caught
// in the first review round: a store total that falls well short of the
// broker's balance must read as a genuine break, not get explained away.
func TestReconcile_QuantityShortfall(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 100, 150, 50, 900000), // available=100 unsettled=50 balance=150, internally consistent
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 50, CostBasisExFeeIDR: 450000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %+v, want 1", report.Divergences)
	}
	dv := report.Divergences[0]
	if dv.Kind != DivergenceQuantityMismatch {
		t.Fatalf("Kind = %v, want DivergenceQuantityMismatch — a shortfall must never read as benign", dv.Kind)
	}
	if dv.Severity != SeverityCritical {
		t.Errorf("Severity = %v, want SeverityCritical", dv.Severity)
	}
	if report.ExitCode() != 1 {
		t.Errorf("ExitCode() = %d, want 1", report.ExitCode())
	}
}

// TestReconcile_UnsettledLagProducesNoDivergenceOnceComparedAgainstBalance
// is the second-round correction to the first round's "unsettled_only"
// finding. Comparing against `available` (round 1) made a store that
// matches `balance` look like it needed a special-cased benign kind. Now
// that the comparison basis is `balance` itself, a store that agrees with
// the true holding produces no delta at all — there is nothing to classify.
func TestReconcile_UnsettledLagProducesNoDivergenceOnceComparedAgainstBalance(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 100, 150, 50, 900000), // same broker position as the shortfall test above
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 150, CostBasisExFeeIDR: 900000}, // store matches balance
	}

	report := Reconcile("live", broker, nil, derived)

	if len(report.Divergences) != 0 {
		t.Fatalf("Divergences = %+v, want none — DivergenceUnsettledOnly is retired, this case is just a clean match now", report.Divergences)
	}
	if report.Verdict != VerdictMatch {
		t.Errorf("Verdict = %v, want MATCH", report.Verdict)
	}
	if report.ExitCode() != 0 {
		t.Errorf("ExitCode() = %d, want 0", report.ExitCode())
	}
}

// TestReconcile_RestingSellOrderExplainsTheGap is the real-account case that
// falsified round 1's two-term BalanceConsistent: a resting GTC sell for the
// entire position drives Available and Unsettled to 0 while Balance still
// holds everything. Feeding the resting order in through openOrders must
// resolve this cleanly — no DivergenceSettlementInconsistent, no quantity
// mismatch — since it is a completely normal account state. Numbers here
// are synthetic, not the real account's.
func TestReconcile_RestingSellOrderExplainsTheGap(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("ZZZZ", 0, 2500, 0, 1201750), // available=0 unsettled=0 balance=2500
	}}
	openOrders := []Order{
		activeSell("ZZZZ", 25), // 25 lots = 2500 shares reserved
	}
	derived := []engine.DerivedPosition{
		{Symbol: "ZZZZ", Shares: 2500, CostBasisExFeeIDR: 1201750},
	}

	report := Reconcile("live", broker, openOrders, derived)

	if len(report.Divergences) != 0 {
		t.Fatalf("Divergences = %+v, want none — a resting sell for the whole position is a normal account state", report.Divergences)
	}
	if report.Verdict != VerdictMatch {
		t.Errorf("Verdict = %v, want MATCH", report.Verdict)
	}
}

// TestReconcile_WithoutOpenOrdersARestingSellReadsAsInconsistent documents
// the deliberate conservative failure mode: if the caller doesn't supply
// open-orders data, a resting sell (reservedSell defaults to 0) is
// indistinguishable from a genuine three-way break, and it is reported as
// DivergenceSettlementInconsistent rather than silently assumed benign.
func TestReconcile_WithoutOpenOrdersARestingSellReadsAsInconsistent(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("ZZZZ", 0, 2500, 0, 1201750),
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "ZZZZ", Shares: 2500, CostBasisExFeeIDR: 1201750},
	}

	report := Reconcile("live", broker, nil, derived) // no openOrders

	if len(report.Divergences) != 1 || report.Divergences[0].Kind != DivergenceSettlementInconsistent {
		t.Fatalf("Divergences = %+v, want one DivergenceSettlementInconsistent", report.Divergences)
	}
	if report.Verdict != VerdictDivergent {
		t.Errorf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
}

// TestReconcile_ImpossibleQuantitiesIsCaughtWithoutOrderData covers the
// cheap check: available alone exceeding balance is impossible under any
// reservedSell value, so it must be flagged even with no open-orders data.
func TestReconcile_ImpossibleQuantitiesIsCaughtWithoutOrderData(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 3000, 2500, 0, 900000), // available > balance
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 2500, CostBasisExFeeIDR: 900000},
	}

	report := Reconcile("live", broker, nil, derived)

	if len(report.Divergences) != 1 || report.Divergences[0].Kind != DivergenceSettlementInconsistent {
		t.Fatalf("Divergences = %+v, want one DivergenceSettlementInconsistent", report.Divergences)
	}
}

// TestReconcile_InconsistentBrokerLegsIsItsOwnCriticalFinding covers the
// case where the broker's own three settlement legs genuinely don't add up
// even accounting for a resting order: neither leg can be trusted as a
// comparison basis, so this must not be silently classified against
// whichever number happens to be closest to the store's total.
func TestReconcile_InconsistentBrokerLegsIsItsOwnCriticalFinding(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 100, 200, 50, 900000), // 100+50+0=150 != balance=200
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 150, CostBasisExFeeIDR: 900000}, // would look like a match against 150 if not caught first
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %+v, want 1", report.Divergences)
	}
	dv := report.Divergences[0]
	if dv.Kind != DivergenceSettlementInconsistent {
		t.Fatalf("Kind = %v, want DivergenceSettlementInconsistent", dv.Kind)
	}
	if dv.Severity != SeverityCritical {
		t.Errorf("Severity = %v, want SeverityCritical", dv.Severity)
	}
}

// TestReconcile_ExcessWithNoExplanationIsAGenuineMismatch covers a store
// total that exceeds broker balance with nothing (no unsettled leg, no
// resting order) to explain it.
func TestReconcile_ExcessWithNoExplanationIsAGenuineMismatch(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 100, 100, 0, 900000), // fully settled, nothing resting
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 150, CostBasisExFeeIDR: 1350000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %+v, want 1", report.Divergences)
	}
	if dv := report.Divergences[0]; dv.Kind != DivergenceQuantityMismatch || dv.Severity != SeverityCritical {
		t.Errorf("Divergence = %+v, want DivergenceQuantityMismatch/Critical", dv)
	}
}

// TestReconcile_QuantityMismatchNotesWhenDeltaMatchesAKnownLeg covers the
// diagnostic hint: a genuine mismatch whose size happens to equal the
// unsettled leg is still Critical (balance already nets that leg out, so a
// real match would not produce this delta), but the detail string should
// flag the coincidence since it's a useful lead for whoever investigates.
func TestReconcile_QuantityMismatchNotesWhenDeltaMatchesAKnownLeg(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1500, 500, 900000), // consistent: 1000+500+0=1500
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 1000, CostBasisExFeeIDR: 900000}, // store missed the unsettled leg entirely
	}

	report := Reconcile("live", broker, nil, derived)

	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %+v, want 1", report.Divergences)
	}
	dv := report.Divergences[0]
	if dv.Kind != DivergenceQuantityMismatch {
		t.Fatalf("Kind = %v, want DivergenceQuantityMismatch — matching a known leg's size doesn't make it benign", dv.Kind)
	}
	if dv.Severity != SeverityCritical {
		t.Errorf("Severity = %v, want SeverityCritical", dv.Severity)
	}
	if report.Verdict != VerdictDivergent {
		t.Errorf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if dv.Detail == "" {
		t.Fatal("Detail is empty")
	}
}

func TestReconcile_CostBasisMismatch(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1000, 0, 9200000),
	}}
	derived := []engine.DerivedPosition{
		// Quantities agree but cost basis is off by more than tolerance
		// (broker 9,200,000 vs store 9,800,000 — 6.5%, well past the 0.5%/
		// 1000 IDR tolerance).
		{Symbol: "BBCA", Shares: 1000, CostBasisExFeeIDR: 9800000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictDivergent {
		t.Fatalf("Verdict = %v, want DIVERGENT", report.Verdict)
	}
	if len(report.Divergences) != 1 {
		t.Fatalf("Divergences = %+v, want 1", report.Divergences)
	}
	dv := report.Divergences[0]
	if dv.Kind != DivergenceCostBasisMismatch {
		t.Errorf("Kind = %v, want DivergenceCostBasisMismatch", dv.Kind)
	}
	if dv.Severity != SeverityWarning {
		t.Errorf("Severity = %v, want SeverityWarning", dv.Severity)
	}
	if dv.DeltaCostBasisIDR != 600000 {
		t.Errorf("DeltaCostBasisIDR = %d, want 600000", dv.DeltaCostBasisIDR)
	}
}

func TestReconcile_CostBasisWithinToleranceIsNotFlagged(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1000, 0, 9200000),
	}}
	derived := []engine.DerivedPosition{
		// 0.2% off — within the 0.5% relative tolerance.
		{Symbol: "BBCA", Shares: 1000, CostBasisExFeeIDR: 9218000},
	}

	report := Reconcile("live", broker, nil, derived)

	if report.Verdict != VerdictMatch {
		t.Fatalf("Verdict = %v, want MATCH; divergences=%+v", report.Verdict, report.Divergences)
	}
}

func TestReconcile_ReportCarriesSinkKindAndSymbolCounts(t *testing.T) {
	broker := PortfolioList{Results: []Position{
		brokerPosition("BBCA", 1000, 1000, 0, 9200000),
		brokerPosition("TLKM", 500, 500, 0, 1500000),
	}}
	derived := []engine.DerivedPosition{
		{Symbol: "BBCA", Shares: 1000, CostBasisExFeeIDR: 9200000},
	}

	report := Reconcile("paper", broker, nil, derived)

	if report.SinkKind != "paper" {
		t.Errorf("SinkKind = %q, want paper", report.SinkKind)
	}
	if report.BrokerSymbols != 2 {
		t.Errorf("BrokerSymbols = %d, want 2", report.BrokerSymbols)
	}
	if report.StoreSymbols != 1 {
		t.Errorf("StoreSymbols = %d, want 1", report.StoreSymbols)
	}
}

func TestDivergenceKind_String(t *testing.T) {
	tests := map[DivergenceKind]string{
		DivergenceHeldAtBrokerOnly:       "held_at_broker_only",
		DivergenceHeldInStoreOnly:        "held_in_store_only",
		DivergenceSettlementInconsistent: "settlement_inconsistent",
		DivergenceQuantityMismatch:       "quantity_mismatch",
		DivergenceCostBasisMismatch:      "cost_basis_mismatch",
	}
	for kind, want := range tests {
		if got := kind.String(); got != want {
			t.Errorf("%v.String() = %q, want %q", kind, got, want)
		}
	}
}
