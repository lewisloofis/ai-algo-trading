package account

import (
	"fmt"
	"math"
)

// LotSize is the number of shares per lot on the IDX. Reused everywhere a
// lot<->share conversion is needed so 100 doesn't get scattered as a magic
// number (docs/reference/07-fees-and-costs.md §7 rounding notes).
const LotSize = 100

// lotShareTolerance absorbs float round-trip noise when comparing a lot
// count against a share count; both arrive as float64 in the API even
// though they are always whole numbers in practice.
const lotShareTolerance = 0.5

// LotShare is a share count expressed as both lots and shares — the {lot,
// share} pair used throughout the Carina portfolio API
// (PortfolioQty.Qty in sb-protos/.../portfolio/entity/v2/portfolio.pb.go:2971).
type LotShare struct {
	Lot   float64 `json:"lot"`
	Share float64 `json:"share"`
}

// Shares returns the share count, preferring the API's own Share field but
// falling back to Lot*LotSize if Share is zero while Lot is not. This is
// defensive against a response that populates only one side of the pair; it
// is not a documented API behavior, just a guard against trusting a single
// field in isolation.
func (q LotShare) Shares() float64 {
	if q.Share != 0 {
		return q.Share
	}
	return q.Lot * LotSize
}

// Settlement is the T+2 settlement breakdown for one holding — PortfolioQty
// in docs/reference/05-account-state.md §2.2.1.
//
// Balance is the true holding, independent of settlement state and of any
// resting order. The corrected three-way identity is:
//
//	Balance == Available + Unsettled + reservedSell
//
// where reservedSell is shares already committed to an open ACTIVE sell
// order for this symbol (see ReservedSellShares in orders.go). Available
// means "settled AND not already committed to a resting sell order" — not
// simply "T+2 settled". An earlier version of this type documented
// Balance == Available + Unsettled (no reservedSell term) and enforced it as
// BalanceConsistent(); that was wrong and has been removed. Confirmed live:
// a resting GTC sell order for an entire position drives both Available and
// Unsettled to 0 while Balance still holds the full amount — the old
// two-term identity would flag every such account as broken.
type Settlement struct {
	StockOnHand      float64  `json:"stock_on_hand"`
	Available        LotShare `json:"available"`
	Balance          LotShare `json:"balance"`
	Unsettled        LotShare `json:"unsettled"`
	UnsettledUntilAt Date     `json:"unsettled_until_at"`
}

// Date is google.type.Date — the {year, month, day} object the live Carina
// API sends for PortfolioQty.unsettled_until_at (proto:
// sb-protos/.../portfolio/entity/v2/portfolio.pb.go:1014, `*date.Date`, not a
// string). Confirmed against a real account: a fully-settled position with
// nothing unsettled returns the literal sentinel {1,1,1}, not an omitted
// field or Go's natural zero value — IsZero treats both as "no date" so a
// caller never has to know which one a given response used.
type Date struct {
	Year  int `json:"year"`
	Month int `json:"month"`
	Day   int `json:"day"`
}

// IsZero reports whether d represents "no date".
func (d Date) IsZero() bool {
	return (d.Year == 0 && d.Month == 0 && d.Day == 0) || (d.Year == 1 && d.Month == 1 && d.Day == 1)
}

// String renders d as YYYY-MM-DD, or "" when IsZero. Callers must use this
// rather than formatting the fields directly — otherwise the sentinel value
// prints as "0001-01-01", which reads as a real (and very wrong) date.
func (d Date) String() string {
	if d.IsZero() {
		return ""
	}
	return fmt.Sprintf("%04d-%02d-%02d", d.Year, d.Month, d.Day)
}

// Sellable is the quantity that can actually be sold right now: the T+2
// settled `available` leg. Always use this to size a sell order, never
// Balance — Balance includes unsettled shares from recent buys, and selling
// those is a server rejection (05-account-state.md §2.2.1, §4: "Never sell
// balance.lot"). There is deliberately no method on Settlement that returns
// Balance under a "what can I sell" name; a caller that wants the display
// total (what most portfolio UIs show) reads .Balance directly and knows it
// is doing so.
func (s Settlement) Sellable() LotShare {
	return s.Available
}

// QuantitiesPossible is a cheap, order-data-free sanity check: since
// Balance == Available + Unsettled + reservedSell and reservedSell can never
// be negative, either leg alone exceeding Balance is impossible under any
// value of reservedSell. It catches a badly malformed response without
// needing an open-orders fetch; it does NOT establish full consistency —
// use SettlementConsistent for that once reservedSell is known.
func (s Settlement) QuantitiesPossible() bool {
	balance := s.Balance.Shares()
	return s.Available.Shares() <= balance+lotShareTolerance &&
		s.Unsettled.Shares() <= balance+lotShareTolerance
}

