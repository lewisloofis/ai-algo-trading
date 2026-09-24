package live

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strconv"
)

// Endpoint paths (docs/reference/03-order-placement.md §1, §3, §4, §5).
const (
	pathBuy    = "/order/v2/buy"
	pathSell   = "/order/v2/sell"
	pathCancel = "/order/v2/cancel"
	pathAmend  = "/order/v2/amend"
	pathDetail = "/order/v2/detail"
	pathList   = "/order/v2/list"
)

// platformOrderTypeLimitDay is the ONLY platform_order_type this package
// ever sends. Never PLATFORM_ORDER_TYPE_MARKET_FAK: plan 18 B5 forbids
// market-ordering a basket, and B4 has no execution algo of its own to
// decide when crossing the spread is warranted — every order this package
// places is a plain day limit.
const platformOrderTypeLimitDay = "PLATFORM_ORDER_TYPE_LIMIT_DAY"

// PlaceRequest is the input to Client.Place. UIRef is caller-supplied
// (Sink mints it — see uiref.go) rather than generated inside Place, so a
// retry of the same client order id can deliberately reuse the same ui_ref.
type PlaceRequest struct {
	UIRef     string
	Symbol    string
	Side      string // "buy" or "sell" — any other value is an error, never a default
	PriceIDR  int64
	Shares    int64 // already lots*sharesPerLot; Place does not do that conversion
	BoardType string
}

type placeBody struct {
	UIRef             string `json:"ui_ref"`
	Symbol            string `json:"symbol"`
	Price             int64  `json:"price"`
	Shares            string `json:"shares"` // §1: shares is a string on buy/sell, unlike amend's numeric shares
	BoardType         string `json:"board_type"`
	IsGTC             bool   `json:"is_gtc"`
	PlatformOrderType string `json:"platform_order_type"`
}

type placeResponseData struct {
	OrderID string `json:"order_id"`
}

// Place submits a regular day limit order and returns Carina's broker order
// id (a numeric string) on success. Side selects the path — "buy" ->
// /order/v2/buy, "sell" -> /order/v2/sell — and any other value is rejected
// outright rather than defaulted to either one.
//
// Place does not tick-snap, size, or cap req.PriceIDR/req.Shares: those are
// strategy/risk's and the strategy's job (see rebalance.snapTickIDR). An
// off-tick price or an over-cap size reaching here is a bug upstream, and
// this method surfaces Carina's resulting 4xx (ErrInvalidParameter, via
// classifyOrderError) rather than silently rounding it into something that
// would be accepted — the same reasoning strategy/risk/local_checks.go's
// checkStructural already documents for its own reject-don't-round rule.
func (c *Client) Place(ctx context.Context, req PlaceRequest) (string, error) {
	path, err := orderPath(req.Side)
	if err != nil {
		return "", err
	}
	if req.UIRef == "" {
		return "", errors.New("live: Place: UIRef is required")
	}
	if req.Symbol == "" {
		return "", errors.New("live: Place: Symbol is required")
	}
	if req.BoardType == "" {
		return "", errors.New("live: Place: BoardType is required (the zero value is not treated as RG — see engine.BoardType's doc comment)")
	}

	body := placeBody{
		UIRef:             req.UIRef,
		Symbol:            req.Symbol,
		Price:             req.PriceIDR,
		Shares:            strconv.FormatInt(req.Shares, 10),
		BoardType:         req.BoardType,
		IsGTC:             false,
		PlatformOrderType: platformOrderTypeLimitDay,
	}

	respBody, err := c.postJSON(ctx, path, body)
	if err != nil {
		return "", err
	}
	data, err := decodeEnvelope[placeResponseData](respBody, path)
	if err != nil {
		return "", err
	}
	if data.OrderID == "" {
		return "", fmt.Errorf("live: %s: response had no order_id", path)
	}
	return data.OrderID, nil
}

func orderPath(side string) (string, error) {
	switch side {
	case "buy":
		return pathBuy, nil
	case "sell":
		return pathSell, nil
	default:
		return "", fmt.Errorf("live: order side must be \"buy\" or \"sell\", got %q", side)
	}
}

type cancelBody struct {
	OrderID string `json:"order_id"`
	UIRef   string `json:"ui_ref"`
}

// Cancel withdraws brokerOrderID. uiRef should be a fresh id per distinct
// cancel attempt (docs/reference/03-order-placement.md §0: "generate a
// fresh ui_ref per distinct order action"), reused only when retrying that
// exact cancel — Sink mints one per call for this reason.
func (c *Client) Cancel(ctx context.Context, brokerOrderID, uiRef string) error {
	if brokerOrderID == "" {
		return errors.New("live: Cancel: brokerOrderID is required")
	}
	if uiRef == "" {
		return errors.New("live: Cancel: uiRef is required")
	}
	_, err := c.postJSON(ctx, pathCancel, cancelBody{OrderID: brokerOrderID, UIRef: uiRef})
	return err
}

// AmendRequest is the input to Client.Amend (docs/reference/03-order-placement.md
// §3). Nothing in B4 calls this today — Sink has no execution algo (that's
// B5) and never amends a resting order — but the endpoint is part of the
// same order-mutation surface this package owns exclusively, so it is
// modeled and tested here rather than left for whoever builds B5 to
// reverse-engineer again.
type AmendRequest struct {
	BrokerOrderID string
	UIRef         string
	Symbol        string
	PriceIDR      int64
	Shares        int64
	BoardType     string
}

