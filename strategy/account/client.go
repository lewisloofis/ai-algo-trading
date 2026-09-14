// Package account is a read-only client for the Carina brokerage
// account-state endpoints (portfolio, balance, PnL summary), plus the
// reconciliation logic that compares broker-reported holdings against the
// position derived from strategy/engine's post-trade store.
//
// This package sends zero orders and is structurally read-only: get is the
// only HTTP request path in the package, and http.MethodGet is the only HTTP
// method constant it references. readonly_test.go fails the build if that
// property is ever violated.
package account

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

// DefaultHost is the production Carina host, used when New is called with an
// empty host.
const DefaultHost = "https://carina.stockbit.com"

// bodyReadLimit caps how much of a response body get() will read, to bound
// memory on an unexpectedly large or non-JSON response.
const bodyReadLimit = 1 << 20 // 1 MiB

// bodySnippetLimit caps how much of a response body an APIError embeds.
const bodySnippetLimit = 500

// TokenSource supplies the trading (securities) session token used to
// authenticate every request. Carina requires the trading token obtained
// from the PIN login, not the main account token (docs/reference/05-account-state.md).
// strategy/auth.go's Session type (package main, and therefore unimportable
// here) satisfies this interface with its existing TradingToken method.
type TokenSource interface {
	TradingToken() string
}

// Client is a read-only HTTP client for the Carina account-state endpoints.
// It has exactly one request path (get); there is no method on Client that
// performs a POST, PUT, PATCH, or DELETE, and none should ever be added —
// see readonly_test.go.
type Client struct {
	host   string
	tokens TokenSource
	http   *http.Client
}

// New returns a Client for host, authenticating with tokens over httpClient.
// An empty host defaults to DefaultHost. A nil httpClient defaults to
// http.DefaultClient; tests should inject a client pointed at an
// httptest.Server so no test touches the real network.
func New(host string, tokens TokenSource, httpClient *http.Client) *Client {
	if host == "" {
		host = DefaultHost
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{host: strings.TrimRight(host, "/"), tokens: tokens, http: httpClient}
}

// ErrUnauthorized is returned when the trading token is rejected (HTTP 401
// or 403). It is a distinct, checkable error — never folded into an empty
// result. This repo has already shipped the bug where an expired token made
// an endpoint 401, the caller degraded to "nothing found", and a downstream
// process wrote a confident verdict over that silence (see
// docs/plans/18-alpha-and-infrastructure-split.md §4.4). Here that failure
// mode would report "you hold nothing" and reconcile it as a match, which is
// worse, so it must surface as an error instead.
var ErrUnauthorized = errors.New("account: trading token rejected (HTTP 401/403) — check SB_TRADING_ACCESS_TOKEN")

// ErrEmptyResponse is returned when a 200 response has no usable `data`
// payload — the field was absent, explicitly null, or failed to parse into
// the expected shape. It is distinct from a legitimately empty portfolio: a
// well-formed 200 with `results: []` is a flat account and is not an error.
var ErrEmptyResponse = errors.New("account: response had no usable data payload")

// APIError is returned for any non-2xx response that is not an auth failure.
type APIError struct {
	Path   string
	Status int
	Body   string // truncated to bodySnippetLimit bytes
}

func (e *APIError) Error() string {
	return fmt.Sprintf("account: %s returned HTTP %d: %s", e.Path, e.Status, e.Body)
}

// get is the package's one and only HTTP request path: a GET request with
// the Bearer trading token attached. It never sends POST, PUT, PATCH, or
// DELETE, and http.MethodGet is the only HTTP method constant referenced
// anywhere in this package (readonly_test.go asserts both properties). The
// token is read once from c.tokens and used solely to set the Authorization
// header — it is never included in a returned error or in any log line.
func (c *Client) get(ctx context.Context, path string, query url.Values) ([]byte, error) {
	u := c.host + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, fmt.Errorf("account: build request for %s: %w", path, err)
	}
	req.Header.Set("Authorization", "Bearer "+c.tokens.TradingToken())
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("account: request %s: %w", path, err)
	}
	defer resp.Body.Close()

	body, readErr := io.ReadAll(io.LimitReader(resp.Body, bodyReadLimit))

	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("account: %s: %w", path, ErrUnauthorized)
	}
	if resp.StatusCode/100 != 2 {
		return nil, &APIError{Path: path, Status: resp.StatusCode, Body: truncate(string(body), bodySnippetLimit)}
	}
	if readErr != nil {
		return nil, fmt.Errorf("account: read %s response body: %w", path, readErr)
	}
	return body, nil
}

