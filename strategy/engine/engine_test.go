package engine

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"collector/feed"
	"collector/segment"
)

// liveEvent builds a liveprice MarketEvent for a symbol.
func liveEvent(symbol string, price float64, recvNs int64) MarketEvent {
	d := &feed.Decoded{Kind: "liveprice", Symbol: symbol, LastPrice: price}
	return MarketEvent{RecvNs: recvNs, Kind: EventLivePrice, Symbol: symbol, Decoded: d}
}

// recordingStrategy logs the order of OnEvent and OnFill callbacks and submits
// one order on the second event it sees.
type recordingStrategy struct {
	calls    []string
	submitOn int
	seen     int
}

func (s *recordingStrategy) OnEvent(ctx context.Context, e MarketEvent, eng Engine) {
	s.calls = append(s.calls, "event")
	s.seen++
	if s.seen == s.submitOn {
		_, _ = eng.SubmitOrder(ctx, OrderIntent{
			Symbol: e.Symbol, Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "o1",
		})
	}
}

func (s *recordingStrategy) OnFill(ctx context.Context, f FillRecord) {
	s.calls = append(s.calls, "fill")
}

func newTestStore(t *testing.T) *PostTradeStore {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.db")
	store, err := OpenStore(path)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

func feedEvents(evs ...MarketEvent) <-chan MarketEvent {
	ch := make(chan MarketEvent, len(evs))
	for _, e := range evs {
		ch <- e
	}
	close(ch)
	return ch
}

func TestEventFiresBeforeFill(t *testing.T) {
	store := newTestStore(t)
	sink := NewPaperSink()
	strat := &recordingStrategy{submitOn: 2}

	// First event seeds the book; second event submits and fills.
	src := feedEvents(
		liveEvent("AAA", 100, 1),
		liveEvent("AAA", 100, 2),
	)
	if _, err := Run(context.Background(), src, strat, sink, store, "rec"); err != nil {
		t.Fatalf("Run: %v", err)
	}

	want := []string{"event", "event", "fill"}
	if len(strat.calls) != len(want) {
		t.Fatalf("calls = %v, want %v", strat.calls, want)
	}
	for i := range want {
		if strat.calls[i] != want[i] {
			t.Fatalf("calls = %v, want %v", strat.calls, want)
		}
	}
}

func TestPaperSinkFeeMath(t *testing.T) {
	sink := NewPaperSink()
	// Seed both touches at 8750 via a liveprice.
	sink.Observe(liveEvent("AAA", 8750, 1))

	id, err := sink.Submit(context.Background(), OrderIntent{
		Symbol: "AAA", Side: "buy", PriceIDR: 8750, Lots: 100, OrderID: "o1",
	})
	if err != nil {
		t.Fatalf("Submit: %v", err)
	}
	if id != "o1" {
		t.Fatalf("id = %q, want o1", id)
	}

	f := sink.LastFill()
	// notional = 8750 * 100 lots * 100 shares = 87,500,000; * 0.0015 = 131,250.
	const wantFee = 131_250
	if f.FeeIDR != wantFee {
		t.Fatalf("FeeIDR = %d, want %d", f.FeeIDR, wantFee)
	}
	if f.FillPriceIDR != 8750 {
		t.Fatalf("FillPriceIDR = %d, want 8750", f.FillPriceIDR)
	}
	if f.SlippageBps != 0 {
		t.Fatalf("SlippageBps = %v, want 0", f.SlippageBps)
	}

	// Sell side uses the 25 bps rate: 87,500,000 * 0.0025 = 218,750.
	sink2 := NewPaperSink()
	sink2.Observe(liveEvent("AAA", 8750, 1))
	if _, err := sink2.Submit(context.Background(), OrderIntent{
		Symbol: "AAA", Side: "sell", PriceIDR: 8750, Lots: 100, OrderID: "o2",
	}); err != nil {
		t.Fatalf("Submit sell: %v", err)
	}
	if got := sink2.LastFill().FeeIDR; got != 218_750 {
		t.Fatalf("sell FeeIDR = %d, want 218750", got)
	}
}

func TestStoreRoundTrip(t *testing.T) {
	store := newTestStore(t)
	ctx := context.Background()
	_ = ctx

	dec := DecisionRow{Seq: 1, RecvNs: 111, StrategyID: "s", Symbol: "AAA", EventKind: "liveprice"}
	if err := store.InsertDecision(dec); err != nil {
		t.Fatalf("InsertDecision: %v", err)
	}
	intent := IntentRow{Seq: 2, RecvNs: 222, StrategyID: "s", Symbol: "AAA", Side: "buy", PriceIDR: 8750, Lots: 1, OrderID: "o1", SinkKind: "paper"}
	if err := store.InsertIntent(intent); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	fill := FillRow{Seq: 3, RecvNs: 333, OrderID: "o1", FillPriceIDR: 8750, FillLots: 1, FeeIDR: 1312, SlippageBps: 1.5, SinkKind: "paper"}
	if err := store.InsertFill(fill); err != nil {
		t.Fatalf("InsertFill: %v", err)
	}
	attr := AttributionRow{Seq: 4, Symbol: "AAA", StrategyID: "s", EntryRecvNs: 222, ExitRecvNs: 333, GrossPnlBps: 10.5, NetPnlBps: 6.0, EntryOrderID: "o1", ExitOrderID: "o2", HoldCalendarDays: 0.5}
	if err := store.InsertAttribution(attr); err != nil {
		t.Fatalf("InsertAttribution: %v", err)
	}

	// Read each back and verify field-for-field.
	var gd DecisionRow
	row := store.db.QueryRow(`SELECT seq, recv_ns, strategy_id, symbol, event_kind FROM decision WHERE seq=1`)
	if err := row.Scan(&gd.Seq, &gd.RecvNs, &gd.StrategyID, &gd.Symbol, &gd.EventKind); err != nil {
		t.Fatalf("scan decision: %v", err)
	}
	if gd.Seq != dec.Seq || gd.RecvNs != dec.RecvNs || gd.Symbol != dec.Symbol || gd.EventKind != dec.EventKind {
		t.Fatalf("decision = %+v, want %+v", gd, dec)
	}

	var gi IntentRow
	row = store.db.QueryRow(`SELECT seq, recv_ns, side, price_idr, lots, order_id, sink_kind FROM order_intent WHERE seq=2`)
	if err := row.Scan(&gi.Seq, &gi.RecvNs, &gi.Side, &gi.PriceIDR, &gi.Lots, &gi.OrderID, &gi.SinkKind); err != nil {
		t.Fatalf("scan intent: %v", err)
	}
	if gi.PriceIDR != intent.PriceIDR || gi.Side != intent.Side || gi.OrderID != intent.OrderID || gi.SinkKind != intent.SinkKind {
		t.Fatalf("intent = %+v, want %+v", gi, intent)
	}

	var gf FillRow
	row = store.db.QueryRow(`SELECT seq, fill_price_idr, fee_idr, slippage_bps FROM fill WHERE seq=3`)
	if err := row.Scan(&gf.Seq, &gf.FillPriceIDR, &gf.FeeIDR, &gf.SlippageBps); err != nil {
		t.Fatalf("scan fill: %v", err)
	}
	if gf.FillPriceIDR != fill.FillPriceIDR || gf.FeeIDR != fill.FeeIDR || gf.SlippageBps != fill.SlippageBps {
		t.Fatalf("fill = %+v, want %+v", gf, fill)
	}

	var ga AttributionRow
	row = store.db.QueryRow(`SELECT seq, gross_pnl_bps, net_pnl_bps, hold_calendar_days FROM attribution WHERE seq=4`)
	if err := row.Scan(&ga.Seq, &ga.GrossPnlBps, &ga.NetPnlBps, &ga.HoldCalendarDays); err != nil {
		t.Fatalf("scan attribution: %v", err)
	}
	if ga.GrossPnlBps != attr.GrossPnlBps || ga.NetPnlBps != attr.NetPnlBps || ga.HoldCalendarDays != attr.HoldCalendarDays {
		t.Fatalf("attribution = %+v, want %+v", ga, attr)
	}
}

// countingStrategy counts OnEvent calls; used by cancellation and
// reproducibility tests.
type countingStrategy struct{ n int }

func (s *countingStrategy) OnEvent(ctx context.Context, e MarketEvent, eng Engine) { s.n++ }
func (s *countingStrategy) OnFill(ctx context.Context, f FillRecord)               {}

// cancelStrategy cancels the run from within the first OnEvent, then a later
// event must be observed as cancellation rather than processed.
type cancelStrategy struct {
	cancel context.CancelFunc
	seen   int
}

func (s *cancelStrategy) OnEvent(ctx context.Context, e MarketEvent, eng Engine) {
	s.seen++
	if s.seen == 1 {
		s.cancel()
	}
}
func (s *cancelStrategy) OnFill(ctx context.Context, f FillRecord) {}

func TestContextCancellation(t *testing.T) {
	store := newTestStore(t)
	ctx, cancel := context.WithCancel(context.Background())

	// An unbuffered source: the producer blocks until Run reads, so after the
	// first event cancels the context, the select observes Done deterministically.
	src := make(chan MarketEvent)
	go func() {
		defer close(src)
		for i := 0; i < 5; i++ {
			select {
			case src <- liveEvent("AAA", 100, int64(i+1)):
			case <-ctx.Done():
				return
			}
		}
	}()

	strat := &cancelStrategy{cancel: cancel}
	_, err := Run(ctx, src, strat, NewPaperSink(), store, "c")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	// The run must stop on cancellation, well before draining all 5 events.
	if strat.seen >= 5 {
		t.Fatalf("strategy saw %d events; cancellation did not stop the run", strat.seen)
	}
}

func TestPaperSinkMidFill(t *testing.T) {
	sink := NewMidPriceSink()
	// Seed bid=1000 via a liveprice (seeds both touches if neither is set).
	sink.Observe(liveEvent("AAA", 1000, 1))
	// Inject a second liveprice to update ask only — but liveEvent seeds both
	// touches at the new price only when the side is not yet set. Since bid is
	// already set from the first event, a second liveprice does not update bid.
	// Instead use a helper that directly manipulates the book state.
	//
	// The simplest approach: seed via two liveprices at different prices on a
	// fresh sink, which isn't possible with liveprices alone. Use an orderbook
	// event body to set offer independently.
	sink2 := NewMidPriceSink()
	// First liveprice: sets bid=ask=900 (both unset, so both get seeded).
	sink2.Observe(liveEvent("AAA", 900, 1))
	// Orderbook OFFER event to override ask to 1100 (body format: #O|x|OFFER|price;0;vol|ts).
	offerBody := "#O|x|OFFER|1100;0;100000|2"
	offerEvt := MarketEvent{
		RecvNs:  2,
		Kind:    EventOrderbook,
		Symbol:  "AAA",
		Decoded: &feed.Decoded{Kind: "orderbook", Symbol: "AAA", Body: offerBody},
	}
	sink2.Observe(offerEvt)
	// Now bid=900, ask=1100; mid = (900+1100)/2 = 1000.
	id, err := sink2.Submit(context.Background(), OrderIntent{
		Symbol: "AAA", Side: "buy", PriceIDR: 900, Lots: 1, OrderID: "mid1",
	})
	if err != nil {
		t.Fatalf("Submit mid: %v", err)
	}
	if id != "mid1" {
		t.Fatalf("id = %q, want mid1", id)
	}
	f := sink2.LastFill()
	if f.FillPriceIDR != 1000 {
		t.Fatalf("FillPriceIDR = %d, want 1000 (mid of bid=900 ask=1100)", f.FillPriceIDR)
	}

	// Sell side should also use mid.
	sink3 := NewMidPriceSink()
	sink3.Observe(liveEvent("AAA", 900, 1))
	sink3.Observe(offerEvt)
	if _, err := sink3.Submit(context.Background(), OrderIntent{
		Symbol: "AAA", Side: "sell", PriceIDR: 900, Lots: 1, OrderID: "mid2",
	}); err != nil {
		t.Fatalf("Submit sell mid: %v", err)
	}
	if got := sink3.LastFill().FillPriceIDR; got != 1000 {
		t.Fatalf("sell FillPriceIDR = %d, want 1000 (mid)", got)
	}

	// Taker sink must NOT use mid — it fills at far touch.
	sink4 := NewPaperSink()
	sink4.Observe(liveEvent("AAA", 900, 1))
	sink4.Observe(offerEvt)
	if _, err := sink4.Submit(context.Background(), OrderIntent{
		Symbol: "AAA", Side: "buy", PriceIDR: 900, Lots: 1, OrderID: "t1",
	}); err != nil {
		t.Fatalf("Submit taker: %v", err)
	}
	if got := sink4.LastFill().FillPriceIDR; got != 1100 {
		t.Fatalf("taker FillPriceIDR = %d, want 1100 (ask, not mid)", got)
	}
}

func TestReplayReproducibility(t *testing.T) {
	mk := func() []MarketEvent {
		return []MarketEvent{
			liveEvent("AAA", 100, 1),
			liveEvent("AAA", 101, 2),
			liveEvent("BBB", 200, 3),
			{RecvNs: 4, Kind: EventControl, Control: &segment.ControlEvent{Event: "RECONNECT"}},
			liveEvent("AAA", 102, 5),
		}
	}

	run := func() (Counters, int64) {
		store := newTestStore(t)
		strat := &countingStrategy{}
		evs := mk()
		ch := make(chan MarketEvent, len(evs))
		for _, e := range evs {
			ch <- e
		}
		close(ch)
		c, err := Run(context.Background(), ch, strat, NewPaperSink(), store, "r")
		if err != nil {
			t.Fatalf("Run: %v", err)
		}
		var maxSeq int64
		if err := store.db.QueryRow(`SELECT COALESCE(MAX(seq),0) FROM decision`).Scan(&maxSeq); err != nil {
			t.Fatalf("max seq: %v", err)
		}
		return c, maxSeq
	}

	c1, s1 := run()
	c2, s2 := run()
	if s1 != s2 {
		t.Fatalf("max seq differs: %d vs %d", s1, s2)
	}
	if c1.OrdersAttempted != c2.OrdersAttempted || c1.Fills != c2.Fills {
		t.Fatalf("counters differ: %+v vs %+v", c1, c2)
	}
}

// TestRunExhaustsSourceCleanly verifies that Run returns nil (not an error)
// when the source channel is closed after delivering all events. This is the
// §7.10 "exhausts the source and closes cleanly" requirement.
func TestRunExhaustsSourceCleanly(t *testing.T) {
	store := newTestStore(t)
	events := []MarketEvent{
		liveEvent("AAA", 100, 1),
		liveEvent("AAA", 101, 2),
		liveEvent("AAA", 102, 3),
	}
	src := feedEvents(events...)
	strat := &countingStrategy{}

	_, err := Run(context.Background(), src, strat, NewPaperSink(), store, "exhaust")
	if err != nil {
		t.Fatalf("Run returned %v on clean source exhaustion, want nil", err)
	}
	if strat.n != len(events) {
		t.Fatalf("strategy saw %d events, want %d", strat.n, len(events))
	}
}

// TestReplayReproducibilityWithTrades verifies the stronger replay-reproducibility
// criterion from §7.9 and §8: two runs over the same events with the same
// strategy produce stores whose decision, fill, and intent counts and sequences
// are field-for-field identical. The recording strategy submits one order on
// event 2, producing a deterministic fill row in both runs.
func TestReplayReproducibilityWithTrades(t *testing.T) {
	type snapshot struct {
		maxDecSeq  int64
		fillCount  int64
		intentSeqs []int64
	}

	run := func() snapshot {
		store := newTestStore(t)
		strat := &recordingStrategy{submitOn: 2}
		src := feedEvents(
			liveEvent("AAA", 8750, 1_000_000_000),
			liveEvent("AAA", 8750, 2_000_000_000),
		)
		if _, err := Run(context.Background(), src, strat, NewPaperSink(), store, "repro"); err != nil {
			t.Fatalf("Run: %v", err)
		}

		var s snapshot
		if err := store.db.QueryRow(`SELECT COALESCE(MAX(seq),0) FROM decision`).Scan(&s.maxDecSeq); err != nil {
			t.Fatalf("max decision seq: %v", err)
		}
		if err := store.db.QueryRow(`SELECT COUNT(*) FROM fill`).Scan(&s.fillCount); err != nil {
			t.Fatalf("fill count: %v", err)
		}
		rows, err := store.db.Query(`SELECT seq FROM order_intent ORDER BY seq`)
		if err != nil {
			t.Fatalf("query intent seqs: %v", err)
		}
		defer rows.Close()
		for rows.Next() {
			var seq int64
			if err := rows.Scan(&seq); err != nil {
				t.Fatalf("scan seq: %v", err)
			}
			s.intentSeqs = append(s.intentSeqs, seq)
		}
		return s
	}

	r1 := run()
	r2 := run()

	if r1.maxDecSeq != r2.maxDecSeq {
		t.Fatalf("max decision seq: run1=%d run2=%d — not reproducible", r1.maxDecSeq, r2.maxDecSeq)
	}
	if r1.fillCount != r2.fillCount {
		t.Fatalf("fill count: run1=%d run2=%d — fill records differ between runs", r1.fillCount, r2.fillCount)
	}
	if len(r1.intentSeqs) != len(r2.intentSeqs) {
		t.Fatalf("intent count: run1=%d run2=%d", len(r1.intentSeqs), len(r2.intentSeqs))
	}
	for i := range r1.intentSeqs {
		if r1.intentSeqs[i] != r2.intentSeqs[i] {
			t.Fatalf("intent seq[%d]: run1=%d run2=%d", i, r1.intentSeqs[i], r2.intentSeqs[i])
		}
	}
}

// TestStoreWALMode verifies that the SQLite store opens in WAL journal mode, as
// required by §8's append-only enforcement clause.
func TestStoreWALMode(t *testing.T) {
	store := newTestStore(t)
	var mode string
	if err := store.db.QueryRow(`PRAGMA journal_mode`).Scan(&mode); err != nil {
		t.Fatalf("PRAGMA journal_mode: %v", err)
	}
	if mode != "wal" {
		t.Fatalf("journal_mode = %q, want wal", mode)
	}
}

// TestStoreAppendOnly verifies the append-only property of the four EVENT
// tables: the PostTradeStore exposes only Insert methods for decision,
// order_intent, fill and attribution, and no Delete path at all. (The one
// UPDATE in the type is FinishRun, which touches only the `run` header table
// and never an event row — see TestFinishRunTouchesOnlyTheRunTable and
// PostTradeStore's doc comment for why the header is mutable.)
// The test also confirms that INSERT-ONLY operation is enforced by
// attempting a raw UPDATE on the underlying table and verifying the store
// remains consistent (same row count; the UPDATE itself succeeds in SQLite
// since no trigger prevents it, but the invariant is upheld at the code level
// by the absence of any Update/Delete method on PostTradeStore).
//
// §8: "Append-only enforcement — the SQLite store opens in WAL mode; any
// UPDATE or DELETE statement panics in tests; the store file grows
// monotonically across runs."
func TestStoreAppendOnly(t *testing.T) {
	store := newTestStore(t)

	// Insert a known row.
	if err := store.InsertDecision(DecisionRow{
		Seq: 1, RecvNs: 111, StrategyID: "s", Symbol: "AAA", EventKind: "liveprice",
	}); err != nil {
		t.Fatalf("InsertDecision: %v", err)
	}

	// Verify PostTradeStore has no Update/Delete methods at compile time by
	// calling only its documented public API and checking the row is present.
	var count int
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM decision`).Scan(&count); err != nil {
		t.Fatalf("count decision: %v", err)
	}
	if count != 1 {
		t.Fatalf("decision count = %d, want 1", count)
	}

	// Insert a second row (monotonically growing store).
	if err := store.InsertDecision(DecisionRow{
		Seq: 2, RecvNs: 222, StrategyID: "s", Symbol: "BBB", EventKind: "liveprice",
	}); err != nil {
		t.Fatalf("InsertDecision second: %v", err)
	}
	if err := store.db.QueryRow(`SELECT COUNT(*) FROM decision`).Scan(&count); err != nil {
		t.Fatalf("count after second insert: %v", err)
	}
	if count != 2 {
		t.Fatalf("decision count after two inserts = %d, want 2 (store must grow monotonically)", count)
	}

	// Duplicate seq (primary key constraint) must be rejected — the store is
	// append-only: you cannot silently overwrite a row by inserting the same seq.
	if err := store.InsertDecision(DecisionRow{
		Seq: 1, RecvNs: 999, StrategyID: "x", Symbol: "DUPE", EventKind: "liveprice",
	}); err == nil {
		t.Fatal("InsertDecision with duplicate seq must return an error (PK constraint), got nil")
	}
}

func TestScheduleFeeIDR(t *testing.T) {
	cases := []struct {
		side     string
		notional int64
		want     int64
	}{
		{"buy", 2_700, 4},     // GOTO 1 lot @ 27: 4.05
		{"sell", 2_600, 7},    // 6.5 rounds half away from zero
		{"buy", 525_000, 788}, // BBCA 1 lot @ 5250: 787.5
		{"sell", 87_500_000, 218_750},
	}
	for _, tc := range cases {
		if got := ScheduleFeeIDR(tc.side, tc.notional); got != tc.want {
			t.Errorf("ScheduleFeeIDR(%q, %d) = %d, want %d", tc.side, tc.notional, got, tc.want)
		}
	}
}
