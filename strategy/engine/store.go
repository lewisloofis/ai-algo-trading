package engine

import (
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"fmt"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

// LegacyRunID is the run_id carried by rows written before the run_id column
// existed (plan-19 Stage 3). ALTER TABLE ADD COLUMN cannot invent a value for
// history, so those rows get the empty default and a reader must render them
// as an unknown run rather than fold them into whichever run it is looking
// at. The dashboard surfaces them under this label instead of dropping them.
const LegacyRunID = ""

// Store-path convention (plan-19 Stage 3, the "decide and document" box):
//
//	ONE STORE FILE PER RUN.
//
// seq is `INTEGER PRIMARY KEY` and every run restarts it at 1, so appending a
// second run to the same file collides on the primary key — which is not a
// flaw to work around but the append-only guarantee doing its job (see
// engine_test.go TestStoreAppendOnly: a duplicate seq must be rejected, never
// silently overwrite). Widening the key to (run_id, seq) would rewrite the
// primary key of the existing 938 MB W2.4 replay store for no gain, because
// nothing needs two runs interleaved in one file.
//
// run_id is still recorded on every row, for the two things that need it:
// a store file becomes self-describing (which run, which binary, which
// segments — join to the `run` table in the same file), and a reader given
// several store files can UNION them into one table and still group correctly.
const StorePathConvention = "one store file per run"

const schemaSQL = `
CREATE TABLE IF NOT EXISTS decision (
    seq INTEGER PRIMARY KEY,
    recv_ns INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    signal_value REAL,
    run_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_intent (
    seq INTEGER PRIMARY KEY,
    recv_ns INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price_idr INTEGER NOT NULL,
    lots INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    sink_kind TEXT NOT NULL DEFAULT 'paper',
    run_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fill (
    seq INTEGER PRIMARY KEY,
    recv_ns INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    fill_price_idr INTEGER NOT NULL,
    fill_lots INTEGER NOT NULL,
    fee_idr INTEGER NOT NULL,
    slippage_bps REAL NOT NULL,
    net_pnl_idr INTEGER,
    sink_kind TEXT NOT NULL DEFAULT 'paper',
    run_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS attribution (
    seq INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    entry_recv_ns INTEGER NOT NULL,
    exit_recv_ns INTEGER NOT NULL,
    gross_pnl_bps REAL NOT NULL,
    net_pnl_bps REAL NOT NULL,
    entry_order_id TEXT NOT NULL,
    exit_order_id TEXT NOT NULL,
    hold_calendar_days REAL NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    as_of_date TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run (
    run_id TEXT PRIMARY KEY,
    started_ns INTEGER NOT NULL,
    ended_ns INTEGER,
    outcome TEXT NOT NULL DEFAULT 'running',
    error TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    pacing TEXT NOT NULL DEFAULT '',
    fill_mode TEXT NOT NULL,
    sink_kind TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    input_spec TEXT NOT NULL,
    binary_hash TEXT NOT NULL DEFAULT '',
    orders_attempted INTEGER,
    fills INTEGER,
    total_fees_idr INTEGER,
    round_trips INTEGER,
    net_pnl_bps REAL
);
`

// migrations lists the columns added to the four event tables after their
// first release, as (table, column, DDL type + default). Applied by migrate on
// every OpenStore against an existing file: CREATE TABLE IF NOT EXISTS is a
// no-op on a store created before these columns existed, so without this an
// old store keeps its old shape and every INSERT naming run_id fails.
//
// Each column is nullable-by-default-string on purpose — ALTER TABLE ADD
// COLUMN cannot back-fill history, and inventing a run_id for rows that
// predate the concept would be a lie. They get LegacyRunID.
var migrations = []struct{ table, column, ddl string }{
	{"decision", "run_id", "TEXT NOT NULL DEFAULT ''"},
	{"order_intent", "run_id", "TEXT NOT NULL DEFAULT ''"},
	{"fill", "run_id", "TEXT NOT NULL DEFAULT ''"},
	{"attribution", "run_id", "TEXT NOT NULL DEFAULT ''"},
	{"attribution", "as_of_date", "TEXT NOT NULL DEFAULT ''"},
}

// DecisionRow is one OnEvent observation.
type DecisionRow struct {
	// Seq is the run-scoped monotonic sequence number. It orders every row in
	// the store — decisions, intents, fills and attributions share one counter
	// — and is the primary key, so a duplicate is rejected rather than
	// overwriting (the append-only guarantee).
	Seq int64
	// RecvNs is the market event's receive timestamp, Unix nanoseconds UTC.
	// In replay it is the timestamp captured in the segment, not wall-clock
	// now: a replayed run's timeline is the original session's.
	RecvNs int64
	// StrategyID is the strategy that saw this event, e.g. "orb". It is the
	// runtime's own identifier and does NOT map to a research family name by
	// string — see the strategy-id join map in the dashboard.
	StrategyID string
	// Symbol is the IDX ticker the event belongs to.
	Symbol string
	// EventKind is the market event type ("liveprice", "orderbook", ...).
	EventKind string
	// SignalValue is the strategy's scalar signal for this event, when it
	// implements SignalReporter. NULL means the strategy reported no value —
	// not that the value was zero.
	SignalValue sql.NullFloat64
	// RunID identifies the run that wrote this row; LegacyRunID ("") for rows
	// written before the column existed.
	RunID string
}

// IntentRow is one SubmitOrder request.
type IntentRow struct {
	// Seq is the run-scoped monotonic sequence number (see DecisionRow.Seq).
	Seq int64
	// RecvNs is the receive timestamp of the event that produced this intent.
	RecvNs int64
	// StrategyID is the strategy that submitted the order.
	StrategyID string
	// Symbol is the IDX ticker being traded.
	Symbol string
	// Side is "buy" or "sell".
	Side string
	// PriceIDR is the limit price in whole IDR.
	PriceIDR int64
	// Lots is the order size in lots (1 lot = 100 shares).
	Lots int
	// OrderID is the intent's own identifier, echoed by the fill it produces.
	OrderID string
	// SinkKind records which sink accepted the intent: "paper" for a simulated
	// fill, a live value once live routing exists. Replay PnL and live PnL
	// must never be summed into one number, and this is the column that
	// prevents it.
	SinkKind string
	// RunID identifies the run that wrote this row.
	RunID string
}

// FillRow is one simulated execution.
type FillRow struct {
	// Seq is the run-scoped monotonic sequence number (see DecisionRow.Seq).
	Seq int64
	// RecvNs is the receive timestamp of the event the fill was produced on.
	RecvNs int64
	// OrderID links the fill to its order_intent row.
	OrderID string
	// FillPriceIDR is the executed price in whole IDR.
	FillPriceIDR int64
	// FillLots is the executed size in lots.
	FillLots int
	// FeeIDR is the commission paid on this fill, in whole IDR.
	FeeIDR int64
	// SlippageBps is the executed price's distance from the reference price,
	// in basis points — the simulator's modelled cost of crossing the spread.
	SlippageBps float64
	// NetPnlIDR is the realised PnL of this fill in IDR when it closes a
	// position; NULL on an opening fill, which has not realised anything.
	NetPnlIDR sql.NullInt64
	// SinkKind records which sink produced the fill (see IntentRow.SinkKind).
	SinkKind string
	// RunID identifies the run that wrote this row.
	RunID string
}

// AttributionRow is one completed round-trip.
type AttributionRow struct {
	// Seq is the run-scoped monotonic sequence number (see DecisionRow.Seq).
	Seq int64
	// Symbol is the IDX ticker traded.
	Symbol string
	// StrategyID is the strategy credited with the round-trip.
	StrategyID string
	// EntryRecvNs is the receive timestamp of the opening fill.
	EntryRecvNs int64
	// ExitRecvNs is the receive timestamp of the closing fill.
	ExitRecvNs int64
	// GrossPnlBps is the entry-direction return in basis points, before fees
	// and before any other friction.
	GrossPnlBps float64
	// NetPnlBps is GrossPnlBps minus both legs' fees expressed in basis points
	// of the entry notional. It does NOT include financing or market impact —
	// those are modelled in research, not by the fill simulator.
	NetPnlBps float64
	// EntryOrderID is the opening fill's order id.
	EntryOrderID string
	// ExitOrderID is the closing fill's order id.
	ExitOrderID string
	// HoldCalendarDays is the wall-clock distance between the two fills in
	// calendar days — not trading days, and not a settlement horizon.
	HoldCalendarDays float64
	// RunID identifies the run that wrote this row.
	RunID string
	// AsOfDate is the Jakarta (WIB) calendar date the round-trip CLOSED on,
	// as YYYY-MM-DD. It is the day key for per-day PnL: the exchange's date,
	// not the host's, because a UTC host disagrees with WIB for the seven
	// hours after Jakarta midnight.
	AsOfDate string
}

// RunOutcome values for RunRow.Outcome.
const (
	RunOutcomeRunning  = "running"  // row written at start; the run has not ended
	RunOutcomeComplete = "complete" // event stream ended cleanly
	RunOutcomeError    = "error"    // the run stopped on an error; see RunRow.Error
)

// RunRow is the header for one strategy-run process: what it ran, against
// what, and how it ended. It is what makes a store file self-describing and
// what lets a reader tell two runs' rows apart.
type RunRow struct {
	// RunID is the run's identifier, generated at start by NewRunID.
	RunID string
	// StartedNs is wall-clock process start, Unix nanoseconds UTC. This is
	// real time even for a replay, whose event timestamps are historical.
	StartedNs int64
	// EndedNs is wall-clock process end; NULL while the run is in flight, and
	// still NULL afterwards if the process was killed before it could finish.
	EndedNs sql.NullInt64
	// Outcome is "running", "complete" or "error". A row left at "running" is
	// a crashed run — that is the point of writing the header at start.
	Outcome string
	// Error is the failure message when Outcome is "error"; empty otherwise.
	Error string
	// Source is "replay" or "live" — where MarketEvents came from.
	Source string
	// Pacing is "fast" or "paced" for a replay; empty for live.
	Pacing string
	// FillMode is the paper simulator's fill rule: "taker" or "mid".
	FillMode string
	// SinkKind is the order sink used (see IntentRow.SinkKind).
	SinkKind string
	// StrategyID is the strategy that ran.
	StrategyID string
	// InputSpec is the segment glob (replay) or symbol list (live) the run
	// consumed — the "against what" a PnL figure is only meaningful with.
	InputSpec string
	// BinaryHash identifies the binary that produced the run. Empty when it
	// could not be computed; never faked.
	BinaryHash string
	// Counters is the final engine.Counters; zero-valued while running.
	Counters Counters
	// CountersValid is false until FinishRun writes the counters, so a reader
	// can tell "no round-trips" from "not finished".
	CountersValid bool
}

// PostTradeStore is a SQLite store for one run.
//
// The four event tables — decision, order_intent, fill, attribution — are
// strictly append-only: this type issues only INSERTs against them, and their
// seq primary key rejects a duplicate rather than overwriting it.
//
// The `run` table is the single exception, and deliberately so. Its row is
// INSERTed at start (so a process that dies leaves a visible "running" row
// rather than no row at all) and UPDATEd exactly once at exit by FinishRun, to
// record how the run ended. Writing it only at exit would mean a crashed run
// is indistinguishable from a run that never happened, which is the one thing
// a run header exists to tell you.
type PostTradeStore struct {
	db    *sql.DB
	runID string
}

// OpenStore opens (creating if needed) a WAL-mode SQLite database at path,
// applies the schema and runs migrations. Rows written through the returned
// store carry LegacyRunID until BeginRun is called.
func OpenStore(path string) (*PostTradeStore, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open store: %w", err)
	}
	if _, err := db.Exec("PRAGMA journal_mode=WAL;"); err != nil {
		db.Close()
		return nil, fmt.Errorf("enable WAL: %w", err)
	}
	if _, err := db.Exec(schemaSQL); err != nil {
		db.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	if err := migrate(db); err != nil {
		db.Close()
		return nil, fmt.Errorf("migrate store: %w", err)
	}
	return &PostTradeStore{db: db, runID: LegacyRunID}, nil
}

// migrate adds any column in migrations that the opened file does not already
// have. Idempotent: a freshly created store already has them all and this is a
// no-op.
func migrate(db *sql.DB) error {
	for _, m := range migrations {
		has, err := hasColumn(db, m.table, m.column)
		if err != nil {
			return err
		}
		if has {
			continue
		}
		stmt := fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", m.table, m.column, m.ddl)
		if _, err := db.Exec(stmt); err != nil {
			return fmt.Errorf("add %s.%s: %w", m.table, m.column, err)
		}
	}
	return nil
}

func hasColumn(db *sql.DB, table, column string) (bool, error) {
	rows, err := db.Query(fmt.Sprintf("PRAGMA table_info(%s)", table))
	if err != nil {
		return false, fmt.Errorf("table_info(%s): %w", table, err)
	}
	defer rows.Close()
	for rows.Next() {
		var (
			cid        int
			name, typ  string
			notNull    int
			dflt       sql.NullString
			primaryKey int
		)
		if err := rows.Scan(&cid, &name, &typ, &notNull, &dflt, &primaryKey); err != nil {
			return false, fmt.Errorf("scan table_info(%s): %w", table, err)
		}
		if name == column {
			return true, rows.Err()
		}
	}
	return false, rows.Err()
}

// NewRunID mints a run identifier: a UTC timestamp for human sorting plus 8
// random hex characters so two runs started in the same second do not collide.
func NewRunID(now time.Time) (string, error) {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", fmt.Errorf("new run id: %w", err)
	}
	return now.UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(b[:]), nil
}

