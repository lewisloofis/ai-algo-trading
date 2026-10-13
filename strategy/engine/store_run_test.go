package engine

import (
	"database/sql"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestNewRunIDIsSortableAndUnique covers both properties the id needs: it
// sorts by start time as a plain string, and two runs started in the same
// second do not collide.
func TestNewRunIDIsSortableAndUnique(t *testing.T) {
	now := time.Date(2026, 9, 16, 3, 4, 5, 0, time.UTC)
	a, err := NewRunID(now)
	if err != nil {
		t.Fatalf("NewRunID: %v", err)
	}
	b, err := NewRunID(now)
	if err != nil {
		t.Fatalf("NewRunID: %v", err)
	}
	if a == b {
		t.Fatalf("two run ids minted in the same second collided: %q", a)
	}
	if !strings.HasPrefix(a, "20260916T030405Z-") {
		t.Fatalf("run id %q does not start with its UTC timestamp", a)
	}
	later, err := NewRunID(now.Add(time.Second))
	if err != nil {
		t.Fatalf("NewRunID: %v", err)
	}
	if !(a < later) {
		t.Fatalf("run ids do not sort by start time: %q !< %q", a, later)
	}
}

func testRunRow(id string) RunRow {
	return RunRow{
		RunID:      id,
		StartedNs:  1_000,
		Source:     "replay",
		Pacing:     "fast",
		FillMode:   "taker",
		SinkKind:   "paper",
		StrategyID: "orb",
		InputSpec:  "segments/dt=2026-09-01/*.binpb.zst",
		BinaryHash: "deadbeef",
	}
}

// TestBeginRunStampsEveryTable is the point of Stage 3: a row written after
// BeginRun carries the run id, in all four event tables.
func TestBeginRunStampsEveryTable(t *testing.T) {
	store := newTestStore(t)
	if err := store.BeginRun(testRunRow("run-1")); err != nil {
		t.Fatalf("BeginRun: %v", err)
	}
	if store.RunID() != "run-1" {
		t.Fatalf("RunID() = %q, want run-1", store.RunID())
	}

	if err := store.InsertDecision(DecisionRow{Seq: 1, RecvNs: 1, StrategyID: "orb", Symbol: "AAA", EventKind: "liveprice"}); err != nil {
		t.Fatalf("InsertDecision: %v", err)
	}
	if err := store.InsertIntent(IntentRow{Seq: 2, RecvNs: 2, StrategyID: "orb", Symbol: "AAA", Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "o1", SinkKind: "paper"}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	if err := store.InsertFill(FillRow{Seq: 3, RecvNs: 3, OrderID: "o1", FillPriceIDR: 100, FillLots: 1, FeeIDR: 1, SinkKind: "paper"}); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}
	if err := store.InsertAttribution(AttributionRow{Seq: 4, Symbol: "AAA", StrategyID: "orb", EntryRecvNs: 2, ExitRecvNs: 3, EntryOrderID: "o1", ExitOrderID: "o2", AsOfDate: "2026-09-16"}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	for _, table := range []string{"decision", "order_intent", "fill", "attribution"} {
		var got string
		if err := store.db.QueryRow("SELECT run_id FROM " + table).Scan(&got); err != nil {
			t.Fatalf("select run_id from %s: %v", table, err)
		}
		if got != "run-1" {
			t.Fatalf("%s.run_id = %q, want run-1", table, got)
		}
	}

	var asOf string
	if err := store.db.QueryRow(`SELECT as_of_date FROM attribution`).Scan(&asOf); err != nil {
		t.Fatalf("select as_of_date: %v", err)
	}
	if asOf != "2026-09-16" {
		t.Fatalf("as_of_date = %q, want 2026-09-16", asOf)
	}
}

// TestRowsBeforeBeginRunAreLegacy: a store used without BeginRun still works,
// and its rows are labelled as belonging to no known run rather than being
// attributed to one.
func TestRowsBeforeBeginRunAreLegacy(t *testing.T) {
	store := newTestStore(t)
	if store.RunID() != LegacyRunID {
		t.Fatalf("fresh store RunID() = %q, want LegacyRunID", store.RunID())
	}
	if err := store.InsertDecision(DecisionRow{Seq: 1, RecvNs: 1, StrategyID: "orb", Symbol: "AAA", EventKind: "liveprice"}); err != nil {
		t.Fatalf("InsertDecision: %v", err)
	}
	var got string
	if err := store.db.QueryRow(`SELECT run_id FROM decision`).Scan(&got); err != nil {
		t.Fatalf("select run_id: %v", err)
	}
	if got != LegacyRunID {
		t.Fatalf("run_id = %q, want LegacyRunID", got)
	}
}

func TestBeginRunRejectsEmptyRunID(t *testing.T) {
	store := newTestStore(t)
	if err := store.BeginRun(RunRow{Source: "replay"}); err == nil {
		t.Fatal("BeginRun with an empty run id must fail")
	}
}

// TestFinishRunRecordsOutcome covers the clean path and the error path, which
// is the one that matters: a run that stopped for a reason must say so.
func TestFinishRunRecordsOutcome(t *testing.T) {
	t.Run("complete", func(t *testing.T) {
		store := newTestStore(t)
		if err := store.BeginRun(testRunRow("run-ok")); err != nil {
			t.Fatalf("BeginRun: %v", err)
		}
		c := Counters{OrdersAttempted: 7, Fills: 5, TotalFeesIDR: 900, RoundTrips: 2, NetPnlBps: -3.5}
		if err := store.FinishRun(2_000, c, nil); err != nil {
			t.Fatalf("FinishRun: %v", err)
		}

		var (
			outcome, errMsg string
			endedNs         sql.NullInt64
			orders, fills   int
			roundTrips      int
			fees            int64
			netBps          float64
		)
		row := store.db.QueryRow(`SELECT outcome, error, ended_ns, orders_attempted, fills, total_fees_idr, round_trips, net_pnl_bps FROM run WHERE run_id = ?`, "run-ok")
		if err := row.Scan(&outcome, &errMsg, &endedNs, &orders, &fills, &fees, &roundTrips, &netBps); err != nil {
			t.Fatalf("scan run: %v", err)
		}
		if outcome != RunOutcomeComplete || errMsg != "" {
			t.Fatalf("outcome=%q error=%q, want complete/empty", outcome, errMsg)
		}
		if !endedNs.Valid || endedNs.Int64 != 2_000 {
			t.Fatalf("ended_ns = %v, want 2000", endedNs)
		}
		if orders != 7 || fills != 5 || fees != 900 || roundTrips != 2 || netBps != -3.5 {
			t.Fatalf("counters not persisted: %d %d %d %d %v", orders, fills, fees, roundTrips, netBps)
		}
	})

	t.Run("error", func(t *testing.T) {
		store := newTestStore(t)
		if err := store.BeginRun(testRunRow("run-bad")); err != nil {
			t.Fatalf("BeginRun: %v", err)
		}
		if err := store.FinishRun(2_000, Counters{}, errors.New("feed died")); err != nil {
			t.Fatalf("FinishRun: %v", err)
		}
		var outcome, errMsg string
		if err := store.db.QueryRow(`SELECT outcome, error FROM run WHERE run_id = ?`, "run-bad").Scan(&outcome, &errMsg); err != nil {
			t.Fatalf("scan run: %v", err)
		}
		if outcome != RunOutcomeError || errMsg != "feed died" {
			t.Fatalf("outcome=%q error=%q, want error/'feed died'", outcome, errMsg)
		}
	})
}

// TestRunHeaderIsWrittenAtStart is why BeginRun inserts rather than deferring
// everything to exit: a process that dies leaves a visible "running" row, not
// silence.
func TestRunHeaderIsWrittenAtStart(t *testing.T) {
	store := newTestStore(t)
	if err := store.BeginRun(testRunRow("run-crash")); err != nil {
		t.Fatalf("BeginRun: %v", err)
	}
	var (
		outcome string
		ended   sql.NullInt64
	)
	if err := store.db.QueryRow(`SELECT outcome, ended_ns FROM run WHERE run_id = ?`, "run-crash").Scan(&outcome, &ended); err != nil {
		t.Fatalf("scan run: %v", err)
	}
	if outcome != RunOutcomeRunning {
		t.Fatalf("outcome = %q, want running", outcome)
	}
	if ended.Valid {
		t.Fatalf("ended_ns must be NULL while the run is in flight, got %v", ended.Int64)
	}
}

func TestFinishRunWithoutBeginRunFails(t *testing.T) {
	store := newTestStore(t)
	if err := store.FinishRun(1, Counters{}, nil); err == nil {
		t.Fatal("FinishRun without BeginRun must fail")
	}
}

// TestMigrateAddsColumnsToAPreExistingStore is the compatibility guard for the
// 938 MB W2.4 replay store: CREATE TABLE IF NOT EXISTS would leave it at its
// old shape and every INSERT naming run_id would then fail.
func TestMigrateAddsColumnsToAPreExistingStore(t *testing.T) {
	path := filepath.Join(t.TempDir(), "old.db")

	// Build a store with the pre-plan-19 schema, by hand.
	old, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	_, err = old.Exec(`
CREATE TABLE decision (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, strategy_id TEXT NOT NULL, symbol TEXT NOT NULL, event_kind TEXT NOT NULL, signal_value REAL);
CREATE TABLE order_intent (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, strategy_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, price_idr INTEGER NOT NULL, lots INTEGER NOT NULL, order_id TEXT NOT NULL, sink_kind TEXT NOT NULL DEFAULT 'paper');
CREATE TABLE fill (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, order_id TEXT NOT NULL, fill_price_idr INTEGER NOT NULL, fill_lots INTEGER NOT NULL, fee_idr INTEGER NOT NULL, slippage_bps REAL NOT NULL, net_pnl_idr INTEGER, sink_kind TEXT NOT NULL DEFAULT 'paper');
CREATE TABLE attribution (seq INTEGER PRIMARY KEY, symbol TEXT NOT NULL, strategy_id TEXT NOT NULL, entry_recv_ns INTEGER NOT NULL, exit_recv_ns INTEGER NOT NULL, gross_pnl_bps REAL NOT NULL, net_pnl_bps REAL NOT NULL, entry_order_id TEXT NOT NULL, exit_order_id TEXT NOT NULL, hold_calendar_days REAL NOT NULL);
INSERT INTO decision (seq, recv_ns, strategy_id, symbol, event_kind) VALUES (1, 111, 'orb', 'HISTORY', 'liveprice');
`)
	if err != nil {
		t.Fatalf("build legacy schema: %v", err)
	}
	if err := old.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	store, err := OpenStore(path)
	if err != nil {
		t.Fatalf("OpenStore on a legacy file: %v", err)
	}
	defer store.Close()

	// The pre-existing row survives and is labelled legacy, not attributed to
	// whatever run opens the file next.
	var legacyRunID string
	if err := store.db.QueryRow(`SELECT run_id FROM decision WHERE symbol = 'HISTORY'`).Scan(&legacyRunID); err != nil {
		t.Fatalf("select legacy run_id: %v", err)
	}
	if legacyRunID != LegacyRunID {
		t.Fatalf("pre-existing row run_id = %q, want LegacyRunID", legacyRunID)
	}

	// And the migrated store accepts new writes.
	if err := store.BeginRun(testRunRow("run-after-migration")); err != nil {
		t.Fatalf("BeginRun after migration: %v", err)
	}
	if err := store.InsertAttribution(AttributionRow{Seq: 9, Symbol: "AAA", StrategyID: "orb", EntryRecvNs: 1, ExitRecvNs: 2, EntryOrderID: "a", ExitOrderID: "b", AsOfDate: "2026-09-16"}); err != nil {
		t.Fatalf("InsertAttribution after migration: %v", err)
	}
}

func TestMigrateIsIdempotent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "twice.db")
	for i := 0; i < 3; i++ {
		store, err := OpenStore(path)
		if err != nil {
			t.Fatalf("OpenStore #%d: %v", i, err)
		}
		if err := store.Close(); err != nil {
			t.Fatalf("Close #%d: %v", i, err)
		}
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("store file missing: %v", err)
	}
}

