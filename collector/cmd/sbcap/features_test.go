package main

import (
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"strconv"
	"testing"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/replay"
	"sb-algo-prototype/internal/sbproto"
	"sb-algo-prototype/internal/segment"
)

func floatPtr(v float64) *float64 { return &v }

// ---------- assembleRow unit tests ----------

// makeBidLevels returns a BID BookSide from alternating (price, vol) pairs.
func makeBidLevels(pairs ...float64) feed.BookSide {
	bs := feed.BookSide{Side: "BID"}
	for i := 0; i+1 < len(pairs); i += 2 {
		bs.Levels = append(bs.Levels, feed.Level{Price: pairs[i], Volume: pairs[i+1]})
	}
	return bs
}

// makeOfferLevels returns an OFFER BookSide from alternating (price, vol) pairs.
func makeOfferLevels(pairs ...float64) feed.BookSide {
	bs := feed.BookSide{Side: "OFFER"}
	for i := 0; i+1 < len(pairs); i += 2 {
		bs.Levels = append(bs.Levels, feed.Level{Price: pairs[i], Volume: pairs[i+1]})
	}
	return bs
}

// TestAssembleRowSorting verifies that BID levels are sorted descending and
// OFFER levels ascending so that index 0 is always top-of-book regardless of
// the order the levels arrive.
func TestAssembleRowSorting(t *testing.T) {
	t.Parallel()

	// Levels deliberately out of best-price-first order.
	bid := makeBidLevels(
		100, 10,
		102, 20, // best bid
		101, 15,
	)
	offer := makeOfferLevels(
		203, 5,
		201, 8, // best ask
		202, 6,
	)

	const recvNs = int64(1_700_000_001_000_000_000)
	row := assembleRow(recvNs, &bid, &offer, 1, 2, 0, false, 0, false, 3, nil, nil)

	// Fixed columns: recv_ns(0) iso_wib(1) bid_seq(2) offer_seq(3) last_price(4) iep(5)
	//                delta_bid_vol(6) delta_ask_vol(7)
	// Then for each level i: bid_px_i(8+4i) bid_vol_i(9+4i) ask_px_i(10+4i) ask_vol_i(11+4i)
	if row[8] != "102" {
		t.Errorf("bid_px_0: got %s want 102", row[8])
	}
	if row[9] != "20" {
		t.Errorf("bid_vol_0: got %s want 20", row[9])
	}
	if row[10] != "201" {
		t.Errorf("ask_px_0: got %s want 201", row[10])
	}
	if row[11] != "8" {
		t.Errorf("ask_vol_0: got %s want 8", row[11])
	}
}

// TestAssembleRowDepthTruncation verifies that when the book has more levels
// than depth, only the first N levels appear in the row.
func TestAssembleRowDepthTruncation(t *testing.T) {
	t.Parallel()

	bid := makeBidLevels(105, 1, 104, 2, 103, 3, 102, 4, 101, 5, 100, 6) // 6 levels
	offer := makeOfferLevels(106, 1, 107, 2, 108, 3, 109, 4, 110, 5, 111, 6)

	const depth = 3
	row := assembleRow(0, &bid, &offer, 0, 0, 0, false, 0, false, depth, nil, nil)

	// Fixed 8 + 4*depth columns (8 fixed: recv_ns..delta_ask_vol).
	if len(row) != 8+4*depth {
		t.Fatalf("row len: got %d want %d", len(row), 8+4*depth)
	}
	if row[8] != "105" {
		t.Errorf("bid_px_0: got %s want 105", row[8])
	}
	// bid_px_2 is at 8 + 4*2 = 16
	if row[16] != "103" {
		t.Errorf("bid_px_2: got %s want 103", row[16])
	}
}

// TestAssembleRowDepthPadding verifies that missing depth levels produce empty
// strings in the row (not "0").
func TestAssembleRowDepthPadding(t *testing.T) {
	t.Parallel()

	bid := makeBidLevels(100, 10)    // 1 level
	offer := makeOfferLevels(101, 5) // 1 level

	const depth = 3
	row := assembleRow(0, &bid, &offer, 0, 0, 0, false, 0, false, depth, nil, nil)

	// bid_px_1 is at 8 + 4*1 = 12; ask_px_1 is at 10 + 4*1 = 14.
	if row[12] != "" {
		t.Errorf("bid_px_1 (missing): got %q want empty", row[12])
	}
	if row[14] != "" {
		t.Errorf("ask_px_1 (missing): got %q want empty", row[14])
	}
	// bid_px_2 is at 8 + 4*2 = 16
	if row[16] != "" {
		t.Errorf("bid_px_2 (missing): got %q want empty", row[16])
	}
}

