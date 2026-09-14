package account

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestPackageIsReadOnly fails if any non-test source file in this package
// mentions an HTTP method other than GET. This package talks to a live
// brokerage account, so "no write path exists" needs to be enforced by a
// test, not left as a convention — see client.go's get().
func TestPackageIsReadOnly(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatalf("glob *.go: %v", err)
	}
	if len(files) == 0 {
		t.Fatal("no .go files found — test is not scanning the right directory")
	}

	// Any of these appearing in a non-test source file means a write path
	// has been introduced. Covers both the http.MethodX / literal-verb form
	// or c.http.Do(req with a non-GET method) AND the http.Post/PostForm
	// convenience functions, which use neither a "POST" method constant nor
	// http.NewRequestWithContext and so would otherwise slip past both this
	// list and the single-request-builder count below.
	forbidden := []string{
		"http.MethodPost", "http.MethodPut", "http.MethodPatch", "http.MethodDelete",
		`"POST"`, `"PUT"`, `"PATCH"`, `"DELETE"`,
		"http.Post(", "http.PostForm(", ".Post(", ".PostForm(",
	}

	requestBuilders := 0
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		data, err := os.ReadFile(f)
		if err != nil {
			t.Fatalf("read %s: %v", f, err)
		}
		text := string(data)

		for _, bad := range forbidden {
			if strings.Contains(text, bad) {
				t.Errorf("%s: contains forbidden HTTP method token %q — this package must be GET-only", f, bad)
			}
		}
		requestBuilders += strings.Count(text, "http.NewRequestWithContext(")
	}

	// The package must have exactly one place that builds an HTTP request —
	// client.go's get(). More than one means a second request path was
	// added outside the reviewed one.
	if requestBuilders != 1 {
		t.Errorf("found %d call(s) to http.NewRequestWithContext across non-test files, want exactly 1 (in get())", requestBuilders)
	}
}
