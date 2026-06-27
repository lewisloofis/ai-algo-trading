package feed

import "collector/sbproto"

// DetectSwitchReset is a cheap partial decoder for the hot capture path.
// It walks the outer WebsocketWrapMessageChannel looking only for field 10
// (consumer Orderbook), and within it field 1 (stock_code) and field 6
// (switch_reset varint).  It always returns the symbol when this is an
// orderbook frame; reset is true only when switch_reset is non-zero.
//
// Non-orderbook frames return ("", false).
//
// This avoids a full DecodeWrap allocation on the capture hot path.
func DetectSwitchReset(frame []byte) (symbol string, reset bool) {
	r := sbproto.NewReader(frame)
	for !r.EOF() {
		field, wire, err := r.ReadTag()
		if err != nil {
			return "", false
		}
		if field == 10 && wire == sbproto.WireBytes { // consumer Orderbook
			sub, err := r.ReadBytes()
			if err != nil {
				return "", false
			}
			sym, sw := detectOrderbookSwitchReset(sub)
			return sym, sw != 0
		}
		if err := r.Skip(wire); err != nil {
			return "", false
		}
	}
	return "", false
}

// detectOrderbookSwitchReset reads field 1 (stock_code) and field 6
// (switch_reset) from a consumer Orderbook sub-message, stopping early
// once both are found.
func detectOrderbookSwitchReset(data []byte) (stockCode string, switchReset int) {
	r := sbproto.NewReader(data)
	for !r.EOF() {
		field, wire, err := r.ReadTag()
		if err != nil {
			return
		}
		switch {
		case field == 1 && wire == sbproto.WireBytes: // stock_code
			b, err := r.ReadBytes()
			if err != nil {
				return
			}
			stockCode = string(b)
		case field == 6 && wire == sbproto.WireVarint: // switch_reset
			v, err := r.ReadVarint()
			if err != nil {
				return
			}
			switchReset = int(v)
			// Both values found — stop scanning.
			if stockCode != "" {
				return
			}
		default:
			if r.Skip(wire) != nil {
				return
			}
		}
	}
	return
}
