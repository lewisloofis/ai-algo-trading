package market

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"collector/calendar"
)

// Default bodies. Shapes are transcribed from the protos and clients cited
// in doc.go: reference 06 §2.1/§2.2 for the two market-time routes,
// sb-web/src/global/Securities/types/api/stockTradable.ts for tradability,
// and negoengine order_book entity v1 for the band.
const (
	bodyMarketOpen   = `{"message":"ok","data":{"market":{"status":"STATUS_OPEN"},"iepiev_regular":{"status":"STATUS_OPEN"},"iepiev_fca":{"status":"STATUS_CLOSE"}}}`
	bodyMarketClosed = `{"message":"ok","data":{"market":{"status":"STATUS_CLOSE"},"iepiev_regular":{"status":"STATUS_CLOSE"},"iepiev_fca":{"status":"STATUS_CLOSE"}}}`
	bodySession1     = `{"message":"ok","data":{"datetime":"2026-09-16 10:00:00","detail":{"regular":{"session":1,"state_name":"STATE_NAME_SESSION_1","state_start_time":"09:00:00","state_end_time":"11:59:59"},"fca":{"session":1,"state_name":"STATE_NAME_SESSION_1"}}}}`
	bodySessionBreak = `{"message":"ok","data":{"datetime":"2026-09-16 12:00:00","detail":{"regular":{"session":1,"state_name":"STATE_NAME_BREAK"},"fca":{"session":1,"state_name":"STATE_NAME_BREAK"}}}}`
	bodyTradableYes  = `{"message":"ok","data":[{"stock_code":"BBCA","is_tradable":true,"is_sharia_tradable":true,"is_margin_tradable":true}]}`
	bodyTradableNo   = `{"message":"ok","data":[{"stock_code":"BBCA","is_tradable":false,"is_sharia_tradable":false,"is_margin_tradable":false}]}`
	// Shaped like the live response measured 2026-09-16 in SESSION_1: bare
	// numeric ARA/ARB, and max_requestable_* WIDER than the hard band (BBCA
	// came back above=7675 below=5450 max_requestable_above=13225
	// max_requestable_below=3360). The gate deliberately reads only
	// above/below, the hard auto-reject bounds. The string encoding proto3
	// JSON also permits is covered separately, in
	// TestAutoRejectPriceParsesStringAndNumericEncodings.
	bodyBand9001100 = `{"message":"ok","data":{"order_book":{"asset_code":"BBCA"},"auto_reject_price":{"above":1100,"below":900,"max_requestable_above":1500,"max_requestable_below":600}}}`
)

// fakeStack is a pair of httptest servers standing in for Exodus and
// Carina. Two servers rather than one, so that "the right token went to the
// right host" is a property a test can actually assert — pointing both
// hosts at a single server would make that check vacuous.
type fakeStack struct {
	exodus *httptest.Server
	carina *httptest.Server

	mu     sync.Mutex
	bodies map[string]string
	status map[string]int
	hits   map[string]int
	auth   map[string]string
	agent  map[string]string
}

func newFakeStack(t *testing.T) *fakeStack {
	t.Helper()
	f := &fakeStack{
		bodies: map[string]string{
			pathMarketTime:    bodyMarketOpen,
			pathMarketSession: bodySession1,
			pathStockTradable: bodyTradableYes,
			pathOrderBook:     bodyBand9001100,
		},
		status: map[string]int{},
		hits:   map[string]int{},
		auth:   map[string]string{},
		agent:  map[string]string{},
	}
	f.exodus = httptest.NewServer(http.HandlerFunc(f.serve))
	f.carina = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.exodus.Close)
	t.Cleanup(f.carina.Close)
	return f
}

// route maps a request path to the fixture key it is served from. The
// order-book route carries the symbol in the path, so it matches by prefix.
func route(path string) string {
	if strings.HasPrefix(path, pathOrderBook) {
		return pathOrderBook
	}
	return path
}

func (f *fakeStack) serve(w http.ResponseWriter, r *http.Request) {
	key := route(r.URL.Path)
	f.mu.Lock()
	f.hits[key]++
	f.auth[key] = r.Header.Get("Authorization")
	f.agent[key] = r.Header.Get("User-Agent")
	status, hasStatus := f.status[key]
	body, hasBody := f.bodies[key]
	f.mu.Unlock()

	if hasStatus && status != http.StatusOK {
		w.WriteHeader(status)
		_, _ = w.Write([]byte(`{"error":"injected"}`))
		return
	}
	if !hasBody {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(body))
}

func (f *fakeStack) setBody(path, body string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.bodies[path] = body
}

func (f *fakeStack) setStatus(path string, status int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.status[path] = status
}

func (f *fakeStack) hitCount(path string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.hits[path]
}

func (f *fakeStack) authFor(path string) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.auth[path]
}

func (f *fakeStack) agentFor(path string) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.agent[path]
}

const (
	testAccessToken  = "main-session-token"
	testTradingToken = "trading-session-token"
)

func (f *fakeStack) client(t *testing.T) *Client {
	t.Helper()
	c, err := NewClient(StaticTokens{Access: testAccessToken, Trading: testTradingToken}, ClientOptions{
		ExodusHost: f.exodus.URL,
		CarinaHost: f.carina.URL,
		HTTPClient: f.exodus.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

// fakeClock is a manually advanced clock, so TTL behaviour is tested by
// moving time rather than by sleeping.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFakeClock(at time.Time) *fakeClock { return &fakeClock{t: at} }

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

// tradingNoon is a Wednesday that the embedded IDX calendar agrees is a
// trading day, at 12:00 WIB. Tests that care about the holiday field pick
// their own instant.
var tradingNoon = time.Date(2026, 9, 16, 12, 0, 0, 0, wib)

// saturdayNoon is the weekend counterpart, for tests that need the calendar
// to refuse the day.
var saturdayNoon = time.Date(2026, 9, 19, 12, 0, 0, 0, wib)

func loadCalendar(t *testing.T) *calendar.Calendar {
	t.Helper()
	cal, err := calendar.Load()
	if err != nil {
		t.Fatalf("calendar.Load: %v", err)
	}
	return cal
}

func newTestGate(t *testing.T, f *fakeStack, clock *fakeClock) *Gate {
	t.Helper()
	g, err := NewGate(loadCalendar(t), f.client(t), GateOptions{Clock: clock.Now})
	if err != nil {
		t.Fatalf("NewGate: %v", err)
	}
	return g
}
