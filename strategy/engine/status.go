package engine

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// RunProgress is a concurrency-safe view of a run that is still going.
//
// engine.Run writes to it after every event; a status writer on another
// goroutine reads it. It exists because engine.Counters is returned only when
// Run returns, which is exactly when nobody needs it any more: a process that
// has been streaming for four hours currently reports nothing at all until it
// stops.
type RunProgress struct {
	mu          sync.Mutex
	eventsSeen  int64
	lastEventNs int64
	counters    Counters
}

func (p *RunProgress) set(lastEventNs int64, c Counters) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if lastEventNs > 0 {
		p.eventsSeen++
		p.lastEventNs = lastEventNs
	}
	p.counters = c
}

// ProgressSnapshot is an immutable copy of a RunProgress at one instant.
type ProgressSnapshot struct {
	// EventsSeen is how many MarketEvents the loop has consumed.
	EventsSeen int64 `json:"events_seen"`
	// LastEventNs is the receive timestamp of the most recent event, Unix
	// nanoseconds UTC; 0 before the first event. In replay this is historical
	// time, so it must not be compared against wall-clock now to judge
	// staleness — see StatusDoc.WallClockNs.
	LastEventNs int64 `json:"last_event_ns"`
	// LastEventAsOfDate is LastEventNs as a WIB calendar date, or "" before
	// the first event.
	LastEventAsOfDate string `json:"last_event_as_of_date"`
	// Counters are the run's counters so far, with NetPnlBps averaged over the
	// round-trips completed to this point.
	Counters Counters `json:"counters"`
}

// Snapshot returns the current progress.
func (p *RunProgress) Snapshot() ProgressSnapshot {
	p.mu.Lock()
	defer p.mu.Unlock()
	return ProgressSnapshot{
		EventsSeen:        p.eventsSeen,
		LastEventNs:       p.lastEventNs,
		LastEventAsOfDate: JakartaDate(p.lastEventNs),
		Counters:          p.counters,
	}
}

// RiskSnapshot is the risk state a status file reports. It is declared here,
// not in package risk, because risk imports engine and not the other way
// round; risk.RiskSink.Snapshot returns this type.
type RiskSnapshot struct {
	// KillSwitchTripped is true once the switch has tripped, by either the
	// external file or an in-process trip. There is no untrip.
	KillSwitchTripped bool `json:"kill_switch_tripped"`
	// KillSwitchReason is why it tripped; empty when it has not.
	KillSwitchReason string `json:"kill_switch_reason"`
	// TradingDay is the calendar day the per-day notional counter is scoped
	// to, as YYYY-MM-DD in whatever zone the sink's Clock reports.
	TradingDay string `json:"trading_day"`
	// DayNotionalIDR is the notional admitted so far today.
	DayNotionalIDR float64 `json:"day_notional_idr"`
	// DayNotionalCapIDR is the per-day cap that figure is measured against.
	DayNotionalCapIDR float64 `json:"day_notional_cap_idr"`
	// GrossExposureIDR is the summed tracked notional across all symbols.
	GrossExposureIDR float64 `json:"gross_exposure_idr"`
	// GrossExposureCapIDR is the gross-exposure cap.
	GrossExposureCapIDR float64 `json:"gross_exposure_cap_idr"`
	// PositionsIDR is the tracked notional per symbol, long-only so never
	// negative.
	PositionsIDR map[string]float64 `json:"positions_idr"`
	// OpenReservations is how many admitted orders have not yet been released
	// — a number that should return to zero and is a leak if it does not.
	OpenReservations int `json:"open_reservations"`
}

// RiskReporter is implemented by a sink that can report its risk state.
// risk.RiskSink implements it.
type RiskReporter interface {
	Snapshot() RiskSnapshot
}

// StatusDoc is what lands in status.json.
type StatusDoc struct {
	// SchemaVersion is bumped when a reader must notice a shape change.
	SchemaVersion int `json:"schema_version"`
	// RunID ties this file to the `run` row and to every event row in the
	// store.
	RunID string `json:"run_id"`
	// WallClockNs is when this file was written, Unix nanoseconds UTC. Compare
	// THIS against now to judge whether the process is alive; LastEventNs is
	// the market's clock, which in replay is years old.
	WallClockNs int64 `json:"wall_clock_ns"`
	// WrittenAt is WallClockNs as an RFC3339 UTC string, for humans.
	WrittenAt string `json:"written_at"`
	// Source is "replay" or "live".
	Source string `json:"source"`
	// SinkKind is the order sink in use — "paper" today. Replay and paper PnL
	// are not live PnL, and this is the field that says so.
	SinkKind string `json:"sink_kind"`
	// StrategyID is the strategy being run.
	StrategyID string `json:"strategy_id"`
	// InputSpec is the segment glob or symbol list the run consumed.
	InputSpec string `json:"input_spec"`
	// Outcome is "running", "complete" or "error" — the same values as
	// RunRow.Outcome. A file left at "running" whose WallClockNs is stale is
	// a dead process.
	Outcome string `json:"outcome"`
	// Error is the failure message once Outcome is "error".
	Error string `json:"error,omitempty"`
	// Progress is the event-loop state.
	Progress ProgressSnapshot `json:"progress"`
	// Risk is the pre-trade risk state, or null when the run has no RiskSink
	// wired. Null means "not reported", never "no limits in force".
	Risk *RiskSnapshot `json:"risk"`
}

