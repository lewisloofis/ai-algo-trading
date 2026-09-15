package risk

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestPackageMakesNoNetworkCalls fails the build if any non-test source file
// in this package ever mentions an HTTP method token, imports net/http, or
// calls http.NewRequest*. This package decorates an OrderSink and must never
// place an order or make a network call itself (B4 supplies the live sink;
// this layer only decides allow/deny) — "never calls out" needs to be
// enforced by a test, not left as a comment. Modeled on
// strategy/account/readonly_test.go, but stricter: that package is
// GET-only and permits exactly one http.NewRequestWithContext call; this
// package permits none at all.
func TestPackageMakesNoNetworkCalls(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatalf("glob *.go: %v", err)
	}
	if len(files) == 0 {
		t.Fatal("no .go files found — test is not scanning the right directory")
	}

	forbidden := []string{
		"net/http",
		"http.NewRequest", // catches NewRequest and NewRequestWithContext
		"http.MethodGet", "http.MethodPost", "http.MethodPut", "http.MethodPatch", "http.MethodDelete",
		`"GET"`, `"POST"`, `"PUT"`, `"PATCH"`, `"DELETE"`,
		"http.Get(", "http.Post(", "http.PostForm(", "http.Client", "http.DefaultClient",
	}

	scanned := 0
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		scanned++
		data, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("read %s: %v", f, err)
		}
		text := string(data)

		for _, bad := range forbidden {
			if strings.Contains(text, bad) {
				t.Errorf("%s: contains forbidden token %q — this package must make no network calls at all", f, bad)
			}
		}
	}
	if scanned == 0 {
		t.Fatal("no non-test .go files found — test is not scanning the right directory")
	}
}
