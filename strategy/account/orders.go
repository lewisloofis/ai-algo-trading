package account

import "context"

// Order is one entry from GET /order/v2/list — an open (or recently
// resolved) order. Only the fields Reconcile needs to compute open-sell-
// order reservations are modeled here; the endpoint returns considerably
// more.
type Order struct {
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
// Confirmed live: unlike the portfolio/balance endpoints, this response's
// `data` field is a bare JSON array, not an object; decodeEnvelope handles
// that the same way it handles an object payload — it decodes whatever
// `data` holds into T.
func (c *Client) OpenOrders(ctx context.Context) ([]Order, error) {
	body, err := c.get(ctx, "/order/v2/list", nil)
	if err != nil {
		return nil, err
	}
	return decodeEnvelope[[]Order](body, "/order/v2/list")
}

// ReservedSellShares sums qty.lot_open * LotSize over every ACTIVE sell
// order in orders, grouped by symbol. This is the reservedSell term in
// Settlement's corrected three-way identity
// (docs/reference/05-account-state.md §2.2.1): a resting sell order commits
// shares out of `available` from the moment it's placed, well before it
// fills — confirmed live, a resting GTC sell for an entire position drives
// both Available and Unsettled to 0 while Balance still holds the full
// amount. A symbol with no ACTIVE sell orders is absent from the returned
// map (callers should treat a missing entry as 0, e.g. via plain map
// indexing).
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
