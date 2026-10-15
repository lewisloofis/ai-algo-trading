package portfolio

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
)

// SidecarPath is where the emitter (research/strategy_fit/target_emitter.py)
// writes the explanatory sidecar of a target file: <stem>.signal.json.
func SidecarPath(targetPath string) string {
	return strings.TrimSuffix(targetPath, ".json") + ".signal.json"
}

// RequireLiveCandidate refuses a target whose emitter sidecar says its spec is
// anything but "candidate". Only a candidate may arm a live sink; "paper",
// "retired" and "example" specs are paper-only. A target with no sidecar (a
// hand-written file) is allowed: there is nothing to check against. A sidecar
// that exists but cannot be read, parsed, or has no spec_status fails closed.
func RequireLiveCandidate(targetPath string) error {
	raw, err := os.ReadFile(SidecarPath(targetPath))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("target sidecar %s unreadable (%v): failing closed", SidecarPath(targetPath), err)
	}
	var sc struct {
		SpecStatus string `json:"spec_status"`
	}
	if err := json.Unmarshal(raw, &sc); err != nil {
		return fmt.Errorf("target sidecar %s unreadable (%v): failing closed", SidecarPath(targetPath), err)
	}
	switch sc.SpecStatus {
	case "":
		return fmt.Errorf("target sidecar %s has no spec_status: failing closed", SidecarPath(targetPath))
	case "candidate":
		return nil
	default:
		return fmt.Errorf("emitter spec status is %q: only a \"candidate\" spec may go live (a paper spec is paper-only)", sc.SpecStatus)
	}
}
