package main

import "testing"

// Sample orderbook bodies in the exact wire format used by the live feed,
// taken from the client test stubs (iOS WatchlistInteractor / Android
// OrderBookMapperV2 / web webSocket.js): #O|SYM|SIDE|price;queue;volume;|…|ts
const (
	sampleOffer = "#O|BANK|OFFER|418;2;5700;|420;7;60900;|422;9;177700;|1663553794"
	sampleBid   = "#O|BANK|BID|472;254;4990200;|470;181;1914100;|468;89;1703300;|1663129638"
)

func TestParseOrderbookBody(t *testing.T) {
	offer, ok := parseOrderbookBody(sampleOffer)
	if !ok {
		t.Fatal("offer should parse")
	}
	if offer.side != "OFFER" {
		t.Errorf("side = %q, want OFFER", offer.side)
	}
	if len(offer.levels) != 3 {
		t.Fatalf("levels = %d, want 3", len(offer.levels))
	}
	if offer.levels[0].price != 418 || offer.levels[0].volume != 5700 {
		t.Errorf("top offer = %v, want price=418 vol=5700", offer.levels[0])
	}

	bid, ok := parseOrderbookBody(sampleBid)
	if !ok {
		t.Fatal("bid should parse")
	}
	if bid.side != "BID" || bid.levels[0].price != 472 || bid.levels[0].volume != 4990200 {
		t.Errorf("top bid = %v (side %s), want price=472 vol=4990200", bid.levels[0], bid.side)
	}
}

func TestParseOrderbookBodyRejectsGarbage(t *testing.T) {
	for _, s := range []string{"", "garbage", "#X|BANK|BID|1;2;3;|0", "#O|BANK|FOO|1;2;3;|0"} {
		if _, ok := parseOrderbookBody(s); ok {
			t.Errorf("expected %q to be rejected", s)
		}
	}
}

func TestImbalanceSignals(t *testing.T) {
	s := NewImbalanceStrategy(3, 0.30)

	// Heavy bids vs light offers → buying pressure → BUY.
	if _, _, _, ready := s.Update(mustParse(t, sampleBid)); ready {
		t.Fatal("should not be ready with only one side")
	}
	sig, imb, _, ready := s.Update(mustParse(t, sampleOffer))
	if !ready {
		t.Fatal("should be ready with both sides")
	}
	if imb <= 0 {
		t.Errorf("imbalance = %.3f, want positive (bid-heavy)", imb)
	}
	if sig != SignalBuy {
		t.Errorf("signal = %s, want BUY", sig)
	}

	// Now flip: heavy offers vs light bids → SELL.
	heavyOffer := "#O|BANK|OFFER|418;2;9000000;|420;7;8000000;|422;9;7000000;|1"
	lightBid := "#O|BANK|BID|472;1;100;|470;1;100;|468;1;100;|1"
	_, _, _, _ = s.Update(mustParse(t, lightBid))
	sig, imb, _, _ = s.Update(mustParse(t, heavyOffer))
	if imb >= 0 {
		t.Errorf("imbalance = %.3f, want negative (offer-heavy)", imb)
	}
	if sig != SignalSell {
		t.Errorf("signal = %s, want SELL", sig)
	}
}

func TestProtobufWrapRoundTrip(t *testing.T) {
	// Hand-build a WebsocketWrapMessageChannel with field 10 = Orderbook
	// {1: stock_code, 2: body} and confirm decodeWrap reads it back.
	var ob pbWriter
	ob.string(1, "BBCA")
	ob.string(2, sampleBid)

	var wrap pbWriter
	wrap.message(10, ob.buf)

	msg, err := decodeWrap(wrap.buf)
	if err != nil {
		t.Fatalf("decodeWrap: %v", err)
	}
	if msg.kind != "orderbook" {
		t.Fatalf("kind = %q, want orderbook", msg.kind)
	}
	if msg.symbol != "BBCA" {
		t.Errorf("symbol = %q, want BBCA", msg.symbol)
	}
	if msg.body != sampleBid {
		t.Errorf("body mismatch")
	}
}

func TestBuildSubscribeEncodesSymbol(t *testing.T) {
	// The subscribe request must carry the symbol in channel.order_book (field 2
	// of the channel message at field 2 of the request) and the access token.
	out := buildSubscribe("123", "tok", "key", "BBCA")
	if len(out) == 0 {
		t.Fatal("empty subscribe payload")
	}
	r := pbReader{buf: out}
	foundChannel := false
	for !r.eof() {
		field, wire, err := r.readTag()
		if err != nil {
			t.Fatalf("readTag: %v", err)
		}
		if field == 2 && wire == wireBytes {
			sub, _ := r.readBytes()
			// channel.order_book (field 2) should equal "BBCA"
			cr := pbReader{buf: sub}
			for !cr.eof() {
				cf, cw, _ := cr.readTag()
				if cf == 2 && cw == wireBytes {
					b, _ := cr.readBytes()
					if string(b) == "BBCA" {
						foundChannel = true
					}
				} else {
					_ = cr.skip(cw)
				}
			}
		} else {
			_ = r.skip(wire)
		}
	}
	if !foundChannel {
		t.Error("subscribe payload did not encode order_book=BBCA")
	}
}

func mustParse(t *testing.T, body string) bookSide {
	t.Helper()
	side, ok := parseOrderbookBody(body)
	if !ok {
		t.Fatalf("failed to parse %q", body)
	}
	return side
}
