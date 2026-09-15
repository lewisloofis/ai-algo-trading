package risk

import (
	"os"
	"path/filepath"
	"testing"
)

func TestKillSwitch_TripsInProcess_AndFirstReasonSticks(t *testing.T) {
	ks := newKillSwitch("")
	if tripped, _ := ks.Check(); tripped {
		t.Fatal("expected not tripped initially")
	}

	ks.Trip("manual test trip")
	tripped, reason := ks.Check()
	if !tripped || reason != "manual test trip" {
		t.Fatalf("expected tripped with reason recorded, got tripped=%v reason=%q", tripped, reason)
	}

	// Stays tripped, and the FIRST reason wins — a second Trip must not
	// overwrite it or untrip anything.
	ks.Trip("second reason")
	tripped2, reason2 := ks.Check()
	if !tripped2 || reason2 != "manual test trip" {
		t.Fatalf("expected first reason to stick, got tripped=%v reason=%q", tripped2, reason2)
	}
}

func TestKillSwitch_FileAbsent_NotTripped(t *testing.T) {
	dir := t.TempDir()
	ks := newKillSwitch(filepath.Join(dir, "does-not-exist"))
	if tripped, _ := ks.Check(); tripped {
		t.Fatal("expected not tripped when the kill file is absent")
	}
}

func TestKillSwitch_ExternalFileTrips_AndStaysTrippedAfterRemoval(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "KILL")
	ks := newKillSwitch(path)

	if tripped, _ := ks.Check(); tripped {
		t.Fatal("expected not tripped before the file exists")
	}

	if err := os.WriteFile(path, []byte("stop"), 0644); err != nil {
		t.Fatalf("write kill file: %v", err)
	}
	tripped, reason := ks.Check()
	if !tripped {
		t.Fatal("expected tripped once the kill file exists")
	}
	if reason == "" {
		t.Fatal("expected a non-empty reason")
	}

	// No auto-recovery: removing the file must not untrip it.
	if err := os.Remove(path); err != nil {
		t.Fatalf("remove kill file: %v", err)
	}
	tripped2, _ := ks.Check()
	if !tripped2 {
		t.Fatal("expected the kill switch to remain tripped after the file is removed")
	}
}

func TestKillSwitch_FileUnreadable_FailsClosed(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("running as root; unix permission bits are not enforced")
	}

	dir := t.TempDir()
	sub := filepath.Join(dir, "locked")
	if err := os.Mkdir(sub, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	killFile := filepath.Join(sub, "kill")
	if err := os.WriteFile(killFile, []byte("x"), 0644); err != nil {
		t.Fatalf("write kill file: %v", err)
	}
	if err := os.Chmod(sub, 0000); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	t.Cleanup(func() { _ = os.Chmod(sub, 0755) })

	ks := newKillSwitch(killFile)
	tripped, reason := ks.Check()
	if !tripped {
		t.Fatal("expected the kill switch to fail closed when its file cannot be stat'd")
	}
	if reason == "" {
		t.Fatal("expected a reason explaining the failure")
	}
}
