package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"collector/calendar"
	"strategy/engine"
	"strategy/livesession"
)

// A 5-session paper hold carries across days: the day-1 paper fill is in a
// store on another date, and later runs must see it through the prefix glob
// live-session passes as --prior-stores.

const paperID = "wifi_momo_h5"

func paperTarget(t *testing.T, dir, day string, weight float64) string {
	t.Helper()
	p := filepath.Join(dir, "target_"+paperID+"_"+day+".json")
	body := fmt.Sprintf(`{"schema_version":1,"as_of":"%sT09:00:00+07:00","strategy_id":%q,"capital_model":"owned_cash",
"base_notional_idr":178100,"targets":[{"symbol":"WIFI","weight":%v}],
"constraints":{"max_participation_rate":0.05,"complete_by":"%sT11:30:00+07:00","board":"RG"}}`, day, paperID, weight, day)
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

// seedPaperStore writes what a paper strategy-run leaves behind: a paper run
// row (no account) with fills for WIFI at 1,370.
func seedPaperStore(t *testing.T, dir, day string, fills ...string) {
	t.Helper()
	st, err := engine.OpenStore(filepath.Join(dir, day+"-"+paperID+"-paper-a1.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	if err := st.BeginRun(engine.RunRow{RunID: "run-" + day, StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: "paper", StrategyID: paperID, InputSpec: "WIFI"}); err != nil {
		t.Fatal(err)
	}
	d, _ := time.ParseInLocation("2006-01-02", day, calendar.Jakarta)
	ns := d.Add(9*time.Hour + 31*time.Minute).UnixNano()
	for i, side := range fills {
		id := fmt.Sprintf("o-%s-%d", day, i)
		seq := int64(2*i + 1)
		if err := st.InsertIntent(engine.IntentRow{Seq: seq, RecvNs: ns, StrategyID: paperID, Symbol: "WIFI", Side: side, PriceIDR: 1370, Lots: 1, OrderID: id, SinkKind: "paper"}); err != nil {
			t.Fatal(err)
		}
		if err := st.InsertFill(engine.FillRow{Seq: seq + 1, RecvNs: ns, OrderID: id, FillPriceIDR: 1370, FillLots: 1, FeeIDR: 205.5, SinkKind: "paper"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := st.FinishRun(ns+1, engine.Counters{}, nil); err != nil {
		t.Fatal(err)
	}
}

// runPaperDay drives one paper day exactly as strategy-run wires it (newStrategy
// + loadPriorState over live-session's own prior-stores glob) and returns the
// orders the strategy submitted.
func runPaperDay(t *testing.T, dir, day string, weight float64) []engine.OrderIntent {
	t.Helper()
	globs := livesession.PriorStoreGlobs(livesession.Config{RunsDir: dir}, livesession.StoreKey("paper", paperID))
	strat, id, err := newStrategy("rebalance", paperTarget(t, dir, day, weight),
		&priorStateConfig{glob: joinGlobs(globs), sinkKind: "paper"}, 0, rebalanceAt{hour: 9, min: 30}, 0)
	if err != nil || id != paperID {
		t.Fatalf("newStrategy: id=%q err=%v", id, err)
	}
	d, _ := time.ParseInLocation("2006-01-02", day, calendar.Jakarta)
	ns := d.Add(9*time.Hour + 35*time.Minute).UnixNano()
	fe := &fakeStrategyEngine{now: time.Unix(0, ns)}
	strat.OnEvent(context.Background(), liveEventFor("WIFI", 1370, ns), fe)
	return fe.calls
}

func joinGlobs(g []string) string {
	out := ""
	for i, s := range g {
		if i > 0 {
			out += ","
		}
		out += s
	}
	return out
}

func TestPaperHold_DayOneBuysOneLot(t *testing.T) {
	dir := t.TempDir()
	calls := runPaperDay(t, dir, "2026-10-16", 1.0)
	if len(calls) != 1 || calls[0].Side != "buy" || calls[0].Lots != 1 {
		t.Fatalf("day 1 calls = %+v, want one 1-lot buy", calls)
	}
}

func TestPaperHold_Day2SeesTheDay1FillAndDoesNotRebuy(t *testing.T) {
	dir := t.TempDir()
	seedPaperStore(t, dir, "2026-10-16", "buy")
	for _, day := range []string{"2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"} {
		if calls := runPaperDay(t, dir, day, 1.0); len(calls) != 0 {
			t.Fatalf("%s: orders %+v, want none (the position carries)", day, calls)
		}
	}
}

func TestPaperHold_ExitDaySellsToZero(t *testing.T) {
	dir := t.TempDir()
	seedPaperStore(t, dir, "2026-10-16", "buy")
	calls := runPaperDay(t, dir, "2026-10-23", 0)
	if len(calls) != 1 || calls[0].Side != "sell" || calls[0].Lots != 1 {
		t.Fatalf("exit day calls = %+v, want one 1-lot sell", calls)
	}
}

// The exit sell lives in a LATER store than the buy. Positions must be netted
// across stores: after the exit the strategy is flat and must not sell again.
func TestPaperHold_AfterTheExitSellTheNextDayIsFlat(t *testing.T) {
	dir := t.TempDir()
	seedPaperStore(t, dir, "2026-10-16", "buy")
	seedPaperStore(t, dir, "2026-10-23", "sell")
	if calls := runPaperDay(t, dir, "2026-10-26", 0); len(calls) != 0 {
		t.Fatalf("after exit: orders %+v, want none (flat)", calls)
	}
	// and a fresh entry the same day is a buy again
	if calls := runPaperDay(t, dir, "2026-10-26", 1.0); len(calls) != 1 || calls[0].Side != "buy" {
		t.Fatalf("re-entry calls = %+v", calls)
	}
}

// Why the glob must span days: a same-day-only glob forgets the day-1 fill.
func TestPaperHold_SameDayOnlyGlobForgetsThePositionAndRebuys(t *testing.T) {
	dir := t.TempDir()
	seedPaperStore(t, dir, "2026-10-16", "buy")
	strat, _, err := newStrategy("rebalance", paperTarget(t, dir, "2026-10-19", 1.0),
		&priorStateConfig{glob: filepath.Join(dir, "2026-10-19-*.db"), sinkKind: "paper"}, 0, rebalanceAt{hour: 9, min: 30}, 0)
	if err != nil {
		t.Fatal(err)
	}
	ns := time.Date(2026, 10, 19, 9, 35, 0, 0, calendar.Jakarta).UnixNano()
	fe := &fakeStrategyEngine{now: time.Unix(0, ns)}
	strat.OnEvent(context.Background(), liveEventFor("WIFI", 1370, ns), fe)
	if len(fe.calls) != 1 {
		t.Fatalf("same-day glob: %d orders, want 1 (documents the bug the all-days glob prevents)", len(fe.calls))
	}
}