// decodeEnvelope unmarshals a Carina response body of the form
// {"message": "...", "data": {...}} into T, returning data's payload.
//
// Parsing is defensive on purpose: 05-account-state.md warns the response
// nesting has varied between client/API versions. If the `data` key is
// entirely absent from an otherwise non-empty body, decodeEnvelope falls
// back to decoding the whole body as T (some client versions flatten the
// payload to the top level). But a `data` field that is present and
// explicitly null, or a body with no fields at all, is treated as an error —
// never silently decoded into a zero-value T, which would be
// indistinguishable from a legitimately empty result.
func decodeEnvelope[T any](body []byte, path string) (T, error) {
	var zero T

	var probe map[string]json.RawMessage
	if err := json.Unmarshal(body, &probe); err != nil {
		return zero, fmt.Errorf("%w: %s: response is not a JSON object: %v", ErrEmptyResponse, path, err)
	}

	raw, hasData := probe["data"]
	switch {
	case hasData && string(raw) == "null":
		return zero, fmt.Errorf("%w: %s: data field is null", ErrEmptyResponse, path)
	case hasData:
		// use raw as-is
	default:
		if len(probe) == 0 {
			return zero, fmt.Errorf("%w: %s: empty response body", ErrEmptyResponse, path)
		}
		raw = body
	}

	var out T
	if err := json.Unmarshal(raw, &out); err != nil {
		return zero, fmt.Errorf("%w: %s: decode data: %v", ErrEmptyResponse, path, err)
	}
	return out, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// PortfolioList fetches GET /portfolio/v2/list. accNo is optional; pass ""
// to let the server resolve the account from the auth token.
func (c *Client) PortfolioList(ctx context.Context, accNo string) (PortfolioList, error) {
	q := url.Values{}
	if accNo != "" {
		q.Set("acc_no", accNo)
	}
	body, err := c.get(ctx, "/portfolio/v2/list", q)
	if err != nil {
		return PortfolioList{}, err
	}
	return decodeEnvelope[PortfolioList](body, "/portfolio/v2/list")
}

// CashInfo fetches GET /balance/cash/info for stockCode (required — the
// server uses it to compute stock-specific buying power).
func (c *Client) CashInfo(ctx context.Context, stockCode string) (CashInfo, error) {
	q := url.Values{"stock_code": {stockCode}}
	body, err := c.get(ctx, "/balance/cash/info", q)
	if err != nil {
		return CashInfo{}, err
	}
	return decodeEnvelope[CashInfo](body, "/balance/cash/info")
}

// Withdrawable fetches GET /balance/withdrawable, aggregated across all
// related sub-accounts.
func (c *Client) Withdrawable(ctx context.Context) (Withdrawable, error) {
	body, err := c.get(ctx, "/balance/withdrawable", nil)
	if err != nil {
		return Withdrawable{}, err
	}
	return decodeEnvelope[Withdrawable](body, "/balance/withdrawable")
}

// PortfolioSummary fetches GET /portfolio/v2/summary — the aggregate PnL and
// debt-ratio view.
func (c *Client) PortfolioSummary(ctx context.Context) (PortfolioSummaryData, error) {
	body, err := c.get(ctx, "/portfolio/v2/summary", nil)
	if err != nil {
		return PortfolioSummaryData{}, err
	}
	return decodeEnvelope[PortfolioSummaryData](body, "/portfolio/v2/summary")
}

// CashInfo is GetCashInfoResponse.data (docs/reference/05-account-state.md §1.2).
type CashInfo struct {
	TradeLimit             float64 `json:"trade_limit"`
	TradeBalance           float64 `json:"trade_balance"`
	DayTradeBuyingPower    float64 `json:"day_trade_buying_power"`
	DayTradeLeverage       float64 `json:"day_trade_leverage"` // IDR amount, NOT a multiplier — confirmed live, contra §1.2's "leverage multiplier"
	DayTradeTradingBalance float64 `json:"day_trade_trading_balance"`
	Margin                 *Margin `json:"margin,omitempty"`
}

// Margin is CashInfo.margin. Confirmed live: this block is present for every
// account, not "only for margin accounts" as §1.3 states — a non-margin
// account carries Status == "MARGIN_ACCOUNT_STATUS_UNREGISTERED" and zeros
// for every other field. Status is therefore the real "is this a margin
// account" check; Margin != nil is not (kept as a pointer only for defensive
// parsing — see decodeEnvelope's doc comment — not because absence is
// expected).
type Margin struct {
	BuyingPower     float64 `json:"buying_power"`
	Status          string  `json:"status"`
	TradingBalance  float64 `json:"trading_balance"`
	TotalDebt       float64 `json:"total_debt"`
	TotalCollateral float64 `json:"total_collateral"`
}

// Withdrawable is GetWithdrawableResponse.data (§1.4).
type Withdrawable struct {
	Balance             WithdrawableBalance  `json:"balance"`
	SettlementSchedules []SettlementSchedule `json:"settlement_schedules"`
	Legends             []string             `json:"legends"`
}

// WithdrawableBalance is Withdrawable.balance.
type WithdrawableBalance struct {
	Withdrawable      float64 `json:"withdrawable"`
	Transaction       float64 `json:"transaction"`
	Pending           float64 `json:"pending"`
	LeverageLiability float64 `json:"leverage_liability"`
	NetWithdrawable   float64 `json:"net_withdrawable"`
	Transferable      float64 `json:"transferable"`
}

// SettlementSchedule is one entry of Withdrawable.settlement_schedules — the
// T+0/T+1/T+2 cash-settlement tiers.
type SettlementSchedule struct {
	Title         string  `json:"title"`
	FormattedDate string  `json:"formatted_date"`
	Balance       float64 `json:"balance"`
}

// PortfolioSummaryData is GET /portfolio/v2/summary's data payload (§2.4).
type PortfolioSummaryData struct {
	Aggregated   PortfolioSummary   `json:"aggregated_portfolio_summary"`
	PerPortfolio []PortfolioSummary `json:"summary_per_portfolios"`
}
