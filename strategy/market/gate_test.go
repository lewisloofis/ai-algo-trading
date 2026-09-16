package market

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	"strategy/risk"
)

func TestNewGate_RequiresCalendarAndClient(t *testing.T) {
	f := newFakeStack(t)
	if _, err := NewGate(nil, f.client(t), GateOptions{}); err == nil {
		t.Error("NewGate(nil calendar) succeeded; without a calendar there is no holiday answer")
	}
	if _, err := NewGate(loadCalendar(t), nil, GateOptions{}); err == nil {
		t.Error("NewGate(nil client) succeeded; without a client the honest construction is risk.DenyAllGate")
	}
}

func TestGate_AllowsWhenEverySourceAgrees(t *testing.T) {
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(tradingNoon))

	state, err := g.State(context.Background(), "BBCA", 1000)
	if err != nil {
		t.Fatalf("State: %v", err)
	}
	want := risk.MarketState{
		NotHoliday: risk.Allow, MarketOpen: risk.Allow, Tradable: risk.Allow,
		NotSuspended: risk.Allow, WithinBand: risk.Allow,
	}
	if state != want {
		t.Errorf("state = %+v, want %+v", state, want)
	}
}

// TestGate_HolidayIsDecidedInJakartaNotLocally is the timezone test. The
// clock returns Friday 19:00 UTC, which is Saturday 02:00 in Jakarta. A
// gate that asked the host's own date would call it a trading day; the
// exchange's date is what matters, and the collector box runs on UTC.
func TestGate_HolidayIsDecidedInJakartaNotLocally(t *testing.T) {
	fridayEveningUTC := time.Date(2026, 9, 18, 19, 0, 0, 0, time.UTC)
	if fridayEveningUTC.Weekday() != time.Friday {
		t.Fatalf("fixture drift: %s is a %s, expected Friday", fridayEveningUTC, fridayEveningUTC.Weekday())
	}
	if fridayEveningUTC.In(wib).Weekday() != time.Saturday {
		t.Fatalf("fixture drift: %s is a %s in WIB, expected Saturday", fridayEveningUTC, fridayEveningUTC.In(wib).Weekday())
	}

	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(fridayEveningUTC))

	d := g.Detail(context.Background(), "BBCA", 1000)
	if d.State.NotHoliday != risk.Deny {
		t.Errorf("NotHoliday = %s, want deny (it is Saturday in Jakarta)", d.State.NotHoliday)
	}
	if d.Allowed() {
		t.Error("Allowed() = true on a Saturday")
	}
}

func TestGate_KnownHolidayDenies(t *testing.T) {
	// 2026-01-01, Tahun Baru — in the embedded IDX holiday file.
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(time.Date(2026, 1, 1, 10, 0, 0, 0, wib)))
	d := g.Detail(context.Background(), "BBCA", 1000)
	if d.State.NotHoliday != risk.Deny {
		t.Errorf("NotHoliday = %s on 2026-01-01, want deny", d.State.NotHoliday)
	}
}

func TestGate_PhaseVerdicts(t *testing.T) {
	cases := []struct {
		name       string
		marketTime string
		session    string
		want       risk.TriState
		reasonHas  string
	}{
		{"session 1, market open", bodyMarketOpen, bodySession1, risk.Allow, "regular session"},
		{"lunch break", bodyMarketOpen, bodySessionBreak, risk.Deny, "does not accept regular orders"},
		{
			// The adversarial one: the session endpoint still says
			// SESSION_1 while the coarse status says closed. Either source
			// saying "closed" must be enough to deny.
			"session says open, market-time says closed",
			bodyMarketClosed, bodySession1, risk.Deny, "market reports closed",
		},
		{
			"unrecognised phase is unknown, not a confident deny",
			bodyMarketOpen,
			`{"data":{"detail":{"regular":{"state_name":"STATE_NAME_SOMETHING_NEW"}}}}`,
			risk.Unknown, "unrecognised combination",
		},
		{
			"unspecified status with a good phase is unknown",
			`{"data":{"market":{"status":"STATUS_UNSPECIFIED"}}}`,
			bodySession1, risk.Unknown, "unrecognised combination",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeStack(t)
			f.setBody(pathMarketTime, tc.marketTime)
			f.setBody(pathMarketSession, tc.session)
			g := newTestGate(t, f, newFakeClock(tradingNoon))

			d := g.Detail(context.Background(), "BBCA", 1000)
			if d.State.MarketOpen != tc.want {
				t.Errorf("MarketOpen = %s, want %s", d.State.MarketOpen, tc.want)
			}
			if got := fieldReason(t, d, "market_open"); !strings.Contains(got, tc.reasonHas) {
				t.Errorf("reason = %q, want it to contain %q", got, tc.reasonHas)
			}
		})
	}
}

