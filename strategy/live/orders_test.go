package live

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"testing"
)

func readBody(t *testing.T, r *http.Request) map[string]any {
	t.Helper()
	data, err := io.ReadAll(r.Body)
	if err != nil {
		t.Fatalf("read request body: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("unmarshal request body %s: %v", data, err)
	}
	return m
}

func TestPlace_Buy_HitsBuyPathWithCorrectBody(t *testing.T) {
	var gotMethod string
	var gotBody map[string]any
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: func(w http.ResponseWriter, r *http.Request) {
			gotMethod = r.Method
			gotBody = readBody(t, r)
			jsonHandler(http.StatusOK, `{"data":{"order_id":"999"}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)

	orderID, err := c.Place(context.Background(), PlaceRequest{
		UIRef: "A1ref", Symbol: "BBCA", Side: "buy", PriceIDR: 9500, Shares: 300, BoardType: "RG",
	})
	if err != nil {
		t.Fatalf("Place: %v", err)
	}
	if orderID != "999" {
		t.Errorf("Place order id = %q, want 999", orderID)
	}
	if gotMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if gotBody["ui_ref"] != "A1ref" {
		t.Errorf("ui_ref = %v, want A1ref", gotBody["ui_ref"])
	}
	if gotBody["symbol"] != "BBCA" {
		t.Errorf("symbol = %v, want BBCA", gotBody["symbol"])
	}
	if gotBody["price"] != float64(9500) {
		t.Errorf("price = %v, want 9500", gotBody["price"])
	}
	if gotBody["shares"] != "300" {
		t.Errorf("shares = %v (%T), want string \"300\" (§1: shares is a string on buy/sell)", gotBody["shares"], gotBody["shares"])
	}
	if gotBody["board_type"] != "RG" {
		t.Errorf("board_type = %v, want RG", gotBody["board_type"])
	}
	if gotBody["is_gtc"] != false {
		t.Errorf("is_gtc = %v, want false", gotBody["is_gtc"])
	}
	if gotBody["platform_order_type"] != platformOrderTypeLimitDay {
		t.Errorf("platform_order_type = %v, want %s (never MARKET_FAK)", gotBody["platform_order_type"], platformOrderTypeLimitDay)
	}
}

func TestPlace_Sell_HitsSellPath(t *testing.T) {
	var hitSell bool
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathSell: func(w http.ResponseWriter, r *http.Request) {
			hitSell = true
			jsonHandler(http.StatusOK, `{"data":{"order_id":"1000"}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)

	if _, err := c.Place(context.Background(), PlaceRequest{
		UIRef: "A2ref", Symbol: "BBCA", Side: "sell", PriceIDR: 9500, Shares: 100, BoardType: "RG",
	}); err != nil {
		t.Fatalf("Place: %v", err)
	}
	if !hitSell {
		t.Error("sell order never reached /order/v2/sell")
	}
}

// TestPlace_InvalidSideSendsNothing guards Client.Place's "never default"
// rule: an unrecognised side must be rejected before any request is built,
// not silently routed to buy or sell.
func TestPlace_InvalidSideSendsNothing(t *testing.T) {
	var requests int
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy:  countingHandler(&requests, jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`)),
		pathSell: countingHandler(&requests, jsonHandler(http.StatusOK, `{"data":{"order_id":"1"}}`)),
	})
	c := newTestClient(t, srv)

	_, err := c.Place(context.Background(), PlaceRequest{
		UIRef: "A3ref", Symbol: "BBCA", Side: "BUY", PriceIDR: 9500, Shares: 100, BoardType: "RG",
	})
	if err == nil {
		t.Fatal("Place with side \"BUY\" (wrong case): got nil error, want an error")
	}
	if requests != 0 {
		t.Errorf("requests sent for an invalid side = %d, want 0", requests)
	}
}

func TestCancel_HitsCancelPathWithOrderIDAndUIRef(t *testing.T) {
	var gotBody map[string]any
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathCancel: func(w http.ResponseWriter, r *http.Request) {
			gotBody = readBody(t, r)
			jsonHandler(http.StatusOK, `{"data":{}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)

	if err := c.Cancel(context.Background(), "12345", "Acancelref"); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	if gotBody["order_id"] != "12345" {
		t.Errorf("order_id = %v, want 12345", gotBody["order_id"])
	}
	if gotBody["ui_ref"] != "Acancelref" {
		t.Errorf("ui_ref = %v, want Acancelref", gotBody["ui_ref"])
	}
}

func TestAmend_SharesIsNumericUnlikePlace(t *testing.T) {
	var gotBody map[string]any
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathAmend: func(w http.ResponseWriter, r *http.Request) {
			gotBody = readBody(t, r)
			jsonHandler(http.StatusOK, `{"data":{}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)

	err := c.Amend(context.Background(), AmendRequest{
		BrokerOrderID: "12345", UIRef: "Aamendref", Symbol: "BBCA", PriceIDR: 9550, Shares: 100, BoardType: "RG",
	})
	if err != nil {
		t.Fatalf("Amend: %v", err)
	}
	if gotBody["shares"] != float64(100) {
		t.Errorf("amend shares = %v (%T), want numeric 100 (§3: unlike buy/sell's string shares)", gotBody["shares"], gotBody["shares"])
	}
}

func TestDetail_SendsOrderIDQueryParam(t *testing.T) {
	var gotQuery string
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathDetail: func(w http.ResponseWriter, r *http.Request) {
			gotQuery = r.URL.Query().Get("order_id")
			jsonHandler(http.StatusOK, `{"data":{"order_id":"777","symbol":"BBCA","status_text":"OPEN"}}`)(w, r)
		},
	})
	c := newTestClient(t, srv)

	snap, err := c.Detail(context.Background(), "777")
	if err != nil {
		t.Fatalf("Detail: %v", err)
	}
	if gotQuery != "777" {
		t.Errorf("order_id query param = %q, want 777", gotQuery)
	}
	if snap.OrderID != "777" || snap.StatusText != "OPEN" {
		t.Errorf("Detail = %+v, want OrderID=777 StatusText=OPEN", snap)
	}
}

func TestList_DecodesEnvelopeArray(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathList: jsonHandler(http.StatusOK, `{"data":[
			{"order_id":"1","symbol":"BBCA","side":"SIDE_BUY","status_text":"OPEN","qty":{"lot_done":0,"lot_open":1,"lot_total":1}},
			{"order_id":"2","symbol":"TINS","side":"SIDE_SELL","status_text":"MATCH","qty":{"lot_done":2,"lot_open":0,"lot_total":2}}
		]}`),
	})
	c := newTestClient(t, srv)

	orders, err := c.List(context.Background())
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(orders) != 2 {
		t.Fatalf("List = %d entries, want 2", len(orders))
	}
	if orders[0].Symbol != "BBCA" || orders[1].Symbol != "TINS" {
		t.Errorf("List = %+v, want BBCA then TINS", orders)
	}
}

func TestList_EmptyResultsIsNotAnError(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathList: jsonHandler(http.StatusOK, `{"data":[]}`),
	})
	c := newTestClient(t, srv)

	orders, err := c.List(context.Background())
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(orders) != 0 {
		t.Errorf("List = %d entries, want 0", len(orders))
	}
}

func TestPlace_NoOrderIDInResponseIsAnError(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusOK, `{"data":{}}`),
	})
	c := newTestClient(t, srv)

	_, err := c.Place(context.Background(), PlaceRequest{
		UIRef: "A4ref", Symbol: "BBCA", Side: "buy", PriceIDR: 9500, Shares: 100, BoardType: "RG",
	})
	if err == nil {
		t.Fatal("Place with no order_id in response: got nil error, want an error")
	}
}

func TestPlace_DuplicateOrderIsClassified(t *testing.T) {
	srv := routeServer(t, map[string]http.HandlerFunc{
		pathBuy: jsonHandler(http.StatusConflict, `{"message":"ErrorDuplicateOrder"}`),
	})
	c := newTestClient(t, srv)

	_, err := c.Place(context.Background(), PlaceRequest{
		UIRef: "A5ref", Symbol: "BBCA", Side: "buy", PriceIDR: 9500, Shares: 100, BoardType: "RG",
	})
	if !errors.Is(err, ErrDuplicateOrder) {
		t.Fatalf("Place error = %v, want errors.Is(err, ErrDuplicateOrder)", err)
	}
}
