package main

// Encoding of the outgoing WebsocketRequest and decoding of the incoming
// WebsocketWrapMessageChannel, per the sb-protos datafeed contracts.
//
// WebsocketRequest (securities/transactional/datafeed/v1/websocket.proto):
//   1: string user_id
//   2: WebsocketChannel channel
//   3: string key
//   4: PingRequest ping        (PingRequest{ 1: string message })
//   5: string access_token
//
// WebsocketChannel:
//   2: repeated string order_book
//   6: repeated string liveprice
//
// WebsocketWrapMessageChannel oneof message_channel:
//   2: PingResponse ping
//   7: Error error               (Error{ 1: ErrorCode code; 2: string message })
//   9: consumer LivePrice liveprice
//  10: consumer Orderbook orderbook   (Orderbook{ 1: string stock_code; 2: string body })
//
// consumer LivePrice (datafeed/consumer/entity/v1/entity.proto):
//   1: string stock_code
//   2: double lastprice

import (
	"fmt"
	"strconv"
	"strings"
)

// buildSubscribe returns the binary WebsocketRequest that authenticates the
// socket and subscribes to the order_book + liveprice channels for one symbol.
func buildSubscribe(userID, accessToken, key, symbol string) []byte {
	var ch pbWriter
	ch.string(2, symbol) // order_book
	ch.string(6, symbol) // liveprice

	var req pbWriter
	req.string(1, userID)
	req.message(2, ch.buf)
	req.string(3, key)
	req.string(5, accessToken)
	return req.buf
}

// buildPing returns the binary WebsocketRequest carrying a ping (keep-alive).
func buildPing(userID, accessToken string) []byte {
	var ping pbWriter
	ping.string(1, "ping") // PingRequest.message

	var req pbWriter
	req.string(1, userID)
	req.message(4, ping.buf)
	req.string(5, accessToken)
	return req.buf
}

// decoded carries whatever a single incoming frame turned out to contain.
type decoded struct {
	kind      string // "orderbook" | "liveprice" | "ping" | "error" | "other"
	symbol    string
	body      string  // raw orderbook body string (when kind == "orderbook")
	lastPrice float64 // when kind == "liveprice"
	errCode   int     // when kind == "error"
	errMsg    string
}

// decodeWrap parses one WebsocketWrapMessageChannel frame.
func decodeWrap(data []byte) (decoded, error) {
	r := pbReader{buf: data}
	for !r.eof() {
		field, wire, err := r.readTag()
		if err != nil {
			return decoded{}, err
		}
		switch {
		case field == 10 && wire == wireBytes: // orderbook
			sub, err := r.readBytes()
			if err != nil {
				return decoded{}, err
			}
			code, body := decodeOrderbookMsg(sub)
			return decoded{kind: "orderbook", symbol: code, body: body}, nil
		case field == 9 && wire == wireBytes: // liveprice
			sub, err := r.readBytes()
			if err != nil {
				return decoded{}, err
			}
			code, price := decodeLivePriceMsg(sub)
			return decoded{kind: "liveprice", symbol: code, lastPrice: price}, nil
		case field == 7 && wire == wireBytes: // error
			sub, err := r.readBytes()
			if err != nil {
				return decoded{}, err
			}
			c, m := decodeErrorMsg(sub)
			return decoded{kind: "error", errCode: c, errMsg: m}, nil
		case field == 2 && wire == wireBytes: // ping response
			if _, err := r.readBytes(); err != nil {
				return decoded{}, err
			}
			return decoded{kind: "ping"}, nil
		default:
			if err := r.skip(wire); err != nil {
				return decoded{}, err
			}
		}
	}
	return decoded{kind: "other"}, nil
}

func decodeOrderbookMsg(data []byte) (stockCode, body string) {
	r := pbReader{buf: data}
	for !r.eof() {
		field, wire, err := r.readTag()
		if err != nil {
			return
		}
		if wire == wireBytes && (field == 1 || field == 2) {
			b, err := r.readBytes()
			if err != nil {
				return
			}
			if field == 1 {
				stockCode = string(b)
			} else {
				body = string(b)
			}
			continue
		}
		if r.skip(wire) != nil {
			return
		}
	}
	return
}

func decodeLivePriceMsg(data []byte) (stockCode string, lastPrice float64) {
	r := pbReader{buf: data}
	for !r.eof() {
		field, wire, err := r.readTag()
		if err != nil {
			return
		}
		switch {
		case field == 1 && wire == wireBytes:
			b, err := r.readBytes()
			if err != nil {
				return
			}
			stockCode = string(b)
		case field == 2 && wire == wireFixed64:
			d, err := r.readDouble()
			if err != nil {
				return
			}
			lastPrice = d
		default:
			if r.skip(wire) != nil {
				return
			}
		}
	}
	return
}

func decodeErrorMsg(data []byte) (code int, msg string) {
	r := pbReader{buf: data}
	for !r.eof() {
		field, wire, err := r.readTag()
		if err != nil {
			return
		}
		switch {
		case field == 1 && wire == wireVarint:
			v, err := r.readVarint()
			if err != nil {
				return
			}
			code = int(v)
		case field == 2 && wire == wireBytes:
			b, err := r.readBytes()
			if err != nil {
				return
			}
			msg = string(b)
		default:
			if r.skip(wire) != nil {
				return
			}
		}
	}
	return
}

// ---------- Orderbook body parsing ----------
//
// The orderbook `body` string is pipe-delimited (verified against the parsers
// in all three clients: iOS WatchlistInteractor, Android OrderBookMapperV2,
// web utils/webSocket.js):
//
//   #O|<SYMBOL>|<BID|OFFER>|<price>;<queue>;<volume>;|...|<unixTime>
//
// Bids and offers arrive as SEPARATE messages; the side is at index 2.

type level struct {
	price  float64
	volume float64
}

type bookSide struct {
	side   string // "BID" or "OFFER"
	levels []level
}

func parseOrderbookBody(body string) (bookSide, bool) {
	parts := strings.Split(body, "|")
	if len(parts) < 4 || parts[0] != "#O" {
		return bookSide{}, false
	}
	side := strings.ToUpper(parts[2])
	if side != "BID" && side != "OFFER" {
		return bookSide{}, false
	}
	// Levels run from index 3 up to (but not including) the trailing timestamp.
	var levels []level
	for i := 3; i < len(parts)-1; i++ {
		cols := strings.Split(parts[i], ";")
		if len(cols) < 3 {
			continue
		}
		price, err1 := strconv.ParseFloat(strings.TrimSpace(cols[0]), 64)
		vol, err2 := strconv.ParseFloat(strings.TrimSpace(cols[2]), 64)
		if err1 != nil || err2 != nil || price <= 0 {
			continue
		}
		levels = append(levels, level{price: price, volume: vol})
	}
	if len(levels) == 0 {
		return bookSide{}, false
	}
	return bookSide{side: side, levels: levels}, true
}

func (b bookSide) String() string {
	if len(b.levels) == 0 {
		return b.side + " (empty)"
	}
	top := b.levels[0]
	return fmt.Sprintf("%s top=%.0f x %.0f (%d levels)", b.side, top.price, top.volume, len(b.levels))
}
