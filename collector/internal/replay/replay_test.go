package replay_test

import (
	"encoding/binary"
	"math"
	"os"
	"testing"

	"sb-algo-prototype/internal/replay"
	"sb-algo-prototype/internal/sbproto"
	"sb-algo-prototype/internal/segment"
)

// appendVarReplay appends a varint-encoded uint64 to buf.
func appendVarReplay(buf []byte, v uint64) []byte {
	for v >= 0x80 {
		buf = append(buf, byte(v)|0x80)
		v >>= 7
	}
	return append(buf, byte(v))
}

// appendFixed64Replay appends 8 little-endian bytes to buf.
func appendFixed64Replay(buf []byte, v uint64) []byte {
	for i := 0; i < 8; i++ {
		buf = append(buf, byte(v))
		v >>= 8
	}
	return buf
}

// lenDelimReplay appends a length-delimited (WireBytes) field to buf.
func lenDelimReplay(buf []byte, field int, data []byte) []byte {
	buf = appendVarReplay(buf, uint64(field<<3|sbproto.WireBytes))
	buf = appendVarReplay(buf, uint64(len(data)))
	return append(buf, data...)
}

// buildRTMsgReplay constructs a raw RunningTrade sub-message byte slice.
// Fields: stock(2), price(3,fixed64), volume(4,fixed64), action(5,varint).
func buildRTMsgReplay(symbol string, price, volume float64, action int) []byte {
	var buf []byte
	buf = lenDelimReplay(buf, 2, []byte(symbol))
	buf = appendVarReplay(buf, uint64(3<<3|sbproto.WireFixed64))
	buf = appendFixed64Replay(buf, math.Float64bits(price))
	buf = appendVarReplay(buf, uint64(4<<3|sbproto.WireFixed64))
	buf = appendFixed64Replay(buf, math.Float64bits(volume))
	buf = appendVarReplay(buf, uint64(5<<3|sbproto.WireVarint))
	buf = appendVarReplay(buf, uint64(action))
	return buf
}

// buildRunningTradeBatchFrame builds a WebsocketWrapMessageChannel frame
// containing a RunningTradeBatch (field 8) with the given symbols.
func buildRunningTradeBatchFrame(symbols []string) []byte {
	var batchBuf []byte
	for _, sym := range symbols {
		batchBuf = lenDelimReplay(batchBuf, 1, buildRTMsgReplay(sym, 1000.0, 100.0, 1))
	}
	return lenDelimReplay(nil, 8, batchBuf)
}

// buildOrderbookFrame returns a raw WebsocketWrapMessageChannel frame wrapping
// a consumer Orderbook sub-message with the given symbol, body, and sequence.
//
// WebsocketWrapMessageChannel: field 10 (message) = Orderbook
// consumer Orderbook: field 1 (string) = stock_code, field 2 (string) = body,
//
//	field 3 (varint) = sequence_number
func buildOrderbookFrame(symbol, body string, seq int64) []byte {
	var ob sbproto.Writer
	ob.String(1, symbol)
	ob.String(2, body)
	ob.Tag(3, sbproto.WireVarint)
	ob.Varint(uint64(seq))

	var wrap sbproto.Writer
	wrap.Message(10, ob.Bytes())
	return wrap.Bytes()
}

// buildLivePriceFrame returns a raw WebsocketWrapMessageChannel frame wrapping
// a consumer LivePrice sub-message.
//
// WebsocketWrapMessageChannel: field 9 (message) = LivePrice
// consumer LivePrice: field 1 (string) = stock_code, field 2 (fixed64) = lastprice
func buildLivePriceFrame(symbol string, lastPrice float64) []byte {
	// Build the LivePrice sub-message byte-by-byte. sbproto.Writer has no
	// WriteDouble helper, so we encode field 2 (wire type 1 = fixed64) manually:
	// tag byte = (2 << 3) | 1 = 0x11, then 8 bytes little-endian IEEE 754 double.
	var symW sbproto.Writer
	symW.String(1, symbol)

	var priceBuf [9]byte
	priceBuf[0] = (2 << 3) | 1 // tag: field 2, wire type 1 (fixed64)
	binary.LittleEndian.PutUint64(priceBuf[1:], math.Float64bits(lastPrice))

	lp := append(symW.Bytes(), priceBuf[:]...)

	var wrap sbproto.Writer
	wrap.Message(9, lp)
	return wrap.Bytes()
}

// writeTestSegment writes a sequence of records to a temp file and returns the path.
type segRecord struct {
	recType     byte
	timestampNs int64
	payload     []byte
}

