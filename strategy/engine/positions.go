package engine

import (
	"fmt"
	"math"
)

// DerivedPosition is our own record of a position, built by replaying one
// sink kind's fills through a moving-average cost model. It exists so
// strategy/account can reconcile broker-reported holdings against what our
// own fills say we should be holding, without either side being trusted by
// default (docs/plans/18-alpha-and-infrastructure-split.md §5, B1).
type DerivedPosition struct {
	Symbol string
	Shares float64

	// CostBasisExFeeIDR and CostBasisInclFeeIDR are the running cost basis
	// of the currently-held shares only: on a sell, cost basis is reduced
	// proportionally at the average cost per share held at that point
	// (moving-average accounting), not FIFO/LIFO lot matching. This matches
	// the basis the broker's own amount_invested figure is computed on
	// (docs/reference/05-account-state.md §2.2.3), which is also an average.
	CostBasisExFeeIDR   int64
	CostBasisInclFeeIDR int64

	FillCount int // number of fill rows folded into this position
}

// lotAccount is the running moving-average state for one symbol while
// replaying fills in recv_ns order.
type lotAccount struct {
	shares         float64
	costExFeeIDR   float64
	costInclFeeIDR float64
	fillCount      int
}

func (a *lotAccount) applyBuy(shares, priceIDR, feeIDR float64) {
	a.shares += shares
	a.costExFeeIDR += priceIDR * shares
	a.costInclFeeIDR += priceIDR*shares + feeIDR
	a.fillCount++
}

func (a *lotAccount) applySell(shares float64) {
	a.fillCount++
	if a.shares <= 0 {
		return
	}
	// Guard against a sell larger than the recorded position (bad data, or a
	// short this package doesn't model — long-only per plan 18 §11): clamp
	// to what's on record rather than go negative.
	if shares > a.shares {
		shares = a.shares
	}
	avgExFee := a.costExFeeIDR / a.shares
	avgInclFee := a.costInclFeeIDR / a.shares
	a.costExFeeIDR -= avgExFee * shares
	a.costInclFeeIDR -= avgInclFee * shares
	a.shares -= shares
}

// DerivedPositions replays every fill recorded under sinkKind through a
// moving-average cost model and returns the resulting position per symbol,
// joining fill to order_intent on order_id to recover symbol and side (the
// fill table itself carries neither — see the schema in store.go).
//
// sinkKind is load-bearing: both order_intent and fill carry it, defaulting
// to 'paper'. Reconciling paper fills against a live broker account would be
// pure noise, so both sides of the join are filtered to the requested kind.
// A symbol whose fills net to zero shares (fully closed) is omitted — it is
// not currently "held" by either side.
func (s *PostTradeStore) DerivedPositions(sinkKind string) ([]DerivedPosition, error) {
	rows, err := s.db.Query(`
		SELECT oi.symbol, oi.side, f.fill_price_idr, f.fill_lots, f.fee_idr
		FROM fill f
		JOIN order_intent oi ON oi.order_id = f.order_id
		WHERE f.sink_kind = ? AND oi.sink_kind = ?
		ORDER BY oi.symbol, f.recv_ns, f.seq
	`, sinkKind, sinkKind)
	if err != nil {
		return nil, fmt.Errorf("derived positions: query fills: %w", err)
	}
	defer rows.Close()

	accounts := map[string]*lotAccount{}
	var order []string
	for rows.Next() {
		var symbol, side string
		var priceIDR int64
		var lots int
		var feeIDR int64
		if err := rows.Scan(&symbol, &side, &priceIDR, &lots, &feeIDR); err != nil {
			return nil, fmt.Errorf("derived positions: scan fill: %w", err)
		}
		acc, ok := accounts[symbol]
		if !ok {
			acc = &lotAccount{}
			accounts[symbol] = acc
			order = append(order, symbol)
		}
		shares := float64(lots * sharesPerLot)
		switch side {
		case "buy":
			acc.applyBuy(shares, float64(priceIDR), float64(feeIDR))
		case "sell":
			acc.applySell(shares)
		default:
			return nil, fmt.Errorf("derived positions: order_intent %s: unknown side %q", symbol, side)
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("derived positions: iterate fills: %w", err)
	}

	out := make([]DerivedPosition, 0, len(order))
	for _, symbol := range order {
		acc := accounts[symbol]
		if acc.shares <= 0 {
			continue
		}
		out = append(out, DerivedPosition{
			Symbol:              symbol,
			Shares:              acc.shares,
			CostBasisExFeeIDR:   int64(math.Round(acc.costExFeeIDR)),
			CostBasisInclFeeIDR: int64(math.Round(acc.costInclFeeIDR)),
			FillCount:           acc.fillCount,
		})
	}
	return out, nil
}

// AttributionsByStrategy returns every completed round-trip recorded for
// strategyID, in entry order. The attribution table carries strategy_id but
// not sink_kind (see the schema in store.go); a caller reconciling against
// one sink kind should already know which strategy_id(s) ran under it.
func (s *PostTradeStore) AttributionsByStrategy(strategyID string) ([]AttributionRow, error) {
	rows, err := s.db.Query(`
		SELECT seq, symbol, strategy_id, entry_recv_ns, exit_recv_ns, gross_pnl_bps, net_pnl_bps, entry_order_id, exit_order_id, hold_calendar_days
		FROM attribution
		WHERE strategy_id = ?
		ORDER BY seq
	`, strategyID)
	if err != nil {
		return nil, fmt.Errorf("attributions by strategy: query: %w", err)
	}
	defer rows.Close()

	var out []AttributionRow
	for rows.Next() {
		var r AttributionRow
		if err := rows.Scan(&r.Seq, &r.Symbol, &r.StrategyID, &r.EntryRecvNs, &r.ExitRecvNs, &r.GrossPnlBps, &r.NetPnlBps, &r.EntryOrderID, &r.ExitOrderID, &r.HoldCalendarDays); err != nil {
			return nil, fmt.Errorf("attributions by strategy: scan: %w", err)
		}
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("attributions by strategy: iterate: %w", err)
	}
	return out, nil
}