// TestAssembleRowCarryForward verifies that last_price and iep are formatted
// correctly when present and are empty strings when not yet seen.
func TestAssembleRowCarryForward(t *testing.T) {
	t.Parallel()

	bid := makeBidLevels(100, 10)
	offer := makeOfferLevels(101, 5)

	tests := []struct {
		name         string
		hasLastPrice bool
		lastPrice    float64
		hasIEP       bool
		iep          float64
		wantLP       string
		wantIEP      string
	}{
		{
			name:    "neither seen yet",
			wantLP:  "",
			wantIEP: "",
		},
		{
			name:         "last_price seen, iep not",
			hasLastPrice: true,
			lastPrice:    234,
			wantLP:       "234",
			wantIEP:      "",
		},
		{
			name:         "both seen",
			hasLastPrice: true,
			lastPrice:    234,
			hasIEP:       true,
			iep:          235.5,
			wantLP:       "234",
			wantIEP:      "235.5",
		},
		{
			name:         "float without scientific notation",
			hasLastPrice: true,
			lastPrice:    1234567,
			wantLP:       "1234567",
			wantIEP:      "",
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			row := assembleRow(0, &bid, &offer, 0, 0,
				tc.lastPrice, tc.hasLastPrice,
				tc.iep, tc.hasIEP,
				1, nil, nil)
			if row[4] != tc.wantLP {
				t.Errorf("last_price: got %q want %q", row[4], tc.wantLP)
			}
			if row[5] != tc.wantIEP {
				t.Errorf("iep: got %q want %q", row[5], tc.wantIEP)
			}
		})
	}
}

// ---------- buildFeaturesHeader test ----------

// TestBuildFeaturesHeader verifies column count and depth-indexed column names.
func TestBuildFeaturesHeader(t *testing.T) {
	t.Parallel()

	for _, depth := range []int{1, 3, 5} {
		depth := depth
		t.Run(fmt.Sprintf("depth%d", depth), func(t *testing.T) {
			t.Parallel()
			h := buildFeaturesHeader(depth)
			want := 8 + 4*depth
			if len(h) != want {
				t.Fatalf("depth %d: header len got %d want %d", depth, len(h), want)
			}
			// Spot-check: first fixed column, delta cols, last-level bid_px name.
			if h[0] != "recv_ns" {
				t.Errorf("h[0]: got %q want recv_ns", h[0])
			}
			if h[6] != "delta_bid_vol" {
				t.Errorf("h[6]: got %q want delta_bid_vol", h[6])
			}
			if h[7] != "delta_ask_vol" {
				t.Errorf("h[7]: got %q want delta_ask_vol", h[7])
			}
			lastBidPx := fmt.Sprintf("bid_px_%d", depth-1)
			idx := 8 + 4*(depth-1)
			if h[idx] != lastBidPx {
				t.Errorf("h[%d]: got %q want %q", idx, h[idx], lastBidPx)
			}
		})
	}
}

// ---------- integration test: no rows before both sides seen ----------

// buildObFrameF constructs a WebsocketWrapMessageChannel Orderbook frame.
func buildObFrameF(symbol, body string, seq int64) []byte {
	var ob sbproto.Writer
	ob.String(1, symbol)
	ob.String(2, body)
	ob.Tag(3, sbproto.WireVarint)
	ob.Varint(uint64(seq))

	var wrap sbproto.Writer
	wrap.Message(10, ob.Bytes())
	return wrap.Bytes()
}

// buildLpFrameF constructs a WebsocketWrapMessageChannel LivePrice frame.
func buildLpFrameF(symbol string, lastPrice float64) []byte {
	var symW sbproto.Writer
	symW.String(1, symbol)

	var priceBuf [9]byte
	priceBuf[0] = (2 << 3) | 1 // field 2, wire type 1 (fixed64)
	binary.LittleEndian.PutUint64(priceBuf[1:], math.Float64bits(lastPrice))

	lp := append(symW.Bytes(), priceBuf[:]...)
	var wrap sbproto.Writer
	wrap.Message(9, lp)
	return wrap.Bytes()
}

// writeSegF writes records to a temp *.binpb file and returns the path.
func writeSegF(t *testing.T, records []struct {
	recType     byte
	timestampNs int64
	payload     []byte
}) string {
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
		}
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}
	return f.Name()
}

