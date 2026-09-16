package market

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
)

// ---------------------------------------------------------------------------
// Wire types
//
// Every numeric or enum field below is parsed through a permissive
// UnmarshalJSON rather than a bare Go type, because the encoding is a
// property of the gateway's marshaler configuration rather than of the
// contract: proto3's JSON mapping permits 64-bit integers as STRINGS, and
// enums as either their name or their number.
//
// MEASURED 2026-09-16, live, in SESSION_1 (cmd/market-probe --parsed):
// this gateway emits ARA/ARB as BARE NUMBERS ({"above":7675,"below":5450})
// and state_name as the full enum NAME ("STATE_NAME_SESSION_1"). Those are
// the forms production actually returns today. The permissive parsers stay
// anyway — a marshaler flag is exactly the kind of thing that changes
// without notice on a private API, and the cost of the alternative is not
// an error but a silent permanent deny: a strict uint64 field would fail to
// parse a perfectly good "7675", the band would go Unknown, and every order
// would be rejected for a reason no log line would explain.
// ---------------------------------------------------------------------------

// MarketTimeData is GET /company-price-feed/market-time's data payload
// (reference 06 §2.1).
type MarketTimeData struct {
	Market        StatusItem `json:"market"`
	IEPIEVRegular StatusItem `json:"iepiev_regular"`
	IEPIEVFCA     StatusItem `json:"iepiev_fca"`
}

// StatusItem is one {"status": ...} object.
type StatusItem struct {
	Status Status `json:"status"`
}

// Status is the coarse open/closed enum. Normalised to one of
// StatusOpen/StatusClose/StatusUnspecified; anything unrecognised is kept
// verbatim so a reason string can name what actually arrived.
type Status string

const (
	StatusUnspecified Status = "STATUS_UNSPECIFIED"
	StatusOpen        Status = "STATUS_OPEN"
	StatusClose       Status = "STATUS_CLOSE"
)

// UnmarshalJSON accepts the enum name ("STATUS_OPEN"), the raw lowercase
// form the web client also handles ("open"/"close" —
// sb-web/src/typings/market.ts:72-78 maps both), or the proto enum number.
func (s *Status) UnmarshalJSON(b []byte) error {
	raw, num, isNum, err := scalarToken(b)
	if err != nil {
		return fmt.Errorf("status: %w", err)
	}
	if isNum {
		switch num {
		case 1:
			*s = StatusOpen
		case 2:
			*s = StatusClose
		default:
			*s = StatusUnspecified
		}
		return nil
	}
	switch v := strings.ToUpper(strings.TrimSpace(raw)); v {
	case "OPEN", "STATUS_OPEN":
		*s = StatusOpen
	case "CLOSE", "CLOSED", "STATUS_CLOSE":
		*s = StatusClose
	case "":
		*s = StatusUnspecified
	default:
		*s = Status(v)
	}
	return nil
}

// SessionData is GET /company-price-feed/market-time/session's data payload
// (reference 06 §2.2).
type SessionData struct {
	Datetime string        `json:"datetime"`
	Detail   SessionDetail `json:"detail"`
}

// SessionDetail carries the two parallel session tracks. Only Regular
// matters here: the regular continuous-auction board is the only one this
// repo trades (plan 18 §11 rules out negotiated and FCA).
type SessionDetail struct {
	Regular SessionDetailItem `json:"regular"`
	FCA     SessionDetailItem `json:"fca"`
}

// SessionDetailItem is one board's current phase.
type SessionDetailItem struct {
	Session       int       `json:"session"`
	StateName     StateName `json:"state_name"`
	IsLastSession bool      `json:"is_last_session"`
	IsEndOfDay    bool      `json:"is_end_of_day"`
	StateStart    string    `json:"state_start_time"`
	StateEnd      string    `json:"state_end_time"`
	SuspendInfo   string    `json:"suspend_info"`
}

// StateName is the intraday phase enum (reference 06 §2.2). Values are
// normalised to the STATE_NAME_* spelling.
type StateName string

const (
	StateUnspecified  StateName = "STATE_NAME_UNSPECIFIED"
	StatePreOpening   StateName = "STATE_NAME_PRE_OPENING"
	StateSession1     StateName = "STATE_NAME_SESSION_1"
	StateSession2     StateName = "STATE_NAME_SESSION_2"
	StateSession3     StateName = "STATE_NAME_SESSION_3"
	StateSession4     StateName = "STATE_NAME_SESSION_4"
	StateSession5     StateName = "STATE_NAME_SESSION_5"
	StateBreak        StateName = "STATE_NAME_BREAK"
	StatePreClosing   StateName = "STATE_NAME_PRE_CLOSING"
	StatePostClosing  StateName = "STATE_NAME_POST_CLOSING"
	StateNegotiation  StateName = "STATE_NAME_NEGOTIATION"
	StateMarketClosed StateName = "STATE_NAME_MARKET_CLOSED"
	StateBeforeMarket StateName = "STATE_NAME_BEFORE_MARKET"
)

// stateNameByNumber is the proto enum's own numbering
// (securities/transactional/.../market_time.proto:9-23), used when the
// gateway emits numeric enums.
var stateNameByNumber = map[int64]StateName{
	0:  StateUnspecified,
	1:  StatePreOpening,
	2:  StateSession1,
	3:  StateSession2,
	4:  StateSession3,
	5:  StateSession4,
	6:  StateSession5,
	7:  StateBreak,
	8:  StatePreClosing,
	9:  StatePostClosing,
	10: StateNegotiation,
	11: StateMarketClosed,
	12: StateBeforeMarket,
}

