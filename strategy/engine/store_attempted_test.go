package engine

import "testing"

// TestAttemptedWindows_KeyedByJakartaDateAndSymbol pins the key format this
// function promises: "<jakarta-date>|<symbol>", identical to what
// strategies/rebalance.Strategy's own internal attempted map uses (window +
// "|" + symbol, where window is a Jakarta calendar date for a DailyAt
// schedule) — see rebalance.Options.Attempted's doc comment. A caller seeds
// that field directly from this result with no reformatting.
func TestAttemptedWindows_KeyedByJakartaDateAndSymbol(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	// 2026-09-24 09:05 WIB.
	recvNs := int64(1790039100000000000)
	insertFilledOrder(t, store, &seq, "live", "TINS", "buy", 1000, 30, 4500, recvNs)

	got, err := store.AttemptedWindows("test", "live")
	if err != nil {
		t.Fatalf("AttemptedWindows: %v", err)
	}
	wantKey := JakartaDate(recvNs) + "|TINS"
	if !got[wantKey] {
		t.Fatalf("AttemptedWindows = %v, want key %q present", got, wantKey)
	}
	if len(got) != 1 {
		t.Fatalf("AttemptedWindows = %v, want exactly 1 entry", got)
	}
}

// TestAttemptedWindows_FiltersByStrategyAndSinkKind proves a restarted
// process only sees ITS OWN prior attempts — not another strategy's, and
// not a different sink kind's (a paper backtest must never gate a live
// strategy's restart recovery, or vice versa).
func TestAttemptedWindows_FiltersByStrategyAndSinkKind(t *testing.T) {
	store := newTestStore(t)
	recvNs := int64(1790039100000000000)
	if err := store.InsertIntent(IntentRow{
		Seq: 1, RecvNs: recvNs, StrategyID: "calibration", Symbol: "TINS", Side: "buy",
		PriceIDR: 1000, Lots: 30, OrderID: "a", SinkKind: "live",
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	if err := store.InsertIntent(IntentRow{
		Seq: 2, RecvNs: recvNs, StrategyID: "orb", Symbol: "BBCA", Side: "buy",
		PriceIDR: 9000, Lots: 1, OrderID: "b", SinkKind: "live",
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}
	if err := store.InsertIntent(IntentRow{
		Seq: 3, RecvNs: recvNs, StrategyID: "calibration", Symbol: "AMMN", Side: "buy",
		PriceIDR: 2500, Lots: 12, OrderID: "c", SinkKind: "paper",
	}); err != nil {
		t.Fatalf("InsertIntent: %v", err)
	}

	got, err := store.AttemptedWindows("calibration", "live")
	if err != nil {
		t.Fatalf("AttemptedWindows: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("AttemptedWindows = %v, want exactly 1 entry (calibration/live/TINS only)", got)
	}
	if !got[JakartaDate(recvNs)+"|TINS"] {
		t.Fatalf("AttemptedWindows = %v, missing TINS", got)
	}
}

// TestAttemptedWindows_EmptyStoreReturnsEmptySet is the first-run case: a
// brand-new store (no prior attempts today or ever) must not fabricate a
// gate — a fresh process must be free to attempt everything.
func TestAttemptedWindows_EmptyStoreReturnsEmptySet(t *testing.T) {
	store := newTestStore(t)
	got, err := store.AttemptedWindows("calibration", "live")
	if err != nil {
		t.Fatalf("AttemptedWindows: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("AttemptedWindows = %v, want empty", got)
	}
}

// TestAttemptedWindowsForRetry pins which prior attempts block which --retry
// level: retry 0 sees every attempt (historical behaviour); retry N sees only
// attempts whose order id carries the exact "-r<N>" suffix.
func TestAttemptedWindowsForRetry(t *testing.T) {
	recvNs := int64(1790039100000000000) // 2026-09-24 09:05 WIB
	ids := map[string]string{"AAA": "rebalance-AAA-2026-09-24", "BBB": "rebalance-BBB-2026-09-24-r2", "CCC": "rebalance-CCC-2026-09-24-r3", "DDD": "rebalance-DDD-2026-09-24-r22"}
	cases := []struct {
		name  string
		retry int
		want  []string
	}{
		{"retry 0 sees all", 0, []string{"AAA", "BBB", "CCC", "DDD"}},
		{"retry 2 sees only -r2", 2, []string{"BBB"}},
		{"retry 3 sees only -r3", 3, []string{"CCC"}},
		{"retry 4 sees none", 4, nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			store := newTestStore(t)
			var seq int64
			for sym, id := range ids {
				seq++
				if err := store.InsertIntent(IntentRow{Seq: seq, RecvNs: recvNs, StrategyID: "test", Symbol: sym, Side: "buy", PriceIDR: 1, Lots: 1, OrderID: id, SinkKind: "live"}); err != nil {
					t.Fatalf("InsertIntent: %v", err)
				}
			}
			got, err := store.AttemptedWindowsForRetry("test", "live", c.retry)
			if err != nil {
				t.Fatalf("AttemptedWindowsForRetry: %v", err)
			}
			if len(got) != len(c.want) {
				t.Fatalf("got %v, want symbols %v", got, c.want)
			}
			for _, sym := range c.want {
				if !got[JakartaDate(recvNs)+"|"+sym] {
					t.Fatalf("got %v, missing %s", got, sym)
				}
			}
		})
	}
}