// TestNoBothSidesNoRow verifies that runFeatures emits zero rows when only one
// book side has arrived, and exactly one row once the second side arrives.
func TestNoBothSidesNoRow(t *testing.T) {
	t.Parallel()

	const sym = "BUMI"
	const ts1 = int64(1_750_000_001_000_000_000)
	const ts2 = int64(1_750_000_002_000_000_000)
	const ts3 = int64(1_750_000_003_000_000_000)

	bidBody := "#O|BUMI|BID|100;1;500;|1750000001"
	offerBody := "#O|BUMI|OFFER|101;1;300;|1750000002"

	type rec struct {
		recType     byte
		timestampNs int64
		payload     []byte
	}

	segPath := writeSegF(t, []struct {
		recType     byte
		timestampNs int64
		payload     []byte
	}{
		// BID only — must not produce a row.
		{segment.RecordData, ts1, buildObFrameF(sym, bidBody, 1)},
		// LivePrice — also must not produce a row on its own.
		{segment.RecordData, ts2, buildLpFrameF(sym, 100.0)},
		// OFFER arrives — both sides now known; this update emits the first row.
		{segment.RecordData, ts3, buildObFrameF(sym, offerBody, 2)},
	})

	rows := countFeatRows(t, segPath, sym)
	if rows != 1 {
		t.Errorf("emitted rows: got %d want 1", rows)
	}
}

// countFeatRows replays segPath and counts how many orderbook events would
// produce a CSV row under runFeatures logic (both sides non-nil).
func countFeatRows(t *testing.T, segPath, sym string) int {
	t.Helper()
	var bid, offer *feed.BookSide
	rows := 0

	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		if evt.Symbol != sym || evt.Kind != replay.EventOrderbook {
			return
		}
		if evt.Decoded == nil || evt.Decoded.Body == "" {
			return
		}
		bs, ok := feed.ParseOrderbookBody(evt.Decoded.Body)
		if !ok {
			return
		}
		bsCopy := bs
		switch bs.Side {
		case "BID":
			bid = &bsCopy
		case "OFFER":
			offer = &bsCopy
		}
		if bid != nil && offer != nil {
			rows++
		}
	}); err != nil {
		t.Fatalf("replay: %v", err)
	}
	return rows
}

// ---------- delta column tests ----------

// TestAssembleRowDeltaColumns verifies that delta columns are formatted
// correctly: nil → empty string, non-nil → formatted float.
func TestAssembleRowDeltaColumns(t *testing.T) {
	t.Parallel()

	bid := makeBidLevels(100, 10)
	offer := makeOfferLevels(101, 5)

	tests := []struct {
		name         string
		deltaBidVol  *float64
		deltaAskVol  *float64
		wantDeltaBid string
		wantDeltaAsk string
	}{
		{
			name:         "nil deltas (first row)",
			deltaBidVol:  nil,
			deltaAskVol:  nil,
			wantDeltaBid: "",
			wantDeltaAsk: "",
		},
		{
			name:         "positive and negative delta",
			deltaBidVol:  floatPtr(50),
			deltaAskVol:  floatPtr(-30),
			wantDeltaBid: "50",
			wantDeltaAsk: "-30",
		},
		{
			name:         "zero delta",
			deltaBidVol:  floatPtr(0),
			deltaAskVol:  floatPtr(0),
			wantDeltaBid: "0",
			wantDeltaAsk: "0",
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			row := assembleRow(0, &bid, &offer, 0, 0, 0, false, 0, false, 1,
				tc.deltaBidVol, tc.deltaAskVol)
			// delta_bid_vol is at index 6, delta_ask_vol at index 7.
			if row[6] != tc.wantDeltaBid {
				t.Errorf("delta_bid_vol: got %q want %q", row[6], tc.wantDeltaBid)
			}
			if row[7] != tc.wantDeltaAsk {
				t.Errorf("delta_ask_vol: got %q want %q", row[7], tc.wantDeltaAsk)
			}
		})
	}
}

