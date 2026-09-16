package market

import (
	"context"
	"errors"
	"net/http"
	"testing"
)

func TestNewClient_RequiresTokens(t *testing.T) {
	if _, err := NewClient(nil, ClientOptions{}); err == nil {
		t.Fatal("NewClient(nil) succeeded; a nil TokenSource must fail at construction, not at the first order")
	}
}

func TestNewClient_DefaultsToProductionHosts(t *testing.T) {
	c, err := NewClient(StaticTokens{}, ClientOptions{})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c.exodusHost != DefaultExodusHost || c.carinaHost != DefaultCarinaHost {
		t.Errorf("hosts = %q/%q, want %q/%q", c.exodusHost, c.carinaHost, DefaultExodusHost, DefaultCarinaHost)
	}
	if c.userAgent != DefaultUserAgent {
		t.Errorf("userAgent = %q, want %q", c.userAgent, DefaultUserAgent)
	}
}

// TestEachEndpointUsesItsOwnSessionToken is the test that would have caught
// the most likely wiring mistake in this package: Stockbit has two
// independent sessions, and sending the main token to Carina (or the
// trading token to Exodus) fails as a 401, which this gate reports as
// "could not establish" — i.e. every order denied, for a reason that looks
// like an outage.
func TestEachEndpointUsesItsOwnSessionToken(t *testing.T) {
	f := newFakeStack(t)
	c := f.client(t)
	ctx := context.Background()

	if _, err := c.MarketTime(ctx); err != nil {
		t.Fatalf("MarketTime: %v", err)
	}
	if _, err := c.SessionDetail(ctx); err != nil {
		t.Fatalf("SessionDetail: %v", err)
	}
	if _, err := c.Tradable(ctx, "BBCA"); err != nil {
		t.Fatalf("Tradable: %v", err)
	}
	if _, err := c.AutoRejectPrice(ctx, "BBCA"); err != nil {
		t.Fatalf("AutoRejectPrice: %v", err)
	}

	cases := []struct {
		path string
		want string
	}{
		{pathMarketTime, "Bearer " + testAccessToken},
		{pathMarketSession, "Bearer " + testAccessToken},
		{pathStockTradable, "Bearer " + testTradingToken},
		{pathOrderBook, "Bearer " + testTradingToken},
	}
	for _, tc := range cases {
		if got := f.authFor(tc.path); got != tc.want {
			t.Errorf("%s Authorization = %q, want %q", tc.path, got, tc.want)
		}
	}
}

// TestUserAgentIsSent pins the WAF workaround. Without it the live calls
// answer 403, which this package would report as "unauthorized" — an
// operator would chase a token that was never the problem.
func TestUserAgentIsSent(t *testing.T) {
	f := newFakeStack(t)
	if _, err := f.client(t).MarketTime(context.Background()); err != nil {
		t.Fatalf("MarketTime: %v", err)
	}
	if got := f.agentFor(pathMarketTime); got != DefaultUserAgent {
		t.Errorf("User-Agent = %q, want %q", got, DefaultUserAgent)
	}
}

func TestAuthFailuresAreTypedNotEmpty(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		f := newFakeStack(t)
		f.setStatus(pathStockTradable, status)
		_, err := f.client(t).Tradable(context.Background(), "BBCA")
		if !errors.Is(err, ErrUnauthorized) {
			t.Errorf("HTTP %d: err = %v, want ErrUnauthorized — an expired token must never read as 'not tradable'", status, err)
		}
	}
}

func TestNon2xxBecomesAPIError(t *testing.T) {
	f := newFakeStack(t)
	f.setStatus(pathMarketTime, http.StatusInternalServerError)
	_, err := f.client(t).MarketTime(context.Background())
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("err = %v, want *APIError", err)
	}
	if apiErr.Status != http.StatusInternalServerError {
		t.Errorf("APIError.Status = %d, want 500", apiErr.Status)
	}
}

func TestNullDataIsAnErrorNotAZeroValue(t *testing.T) {
	f := newFakeStack(t)
	f.setBody(pathStockTradable, `{"message":"ok","data":null}`)
	_, err := f.client(t).Tradable(context.Background(), "BBCA")
	if !errors.Is(err, ErrEmptyResponse) {
		t.Fatalf("err = %v, want ErrEmptyResponse", err)
	}
}

// TestTradableRowForAnotherSymbolIsNotAnAnswer is the subtle one. The
// endpoint takes a list and answers with a list; a response that omits the
// symbol asked about must not be read as a zero-value row, because a
// zero-value row has IsTradable=false and would be reported as a confident
// "this stock is halted".
func TestTradableRowForAnotherSymbolIsNotAnAnswer(t *testing.T) {
	f := newFakeStack(t)
	f.setBody(pathStockTradable, `{"message":"ok","data":[{"stock_code":"BBRI","is_tradable":true}]}`)
	_, err := f.client(t).Tradable(context.Background(), "BBCA")
	if !errors.Is(err, ErrEmptyResponse) {
		t.Fatalf("err = %v, want ErrEmptyResponse for a response with no BBCA row", err)
	}
}

func TestTradableMatchesSymbolCaseInsensitively(t *testing.T) {
	f := newFakeStack(t)
	f.setBody(pathStockTradable, `{"message":"ok","data":[{"stock_code":"bbca","is_tradable":true}]}`)
	row, err := f.client(t).Tradable(context.Background(), "BBCA")
	if err != nil {
		t.Fatalf("Tradable: %v", err)
	}
	if !row.IsTradable {
		t.Error("IsTradable = false, want true")
	}
}

func TestAutoRejectPriceParsesStringAndNumericEncodings(t *testing.T) {
	cases := map[string]string{
		"string-encoded uint64 (proto3 JSON mapping)": `{"data":{"auto_reject_price":{"above":"1100","below":"900"}}}`,
		"bare numbers (reference 06's example)":       `{"data":{"auto_reject_price":{"above":1100,"below":900}}}`,
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			f := newFakeStack(t)
			f.setBody(pathOrderBook, body)
			band, err := f.client(t).AutoRejectPrice(context.Background(), "BBCA")
			if err != nil {
				t.Fatalf("AutoRejectPrice: %v", err)
			}
			if band.Above != 1100 || band.Below != 900 {
				t.Errorf("band = %d..%d, want 900..1100", band.Below, band.Above)
			}
		})
	}
}

func TestOrderBookPathCarriesTheSymbol(t *testing.T) {
	f := newFakeStack(t)
	var seen string
	f.carina.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(bodyBand9001100))
	})
	if _, err := f.client(t).AutoRejectPrice(context.Background(), "BBCA"); err != nil {
		t.Fatalf("AutoRejectPrice: %v", err)
	}
	if want := pathOrderBook + "BBCA"; seen != want {
		t.Errorf("path = %q, want %q", seen, want)
	}
}
