package market

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"collector/feed"
)

// Default hosts. The split is not cosmetic: the market-time endpoints are
// served by Exodus and authenticated with the main account token, while
// tradability and the ARA/ARB band are served by Carina and authenticated
// with the PIN-gated trading token (see TokenSource).
const (
	DefaultExodusHost = "https://exodus.stockbit.com"
	DefaultCarinaHost = "https://carina.stockbit.com"
)

// DefaultUserAgent is sent on every request. It is not decoration: the WAF
// in front of Exodus answers 403 to requests carrying a default library
// user-agent, which is how research/screener/screen_universe.py's
// LiveDataSource came to pin the same string. A 403 here would surface as
// "the gate could not answer", i.e. every order denied, so the header is
// part of the contract rather than a nicety.
const DefaultUserAgent = feed.DefaultUserAgent

// bodyReadLimit caps how much of a response body get will read, bounding
// memory on an unexpectedly large or non-JSON response.
const bodyReadLimit = 1 << 20 // 1 MiB

// bodySnippetLimit caps how much of a response body an APIError embeds.
const bodySnippetLimit = 500

// TokenSource supplies both session tokens this package needs. Stockbit has
// two independent authenticated sessions (docs/reference/README.md): the
// main account session, which Exodus accepts, and the trading (securities)
// session unlocked with the PIN, which Carina requires. A single-token
// interface would force a caller to guess which one a given endpoint wants
// and would fail at runtime, as a 401, on whichever half it guessed wrong.
//
// strategy/auth.go's Session type (package main, and therefore unimportable
// here) already satisfies this with its existing AccessToken and
// TradingToken methods — the same trick strategy/account's TokenSource
// uses.
type TokenSource interface {
	// AccessToken returns the main account session token (Exodus).
	AccessToken() string
	// TradingToken returns the trading session token (Carina).
	TradingToken() string
}

// StaticTokens is a TokenSource backed by two fixed strings, for callers
// that obtained tokens out of band (an environment variable, an operator
// paste) rather than by logging in. Mirrors how cmd/account-recon and
// strategy-run take pre-obtained tokens.
type StaticTokens struct {
	Access  string
	Trading string
}

func (s StaticTokens) AccessToken() string  { return s.Access }
func (s StaticTokens) TradingToken() string { return s.Trading }

// Client is a read-only HTTP client for the five reference-06 endpoints
// this package needs. get is its one and only request path; no method on
// Client performs a POST, PUT, PATCH or DELETE and none should ever be
// added (readonly_test.go fails the build if that changes).
type Client struct {
	exodusHost string
	carinaHost string
	userAgent  string
	tokens     TokenSource
	http       *http.Client
}

// ClientOptions overrides Client's defaults. The zero value is correct for
// production; tests set ExodusHost and CarinaHost to an httptest.Server so
// no test touches the real network.
type ClientOptions struct {
	ExodusHost string
	CarinaHost string
	UserAgent  string
	HTTPClient *http.Client
}

// NewClient returns a Client authenticating with tokens. tokens is required:
// there is no anonymous mode for these endpoints, and a nil TokenSource
// would panic on the first call rather than at construction.
func NewClient(tokens TokenSource, opts ClientOptions) (*Client, error) {
	if tokens == nil {
		return nil, errors.New("market: NewClient: tokens is required")
	}
	c := &Client{
		exodusHost: strings.TrimRight(orDefault(opts.ExodusHost, DefaultExodusHost), "/"),
		carinaHost: strings.TrimRight(orDefault(opts.CarinaHost, DefaultCarinaHost), "/"),
		userAgent:  orDefault(opts.UserAgent, DefaultUserAgent),
		tokens:     tokens,
		http:       opts.HTTPClient,
	}
	if c.http == nil {
		c.http = http.DefaultClient
	}
	return c, nil
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}

// ErrUnauthorized is returned when a token is rejected (HTTP 401 or 403).
// It is a distinct, checkable error and is never folded into an empty or
// permissive result — an expired token must surface as "this gate cannot
// answer" (which denies) and never as "nothing is suspended, carry on".
// This repo has already shipped the opposite bug once: plan 18 §4.4, where
// an expired token let a grid run write a confident NO-GO over zero
// symbols.
var ErrUnauthorized = errors.New("token rejected (HTTP 401/403)")

// ErrEmptyResponse is returned when a 200 response carries no usable data
// payload — absent, explicitly null, or unparseable into the expected
// shape. Distinct from a legitimately empty list.
var ErrEmptyResponse = errors.New("response had no usable data payload")

// APIError is any non-2xx response that is not an auth failure.
type APIError struct {
	Path   string
	Status int
	Body   string // truncated to bodySnippetLimit bytes
}

func (e *APIError) Error() string {
	return fmt.Sprintf("market: %s returned HTTP %d: %s", e.Path, e.Status, e.Body)
}

