package engine

import (
	"context"
	"database/sql"
	"path/filepath"
	"strconv"
	"testing"

	"collector/feed"
	"collector/segment"
)

func bookEvent(symbol, side string, px int, recvNs int64) MarketEvent {
	body := "#O|" + symbol + "|" + side + "|" + strconv.Itoa(px) + ";1;500;|1"
	d := &feed.Decoded{Kind: "orderbook", Symbol: symbol, Body: body}
	return MarketEvent{RecvNs: recvNs, Kind: EventOrderbook, Symbol: symbol, Decoded: d}
}

type intentQuote struct {
	bid, ask, last, quoteNs, ageMs sql.NullInt64
	mid                            sql.NullFloat64
}

func readIntentQuote(t *testing.T, s *PostTradeStore, orderID string) intentQuote {
	t.Helper()
	var q intentQuote
	err := s.QueryRow(`SELECT arrival_bid_idr, arrival_ask_idr, arrival_mid_idr, arrival_last_idr, arrival_quote_ns, arrival_quote_age_ms FROM order_intent WHERE order_id = ?`, orderID).
		Scan(&q.bid, &q.ask, &q.mid, &q.last, &q.quoteNs, &q.ageMs)
	if err != nil {
		t.Fatalf("read intent quote: %v", err)
	}
	return q
}

// submitOnce submits one buy on the event whose RecvNs == submitNs.
type submitOnce struct {
	submitNs int64
	orderID  string
}

func (s *submitOnce) OnEvent(ctx context.Context, e MarketEvent, eng Engine) {
	if e.RecvNs == s.submitNs {
		_, _ = eng.SubmitOrder(ctx, OrderIntent{Symbol: "AAA", Side: "buy", PriceIDR: 1000, Lots: 1, OrderID: s.orderID, BoardType: BoardRG})
	}
}
func (s *submitOnce) OnFill(ctx context.Context, f FillRecord) {}

func TestRun_RecordsArrivalQuoteAtSubmit_PaperSink(t *testing.T) {
	store := newTestStore(t)
	src := feedEvents(
		bookEvent("AAA", "BID", 1000, 1_000_000_000),
		bookEvent("AAA", "OFFER", 1005, 2_000_000_000),
		liveEvent("AAA", 1002, 3_000_000_000),
		liveEvent("AAA", 1003, 5_000_000_000), // submit on this event
	)
	if _, err := Run(context.Background(), src, &submitOnce{submitNs: 5_000_000_000, orderID: "o1"}, NewPaperSink(), store, "t"); err != nil {
		t.Fatalf("Run: %v", err)
	}
	q := readIntentQuote(t, store, "o1")
	if q.bid.Int64 != 1000 || q.ask.Int64 != 1005 || !q.mid.Valid || q.mid.Float64 != 1002.5 {
		t.Fatalf("bid/ask/mid = %v/%v/%v", q.bid, q.ask, q.mid)
	}
	if q.last.Int64 != 1003 {
		t.Fatalf("last = %v, want 1003", q.last)
	}
	if q.quoteNs.Int64 != 1_000_000_000 || q.ageMs.Int64 != 4000 {
		t.Fatalf("quote_ns/age_ms = %v/%v, want 1e9/4000 (older side)", q.quoteNs, q.ageMs)
	}
}

func TestRun_RecordsArrivalQuoteAtSubmit_AsyncSink(t *testing.T) {
	store := newTestStore(t)
	src := feedEvents(
		bookEvent("AAA", "BID", 2000, 10),
		bookEvent("AAA", "OFFER", 2010, 20),
		liveEvent("AAA", 2005, 30),
	)
	if _, err := Run(context.Background(), src, &submitOnce{submitNs: 30, orderID: "o1"}, newFakeAsyncSink("fake-live"), store, "t"); err != nil {
		t.Fatalf("Run: %v", err)
	}
	q := readIntentQuote(t, store, "o1")
	if q.mid.Float64 != 2005 || q.bid.Int64 != 2000 || q.ask.Int64 != 2010 || q.last.Int64 != 2005 {
		t.Fatalf("quote = %+v", q)
	}
}

