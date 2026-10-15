package engine

import "testing"

func TestBuyNotionalIDRByDate_CountsOnlyTodaysLiveBuysThatWereNotRejected(t *testing.T) {
	store := newTestStore(t)
	day := int64(1790039100000000000) // 2026-09-24 09:05 WIB
	yesterday := day - 24*3600*1_000_000_000
	date := JakartaDate(day)

	ins := func(seq int64, recv int64, side, sink, orderID string, price int64, lots int) {
		t.Helper()
		if err := store.InsertIntent(IntentRow{Seq: seq, RecvNs: recv, StrategyID: "s", Symbol: "AAAA", Side: side,
			PriceIDR: price, Lots: lots, OrderID: orderID, SinkKind: sink}); err != nil {
			t.Fatal(err)
		}
	}
	ins(1, day, "buy", "live", "o1", 1_000, 2)       // 200,000 counts
	ins(2, day, "buy", "live", "o2", 500, 1)         // 50,000 counts
	ins(3, day, "sell", "live", "o3", 9_000, 5)      // sell: never
	ins(4, day, "buy", "paper", "o4", 9_000, 5)      // other sink: never
	ins(5, yesterday, "buy", "live", "o5", 9_000, 5) // other day: never
	ins(6, day, "buy", "live", "o6", 9_000, 5)       // rejected below: consumed nothing
	if err := store.InsertOrderReject(RejectRow{Seq: 7, RecvNs: day, StrategyID: "s", Symbol: "AAAA", Side: "buy",
		PriceIDR: 9_000, Lots: 5, OrderID: "o6", Reason: "cap", SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}

	got, err := store.BuyNotionalIDRByDate("live", date)
	if err != nil {
		t.Fatalf("BuyNotionalIDRByDate: %v", err)
	}
	if got != 250_000 {
		t.Fatalf("got %d, want 250000", got)
	}
}

func TestBuyNotionalIDRByDate_EmptyStoreIsZero(t *testing.T) {
	got, err := newTestStore(t).BuyNotionalIDRByDate("live", "2026-09-24")
	if err != nil || got != 0 {
		t.Fatalf("got %d, %v", got, err)
	}
}
