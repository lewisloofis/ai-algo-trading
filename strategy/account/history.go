package account

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// This file reads GET /history, the broker's own trade record. It is the
// source of truth the bot's post-trade stores are compared against (see
// strategy/recon). Like everything in this package it is GET-only.
//
// Account scoping: the request carries no account parameter, so like
// /order/v2/list the answer is for the trading token's own account. Callers
// must confirm the token's account is the one they mean (strategy/tokenacct)
// before treating the history as that account's.
//
// Privacy: a row's move_stock_cash block holds account numbers and holder
// names. It is deliberately NOT modelled — encoding/json drops it at decode —
// so no value of any type in this file can carry it, and nothing downstream
// can persist or log it (history_test.go proves it).

// WIB is Jakarta time, UTC+7, no daylight saving. A fixed zone avoids a
// dependency on the host's tzdata.
var WIB = time.FixedZone("WIB", 7*60*60)

const (
	// historyPath is the endpoint.
	historyPath = "/history"
	// HistoryMaxPages bounds pagination: a broker answering max_page in the
	// thousands is a malformed response, not a reason to loop.
	HistoryMaxPages = 50
	// historyAmountSlackIDR is how far amount may differ from price*shares
	// before the row is rejected as inconsistent.
	historyAmountSlackIDR = 1.0
)

// HistoryQuery is the endpoint's filter set. Start/End/Action/Keyword are
// sent empty, as the app does.
type HistoryQuery struct {
	Period  string // e.g. "3 month"
	Limit   int
	Start   string
	End     string
	Action  string
	Keyword string
}

// DefaultHistoryQuery is what the app sends: 100 rows per page, 3 months.
func DefaultHistoryQuery() HistoryQuery { return HistoryQuery{Period: "3 month", Limit: 100} }

// HistoryTrade is one executed BUY or SELL (command BUY/SELL, status MATCH).
type HistoryTrade struct {
	ID           string    `json:"id"`
	Symbol       string    `json:"symbol"`
	Side         string    `json:"side"` // "buy" | "sell"
	PriceIDR     float64   `json:"price_idr"`
	Lots         int       `json:"lots"`
	AmountIDR    float64   `json:"amount_idr"`
	FeeIDR       float64   `json:"fee_idr"` // the broker's fee: truth
	NetAmountIDR float64   `json:"net_amount_idr"`
	Time         time.Time `json:"time"` // WIB
}

// Date is the trade's Jakarta calendar date, YYYY-MM-DD.
func (t HistoryTrade) Date() string { return t.Time.In(WIB).Format("2006-01-02") }

// CashEvent is a non-trade money movement (cash in/out, dividends, ...).
// Account numbers and names are not carried.
type CashEvent struct {
	ID        string    `json:"id"`
	Command   string    `json:"command"`
	AmountIDR float64   `json:"amount_idr"`
	Status    string    `json:"status"`
	Time      time.Time `json:"time"`
}

// HistoryPage is one parsed response page.
type HistoryPage struct {
	Trades     []HistoryTrade `json:"trades"`
	CashEvents []CashEvent    `json:"cash_events"`
	// Skipped counts rows that were recognised but are not executed trades,
	// keyed "COMMAND(STATUS)", e.g. a cancelled BUY.
	Skipped map[string]int `json:"skipped"`
	// Unknown counts rows whose command this parser does not know. They are
	// never dropped silently and never break parsing.
	Unknown map[string]int `json:"unknown"`
	MaxPage int            `json:"max_page"`
}

// History is every page, merged.
type History struct {
	Trades     []HistoryTrade
	CashEvents []CashEvent
	Skipped    map[string]int
	Unknown    map[string]int
	Pages      int
}

var symbolRE = regexp.MustCompile(`^[A-Z0-9]{2,8}$`)

type historyEnvelope struct {
	History []struct {
		HistoryList []json.RawMessage `json:"history_list"`
	} `json:"history"`
	Meta struct {
		MaxPage int `json:"max_page"`
	} `json:"meta"`
}

// tradeRow is the documented shape of a BUY/SELL row. Unknown fields are
// ignored; documented ones are validated by validate().
type tradeRow struct {
	Command   string   `json:"command"`
	Symbol    string   `json:"symbol"`
	Price     *float64 `json:"price"`
	Lot       *float64 `json:"lot"`
	Shares    *float64 `json:"shares"`
	Amount    *float64 `json:"amount"`
	Fee       *float64 `json:"fee"`
	NetAmount *float64 `json:"netamount"`
	Status    string   `json:"status"`
	Date      string   `json:"date"`
	Time      string   `json:"time"`
	ID        string   `json:"id"`
}

// looseRow reads only fields every row has, tolerating any type elsewhere.
type looseRow struct {
	Command string          `json:"command"`
	Status  string          `json:"status"`
	ID      json.RawMessage `json:"id"`
	Amount  json.RawMessage `json:"amount"`
	Date    string          `json:"date"`
	Time    string          `json:"time"`
}

// cashCommands are non-trade commands recognised as money movements.
func isCashCommand(c string) bool {
	switch {
	case strings.HasPrefix(c, "INTRA_MOVE_CASH"):
		return true
	}
	switch c {
	case "CASH_IN", "CASH_OUT", "DIVIDEND", "DEPOSIT", "WITHDRAW", "WITHDRAWAL":
		return true
	}
	return false
}

