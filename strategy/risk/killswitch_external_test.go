package risk

import (
	"context"
	"errors"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

// This file targets plan 18 §10's box "Kill switch trips from outside the
// process and is observed to stop orders."
//
// The mechanism (killswitch.go) is a file check: RiskSink.Submit calls
// killSwitch.Check(), which os.Stat()s a configured path on every Submit.
// killSwitch itself never writes that file — the only path that creates it
// is whatever external actor is expected to in production (an operator, a
// systemd/ops script, an SSM-triggered action per plan 18 §5 B7, which is
// not yet built). structural_test.go additionally guarantees this package
// makes no network calls, so the file is genuinely the only channel in.
//
// killswitch_test.go already covers the killSwitch type's file-check logic
// directly (absence, presence, removal-does-not-untrip, unreadable-fails-
// closed) via os.WriteFile from the test goroutine. That proves the
// mechanism, but writing the file from the same test process leaves a
// literal reading of "from outside the process" unproven. This test closes
// that gap: it spawns a real, separate OS process (not a goroutine, not a
// second call from this binary) to create the kill file — the same shape
// as an operator running `touch KILL` on the box — and then drives a
// genuine RiskSink.Submit, in this process, to observe that the order the
// kill switch is supposed to stop is in fact stopped, and never reaches the
// inner sink.
func TestKillSwitch_TrippedByExternalProcess_StopsOrders(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test spawns a POSIX shell to simulate an external process dropping the kill file")
	}

	dir := t.TempDir()
	killPath := filepath.Join(dir, "KILL")

	inner := &recordingSink{}
	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{pnl: 0}, killPath, nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}

	// Before the external trip: an ordinary order is admitted normally.
	if _, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1)); err != nil {
		t.Fatalf("expected admit before the kill file exists, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("expected 1 order at the inner sink before the trip, got %d", inner.count())
	}

	// Trip it from a genuinely separate OS process — not this test's
	// goroutine, not a method call on sink or killSwitch. This is the
	// out-of-process actor the box asks for.
	cmd := exec.Command("/bin/sh", "-c", `printf 'stop' > "$1"`, "sh", killPath)
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("external process failed to create the kill file: %v, output: %s", err, out)
	}

	// Observed from this process: the next order is stopped, and it never
	// reaches the inner sink.
	_, err = sink.Submit(context.Background(), intent("TLKM", "buy", 10_000, 1))
	if !errors.Is(err, ErrKillSwitchTripped) {
		t.Fatalf("expected ErrKillSwitchTripped once the external process dropped the kill file, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("order submitted after the external trip reached the inner sink: count=%d, want 1", inner.count())
	}

	// And it is observable without attempting an order too, via the same
	// ops-facing surface Snapshot()/status.json use.
	if tripped, reason := sink.KillSwitchTripped(); !tripped || reason == "" {
		t.Fatalf("expected KillSwitchTripped() to report tripped with a reason, got tripped=%v reason=%q", tripped, reason)
	}

	// A third order, well after the fact, stays stopped too — no
	// auto-recovery, matching the file-level guarantee killswitch_test.go
	// already pins (TestKillSwitch_ExternalFileTrips_AndStaysTrippedAfterRemoval).
	_, err = sink.Submit(context.Background(), intent("BMRI", "buy", 10_000, 1))
	if !errors.Is(err, ErrKillSwitchTripped) {
		t.Fatalf("expected the kill switch to remain tripped, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("expected inner sink to still show only the 1 pre-trip order, got %d", inner.count())
	}
}

// Non-vacuity: confirmed by hand by temporarily changing killSwitch.Check
// (killswitch.go) to unconditionally `return false, ""`, re-running this
// test with `go test -run TestKillSwitch_TrippedByExternalProcess_StopsOrders
// ./risk/...`, and observing it FAIL at the ErrKillSwitchTripped assertion
// (the order after the external trip was admitted; inner.count() reached 2).
// Reverted with `git checkout -- strategy/risk/killswitch.go` (clean/tracked
// at HEAD at the time; `git diff --stat` confirmed no residual change), then
// re-ran and confirmed PASS.
