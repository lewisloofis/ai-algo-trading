package feed_test

import (
	"math"
	"testing"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/sbproto"
)

// ---------- Raw protobuf helpers (fixed64-safe) ----------

// appendVarBytes appends a varint-encoded uint64 to buf.
func appendVarBytes(buf []byte, v uint64) []byte {
	for v >= 0x80 {
		buf = append(buf, byte(v)|0x80)
		v >>= 7
	}
	return append(buf, byte(v))
}

// appendFixed64Bytes appends 8 little-endian bytes of v to buf.
func appendFixed64Bytes(buf []byte, v uint64) []byte {
	for i := 0; i < 8; i++ {
		buf = append(buf, byte(v))
		v >>= 8
	}
	return buf
}

// lenDelim appends a length-delimited field (WireBytes) to buf.
func lenDelim(buf []byte, field int, data []byte) []byte {
	buf = appendVarBytes(buf, uint64(field<<3|sbproto.WireBytes))
	buf = appendVarBytes(buf, uint64(len(data)))
	return append(buf, data...)
}

// buildRTMsg constructs a raw RunningTrade sub-message byte slice.
// Fields encoded: stock(2), price(3,fixed64), volume(4,fixed64),
// action(5,varint), trade_number(9,varint).
func buildRTMsg(symbol string, price, volume float64, action int, tradeNum int32) []byte {
	var buf []byte
	buf = lenDelim(buf, 2, []byte(symbol)) // stock

	buf = appendVarBytes(buf, uint64(3<<3|sbproto.WireFixed64)) // price tag
	buf = appendFixed64Bytes(buf, math.Float64bits(price))

	buf = appendVarBytes(buf, uint64(4<<3|sbproto.WireFixed64)) // volume tag
	buf = appendFixed64Bytes(buf, math.Float64bits(volume))

	buf = appendVarBytes(buf, uint64(5<<3|sbproto.WireVarint)) // action tag
	buf = appendVarBytes(buf, uint64(action))

	buf = appendVarBytes(buf, uint64(9<<3|sbproto.WireVarint)) // trade_number tag
	buf = appendVarBytes(buf, uint64(tradeNum))
	return buf
}

// TestDecodeWrapSingleRunningTrade verifies that a single running_trade frame
// (field 1 of WebsocketWrapMessageChannel) is decoded to Kind="running_trade"
// with one Trade entry.
func TestDecodeWrapSingleRunningTrade(t *testing.T) {
	t.Parallel()

	rtBytes := buildRTMsg("BBCA", 8750.0, 500.0, 1 /*BUY*/, 14201)
	// Wrap as field 1 (single running_trade) of WebsocketWrapMessageChannel
	frame := lenDelim(nil, 1, rtBytes)

	d, err := feed.DecodeWrap(frame)
	if err != nil {
		t.Fatalf("DecodeWrap error: %v", err)
	}
	if d.Kind != "running_trade" {
		t.Errorf("Kind: got %q want running_trade", d.Kind)
	}
	if d.Symbol != "BBCA" {
		t.Errorf("Symbol: got %q want BBCA", d.Symbol)
	}
	if len(d.Trades) != 1 {
		t.Fatalf("Trades length: got %d want 1", len(d.Trades))
	}
	tr := d.Trades[0]
	if tr.Symbol != "BBCA" {
		t.Errorf("Trade.Symbol: got %q want BBCA", tr.Symbol)
	}
	if tr.Price != 8750.0 {
		t.Errorf("Trade.Price: got %v want 8750", tr.Price)
	}
	if tr.Volume != 500.0 {
		t.Errorf("Trade.Volume: got %v want 500", tr.Volume)
	}
	if tr.Action != 1 {
		t.Errorf("Trade.Action: got %d want 1 (BUY)", tr.Action)
	}
	if tr.Side != "B" {
		t.Errorf("Trade.Side: got %q want B", tr.Side)
	}
	if tr.TradeNumber != 14201 {
		t.Errorf("Trade.TradeNumber: got %d want 14201", tr.TradeNumber)
	}
}

// TestDecodeWrapRunningTradeBatch verifies that a running_trade_batch frame
// (field 8 of WebsocketWrapMessageChannel) is decoded to the correct N trades.
func TestDecodeWrapRunningTradeBatch(t *testing.T) {
	t.Parallel()

	type entry struct {
		symbol   string
		price    float64
		volume   float64
		action   int
		tradeNum int32
	}
	entries := []entry{
		{"BBCA", 8750.0, 200.0, 1, 100},
		{"BBCA", 8750.0, 500.0, 2, 101},
		{"BBCA", 8775.0, 100.0, 1, 102},
	}

	// Build RunningTradeBatch: repeated RunningTrade batch = 1
	var batchBuf []byte
	for _, e := range entries {
		batchBuf = lenDelim(batchBuf, 1, buildRTMsg(e.symbol, e.price, e.volume, e.action, e.tradeNum))
	}

	// Wrap as field 8 (running_trade_batch) of WebsocketWrapMessageChannel
	frame := lenDelim(nil, 8, batchBuf)

	d, err := feed.DecodeWrap(frame)
	if err != nil {
		t.Fatalf("DecodeWrap error: %v", err)
	}
	if d.Kind != "running_trade" {
		t.Errorf("Kind: got %q want running_trade", d.Kind)
	}
	if len(d.Trades) != len(entries) {
		t.Fatalf("Trades length: got %d want %d", len(d.Trades), len(entries))
	}
	for i, e := range entries {
		tr := d.Trades[i]
		if tr.Symbol != e.symbol {
			t.Errorf("[%d] Symbol: got %q want %q", i, tr.Symbol, e.symbol)
		}
		if tr.Price != e.price {
			t.Errorf("[%d] Price: got %v want %v", i, tr.Price, e.price)
		}
		if tr.TradeNumber != e.tradeNum {
			t.Errorf("[%d] TradeNumber: got %d want %d", i, tr.TradeNumber, e.tradeNum)
		}
	}
	// Verify side mapping for first two entries (BUY=B, SELL=S).
	if d.Trades[0].Side != "B" {
		t.Errorf("[0] Side: got %q want B", d.Trades[0].Side)
	}
	if d.Trades[1].Side != "S" {
		t.Errorf("[1] Side: got %q want S", d.Trades[1].Side)
	}
}
