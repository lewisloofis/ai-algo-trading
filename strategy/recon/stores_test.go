package recon

import (
	"path/filepath"
	"testing"

	"strategy/engine"
)

func makeStore(t *testing.T, dir, name, sink, account, symbol, side string, price int64, fee float64, recvNs int64) string {
	t.Helper()
	path := filepath.Join(dir, name)
	s, err := engine.OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.BeginRun(engine.RunRow{RunID: "run-" + name, StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: sink, StrategyID: "s", InputSpec: "X", Account: account}); err != nil {
		t.Fatal(err)
	}
	if err := s.InsertIntent(engine.IntentRow{Seq: 1, RecvNs: recvNs, StrategyID: "s", Symbol: symbol, Side: side, PriceIDR: price, Lots: 1, OrderID: "o-" + name, SinkKind: sink}); err != nil {
		t.Fatal(err)
	}
	if err := s.InsertFill(engine.FillRow{Seq: 2, RecvNs: recvNs, OrderID: "o-" + name, FillPriceIDR: price, FillLots: 1, FeeIDR: fee, SinkKind: sink}); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestResolveAndLoadStoreFills(t *testing.T) {
	dir := t.TempDir()
	makeStore(t, dir, "a-live.db", "live", "ACC", "GOTO", "buy", 27, 4.05, 1_000)
	makeStore(t, dir, "b-live.db", "live", "ACC", "GOTO", "buy", 28, 4.2, 2_000)
	makeStore(t, dir, "c-paper.db", "paper", "ACC", "GOTO", "buy", 29, 4, 3_000)
	makeStore(t, dir, "d-other.db", "live", "OTHER", "BBCA", "buy", 9000, 13, 4_000)
	makeStore(t, dir, "e-skip.db", "live", "ACC", "GOTO", "buy", 30, 4, 5_000)

	paths, err := ResolveStorePaths([]string{filepath.Join(dir, "*.db"), filepath.Join(dir, "a-live.db"), filepath.Join(dir, "none-*.db")}, []string{"e-skip.db"})
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) != 4 {
		t.Fatalf("paths = %v, want 4 distinct non-excluded files", paths)
	}
	names, fills, err := LoadStoreFills(paths, "live", "ACC", false)
	if err != nil {
		t.Fatal(err)
	}
	if len(names) != 4 || len(fills) != 2 || fills[0].PriceIDR != 27 || fills[1].FeeIDR != 4.2 {
		t.Fatalf("names=%v fills=%+v, want the two ACC live fills in time order", names, fills)
	}
	for _, n := range names {
		if filepath.Base(n) != n {
			t.Errorf("name %q is not a base name", n)
		}
	}
}
