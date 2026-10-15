package livesession

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func touch(t *testing.T, p string) {
	t.Helper()
	if err := os.WriteFile(p, nil, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestNextStorePath_CountsOnlyThisDateAndStrategy(t *testing.T) {
	dir := t.TempDir()
	now := testNow()
	p, err := NextStorePath(dir, "roundtrip-goto", now)
	if err != nil || filepath.Base(p) != "2026-10-15-roundtrip-goto-a1.db" {
		t.Fatalf("first = %s, %v", p, err)
	}
	touch(t, filepath.Join(dir, "2026-10-15-roundtrip-goto-a1.db"))
	touch(t, filepath.Join(dir, "2026-10-15-roundtrip-goto-a3.db"))
	touch(t, filepath.Join(dir, "2026-10-14-roundtrip-goto-a9.db"))  // other day
	touch(t, filepath.Join(dir, "2026-10-15-other-a7.db"))           // other strategy
	touch(t, filepath.Join(dir, "2026-10-15-roundtrip-goto-a2.txt")) // not a store
	p, err = NextStorePath(dir, "roundtrip-goto", now)
	if err != nil || filepath.Base(p) != "2026-10-15-roundtrip-goto-a4.db" {
		t.Fatalf("next = %s, %v", p, err)
	}
	if _, err := os.Stat(p); err == nil {
		t.Error("NextStorePath must not create the file")
	}
}

func TestNextStorePath_SanitisesStrategyName(t *testing.T) {
	dir := t.TempDir()
	p, err := NextStorePath(dir, "My Strat/../x", testNow())
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Dir(p) != dir || strings.Contains(filepath.Base(p), "/") || !strings.HasSuffix(p, "-a1.db") {
		t.Fatalf("p = %s", p)
	}
}

func TestPriorStoreGlobs_OperatorGlobsPlusAttempts(t *testing.T) {
	g := PriorStoreGlobs(Config{RunsDir: "runs", PriorStores: []string{"runs/2026-10-14-roundtrip-*.db"}}, "roundtrip-goto")
	if len(g) != 2 || g[0] != "runs/2026-10-14-roundtrip-*.db" || g[1] != "runs/*-roundtrip-goto-a[0-9]*.db" {
		t.Fatalf("globs = %v", g)
	}
}

func TestReadStoreSummary(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "s.db")
	writeStore(t, p, storeSpec{hash: "abc", orders: 2, fills: 1, buyLots: 1, rejects: []string{"risk: kill switch is tripped", "carina: nope"}})
	s, err := ReadStoreSummary(p)
	if err != nil {
		t.Fatal(err)
	}
	if !s.HasRun || s.BinaryHash != "abc" || s.Orders != 2 || s.Fills != 1 || s.FeesIDR != 4 || s.Rejects != 2 || s.KillRejects != 1 {
		t.Errorf("summary = %+v", s)
	}
	missing, err := ReadStoreSummary(filepath.Join(dir, "absent.db"))
	if err != nil || missing.Exists {
		t.Errorf("missing store = %+v, %v", missing, err)
	}
}

func TestRetryCommand_ReplacesExistingRetry(t *testing.T) {
	got := RetryCommand([]string{"live-session", "--retry", "2", "--account", "A", "--retry=3"}, 4)
	if got != "live-session --account A --retry 4" {
		t.Errorf("got %q", got)
	}
}
