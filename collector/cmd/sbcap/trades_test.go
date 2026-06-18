package main

import (
	"encoding/binary"
	"math"
	"os"
	"testing"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/replay"
	"sb-algo-prototype/internal/sbproto"
	"sb-algo-prototype/internal/segment"
)

// buildTradeFrameF encodes a single RunningTrade wrapped in a
// WebsocketWrapMessageChannel frame (field 1 = running_trade).
//
// RunningTrade fields used:
//
//	2: string stock (symbol)
//	3: double price  (WireFixed64)
//	4: double volume (WireFixed64)
//	5: TradeType action (WireVarint; 0=unspecified, 1=BUY→"B", 2=SELL→"S")
//	9: int32 trade_number (WireVarint)
func buildTradeFrameF(symbol string, price, vol float64, action, tradeNum int) []byte {
	var symW sbproto.Writer
	symW.String(2, symbol)
	trBytes := symW.Bytes()

	trBytes = appendFixed64TF(trBytes, 3, math.Float64bits(price))
	trBytes = appendFixed64TF(trBytes, 4, math.Float64bits(vol))
	trBytes = appendVarintTF(trBytes, 5, uint64(action))
	trBytes = appendVarintTF(trBytes, 9, uint64(tradeNum))

	var wrap sbproto.Writer
	wrap.Message(1, trBytes)
	return wrap.Bytes()
}

func appendFixed64TF(buf []byte, field int, v uint64) []byte {
	var tmp [9]byte
	tmp[0] = byte((field << 3) | sbproto.WireFixed64)
	binary.LittleEndian.PutUint64(tmp[1:], v)
	return append(buf, tmp[:]...)
}

func appendVarintTF(buf []byte, field int, v uint64) []byte {
	var w sbproto.Writer
	w.Tag(field, sbproto.WireVarint)
	w.Varint(v)
	return append(buf, w.Bytes()...)
}

// collectTradesFiltered replays segPath and returns all trades for sym whose
// recv_ns falls within [fromNs, toNs] (0 = unbounded). Side="" trades are
// excluded (mirrors runTrades behaviour).
func collectTradesFiltered(t *testing.T, segPath, sym string, fromNs, toNs int64) []feed.Trade {
	t.Helper()
	var out []feed.Trade
	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		if evt.Kind != replay.EventRunningTrade || evt.Symbol != sym {
			return
		}
		if fromNs > 0 && evt.RecvNs < fromNs {
			return
		}
		if toNs > 0 && evt.RecvNs > toNs {
			return
		}
		if evt.Decoded == nil || len(evt.Decoded.Trades) == 0 {
			return
		}
		tr := evt.Decoded.Trades[0]
		if tr.Side == "" {
			return
		}
		out = append(out, tr)
	}); err != nil {
		t.Fatalf("replay: %v", err)
	}
	return out
}

// writeTradeSegF builds a segment file containing only RunningTrade records.
func writeTradeSegF(t *testing.T, records []struct {
	sym      string
	price    float64
	vol      float64
	action   int
	tradeNum int
	ts       int64
}) string {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "seg-trades-*.binpb")
	if err != nil {
		t.Fatalf("create temp: %v", err)
	}
	defer f.Close()
	w := segment.NewWriter(f)
	for _, r := range records {
		if werr := w.WriteData(r.ts, buildTradeFrameF(r.sym, r.price, r.vol, r.action, r.tradeNum)); werr != nil {
			t.Fatalf("WriteData: %v", werr)
		}
	}
	if werr := w.Flush(); werr != nil {
		t.Fatalf("flush: %v", werr)
	}
	return f.Name()
}

// ---------- tests ----------

// TestTradesSideFilter verifies that side="" (action=0, pre-opening) trades
// are skipped; only trades with a valid aggressor-side tag are collected.
func TestTradesSideFilter(t *testing.T) {
	t.Parallel()

	const sym = "BUMI"
	const ts1 = int64(1_750_000_001_000_000_000)
	const ts2 = int64(1_750_000_002_000_000_000)
	const ts3 = int64(1_750_000_003_000_000_000)

	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 500, action: 0, tradeNum: 1, ts: ts1}, // side="" → skip
		{sym: sym, price: 101, vol: 300, action: 1, tradeNum: 2, ts: ts2}, // side="B" → keep
		{sym: sym, price: 102, vol: 200, action: 2, tradeNum: 3, ts: ts3}, // side="S" → keep
	})

	trades := collectTradesFiltered(t, segPath, sym, 0, 0)
	if len(trades) != 2 {
		t.Fatalf("side filter: got %d trades want 2", len(trades))
	}
	if trades[0].Side != "B" {
		t.Errorf("trade 0 side: got %q want B", trades[0].Side)
	}
	if trades[1].Side != "S" {
		t.Errorf("trade 1 side: got %q want S", trades[1].Side)
	}
}

// TestTradesSymbolFilter verifies that trades for symbols other than the
// requested one are excluded.
func TestTradesSymbolFilter(t *testing.T) {
	t.Parallel()

	const ts1 = int64(1_750_000_001_000_000_000)
	const ts2 = int64(1_750_000_002_000_000_000)

	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: "BUMI", price: 100, vol: 500, action: 1, tradeNum: 1, ts: ts1},
		{sym: "ANTM", price: 200, vol: 300, action: 2, tradeNum: 2, ts: ts2},
	})

	trades := collectTradesFiltered(t, segPath, "BUMI", 0, 0)
	if len(trades) != 1 {
		t.Fatalf("symbol filter: got %d trades want 1", len(trades))
	}
	if trades[0].Symbol != "BUMI" {
		t.Errorf("symbol: got %q want BUMI", trades[0].Symbol)
	}
	if trades[0].TradeNumber != 1 {
		t.Errorf("trade_num: got %d want 1", trades[0].TradeNumber)
	}
}

// TestTradesTimeWindowFilter verifies that fromNs/toNs bound the emitted rows.
func TestTradesTimeWindowFilter(t *testing.T) {
	t.Parallel()

	const sym = "BUMI"
	const ts1 = int64(1_750_000_001_000_000_000)
	const ts2 = int64(1_750_000_002_000_000_000)
	const ts3 = int64(1_750_000_003_000_000_000)

	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 500, action: 1, tradeNum: 1, ts: ts1},
		{sym: sym, price: 101, vol: 300, action: 1, tradeNum: 2, ts: ts2},
		{sym: sym, price: 102, vol: 200, action: 1, tradeNum: 3, ts: ts3},
	})

	// Filter to ts2 only (fromNs=ts2, toNs=ts2).
	trades := collectTradesFiltered(t, segPath, sym, ts2, ts2)
	if len(trades) != 1 {
		t.Fatalf("time filter: got %d trades want 1", len(trades))
	}
	if trades[0].TradeNumber != 2 {
		t.Errorf("trade_num: got %d want 2", trades[0].TradeNumber)
	}
}
