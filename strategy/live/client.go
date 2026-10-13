package live

import (
	"bytes"
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

// This file mirrors strategy/account/client.go's transport shape (host
// default, bodyReadLimit, the {"data": ...} envelope convention, APIError,
// ErrUnauthorized) rather than importing or extending it. account is
// structurally read-only by construction — readonly_test.go fails the
// build the moment a POST/PUT/PATCH/DELETE appears anywhere in that
// package — and this package's entire reason to exist is to send exactly
// those methods. Sharing the ~60 lines of transport below would mean
// either weakening account's read-only guarantee (the one property that
// makes it safe to hand a read-only client to anything without a second
// thought) or inventing a third package for a request builder this small.
// Neither is worth it; the duplication is deliberate and should stay that
// way even if the two transports drift slightly over time.

// DefaultHost is the production Carina host, used when NewClient is called
// with an empty host.
const DefaultHost = "https://carina.stockbit.com"

// bodyReadLimit caps how much of a response body do() will read, bounding
// memory on an unexpectedly large or non-JSON response.
const bodyReadLimit = 1 << 20 // 1 MiB

// bodySnippetLimit caps how much of a response body an APIError, or a
// classified sentinel error, embeds.
const bodySnippetLimit = 500

// sharesPerLot mirrors strategy/engine/sink.go's, strategy/risk's, and
// strategy/strategies/rebalance's own constant: 1 lot = 100 shares on the
// IDX (docs/reference/06-pre-trade-rules.md §6). This package cannot import
// any of theirs without creating a dependency none of them expect, so this
// is a fourth independent copy that must be kept in sync with the others by
// hand — see rebalance.go's snapTickIDR for the precedent.
const sharesPerLot = 100

// TokenSource supplies the trading (securities) session token used to
// authenticate every request. Carina requires the trading token obtained
// from the PIN login, not the main account token
// (docs/reference/01-authentication.md §4). It is declared independently
// from strategy/account.TokenSource — Go's structural typing means a single
// concrete type (strategy/auth.go's Session) satisfies both without either
// package importing the other.
type TokenSource interface {
	TradingToken() string
}

// Client is a Carina order-routing client, scoped to a single brokerage
// sub-account (accNo — see this package's doc comment for the open
// question about what that scoping actually accomplishes today). It is the
// only client in this module with a write path: every other package either
// reads (strategy/account) or never leaves the process (engine.PaperSink).
type Client struct {
	host   string
	accNo  string
	tokens TokenSource
	http   *http.Client
}

// NewClient returns a Client for host, scoped to accNo, authenticating with
// tokens over hc. It fails closed on two independent conditions:
//
//   - arming.Armed() must be true. This check runs FIRST, before accNo or
//     tokens are even inspected, so an unarmed call touches nothing and
//     sends nothing — see ErrNotArmed and this package's doc comment on
//     why there is no dry-run fallback instead.
//   - accNo is required, for the same reason strategy/account.New requires
//     it: an empty value would silently fall through to whichever account
//     the trading token's own login addresses, and this package's caller
//     needs to have made that choice explicitly, not by omission.
//
// tokens must not be nil: unlike strategy/account.New (which leaves a nil
// TokenSource to panic on first use), this package places real orders with
// real money, and a construction-time check costs nothing next to that.
//
// An empty host defaults to DefaultHost. A nil hc defaults to
// http.DefaultClient; tests should inject a client pointed at an
// httptest.Server so no test ever touches the real network.
func NewClient(host, accNo string, tokens TokenSource, hc *http.Client, arming Arming) (*Client, error) {
	if !arming.Armed() {
		return nil, ErrNotArmed
	}
	if accNo == "" {
		return nil, errors.New("live: NewClient: accNo is required (an empty value would silently fall through to the trading token's own default account)")
	}
	if tokens == nil {
		return nil, errors.New("live: NewClient: tokens is required")
	}
	if host == "" {
		host = DefaultHost
	}
	if hc == nil {
		hc = http.DefaultClient
	}
	return &Client{host: strings.TrimRight(host, "/"), accNo: accNo, tokens: tokens, http: hc}, nil
}

// ErrUnauthorized is returned when the trading token is rejected (HTTP 401
// or 403). It is a distinct, checkable error — never folded into "no orders"
// or any other empty-looking result. See strategy/account/client.go's
// ErrUnauthorized for the incident this convention guards against; here the
// equivalent failure would be a Submit that silently never placed an order
// while reporting nothing wrong.
var ErrUnauthorized = errors.New("live: trading token rejected (HTTP 401/403)")

// Sentinel errors for the typed failures docs/reference/04-order-lifecycle.md
// §5.3 documents at the API layer. errors.Is against these; the wrapped
// message carries the endpoint and a truncated body snippet.
//
// ErrDuplicateOrder is not a failure in the usual sense: it means Carina's
// ui_ref idempotency guard fired because this exact action was already
// submitted once. The caller (Sink.Submit) resolves it by finding and
// adopting the order that already resulted from the first attempt, via
// List/Detail, rather than surfacing it as an error to the strategy that
// is holding a reservation against this order's lots.
var (
	ErrDuplicateOrder   = errors.New("live: duplicate order (ui_ref already used) — resolve via List/Detail, do not treat as a failure")
	ErrUserSuspended    = errors.New("live: account suspended")
	ErrInvalidParameter = errors.New("live: invalid order parameter")
)

// APIError is returned for any non-2xx response that classifyOrderError
// could not map to one of the sentinels above.
type APIError struct {
	Path   string
	Status int
	Body   string // truncated to bodySnippetLimit bytes
}

func (e *APIError) Error() string {
	return fmt.Sprintf("live: %s returned HTTP %d: %s", e.Path, e.Status, e.Body)
}

// classifyOrderError maps a non-2xx order-endpoint response to a typed
// sentinel when the body names a recognised failure mode, or to a generic
// *APIError otherwise.
//
// GUESS, flagged for review: docs/reference/04-order-lifecycle.md §5.3 names
// the typed protobuf error variants (ErrorDuplicateOrder, ErrorUserSuspended,
// ErrorInvalidParameter, ...) this package must detect, but neither that doc
// nor any fixture captures the JSON shape Carina actually serializes them
// as over this REST surface — only the proto message names are confirmed,
// not their wire encoding. This checks the raw response body, case-
// insensitively, for each variant's name; it is a best-effort classifier
// pending a real captured error response, not a confirmed contract. A body
// that matches none of the known substrings still returns a usable
// *APIError with the response attached, so an unrecognised shape degrades
// to "generic failure", never to "success" or to the wrong sentinel.
func classifyOrderError(path string, status int, body []byte) error {
	text := strings.ToUpper(string(body))
	snippet := truncate(string(body), bodySnippetLimit)
	switch {
	case strings.Contains(text, "DUPLICATE"):
		return fmt.Errorf("%w: %s (HTTP %d): %s", ErrDuplicateOrder, path, status, snippet)
	case strings.Contains(text, "SUSPEND"):
		return fmt.Errorf("%w: %s (HTTP %d): %s", ErrUserSuspended, path, status, snippet)
	case strings.Contains(text, "INVALID") && (strings.Contains(text, "PARAM") || strings.Contains(text, "PARAMETER")):
		return fmt.Errorf("%w: %s (HTTP %d): %s", ErrInvalidParameter, path, status, snippet)
	default:
		return &APIError{Path: path, Status: status, Body: snippet}
	}
}

// do is this package's one request builder, mirroring
// strategy/account/client.go's get(): every method on Client funnels through
// it, so the Authorization header, the body-size cap, and the error
// classification all live in exactly one place. Unlike account's get, this
// also supports POST with a JSON body, since placing/cancelling/amending an
// order is this package's entire purpose.
//
// The token is read once from c.tokens and used solely to set the
// Authorization header — it is never included in a returned error or a log
// line. See structural_test.go for the test that enforces this at runtime.
func (c *Client) do(ctx context.Context, method, path string, query url.Values, jsonBody any) ([]byte, error) {
	u := c.host + path
	if len(query) > 0 {
		u += "?" + query.Encode()
	}

	var reader io.Reader
	if jsonBody != nil {
		b, err := json.Marshal(jsonBody)
		if err != nil {
			return nil, fmt.Errorf("live: encode request for %s: %w", path, err)
		}
		reader = bytes.NewReader(b)
	}

	req, err := http.NewRequestWithContext(ctx, method, u, reader)
	if err != nil {
		return nil, fmt.Errorf("live: build request for %s: %w", path, err)
	}
	req.Header.Set("User-Agent", feed.DefaultUserAgent)
	req.Header.Set("Authorization", "Bearer "+c.tokens.TradingToken())
	req.Header.Set("Accept", "application/json")
	if jsonBody != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("live: request %s: %w", path, err)
	}
	defer resp.Body.Close()

	body, readErr := io.ReadAll(io.LimitReader(resp.Body, bodyReadLimit))

	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("live: %s: %w", path, ErrUnauthorized)
	}
	if resp.StatusCode/100 != 2 {
		return nil, classifyOrderError(path, resp.StatusCode, body)
	}
	if readErr != nil {
		return nil, fmt.Errorf("live: read %s response body: %w", path, readErr)
	}
	return body, nil
}