// SettlementConsistent reports whether the corrected three-way identity
// holds: Available + Unsettled + reservedSell == Balance, within float
// tolerance. reservedSell is the shares already committed to an open ACTIVE
// sell order for this symbol — see ReservedSellShares. A caller that finds
// this false has almost certainly misread the response (or is missing the
// open-orders data reservedSell should have come from) rather than caught
// the broker in a real internal inconsistency, but it is exposed so a
// caller (e.g. the reconciliation report) can flag it rather than silently
// trust one of the disagreeing numbers.
func (s Settlement) SettlementConsistent(reservedSell float64) bool {
	got := s.Available.Shares() + s.Unsettled.Shares() + reservedSell
	want := s.Balance.Shares()
	return math.Abs(got-want) <= lotShareTolerance
}

// PriceAverage is PortfolioPrice.average — the cost side of a position's
// price block.
type PriceAverage struct {
	Price float64 `json:"price"` // volume-weighted cost per share, excluding fees
	Fee   float64 `json:"fee"`   // cost per share, including brokerage fees (use for break-even)
}

// Price is PortfolioPrice — latest market price and average cost
// (docs/reference/05-account-state.md §2.2.2).
type Price struct {
	Latest  float64      `json:"latest"`
	Average PriceAverage `json:"average"`
}

// Unrealised is PortfolioAsset.unrealised — the broker's own unrealized PnL
// claim for this position, carried for cross-checking against
// ComputePositionPnL rather than as this package's source of truth.
type Unrealised struct {
	MarketValue float64 `json:"market_value"`
	ProfitLoss  float64 `json:"profit_loss"`
	Gain        float64 `json:"gain"` // percent
}

// Asset is PortfolioAsset (§2.2.3).
type Asset struct {
	Unrealised     Unrealised `json:"unrealised"`
	AmountInvested float64    `json:"amount_invested"` // total cost basis, ex-fee
}

// Flags is PortfolioFlags — the order-eligibility flags carried on a
// position. Read-only here: this package never acts on can_buy/can_sell,
// only reports them.
type Flags struct {
	AllowOrder       bool `json:"allow_order"`
	HaveSmartOrder   bool `json:"have_smart_order"`
	CanBuy           bool `json:"can_buy"`
	CanSell          bool `json:"can_sell"`
	AllowMarketOrder bool `json:"allow_market_order"`
}

// HaircutRates is Haircut — the trading-limit and margin-collateral haircut
// rates applied to this stock (§2.5).
type HaircutRates struct {
	TradingLimit  float64 `json:"trading_limit"`
	MarginTrading float64 `json:"margin_trading"`
}

// Position is one PortfolioItem — a single symbol's holding, as reported by
// the broker (§2.2).
type Position struct {
	Symbol   string       `json:"symbol"`
	IsSharia bool         `json:"is_sharia"`
	Qty      Settlement   `json:"qty"`
	Price    Price        `json:"price"`
	Asset    Asset        `json:"asset"`
	Flags    Flags        `json:"flags"`
	Haircut  HaircutRates `json:"haircut"`
}

// PortfolioTrading is PortfolioSummary.trading.
type PortfolioTrading struct {
	Balance float64 `json:"balance"`
}

// PortfolioAmount is PortfolioSummary.amount.
type PortfolioAmount struct {
	Invested    float64 `json:"invested"`
	Allocated   float64 `json:"allocated"`
	CreditLimit float64 `json:"credit_limit"`
}

// PortfolioProfitLoss is PortfolioSummary.profit_loss.
type PortfolioProfitLoss struct {
	Net        float64 `json:"net"`
	Unrealised float64 `json:"unrealised"`
	Realised   float64 `json:"realised"`
}

// PortfolioDebtBuffer is PortfolioSummary.debt.buffer.
type PortfolioDebtBuffer struct {
	Value      float64 `json:"value"`
	Percentage float64 `json:"percentage"`
}

// PortfolioDebt is PortfolioSummary.debt.
type PortfolioDebt struct {
	Ratio       float64             `json:"ratio"` // percent, e.g. 93.55
	Total       float64             `json:"total"`
	MarketValue float64             `json:"market_value"`
	Buffer      PortfolioDebtBuffer `json:"buffer"`
}

// PortfolioSummary is PortfolioList.summary and
// PortfolioSummaryData.aggregated_portfolio_summary — the account-wide
// aggregate (§2.4).
type PortfolioSummary struct {
	Trading    PortfolioTrading    `json:"trading"`
	Amount     PortfolioAmount     `json:"amount"`
	ProfitLoss PortfolioProfitLoss `json:"profit_loss"`
	Gain       float64             `json:"gain"`
	Equity     float64             `json:"equity"`
	Debt       PortfolioDebt       `json:"debt"`
}

// PortfolioList is GET /portfolio/v2/list's data payload (§2.1). Results is
// the regular account's positions; DayTrade is the day-trade virtual
// account's, same shape. An empty Results on a well-formed response is a
// legitimately flat account, not an error — see decodeEnvelope.
type PortfolioList struct {
	Summary  PortfolioSummary `json:"summary"`
	Results  []Position       `json:"results"`
	DayTrade []Position       `json:"day_trade"`
}