func TestGate_NotTradableDeniesTradabilityAndSuspension(t *testing.T) {
	f := newFakeStack(t)
	f.setBody(pathStockTradable, bodyTradableNo)
	g := newTestGate(t, f, newFakeClock(tradingNoon))

	d := g.Detail(context.Background(), "BBCA", 1000)
	if d.State.Tradable != risk.Deny {
		t.Errorf("Tradable = %s, want deny", d.State.Tradable)
	}
	if d.State.NotSuspended != risk.Deny {
		t.Errorf("NotSuspended = %s, want deny", d.State.NotSuspended)
	}
}

// TestGate_SuspensionSourceIsLabelledAsAProxy pins the honesty requirement:
// NotSuspended is not independently sourced, and the value says so on
// itself rather than only in the package doc.
func TestGate_SuspensionSourceIsLabelledAsAProxy(t *testing.T) {
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(tradingNoon))
	d := g.Detail(context.Background(), "BBCA", 1000)

	for _, field := range d.Fields {
		if field.Name != "not_suspended" {
			continue
		}
		if field.Source != SuspensionProxyNote {
			t.Errorf("not_suspended source = %q, want %q", field.Source, SuspensionProxyNote)
		}
		if !strings.Contains(field.Source, "proxy") {
			t.Errorf("not_suspended source %q does not say it is a proxy", field.Source)
		}
		return
	}
	t.Fatal("no not_suspended field in Detail")
}

func TestGate_BandVerdicts(t *testing.T) {
	cases := []struct {
		name  string
		price int64
		want  risk.TriState
	}{
		{"below ARB", 899, risk.Deny},
		{"on ARB", 900, risk.Allow},
		{"inside", 1000, risk.Allow},
		{"on ARA", 1100, risk.Allow},
		{"above ARA", 1101, risk.Deny},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeStack(t)
			g := newTestGate(t, f, newFakeClock(tradingNoon))
			d := g.Detail(context.Background(), "BBCA", tc.price)
			if d.State.WithinBand != tc.want {
				t.Errorf("WithinBand(%d) = %s, want %s", tc.price, d.State.WithinBand, tc.want)
			}
		})
	}
}

// TestGate_UnusableBandIsUnknownNotUnbounded: a missing or inverted band is
// the absence of a constraint reading, not the absence of a constraint.
func TestGate_UnusableBandIsUnknownNotUnbounded(t *testing.T) {
	cases := map[string]string{
		"absent auto_reject_price": `{"data":{"order_book":{"asset_code":"BBCA"}}}`,
		"zero bounds":              `{"data":{"auto_reject_price":{"above":"0","below":"0"}}}`,
		"inverted":                 `{"data":{"auto_reject_price":{"above":"900","below":"1100"}}}`,
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			f := newFakeStack(t)
			f.setBody(pathOrderBook, body)
			g := newTestGate(t, f, newFakeClock(tradingNoon))

			state, err := g.State(context.Background(), "BBCA", 1000)
			if state.WithinBand != risk.Unknown {
				t.Errorf("WithinBand = %s, want unknown", state.WithinBand)
			}
			if err == nil {
				t.Error("State returned a nil error for an unusable band; RiskSink must see this as unresolved")
			}
		})
	}
}

