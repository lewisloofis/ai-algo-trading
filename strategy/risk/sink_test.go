package risk

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"strategy/engine"
)

func newTestSink(t *testing.T, equity SleeveEquityIDR, limits Limits, gate MarketGate, pnl PnLProvider, clock Clock) (*RiskSink, *recordingSink) {
	t.Helper()
	inner := &recordingSink{}
	sink, err := NewRiskSink(inner, equity, limits, gate, pnl, "", clock)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}
	return sink, inner
}

func mustAdmit(t *testing.T, sink *RiskSink, in engine.OrderIntent) {
	t.Helper()
	if _, err := sink.Submit(context.Background(), in); err != nil {
		t.Fatalf("expected admit for %+v, got %v", in, err)
	}
}

func TestNewRiskSink_RequiresInnerAndPnL(t *testing.T) {
	if _, err := NewRiskSink(nil, SleeveEquityIDR(1), permissiveLimits(), allowGate{}, fixedPnL{}, "", nil); err == nil {
		t.Fatal("expected error when inner sink is nil")
	}
	if _, err := NewRiskSink(&recordingSink{}, SleeveEquityIDR(1), permissiveLimits(), allowGate{}, nil, "", nil); err == nil {
		t.Fatal("expected error when pnl provider is nil")
	}
}

func TestNewRiskSink_NilMarketGateDefaultsToDenyAll(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), nil, fixedPnL{pnl: 0}, nil)
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrMarketUnknown) {
		t.Fatalf("expected a nil MarketGate to default to deny-all, got %v", err)
	}
	if inner.count() != 0 {
		t.Fatalf("rejected order reached inner sink")
	}
}

// --- per-order notional cap ---

func TestSubmit_PerOrderNotionalCap_BoundaryAndOverLimit(t *testing.T) {
	limits := permissiveLimits()
	limits.PerOrderNotionalFraction = 0.1 // cap = 1,500,000 IDR on a 15M sleeve
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, nil)

	// Exactly at the cap: 1 lot @ 15,000 IDR * 100 shares = 1,500,000 -> admit.
	mustAdmit(t, sink, intent("BBCA", "buy", 15_000, 1))
	if inner.count() != 1 {
		t.Fatalf("expected the boundary order to reach inner sink")
	}

	// Just over: 1 lot @ 15,025 IDR = 1,502,500 -> reject.
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 15_025, 1))
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("expected ErrCapExceeded just over the per-order cap, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("rejected order must not reach inner sink, got count=%d", inner.count())
	}
}

// --- per-day cumulative notional cap, with rollover ---

func TestSubmit_PerDayNotionalCap_BoundaryAndRollover(t *testing.T) {
	limits := permissiveLimits()
	limits.PerDayNotionalFraction = 0.2 // cap = 3,000,000 IDR

	day1 := time.Date(2026, 9, 15, 10, 0, 0, 0, time.UTC)
	cur := day1
	clock := func() time.Time { return cur }

	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, clock)

	// price 10,000 * 100 shares = 1,000,000 IDR/lot.
	// 3 lots = 3,000,000 -> exactly at the day cap -> admit.
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 3))

	// One more lot the same day -> day total 4,000,000 > 3,000,000 -> reject.
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("expected ErrCapExceeded once the day cap is exceeded, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("rejected order reached inner sink, count=%d", inner.count())
	}

	// Roll over to the next day: the same order must now admit because the
	// per-day counter reset.
	cur = day1.Add(24 * time.Hour)
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 1))
	if inner.count() != 2 {
		t.Fatalf("expected the post-rollover order to reach inner sink, count=%d", inner.count())
	}
}

// --- max gross exposure, across symbols, including a partial reduction ---

func TestSubmit_MaxGrossExposure_MultiSymbolAndPartialReduction(t *testing.T) {
	limits := permissiveLimits()
	limits.MaxGrossExposureFraction = 0.4 // cap = 6,000,000 IDR
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, nil)

	// BBCA: 3 lots @ 10,000 = 3,000,000. Gross so far: 3,000,000.
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 3))

	// TLKM: 3 lots @ 10,000 = 3,000,000. Gross so far: 6,000,000 -> exactly
	// at the cap -> admit.
	mustAdmit(t, sink, intent("TLKM", "buy", 10_000, 3))

	// BMRI: 1 lot @ 10,000 = 1,000,000 more would push gross to 7,000,000
	// > 6,000,000 -> reject.
	_, err := sink.Submit(context.Background(), intent("BMRI", "buy", 10_000, 1))
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("expected ErrCapExceeded over the gross exposure cap, got %v", err)
	}

	// Partially reduce BBCA by selling 2 lots (2,000,000), dropping gross to
	// 4,000,000. BMRI's 1,000,000 order now fits (gross -> 5,000,000).
	mustAdmit(t, sink, intent("BBCA", "sell", 10_000, 2))
	mustAdmit(t, sink, intent("BMRI", "buy", 10_000, 1))

	if inner.count() != 4 {
		t.Fatalf("expected 4 admitted orders to reach inner sink, got %d", inner.count())
	}
}

