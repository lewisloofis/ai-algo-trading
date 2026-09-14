package account

import (
	"math"

	"strategy/engine"
)

// FeeSchedule holds the all-in commission rates charged per leg
// (docs/reference/07-fees-and-costs.md §1). These rates already include the
// regulatory "Exchange Fee" breakdown the order preview shows — that
// breakdown is not additive on top of the headline rate. Kept as a config
// knob rather than a package constant because the doc explicitly notes
// commission can vary by account tier or promo.
type FeeSchedule struct {
	BuyRate  float64 // all-in, e.g. 0.0015 = 0.15%
	SellRate float64 // all-in, e.g. 0.0025 = 0.25%
}

// DefaultFeeSchedule is the schedule confirmed in 07-fees-and-costs.md §2 for
// this account as of its June 2026 capture: buy 0.15%, sell 0.25%. It is a
// default, not a guarantee — re-confirm against a live order preview before
// trusting it in production, per the doc's own caveat.
var DefaultFeeSchedule = FeeSchedule{BuyRate: 0.0015, SellRate: 0.0025}

// RoundTripRate is the sum of both legs' all-in rates — the minimum
// favourable price move needed to cover fees alone on a round trip.
func (f FeeSchedule) RoundTripRate() float64 {
	return f.BuyRate + f.SellRate
}

// roundIDR is the one rounding boundary in this package: float64 IDR amounts
// are rounded to the nearest whole IDR (round-half-away-from-zero, via
// math.Round) only at the point they become an int64 result. Every
// intermediate computation stays float64.
func roundIDR(x float64) int64 {
	return int64(math.Round(x))
}

// PositionPnL is the fee-inclusive cost basis and unrealized PnL for one
// position, computed from the prices and quantities on the Position it was
// derived from.
type PositionPnL struct {
	Symbol string

	// Shares is Qty.Balance.Shares() — the total held, including unsettled.
	// This is deliberately the same basis the broker uses for
	// AmountInvested and Unrealised.MarketValue, so ComputePositionPnL's
	// output is comparable to the broker's own figures.
	Shares float64

	CostBasisExFeeIDR   int64 // Shares * Price.Average.Price, rounded
	CostBasisInclFeeIDR int64 // Shares * Price.Average.Fee, rounded
	MarketValueIDR      int64 // Shares * Price.Latest, rounded

	UnrealizedPnLIDR int64   // MarketValueIDR - CostBasisInclFeeIDR
	UnrealizedPnLBps float64 // UnrealizedPnLIDR / CostBasisInclFeeIDR * 10000; 0 if cost basis is 0

	// BreakEvenPrice is the price per share at which selling the entire
	// position exactly recovers CostBasisInclFeeIDR after also paying the
	// sell leg's fee: Price.Average.Fee already bakes in the buy leg's fee
	// (05-account-state.md §2.2.2), so solving
	// breakEven*(1-SellRate) == Price.Average.Fee for breakEven covers both
	// legs without needing the original buy fee reconstructed separately.
	BreakEvenPrice float64
}

// ComputePositionPnL computes fee-inclusive cost basis, market value, and
// unrealized PnL for one position under fees.
func ComputePositionPnL(p Position, fees FeeSchedule) PositionPnL {
	shares := p.Qty.Balance.Shares()

	costExFee := shares * p.Price.Average.Price
	costInclFee := shares * p.Price.Average.Fee
	marketValue := shares * p.Price.Latest

	unrealizedIDR := marketValue - costInclFee
	var unrealizedBps float64
	if costInclFee != 0 {
		unrealizedBps = unrealizedIDR / costInclFee * 10000
	}

	var breakEven float64
	if fees.SellRate < 1 {
		breakEven = p.Price.Average.Fee / (1 - fees.SellRate)
	}

	return PositionPnL{
		Symbol:              p.Symbol,
		Shares:              shares,
		CostBasisExFeeIDR:   roundIDR(costExFee),
		CostBasisInclFeeIDR: roundIDR(costInclFee),
		MarketValueIDR:      roundIDR(marketValue),
		UnrealizedPnLIDR:    roundIDR(unrealizedIDR),
		UnrealizedPnLBps:    unrealizedBps,
		BreakEvenPrice:      breakEven,
	}
}

// PortfolioPnL is the aggregate of every position's PositionPnL.
type PortfolioPnL struct {
	Positions []PositionPnL

	TotalCostBasisExFeeIDR   int64
	TotalCostBasisInclFeeIDR int64
	TotalMarketValueIDR      int64
	TotalUnrealizedPnLIDR    int64
	TotalUnrealizedPnLBps    float64 // TotalUnrealizedPnLIDR / TotalCostBasisInclFeeIDR * 10000
}

// ComputePortfolioPnL computes PositionPnL for every position and sums them.
func ComputePortfolioPnL(positions []Position, fees FeeSchedule) PortfolioPnL {
	out := PortfolioPnL{Positions: make([]PositionPnL, 0, len(positions))}
	for _, p := range positions {
		pnl := ComputePositionPnL(p, fees)
		out.Positions = append(out.Positions, pnl)
		out.TotalCostBasisExFeeIDR += pnl.CostBasisExFeeIDR
		out.TotalCostBasisInclFeeIDR += pnl.CostBasisInclFeeIDR
		out.TotalMarketValueIDR += pnl.MarketValueIDR
		out.TotalUnrealizedPnLIDR += pnl.UnrealizedPnLIDR
	}
	if out.TotalCostBasisInclFeeIDR != 0 {
		out.TotalUnrealizedPnLBps = float64(out.TotalUnrealizedPnLIDR) / float64(out.TotalCostBasisInclFeeIDR) * 10000
	}
	return out
}

// RealizedPnL summarizes completed round-trips already recorded in the
// post-trade store's attribution table. The store records realized PnL in
// basis points only — engine.AttributionRow carries no IDR field — so this
// mirrors that rather than inventing an IDR figure the store doesn't have.
// Callers should pre-filter rows to the strategy/run of interest: the
// attribution table carries strategy_id but not sink_kind (see
// engine/store.go's schema and engine.AttributionsByStrategy).
type RealizedPnL struct {
	RoundTrips      int
	MeanGrossPnlBps float64
	MeanNetPnlBps   float64
}

// ComputeRealizedPnL summarizes rows.
func ComputeRealizedPnL(rows []engine.AttributionRow) RealizedPnL {
	if len(rows) == 0 {
		return RealizedPnL{}
	}
	var sumGross, sumNet float64
	for _, r := range rows {
		sumGross += r.GrossPnlBps
		sumNet += r.NetPnlBps
	}
	n := float64(len(rows))
	return RealizedPnL{
		RoundTrips:      len(rows),
		MeanGrossPnlBps: sumGross / n,
		MeanNetPnlBps:   sumNet / n,
	}
}
