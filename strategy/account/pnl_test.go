package account

import (
	"math"
	"testing"

	"strategy/engine"
)

const floatTolerance = 1e-6

func almostEqual(a, b, tol float64) bool {
	return math.Abs(a-b) <= tol
}

func TestFeeSchedule_RoundTripRate(t *testing.T) {
	got := DefaultFeeSchedule.RoundTripRate()
	want := 0.0015 + 0.0025
	if !almostEqual(got, want, floatTolerance) {
		t.Errorf("RoundTripRate() = %v, want %v", got, want)
	}
}

func testPosition() Position {
	return Position{
		Symbol: "TEST",
		Qty: Settlement{
			Balance: LotShare{Lot: 10, Share: 1000},
		},
		Price: Price{
			Latest:  1100,
			Average: PriceAverage{Price: 990, Fee: 1000},
		},
	}
}

func TestComputePositionPnL_FeeInclusiveCostBasisAndBreakEven(t *testing.T) {
	p := testPosition()
	fees := FeeSchedule{BuyRate: 0.0015, SellRate: 0.0025}

	got := ComputePositionPnL(p, fees)

	if got.Shares != 1000 {
		t.Errorf("Shares = %v, want 1000", got.Shares)
	}
	if got.CostBasisExFeeIDR != 990000 {
		t.Errorf("CostBasisExFeeIDR = %d, want 990000", got.CostBasisExFeeIDR)
	}
	if got.CostBasisInclFeeIDR != 1000000 {
		t.Errorf("CostBasisInclFeeIDR = %d, want 1000000", got.CostBasisInclFeeIDR)
	}
	if got.MarketValueIDR != 1100000 {
		t.Errorf("MarketValueIDR = %d, want 1100000", got.MarketValueIDR)
	}
	if got.UnrealizedPnLIDR != 100000 {
		t.Errorf("UnrealizedPnLIDR = %d, want 100000", got.UnrealizedPnLIDR)
	}
	if !almostEqual(got.UnrealizedPnLBps, 1000, floatTolerance) {
		t.Errorf("UnrealizedPnLBps = %v, want 1000", got.UnrealizedPnLBps)
	}

	// BreakEvenPrice must clear both legs: selling the whole position at
	// BreakEvenPrice, after paying the sell fee, must recover exactly
	// CostBasisInclFeeIDR.
	wantBreakEven := 1000.0 / (1 - fees.SellRate)
	if !almostEqual(got.BreakEvenPrice, wantBreakEven, 1e-9) {
		t.Errorf("BreakEvenPrice = %v, want %v", got.BreakEvenPrice, wantBreakEven)
	}
	proceedsAfterSellFee := got.BreakEvenPrice * got.Shares * (1 - fees.SellRate)
	if !almostEqual(proceedsAfterSellFee, float64(got.CostBasisInclFeeIDR), 1e-6) {
		t.Errorf("selling at BreakEvenPrice nets %v, want exactly CostBasisInclFeeIDR %d", proceedsAfterSellFee, got.CostBasisInclFeeIDR)
	}
}

func TestComputePositionPnL_ZeroCostBasisDoesNotDivideByZero(t *testing.T) {
	p := Position{
		Symbol: "FREE",
		Qty:    Settlement{Balance: LotShare{Share: 100}},
		Price:  Price{Latest: 500, Average: PriceAverage{Price: 0, Fee: 0}},
	}
	got := ComputePositionPnL(p, DefaultFeeSchedule)

	if got.CostBasisInclFeeIDR != 0 {
		t.Fatalf("CostBasisInclFeeIDR = %d, want 0", got.CostBasisInclFeeIDR)
	}
	if got.UnrealizedPnLBps != 0 {
		t.Errorf("UnrealizedPnLBps = %v, want 0 (guarded, not NaN/Inf)", got.UnrealizedPnLBps)
	}
	if math.IsNaN(got.UnrealizedPnLBps) || math.IsInf(got.UnrealizedPnLBps, 0) {
		t.Fatalf("UnrealizedPnLBps = %v, must be finite", got.UnrealizedPnLBps)
	}
}

func TestComputePortfolioPnL_SumsPositions(t *testing.T) {
	positions := []Position{
		testPosition(),
		{
			Symbol: "OTHER",
			Qty:    Settlement{Balance: LotShare{Share: 500}},
			Price:  Price{Latest: 200, Average: PriceAverage{Price: 210, Fee: 211}},
		},
	}
	fees := FeeSchedule{BuyRate: 0.0015, SellRate: 0.0025}

	got := ComputePortfolioPnL(positions, fees)

	if len(got.Positions) != 2 {
		t.Fatalf("Positions = %d entries, want 2", len(got.Positions))
	}

	wantCostInclFee := got.Positions[0].CostBasisInclFeeIDR + got.Positions[1].CostBasisInclFeeIDR
	if got.TotalCostBasisInclFeeIDR != wantCostInclFee {
		t.Errorf("TotalCostBasisInclFeeIDR = %d, want %d", got.TotalCostBasisInclFeeIDR, wantCostInclFee)
	}
	wantUnrealized := got.Positions[0].UnrealizedPnLIDR + got.Positions[1].UnrealizedPnLIDR
	if got.TotalUnrealizedPnLIDR != wantUnrealized {
		t.Errorf("TotalUnrealizedPnLIDR = %d, want %d", got.TotalUnrealizedPnLIDR, wantUnrealized)
	}
	wantBps := float64(wantUnrealized) / float64(wantCostInclFee) * 10000
	if !almostEqual(got.TotalUnrealizedPnLBps, wantBps, floatTolerance) {
		t.Errorf("TotalUnrealizedPnLBps = %v, want %v", got.TotalUnrealizedPnLBps, wantBps)
	}
}

func TestComputeRealizedPnL(t *testing.T) {
	rows := []engine.AttributionRow{
		{Symbol: "AAA", StrategyID: "s1", GrossPnlBps: 100, NetPnlBps: 60},
		{Symbol: "BBB", StrategyID: "s1", GrossPnlBps: 200, NetPnlBps: 160},
	}

	got := ComputeRealizedPnL(rows)

	if got.RoundTrips != 2 {
		t.Errorf("RoundTrips = %d, want 2", got.RoundTrips)
	}
	if !almostEqual(got.MeanGrossPnlBps, 150, floatTolerance) {
		t.Errorf("MeanGrossPnlBps = %v, want 150", got.MeanGrossPnlBps)
	}
	if !almostEqual(got.MeanNetPnlBps, 110, floatTolerance) {
		t.Errorf("MeanNetPnlBps = %v, want 110", got.MeanNetPnlBps)
	}
}

func TestComputeRealizedPnL_Empty(t *testing.T) {
	got := ComputeRealizedPnL(nil)
	if got.RoundTrips != 0 || got.MeanGrossPnlBps != 0 || got.MeanNetPnlBps != 0 {
		t.Errorf("ComputeRealizedPnL(nil) = %+v, want zero value", got)
	}
}
