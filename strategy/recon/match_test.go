package recon

import (
	"math/rand"
	"testing"
	"time"

	"strategy/account"
	"strategy/engine"
)

var wib = account.WIB

func bt(id, side, date, clock string, price float64, lots int, fee float64) account.HistoryTrade {
	ts, err := time.ParseInLocation("2006-01-02 15:04:05", date+" "+clock, wib)
	if err != nil {
		panic(err)
	}
	return account.HistoryTrade{ID: id, Symbol: "GOTO", Side: side, PriceIDR: price, Lots: lots, FeeIDR: fee, Time: ts}
}

func sf(order, side, date, clock string, price int64, lots int, fee float64) engine.StoreFill {
	ts, err := time.ParseInLocation("2006-01-02 15:04:05", date+" "+clock, wib)
	if err != nil {
		panic(err)
	}
	return engine.StoreFill{OrderID: order, RunID: "r-" + order, Symbol: "GOTO", Side: side, RecvNs: ts.UnixNano(), PriceIDR: price, Lots: lots, FeeIDR: fee}
}

// The real scenario (synthetic ids): two bot buys match, the manual sell is
// broker-only.
func realScenario() ([]account.HistoryTrade, []engine.StoreFill) {
	broker := []account.HistoryTrade{
		bt("900000003", "buy", "2026-10-14", "15:38:00", 28, 1, 4.2),
		bt("900000002", "sell", "2026-10-13", "14:14:15", 26, 1, 6.5),
		bt("900000001", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05),
	}
	fills := []engine.StoreFill{
		sf("rebalance-GOTO-2026-10-13", "buy", "2026-10-13", "14:12:02", 27, 1, 0),
		sf("rebalance-GOTO-2026-10-14", "buy", "2026-10-14", "15:38:01", 28, 1, 4),
	}
	return broker, fills
}

func TestMatchTrades_RealScenario(t *testing.T) {
	broker, fills := realScenario()
	m := MatchTrades(broker, fills, DefaultMatchTolerance)

	if len(m.Matched) != 2 {
		t.Fatalf("matched = %d, want 2: %+v", len(m.Matched), m.Matched)
	}
	if len(m.BrokerOnly) != 1 || m.BrokerOnly[0].ID != "900000002" || m.BrokerOnly[0].Side != "sell" {
		t.Fatalf("broker-only = %+v, want the manual sell 900000002", m.BrokerOnly)
	}
	if len(m.StoreOnly) != 0 {
		t.Fatalf("store-only = %+v, want none", m.StoreOnly)
	}
	byID := map[string]Pair{}
	for _, p := range m.Matched {
		byID[p.Broker.ID] = p
	}
	p27 := byID["900000001"]
	if p27.Fill.OrderID != "rebalance-GOTO-2026-10-13" || p27.FeeGap() != 4.05 {
		t.Errorf("10-13 buy pair wrong: %+v gap=%v", p27, p27.FeeGap())
	}
	if !p27.FeeGapFlagged() {
		t.Error("gap 4.05 must be flagged (> Rp 0.5)")
	}
	p28 := byID["900000003"]
	if gap := p28.FeeGap(); gap < 0.19 || gap > 0.21 {
		t.Errorf("10-14 buy gap = %v, want ~0.2 (4.2 vs 4)", gap)
	}
	if p28.FeeGapFlagged() {
		t.Error("gap 0.2 is within Rp 0.5 and must not be flagged")
	}
}

func TestMatchTrades_StoreOnlyIsPhantom(t *testing.T) {
	broker, fills := realScenario()
	fills = append(fills, sf("phantom", "buy", "2026-10-14", "10:00:00", 30, 2, 9))
	m := MatchTrades(broker, fills, DefaultMatchTolerance)
	if len(m.StoreOnly) != 1 || m.StoreOnly[0].OrderID != "phantom" {
		t.Fatalf("store-only = %+v, want the phantom fill", m.StoreOnly)
	}
}

func TestMatchTrades_KeyMustAgree(t *testing.T) {
	base := sf("o", "buy", "2026-10-13", "14:12:02", 27, 1, 4)
	cases := map[string]engine.StoreFill{
		"price":  sf("o", "buy", "2026-10-13", "14:12:02", 28, 1, 4),
		"lots":   sf("o", "buy", "2026-10-13", "14:12:02", 27, 2, 4),
		"side":   sf("o", "sell", "2026-10-13", "14:12:02", 27, 1, 4),
		"date":   sf("o", "buy", "2026-10-14", "14:12:02", 27, 1, 4),
		"time":   sf("o", "buy", "2026-10-13", "14:30:00", 27, 1, 4),
		"symbol": func() engine.StoreFill { f := base; f.Symbol = "BBCA"; return f }(),
	}
	broker := []account.HistoryTrade{bt("1", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05)}
	if m := MatchTrades(broker, []engine.StoreFill{base}, DefaultMatchTolerance); len(m.Matched) != 1 {
		t.Fatalf("control did not match: %+v", m)
	}
	for name, f := range cases {
		m := MatchTrades(broker, []engine.StoreFill{f}, DefaultMatchTolerance)
		if len(m.Matched) != 0 || len(m.BrokerOnly) != 1 || len(m.StoreOnly) != 1 {
			t.Errorf("%s differs but still matched: %+v", name, m)
		}
	}
}

// Two identical-looking trades must pair deterministically, whatever order the
// inputs arrive in.
func TestMatchTrades_TiesAreDeterministic(t *testing.T) {
	broker := []account.HistoryTrade{
		bt("B1", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05),
		bt("B2", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05),
	}
	fills := []engine.StoreFill{
		sf("F1", "buy", "2026-10-13", "14:12:01", 27, 1, 4),
		sf("F2", "buy", "2026-10-13", "14:12:01", 27, 1, 4),
	}
	want := ""
	for seed := int64(0); seed < 20; seed++ {
		r := rand.New(rand.NewSource(seed))
		b := append([]account.HistoryTrade(nil), broker...)
		f := append([]engine.StoreFill(nil), fills...)
		r.Shuffle(len(b), func(i, j int) { b[i], b[j] = b[j], b[i] })
		r.Shuffle(len(f), func(i, j int) { f[i], f[j] = f[j], f[i] })
		m := MatchTrades(b, f, DefaultMatchTolerance)
		if len(m.Matched) != 2 {
			t.Fatalf("seed %d: matched %d, want 2", seed, len(m.Matched))
		}
		got := ""
		for _, p := range m.Matched {
			got += p.Broker.ID + "=" + p.Fill.OrderID + ";"
		}
		if want == "" {
			want = got
		} else if got != want {
			t.Fatalf("seed %d: pairing %q differs from %q", seed, got, want)
		}
	}
}

func TestMatchTrades_ClosestInTimeWins(t *testing.T) {
	broker := []account.HistoryTrade{bt("B", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05)}
	fills := []engine.StoreFill{
		sf("far", "buy", "2026-10-13", "14:15:00", 27, 1, 4),
		sf("near", "buy", "2026-10-13", "14:12:03", 27, 1, 4),
	}
	m := MatchTrades(broker, fills, DefaultMatchTolerance)
	if len(m.Matched) != 1 || m.Matched[0].Fill.OrderID != "near" {
		t.Fatalf("matched %+v, want the nearer fill", m.Matched)
	}
	if len(m.StoreOnly) != 1 || m.StoreOnly[0].OrderID != "far" {
		t.Fatalf("store-only %+v, want the far fill", m.StoreOnly)
	}
}