// TestJakartaDate pins the WIB conversion: a UTC host must not file an event
// from 23:00 UTC under the previous day.
func TestJakartaDate(t *testing.T) {
	// 2026-09-15T18:00:00Z is 2026-09-16 01:00 in WIB.
	ns := time.Date(2026, 9, 15, 18, 0, 0, 0, time.UTC).UnixNano()
	if got := JakartaDate(ns); got != "2026-09-16" {
		t.Fatalf("JakartaDate = %q, want 2026-09-16 (WIB is UTC+7)", got)
	}
	// Mid-session is unambiguous either way.
	ns = time.Date(2026, 9, 16, 3, 0, 0, 0, time.UTC).UnixNano()
	if got := JakartaDate(ns); got != "2026-09-16" {
		t.Fatalf("JakartaDate = %q, want 2026-09-16", got)
	}
	if got := JakartaDate(0); got != "" {
		t.Fatalf("JakartaDate(0) = %q, want empty", got)
	}
}

// TestAttributionAverageMatchesCounters pins the one definition the dashboard
// re-expresses in SQL. Panel C's mean_net_pnl_bps is
// `AVG(net_pnl_bps) FROM attribution`, and Counters.NetPnlBps is
// sumBps/RoundTrips over the same round-trips. They must be the same number,
// or the page and the run's own printed line disagree about what a strategy
// earned. (dashboard/test_panel_c.py holds the other half of this pact.)
func TestAttributionAverageMatchesCounters(t *testing.T) {
	store := newTestStore(t)
	if err := store.BeginRun(testRunRow("run-avg")); err != nil {
		t.Fatalf("BeginRun: %v", err)
	}

	bps := []float64{12.5, -40.0, 3.25, -1.75}
	for i, v := range bps {
		if err := store.InsertAttribution(AttributionRow{
			Seq: int64(i + 1), Symbol: "AAA", StrategyID: "orb",
			EntryRecvNs: 1, ExitRecvNs: 2, GrossPnlBps: v + 5, NetPnlBps: v,
			EntryOrderID: "a", ExitOrderID: "b", AsOfDate: "2026-09-16",
		}); err != nil {
			t.Fatalf("InsertAttribution: %v", err)
		}
	}

	// The engine's arithmetic, from the same rows.
	st := &runState{}
	for _, v := range bps {
		st.sumBps += v
		st.counters.RoundTrips++
	}
	want := finalize(st).NetPnlBps

	var got float64
	if err := store.db.QueryRow(`SELECT AVG(net_pnl_bps) FROM attribution`).Scan(&got); err != nil {
		t.Fatalf("avg: %v", err)
	}
	if math.Abs(got-want) > 1e-9 {
		t.Fatalf("SQL AVG = %v but Counters.NetPnlBps = %v — the dashboard and the run would disagree", got, want)
	}
}