type amendBody struct {
	OrderID   string `json:"order_id"`
	UIRef     string `json:"ui_ref"`
	Symbol    string `json:"symbol"`
	Price     int64  `json:"price"`
	Shares    int64  `json:"shares"` // §3: shares is numeric on amend, unlike buy/sell's string shares
	BoardType string `json:"board_type"`
}

func (c *Client) Amend(ctx context.Context, req AmendRequest) error {
	if req.BrokerOrderID == "" {
		return errors.New("live: Amend: BrokerOrderID is required")
	}
	if req.UIRef == "" {
		return errors.New("live: Amend: UIRef is required")
	}
	body := amendBody{
		OrderID:   req.BrokerOrderID,
		UIRef:     req.UIRef,
		Symbol:    req.Symbol,
		Price:     req.PriceIDR,
		Shares:    req.Shares,
		BoardType: req.BoardType,
	}
	_, err := c.postJSON(ctx, pathAmend, body)
	return err
}

// Snapshot is one order as Carina currently reports it — the payload of
// GET /order/v2/detail, and one entry of GET /order/v2/list's array. It
// models only what the poller needs to drive the state machine and derive
// fills (state.go, sink.go); strategy/account.Order already documents the
// full confirmed key set of this same /order/v2/list endpoint that this
// type leaves out: action, corpaction_info, day_trade, force_sell,
// group_id, gtc, is_sharia, leverage_type, market_order_id, nego_in_app,
// order_source, platform_order_type, prev_order_id, securities_kind,
// smart_order, time, time_utc. None of it is needed to drive a regular day
// limit order's state machine, and ui_ref is notably NOT in that confirmed
// key set either — see Sink.resolveDuplicate's doc comment for the
// consequence.
type Snapshot struct {
	OrderID    string         `json:"order_id"` // Carina's broker order id — see this package's doc comment on why it is never used as engine.OrderID
	Symbol     string         `json:"symbol"`
	Side       string         `json:"side"` // "SIDE_BUY" | "SIDE_SELL" — matches account.Order's confirmed values on this same endpoint
	StatusText string         `json:"status_text"`
	Qty        SnapshotQty    `json:"qty"`
	Price      SnapshotPrice  `json:"price"`
	Amount     SnapshotAmount `json:"amount"`

	// Message carries the human-readable IDX/broker text
	// (docs/reference/04-order-lifecycle.md §7.4), populated on a REJECTED
	// row. It is never a token and is safe to pass to Logf or an error.
	Message string `json:"message"`
}

// SnapshotQty is Snapshot.qty. Fields are float64 to match Carina's wire
// shape (docs/reference/04-order-lifecycle.md §4.1), though lots are always
// whole numbers on the IDX. Invariant while an order is live: LotDone +
// LotOpen == LotTotal.
type SnapshotQty struct {
	LotDone  float64 `json:"lot_done"`
	LotOpen  float64 `json:"lot_open"`
	LotTotal float64 `json:"lot_total"`
}

// SnapshotPrice is Snapshot.price. Order is the originally submitted limit
// price (mirrors account.OrderPrice.Order, confirmed live on this same
// endpoint); Average is the running volume-weighted execution price plus
// its fee component (docs/reference/04-order-lifecycle.md §4).
type SnapshotPrice struct {
	Order   float64              `json:"order"`
	Average SnapshotAveragePrice `json:"average"`
}

type SnapshotAveragePrice struct {
	Price float64 `json:"price"`
	// Fee is documented only as "the fee component of the average"
	// (docs/reference/04-order-lifecycle.md §4) — whether that is
	// cumulative or a per-share average is not confirmed either way, so
	// this package does not attempt to derive a per-fill fee from it (see
	// Sink's poll loop, which leaves FillRecord.FeeIDR at 0 rather than
	// guess).
	Fee float64 `json:"fee"`
}

// SnapshotAmount is Snapshot.amount. MatchedTotal is the cumulative matched
// value in IDR across every execution so far
// (docs/reference/04-order-lifecycle.md §4) — the poller prefers deriving
// each marginal fill's own price from the change in this value; see
// marginalFillPriceIDR's doc comment for when and why it falls back to
// Price.Average.Price instead.
type SnapshotAmount struct {
	MatchedTotal float64 `json:"matched_total"`
}

// Detail fetches GET /order/v2/detail for one broker order id.
func (c *Client) Detail(ctx context.Context, brokerOrderID string) (Snapshot, error) {
	if brokerOrderID == "" {
		return Snapshot{}, errors.New("live: Detail: brokerOrderID is required")
	}
	q := url.Values{"order_id": {brokerOrderID}}
	body, err := c.get(ctx, pathDetail, q)
	if err != nil {
		return Snapshot{}, err
	}
	return decodeEnvelope[Snapshot](body, pathDetail)
}

// List fetches GET /order/v2/list — every order the trading token can see.
//
// CAUTION, confirmed live (docs/plans/18-alpha-and-infrastructure-split.md
// §3.4, mirroring account.OpenOrders' doc comment for the read-only view of
// this same endpoint): this endpoint does NOT honour account scoping. It
// returns every order across every sub-account the trading token can reach,
// including the core book's, and a Snapshot carries no account field at
// all. Sink.Baseline and Sink.ReconcileTerminal are built around this
// constraint rather than around it being fixable — see their doc comments.
func (c *Client) List(ctx context.Context) ([]Snapshot, error) {
	body, err := c.get(ctx, pathList, nil)
	if err != nil {
		return nil, err
	}
	return decodeEnvelope[[]Snapshot](body, pathList)
}
