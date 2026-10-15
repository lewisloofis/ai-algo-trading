package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCheckTradingToken(t *testing.T) {
	cases := []struct {
		name      string
		sink      string
		riskGated bool
		token     string
		wantErr   bool
	}{
		{"live sink needs the token", "live", true, "", true},
		{"live sink with the token", "live", true, "x", false},
		{"paper+live source needs no trading token", "paper", true, "", false},
		{"paper+replay needs none", "paper", false, "", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := checkTradingToken(c.sink, c.riskGated, c.token)
			if (err != nil) != c.wantErr {
				t.Fatalf("err = %v, wantErr %v", err, c.wantErr)
			}
		})
	}
}

// The session-only gate is for a paper sink without a trading token only. A
// live sink never gets it, even with an empty token (it is refused earlier).
func TestSessionOnlyGate(t *testing.T) {
	if !sessionOnlyGate("paper", "") {
		t.Error("paper without a trading token must use the session-only gate")
	}
	if sessionOnlyGate("paper", "tok") {
		t.Error("paper WITH a trading token keeps the full gate")
	}
	if sessionOnlyGate("live", "") {
		t.Error("a live sink must never use the session-only gate")
	}
}

func TestRefuseNonCandidateTargetForLiveSink(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "target_x_2026-10-16.json")
	os.WriteFile(target, []byte("{}"), 0o644)
	os.WriteFile(filepath.Join(dir, "target_x_2026-10-16.signal.json"), []byte(`{"spec_status":"paper"}`), 0o644)
	if err := requireLiveTarget("live", target); err == nil || !strings.Contains(err.Error(), "paper") {
		t.Fatalf("live sink + paper-status target: err = %v", err)
	}
	if err := requireLiveTarget("paper", target); err != nil {
		t.Fatalf("paper sink must accept a paper target: %v", err)
	}
	if err := requireLiveTarget("live", ""); err != nil {
		t.Fatalf("no target (orb) must pass: %v", err)
	}
}
