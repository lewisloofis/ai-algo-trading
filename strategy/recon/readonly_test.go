package recon

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// forbiddenInReconPaths are the things whose presence in the reconciliation
// code would give it a way to place an order: the live-order package, an HTTP
// client of its own, or a write verb. The recon path must be structurally
// unable to trade, the same guarantee strategy/account gives (its own
// readonly_test.go); this extends it to this package and to the
// account-recon command.
var forbiddenInReconPaths = []string{
	`"strategy/live"`, `"strategy/live/`,
	`"net/http"`,
	"http.NewRequest",
	"http.MethodPost", "http.MethodPut", "http.MethodPatch", "http.MethodDelete",
	`"POST"`, `"PUT"`, `"PATCH"`, `"DELETE"`,
	"PlaceOrder", "CancelOrder", "AmendOrder",
}

func scanNonTestGo(t *testing.T, dir string, allowed ...string) {
	t.Helper()
	files, err := filepath.Glob(filepath.Join(dir, "*.go"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no .go files in %s (err %v) — the test is not scanning the right place", dir, err)
	}
	for _, f := range files {
		if strings.HasSuffix(f, "_test.go") {
			continue
		}
		data, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, bad := range forbiddenInReconPaths {
			skip := false
			for _, a := range allowed {
				if a == bad {
					skip = true
				}
			}
			if !skip && strings.Contains(string(data), bad) {
				t.Errorf("%s contains %q: the reconciliation path must not be able to place orders", f, bad)
			}
		}
	}
}

func TestReconPackageCannotPlaceOrders(t *testing.T) { scanNonTestGo(t, ".") }

// The account-recon command may construct an http.Client for the read-only
// account.Client, nothing more.
func TestAccountReconCommandCannotPlaceOrders(t *testing.T) {
	scanNonTestGo(t, filepath.Join("..", "cmd", "account-recon"), `"net/http"`)
}
