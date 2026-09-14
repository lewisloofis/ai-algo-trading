package account

import "context"

// Order is one entry from GET /order/v2/list — an open (or recently
// resolved) order. Only the fields Reconcile needs to compute open-sell-
// order reservations are modeled here; the endpoint returns considerably
// more (confirmed live, the full key set also includes action, amount,
// corpaction_info, day_trade, force_sell, group_id, is_sharia,
// leverage_type, market_order_id, message, nego_in_app, order_source,
// platform_order_type, prev_order_id, securities_kind, smart_order, time,
// time_utc — none of it an account identifier; see OrderID's doc comment).
type Order struct {
	OrderID    string     `json:"order_id"`
	Symbol     string     `json:"symbol"`
	Side       string     `json:"side"`        // "SIDE_BUY" | "SIDE_SELL"
	StatusText string     `json:"status_text"` // e.g. "ACTIVE"
	Qty        OrderQty   `json:"qty"`
	GTC        OrderGTC   `json:"gtc"`
	Price      OrderPrice `json:"price"`
}

// OrderQty is Order.qty.
type OrderQty struct {
	LotOpen  float64 `json:"lot_open"`
	LotDone  float64 `json:"lot_done"`
	LotTotal float64 `json:"lot_total"`
}

// OrderGTC is Order.gtc.
type OrderGTC struct {
	IsGTC bool `json:"is_gtc"`
}

// OrderPrice is Order.price.
type OrderPrice struct {
	Order float64 `json:"order"`
}

// activeSellStatus and activeSellSide are the two fields ReservedSellShares
// filters on, named so the filter reads as a sentence rather than two
// unexplained string literals.
const (
	activeSellSide   = "SIDE_SELL"
	activeSellStatus = "ACTIVE"
)

// OpenOrders fetches GET /order/v2/list — the account's open orders.
//
// CAUTION, confirmed live: this endpoint does NOT honour account scoping —
// it returns every order across every sub-account the trading token can
// see, and Order carries no account field at all (see Order's doc comment
// for the full confirmed key set). A resting order in an unrelated
// sub-account (e.g. the core book) is indistinguishable from the caller's
// own by symbol/side/status alone. Do not use ReservedSellShares directly
// on this method's output for anything account-scoped — use
// OwnReservedSellShares with the caller's own order IDs instead.
//
// Unlike the portfolio/balance endpoints, this response's `data` field is a
// bare JSON array, not an object; decodeEnvelope handles that the same way
// it handles an object payload — it decodes whatever `data` holds into T.
func (c *Client) OpenOrders(ctx context.Context) ([]Order, error) {
	body, err := c.get(ctx, "/order/v2/list", nil)
	if err != nil {
		return nil, err
	}
	return decodeEnvelope[[]Order](body, "/order/v2/list")
}

// ReservedSellShares sums qty.lot_open * LotSize over every ACTIVE sell
// order in orders, grouped by symbol, with NO account filtering. This is
// the reservedSell term in Settlement's corrected three-way identity
// (docs/reference/05-account-state.md §2.2.1): a resting sell order commits
// shares out of `available` from the moment it's placed, well before it
// fills — confirmed live, a resting GTC sell for an entire position drives
// both Available and Unsettled to 0 while Balance still holds the full
// amount.
//
// Exposed for completeness/debugging only. Reconcile uses
// OwnReservedSellShares instead: since OpenOrders' rows carry no account
// attribution (see its doc comment), this unfiltered sum would count a
// resting order in an unrelated sub-account as a reservation against
// whichever account happens to hold the same symbol.
//
// A symbol with no ACTIVE sell orders is absent from the returned map
// (callers should treat a missing entry as 0, e.g. via plain map indexing).
func ReservedSellShares(orders []Order) map[string]float64 {
	reserved := make(map[string]float64, len(orders))
	for _, o := range orders {
		if o.Side != activeSellSide || o.StatusText != activeSellStatus {
			continue
		}
		reserved[o.Symbol] += o.Qty.LotOpen * LotSize
	}
	return reserved
}

// OwnReservedSellShares is ReservedSellShares restricted to orders whose
// OrderID appears in ours. ours is normally
// engine.PostTradeStore.OrderIDs(sinkKind) — the store's own record of
// order IDs this bot placed, which is the only reliable account
// discriminator available: /order/v2/list itself cannot provide one (see
// OpenOrders' doc comment). A symbol with a resting sell in an unrelated
// account, and no order of ours for that symbol, correctly reserves 0 —
// confirmed live against an empty sleeve account that shares a symbol with
// a resting sell in the (unrelated) core book.
func OwnReservedSellShares(orders []Order, ours map[string]struct{}) map[string]float64 {
	filtered := make([]Order, 0, len(orders))
	for _, o := range orders {
		if _, ok := ours[o.OrderID]; ok {
			filtered = append(filtered, o)
		}
	}
	return ReservedSellShares(filtered)
}
