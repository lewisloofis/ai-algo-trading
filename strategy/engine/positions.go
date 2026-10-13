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
//
// The join also matches run_id: rebalance order ids are deterministic per
// symbol and day, so two runs sharing a file can record the same order_id,
// and an order_id-only join counts each fill once per matching intent.
//
// account is load-bearing the same way: only fills from runs whose
// run.account equals it are counted, so a store holding sleeve fills never
// reports them as the core book's (plan 18 §3.4). Rows with no run header,
// and runs recorded before run.account existed, count as account "".
func (s *PostTradeStore) DerivedPositions(sinkKind, account string) ([]DerivedPosition, error) {
	rows, err := s.db.Query(`
		SELECT oi.symbol, oi.side, f.fill_price_idr, f.fill_lots, f.fee_idr
		FROM fill f
		JOIN order_intent oi ON oi.order_id = f.order_id AND oi.run_id = f.run_id
		LEFT JOIN run r ON r.run_id = oi.run_id
		WHERE f.sink_kind = ? AND oi.sink_kind = ? AND COALESCE(r.account, '') = ?
		ORDER BY oi.symbol, f.recv_ns, f.seq
	`, sinkKind, sinkKind, account)
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

// OrderIDs returns the set of order_id values recorded in order_intent under
// sinkKind — this bot's own orders, as far as the store knows. It exists
// because /order/v2/list (strategy/account.Client.OpenOrders) returns every
// sub-account's orders with no account attribution at all: order_id is the
// only reliable discriminator between "ours" and "some other account's",
// and the store is the only place that knows which order_ids are ours (see
// strategy/account.OwnReservedSellShares). Filtered by run account exactly
// as DerivedPositions is.
func (s *PostTradeStore) OrderIDs(sinkKind, account string) (map[string]struct{}, error) {
	rows, err := s.db.Query(`
		SELECT oi.order_id FROM order_intent oi
		LEFT JOIN run r ON r.run_id = oi.run_id
		WHERE oi.sink_kind = ? AND COALESCE(r.account, '') = ?
	`, sinkKind, account)
	if err != nil {
		return nil, fmt.Errorf("order ids: query: %w", err)
	}
	defer rows.Close()

	ids := make(map[string]struct{})
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("order ids: scan: %w", err)
		}
		ids[id] = struct{}{}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("order ids: iterate: %w", err)
	}
	return ids, nil
}