func (c *Client) get(ctx context.Context, path string, query url.Values) ([]byte, error) {
	return c.do(ctx, http.MethodGet, path, query, nil)
}

func (c *Client) postJSON(ctx context.Context, path string, body any) ([]byte, error) {
	return c.do(ctx, http.MethodPost, path, nil, body)
}

// decodeEnvelope unmarshals a Carina response body of the form
// {"message": "...", "data": ...} into T, returning data's payload. It
// mirrors strategy/account/client.go's decodeEnvelope exactly, including
// its flattened-envelope fallback and its refusal to decode an explicitly
// null or absent data field into a zero-value T — see that function's doc
// comment for the full reasoning; it applies here unchanged.
func decodeEnvelope[T any](body []byte, path string) (T, error) {
	var zero T

	var probe map[string]json.RawMessage
	if err := json.Unmarshal(body, &probe); err != nil {
		return zero, fmt.Errorf("live: %s: response is not a JSON object: %v", path, err)
	}

	raw, hasData := probe["data"]
	switch {
	case hasData && string(raw) == "null":
		return zero, fmt.Errorf("live: %s: data field is null", path)
	case hasData:
		// use raw as-is
	default:
		if len(probe) == 0 {
			return zero, fmt.Errorf("live: %s: empty response body", path)
		}
		raw = body
	}

	var out T
	if err := json.Unmarshal(raw, &out); err != nil {
		return zero, fmt.Errorf("live: %s: decode data: %v", path, err)
	}
	return out, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