// --- max position per name ---

func TestSubmit_MaxPositionPerName_BoundaryAndOverLimit(t *testing.T) {
	limits := permissiveLimits()
	limits.MaxPositionPerNameFraction = 0.2 // cap = 3,000,000 IDR
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, nil)

	// 3 lots @ 10,000 = 3,000,000 -> exactly at the per-name cap -> admit.
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 3))

	// One more lot -> 4,000,000 > 3,000,000 -> reject.
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrCapExceeded) {
		t.Fatalf("expected ErrCapExceeded over the per-name cap, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("rejected order reached inner sink, count=%d", inner.count())
	}
}

// --- structural (local) checks ---

func TestSubmit_StructuralChecks(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{pnl: 0}, nil)

	cases := []struct {
		name   string
		intent engine.OrderIntent
	}{
		{"zero lots", intent("BBCA", "buy", 10_000, 0)},
		{"negative lots", intent("BBCA", "buy", 10_000, -1)},
		{"zero price", intent("BBCA", "buy", 0, 1)},
		{"negative price", intent("BBCA", "buy", -100, 1)},
		{"bad side", intent("BBCA", "hold", 10_000, 1)},
		{"off tick (>=5000 band, tick 25)", intent("BBCA", "buy", 10_013, 1)},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := sink.Submit(context.Background(), c.intent)
			if !errors.Is(err, ErrInvalidIntent) {
				t.Fatalf("expected ErrInvalidIntent, got %v", err)
			}
		})
	}
	if inner.count() != 0 {
		t.Fatalf("no structurally-invalid intent should reach inner sink, count=%d", inner.count())
	}

	// Just inside valid: positive, on-tick, buy order must admit.
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 1))
	if inner.count() != 1 {
		t.Fatalf("expected the valid order to reach inner sink")
	}
}

func TestIsOnApproxTickIDR_Bands(t *testing.T) {
	cases := []struct {
		price int64
		want  bool
	}{
		{199, true}, // tick 1 below 200: anything divides
		{198, true},
		{200, true},  // tick 2 band starts at 200
		{201, false}, // odd, tick 2
		{500, true},  // tick 5 band starts
		{503, false},
		{2000, true}, // tick 10 band starts
		{2005, false},
		{5000, true},  // tick 25 band starts
		{5010, false}, // not a multiple of 25
		{5025, true},
	}
	for _, c := range cases {
		if got := isOnApproxTickIDR(c.price); got != c.want {
			t.Errorf("isOnApproxTickIDR(%d) = %v, want %v", c.price, got, c.want)
		}
	}
}

// --- market gate (endpoint-backed, deny-by-default) ---

func TestSubmit_MarketGate_UnknownRejects(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), fixedGate{state: MarketState{}}, fixedPnL{pnl: 0}, nil)
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrMarketUnknown) {
		t.Fatalf("expected ErrMarketUnknown for an all-Unknown state, got %v", err)
	}
	if inner.count() != 0 {
		t.Fatalf("rejected order reached inner sink")
	}
}

func TestSubmit_MarketGate_PartialDenyRejects(t *testing.T) {
	state := allowState()
	state.WithinBand = Deny
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), fixedGate{state: state}, fixedPnL{pnl: 0}, nil)
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrMarketUnknown) {
		t.Fatalf("expected reject when one field is Deny, got %v", err)
	}
	if inner.count() != 0 {
		t.Fatalf("rejected order reached inner sink")
	}
}

func TestSubmit_MarketGate_ErrorRejects(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), fixedGate{state: allowState(), err: errors.New("endpoint down")}, fixedPnL{pnl: 0}, nil)
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrMarketUnknown) {
		t.Fatalf("expected reject on gate error, got %v", err)
	}
	if inner.count() != 0 {
		t.Fatalf("rejected order reached inner sink")
	}
}

func TestSubmit_MarketGate_AllAllowAdmits(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{pnl: 0}, nil)
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 1))
	if inner.count() != 1 {
		t.Fatalf("expected the admitted order to reach inner sink")
	}
}

func TestDenyAllGate_DeniesEverything(t *testing.T) {
	state, err := (DenyAllGate{}).State(context.Background(), "BBCA", 10_000)
	if err != nil {
		t.Fatalf("DenyAllGate must not error: %v", err)
	}
	if state.allAllow() {
		t.Fatalf("DenyAllGate must never allAllow")
	}
}

