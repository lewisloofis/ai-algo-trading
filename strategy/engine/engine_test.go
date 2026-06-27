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