// TestGate_StaleCacheIsNeverServed is the safety property the package doc
// claims. Warm the cache, let it expire, then break the endpoint: the
// answer must become Unknown, never the value that was true a minute ago.
func TestGate_StaleCacheIsNeverServed(t *testing.T) {
	f := newFakeStack(t)
	clock := newFakeClock(tradingNoon)
	g := newTestGate(t, f, clock)
	ctx := context.Background()

	if state, err := g.State(ctx, "BBCA", 1000); err != nil || state.Tradable != risk.Allow {
		t.Fatalf("warm-up: state = %+v, err = %v; want a clean allow", state, err)
	}

	// Every endpoint now fails, and every TTL has expired.
	f.setStatus(pathMarketTime, http.StatusInternalServerError)
	f.setStatus(pathMarketSession, http.StatusInternalServerError)
	f.setStatus(pathStockTradable, http.StatusInternalServerError)
	f.setStatus(pathOrderBook, http.StatusInternalServerError)
	clock.Advance(DefaultBandTTL + time.Second)

	state, err := g.State(ctx, "BBCA", 1000)
	if err == nil {
		t.Fatal("State returned nil error after every source failed")
	}
	if state.MarketOpen != risk.Unknown {
		t.Errorf("MarketOpen = %s, want unknown — a stale phase must not be served", state.MarketOpen)
	}
	if state.Tradable != risk.Unknown || state.NotSuspended != risk.Unknown {
		t.Errorf("Tradable/NotSuspended = %s/%s, want unknown/unknown", state.Tradable, state.NotSuspended)
	}
	if state.WithinBand != risk.Unknown {
		t.Errorf("WithinBand = %s, want unknown — a stale band must not be served", state.WithinBand)
	}
}

// TestGate_FreshCacheIsReused keeps the TTLs honest in the other direction:
// if every call refetched, the gate would put four requests on the order
// path per order.
func TestGate_FreshCacheIsReused(t *testing.T) {
	f := newFakeStack(t)
	clock := newFakeClock(tradingNoon)
	g := newTestGate(t, f, clock)
	ctx := context.Background()

	for i := 0; i < 3; i++ {
		clock.Advance(time.Second) // still inside every TTL
		if _, err := g.State(ctx, "BBCA", 1000); err != nil {
			t.Fatalf("State #%d: %v", i, err)
		}
	}
	for _, path := range []string{pathMarketTime, pathMarketSession, pathStockTradable, pathOrderBook} {
		if got := f.hitCount(path); got != 1 {
			t.Errorf("%s fetched %d times, want 1 (TTL should have covered all three calls)", path, got)
		}
	}
}

func TestGate_EachTTLExpiresIndependently(t *testing.T) {
	f := newFakeStack(t)
	clock := newFakeClock(tradingNoon)
	g := newTestGate(t, f, clock)
	ctx := context.Background()

	if _, err := g.State(ctx, "BBCA", 1000); err != nil {
		t.Fatalf("warm-up: %v", err)
	}
	// Past the session TTL, inside the tradability and band TTLs.
	clock.Advance(DefaultSessionTTL + time.Second)
	if _, err := g.State(ctx, "BBCA", 1000); err != nil {
		t.Fatalf("second call: %v", err)
	}

	if got := f.hitCount(pathMarketSession); got != 2 {
		t.Errorf("session fetched %d times, want 2 (its TTL expired)", got)
	}
	if got := f.hitCount(pathStockTradable); got != 1 {
		t.Errorf("tradability fetched %d times, want 1 (its TTL had not expired)", got)
	}
	if got := f.hitCount(pathOrderBook); got != 1 {
		t.Errorf("band fetched %d times, want 1 (its TTL had not expired)", got)
	}
}

// TestGate_ClockSteppedBackwardsInvalidatesCache: an entry stamped in the
// future is not fresh, it is untrustworthy. Without this, an NTP step
// backwards would pin every cached answer for the length of the jump.
func TestGate_ClockSteppedBackwardsInvalidatesCache(t *testing.T) {
	f := newFakeStack(t)
	clock := newFakeClock(tradingNoon)
	g := newTestGate(t, f, clock)
	ctx := context.Background()

	if _, err := g.State(ctx, "BBCA", 1000); err != nil {
		t.Fatalf("warm-up: %v", err)
	}
	clock.Advance(-time.Hour)
	if _, err := g.State(ctx, "BBCA", 1000); err != nil {
		t.Fatalf("after step back: %v", err)
	}
	if got := f.hitCount(pathStockTradable); got != 2 {
		t.Errorf("tradability fetched %d times, want 2 — a future-stamped entry must be refetched", got)
	}
}

