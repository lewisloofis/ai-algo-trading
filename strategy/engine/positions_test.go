package engine

import (
	"strconv"
	"testing"
)

// insertFilledOrder writes one order_intent + fill pair under sinkKind,
// seeding the store the way a real Run would, and returns the order id it
// generated so a caller can reference it (e.g. as an AttributionRow's
// EntryOrderID).
func insertFilledOrder(t *testing.T, store *PostTradeStore, seq *int64, sinkKind, symbol, side string, priceIDR int64, lots int, feeIDR int64, recvNs int64) string {
	t.Helper()
	*seq++
	orderID := symbol + "-" + side + "-" + strconv.FormatInt(*seq, 10)
	if err := store.InsertIntent(IntentRow{
		Seq: *seq, RecvNs: recvNs, StrategyID: "test", Symbol: symbol, Side: side,
		PriceIDR: priceIDR, Lots: lots, OrderID: orderID, SinkKind: sinkKind,
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	*seq++
	if err := store.InsertFill(FillRow{
		Seq: *seq, RecvNs: recvNs, OrderID: orderID, FillPriceIDR: priceIDR, FillLots: lots,
		FeeIDR: float64(feeIDR), SlippageBps: 0, SinkKind: sinkKind,
	}); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}
	return orderID
}

func TestDerivedPositions_SingleBuy(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9200, 10, 13800, 1)

	got, err := store.DerivedPositions("live", "")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("DerivedPositions = %+v, want 1 entry", got)
	}
	p := got[0]
	if p.Symbol != "BBCA" {
		t.Errorf("Symbol = %q, want BBCA", p.Symbol)
	}
	if p.Shares != 1000 {
		t.Errorf("Shares = %v, want 1000 (10 lots * 100)", p.Shares)
	}
	if p.CostBasisExFeeIDR != 9200*1000 {
		t.Errorf("CostBasisExFeeIDR = %d, want %d", p.CostBasisExFeeIDR, 9200*1000)
	}
	if p.CostBasisInclFeeIDR != 9200*1000+13800 {
		t.Errorf("CostBasisInclFeeIDR = %d, want %d", p.CostBasisInclFeeIDR, 9200*1000+13800)
	}
	if p.FillCount != 1 {
		t.Errorf("FillCount = %d, want 1", p.FillCount)
	}
}

func TestDerivedPositions_BuyThenPartialSell_MovingAverageCostBasis(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	// Buy 1000 shares @ 9000, then sell 400 shares. Remaining 600 shares
	// should carry the same average cost per share as before the sell.
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 10, 13500, 1)
	insertFilledOrder(t, store, &seq, "live", "BBCA", "sell", 9500, 4, 9500, 2)

	got, err := store.DerivedPositions("live", "")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("DerivedPositions = %+v, want 1 entry", got)
	}
	p := got[0]
	if p.Shares != 600 {
		t.Fatalf("Shares = %v, want 600", p.Shares)
	}
	// avg cost ex-fee before sell = 9000/share; 600 remaining * 9000 = 5,400,000
	if p.CostBasisExFeeIDR != 5400000 {
		t.Errorf("CostBasisExFeeIDR = %d, want 5400000", p.CostBasisExFeeIDR)
	}
}

func TestDerivedPositions_FullyClosedPositionIsOmitted(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 10, 13500, 1)
	insertFilledOrder(t, store, &seq, "live", "BBCA", "sell", 9500, 10, 23750, 2)

	got, err := store.DerivedPositions("live", "")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("DerivedPositions = %+v, want none (fully closed)", got)
	}
}