func TestRun_NoQuoteStoresNull(t *testing.T) {
	store := newTestStore(t)
	src := feedEvents(MarketEvent{RecvNs: 7, Kind: EventRunningTrade, Symbol: "AAA"})
	if _, err := Run(context.Background(), src, &submitOnce{submitNs: 7, orderID: "o1"}, newFakeAsyncSink("fake-live"), store, "t"); err != nil {
		t.Fatalf("Run: %v", err)
	}
	q := readIntentQuote(t, store, "o1")
	if q.bid.Valid || q.ask.Valid || q.mid.Valid || q.last.Valid || q.quoteNs.Valid || q.ageMs.Valid {
		t.Fatalf("expected all NULL, got %+v", q)
	}
}

func TestRun_LivePriceNeverSeedsBidAsk(t *testing.T) {
	store := newTestStore(t)
	src := feedEvents(liveEvent("AAA", 1002, 1), liveEvent("AAA", 1002, 2))
	if _, err := Run(context.Background(), src, &submitOnce{submitNs: 2, orderID: "o1"}, newFakeAsyncSink("fake-live"), store, "t"); err != nil {
		t.Fatalf("Run: %v", err)
	}
	q := readIntentQuote(t, store, "o1")
	if q.bid.Valid || q.ask.Valid || q.mid.Valid {
		t.Fatalf("liveprice must not fabricate a book: %+v", q)
	}
	if q.last.Int64 != 1002 {
		t.Fatalf("last = %v", q.last)
	}
}

func TestQuoteBook_OneSidedCrossedAndReset(t *testing.T) {
	q := newQuoteBook()
	q.observe(bookEvent("AAA", "BID", 1000, 10))
	s := q.snapshot("AAA", 20_000_000)
	if s == nil || !s.BidIDR.Valid || s.AskIDR.Valid || s.MidIDR.Valid {
		t.Fatalf("one-sided: %+v", s)
	}
	q.observe(bookEvent("AAA", "OFFER", 990, 11)) // crossed
	if s = q.snapshot("AAA", 20); s != nil {
		t.Fatalf("crossed book with no last must record nothing: %+v", s)
	}
	q.observe(bookEvent("AAA", "OFFER", 1010, 12))
	if s = q.snapshot("AAA", 20); s == nil || s.MidIDR.Float64 != 1005 {
		t.Fatalf("mid = %+v", s)
	}
	q.observe(MarketEvent{Kind: EventControl, Control: &segment.ControlEvent{Event: "RECONNECT"}})
	if q.snapshot("AAA", 30) != nil {
		t.Fatal("RECONNECT must clear the quote")
	}
}

func TestMigrate_OldStoreGainsArrivalColumnsAndStillWrites(t *testing.T) {
	path := filepath.Join(t.TempDir(), "old.db")
	old, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = old.Exec(`
CREATE TABLE order_intent (seq INTEGER PRIMARY KEY, recv_ns INTEGER NOT NULL, strategy_id TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, price_idr INTEGER NOT NULL, lots INTEGER NOT NULL, order_id TEXT NOT NULL, sink_kind TEXT NOT NULL DEFAULT 'paper', run_id TEXT NOT NULL DEFAULT '');
INSERT INTO order_intent (seq, recv_ns, strategy_id, symbol, side, price_idr, lots, order_id) VALUES (1, 5, 'orb', 'OLD', 'buy', 100, 1, 'old-1');`)
	if err != nil {
		t.Fatal(err)
	}
	old.Close()

	store, err := OpenStore(path)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	defer store.Close()
	q := readIntentQuote(t, store, "old-1")
	if q.bid.Valid || q.mid.Valid || q.last.Valid {
		t.Fatalf("pre-existing row must read NULL, got %+v", q)
	}
	if err := store.InsertIntent(IntentRow{Seq: 2, RecvNs: 6, StrategyID: "orb", Symbol: "NEW", Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "new-1", SinkKind: "paper",
		Arrival: &ArrivalQuote{MidIDR: sql.NullFloat64{Float64: 100.5, Valid: true}}}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	if got := readIntentQuote(t, store, "new-1"); got.mid.Float64 != 100.5 {
		t.Fatalf("mid = %v", got.mid)
	}
	// nil Arrival stays NULL.
	if err := store.InsertIntent(IntentRow{Seq: 3, RecvNs: 7, StrategyID: "orb", Symbol: "NEW", Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "new-2", SinkKind: "paper"}); err != nil {
		t.Fatalf("InsertIntent nil quote: %v", err)
	}
	if got := readIntentQuote(t, store, "new-2"); got.mid.Valid {
		t.Fatalf("nil Arrival must store NULL, got %+v", got)
	}
}
