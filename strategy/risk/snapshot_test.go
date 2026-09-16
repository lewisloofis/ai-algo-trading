package risk

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"strategy/engine"
)

func snapshotSink(t *testing.T, killFile string) *RiskSink {
	t.Helper()
	sink, err := NewRiskSink(
		&recordingSink{},
		SleeveEquityIDR(10_000_000),
		permissiveLimits(),
		allowGate{},
		fixedPnL{pnl: 0},
		killFile,
		func() time.Time { return time.Date(2026, 9, 16, 10, 0, 0, 0, time.UTC) },
	)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	return sink
}

func TestSnapshotOnAFreshSink(t *testing.T) {
	r := snapshotSink(t, "")
	got := r.Snapshot()

	if got.KillSwitchTripped {
		t.Fatal("fresh sink must not report a tripped kill switch")
	}
	if got.TradingDay != "" {
		t.Fatalf("trading_day = %q, want empty before any order is admitted", got.TradingDay)
	}
	if got.DayNotionalIDR != 0 || got.GrossExposureIDR != 0 {
		t.Fatalf("fresh sink has exposure: %+v", got)
	}
	if got.DayNotionalCapIDR <= 0 || got.GrossExposureCapIDR <= 0 {
		t.Fatalf("caps must be reported so a figure has something to be measured against: %+v", got)
	}
	if got.PositionsIDR == nil {
		t.Fatal("positions must be an empty map, not nil — a JSON null reads as 'unknown'")
	}
}

// TestSnapshotReportsAdmittedExposure is the whole reason this method exists:
// day notional and positions were previously visible only from inside the
// process.
func TestSnapshotReportsAdmittedExposure(t *testing.T) {
	r := snapshotSink(t, "")
	_, err := r.Submit(context.Background(), engine.OrderIntent{
		Symbol: "ITMG", Side: "buy", PriceIDR: 1_000, Lots: 1, OrderID: "o1",
		BoardType: engine.BoardRG,
	})
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}

	got := r.Snapshot()
	if got.DayNotionalIDR <= 0 {
		t.Fatalf("day notional = %v after an admitted order, want > 0", got.DayNotionalIDR)
	}
	if got.PositionsIDR["ITMG"] <= 0 {
		t.Fatalf("positions = %+v, want ITMG > 0", got.PositionsIDR)
	}
	if got.GrossExposureIDR != got.PositionsIDR["ITMG"] {
		t.Fatalf("gross %v != sum of positions %v", got.GrossExposureIDR, got.PositionsIDR)
	}
	if got.TradingDay != "2026-09-16" {
		t.Fatalf("trading_day = %q, want 2026-09-16 (the Clock's date)", got.TradingDay)
	}
}

// TestSnapshotPositionsAreCopied: the caller must not be able to reach into
// the sink's state, and the JSON encoder must not race the event loop.
func TestSnapshotPositionsAreCopied(t *testing.T) {
	r := snapshotSink(t, "")
	if _, err := r.Submit(context.Background(), engine.OrderIntent{
		Symbol: "ITMG", Side: "buy", PriceIDR: 1_000, Lots: 1, OrderID: "o1",
		BoardType: engine.BoardRG,
	}); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	snap := r.Snapshot()
	snap.PositionsIDR["ITMG"] = -999
	if again := r.Snapshot(); again.PositionsIDR["ITMG"] == -999 {
		t.Fatal("mutating the snapshot changed the sink's own state")
	}
}

// TestSnapshotSeesTheKillSwitch: a status file must never report clear while
// an order would be rejected. Snapshot reads through the same Check the
// Submit path uses.
func TestSnapshotSeesTheKillSwitch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "kill")
	r := snapshotSink(t, path)
	if r.Snapshot().KillSwitchTripped {
		t.Fatal("kill switch reported tripped before the file exists")
	}
	if err := os.WriteFile(path, []byte("stop"), 0o600); err != nil {
		t.Fatalf("write kill file: %v", err)
	}
	got := r.Snapshot()
	if !got.KillSwitchTripped {
		t.Fatal("kill switch file present but Snapshot reports clear")
	}
	if got.KillSwitchReason == "" {
		t.Fatal("a tripped switch must report why")
	}
}

// TestSnapshotDoesNotRollTheDayOver: looking at the state must not change it.
// A stale day is reported as a stale day, via TradingDay, not silently reset.
func TestSnapshotDoesNotRollTheDayOver(t *testing.T) {
	now := time.Date(2026, 9, 16, 10, 0, 0, 0, time.UTC)
	clock := func() time.Time { return now }
	r, err := NewRiskSink(&recordingSink{}, SleeveEquityIDR(10_000_000), permissiveLimits(),
		allowGate{}, fixedPnL{pnl: 0}, "", clock)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	if _, err := r.Submit(context.Background(), engine.OrderIntent{
		Symbol: "ITMG", Side: "buy", PriceIDR: 1_000, Lots: 1, OrderID: "o1",
		BoardType: engine.BoardRG,
	}); err != nil {
		t.Fatalf("Submit: %v", err)
	}
	spent := r.Snapshot().DayNotionalIDR

	now = now.Add(24 * time.Hour) // a new trading day, but no order yet
	got := r.Snapshot()
	if got.DayNotionalIDR != spent {
		t.Fatalf("Snapshot rolled the day over: %v -> %v", spent, got.DayNotionalIDR)
	}
	if got.TradingDay != "2026-09-16" {
		t.Fatalf("trading_day = %q, want the day the figure belongs to", got.TradingDay)
	}
}

func TestSnapshotSatisfiesTheEngineInterface(t *testing.T) {
	var _ engine.RiskReporter = snapshotSink(t, "")
}
