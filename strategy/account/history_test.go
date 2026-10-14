package account

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func readFixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile("testdata/" + name)
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	return b
}

func TestParseHistoryPage_TradesCashAndUnknowns(t *testing.T) {
	p, err := ParseHistoryPage(readFixture(t, "history_page1.json"))
	if err != nil {
		t.Fatalf("ParseHistoryPage: %v", err)
	}
	if p.MaxPage != 2 {
		t.Errorf("MaxPage = %d, want 2", p.MaxPage)
	}
	if len(p.Trades) != 3 {
		t.Fatalf("trades = %d, want 3 (BUY/SELL with status MATCH only): %+v", len(p.Trades), p.Trades)
	}
	// Document order is preserved: newest first, as the broker returns it.
	buy28 := p.Trades[0]
	if buy28.ID != "900000003" || buy28.Side != "buy" || buy28.Symbol != "GOTO" || buy28.Lots != 1 ||
		buy28.PriceIDR != 28 || buy28.FeeIDR != 4.2 || buy28.AmountIDR != 2800 || buy28.NetAmountIDR != 2804.2 {
		t.Errorf("buy @28 parsed wrong: %+v", buy28)
	}
	sell := p.Trades[1]
	if sell.Side != "sell" || sell.PriceIDR != 26 || sell.FeeIDR != 6.5 {
		t.Errorf("sell parsed wrong: %+v", sell)
	}
	// Times are WIB (UTC+7).
	if got := buy28.Time.UTC().Format(time.RFC3339); got != "2026-10-14T08:38:00Z" {
		t.Errorf("buy time UTC = %s, want 2026-10-14T08:38:00Z (15:38 WIB)", got)
	}
	if got := buy28.Date(); got != "2026-10-14" {
		t.Errorf("Date() = %s", got)
	}

	if len(p.CashEvents) != 1 || p.CashEvents[0].AmountIDR != 15000000 || p.CashEvents[0].Command != "INTRA_MOVE_CASH_INCREASE" {
		t.Errorf("cash events = %+v", p.CashEvents)
	}
	// A non-MATCH BUY is not a trade; it is counted, not dropped silently.
	if p.Skipped["BUY(CANCELLED)"] != 1 {
		t.Errorf("skipped = %v, want BUY(CANCELLED)=1", p.Skipped)
	}
	// An unknown command is counted and never breaks parsing of the page.
	if p.Unknown["RIGHTS_ISSUE_UNKNOWN"] != 1 {
		t.Errorf("unknown = %v, want RIGHTS_ISSUE_UNKNOWN=1", p.Unknown)
	}
}

// TestParseHistoryPage_NeverKeepsAccountDetails is the redaction proof:
// move_stock_cash carries account numbers and names. They must be stripped at
// parse time so nothing downstream can persist or log them.
func TestParseHistoryPage_NeverKeepsAccountDetails(t *testing.T) {
	p, err := ParseHistoryPage(readFixture(t, "history_page1.json"))
	if err != nil {
		t.Fatal(err)
	}
	out, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	dump := string(out) + " " + strings.Join(allStrings(p), " ")
	for _, secret := range []string{"SYNTH-ACC-SOURCE-111", "SYNTH-ACC-DEST-222", "SYNTH OWNER NAME", "move_stock_cash", "acc_no"} {
		if strings.Contains(dump, secret) {
			t.Errorf("parsed page still carries %q", secret)
		}
	}
	// And the Go types have no field that could hold them.
	for _, v := range []any{HistoryTrade{}, CashEvent{}} {
		b, _ := json.Marshal(v)
		if strings.Contains(strings.ToLower(string(b)), "acc") || strings.Contains(string(b), "name") {
			t.Errorf("%T serialises an account-ish field: %s", v, b)
		}
	}
}

func allStrings(p HistoryPage) []string {
	var out []string
	for _, c := range p.CashEvents {
		out = append(out, c.ID, c.Command, c.Status)
	}
	for _, tr := range p.Trades {
		out = append(out, tr.ID, tr.Symbol, tr.Side)
	}
	return out
}

func TestParseHistoryPage_InvalidTradeRowIsAnError(t *testing.T) {
	_, err := ParseHistoryPage(readFixture(t, "history_bad_row.json"))
	if err == nil {
		t.Fatal("price 0 on a MATCH trade must be rejected, got nil")
	}
	if !strings.Contains(err.Error(), "price") {
		t.Errorf("error should name the field: %v", err)
	}
}