// AttemptedWindows returns, for every order_intent row recorded under
// strategyID and sinkKind, "<jakarta-date>|<symbol>" — one entry per
// distinct pair. This is the store's answer to "which (trading day, symbol)
// pairs has this strategy already attempted", keyed in exactly the format
// strategies/rebalance.Strategy's own internal attempted map uses (window +
// "|" + symbol, where window is a Jakarta calendar date for a DailyAt
// schedule — see rebalance.Options.Attempted's doc comment): a caller seeds
// that field directly from this result with no reformatting.
//
// It exists for plan 18 §5 B4's restart-durability gap: ui_refs and the
// strategy's own "already attempted this window" bookkeeping are in-memory
// and a crash discards them, so a same-day restart could otherwise resubmit
// an order that already reached the broker. order_intent already records
// symbol and recv_ns for every attempt — win or lose, filled or rejected —
// so no new column or table is needed to answer this; it only needed a
// reader. Note engine/store.go's own convention is ONE STORE FILE PER RUN
// (StorePathConvention): after a restart, "today's attempts" live in the
// PRIOR run's store file, not the new run's own (which starts empty) — a
// caller recovering state after a crash must read this from the earlier
// file(s), not the one it is about to write to.
func (s *PostTradeStore) AttemptedWindows(strategyID, sinkKind string) (map[string]bool, error) {
	rows, err := s.db.Query(
		`SELECT DISTINCT symbol, recv_ns FROM order_intent WHERE strategy_id = ? AND sink_kind = ?`,
		strategyID, sinkKind,
	)
	if err != nil {
		return nil, fmt.Errorf("attempted windows: query: %w", err)
	}
	defer rows.Close()

	out := make(map[string]bool)
	for rows.Next() {
		var symbol string
		var recvNs int64
		if err := rows.Scan(&symbol, &recvNs); err != nil {
			return nil, fmt.Errorf("attempted windows: scan: %w", err)
		}
		out[JakartaDate(recvNs)+"|"+symbol] = true
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("attempted windows: iterate: %w", err)
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

// RealizedPnLIDRByDate re-denominates one trading day's realized PnL into
// whole IDR, for sinkKind's rows only. asOfDate must be a Jakarta (WIB)
// calendar date string as produced by JakartaDate — the exchange's date, not
// the host's: a UTC host disagrees with WIB for the seven hours after
// Jakarta midnight, so a host-local date would silently misfile a trade
// against the wrong trading day (see JakartaDate's own doc comment, which
// carries the same reasoning for as_of_date generally).
//
// account.ComputeRealizedPnL deliberately reports realized PnL in basis
// points only, because AttributionRow carries no IDR field — inventing one
// there would let a second PnL figure drift from what the fill simulator
// actually recorded. This is the honest bridge to IDR instead: it
// re-denominates the bps the store already has against the entry leg's
// actual notional (fill_price_idr * fill_lots * 100 shares/lot), rather than
// recomputing PnL from prices in a second place.
//
// An open position contributes nothing here — attribution rows exist only
// for CLOSED round-trips. That is not a bug; it is the division of labour
// with the unrealized half, which must come from the broker's live
// portfolio figures instead (see account.DailyPnL).
//
// The join is on (entry_order_id, entry_recv_ns), not entry_order_id alone.
// recordRoundTrip's openLeg is replaced wholesale by any later same-side
// fill sharing that order_id (see its doc comment), so entry_recv_ns is what
// actually pins down which single fill row the leg's price/lots/fee came
// from; joining on entry_order_id alone would sum every fill ever recorded
// under that order_id and overstate the notional if more than one exists
// (today's PaperSink only ever produces one fill per order, but nothing in
// this schema guarantees that for a live, partially-filled order).
//
// sink_kind is read off the fill row, filtering both the join and the
// result to it: a paper run's rows must never be counted against a live
// session's loss limit, and the attribution table itself carries no
// sink_kind column to filter on directly.
func (s *PostTradeStore) RealizedPnLIDRByDate(sinkKind, asOfDate string) (int64, error) {
	rows, err := s.db.Query(`
		SELECT a.net_pnl_bps, f.fill_price_idr, f.fill_lots
		FROM attribution a
		JOIN fill f ON f.order_id = a.entry_order_id AND f.recv_ns = a.entry_recv_ns AND f.run_id = a.run_id
		WHERE a.as_of_date = ? AND f.sink_kind = ?
	`, asOfDate, sinkKind)
	if err != nil {
		return 0, fmt.Errorf("realized pnl idr by date: query: %w", err)
	}
	defer rows.Close()

	var sumIDR float64
	for rows.Next() {
		var netPnlBps float64
		var fillPriceIDR int64
		var fillLots int
		if err := rows.Scan(&netPnlBps, &fillPriceIDR, &fillLots); err != nil {
			return 0, fmt.Errorf("realized pnl idr by date: scan: %w", err)
		}
		notionalIDR := float64(fillPriceIDR) * float64(fillLots) * sharesPerLot
		sumIDR += netPnlBps / 10000 * notionalIDR
	}
	if err := rows.Err(); err != nil {
		return 0, fmt.Errorf("realized pnl idr by date: iterate: %w", err)
	}
	return int64(math.Round(sumIDR)), nil
}

// FillAccounts returns the distinct run accounts, sorted, under which this
// store holds sinkKind fills (rows with no run header count as ""). It lets
// a caller that reads positions for one account notice fills recorded under
// another, rather than having DerivedPositions' account filter silently drop
// them.
func (s *PostTradeStore) FillAccounts(sinkKind string) ([]string, error) {
	rows, err := s.db.Query(`
		SELECT DISTINCT COALESCE(r.account, '')
		FROM fill f
		LEFT JOIN run r ON r.run_id = f.run_id
		WHERE f.sink_kind = ?
		ORDER BY 1
	`, sinkKind)
	if err != nil {
		return nil, fmt.Errorf("fill accounts: query: %w", err)
	}
	defer rows.Close()
	var accounts []string
	for rows.Next() {
		var a string
		if err := rows.Scan(&a); err != nil {
			return nil, fmt.Errorf("fill accounts: scan: %w", err)
		}
		accounts = append(accounts, a)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("fill accounts: iterate: %w", err)
	}
	return accounts, nil
}