func TestGate_PerSymbolCachesDoNotBleed(t *testing.T) {
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(tradingNoon))
	ctx := context.Background()

	if _, err := g.State(ctx, "BBCA", 1000); err != nil {
		t.Fatalf("BBCA: %v", err)
	}
	// A different symbol must not be answered from BBCA's cached row.
	f.setBody(pathStockTradable, `{"data":[{"stock_code":"BBRI","is_tradable":false}]}`)
	state, _ := g.State(ctx, "BBRI", 1000)
	if state.Tradable != risk.Deny {
		t.Errorf("BBRI Tradable = %s, want deny — BBCA's cached row must not answer for BBRI", state.Tradable)
	}
}

// TestGate_OneFailingSourceDoesNotSuppressTheOthers: an operator reading a
// rejection needs the whole picture, not the first thing that broke.
func TestGate_OneFailingSourceDoesNotSuppressTheOthers(t *testing.T) {
	f := newFakeStack(t)
	f.setStatus(pathOrderBook, http.StatusBadGateway)
	g := newTestGate(t, f, newFakeClock(tradingNoon))

	d := g.Detail(context.Background(), "BBCA", 1000)
	if d.State.WithinBand != risk.Unknown {
		t.Errorf("WithinBand = %s, want unknown", d.State.WithinBand)
	}
	if d.State.NotHoliday != risk.Allow || d.State.MarketOpen != risk.Allow || d.State.Tradable != risk.Allow {
		t.Errorf("other fields = %+v; a Carina outage must not erase the answers that were obtained", d.State)
	}
	if len(d.Fields) != 5 {
		t.Errorf("Detail carries %d fields, want 5", len(d.Fields))
	}
	if d.Err() == nil {
		t.Error("Err() = nil despite a failed source")
	}
}

// TestGate_AllowImpliesItsSourceAnswered is the invariant that makes State's
// two return values safe to disagree about detail but never about danger: a
// field may only be Allow when the source that owns it actually answered.
// Injecting a failure into each source in turn must turn that source's
// field(s) away from Allow every time.
func TestGate_AllowImpliesItsSourceAnswered(t *testing.T) {
	cases := []struct {
		path   string
		fields func(risk.MarketState) []risk.TriState
	}{
		{pathMarketTime, func(s risk.MarketState) []risk.TriState { return []risk.TriState{s.MarketOpen} }},
		{pathMarketSession, func(s risk.MarketState) []risk.TriState { return []risk.TriState{s.MarketOpen} }},
		{pathStockTradable, func(s risk.MarketState) []risk.TriState { return []risk.TriState{s.Tradable, s.NotSuspended} }},
		{pathOrderBook, func(s risk.MarketState) []risk.TriState { return []risk.TriState{s.WithinBand} }},
	}
	for _, tc := range cases {
		t.Run(tc.path, func(t *testing.T) {
			f := newFakeStack(t)
			f.setStatus(tc.path, http.StatusInternalServerError)
			g := newTestGate(t, f, newFakeClock(tradingNoon))

			state, err := g.State(context.Background(), "BBCA", 1000)
			if err == nil {
				t.Fatalf("State returned nil error with %s failing", tc.path)
			}
			for i, v := range tc.fields(state) {
				if v != risk.Unknown {
					t.Errorf("field %d = %s with %s failing, want unknown", i, v, tc.path)
				}
			}
		})
	}
}

func TestGate_ConcurrentCallsAreSafe(t *testing.T) {
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(tradingNoon))
	ctx := context.Background()

	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			sym := "BBCA"
			if i%2 == 0 {
				sym = "BBRI"
			}
			_, _ = g.State(ctx, sym, 1000)
		}(i)
	}
	wg.Wait()
}

func fieldReason(t *testing.T, d Detail, name string) string {
	t.Helper()
	for _, f := range d.Fields {
		if f.Name == name {
			return f.Reason
		}
	}
	t.Fatalf("no %q field in Detail", name)
	return ""
}