// --- daily loss limit ---

func TestSubmit_DailyLossLimit_BoundaryThenBreachTripsKillSwitch(t *testing.T) {
	limits := permissiveLimits()
	limits.DailyLossLimitFraction = 0.1 // cap = 1,500,000 IDR
	pnl := &mutablePnL{pnl: -1_500_000}
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), limits, allowGate{}, pnl, nil)

	// Exactly at the limit: not yet a breach -> admit, no trip.
	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 1))
	if tripped, _ := sink.KillSwitchTripped(); tripped {
		t.Fatalf("must not trip at the daily-loss boundary")
	}

	// One IDR worse -> breach -> reject and trip the kill switch.
	pnl.pnl = -1_500_001
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrDailyLossLimitBreached) {
		t.Fatalf("expected ErrDailyLossLimitBreached, got %v", err)
	}
	if tripped, _ := sink.KillSwitchTripped(); !tripped {
		t.Fatalf("expected the kill switch tripped after a breach")
	}

	// Even if PnL "recovers", the kill switch stays tripped: no auto-recovery.
	pnl.pnl = 0
	_, err = sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrKillSwitchTripped) {
		t.Fatalf("expected ErrKillSwitchTripped once tripped, got %v", err)
	}

	if inner.count() != 1 {
		t.Fatalf("expected exactly 1 admitted order before the breach, got %d", inner.count())
	}
}

func TestSubmit_DailyLoss_ProviderErrorRejectsWithoutTripping(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{err: errors.New("store unavailable")}, nil)
	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrDailyLossLimitBreached) {
		t.Fatalf("expected reject on pnl provider error, got %v", err)
	}
	if tripped, _ := sink.KillSwitchTripped(); tripped {
		t.Fatalf("a transient pnl query error must not permanently trip the kill switch")
	}
	if inner.count() != 0 {
		t.Fatalf("rejected order reached inner sink")
	}
}

// --- rejected intents never reach the inner sink (aggregate check) ---

func TestSubmit_RejectedIntentsNeverReachInnerSink(t *testing.T) {
	inner := &recordingSink{}
	limits := permissiveLimits()
	limits.PerOrderNotionalFraction = 0.01 // cap = 150,000 IDR, easy to exceed
	sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), limits, allowGate{}, fixedPnL{pnl: 0}, "", nil)
	if err != nil {
		t.Fatalf("NewRiskSink: %v", err)
	}

	rejects := []engine.OrderIntent{
		intent("BBCA", "buy", 10_000, 5),  // notional 5,000,000 >> 150,000 cap
		intent("BBCA", "buy", 10_000, 0),  // structural: zero lots
		intent("BBCA", "hold", 10_000, 1), // structural: bad side
	}
	for _, in := range rejects {
		if _, err := sink.Submit(context.Background(), in); err == nil {
			t.Fatalf("expected reject for %+v", in)
		}
	}
	if inner.count() != 0 {
		t.Fatalf("expected 0 intents to reach inner sink, got %d", inner.count())
	}

	// Sanity: the recorder does work when an order is actually admitted.
	mustAdmit(t, sink, intent("BBCA", "buy", 1_000, 1))
	if inner.count() != 1 {
		t.Fatalf("expected the admitted order to reach inner sink")
	}
}

// --- Cancel forwards unchanged ---

func TestCancel_ForwardsToInner(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{pnl: 0}, nil)
	if err := sink.Cancel(context.Background(), engine.OrderID("some-id")); err != nil {
		t.Fatalf("Cancel: %v", err)
	}
	if len(inner.cancelled) != 1 || inner.cancelled[0] != engine.OrderID("some-id") {
		t.Fatalf("expected Cancel forwarded to inner sink, got %+v", inner.cancelled)
	}
}

// --- TripKillSwitch / KillSwitchTripped (the ops-facing API) ---

func TestRiskSink_TripKillSwitch_RejectsSubsequentOrders(t *testing.T) {
	sink, inner := newTestSink(t, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{pnl: 0}, nil)

	mustAdmit(t, sink, intent("BBCA", "buy", 10_000, 1))

	sink.TripKillSwitch("manual operator trip")
	tripped, reason := sink.KillSwitchTripped()
	if !tripped || reason != "manual operator trip" {
		t.Fatalf("expected tripped with reason, got tripped=%v reason=%q", tripped, reason)
	}

	_, err := sink.Submit(context.Background(), intent("BBCA", "buy", 10_000, 1))
	if !errors.Is(err, ErrKillSwitchTripped) {
		t.Fatalf("expected ErrKillSwitchTripped, got %v", err)
	}
	if inner.count() != 1 {
		t.Fatalf("expected only the pre-trip order to have reached inner sink, got %d", inner.count())
	}
}

