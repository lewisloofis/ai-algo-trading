package engine

import (
	"database/sql"
	"path/filepath"
	"testing"
)

// legacyFillSchema is the pre-fix store shape: fee_idr declared INTEGER. Stores
// recorded before the fractional-fee fix look like this and must stay readable.
const legacyFillSchema = `
CREATE TABLE fill (
    seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, order_id TEXT NOT NULL,
    fill_price_idr INTEGER NOT NULL, fill_lots INTEGER NOT NULL, fee_idr INTEGER NOT NULL,
    slippage_bps REAL NOT NULL, net_pnl_idr INTEGER, sink_kind TEXT NOT NULL DEFAULT 'paper',
    run_id TEXT NOT NULL DEFAULT '');
INSERT INTO fill (seq, recv_ns, order_id, fill_price_idr, fill_lots, fee_idr, slippage_bps, sink_kind)
VALUES (1, 1, 'old', 27, 1, 4, 0, 'live');
`

func readFees(t *testing.T, s *PostTradeStore) map[int64]float64 {
	t.Helper()
	rows, err := s.db.Query(`SELECT seq, fee_idr FROM fill ORDER BY seq`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	out := map[int64]float64{}
	for rows.Next() {
		var seq int64
		var fee float64
		if err := rows.Scan(&seq, &fee); err != nil {
			t.Fatal(err)
		}
		out[seq] = fee
	}
	return out
}

// TestFillFeeStoredExactly: the broker charged 4.2 on a Rp 2,800 buy; the old
// integer-typed Go field stored 4. A fractional fee must round-trip exactly.
func TestFillFeeStoredExactly(t *testing.T) {
	s, err := OpenStore(filepath.Join(t.TempDir(), "new.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for i, fee := range []float64{4.2, 4.05, 6.5} {
		if err := s.InsertFill(FillRow{Seq: int64(i + 1), RecvNs: 1, OrderID: "o", FillPriceIDR: 28, FillLots: 1, FeeIDR: fee, SinkKind: "live"}); err != nil {
			t.Fatal(err)
		}
	}
	got := readFees(t, s)
	want := map[int64]float64{1: 4.2, 2: 4.05, 3: 6.5}
	for k, w := range want {
		if got[k] != w {
			t.Errorf("fee seq %d = %v, want %v", k, got[k], w)
		}
	}
}

// TestLegacyIntegerFeeStoreStaysReadableAndAcceptsFractions: an existing store
// whose fee_idr column is declared INTEGER keeps its old whole-rupiah rows
// readable, and fractional fees written into it are kept exactly (SQLite type
// affinity stores a non-integral REAL as REAL), so no table rebuild is needed.
func TestLegacyIntegerFeeStoreStaysReadableAndAcceptsFractions(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy.db")
	raw, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := raw.Exec(legacyFillSchema); err != nil {
		t.Fatal(err)
	}
	raw.Close()

	s, err := OpenStore(path)
	if err != nil {
		t.Fatalf("OpenStore on legacy store: %v", err)
	}
	defer s.Close()
	if err := s.InsertFill(FillRow{Seq: 2, RecvNs: 2, OrderID: "new", FillPriceIDR: 28, FillLots: 1, FeeIDR: 4.2, SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}
	got := readFees(t, s)
	if got[1] != 4 || got[2] != 4.2 {
		t.Fatalf("fees = %v, want {1:4 2:4.2}", got)
	}
}
