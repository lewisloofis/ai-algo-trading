package engine

import (
	"strconv"
	"testing"
)

// insertFilledOrder writes one order_intent + fill pair under sinkKind,
// seeding the store the way a real Run would.
func insertFilledOrder(t *testing.T, store *PostTradeStore, seq *int64, sinkKind, symbol, side string, priceIDR int64, lots int, feeIDR int64, recvNs int64) {
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
		FeeIDR: feeIDR, SlippageBps: 0, SinkKind: sinkKind,
	}); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}
}

func TestDerivedPositions_SingleBuy(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9200, 10, 13800, 1)

	got, err := store.DerivedPositions("live")
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

	got, err := store.DerivedPositions("live")
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

	got, err := store.DerivedPositions("live")
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

	got, err := store.DerivedPositions("live")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 1 || got[0].Symbol != "TLKM" {
		t.Fatalf("DerivedPositions(\"live\") = %+v, want only TLKM", got)
	}

	paperOnly, err := store.DerivedPositions("paper")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(paperOnly) != 1 || paperOnly[0].Symbol != "BBCA" {
		t.Fatalf("DerivedPositions(\"paper\") = %+v, want only BBCA", paperOnly)
	}
}

func TestDerivedPositions_NoFillsReturnsEmpty(t *testing.T) {
	store := newTestStore(t)
	got, err := store.DerivedPositions("live")
	if err != nil {
		t.Fatalf("DerivedPositions: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("DerivedPositions = %+v, want none", got)
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
