package risk

import (
	"fmt"
	"os"
	"sync"
)

// killSwitch is the one piece of RiskSink state that must be assumed
// reachable from a goroutine other than engine.Run's single event-loop
// goroutine: an operator (or a future B7 ops surface) can drop the external
// kill file, or call RiskSink.TripKillSwitch, at any time. tripped and
// reason are guarded by mu; filePath is set once at construction and never
// mutated afterward, so it needs no lock to read.
//
// Once tripped, by either path, it stays tripped for the life of the
// killSwitch value — there is no untrip method. Plan 18 §5 requires "no
// auto-recovery": a human restarts the process deliberately, rather than
// the bot silently resuming because a transient condition cleared.
type killSwitch struct {
	filePath string

	mu      sync.Mutex
	tripped bool
	reason  string
}

func newKillSwitch(filePath string) *killSwitch {
	return &killSwitch{filePath: filePath}
}

// Check reports whether the switch is tripped, and why. If a file path is
// configured and the switch is not already tripped, it stats the file
// first:
//
//   - The file exists: trip, with a reason naming the path.
//   - The file does not exist (os.IsNotExist): not tripped. This also
//     covers a configured directory that was never created — "no file at
//     this path" is the same fact whether the leaf or a parent directory is
//     missing, and it is a definite, checkable absence, not an unknown.
//   - Any other stat error (permission denied, an intermediate path
//     component that is a file not a directory, ...): trip, failing closed.
//     An external kill switch whose state cannot be confirmed is not a kill
//     switch that failed to fire — it is unreadable, and unreadable must
//     resolve to tripped, not to "must be absent". A kill switch that fails
//     open on an I/O error is not a kill switch.
func (k *killSwitch) Check() (bool, string) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if k.tripped {
		return true, k.reason
	}
	if k.filePath == "" {
		return false, ""
	}
	_, err := os.Stat(k.filePath)
	switch {
	case err == nil:
		k.tripped = true
		k.reason = fmt.Sprintf("external kill switch file present at %q", k.filePath)
	case os.IsNotExist(err):
		// Absent — leave tripped=false.
	default:
		k.tripped = true
		k.reason = fmt.Sprintf("external kill switch file at %q could not be checked (%v) — failing closed", k.filePath, err)
	}
	return k.tripped, k.reason
}

// Trip trips the switch in-process immediately, e.g. from a confirmed
// daily-loss breach (RiskSink.checkDailyLoss) or a future ops surface
// (RiskSink.TripKillSwitch). A no-op if already tripped: the first reason
// recorded is the one that sticks.
func (k *killSwitch) Trip(reason string) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if !k.tripped {
		k.tripped = true
		k.reason = reason
	}
}
