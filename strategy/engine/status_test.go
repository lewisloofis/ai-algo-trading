package engine

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func readStatus(t *testing.T, path string) StatusDoc {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read status: %v", err)
	}
	var doc StatusDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("unmarshal status (%s): %v", raw, err)
	}
	return doc
}

func newTestStatusWriter(t *testing.T, p *RunProgress, risk RiskReporter) *StatusWriter {
	t.Helper()
	return &StatusWriter{
		Path:       filepath.Join(t.TempDir(), "nested", "status.json"),
		Interval:   time.Millisecond,
		Progress:   p,
		Risk:       risk,
		RunID:      "run-1",
		Source:     "replay",
		SinkKind:   "paper",
		StrategyID: "orb",
		InputSpec:  "segments/*.binpb.zst",
	}
}

func TestStatusWriteOnceCreatesTheDirectory(t *testing.T) {
	w := newTestStatusWriter(t, &RunProgress{}, nil)
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	doc := readStatus(t, w.Path)
	if doc.SchemaVersion != StatusSchemaVersion {
		t.Fatalf("schema_version = %d, want %d", doc.SchemaVersion, StatusSchemaVersion)
	}
	if doc.RunID != "run-1" || doc.Source != "replay" || doc.SinkKind != "paper" {
		t.Fatalf("header not carried through: %+v", doc)
	}
	if doc.Outcome != RunOutcomeRunning {
		t.Fatalf("outcome = %q, want running", doc.Outcome)
	}
}

// TestStatusRiskIsNullWhenNotWired: null must read as "not reported", which is
// why the field is a pointer rather than a zero-valued struct that would claim
// a 0 IDR cap.
func TestStatusRiskIsNullWhenNotWired(t *testing.T) {
	w := newTestStatusWriter(t, &RunProgress{}, nil)
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	raw, err := os.ReadFile(w.Path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !strings.Contains(string(raw), `"risk": null`) {
		t.Fatalf("status without a RiskSink must render risk as null, got:\n%s", raw)
	}
	if doc := readStatus(t, w.Path); doc.Risk != nil {
		t.Fatalf("Risk = %+v, want nil", doc.Risk)
	}
}

type fakeRisk struct{ snap RiskSnapshot }

func (f fakeRisk) Snapshot() RiskSnapshot { return f.snap }

func TestStatusIncludesRiskWhenWired(t *testing.T) {
	want := RiskSnapshot{
		KillSwitchTripped: true,
		KillSwitchReason:  "external kill switch file present",
		TradingDay:        "2026-09-16",
		DayNotionalIDR:    5_000_000,
		DayNotionalCapIDR: 7_500_000,
		PositionsIDR:      map[string]float64{"ITMG": 1_200_000},
		OpenReservations:  1,
	}
	w := newTestStatusWriter(t, &RunProgress{}, fakeRisk{snap: want})
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	doc := readStatus(t, w.Path)
	if doc.Risk == nil {
		t.Fatal("Risk is nil, want a snapshot")
	}
	if !doc.Risk.KillSwitchTripped || doc.Risk.DayNotionalIDR != 5_000_000 || doc.Risk.PositionsIDR["ITMG"] != 1_200_000 {
		t.Fatalf("risk snapshot not carried through: %+v", doc.Risk)
	}
}

func TestStatusReportsProgress(t *testing.T) {
	p := &RunProgress{}
	p.set(1_700_000_000_000_000_000, Counters{OrdersAttempted: 3, Fills: 2, RoundTrips: 1, NetPnlBps: -12.5})
	w := newTestStatusWriter(t, p, nil)
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	doc := readStatus(t, w.Path)
	if doc.Progress.EventsSeen != 1 {
		t.Fatalf("events_seen = %d, want 1", doc.Progress.EventsSeen)
	}
	if doc.Progress.Counters.RoundTrips != 1 || doc.Progress.Counters.NetPnlBps != -12.5 {
		t.Fatalf("counters not carried: %+v", doc.Progress.Counters)
	}
	if doc.Progress.LastEventAsOfDate == "" {
		t.Fatal("last_event_as_of_date must be set once an event has been seen")
	}
}

// TestStatusWallClockIsNotEventTime: in replay the two are years apart, and a
// staleness check that used event time would call every replay dead.
func TestStatusWallClockIsNotEventTime(t *testing.T) {
	p := &RunProgress{}
	historical := time.Date(2026, 6, 22, 3, 0, 0, 0, time.UTC).UnixNano()
	p.set(historical, Counters{})
	w := newTestStatusWriter(t, p, nil)
	w.Now = func() time.Time { return time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC) }
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	doc := readStatus(t, w.Path)
	if doc.Progress.LastEventNs != historical {
		t.Fatalf("last_event_ns = %d, want the event's own time", doc.Progress.LastEventNs)
	}
	if doc.WallClockNs == doc.Progress.LastEventNs {
		t.Fatal("wall_clock_ns must be the writer's clock, not the event's")
	}
}

