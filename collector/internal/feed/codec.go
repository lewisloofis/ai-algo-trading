package feed

// Codec helpers — encoding of outgoing WebsocketRequest frames and decoding
// of incoming WebsocketWrapMessageChannel frames.
//
// WebsocketRequest (securities/transactional/datafeed/v1/websocket.proto):
//
//	1: string user_id
//	2: WebsocketChannel channel
//	3: string key
//	4: PingRequest ping        (PingRequest{ 1: string message })
//	5: string access_token
//
// WebsocketChannel:
//
//	2:  repeated string order_book
//	6:  repeated string liveprice
//	11: repeated string running_trade_batch
//
// WebsocketWrapMessageChannel oneof message_channel:
//
//	 2: PingResponse ping
//	 7: Error error               (Error{ 1: ErrorCode code; 2: string message })
//	 9: consumer LivePrice liveprice
//	10: consumer Orderbook orderbook
//
// consumer Orderbook (datafeed/consumer/entity/v1/entity.proto):
//
//	1: string stock_code
//	2: string body
//	3: int64  sequence_number   (varint)
//	6: int32  switch_reset      (varint, SwitchReset enum)
//
// consumer LivePrice:
//
//	1: string stock_code
//	2: double lastprice

import (
	"fmt"
	"strconv"
	"strings"

	"sb-algo-prototype/internal/sbproto"
)

// Channels selects which WebsocketChannel fields to subscribe.
type Channels struct {
	OrderBook         bool // field 2
	LivePrice         bool // field 6
	RunningTradeBatch bool // field 11
}

// BuildSubscribe encodes a WebsocketRequest that authenticates the socket and
// subscribes to the requested channels for each symbol in symbols.  The
// resulting frame should be sent as the first binary message on a new
// connection.
//
// Each enabled channel emits one repeated string field per symbol inside the
// embedded WebsocketChannel message.
func BuildSubscribe(userID, accessToken, key string, symbols []string, channels Channels) []byte {
	var ch sbproto.Writer
	for _, sym := range symbols {
		if channels.OrderBook {
			ch.String(2, sym) // order_book
		}
		if channels.LivePrice {
			ch.String(6, sym) // liveprice
		}
		if channels.RunningTradeBatch {
			ch.String(11, sym) // running_trade_batch
		}
	}

	var req sbproto.Writer
	req.String(1, userID)
	req.Message(2, ch.Bytes())
	req.String(3, key)
	req.String(5, accessToken)
	return req.Bytes()
}

// BuildPing encodes a WebsocketRequest carrying a keep-alive ping.
func BuildPing(userID, accessToken string) []byte {
	var ping sbproto.Writer
	ping.String(1, "ping") // PingRequest.message

	var req sbproto.Writer
	req.String(1, userID)
	req.Message(4, ping.Bytes())
	req.String(5, accessToken)
	return req.Bytes()
}

// Decoded carries whatever a single incoming frame contained.
type Decoded struct {
	Kind           string  // "orderbook" | "liveprice" | "ping" | "error" | "other"
	Symbol         string
	Body           string  // raw orderbook body string (when Kind == "orderbook")
	LastPrice      float64 // when Kind == "liveprice"
	SequenceNumber int64   // orderbook sequence number (field 3, when Kind == "orderbook")
	SwitchReset    int     // SwitchReset enum value (field 6, non-zero = reset)
	ErrCode        int     // when Kind == "error"
	ErrMsg         string
}

// DecodeWrap parses one WebsocketWrapMessageChannel frame into a Decoded
// value. Unknown frame types return Kind == "other" with a nil error.
func DecodeWrap(data []byte) (Decoded, error) {
	r := sbproto.NewReader(data)
	for !r.EOF() {
		field, wire, err := r.ReadTag()
		if err != nil {
			return Decoded{}, err
		}
		switch {
		case field == 10 && wire == sbproto.WireBytes: // orderbook
			sub, err := r.ReadBytes()
			if err != nil {
				return Decoded{}, err
			}
			code, body, seq, sw := decodeOrderbookMsg(sub)
			return Decoded{
				Kind:           "orderbook",
				Symbol:         code,
				Body:           body,
				SequenceNumber: seq,
				SwitchReset:    sw,
			}, nil
		case field == 9 && wire == sbproto.WireBytes: // liveprice
			sub, err := r.ReadBytes()
			if err != nil {
				return Decoded{}, err
			}
			code, price := decodeLivePriceMsg(sub)
			return Decoded{Kind: "liveprice", Symbol: code, LastPrice: price}, nil
		case field == 7 && wire == sbproto.WireBytes: // error
			sub, err := r.ReadBytes()
			if err != nil {
				return Decoded{}, err
			}
			c, m := decodeErrorMsg(sub)
			return Decoded{Kind: "error", ErrCode: c, ErrMsg: m}, nil
		case field == 2 && wire == sbproto.WireBytes: // ping response
			if _, err := r.ReadBytes(); err != nil {
				return Decoded{}, err
			}
			return Decoded{Kind: "ping"}, nil
		default:
			if err := r.Skip(wire); err != nil {
				return Decoded{}, err
			}
		}
	}
	return Decoded{Kind: "other"}, nil
}

