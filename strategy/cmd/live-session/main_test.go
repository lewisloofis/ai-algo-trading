package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestRun_RefusesWithoutRequiredFlagsAndExits2(t *testing.T) {
	t.Setenv("SB_TRADING_ACCESS_TOKEN", "")
	t.Setenv("ALGO_LIVE", "")
	t.Setenv("ALGO_CONFIRM", "")
	var out, errb bytes.Buffer
	code := run([]string{"live-session", "--sink", "live", "--account", "X", "--strategy-run-bin", "/nonexistent"}, &out, &errb)
	if code != 2 {
		t.Fatalf("exit %d, want 2", code)
	}
	for _, want := range []string{"SB_TRADING_ACCESS_TOKEN is not set", "not armed", "--rebalance-at is required"} {
		if !strings.Contains(errb.String(), want) {
			t.Errorf("stderr missing %q:\n%s", want, errb.String())
		}
	}
}

// TestRun_ParsesTheUnattendedRunnersLiveArgv pins the exact flag shape the Python runner
// (research/runner/guard.py:_live_argv) builds. With no token and no arming it must be refused
// for THOSE reasons, never for an unknown or mis-typed flag, and it must not reach the network.
func TestRun_ParsesTheUnattendedRunnersLiveArgv(t *testing.T) {
	t.Setenv("SB_TRADING_ACCESS_TOKEN", "")
	t.Setenv("ALGO_LIVE", "")
	t.Setenv("ALGO_CONFIRM", "")
	argv := []string{"live-session",
		"--sink", "live", "--source", "live", "--account", "SYNTH-ACC1", "--equity-idr", "500000",
		"--strategy", "rebalance", "--target-portfolio", "/nonexistent/target.json",
		"--symbols", "BBBB", "--rebalance-at", "09:30", "--runs-dir", t.TempDir(),
		"--max-restarts", "0", "--max-buy-notional-idr", "500000", "--strategy-run-bin", "/nonexistent",
		"--",
		"--risk-per-order-fraction", "1", "--risk-max-position-fraction", "1",
		"--risk-max-gross-fraction", "1", "--risk-per-day-fraction", "2"}
	var out, errb bytes.Buffer
	if code := run(argv, &out, &errb); code != 2 {
		t.Fatalf("exit %d, want 2\n%s", code, errb.String())
	}
	if strings.Contains(errb.String(), "flag provided but not defined") || strings.Contains(errb.String(), "invalid value") {
		t.Fatalf("the runner's argv does not parse:\n%s", errb.String())
	}
	for _, want := range []string{"SB_TRADING_ACCESS_TOKEN is not set", "not armed"} {
		if !strings.Contains(errb.String(), want) {
			t.Errorf("stderr missing %q:\n%s", want, errb.String())
		}
	}
}

func TestRun_BadFlagExits2(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"live-session", "--nope"}, &out, &errb); code != 2 {
		t.Fatalf("exit %d, want 2", code)
	}
}