func TestDerivedPositions_FiltersBySinkKind(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	insertFilledOrder(t, store, &seq, "paper", "BBCA", "buy", 9000, 10, 13500, 1)
	insertFilledOrder(t, store, &seq, "live", "TLKM", "buy", 3000, 5, 2250, 2)

	got, err := store.DerivedPositions("live", "")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 1 || got[0].Symbol != "TLKM" {
		t.Fatalf("DerivedPositions(\"live\") = %+v, want only TLKM", got)
	}

	paperOnly, err := store.DerivedPositions("paper", "")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(paperOnly) != 1 || paperOnly[0].Symbol != "BBCA" {
		t.Fatalf("DerivedPositions(\"paper\") = %+v, want only BBCA", paperOnly)
	}
}

func TestDerivedPositions_NoFillsReturnsEmpty(t *testing.T) {
	store := newTestStore(t)
	got, err := store.DerivedPositions("live", "")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("DerivedPositions = %+v, want none", got)
	}
}

func TestOrderIDs_FiltersBySinkKindAndReturnsASet(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9200, 10, 13800, 1)
	insertFilledOrder(t, store, &seq, "live", "TLKM", "sell", 3100, 5, 1937, 2)
	insertFilledOrder(t, store, &seq, "paper", "AAAA", "buy", 500, 1, 75, 3)

	got, err := store.OrderIDs("live", "")
	if err != nil {
		t.Fatalf("OrderIDs: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("OrderIDs(live) = %+v, want 2 entries", got)
	}
	if _, ok := got["BBCA-buy-1"]; !ok {
		t.Errorf("OrderIDs(live) missing BBCA-buy-1: %+v", got)
	}
	if _, ok := got["TLKM-sell-3"]; !ok {
		t.Errorf("OrderIDs(live) missing TLKM-sell-3: %+v", got)
	}
	if _, ok := got["AAAA-buy-5"]; ok {
		t.Errorf("OrderIDs(live) unexpectedly contains a paper order id: %+v", got)
	}
}