// RunID reports the run every subsequent insert is stamped with.
func (s *PostTradeStore) RunID() string { return s.runID }

// BeginRun writes the run header and binds the store to r.RunID, so every
// later insert is stamped with it. Call before engine.Run.
func (s *PostTradeStore) BeginRun(r RunRow) error {
	if strings.TrimSpace(r.RunID) == "" {
		return fmt.Errorf("begin run: run id is required")
	}
	if r.Outcome == "" {
		r.Outcome = RunOutcomeRunning
	}
	_, err := s.db.Exec(
		`INSERT INTO run (run_id, started_ns, ended_ns, outcome, error, source, pacing, fill_mode, sink_kind, strategy_id, input_spec, binary_hash)
		 VALUES (?, ?, NULL, ?, '', ?, ?, ?, ?, ?, ?, ?)`,
		r.RunID, r.StartedNs, r.Outcome, r.Source, r.Pacing, r.FillMode, r.SinkKind,
		r.StrategyID, r.InputSpec, r.BinaryHash,
	)
	if err != nil {
		return fmt.Errorf("insert run: %w", err)
	}
	s.runID = r.RunID
	return nil
}

// FinishRun closes out the run header: end time, outcome, error text and the
// final counters. runErr may be nil (a clean end of stream).
//
// This is the only UPDATE in this type, and it touches only the `run` table —
// never an event row. Call it on every exit path, including the error one: a
// run that ended badly is a fact worth recording, and a header left at
// "running" means the process died without getting here.
func (s *PostTradeStore) FinishRun(endedNs int64, c Counters, runErr error) error {
	if s.runID == LegacyRunID {
		return fmt.Errorf("finish run: no run in progress (BeginRun was not called)")
	}
	outcome := RunOutcomeComplete
	msg := ""
	if runErr != nil {
		outcome = RunOutcomeError
		msg = runErr.Error()
	}
	_, err := s.db.Exec(
		`UPDATE run SET ended_ns = ?, outcome = ?, error = ?,
		        orders_attempted = ?, fills = ?, total_fees_idr = ?, round_trips = ?, net_pnl_bps = ?
		 WHERE run_id = ?`,
		endedNs, outcome, msg,
		c.OrdersAttempted, c.Fills, c.TotalFeesIDR, c.RoundTrips, c.NetPnlBps,
		s.runID,
	)
	if err != nil {
		return fmt.Errorf("finish run: %w", err)
	}
	return nil
}