// decodeOrderbookMsg extracts stock_code (field 1), body (field 2),
// sequence_number (field 3, varint), and switch_reset (field 6, varint) from
// a consumer Orderbook message.
func decodeOrderbookMsg(data []byte) (stockCode, body string, seq int64, switchReset int) {
	r := sbproto.NewReader(data)
	for !r.EOF() {
		field, wire, err := r.ReadTag()
		if err != nil {
			return
		}
		switch {
		case field == 1 && wire == sbproto.WireBytes:
			b, err := r.ReadBytes()
			if err != nil {
				return
			}
			stockCode = string(b)
		case field == 2 && wire == sbproto.WireBytes:
			b, err := r.ReadBytes()
			if err != nil {
				return
			}
			body = string(b)
		case field == 3 && wire == sbproto.WireVarint:
			v, err := r.ReadVarint()
			if err != nil {
				return
			}
			seq = int64(v)
		case field == 6 && wire == sbproto.WireVarint:
			v, err := r.ReadVarint()
			if err != nil {
				return
			}
			switchReset = int(v)
		default:
			if r.Skip(wire) != nil {
				return
			}
		}
	}
	return
}

func decodeLivePriceMsg(data []byte) (stockCode string, lastPrice float64) {
	r := sbproto.NewReader(data)
	for !r.EOF() {
		field, wire, err := r.ReadTag()
		if err != nil {
			return
		}
		switch {
		case field == 1 && wire == sbproto.WireBytes:
			b, err := r.ReadBytes()
			if err != nil {
				return
			}
			stockCode = string(b)
		case field == 2 && wire == sbproto.WireFixed64:
			d, err := r.ReadDouble()
			if err != nil {
				return
			}
			lastPrice = d
		default:
			if r.Skip(wire) != nil {
				return
			}
		}
	}
	return
}

func decodeErrorMsg(data []byte) (code int, msg string) {
	r := sbproto.NewReader(data)
	for !r.EOF() {
		field, wire, err := r.ReadTag()
		if err != nil {
			return
		}
		switch {
		case field == 1 && wire == sbproto.WireVarint:
			v, err := r.ReadVarint()
			if err != nil {
				return
			}
			code = int(v)
		case field == 2 && wire == sbproto.WireBytes:
			b, err := r.ReadBytes()
			if err != nil {
				return
			}
			msg = string(b)
		default:
			if r.Skip(wire) != nil {
				return
			}
		}
	}
	return
}

// ---------- Orderbook body parsing ----------
//
// The orderbook body string is pipe-delimited:
//
//	#O|<SYMBOL>|<BID|OFFER>|<price>;<queue>;<volume>;|...|<unixTime>
//
// BID and OFFER arrive as separate messages; the side is at index 2.

// Level is one price level in an order book side.
type Level struct {
	Price  float64
	Volume float64
}

// BookSide is one side (BID or OFFER) of the order book for a symbol.
type BookSide struct {
	Side   string // "BID" or "OFFER"
	Levels []Level
}

// ParseOrderbookBody parses the pipe-delimited orderbook body string.
// Returns the BookSide and true on success; zero BookSide and false on any
// parse error or unrecognised format.
func ParseOrderbookBody(body string) (BookSide, bool) {
	parts := strings.Split(body, "|")
	if len(parts) < 4 || parts[0] != "#O" {
		return BookSide{}, false
	}
	side := strings.ToUpper(parts[2])
	if side != "BID" && side != "OFFER" {
		return BookSide{}, false
	}
	// Levels run from index 3 up to (but not including) the trailing timestamp.
	var levels []Level
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
		levels = append(levels, Level{Price: price, Volume: vol})
	}
	if len(levels) == 0 {
		return BookSide{}, false
	}
	return BookSide{Side: side, Levels: levels}, true
}

// String returns a short human-readable description of the BookSide.
func (b BookSide) String() string {
	if len(b.Levels) == 0 {
		return b.Side + " (empty)"
	}
	top := b.Levels[0]
	return fmt.Sprintf("%s top=%.0f x %.0f (%d levels)", b.Side, top.Price, top.Volume, len(b.Levels))
}