// get is the package's only HTTP request path: a GET with a Bearer token.
// The token is read immediately before the call (so a rotated token is
// picked up without rebuilding the Client) and used solely to set the
// Authorization header — it is never logged and never embedded in a
// returned error.
func (c *Client) get(ctx context.Context, host, path string, query url.Values, token string) ([]byte, error) {
	u := host + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, fmt.Errorf("market: build request for %s: %w", path, err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent)
	// Mirrors strategy/auth.go's baseHeaders, which is what the working
	// live calls in this repo send.
	req.Header.Set("X-Platform", "android")
	req.Header.Set("X-AppVersion", "7.0.0")
	req.Header.Set("Accept-Language", "id")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("market: request %s: %w", path, err)
	}
	defer resp.Body.Close()

	body, readErr := io.ReadAll(io.LimitReader(resp.Body, bodyReadLimit))

	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("market: %s: %w", path, ErrUnauthorized)
	}
	if resp.StatusCode/100 != 2 {
		return nil, &APIError{Path: path, Status: resp.StatusCode, Body: truncate(string(body), bodySnippetLimit)}
	}
	if readErr != nil {
		return nil, fmt.Errorf("market: read %s response body: %w", path, readErr)
	}
	return body, nil
}

// decodeEnvelope unmarshals a {"message": "...", "data": ...} response into
// T and returns data's payload.
//
// This is a deliberate duplicate of strategy/account's decodeEnvelope: the
// two packages speak to the same gateway and must parse it the same way,
// but account is structurally read-only under its own build-failing test
// and importing across them to share ten lines would couple a gate to a
// reconciler for no gain. Keep the two in sync — the defensive behaviour
// below is the part that matters:
//
// A `data` key that is present but explicitly null, or a body with no
// fields at all, is an error. It is never decoded into a zero-value T,
// because a zero-value T here reads as "every flag false", and a false
// is_tradable that came from a parse failure is indistinguishable from a
// genuine one. (A zero-value T would deny rather than allow, so this is not
// a safety hole — but it would report a confident, wrong reason.)
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

const (
	pathMarketTime    = "/company-price-feed/market-time"
	pathMarketSession = "/company-price-feed/market-time/session"
	pathStockTradable = "/stock/tradable"
	pathOrderBook     = "/nego-engine/v1/order-book/"
)

// MarketTime fetches the coarse open/closed status (reference 06 §2.1) from
// Exodus with the main account token.
func (c *Client) MarketTime(ctx context.Context) (MarketTimeData, error) {
	body, err := c.get(ctx, c.exodusHost, pathMarketTime, nil, c.tokens.AccessToken())
	if err != nil {
		return MarketTimeData{}, err
	}
	return decodeEnvelope[MarketTimeData](body, pathMarketTime)
}

// SessionDetail fetches the intraday session phase (reference 06 §2.2) from
// Exodus with the main account token.
//
// No datetime query parameter is sent, deliberately. Reference 06 documents
// one and Stockbit's own web client omits it
// (sb-web/src/lib/api/market.ts:29-30), so the server's own clock decides
// "now" — which removes an entire bug class, since the parameter's timezone
// is undocumented and a WIB/UTC mix-up would silently answer for a moment
// seven hours away. That is a phase boundary this gate would then get
// exactly wrong.
func (c *Client) SessionDetail(ctx context.Context) (SessionData, error) {
	body, err := c.get(ctx, c.exodusHost, pathMarketSession, nil, c.tokens.AccessToken())
	if err != nil {
		return SessionData{}, err
	}
	return decodeEnvelope[SessionData](body, pathMarketSession)
}

// Tradable fetches broker-side tradability for one symbol (reference 06
// §7.1, corrected — see the package doc) from Carina with the trading
// token. The endpoint takes a stock_codes list and answers with a row per
// code; this asks for one so that a gate decision is never made from a row
// that belonged to a different symbol.
func (c *Client) Tradable(ctx context.Context, symbol string) (TradableRow, error) {
	q := url.Values{"stock_codes": {symbol}}
	body, err := c.get(ctx, c.carinaHost, pathStockTradable, q, c.tokens.TradingToken())
	if err != nil {
		return TradableRow{}, err
	}
	rows, err := decodeEnvelope[[]TradableRow](body, pathStockTradable)
	if err != nil {
		return TradableRow{}, err
	}
	for _, row := range rows {
		if strings.EqualFold(row.StockCode, symbol) {
			return row, nil
		}
	}
	// A 200 that does not mention the symbol asked about is not "not
	// tradable" — it is an unanswered question, and the difference decides
	// whether the caller reports Deny or Unknown.
	return TradableRow{}, fmt.Errorf("%w: %s: no row for symbol %q in %d row(s)", ErrEmptyResponse, pathStockTradable, symbol, len(rows))
}

// AutoRejectPrice fetches the day's ARA/ARB band for one symbol (reference
// 06 §4) from Carina with the trading token.
func (c *Client) AutoRejectPrice(ctx context.Context, symbol string) (AutoRejectPrice, error) {
	body, err := c.get(ctx, c.carinaHost, pathOrderBook+url.PathEscape(symbol), nil, c.tokens.TradingToken())
	if err != nil {
		return AutoRejectPrice{}, err
	}
	data, err := decodeEnvelope[OrderBookData](body, pathOrderBook)
	if err != nil {
		return AutoRejectPrice{}, err
	}
	return data.AutoRejectPrice, nil
}
