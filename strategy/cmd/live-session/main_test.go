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

func TestRun_BadFlagExits2(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"live-session", "--nope"}, &out, &errb); code != 2 {
		t.Fatalf("exit %d, want 2", code)
	}
}
