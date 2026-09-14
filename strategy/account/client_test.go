package account

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// fakeTokenSource is a TokenSource that returns a fixed token, and records
// whether it was ever asked for it.
type fakeTokenSource struct {
	token string
	asked bool
}

func (f *fakeTokenSource) TradingToken() string {
	f.asked = true
	return f.token
}

// serveFixture starts an httptest.Server that returns the named testdata
// file with the given status for every request, and records the last
// request it received.
func serveFixture(t *testing.T, status int, fixture string) (*httptest.Server, *http.Request) {
	t.Helper()
	var lastReq *http.Request
	body := []byte("{}")
	if fixture != "" {
		data, err := os.ReadFile(fixture)
		if err != nil {
			t.Fatalf("read fixture %s: %v", fixture, err)
		}
		body = data
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		lastReq = r
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write(body)
	}))
	t.Cleanup(srv.Close)
	return srv, lastReq
}

// serveJSON starts an httptest.Server that returns the literal body for
// every request, for tests where a small inline response is clearer than a
// testdata fixture file.
func serveJSON(t *testing.T, status int, body string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestPortfolioList_ParsesEnvelope(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "testdata/portfolio_list.json")
	tokens := &fakeTokenSource{token: "trading-token"}
	c := New(srv.URL, tokens, srv.Client())

	list, err := c.PortfolioList(context.Background(), "")
	if err != nil {
		t.Fatalf("PortfolioList: %v", err)
	}
	if !tokens.asked {
		t.Fatal("client never asked TokenSource for a token")
	}
	if len(list.Results) != 2 {
		t.Fatalf("Results = %d entries, want 2", len(list.Results))
	}
	if got := list.Results[0].Symbol; got != "BBCA" {
		t.Errorf("Results[0].Symbol = %q, want BBCA", got)
	}
	if got := list.Results[0].Qty.Available.Shares(); got != 1000 {
		t.Errorf("Results[0].Qty.Available.Shares() = %v, want 1000", got)
	}
	if got := list.Summary.Debt.Ratio; got != 0 {
		t.Errorf("Summary.Debt.Ratio = %v, want 0", got)
	}
}

func TestPortfolioList_EmptyResultsIsNotAnError(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "testdata/portfolio_list_empty.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	list, err := c.PortfolioList(context.Background(), "")
	if err != nil {
		t.Fatalf("PortfolioList: %v", err)
	}
	if len(list.Results) != 0 {
		t.Fatalf("Results = %d entries, want 0", len(list.Results))
	}
}

func TestPortfolioList_FlattenedEnvelopeFallback(t *testing.T) {
	// Some client/API versions omit the `data` wrapper entirely
	// (05-account-state.md warns the nesting varies); the client must still
	// parse the payload from the top level.
	srv, _ := serveFixture(t, http.StatusOK, "testdata/portfolio_list_flat.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	list, err := c.PortfolioList(context.Background(), "")
	if err != nil {
		t.Fatalf("PortfolioList: %v", err)
	}
	if len(list.Results) != 1 || list.Results[0].Symbol != "BBCA" {
		t.Fatalf("PortfolioList = %+v, want one BBCA result", list)
	}
}

func TestPortfolioList_NullDataIsAnErrorNotEmptyPortfolio(t *testing.T) {
	// This is the case the team is specifically guarding against: a
	// malformed 200 must not silently read as "you hold nothing".
	srv, _ := serveFixture(t, http.StatusOK, "testdata/portfolio_list_null_data.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	_, err := c.PortfolioList(context.Background(), "")
	if err == nil {
		t.Fatal("PortfolioList: got nil error for null data field, want ErrEmptyResponse")
	}
	if !errors.Is(err, ErrEmptyResponse) {
		t.Errorf("PortfolioList error = %v, want errors.Is(err, ErrEmptyResponse)", err)
	}
}

func TestPortfolioList_EmptyBodyIsAnError(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	_, err := c.PortfolioList(context.Background(), "")
	if !errors.Is(err, ErrEmptyResponse) {
		t.Errorf("PortfolioList error = %v, want errors.Is(err, ErrEmptyResponse)", err)
	}
}

func TestGet_401And403ReturnErrUnauthorized(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		srv, _ := serveFixture(t, status, "")
		c := New(srv.URL, &fakeTokenSource{token: "expired"}, srv.Client())

		_, err := c.PortfolioList(context.Background(), "")
		if !errors.Is(err, ErrUnauthorized) {
			t.Errorf("status %d: PortfolioList error = %v, want errors.Is(err, ErrUnauthorized)", status, err)
		}
		if strings.Contains(err.Error(), "expired") {
			t.Errorf("status %d: error %q leaked the token", status, err.Error())
		}
	}
}

func TestGet_OtherNon2xxReturnsAPIError(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusInternalServerError, "")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	_, err := c.PortfolioList(context.Background(), "")
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("PortfolioList error = %v (%T), want *APIError", err, err)
	}
	if apiErr.Status != http.StatusInternalServerError {
		t.Errorf("APIError.Status = %d, want 500", apiErr.Status)
	}
}