// StatusSchemaVersion is StatusDoc.SchemaVersion's current value.
const StatusSchemaVersion = 1

// DefaultStatusInterval is how often StatusWriter rewrites the file when no
// interval is given. Frequent enough that a stale-heartbeat alert can be
// meaningful within a minute, cheap enough to ignore: one small file rewrite.
const DefaultStatusInterval = 5 * time.Second

// StatusWriter rewrites a status file on a ticker for the life of a run.
//
// It is a file, not an HTTP endpoint, on purpose: the file survives the
// process dying, which is the moment its contents matter most. An endpoint
// answers only while the thing you are asking about is healthy enough to
// answer.
type StatusWriter struct {
	// Path is the file to write. Its directory is created if missing.
	Path string
	// Interval is the rewrite period; DefaultStatusInterval when zero.
	Interval time.Duration
	// Progress is the run being reported; required.
	Progress *RunProgress
	// Risk is an optional risk-state source. Nil renders "risk": null.
	Risk RiskReporter
	// Now defaults to time.Now.
	Now func() time.Time

	// Header fields, fixed for the life of the run.
	RunID      string
	Source     string
	SinkKind   string
	StrategyID string
	InputSpec  string
}

// Doc builds the current status document without writing it.
func (w *StatusWriter) Doc(outcome string, runErr error) StatusDoc {
	now := time.Now
	if w.Now != nil {
		now = w.Now
	}
	t := now().UTC()

	var risk *RiskSnapshot
	if w.Risk != nil {
		s := w.Risk.Snapshot()
		risk = &s
	}
	msg := ""
	if runErr != nil {
		msg = runErr.Error()
	}
	var progress ProgressSnapshot
	if w.Progress != nil {
		progress = w.Progress.Snapshot()
	}
	return StatusDoc{
		SchemaVersion: StatusSchemaVersion,
		RunID:         w.RunID,
		WallClockNs:   t.UnixNano(),
		WrittenAt:     t.Format(time.RFC3339),
		Source:        w.Source,
		SinkKind:      w.SinkKind,
		StrategyID:    w.StrategyID,
		InputSpec:     w.InputSpec,
		Outcome:       outcome,
		Error:         msg,
		Progress:      progress,
		Risk:          risk,
	}
}

// WriteOnce renders the status file atomically: write a temp file in the same
// directory, then rename over the target. A reader polling the file therefore
// never sees a half-written document, which a plain truncate-and-write would
// produce roughly every time it is unlucky.
func (w *StatusWriter) WriteOnce(outcome string, runErr error) error {
	if w.Path == "" {
		return nil
	}
	dir := filepath.Dir(w.Path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("status dir: %w", err)
	}
	body, err := json.MarshalIndent(w.Doc(outcome, runErr), "", "  ")
	if err != nil {
		return fmt.Errorf("marshal status: %w", err)
	}
	body = append(body, '\n')

	tmp, err := os.CreateTemp(dir, ".status-*.json")
	if err != nil {
		return fmt.Errorf("status temp: %w", err)
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(body); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return fmt.Errorf("write status: %w", err)
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return fmt.Errorf("close status: %w", err)
	}
	if err := os.Rename(tmpName, w.Path); err != nil {
		os.Remove(tmpName)
		return fmt.Errorf("rename status: %w", err)
	}
	return nil
}

// Start writes the file immediately, then on every tick until ctx is done. It
// returns a stop function that must be called before the final WriteOnce.
//
// stop BLOCKS until the ticker goroutine has returned, and that is the whole
// point of it. Merely signalling the goroutine is not enough: it can already
// be inside a WriteOnce, which would then land after the terminal write and
// leave a finished run reporting "running" forever. Signal-and-return loses
// that race often enough to be caught by a 20 ms test.
//
// A write error does not stop the run: observability failing is not a reason
// to stop trading, and the caller sees it through onErr.
func (w *StatusWriter) Start(ctx context.Context, onErr func(error)) (stop func()) {
	if w.Path == "" {
		return func() {}
	}
	interval := w.Interval
	if interval <= 0 {
		interval = DefaultStatusInterval
	}
	report := func(err error) {
		if err != nil && onErr != nil {
			onErr(err)
		}
	}
	report(w.WriteOnce(RunOutcomeRunning, nil))

	done := make(chan struct{})
	finished := make(chan struct{})
	var once sync.Once
	go func() {
		defer close(finished)
		t := time.NewTicker(interval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-t.C:
				report(w.WriteOnce(RunOutcomeRunning, nil))
			}
		}
	}()
	return func() {
		once.Do(func() { close(done) })
		<-finished
	}
}
