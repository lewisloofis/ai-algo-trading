package engine

import "fmt"

// StoreFill is one recorded execution, joined to its order_intent for symbol
// and side. It is the unit the broker reconciliation matches against the
// broker's own trade history.
type StoreFill struct {
	Seq      int64
	RunID    string
	OrderID  string
	Symbol   string
	Side     string // "buy" | "sell"
	RecvNs   int64
	PriceIDR int64
	Lots     int
	FeeIDR   float64
	// Unattributed marks a fill from a run that recorded no account (a run
	// predating run.account). Its account is unknown, not "this one".
	Unattributed bool
}

// Fills returns every fill recorded under sinkKind for runs whose account is
// account, oldest first. With includeUnattributed, fills from runs that
// recorded no account (legacy stores) are returned too, flagged Unattributed:
// the first live fill (2026-10-13) predates run.account, and dropping it would
// hide a real position. Callers that need strict routing safety (the
// pre-trade gate, DerivedPositions) pass false.
func (s *PostTradeStore) Fills(sinkKind, account string, includeUnattributed bool) ([]StoreFill, error) {
	rows, err := s.db.Query(`
		SELECT f.seq, f.run_id, f.order_id, oi.symbol, oi.side, f.recv_ns,
		       f.fill_price_idr, f.fill_lots, f.fee_idr, COALESCE(r.account, '') = ''
		FROM fill f
		JOIN order_intent oi ON oi.order_id = f.order_id AND oi.run_id = f.run_id
		LEFT JOIN run r ON r.run_id = oi.run_id
		WHERE f.sink_kind = ? AND oi.sink_kind = ?
		  AND (COALESCE(r.account, '') = ? OR (? AND COALESCE(r.account, '') = ''))
		ORDER BY f.recv_ns, f.seq
	`, sinkKind, sinkKind, account, includeUnattributed)
	if err != nil {
		return nil, fmt.Errorf("fills: query: %w", err)
	}
	defer rows.Close()

	var out []StoreFill
	for rows.Next() {
		var f StoreFill
		var unattributed int
		if err := rows.Scan(&f.Seq, &f.RunID, &f.OrderID, &f.Symbol, &f.Side, &f.RecvNs, &f.PriceIDR, &f.Lots, &f.FeeIDR, &unattributed); err != nil {
			return nil, fmt.Errorf("fills: scan: %w", err)
		}
		f.Unattributed = unattributed == 1
		out = append(out, f)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("fills: iterate: %w", err)
	}
	return out, nil
}
