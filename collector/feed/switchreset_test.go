package feed_test

import (
	"testing"

	"collector/feed"
	"collector/sbproto"
)

// buildOrderbookWrap builds a WebsocketWrapMessageChannel frame (field 10 =
// consumer Orderbook) with the given stock_code (field 1) and switch_reset
// (field 6, varint).
func buildOrderbookWrap(stockCode string, switchReset int) []byte {
	var ob sbproto.Writer
	ob.String(1, stockCode)       // stock_code
	ob.Tag(6, sbproto.WireVarint) // switch_reset field
	ob.Varint(uint64(switchReset))

	var wrap sbproto.Writer
	wrap.Message(10, ob.Bytes()) // field 10 = orderbook
	return wrap.Bytes()
}

// buildPingWrap builds a WebsocketWrapMessageChannel ping frame (field 2).
// This is not an orderbook frame and should return ("", false).
func buildPingWrap() []byte {
	var ping sbproto.Writer
	ping.String(1, "pong") // PingResponse.message
	var wrap sbproto.Writer
	wrap.Message(2, ping.Bytes())
	return wrap.Bytes()
}

// TestDetectSwitchReset verifies the hot-path detector using crafted frames.
func TestDetectSwitchReset(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		frame      []byte
		wantSymbol string
		wantReset  bool
	}{
		{
			name:       "orderbook with switch_reset=0 (normal)",
			frame:      buildOrderbookWrap("BBCA", 0),
			wantSymbol: "BBCA",
			wantReset:  false,
		},
		{
			name:       "orderbook with switch_reset=1",
			frame:      buildOrderbookWrap("BMRI", 1),
			wantSymbol: "BMRI",
			wantReset:  true,
		},
		{
			name:       "orderbook with switch_reset=2",
			frame:      buildOrderbookWrap("TLKM", 2),
			wantSymbol: "TLKM",
			wantReset:  true,
		},
		{
			name:       "ping frame (not an orderbook)",
			frame:      buildPingWrap(),
			wantSymbol: "",
			wantReset:  false,
		},
		{
			name:       "empty frame",
			frame:      []byte{},
			wantSymbol: "",
			wantReset:  false,
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			gotSym, gotReset := feed.DetectSwitchReset(tc.frame)
			if gotSym != tc.wantSymbol {
				t.Errorf("symbol: got %q want %q", gotSym, tc.wantSymbol)
			}
			if gotReset != tc.wantReset {
				t.Errorf("reset: got %v want %v", gotReset, tc.wantReset)
			}
		})
	}
}

// TestDetectSwitchResetDedup verifies the dedup pattern used in main.go:
// a sustained reset does not emit a second event, but a transition back
// to non-reset and then reset again does.
func TestDetectSwitchResetDedup(t *testing.T) {
	t.Parallel()

	inReset := make(map[string]bool)
	var events []string // symbol names each time we'd write a control record

	emit := func(frame []byte) {
		sym, isReset := feed.DetectSwitchReset(frame)
		if sym == "" {
			return
		}
		if isReset && !inReset[sym] {
			events = append(events, sym)
		}
		inReset[sym] = isReset
	}

	emit(buildOrderbookWrap("BBCA", 0)) // normal
	emit(buildOrderbookWrap("BBCA", 1)) // reset — emit
	emit(buildOrderbookWrap("BBCA", 1)) // sustained reset — no emit
	emit(buildOrderbookWrap("BBCA", 0)) // reset cleared
	emit(buildOrderbookWrap("BBCA", 1)) // reset again — emit

	if len(events) != 2 {
		t.Fatalf("expected 2 SWITCH_RESET events, got %d: %v", len(events), events)
	}
	for _, sym := range events {
		if sym != "BBCA" {
			t.Errorf("unexpected symbol %q in events", sym)
		}
	}
}
