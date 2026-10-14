package engine

import "testing"

func TestFillsListsSinkKindAndAccountFills(t *testing.T) {
	store := newTestStore(t)
	var seq int64
	mustRun := func(id, sink, account string, started int64) {
		t.Helper()
		if err := store.BeginRun(RunRow{RunID: id, StartedNs: started, Source: "live", FillMode: "taker", SinkKind: sink, StrategyID: "s", InputSpec: "X", Account: account}); err != nil {
			t.Fatal(err)
		}
	}
	mustRun("run-sleeve", "live", "SLEEVE", 1)
	insertFilledOrder(t, store, &seq, "live", "GOTO", "buy", 27, 1, 4, 1_000)
	mustRun("run-core", "live", "CORE", 2)
	insertFilledOrder(t, store, &seq, "live", "BBCA", "buy", 9000, 1, 13, 2_000)
	mustRun("run-legacy", "live", "", 3)
	insertFilledOrder(t, store, &seq, "live", "GOTO", "buy", 28, 1, 4, 3_000)
	mustRun("run-paper", "paper", "SLEEVE", 4)
	insertFilledOrder(t, store, &seq, "paper", "GOTO", "buy", 29, 1, 4, 4_000)

	strict, err := store.Fills("live", "SLEEVE", false)
	if err != nil {
		t.Fatal(err)
	}
	if len(strict) != 1 || strict[0].Symbol != "GOTO" || strict[0].PriceIDR != 27 || strict[0].Unattributed {
		t.Fatalf("strict fills = %+v, want only the SLEEVE live fill", strict)
	}

	loose, err := store.Fills("live", "SLEEVE", true)
	if err != nil {
		t.Fatal(err)
	}
	if len(loose) != 2 {
		t.Fatalf("with unattributed = %d fills, want 2 (SLEEVE + legacy no-account)", len(loose))
	}
	if loose[0].Unattributed || !loose[1].Unattributed {
		t.Errorf("Unattributed flags wrong: %+v", loose)
	}
	if loose[1].PriceIDR != 28 || loose[1].Side != "buy" || loose[1].Lots != 1 || loose[1].OrderID == "" || loose[1].RecvNs != 3_000 {
		t.Errorf("fill fields wrong: %+v", loose[1])
	}
}

func TestFillsKeepsFractionalFee(t *testing.T) {
	store := newTestStore(t)
	if err := store.BeginRun(RunRow{RunID: "r", StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: "X", Account: "A"}); err != nil {
		t.Fatal(err)
	}
	if err := store.InsertIntent(IntentRow{Seq: 1, RecvNs: 1, StrategyID: "s", Symbol: "GOTO", Side: "buy", PriceIDR: 28, Lots: 1, OrderID: "o", SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}
	if err := store.InsertFill(FillRow{Seq: 2, RecvNs: 1, OrderID: "o", FillPriceIDR: 28, FillLots: 1, FeeIDR: 4.2, SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}
	fills, err := store.Fills("live", "A", false)
	if err != nil || len(fills) != 1 || fills[0].FeeIDR != 4.2 {
		t.Fatalf("fills = %+v err = %v, want fee 4.2", fills, err)
	}
}