// TestFinishRunTouchesOnlyTheRunTable backs the claim in PostTradeStore's doc
// comment: the `run` header is the single mutable row, and closing it out must
// not disturb a single event row. Without this, "the four event tables are
// append-only" is a comment rather than a property.
func TestFinishRunTouchesOnlyTheRunTable(t *testing.T) {
	store := newTestStore(t)
	if err := store.BeginRun(testRunRow("run-scope")); err != nil {
		t.Fatalf("BeginRun: %v", err)
	}
	if err := store.InsertDecision(DecisionRow{Seq: 1, RecvNs: 111, StrategyID: "orb", Symbol: "AAA", EventKind: "liveprice"}); err != nil {
		t.Fatalf("InsertDecision: %v", err)
	}
	if err := store.InsertIntent(IntentRow{Seq: 2, RecvNs: 222, StrategyID: "orb", Symbol: "AAA", Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "o1", SinkKind: "paper"}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	if err := store.InsertFill(FillRow{Seq: 3, RecvNs: 333, OrderID: "o1", FillPriceIDR: 100, FillLots: 1, FeeIDR: 7, SinkKind: "paper"}); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}
	if err := store.InsertAttribution(AttributionRow{Seq: 4, Symbol: "AAA", StrategyID: "orb", EntryRecvNs: 222, ExitRecvNs: 333, GrossPnlBps: 9, NetPnlBps: 8, EntryOrderID: "o1", ExitOrderID: "o2", AsOfDate: "2026-09-16"}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	// Fingerprint every event table before and after.
	fingerprint := func() map[string]string {
		out := map[string]string{}
		for _, q := range []struct{ table, sql string }{
			{"decision", `SELECT seq, recv_ns, strategy_id, symbol, event_kind, run_id FROM decision`},
			{"order_intent", `SELECT seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id, sink_kind, run_id FROM order_intent`},
			{"fill", `SELECT seq, recv_ns, order_id, fill_price_idr, fill_lots, fee_idr, slippage_bps, sink_kind, run_id FROM fill`},
			{"attribution", `SELECT seq, symbol, strategy_id, entry_recv_ns, exit_recv_ns, gross_pnl_bps, net_pnl_bps, run_id, as_of_date FROM attribution`},
		} {
			rows, err := store.db.Query(q.sql)
			if err != nil {
				t.Fatalf("query %s: %v", q.table, err)
			}
			var buf strings.Builder
			cols, _ := rows.Columns()
			for rows.Next() {
				vals := make([]any, len(cols))
				ptrs := make([]any, len(cols))
				for i := range vals {
					ptrs[i] = &vals[i]
				}
				if err := rows.Scan(ptrs...); err != nil {
					t.Fatalf("scan %s: %v", q.table, err)
				}
				fmt.Fprintf(&buf, "%v|", vals)
			}
			rows.Close()
			out[q.table] = buf.String()
		}
		return out
	}

	before := fingerprint()
	if err := store.FinishRun(9_999, Counters{OrdersAttempted: 1, Fills: 1, RoundTrips: 1, NetPnlBps: 8}, nil); err != nil {
		t.Fatalf("FinishRun: %v", err)
	}
	after := fingerprint()

	for table, want := range before {
		if after[table] != want {
			t.Fatalf("FinishRun modified %s:\n before=%s\n after =%s", table, want, after[table])
		}
	}
}

// TestMigrateAddsAccountToAPreExistingRunTable covers stores written before
// run.account existed (every store up to 2026-10-13): the column is added, and
// their runs read back with an empty account rather than failing.
func TestMigrateAddsAccountToAPreExistingRunTable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pre-account.db")
	old, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	_, err = old.Exec(`
CREATE TABLE run (run_id TEXT PRIMARY KEY, started_ns INTEGER NOT NULL, ended_ns INTEGER, outcome TEXT NOT NULL DEFAULT 'running', error TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, pacing TEXT NOT NULL DEFAULT '', fill_mode TEXT NOT NULL, sink_kind TEXT NOT NULL, strategy_id TEXT NOT NULL, input_spec TEXT NOT NULL, binary_hash TEXT NOT NULL DEFAULT '', orders_attempted INTEGER, fills INTEGER, total_fees_idr INTEGER, round_trips INTEGER, net_pnl_bps REAL);
INSERT INTO run (run_id, started_ns, source, fill_mode, sink_kind, strategy_id, input_spec) VALUES ('old-run', 1, 'live', 'taker', 'live', 's', 'X');
`)
	if err != nil {
		t.Fatalf("build pre-account schema: %v", err)
	}
	if err := old.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	store, err := OpenStore(path)
	if err != nil {
		t.Fatalf("OpenStore on a pre-account file: %v", err)
	}
	defer store.Close()

	var account string
	if err := store.db.QueryRow(`SELECT account FROM run WHERE run_id = 'old-run'`).Scan(&account); err != nil {
		t.Fatalf("select account: %v", err)
	}
	if account != "" {
		t.Fatalf("pre-existing run account = %q, want empty", account)
	}
	row := testRunRow("new-run")
	row.Account = "TEST-SLEEVE"
	if err := store.BeginRun(row); err != nil {
		t.Fatalf("BeginRun after migration: %v", err)
	}
	if err := store.db.QueryRow(`SELECT account FROM run WHERE run_id = 'new-run'`).Scan(&account); err != nil {
		t.Fatalf("select new account: %v", err)
	}
	if account != "TEST-SLEEVE" {
		t.Fatalf("new run account = %q, want TEST-SLEEVE", account)
	}
}