func TestOrderIDs_NoOrdersReturnsEmptySet(t *testing.T) {
	store := newTestStore(t)

	got, err := store.OrderIDs("live", "")
	if err != nil {
		t.Fatalf("OrderIDs: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("OrderIDs = %+v, want empty", got)
	}
}

func TestAttributionsByStrategy_FiltersAndOrders(t *testing.T) {
	store := newTestStore(t)
	rows := []AttributionRow{
		{Seq: 1, Symbol: "AAA", StrategyID: "s1", EntryRecvNs: 1, ExitRecvNs: 2, GrossPnlBps: 10, NetPnlBps: 5, EntryOrderID: "e1", ExitOrderID: "x1", HoldCalendarDays: 1},
		{Seq: 2, Symbol: "BBB", StrategyID: "s2", EntryRecvNs: 3, ExitRecvNs: 4, GrossPnlBps: 20, NetPnlBps: 15, EntryOrderID: "e2", ExitOrderID: "x2", HoldCalendarDays: 1},
		{Seq: 3, Symbol: "CCC", StrategyID: "s1", EntryRecvNs: 5, ExitRecvNs: 6, GrossPnlBps: 30, NetPnlBps: 25, EntryOrderID: "e3", ExitOrderID: "x3", HoldCalendarDays: 1},
	}
	for _, r := range rows {
		if err := store.InsertAttribution(r); err != nil {
			t.Fatalf("InsertAttribution: %v", err)
		}
	}

	got, err := store.AttributionsByStrategy("s1")
	if err != nil {
		t.Fatalf("AttributionsByStrategy: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("AttributionsByStrategy(s1) = %+v, want 2 rows", got)
	}
	if got[0].Symbol != "AAA" || got[1].Symbol != "CCC" {
		t.Errorf("got symbols [%s, %s], want [AAA, CCC] in seq order", got[0].Symbol, got[1].Symbol)
	}
}

func TestRealizedPnLIDRByDate_SumsMatchingRow(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	entryOrderID := insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 10, 13500, 100)

	seq++
	if err := store.InsertAttribution(AttributionRow{
		Seq: seq, Symbol: "BBCA", StrategyID: "s1",
		EntryRecvNs: 100, ExitRecvNs: 200,
		GrossPnlBps: 100, NetPnlBps: 80,
		EntryOrderID: entryOrderID, ExitOrderID: "exit1",
		HoldCalendarDays: 1, AsOfDate: "2026-09-15",
	}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	// notional = 9000 IDR * 10 lots * 100 shares/lot = 9,000,000 IDR
	// 80 bps of that = 72,000 IDR
	if want := int64(72000); got != want {
		t.Errorf("RealizedPnLIDRByDate = %d, want %d", got, want)
	}
}

func TestRealizedPnLIDRByDate_SumsMultipleRoundTrips(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	e1 := insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 10, 13500, 100)
	e2 := insertFilledOrder(t, store, &seq, "live", "TLKM", "buy", 3000, 5, 2250, 300)

	seq++
	if err := store.InsertAttribution(AttributionRow{
		Seq: seq, Symbol: "BBCA", StrategyID: "s1", EntryRecvNs: 100, ExitRecvNs: 200,
		GrossPnlBps: 100, NetPnlBps: 80, EntryOrderID: e1, ExitOrderID: "x1",
		HoldCalendarDays: 1, AsOfDate: "2026-09-15",
	}); err != nil {
		t.Fatalf("InsertAttribution 1: %v", err)
	}
	seq++
	if err := store.InsertAttribution(AttributionRow{
		Seq: seq, Symbol: "TLKM", StrategyID: "s1", EntryRecvNs: 300, ExitRecvNs: 400,
		GrossPnlBps: -50, NetPnlBps: -60, EntryOrderID: e2, ExitOrderID: "x2",
		HoldCalendarDays: 1, AsOfDate: "2026-09-15",
	}); err != nil {
		t.Fatalf("InsertAttribution 2: %v", err)
	}

	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	// row 1: 9000*10*100=9,000,000 notional, 80bps -> 72,000
	// row 2: 3000*5*100=1,500,000 notional, -60bps -> -9,000
	if want := int64(72000 - 9000); got != want {
		t.Errorf("RealizedPnLIDRByDate = %d, want %d", got, want)
	}
}

func TestRealizedPnLIDRByDate_ExcludesOtherDates(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	entryOrderID := insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 10, 13500, 100)
	seq++
	if err := store.InsertAttribution(AttributionRow{
		Seq: seq, Symbol: "BBCA", StrategyID: "s1", EntryRecvNs: 100, ExitRecvNs: 200,
		GrossPnlBps: 100, NetPnlBps: 80, EntryOrderID: entryOrderID, ExitOrderID: "x1",
		HoldCalendarDays: 1, AsOfDate: "2026-09-14",
	}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	if got != 0 {
		t.Errorf("RealizedPnLIDRByDate = %d, want 0 (attribution row is dated 2026-09-14)", got)
	}
}

func TestRealizedPnLIDRByDate_FiltersBySinkKind(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	// Entry fill recorded under "paper" — a paper run's closed trip must
	// never be counted against a live session's loss limit.
	entryOrderID := insertFilledOrder(t, store, &seq, "paper", "BBCA", "buy", 9000, 10, 13500, 100)
	seq++
	if err := store.InsertAttribution(AttributionRow{
		Seq: seq, Symbol: "BBCA", StrategyID: "s1", EntryRecvNs: 100, ExitRecvNs: 200,
		GrossPnlBps: 100, NetPnlBps: 80, EntryOrderID: entryOrderID, ExitOrderID: "x1",
		HoldCalendarDays: 1, AsOfDate: "2026-09-15",
	}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	if got != 0 {
		t.Errorf("RealizedPnLIDRByDate(live) = %d, want 0 (entry fill is sink_kind=paper)", got)
	}
}

func TestRealizedPnLIDRByDate_OpenPositionContributesNothing(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	// A fill with no closing attribution row: an open position, which must
	// contribute nothing to realized PnL — that is the unrealized half's job.
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 10, 13500, 100)

	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	if got != 0 {
		t.Errorf("RealizedPnLIDRByDate = %d, want 0 (no closed round-trip)", got)
	}
}

func TestRealizedPnLIDRByDate_NoAttributionsReturnsZero(t *testing.T) {
	store := newTestStore(t)
	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	if got != 0 {
		t.Errorf("RealizedPnLIDRByDate = %d, want 0 on an empty store", got)
	}
}

// TestRealizedPnLIDRByDate_DisambiguatesSharedOrderIDByRecvNs guards the join
// key choice: joining on entry_order_id alone would sum every fill ever
// recorded under that order_id. Here two fill rows share one order_id (as a
// live order's partial-then-final fills would) at different recv_ns; only
// the one matching the attribution's entry_recv_ns must be counted.
func TestRealizedPnLIDRByDate_DisambiguatesSharedOrderIDByRecvNs(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	const orderID = "BBCA-buy-shared"

	seq++
	if err := store.InsertIntent(IntentRow{
		Seq: seq, RecvNs: 100, StrategyID: "test", Symbol: "BBCA", Side: "buy",
		PriceIDR: 9000, Lots: 10, OrderID: orderID, SinkKind: "live",
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	seq++
	if err := store.InsertFill(FillRow{
		Seq: seq, RecvNs: 100, OrderID: orderID, FillPriceIDR: 9000, FillLots: 10,
		FeeIDR: 13500, SinkKind: "live",
	}); err != nil {
		t.Fatalf("InsertFill (entry): %v", err)
	}
	seq++
	// A second fill sharing the same order_id at a different recv_ns — must
	// NOT be folded into the entry leg's notional.
	if err := store.InsertFill(FillRow{
		Seq: seq, RecvNs: 150, OrderID: orderID, FillPriceIDR: 9100, FillLots: 5,
		FeeIDR: 6825, SinkKind: "live",
	}); err != nil {
		t.Fatalf("InsertFill (unrelated, shared order_id): %v", err)
	}

	seq++
	if err := store.InsertAttribution(AttributionRow{
		Seq: seq, Symbol: "BBCA", StrategyID: "s1", EntryRecvNs: 100, ExitRecvNs: 200,
		GrossPnlBps: 100, NetPnlBps: 80, EntryOrderID: orderID, ExitOrderID: "x1",
		HoldCalendarDays: 1, AsOfDate: "2026-09-15",
	}); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	got, err := store.RealizedPnLIDRByDate("live", "2026-09-15")
	if err != nil {
		t.Fatalf("RealizedPnLIDRByDate: %v", err)
	}
	// Only the recv_ns=100 fill (10 lots @ 9000) should count:
	// notional = 9,000,000, 80bps -> 72,000.
	if want := int64(72000); got != want {
		t.Errorf("RealizedPnLIDRByDate = %d, want %d (must not double-count the shared order_id's other fill)", got, want)
	}
}

// TestDerivedPositionsAndOrderIDs_FilterByRunAccount pins the plan 18 §3.4
// attribution fix: a store holding fills from runs against two different
// accounts must report each account's positions and order ids only. Before
// this, account-recon against the core book counted the sleeve's fills.
// Account numbers are synthetic.
func TestDerivedPositionsAndOrderIDs_FilterByRunAccount(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	if err := store.BeginRun(RunRow{RunID: "run-a", StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: "X", Account: "TEST-SLEEVE"}); err != nil {
		t.Fatalf("BeginRun a: %v", err)
	}
	sleeveID := insertFilledOrder(t, store, &seq, "live", "GOTO", "buy", 27, 1, 4, 1)
	if err := store.BeginRun(RunRow{RunID: "run-b", StartedNs: 2, Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: "X", Account: "TEST-CORE"}); err != nil {
		t.Fatalf("BeginRun b: %v", err)
	}
	coreID := insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9200, 2, 2760, 2)

	for _, tc := range []struct {
		account, wantSymbol, wantID string
	}{
		{"TEST-SLEEVE", "GOTO", sleeveID},
		{"TEST-CORE", "BBCA", coreID},
	} {
		got, err := store.DerivedPositions("live", tc.account)
		if err != nil {
			t.Fatalf("DerivedPositions(%s): %v", tc.account, err)
		}
		if len(got) != 1 || got[0].Symbol != tc.wantSymbol {
			t.Errorf("DerivedPositions(live, %s) = %+v, want only %s", tc.account, got, tc.wantSymbol)
		}
		ids, err := store.OrderIDs("live", tc.account)
		if err != nil {
			t.Fatalf("OrderIDs(%s): %v", tc.account, err)
		}
		if _, ok := ids[tc.wantID]; len(ids) != 1 || !ok {
			t.Errorf("OrderIDs(live, %s) = %+v, want only %s", tc.account, ids, tc.wantID)
		}
	}

	none, err := store.DerivedPositions("live", "TEST-OTHER")
	if err != nil {
		t.Fatalf("DerivedPositions(other): %v", err)
	}
	if len(none) != 0 {
		t.Errorf("DerivedPositions(live, TEST-OTHER) = %+v, want none", none)
	}
}

// TestDerivedPositions_SameOrderIDInTwoRunsIsNotDoubleCounted: rebalance
// order ids are deterministic (rebalance-<SYM>-<date>), so two runs sharing
// one store file can each record an intent with the same id. A fill must
// join only its own run's intent, or it is counted once per matching intent.
func TestDerivedPositions_SameOrderIDInTwoRunsIsNotDoubleCounted(t *testing.T) {
	store := newTestStore(t)
	for i, run := range []string{"run-a", "run-b"} {
		if err := store.BeginRun(RunRow{RunID: run, StartedNs: int64(i + 1), Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: "X", Account: "TEST-SLEEVE"}); err != nil {
			t.Fatalf("BeginRun %s: %v", run, err)
		}
		if err := store.InsertIntent(IntentRow{Seq: int64(10*i + 1), RecvNs: 1, StrategyID: "s", Symbol: "GOTO", Side: "buy", PriceIDR: 27, Lots: 1, OrderID: "rebalance-GOTO-2026-10-13", SinkKind: "live"}); err != nil {
			t.Fatalf("InsertIntent %s: %v", run, err)
		}
	}
	// Only run-b's order filled.
	if err := store.InsertFill(FillRow{Seq: 12, RecvNs: 2, OrderID: "rebalance-GOTO-2026-10-13", FillPriceIDR: 27, FillLots: 1, FeeIDR: 4, SinkKind: "live"}); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}

	got, err := store.DerivedPositions("live", "TEST-SLEEVE")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 1 || got[0].Shares != 100 {
		t.Fatalf("DerivedPositions = %+v, want GOTO 100 shares (one fill, counted once)", got)
	}
}

func TestFillAccounts_ListsDistinctAccountsWithFills(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	for _, r := range []struct{ run, account, sink string }{
		{"run-a", "TEST-SLEEVE", "live"},
		{"run-b", "test-sleeve", "live"},
		{"run-c", "", "paper"},
	} {
		if err := store.BeginRun(RunRow{RunID: r.run, StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: r.sink, StrategyID: "s", InputSpec: "X", Account: r.account}); err != nil {
			t.Fatalf("BeginRun: %v", err)
		}
		insertFilledOrder(t, store, &seq, r.sink, "GOTO", "buy", 27, 1, 4, 1)
	}
	got, err := store.FillAccounts("live")
	if err != nil {
		t.Fatalf("FillAccounts: %v", err)
	}
	if len(got) != 2 || got[0] != "TEST-SLEEVE" || got[1] != "test-sleeve" {
		t.Fatalf("FillAccounts(live) = %v, want [TEST-SLEEVE test-sleeve]", got)
	}
}