func writeTestSegment(t *testing.T, records []segRecord) string {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "seg-*.binpb")
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	defer f.Close()

	w := segment.NewWriter(f)
	for _, rec := range records {
		switch rec.recType {
		case segment.RecordData:
			if err := w.WriteData(rec.timestampNs, rec.payload); err != nil {
				t.Fatalf("WriteData: %v", err)
			}
		case segment.RecordControl:
			if err := w.WriteControl(rec.timestampNs, rec.payload); err != nil {
				t.Fatalf("WriteControl: %v", err)
			}
		}
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}
	return f.Name()
}

// TestReplaySequence verifies that Replay calls the handler in record order
// with the correct Kind, Symbol, and RecvNs.
func TestReplaySequence(t *testing.T) {
	t.Parallel()

	const (
		ts1 = int64(1_700_000_001_000_000_000)
		ts2 = int64(1_700_000_002_000_000_000)
		ts3 = int64(1_700_000_003_000_000_000)
	)

	obFrame := buildOrderbookFrame("BBCA", "#O|BBCA|BID|8750;1;100;|1700000001", 42)
	lpFrame := buildLivePriceFrame("BMRI", 6100.0)

	ctrlPayload, err := segment.ConnectEvent("wss://test.example.com")
	if err != nil {
		t.Fatalf("build control event: %v", err)
	}

	segPath := writeTestSegment(t, []segRecord{
		{segment.RecordData, ts1, obFrame},
		{segment.RecordData, ts2, lpFrame},
		{segment.RecordControl, ts3, ctrlPayload},
	})

	type eventSummary struct {
		recvNs int64
		kind   replay.EventKind
		symbol string
	}

	var got []eventSummary
	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(e replay.MarketEvent) {
		got = append(got, eventSummary{e.RecvNs, e.Kind, e.Symbol})
	}); err != nil {
		t.Fatalf("Replay: %v", err)
	}

	want := []eventSummary{
		{ts1, replay.EventOrderbook, "BBCA"},
		{ts2, replay.EventLivePrice, "BMRI"},
		{ts3, replay.EventControl, ""},
	}

	if len(got) != len(want) {
		t.Fatalf("event count: got %d want %d", len(got), len(want))
	}
	for i, w := range want {
		g := got[i]
		if g.recvNs != w.recvNs {
			t.Errorf("[%d] RecvNs: got %d want %d", i, g.recvNs, w.recvNs)
		}
		if g.kind != w.kind {
			t.Errorf("[%d] Kind: got %q want %q", i, g.kind, w.kind)
		}
		if g.symbol != w.symbol {
			t.Errorf("[%d] Symbol: got %q want %q", i, g.symbol, w.symbol)
		}
	}
}

// TestReplayControlFields verifies that control event JSON is correctly
// unmarshalled into segment.ControlEvent.
func TestReplayControlFields(t *testing.T) {
	t.Parallel()

	const ts = int64(1_700_000_005_000_000_000)

	ctrlPayload, err := segment.ReconnectEvent(2, 1500)
	if err != nil {
		t.Fatalf("build reconnect event: %v", err)
	}

	segPath := writeTestSegment(t, []segRecord{
		{segment.RecordControl, ts, ctrlPayload},
	})

	var events []replay.MarketEvent
	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(e replay.MarketEvent) {
		events = append(events, e)
	}); err != nil {
		t.Fatalf("Replay: %v", err)
	}

	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	e := events[0]
	if e.Kind != replay.EventControl {
		t.Errorf("Kind: got %q want %q", e.Kind, replay.EventControl)
	}
	if e.Control == nil {
		t.Fatal("Control is nil")
	}
	if e.Control.Event != "RECONNECT" {
		t.Errorf("Control.Event: got %q want RECONNECT", e.Control.Event)
	}
	if e.Control.Attempt != 2 {
		t.Errorf("Control.Attempt: got %d want 2", e.Control.Attempt)
	}
	if e.Control.BackoffMs != 1500 {
		t.Errorf("Control.BackoffMs: got %d want 1500", e.Control.BackoffMs)
	}
}

// TestReplayTruncatedTail verifies that a crash-truncated trailing record does
// not cause a panic or error — the preceding complete records are delivered and
// Replay returns nil.
func TestReplayTruncatedTail(t *testing.T) {
	t.Parallel()

	const ts1 = int64(1_700_000_010_000_000_000)

	obFrame := buildOrderbookFrame("TLKM", "#O|TLKM|OFFER|3800;2;200;|1700000010", 99)
	segPath := writeTestSegment(t, []segRecord{
		{segment.RecordData, ts1, obFrame},
	})

	// Append a truncated second record: type byte + 3 of 8 timestamp bytes.
	f, err := os.OpenFile(segPath, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatalf("open for append: %v", err)
	}
	_, _ = f.Write([]byte{segment.RecordData, 0x00, 0x11, 0x22})
	f.Close()

	var events []replay.MarketEvent
	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(e replay.MarketEvent) {
		events = append(events, e)
	}); err != nil {
		t.Fatalf("Replay returned unexpected error on truncated segment: %v", err)
	}

	if len(events) != 1 {
		t.Fatalf("expected 1 complete event before truncation, got %d", len(events))
	}
	if events[0].Symbol != "TLKM" {
		t.Errorf("Symbol: got %q want TLKM", events[0].Symbol)
	}
}