// ParseHistoryPage parses one /history response body. A BUY/SELL row with
// status MATCH is validated field by field and any defect is an error (a
// wrong trade must not slip into a reconciliation); every other row is
// counted, never dropped silently, and can never fail the parse.
func ParseHistoryPage(body []byte) (HistoryPage, error) {
	page := HistoryPage{Skipped: map[string]int{}, Unknown: map[string]int{}}
	env, err := decodeEnvelope[historyEnvelope](body, historyPath)
	if err != nil {
		return HistoryPage{}, err
	}
	page.MaxPage = env.Meta.MaxPage
	if page.MaxPage < 1 {
		page.MaxPage = 1
	}
	n := 0
	for _, month := range env.History {
		for _, raw := range month.HistoryList {
			n++
			var l looseRow
			if err := json.Unmarshal(raw, &l); err != nil {
				page.Unknown["(unparseable row)"]++
				continue
			}
			switch l.Command {
			case "BUY", "SELL":
				if l.Status != "MATCH" {
					page.Skipped[l.Command+"("+l.Status+")"]++
					continue
				}
				tr, err := parseTradeRow(raw)
				if err != nil {
					return HistoryPage{}, fmt.Errorf("account: %s: row %d: %w", historyPath, n, err)
				}
				page.Trades = append(page.Trades, tr)
			default:
				if isCashCommand(l.Command) {
					page.CashEvents = append(page.CashEvents, cashEventFrom(l))
				} else {
					page.Unknown[l.Command]++
				}
			}
		}
	}
	return page, nil
}

func cashEventFrom(l looseRow) CashEvent {
	ev := CashEvent{Command: l.Command, Status: l.Status}
	var id string
	if json.Unmarshal(l.ID, &id) == nil {
		ev.ID = id
	}
	var amt float64
	if json.Unmarshal(l.Amount, &amt) == nil {
		ev.AmountIDR = amt
	}
	if t, err := parseWIB(l.Date, l.Time); err == nil {
		ev.Time = t
	}
	return ev
}

func parseWIB(date, clock string) (time.Time, error) {
	return time.ParseInLocation("2 Jan 2006 15:04:05", strings.TrimSpace(date)+" "+strings.TrimSpace(clock), WIB)
}

func parseTradeRow(raw json.RawMessage) (HistoryTrade, error) {
	var r tradeRow
	if err := json.Unmarshal(raw, &r); err != nil {
		return HistoryTrade{}, fmt.Errorf("trade row has the wrong field types: %w", err)
	}
	if r.ID == "" {
		return HistoryTrade{}, fmt.Errorf("id is missing")
	}
	if !symbolRE.MatchString(r.Symbol) {
		return HistoryTrade{}, fmt.Errorf("id %s: symbol %q is not a ticker", r.ID, r.Symbol)
	}
	if r.Price == nil || !(*r.Price > 0) || math.IsInf(*r.Price, 0) {
		return HistoryTrade{}, fmt.Errorf("id %s: price must be above 0", r.ID)
	}
	if r.Lot == nil || !(*r.Lot >= 1) || *r.Lot != math.Trunc(*r.Lot) {
		return HistoryTrade{}, fmt.Errorf("id %s: lot must be a whole number of at least 1", r.ID)
	}
	lots := int(*r.Lot)
	if r.Shares != nil && *r.Shares != 0 && *r.Shares != float64(lots*100) {
		return HistoryTrade{}, fmt.Errorf("id %s: shares %v != lot %d x 100", r.ID, *r.Shares, lots)
	}
	if r.Fee == nil || *r.Fee < 0 {
		return HistoryTrade{}, fmt.Errorf("id %s: fee must be 0 or more", r.ID)
	}
	notional := *r.Price * float64(lots*100)
	if r.Amount != nil && *r.Amount != 0 && math.Abs(*r.Amount-notional) > historyAmountSlackIDR {
		return HistoryTrade{}, fmt.Errorf("id %s: amount %v != price x shares %v", r.ID, *r.Amount, notional)
	}
	ts, err := parseWIB(r.Date, r.Time)
	if err != nil {
		return HistoryTrade{}, fmt.Errorf("id %s: date/time: %w", r.ID, err)
	}
	tr := HistoryTrade{
		ID: r.ID, Symbol: r.Symbol, PriceIDR: *r.Price, Lots: lots, FeeIDR: *r.Fee, AmountIDR: notional, Time: ts,
	}
	if r.Amount != nil && *r.Amount != 0 {
		tr.AmountIDR = *r.Amount
	}
	if r.NetAmount != nil {
		tr.NetAmountIDR = *r.NetAmount
	}
	tr.Side = strings.ToLower(r.Command)
	return tr, nil
}

// History fetches every page of /history for q, following meta.max_page, and
// merges them. It is read-only.
func (c *Client) History(ctx context.Context, q HistoryQuery) (History, error) {
	out := History{Skipped: map[string]int{}, Unknown: map[string]int{}}
	for page := 1; ; page++ {
		v := url.Values{
			"page":    {strconv.Itoa(page)},
			"limit":   {strconv.Itoa(q.Limit)},
			"period":  {q.Period},
			"start":   {q.Start},
			"end":     {q.End},
			"action":  {q.Action},
			"keyword": {q.Keyword},
		}
		body, err := c.get(ctx, historyPath, v)
		if err != nil {
			return History{}, err
		}
		p, err := ParseHistoryPage(body)
		if err != nil {
			return History{}, err
		}
		if p.MaxPage > HistoryMaxPages {
			return History{}, fmt.Errorf("account: %s: max_page %d exceeds the safety cap %d", historyPath, p.MaxPage, HistoryMaxPages)
		}
		out.Trades = append(out.Trades, p.Trades...)
		out.CashEvents = append(out.CashEvents, p.CashEvents...)
		for k, n := range p.Skipped {
			out.Skipped[k] += n
		}
		for k, n := range p.Unknown {
			out.Unknown[k] += n
		}
		out.Pages = page
		if page >= p.MaxPage {
			return out, nil
		}
	}
}