func TestGet_SendsBearerAuthorizationHeader(t *testing.T) {
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"data":{}}`))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, &fakeTokenSource{token: "secret-trading-token"}, srv.Client())
	_, _ = c.CashInfo(context.Background(), "BBCA")

	if want := "Bearer secret-trading-token"; gotAuth != want {
		t.Errorf("Authorization header = %q, want %q", gotAuth, want)
	}
}

func TestCashInfo_ParsesEnvelope(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "testdata/cash_info.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	info, err := c.CashInfo(context.Background(), "BBCA")
	if err != nil {
		t.Fatalf("CashInfo: %v", err)
	}
	if info.TradeBalance != 40000000 {
		t.Errorf("TradeBalance = %v, want 40000000", info.TradeBalance)
	}
	// Confirmed live: the margin block is present for every account, not
	// only margin-enabled ones — Status is the real "is this a margin
	// account" signal, not Margin's presence.
	if info.Margin == nil {
		t.Fatal("Margin = nil, want a populated block even for a non-margin account")
	}
	if info.Margin.Status != "MARGIN_ACCOUNT_STATUS_UNREGISTERED" {
		t.Errorf("Margin.Status = %q, want MARGIN_ACCOUNT_STATUS_UNREGISTERED", info.Margin.Status)
	}
}

func TestCashInfo_SendsStockCodeQueryParam(t *testing.T) {
	var gotQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query().Get("stock_code")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"data":{}}`))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())
	_, _ = c.CashInfo(context.Background(), "TLKM")

	if gotQuery != "TLKM" {
		t.Errorf("stock_code query param = %q, want TLKM", gotQuery)
	}
}

func TestWithdrawable_ParsesEnvelope(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "testdata/withdrawable.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	w, err := c.Withdrawable(context.Background())
	if err != nil {
		t.Fatalf("Withdrawable: %v", err)
	}
	if w.Balance.Withdrawable != 5000000 {
		t.Errorf("Balance.Withdrawable = %v, want 5000000", w.Balance.Withdrawable)
	}
	if len(w.SettlementSchedules) != 3 {
		t.Fatalf("SettlementSchedules = %d entries, want 3", len(w.SettlementSchedules))
	}
	if w.SettlementSchedules[2].Title != "T+2" {
		t.Errorf("SettlementSchedules[2].Title = %q, want T+2", w.SettlementSchedules[2].Title)
	}
}

func TestPortfolioSummary_ParsesEnvelope(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "testdata/portfolio_summary.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	summary, err := c.PortfolioSummary(context.Background())
	if err != nil {
		t.Fatalf("PortfolioSummary: %v", err)
	}
	if summary.Aggregated.Debt.Ratio != 93.55 {
		t.Errorf("Aggregated.Debt.Ratio = %v, want 93.55", summary.Aggregated.Debt.Ratio)
	}
}

// TestPortfolioList_LiveShapeExtraFieldsAndUnsettledSentinel decodes a
// fixture shaped after a real (synthetic-data) live response: top-level
// smart_order_info/transfer_info and per-position company/exercise/
// margin_info/trading_end/type fields this package doesn't model, plus the
// unsettled_until_at {1,1,1} sentinel. All of that must be ignored cleanly
// by the decode — see the live-account bug report this fixture was built
// from (unsettled_until_at was originally typed as a bare string).
func TestPortfolioList_LiveShapeExtraFieldsAndUnsettledSentinel(t *testing.T) {
	srv, _ := serveFixture(t, http.StatusOK, "testdata/portfolio_list_live_shape.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	list, err := c.PortfolioList(context.Background(), "")
	if err != nil {
		t.Fatalf("PortfolioList: %v", err)
	}
	if len(list.Results) != 2 {
		t.Fatalf("Results = %d entries, want 2", len(list.Results))
	}

	aaaa := list.Results[0]
	if !aaaa.Qty.UnsettledUntilAt.IsZero() {
		t.Errorf("AAAA UnsettledUntilAt.IsZero() = false, want true for the {1,1,1} sentinel")
	}
	if got := aaaa.Qty.UnsettledUntilAt.String(); got != "" {
		t.Errorf("AAAA UnsettledUntilAt.String() = %q, want \"\" (must not render as 0001-01-01)", got)
	}

	// ZZZZ is shaped after the resting-GTC-sell case found live (synthetic
	// numbers): available=0, unsettled=0, balance=2500. This is a normal
	// account state, not a broker inconsistency — QuantitiesPossible must
	// say so on the cheap, order-data-free check, and SettlementConsistent
	// must resolve once the resting sell's reservedSell is supplied, but
	// flag it as unresolved when it isn't (the conservative default with no
	// open-orders data).
	zzzz := list.Results[1]
	if !zzzz.Qty.QuantitiesPossible() {
		t.Error("ZZZZ QuantitiesPossible() = false, want true (available=0 and unsettled=0 are both <= balance=2500)")
	}
	if zzzz.Qty.SettlementConsistent(0) {
		t.Error("ZZZZ SettlementConsistent(0) = true, want false — without the resting sell's reservedSell, the identity doesn't close")
	}
	if !zzzz.Qty.SettlementConsistent(2500) {
		t.Error("ZZZZ SettlementConsistent(2500) = false, want true — a resting sell for the full balance explains the gap")
	}
}

func TestDate_StringAndIsZero(t *testing.T) {
	tests := []struct {
		name     string
		d        Date
		wantZero bool
		wantStr  string
	}{
		{"go zero value", Date{}, true, ""},
		{"api sentinel {1,1,1}", Date{Year: 1, Month: 1, Day: 1}, true, ""},
		{"real date", Date{Year: 2026, Month: 9, Day: 16}, false, "2026-09-16"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.d.IsZero(); got != tt.wantZero {
				t.Errorf("IsZero() = %v, want %v", got, tt.wantZero)
			}
			if got := tt.d.String(); got != tt.wantStr {
				t.Errorf("String() = %q, want %q", got, tt.wantStr)
			}
		})
	}
}

func TestNew_DefaultsHostAndHTTPClient(t *testing.T) {
	c := New("", &fakeTokenSource{token: "t"}, nil)
	if c.host != DefaultHost {
		t.Errorf("host = %q, want %q", c.host, DefaultHost)
	}
	if c.http == nil {
		t.Error("http client is nil, want http.DefaultClient")
	}
}
