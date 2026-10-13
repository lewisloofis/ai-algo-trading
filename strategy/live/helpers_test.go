package live

import (
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

// testAccNo is a synthetic sleeve account number used across tests that
// don't exercise anything accNo-specific — NewClient requires one, and no
// test should need to care what it is beyond "non-empty".
const testAccNo = "008488L"

// fakeTokenSource is a TokenSource that returns a fixed token and records
// whether it was ever asked for it.
type fakeTokenSource struct {
	token string
	asked atomic.Bool // the poller and a concurrent Cancel both read the token
}

func (f *fakeTokenSource) TradingToken() string {
	f.asked.Store(true)
	return f.token
}

// armedForTest returns an Arming that reports Armed() == true without going
// through the environment — Arming's field is unexported, so a same-package
// test can build one directly rather than mutating process-wide env vars
// for every test that just needs a working Client.
func armedForTest() Arming { return Arming{armed: true} }

// newTestClient builds a Client against srv, armed, failing the test on
// construction error rather than making every call site handle it.
func newTestClient(t *testing.T, srv *httptest.Server) *Client {
	t.Helper()
	c, err := NewClient(srv.URL, testAccNo, &fakeTokenSource{token: "test-trading-token"}, srv.Client(), armedForTest())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

// routeServer dispatches each request to a handler keyed by URL path,
// failing the test on any request to a path with no registered handler —
// tests should register exactly the endpoints they expect to be hit, so an
// unexpected call (e.g. Cancel firing when it shouldn't) is caught rather
// than silently 404ing.
func routeServer(t *testing.T, routes map[string]http.HandlerFunc) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fn, ok := routes[r.URL.Path]
		if !ok {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		fn(w, r)
	}))
	t.Cleanup(srv.Close)
	return srv
}

// jsonHandler returns an http.HandlerFunc that always replies with status
// and the literal body, for routes where a small inline response is
// clearer than a stateful one.
func jsonHandler(status int, body string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}
}

// countingHandler wraps fn, incrementing *count on every call — used to
// assert a route was (or, for the double-arm gate test, was NOT) ever hit.
func countingHandler(count *int, fn http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		*count++
		fn(w, r)
	}
}