// --- TriState / MarketState plumbing ---

func TestTriState_String(t *testing.T) {
	cases := map[TriState]string{Allow: "allow", Deny: "deny", Unknown: "unknown", TriState(99): "unknown"}
	for ts, want := range cases {
		if got := ts.String(); got != want {
			t.Errorf("TriState(%d).String() = %q, want %q", ts, got, want)
		}
	}
}

func TestMarketState_FirstReject_EachField(t *testing.T) {
	cases := []struct {
		name  string
		state MarketState
		want  string
	}{
		{"not holiday", MarketState{NotHoliday: Deny, MarketOpen: Allow, Tradable: Allow, NotSuspended: Allow, WithinBand: Allow}, "not_holiday=deny"},
		{"market open", MarketState{NotHoliday: Allow, MarketOpen: Deny, Tradable: Allow, NotSuspended: Allow, WithinBand: Allow}, "market_open=deny"},
		{"tradable", MarketState{NotHoliday: Allow, MarketOpen: Allow, Tradable: Deny, NotSuspended: Allow, WithinBand: Allow}, "tradable=deny"},
		{"not suspended", MarketState{NotHoliday: Allow, MarketOpen: Allow, Tradable: Allow, NotSuspended: Deny, WithinBand: Allow}, "not_suspended=deny"},
		{"within band", MarketState{NotHoliday: Allow, MarketOpen: Allow, Tradable: Allow, NotSuspended: Allow, WithinBand: Deny}, "within_band=deny"},
		{"all allow", allowState(), ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := c.state.firstReject(); got != c.want {
				t.Errorf("firstReject() = %q, want %q", got, c.want)
			}
		})
	}
}

// --- board type (reference 06 §8) -------------------------------------------

// TestSubmit_BoardTypeIsGated covers the last reference-06 rule to be wired
// (plan 18 §5 B2). The two rejection cases are separate mistakes: an unset
// board is a strategy that never said which venue it meant; a set-but-other
// board is a strategy asking for a venue this system does not route to.
// Both must reject, and neither may reach the inner sink.
func TestSubmit_BoardTypeIsGated(t *testing.T) {
	cases := []struct {
		name  string
		board engine.BoardType
		want  bool // accepted?
	}{
		{"regular board is the one we route", engine.BoardRG, true},
		{"unset is not an implicit RG", "", false},
		{"cash/negotiated board", engine.BoardTN, false},
		{"new-growth board", engine.BoardNG, false},
		{"anything else", engine.BoardType("rg"), false}, // case-sensitive on purpose: the wire value is "RG"
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			inner := &recordingSink{}
			sink, err := NewRiskSink(inner, SleeveEquityIDR(15_000_000), permissiveLimits(), allowGate{}, fixedPnL{pnl: 0}, "", nil)
			if err != nil {
				t.Fatalf("NewRiskSink: %v", err)
			}

			in := intent("BBCA", "buy", 1000, 1)
			in.BoardType = tc.board

			_, err = sink.Submit(context.Background(), in)
			if tc.want {
				if err != nil {
					t.Fatalf("Submit(%q) = %v, want accepted", tc.board, err)
				}
				if inner.count() != 1 {
					t.Errorf("inner sink saw %d orders, want 1", inner.count())
				}
				return
			}
			if !errors.Is(err, ErrInvalidIntent) {
				t.Fatalf("Submit(%q) err = %v, want ErrInvalidIntent", tc.board, err)
			}
			if inner.count() != 0 {
				t.Errorf("inner sink saw %d orders for board %q, want 0", inner.count(), tc.board)
			}
		})
	}
}

// TestBoardTypeRejectionsAreDistinguishable: "you forgot to set it" and
// "we cannot route that" need different messages, because they are fixed in
// different places — the strategy versus the plan.
func TestBoardTypeRejectionsAreDistinguishable(t *testing.T) {
	unset := intent("BBCA", "buy", 1000, 1)
	unset.BoardType = ""
	other := intent("BBCA", "buy", 1000, 1)
	other.BoardType = engine.BoardTN

	errUnset := checkStructural(unset)
	errOther := checkStructural(other)
	if errUnset == nil || errOther == nil {
		t.Fatalf("both must reject: unset=%v other=%v", errUnset, errOther)
	}
	if !strings.Contains(errUnset.Error(), "unset") {
		t.Errorf("unset board error = %q, want it to say the field was never set", errUnset)
	}
	if !strings.Contains(errOther.Error(), "not routable") {
		t.Errorf("other board error = %q, want it to say the venue is not routable", errOther)
	}
}
