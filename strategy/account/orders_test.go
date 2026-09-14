package account

import (
	"context"
	"net/http"
	"testing"
)

func TestClient_OpenOrders_ParsesBareArrayEnvelope(t *testing.T) {
	// Confirmed live: unlike the portfolio/balance endpoints, this
	// response's `data` field is a bare JSON array, not an object.
	srv, _ := serveFixture(t, http.StatusOK, "testdata/open_orders.json")
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	orders, err := c.OpenOrders(context.Background())
	if err != nil {
		t.Fatalf("OpenOrders: %v", err)
	}
	if len(orders) != 3 {
		t.Fatalf("OpenOrders = %d entries, want 3", len(orders))
	}
	if orders[0].Symbol != "ZZZZ" || orders[0].Side != "SIDE_SELL" || orders[0].StatusText != "ACTIVE" {
		t.Errorf("orders[0] = %+v, want ZZZZ/SIDE_SELL/ACTIVE", orders[0])
	}
	if orders[0].Qty.LotOpen != 25 {
		t.Errorf("orders[0].Qty.LotOpen = %v, want 25", orders[0].Qty.LotOpen)
	}
	if !orders[0].GTC.IsGTC {
		t.Error("orders[0].GTC.IsGTC = false, want true")
	}
}

func TestClient_OpenOrders_EmptyArrayIsNotAnError(t *testing.T) {
	srv := serveJSON(t, http.StatusOK, `{"message":"OK","data":[]}`)
	c := New(srv.URL, &fakeTokenSource{token: "t"}, srv.Client())

	orders, err := c.OpenOrders(context.Background())
	if err != nil {
		t.Fatalf("OpenOrders: %v", err)
	}
	if len(orders) != 0 {
		t.Errorf("OpenOrders = %+v, want none", orders)
	}
}

func TestReservedSellShares(t *testing.T) {
	orders := []Order{
		{Symbol: "ZZZZ", Side: "SIDE_SELL", StatusText: "ACTIVE", Qty: OrderQty{LotOpen: 25}},
		{Symbol: "ZZZZ", Side: "SIDE_SELL", StatusText: "ACTIVE", Qty: OrderQty{LotOpen: 5}}, // second resting sell, same symbol: sums
		{Symbol: "AAAA", Side: "SIDE_BUY", StatusText: "ACTIVE", Qty: OrderQty{LotOpen: 5}},  // buy: excluded
		{Symbol: "YYYY", Side: "SIDE_SELL", StatusText: "FILLED", Qty: OrderQty{LotOpen: 0}}, // not ACTIVE: excluded
	}

	got := ReservedSellShares(orders)

	if got["ZZZZ"] != 3000 { // (25+5) lots * 100
		t.Errorf(`got["ZZZZ"] = %v, want 3000`, got["ZZZZ"])
	}
	if got["AAAA"] != 0 {
		t.Errorf(`got["AAAA"] = %v, want 0 (a buy order reserves nothing)`, got["AAAA"])
	}
	if got["YYYY"] != 0 {
		t.Errorf(`got["YYYY"] = %v, want 0 (not ACTIVE)`, got["YYYY"])
	}
	if _, ok := got["YYYY"]; ok {
		t.Error(`got["YYYY"] present in map, want absent (no ACTIVE sell for this symbol)`)
	}
}

func TestReservedSellShares_Empty(t *testing.T) {
	got := ReservedSellShares(nil)
	if len(got) != 0 {
		t.Errorf("ReservedSellShares(nil) = %+v, want empty", got)
	}
}