func TestParseHistoryPage_Validation(t *testing.T) {
	wrap := func(row string) []byte {
		return []byte(`{"data":{"history":[{"date":"Oct 2026","history_list":[` + row + `]}],"meta":{"max_page":1}}}`)
	}
	good := `"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":100,"amount":2800,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"`
	if _, err := ParseHistoryPage(wrap(`{` + good + `}`)); err != nil {
		t.Fatalf("good row rejected: %v", err)
	}
	bad := map[string]string{
		"fractional lot":    `{"command":"BUY","symbol":"GOTO","price":28,"lot":1.5,"shares":150,"amount":4200,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"}`,
		"zero lot":          `{"command":"BUY","symbol":"GOTO","price":28,"lot":0,"shares":0,"amount":0,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"}`,
		"negative fee":      `{"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":100,"amount":2800,"fee":-1,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"}`,
		"missing id":        `{"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":100,"amount":2800,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00"}`,
		"bad date":          `{"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":100,"amount":2800,"fee":4.2,"status":"MATCH","date":"yesterday","time":"15:38:00","id":"1"}`,
		"bad time":          `{"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":100,"amount":2800,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"3pm","id":"1"}`,
		"empty symbol":      `{"command":"BUY","symbol":"","price":28,"lot":1,"shares":100,"amount":2800,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"}`,
		"shares != lot*100": `{"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":50,"amount":2800,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"}`,
		"amount != px*shr":  `{"command":"BUY","symbol":"GOTO","price":28,"lot":1,"shares":100,"amount":9999,"fee":4.2,"status":"MATCH","date":"14 Oct 2026","time":"15:38:00","id":"1"}`,
	}
	for name, row := range bad {
		if _, err := ParseHistoryPage(wrap(row)); err == nil {
			t.Errorf("%s: want error, got nil", name)
		}
	}
}

func TestParseHistoryPage_NotJSONOrEmpty(t *testing.T) {
	for _, body := range []string{``, `not json`, `{"data":null}`, `{}`} {
		if _, err := ParseHistoryPage([]byte(body)); err == nil {
			t.Errorf("body %q: want error", body)
		}
	}
}

func TestClientHistory_PaginatesByMaxPage(t *testing.T) {
	var pages []string
	var firstQuery string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/history" || r.Method != http.MethodGet {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
		}
		page := r.URL.Query().Get("page")
		pages = append(pages, page)
		if firstQuery == "" {
			firstQuery = r.URL.RawQuery
		}
		name := "history_page1.json"
		if page == "2" {
			name = "history_page2.json"
		}
		w.Write(readFixture(t, name))
	}))
	defer srv.Close()

	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "tok"}, srv.Client())
	h, err := c.History(context.Background(), DefaultHistoryQuery())
	if err != nil {
		t.Fatalf("History: %v", err)
	}
	if strings.Join(pages, ",") != "1,2" {
		t.Errorf("pages requested = %v, want 1,2", pages)
	}
	if len(h.Trades) != 4 {
		t.Errorf("trades = %d, want 4 across both pages", len(h.Trades))
	}
	for _, want := range []string{"limit=100", "period=3+month", "page=1", "start=", "end=", "action=", "keyword="} {
		if !strings.Contains(firstQuery, want) {
			t.Errorf("query %q missing %q", firstQuery, want)
		}
	}
	if h.Unknown["RIGHTS_ISSUE_UNKNOWN"] != 1 || h.Skipped["BUY(CANCELLED)"] != 1 || len(h.CashEvents) != 1 {
		t.Errorf("aggregates wrong: unknown=%v skipped=%v cash=%d", h.Unknown, h.Skipped, len(h.CashEvents))
	}
}

func TestClientHistory_PageCapStopsRunawayPagination(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"data":{"history":[],"meta":{"max_page":100000}}}`))
	}))
	defer srv.Close()
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "tok"}, srv.Client())
	if _, err := c.History(context.Background(), DefaultHistoryQuery()); err == nil {
		t.Fatal("absurd max_page must be an error, not an unbounded loop")
	}
}

func TestClientHistory_Unauthorized(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(401) }))
	defer srv.Close()
	c := newTestClient(t, srv.URL, &fakeTokenSource{token: "tok"}, srv.Client())
	if _, err := c.History(context.Background(), DefaultHistoryQuery()); err == nil {
		t.Fatal("401 must surface as an error")
	}
}
