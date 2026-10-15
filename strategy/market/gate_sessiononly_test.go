package market

import (
	"context"
	"strings"
	"testing"

	"strategy/risk"
)

// A session-only gate (paper sink, no trading token) answers the calendar and
// the Exodus session from the access token and never calls Carina.
func TestGate_SessionOnlyNeverCallsCarinaAndAllowsTheCarinaFields(t *testing.T) {
	f := newFakeStack(t)
	g, err := NewGate(loadCalendar(t), f.client(t), GateOptions{Clock: newFakeClock(tradingNoon).Now, SessionOnly: true})
	if err != nil {
		t.Fatal(err)
	}
	d := g.Detail(context.Background(), "BBCA", 1000)
	if d.Err() != nil {
		t.Fatalf("Err = %v", d.Err())
	}
	want := risk.MarketState{NotHoliday: risk.Allow, MarketOpen: risk.Allow, Tradable: risk.Allow, NotSuspended: risk.Allow, WithinBand: risk.Allow}
	if d.State != want {
		t.Errorf("state = %+v, want %+v", d.State, want)
	}
	if n := f.hitCount(pathStockTradable) + f.hitCount(pathOrderBook+"BBCA"); n != 0 {
		t.Errorf("session-only gate made %d Carina call(s)", n)
	}
	for _, fr := range d.Fields {
		if (fr.Name == "tradable" || fr.Name == "not_suspended" || fr.Name == "within_band") &&
			!contains(fr.Source, "not checked") {
			t.Errorf("%s source %q must say it was not checked", fr.Name, fr.Source)
		}
	}
}

func TestGate_SessionOnlyStillDeniesWhenTheMarketIsClosed(t *testing.T) {
	f := newFakeStack(t)
	f.setBody(pathMarketTime, bodyMarketClosed)
	g, err := NewGate(loadCalendar(t), f.client(t), GateOptions{Clock: newFakeClock(tradingNoon).Now, SessionOnly: true})
	if err != nil {
		t.Fatal(err)
	}
	if st, _ := g.State(context.Background(), "BBCA", 1000); st.MarketOpen == risk.Allow {
		t.Errorf("MarketOpen = %s with the market reported closed", st.MarketOpen)
	}
}

func TestGate_DefaultStillChecksCarina(t *testing.T) {
	f := newFakeStack(t)
	g := newTestGate(t, f, newFakeClock(tradingNoon))
	g.Detail(context.Background(), "BBCA", 1000)
	if f.hitCount(pathStockTradable) == 0 {
		t.Error("the default gate must still consult /stock/tradable")
	}
}

func contains(s, sub string) bool { return strings.Contains(s, sub) }