// TestReplayMultipleSegments verifies that events from multiple segment files
// are delivered in segment order.
func TestReplayMultipleSegments(t *testing.T) {
	t.Parallel()

	const (
		ts1 = int64(1_700_000_020_000_000_000)
		ts2 = int64(1_700_000_021_000_000_000)
	)

	seg1 := writeTestSegment(t, []segRecord{
		{segment.RecordData, ts1, buildOrderbookFrame("ASII", "#O|ASII|BID|5000;1;50;|1700000020", 1)},
	})
	seg2 := writeTestSegment(t, []segRecord{
		{segment.RecordData, ts2, buildLivePriceFrame("ASII", 5050.0)},
	})

	var got []replay.EventKind
	if err := replay.Replay([]string{seg1, seg2}, replay.ModeFast, func(e replay.MarketEvent) {
		got = append(got, e.Kind)
	}); err != nil {
		t.Fatalf("Replay: %v", err)
	}

	want := []replay.EventKind{replay.EventOrderbook, replay.EventLivePrice}
	if len(got) != len(want) {
		t.Fatalf("event count: got %d want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("[%d] Kind: got %q want %q", i, got[i], want[i])
		}
	}
}

// TestReplayDecodedFields checks that Decoded is populated with correct values
// for orderbook data records.
func TestReplayDecodedFields(t *testing.T) {
	t.Parallel()

	const (
		ts  = int64(1_700_000_030_000_000_000)
		seq = int64(77)
	)

	body := "#O|BBRI|BID|4200;3;300;|1700000030"
	obFrame := buildOrderbookFrame("BBRI", body, seq)

	segPath := writeTestSegment(t, []segRecord{
		{segment.RecordData, ts, obFrame},
	})

	var events []replay.MarketEvent
	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(e replay.MarketEvent) {
		events = append(events, e)
	}); err != nil {
		t.Fatalf("Replay: %v", err)
	}

	if len(events) != 1 {
		t.Fatalf("expected 1 event, got %d", len(events))
	}
	e := events[0]
	if e.Decoded == nil {
		t.Fatal("Decoded is nil")
	}
	if e.Decoded.Symbol != "BBRI" {
		t.Errorf("Decoded.Symbol: got %q want BBRI", e.Decoded.Symbol)
	}
	if e.Decoded.SequenceNumber != seq {
		t.Errorf("Decoded.SequenceNumber: got %d want %d", e.Decoded.SequenceNumber, seq)
	}
	if e.Decoded.Body != body {
		t.Errorf("Decoded.Body: got %q want %q", e.Decoded.Body, body)
	}
}

// TestReplayRunningTradeBatchExpansion verifies that a single data record
// carrying a RunningTradeBatch with N trades yields N EventRunningTrade events,
// each with the correct per-trade Symbol set.
func TestReplayRunningTradeBatchExpansion(t *testing.T) {
	t.Parallel()

	const ts = int64(1_700_000_040_000_000_000)

	// Build a batch with 3 trades for different symbols.
	symbols := []string{"BBCA", "BMRI", "TLKM"}
	batchFrame := buildRunningTradeBatchFrame(symbols)

	segPath := writeTestSegment(t, []segRecord{
		{segment.RecordData, ts, batchFrame},
	})

	var events []replay.MarketEvent
	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(e replay.MarketEvent) {
		events = append(events, e)
	}); err != nil {
		t.Fatalf("Replay: %v", err)
	}

	if len(events) != len(symbols) {
		t.Fatalf("event count: got %d want %d (one per trade)", len(events), len(symbols))
	}
	for i, sym := range symbols {
		e := events[i]
		if e.Kind != replay.EventRunningTrade {
			t.Errorf("[%d] Kind: got %q want %q", i, e.Kind, replay.EventRunningTrade)
		}
		if e.Symbol != sym {
			t.Errorf("[%d] Symbol: got %q want %q", i, e.Symbol, sym)
		}
		if e.RecvNs != ts {
			t.Errorf("[%d] RecvNs: got %d want %d", i, e.RecvNs, ts)
		}
		if e.Decoded == nil || len(e.Decoded.Trades) != 1 {
			t.Errorf("[%d] expected exactly 1 trade in Decoded, got %v", i, e.Decoded)
		} else if e.Decoded.Trades[0].Symbol != sym {
			t.Errorf("[%d] Trade.Symbol: got %q want %q", i, e.Decoded.Trades[0].Symbol, sym)
		}
	}
}
