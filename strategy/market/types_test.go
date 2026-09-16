package market

import (
	"encoding/json"
	"testing"
)

func TestStatusUnmarshalAcceptsEveryEncoding(t *testing.T) {
	cases := []struct {
		raw  string
		want Status
	}{
		{`"STATUS_OPEN"`, StatusOpen},
		{`"STATUS_CLOSE"`, StatusClose},
		{`"open"`, StatusOpen},   // sb-web maps the raw lowercase form too
		{`"close"`, StatusClose}, // …/typings/market.ts:72-78
		{`1`, StatusOpen},        // numeric enum
		{`2`, StatusClose},
		{`0`, StatusUnspecified},
		{`null`, StatusUnspecified},
		{`""`, StatusUnspecified},
		{`"STATUS_SOMETHING_NEW"`, Status("STATUS_SOMETHING_NEW")}, // kept verbatim
	}
	for _, tc := range cases {
		var got Status
		if err := json.Unmarshal([]byte(tc.raw), &got); err != nil {
			t.Errorf("Unmarshal(%s): %v", tc.raw, err)
			continue
		}
		if got != tc.want {
			t.Errorf("Unmarshal(%s) = %q, want %q", tc.raw, got, tc.want)
		}
	}
}

func TestStateNameUnmarshalAcceptsEveryEncoding(t *testing.T) {
	cases := []struct {
		raw  string
		want StateName
	}{
		{`"STATE_NAME_SESSION_1"`, StateSession1},
		{`"SESSION_1"`, StateSession1}, // bare suffix
		{`"session_2"`, StateSession2}, // lowercased
		{`7`, StateBreak},              // numeric enum, per market_time.proto:9-23
		{`11`, StateMarketClosed},
		{`0`, StateUnspecified},
		{`null`, StateUnspecified},
		{`99`, StateName("STATE_NAME_UNMAPPED_99")},
	}
	for _, tc := range cases {
		var got StateName
		if err := json.Unmarshal([]byte(tc.raw), &got); err != nil {
			t.Errorf("Unmarshal(%s): %v", tc.raw, err)
			continue
		}
		if got != tc.want {
			t.Errorf("Unmarshal(%s) = %q, want %q", tc.raw, got, tc.want)
		}
	}
}

// TestPhaseClassificationIsThreeWay pins the distinction the whole gate
// rests on: a phase is either one that accepts regular orders, one the
// exchange defines as not accepting them, or one nobody recognises. The
// third case must be NEITHER, so the caller can report Unknown instead of
// asserting a closure it never established.
func TestPhaseClassificationIsThreeWay(t *testing.T) {
	accepts := []StateName{StateSession1, StateSession2, StateSession3, StateSession4, StateSession5}
	closed := []StateName{StatePreOpening, StateBreak, StatePreClosing, StatePostClosing, StateNegotiation, StateMarketClosed, StateBeforeMarket}
	unrecognised := []StateName{StateUnspecified, StateName("STATE_NAME_UNMAPPED_99"), StateName("STATE_NAME_SOMETHING_NEW")}

	for _, s := range accepts {
		if !s.acceptsRegularOrders() {
			t.Errorf("%s: acceptsRegularOrders = false, want true", s)
		}
		if s.isKnownClosedPhase() {
			t.Errorf("%s: isKnownClosedPhase = true, want false", s)
		}
	}
	for _, s := range closed {
		if s.acceptsRegularOrders() {
			t.Errorf("%s: acceptsRegularOrders = true, want false", s)
		}
		if !s.isKnownClosedPhase() {
			t.Errorf("%s: isKnownClosedPhase = false, want true", s)
		}
	}
	for _, s := range unrecognised {
		if s.acceptsRegularOrders() || s.isKnownClosedPhase() {
			t.Errorf("%s: classified as %v/%v, want neither — an unrecognised phase must read as Unknown",
				s, s.acceptsRegularOrders(), s.isKnownClosedPhase())
		}
	}
}

// TestNegotiationDoesNotAcceptOurOrders is called out separately because it
// is the one phase where the market IS open for business — just not for the
// board this repo trades.
func TestNegotiationDoesNotAcceptOurOrders(t *testing.T) {
	if StateNegotiation.acceptsRegularOrders() {
		t.Error("NEGOTIATION accepts regular orders; it accepts negotiated ones, which this repo never sends")
	}
}

func TestIDRUnmarshal(t *testing.T) {
	cases := []struct {
		raw     string
		want    IDR
		wantErr bool
	}{
		{`1100`, 1100, false},
		{`"1100"`, 1100, false},
		{`null`, 0, false},
		{`""`, 0, false},
		{`"not-a-number"`, 0, true},
		{`1100.5`, 0, true}, // a fractional rupiah price is not a price
	}
	for _, tc := range cases {
		var got IDR
		err := json.Unmarshal([]byte(tc.raw), &got)
		if tc.wantErr {
			if err == nil {
				t.Errorf("Unmarshal(%s) = %d, want an error", tc.raw, got)
			}
			continue
		}
		if err != nil {
			t.Errorf("Unmarshal(%s): %v", tc.raw, err)
			continue
		}
		if got != tc.want {
			t.Errorf("Unmarshal(%s) = %d, want %d", tc.raw, got, tc.want)
		}
	}
}

func TestBandUsability(t *testing.T) {
	cases := []struct {
		name string
		band AutoRejectPrice
		want bool
	}{
		{"normal", AutoRejectPrice{Below: 900, Above: 1100}, true},
		{"degenerate but valid (below == above)", AutoRejectPrice{Below: 1000, Above: 1000}, true},
		{"absent above", AutoRejectPrice{Below: 900}, false},
		{"absent below", AutoRejectPrice{Above: 1100}, false},
		{"absent both", AutoRejectPrice{}, false},
		{"inverted", AutoRejectPrice{Below: 1100, Above: 900}, false},
		{"negative", AutoRejectPrice{Below: -1, Above: 1100}, false},
	}
	for _, tc := range cases {
		if got := tc.band.usable(); got != tc.want {
			t.Errorf("%s: usable = %v, want %v", tc.name, got, tc.want)
		}
	}
}

func TestBandBoundariesAreInclusive(t *testing.T) {
	band := AutoRejectPrice{Below: 900, Above: 1100}
	cases := map[int64]bool{
		899:  false,
		900:  true, // reference 06 §4: "orders at exactly the boundary are valid"
		1000: true,
		1100: true,
		1101: false,
	}
	for price, want := range cases {
		if got := band.contains(price); got != want {
			t.Errorf("contains(%d) = %v, want %v", price, got, want)
		}
	}
}
