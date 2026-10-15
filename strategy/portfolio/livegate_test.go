package portfolio

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeSidecar(t *testing.T, dir, body string) string {
	t.Helper()
	target := filepath.Join(dir, "target_x_2026-10-16.json")
	if err := os.WriteFile(target, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if body != "" {
		if err := os.WriteFile(filepath.Join(dir, "target_x_2026-10-16.signal.json"), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return target
}

func TestRequireLiveCandidate(t *testing.T) {
	cases := []struct {
		name    string
		sidecar string
		wantErr string // "" = allowed
	}{
		{"no sidecar (hand-written target) is allowed", "", ""},
		{"candidate is allowed", `{"spec_status":"candidate"}`, ""},
		{"paper is refused", `{"spec_status":"paper"}`, `"paper"`},
		{"retired is refused", `{"spec_status":"retired"}`, `"retired"`},
		{"example is refused", `{"spec_status":"example"}`, `"example"`},
		{"sidecar without a status fails closed", `{"strategy_id":"x"}`, "no spec_status"},
		{"unparsable sidecar fails closed", `not json`, "unreadable"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			target := writeSidecar(t, t.TempDir(), c.sidecar)
			err := RequireLiveCandidate(target)
			if c.wantErr == "" {
				if err != nil {
					t.Fatalf("err = %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), c.wantErr) {
				t.Fatalf("err = %v, want containing %q", err, c.wantErr)
			}
		})
	}
}

func TestSidecarPath(t *testing.T) {
	if got := SidecarPath("/r/target_a_2026-10-16.json"); got != "/r/target_a_2026-10-16.signal.json" {
		t.Errorf("SidecarPath = %s", got)
	}
}
