package live

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"strategy/engine"
)

// TestTradingTokenReadInExactlyOnePlace fails the build if
// TokenSource.TradingToken() is ever called from more than one place in
// this package's non-test source. client.go's do() is that one place — it
// reads the token solely to set the Authorization header. This is the
// static half of the "no token leak" guarantee: if there is only one call
// site, and that call site never assigns the result to anything but the
// header, no other code path in this package can put a token into a log
// line or an error string at all. Mirrors
// strategy/account/readonly_test.go's structural check.
func TestTradingTokenReadInExactlyOnePlace(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatalf("glob *.go: %v", err)
	}
	if len(files) == 0 {
		t.Fatal("no .go files found — test is not scanning the right directory")
	}

	callSites := 0
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		data, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("read %s: %v", f, err)
		}
		callSites += strings.Count(string(data), ".TradingToken()")
	}
	if callSites != 1 {
		t.Errorf("found %d call(s) to TradingToken() across non-test files, want exactly 1 (in client.go's do())", callSites)
	}
}

// TestNoTokenLeak_AcrossFailureScenarios is the runtime half: it constructs
// a Client/Sink with a distinctive token that never appears in any server
// response body it crafts, drives a battery of failure paths (401, a
// generic 500, a classified duplicate/suspended/invalid error, and a
// Submit that fails outright), and asserts the token substring never shows
// up in a returned error string or a captured Logf line. Since nothing in
// this test's server responses mentions the token, any appearance in the
// output could only have come from this package's own code.
func TestNoTokenLeak_AcrossFailureScenarios(t *testing.T) {
	const secretToken = "sekuritas-trading-token-do-not-log-me"

	var logs []string
	logf := func(format string, args ...any) {
		logs = append(logs, fmt.Sprintf(format, args...))
	}

	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy:    jsonHandler(http.StatusConflict, `{"message":"ErrorDuplicateOrder"}`),
		pathList:   jsonHandler(http.StatusUnauthorized, `{"message":"unauthorized"}`),
		pathCancel: jsonHandler(http.StatusInternalServerError, `{"message":"boom"}`),
	})
	c, err := NewClient(srv.URL, testAccNo, &fakeTokenSource{token: secretToken}, srv.Client(), armedForTest())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	s, err := NewSink(Options{Client: c, Logf: logf})
	if err != nil {
		t.Fatalf("NewSink: %v", err)
	}
	ctx := context.Background()

	var outputs []string

	// Submit: Place returns a duplicate, and the subsequent List() (used to
	// resolve it) 401s — an error path that touches both a classified
	// sentinel and ErrUnauthorized.
	if _, err := s.Submit(ctx, testIntent("rebalance-BBCA-1", 1)); err != nil {
		outputs = append(outputs, err.Error())
	} else {
		t.Fatal("Submit: got nil error, want the List() 401 to surface via resolveDuplicate")
	}

	// A poll tick where List() 401s.
	s.pollOnce(ctx)

	// A Cancel that hits a generic 500.
	s.mu.Lock()
	s.orders["rebalance-BBCA-1"] = &trackedOrder{Order: Order{ClientOrderID: "rebalance-BBCA-1", BrokerOrderID: "1", State: StateOpen}}
	s.mu.Unlock()
	if err := s.Cancel(ctx, engine.OrderID("rebalance-BBCA-1")); err != nil {
		outputs = append(outputs, err.Error())
	}

	outputs = append(outputs, logs...)
	for _, out := range outputs {
		if strings.Contains(out, secretToken) {
			t.Errorf("token leaked into output: %q", out)
		}
	}
}
