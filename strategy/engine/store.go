package engine

import (
	"database/sql"
	"fmt"

	_ "modernc.org/sqlite"
)

const schemaSQL = `
CREATE TABLE IF NOT EXISTS decision (
    seq INTEGER PRIMARY KEY,
    recv_ns INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    signal_value REAL
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
    sink_kind TEXT NOT NULL DEFAULT 'paper'
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
    sink_kind TEXT NOT NULL DEFAULT 'paper'
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
    hold_calendar_days REAL NOT NULL
);
`

// DecisionRow is one OnEvent observation.
type DecisionRow struct {
	Seq         int64
	RecvNs      int64
	StrategyID  string
	Symbol      string
	EventKind   string
	SignalValue sql.NullFloat64
}

// IntentRow is one SubmitOrder request.
type IntentRow struct {
	Seq        int64
	RecvNs     int64
	StrategyID string
	Symbol     string
	Side       string
	PriceIDR   int64
	Lots       int
	OrderID    string
	SinkKind   string
}

// FillRow is one simulated execution.
type FillRow struct {
	Seq          int64
	RecvNs       int64
	OrderID      string
	FillPriceIDR int64
	FillLots     int
	FeeIDR       int64
	SlippageBps  float64
	NetPnlIDR    sql.NullInt64
	SinkKind     string
}

// AttributionRow is one completed round-trip.
type AttributionRow struct {
	Seq              int64
	Symbol           string
	StrategyID       string
	EntryRecvNs      int64
	ExitRecvNs       int64
	GrossPnlBps      float64
	NetPnlBps        float64
	EntryOrderID     string
	ExitOrderID      string
	HoldCalendarDays float64
}

// PostTradeStore is an append-only SQLite store. It issues only INSERTs; no
// UPDATE or DELETE statement exists in this type.
type PostTradeStore struct {
	db *sql.DB
}

// OpenStore opens (creating if needed) a WAL-mode SQLite database at path and
// applies the schema.
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
	return &PostTradeStore{db: db}, nil
}

// Close closes the underlying database.
func (s *PostTradeStore) Close() error { return s.db.Close() }

// InsertDecision appends one decision row.
func (s *PostTradeStore) InsertDecision(r DecisionRow) error {
	_, err := s.db.Exec(
		`INSERT INTO decision (seq, recv_ns, strategy_id, symbol, event_kind, signal_value) VALUES (?, ?, ?, ?, ?, ?)`,
		r.Seq, r.RecvNs, r.StrategyID, r.Symbol, r.EventKind, r.SignalValue,
	)
	if err != nil {
		return fmt.Errorf("insert decision: %w", err)
	}
	return nil
}

// InsertIntent appends one order_intent row.
func (s *PostTradeStore) InsertIntent(r IntentRow) error {
	_, err := s.db.Exec(
		`INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id, sink_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.RecvNs, r.StrategyID, r.Symbol, r.Side, r.PriceIDR, r.Lots, r.OrderID, r.SinkKind,
	)
	if err != nil {
		return fmt.Errorf("insert intent: %w", err)
	}
	return nil
}

// InsertFill appends one fill row.
func (s *PostTradeStore) InsertFill(r FillRow) error {
	_, err := s.db.Exec(
		`INSERT INTO fill (seq, recv_ns, order_id, fill_price_idr, fill_lots, fee_idr, slippage_bps, net_pnl_idr, sink_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.RecvNs, r.OrderID, r.FillPriceIDR, r.FillLots, r.FeeIDR, r.SlippageBps, r.NetPnlIDR, r.SinkKind,
	)
	if err != nil {
		return fmt.Errorf("insert fill: %w", err)
	}
	return nil
}

// InsertAttribution appends one attribution row.
func (s *PostTradeStore) InsertAttribution(r AttributionRow) error {
	_, err := s.db.Exec(
		`INSERT INTO attribution (seq, symbol, strategy_id, entry_recv_ns, exit_recv_ns, gross_pnl_bps, net_pnl_bps, entry_order_id, exit_order_id, hold_calendar_days) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		r.Seq, r.Symbol, r.StrategyID, r.EntryRecvNs, r.ExitRecvNs, r.GrossPnlBps, r.NetPnlBps, r.EntryOrderID, r.ExitOrderID, r.HoldCalendarDays,
	)
	if err != nil {
		return fmt.Errorf("insert attribution: %w", err)
	}
	return nil
}