// Close closes the underlying database.
func (s *PostTradeStore) Close() error { return s.db.Close() }

// QueryRow executes a query that returns a single row.
func (s *PostTradeStore) QueryRow(query string, args ...any) *sql.Row {
	return s.db.QueryRow(query, args...)
}

// InsertDecision appends one decision row.
func (s *PostTradeStore) InsertDecision(r DecisionRow) error {
	_, err := s.db.Exec(
		`INSERT INTO decision (seq, recv_ns, strategy_id, symbol, event_kind, signal_value, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.RecvNs, r.StrategyID, r.Symbol, r.EventKind, r.SignalValue, s.runID,
	)
	if err != nil {
		return fmt.Errorf("insert decision: %w", err)
	}
	return nil
}

// InsertIntent appends one order_intent row.
func (s *PostTradeStore) InsertIntent(r IntentRow) error {
	_, err := s.db.Exec(
		`INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id, sink_kind, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.RecvNs, r.StrategyID, r.Symbol, r.Side, r.PriceIDR, r.Lots, r.OrderID, r.SinkKind, s.runID,
	)
	if err != nil {
		return fmt.Errorf("insert intent: %w", err)
	}
	return nil
}

// InsertFill appends one fill row.
func (s *PostTradeStore) InsertFill(r FillRow) error {
	_, err := s.db.Exec(
		`INSERT INTO fill (seq, recv_ns, order_id, fill_price_idr, fill_lots, fee_idr, slippage_bps, net_pnl_idr, sink_kind, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.RecvNs, r.OrderID, r.FillPriceIDR, r.FillLots, r.FeeIDR, r.SlippageBps, r.NetPnlIDR, r.SinkKind, s.runID,
	)
	if err != nil {
		return fmt.Errorf("insert fill: %w", err)
	}
	return nil
}

// InsertAttribution appends one attribution row.
func (s *PostTradeStore) InsertAttribution(r AttributionRow) error {
	_, err := s.db.Exec(
		`INSERT INTO attribution (seq, symbol, strategy_id, entry_recv_ns, exit_recv_ns, gross_pnl_bps, net_pnl_bps, entry_order_id, exit_order_id, hold_calendar_days, run_id, as_of_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.Symbol, r.StrategyID, r.EntryRecvNs, r.ExitRecvNs, r.GrossPnlBps, r.NetPnlBps,
		r.EntryOrderID, r.ExitOrderID, r.HoldCalendarDays, s.runID, r.AsOfDate,
	)
	if err != nil {
		return fmt.Errorf("insert attribution: %w", err)
	}
	return nil
}