func TestStatusRecordsTerminalOutcome(t *testing.T) {
	w := newTestStatusWriter(t, &RunProgress{}, nil)
	if err := w.WriteOnce(RunOutcomeError, errors.New("feed died")); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	doc := readStatus(t, w.Path)
	if doc.Outcome != RunOutcomeError || doc.Error != "feed died" {
		t.Fatalf("outcome=%q error=%q, want error/'feed died'", doc.Outcome, doc.Error)
	}
}

// TestStatusStartStopDoesNotOverwriteTheFinalOutcome is the race the stop
// function exists to close: the ticker must not write "running" after the run
// has ended.
func TestStatusStartStopDoesNotOverwriteTheFinalOutcome(t *testing.T) {
	w := newTestStatusWriter(t, &RunProgress{}, nil)
	stop := w.Start(context.Background(), func(err error) { t.Errorf("status write: %v", err) })
	time.Sleep(20 * time.Millisecond)
	stop()
	stop() // idempotent
	if err := w.WriteOnce(RunOutcomeComplete, nil); err != nil {
		t.Fatalf("WriteOnce: %v", err)
	}
	time.Sleep(20 * time.Millisecond)
	if doc := readStatus(t, w.Path); doc.Outcome != RunOutcomeComplete {
		t.Fatalf("outcome = %q after stop, want complete", doc.Outcome)
	}
}

func TestStatusStartWritesImmediately(t *testing.T) {
	w := newTestStatusWriter(t, &RunProgress{}, nil)
	w.Interval = time.Hour // the ticker will never fire during this test
	stop := w.Start(context.Background(), nil)
	defer stop()
	if _, err := os.Stat(w.Path); err != nil {
		t.Fatalf("Start must write the first status synchronously: %v", err)
	}
}

func TestStatusEmptyPathIsANoOp(t *testing.T) {
	w := &StatusWriter{Progress: &RunProgress{}}
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("WriteOnce with no path must be a no-op, got %v", err)
	}
	w.Start(context.Background(), nil)()
}

// TestStatusFileIsNeverPartiallyWritten: a reader polling the file while the
// writer runs must always parse. The atomic rename is what guarantees it.
func TestStatusFileIsNeverPartiallyWritten(t *testing.T) {
	p := &RunProgress{}
	w := newTestStatusWriter(t, p, nil)
	if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
		t.Fatalf("seed write: %v", err)
	}

	var wg sync.WaitGroup
	stopCh := make(chan struct{})
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 200; i++ {
			p.set(int64(i+1), Counters{Fills: i})
			if err := w.WriteOnce(RunOutcomeRunning, nil); err != nil {
				t.Errorf("WriteOnce: %v", err)
				return
			}
		}
		close(stopCh)
	}()

	for {
		select {
		case <-stopCh:
			wg.Wait()
			return
		default:
			raw, err := os.ReadFile(w.Path)
			if err != nil {
				t.Fatalf("read during writes: %v", err)
			}
			var doc StatusDoc
			if err := json.Unmarshal(raw, &doc); err != nil {
				t.Fatalf("status file was read half-written: %v\n%s", err, raw)
			}
		}
	}
}

// TestRunPublishesProgress is the end-to-end check that engine.Run actually
// feeds the writer: the counters were previously unobservable until Run
// returned.
func TestRunPublishesProgress(t *testing.T) {
	store := newTestStore(t)
	p := &RunProgress{}
	src := feedEvents(
		liveEvent("AAA", 1000, 100),
		liveEvent("AAA", 1010, 200),
	)
	if _, err := Run(context.Background(), src, noopStrategy{}, NewPaperSink(), store, "p", WithProgress(p)); err != nil {
		t.Fatalf("Run: %v", err)
	}
	snap := p.Snapshot()
	if snap.EventsSeen != 2 {
		t.Fatalf("events_seen = %d, want 2", snap.EventsSeen)
	}
	if snap.LastEventNs != 200 {
		t.Fatalf("last_event_ns = %d, want 200", snap.LastEventNs)
	}
}

func TestRunWithoutProgressIsUnchanged(t *testing.T) {
	store := newTestStore(t)
	src := feedEvents(liveEvent("AAA", 1000, 100))
	if _, err := Run(context.Background(), src, noopStrategy{}, NewPaperSink(), store, "p"); err != nil {
		t.Fatalf("Run without options must behave as before: %v", err)
	}
}

type noopStrategy struct{}

func (noopStrategy) OnEvent(context.Context, MarketEvent, Engine) {}
func (noopStrategy) OnFill(context.Context, FillRecord)           {}