// TestDeltaColumnStateTracking verifies the delta-column state machine: empty
// on the first row, populated on subsequent rows, and empty again after a
// RECONNECT control event.
func TestDeltaColumnStateTracking(t *testing.T) {
	// Not parallel: segment.Writer is sequential; test logic is stateful.

	const sym = "BUMI"
	const ts1 = int64(1_750_000_001_000_000_000)
	const ts2 = int64(1_750_000_002_000_000_000)
	const ts3 = int64(1_750_000_003_000_000_000)
	const ts4 = int64(1_750_000_004_000_000_000)
	const ts5 = int64(1_750_000_005_000_000_000)
	const ts6 = int64(1_750_000_006_000_000_000)

	reconnJSON, err := segment.ReconnectEvent(1, 0)
	if err != nil {
		t.Fatalf("ReconnectEvent: %v", err)
	}

	f, ferr := os.CreateTemp(t.TempDir(), "seg-delta-*.binpb")
	if ferr != nil {
		t.Fatalf("create temp: %v", ferr)
	}
	defer f.Close()

	w := segment.NewWriter(f)
	writeRec := func(rType byte, ts int64, payload []byte) {
		t.Helper()
		var werr error
		switch rType {
		case segment.RecordData:
			werr = w.WriteData(ts, payload)
		case segment.RecordControl:
			werr = w.WriteControl(ts, payload)
		}
		if werr != nil {
			t.Fatalf("write record: %v", werr)
		}
	}

	writeRec(segment.RecordData, ts1, buildObFrameF(sym, "#O|BUMI|BID|100;1;500;|1750000001", 1))
	writeRec(segment.RecordData, ts2, buildObFrameF(sym, "#O|BUMI|OFFER|101;1;300;|1750000002", 2)) // row 0
	writeRec(segment.RecordData, ts3, buildObFrameF(sym, "#O|BUMI|BID|100;1;600;|1750000003", 3))  // row 1
	writeRec(segment.RecordControl, ts4, reconnJSON)                                                 // dirty
	writeRec(segment.RecordData, ts5, buildObFrameF(sym, "#O|BUMI|BID|100;1;700;|1750000005", 4))  // row 2
	writeRec(segment.RecordData, ts6, buildObFrameF(sym, "#O|BUMI|BID|100;1;800;|1750000006", 5))  // row 3
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}
	segPath := f.Name()

	type deltaRow struct{ deltaBid, deltaAsk string }
	var rows []deltaRow

	type st struct {
		bid, offer *feed.BookSide
		prevBidVol float64
		prevAskVol float64
		hasPrev    bool
		dirty      bool
	}
	s := &st{}

	if err := replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		switch evt.Kind {
		case replay.EventControl:
			s.dirty = true
		case replay.EventOrderbook:
			if evt.Symbol != sym || evt.Decoded == nil || evt.Decoded.Body == "" {
				return
			}
			bs, ok := feed.ParseOrderbookBody(evt.Decoded.Body)
			if !ok {
				return
			}
			bsCopy := bs
			switch bs.Side {
			case "BID":
				s.bid = &bsCopy
			case "OFFER":
				s.offer = &bsCopy
			}
			if s.bid == nil || s.offer == nil {
				return
			}
			bidS := sortedLevels(s.bid.Levels, true)
			askS := sortedLevels(s.offer.Levels, false)
			var curBid, curAsk float64
			if len(bidS) > 0 {
				curBid = bidS[0].Volume
			}
			if len(askS) > 0 {
				curAsk = askS[0].Volume
			}
			var dBid, dAsk string
			if s.hasPrev && !s.dirty {
				dBid = strconv.FormatFloat(curBid-s.prevBidVol, 'f', -1, 64)
				dAsk = strconv.FormatFloat(curAsk-s.prevAskVol, 'f', -1, 64)
			}
			s.prevBidVol = curBid
			s.prevAskVol = curAsk
			s.hasPrev = true
			s.dirty = false
			rows = append(rows, deltaRow{deltaBid: dBid, deltaAsk: dAsk})
		}
	}); err != nil {
		t.Fatalf("replay: %v", err)
	}

	if len(rows) != 4 {
		t.Fatalf("expected 4 rows, got %d", len(rows))
	}
	// Row 0: first row → empty deltas.
	if rows[0].deltaBid != "" || rows[0].deltaAsk != "" {
		t.Errorf("row 0: want empty deltas, got bid=%q ask=%q", rows[0].deltaBid, rows[0].deltaAsk)
	}
	// Row 1: bid 500→600 (+100), ask unchanged (0).
	if rows[1].deltaBid != "100" {
		t.Errorf("row 1 delta_bid: got %q want 100", rows[1].deltaBid)
	}
	if rows[1].deltaAsk != "0" {
		t.Errorf("row 1 delta_ask: got %q want 0", rows[1].deltaAsk)
	}
	// Row 2: first row after RECONNECT → empty deltas.
	if rows[2].deltaBid != "" || rows[2].deltaAsk != "" {
		t.Errorf("row 2 (post-reconnect): want empty deltas, got bid=%q ask=%q",
			rows[2].deltaBid, rows[2].deltaAsk)
	}
	// Row 3: bid 700→800 (+100), ask unchanged (0).
	if rows[3].deltaBid != "100" {
		t.Errorf("row 3 delta_bid: got %q want 100", rows[3].deltaBid)
	}
	if rows[3].deltaAsk != "0" {
		t.Errorf("row 3 delta_ask: got %q want 0", rows[3].deltaAsk)
	}
}