// acceptsRegularOrders reports whether a new regular-board limit order may
// be submitted in this phase. Only SESSION_1..SESSION_5 qualify (reference
// 06 §2.2). NEGOTIATION is excluded on purpose: it accepts negotiated
// orders, which this repo does not send, so treating it as "open" would
// wave through an order destined for a book we never trade.
//
// An unrecognised phase is NOT handled here — it is neither accepted nor
// refused, because the caller must be able to tell "the market is shut"
// (Deny) from "I do not know what phase this is" (Unknown). See
// classifyPhase.
func (s StateName) acceptsRegularOrders() bool {
	switch s {
	case StateSession1, StateSession2, StateSession3, StateSession4, StateSession5:
		return true
	default:
		return false
	}
}

// isKnownClosedPhase reports whether s is a phase the exchange defines and
// in which regular orders are not accepted. Kept as an explicit allow-list
// of names rather than "not acceptsRegularOrders", so a phase the exchange
// adds later reads as Unknown instead of being silently classified as a
// confident Deny.
func (s StateName) isKnownClosedPhase() bool {
	switch s {
	case StatePreOpening, StateBreak, StatePreClosing, StatePostClosing,
		StateNegotiation, StateMarketClosed, StateBeforeMarket:
		return true
	default:
		return false
	}
}

// UnmarshalJSON accepts the enum name, the bare suffix ("SESSION_1"), or
// the proto enum number.
func (s *StateName) UnmarshalJSON(b []byte) error {
	raw, num, isNum, err := scalarToken(b)
	if err != nil {
		return fmt.Errorf("state_name: %w", err)
	}
	if isNum {
		if name, ok := stateNameByNumber[num]; ok {
			*s = name
		} else {
			*s = StateName(fmt.Sprintf("STATE_NAME_UNMAPPED_%d", num))
		}
		return nil
	}
	v := strings.ToUpper(strings.TrimSpace(raw))
	switch {
	case v == "":
		*s = StateUnspecified
	case strings.HasPrefix(v, "STATE_NAME_"):
		*s = StateName(v)
	default:
		*s = StateName("STATE_NAME_" + v)
	}
	return nil
}

// TradableRow is one row of GET /stock/tradable's data array
// (sb-web/src/global/Securities/types/api/stockTradable.ts).
type TradableRow struct {
	StockCode        string `json:"stock_code"`
	IsTradable       bool   `json:"is_tradable"`
	IsShariaTradable bool   `json:"is_sharia_tradable"`
	IsMarginTradable bool   `json:"is_margin_tradable"`
}

// OrderBookData is GET /nego-engine/v1/order-book/{code}'s data payload.
// Only the ARA/ARB band is modelled: the order_book sub-object is the
// negotiation book, which this gate has no use for (live depth comes from
// the market-data websocket).
type OrderBookData struct {
	AutoRejectPrice AutoRejectPrice `json:"auto_reject_price"`
}

// AutoRejectPrice is the day's hard price band (reference 06 §4). Orders
// priced strictly above Above or strictly below Below are auto-rejected by
// the matching engine; the boundaries themselves are valid.
type AutoRejectPrice struct {
	Above               IDR `json:"above"`
	Below               IDR `json:"below"`
	MaxRequestableAbove IDR `json:"max_requestable_above"`
	MaxRequestableBelow IDR `json:"max_requestable_below"`
}

// usable reports whether the band is a band at all. The proto marks both
// bounds required and > 0, so a zero bound means the field was absent from
// the JSON, not that the instrument may trade at any price — and an
// inverted band (below > above) is nonsense that must not be evaluated as
// if it were a real constraint.
func (a AutoRejectPrice) usable() bool {
	return a.Above > 0 && a.Below > 0 && a.Below <= a.Above
}

// contains reports whether priceIDR sits inside the band, inclusive of both
// boundaries (reference 06 §4: "orders at exactly the boundary are valid").
func (a AutoRejectPrice) contains(priceIDR int64) bool {
	return int64(a.Below) <= priceIDR && priceIDR <= int64(a.Above)
}

// IDR is an integer rupiah price that parses from either a JSON number or a
// JSON string. The string form is not hypothetical: proto3's JSON mapping
// serialises the uint64 fields of AutoRejectPrice as strings, and the whole
// band is useless if it fails to parse.
type IDR int64

func (v *IDR) UnmarshalJSON(b []byte) error {
	raw, num, isNum, err := scalarToken(b)
	if err != nil {
		return fmt.Errorf("idr price: %w", err)
	}
	if isNum {
		*v = IDR(num)
		return nil
	}
	raw = strings.TrimSpace(raw)
	if raw == "" {
		*v = 0
		return nil
	}
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return fmt.Errorf("idr price: %q is not an integer: %w", raw, err)
	}
	*v = IDR(n)
	return nil
}

// scalarToken decodes a JSON scalar into either a string or an integer,
// reporting which it was. null decodes as the empty string (not an error):
// an explicitly null enum or price is a missing value, which every caller
// here turns into Unknown.
func scalarToken(b []byte) (str string, num int64, isNum bool, err error) {
	trimmed := bytes.TrimSpace(b)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		return "", 0, false, nil
	}
	if trimmed[0] == '"' {
		var s string
		if err := json.Unmarshal(trimmed, &s); err != nil {
			return "", 0, false, err
		}
		return s, 0, false, nil
	}
	var n json.Number
	if err := json.Unmarshal(trimmed, &n); err != nil {
		return "", 0, false, err
	}
	i, err := n.Int64()
	if err != nil {
		return "", 0, false, fmt.Errorf("%s is not an integer: %w", n.String(), err)
	}
	return "", i, true, nil
}
