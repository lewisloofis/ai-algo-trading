package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"strategy/account"
	"strategy/engine"
)

type fakePortfolio struct {
	shares map[string]float64
	err    error
	calls  int
}

func (f *fakePortfolio) PortfolioList(ctx context.Context) (account.PortfolioList, error) {
	f.calls++
	if f.err != nil {
		return account.PortfolioList{}, f.err
	}
	var pl account.PortfolioList
	for s, n := range f.shares {
		pl.Results = append(pl.Results, account.Position{Symbol: s, Qty: account.Settlement{Balance: account.LotShare{Share: n, Lot: n / 100}}})
	}
	return pl, nil
}

// gateStores writes two live stores for account acc, each holding one GOTO
// buy, mirroring the two bot buys of 2026-10-13/14.
func gateStores(t *testing.T, acc string) string {
	t.Helper()
	dir := t.TempDir()
	buys := []struct {
		name, order string
		price       int64
		at          string
	}{
		{"2026-10-13-live-2.db", "o1", 27, "2026-10-13 14:12:02"},
		{"2026-10-14-roundtrip-buy.db", "o2", 28, "2026-10-14 15:38:01"},
	}
	for _, b := range buys {
		ts, err := time.ParseInLocation("2006-01-02 15:04:05", b.at, account.WIB)
		if err != nil {
			t.Fatal(err)
		}
		s, err := engine.OpenStore(filepath.Join(dir, b.name))
		if err != nil {
			t.Fatal(err)
		}
		if err := s.BeginRun(engine.RunRow{RunID: "run-" + b.name, StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: "GOTO", Account: acc}); err != nil {
			t.Fatal(err)
		}
		if err := s.InsertIntent(engine.IntentRow{Seq: 1, RecvNs: ts.UnixNano(), StrategyID: "s", Symbol: "GOTO", Side: "buy", PriceIDR: b.price, Lots: 1, OrderID: b.order, SinkKind: "live"}); err != nil {
			t.Fatal(err)
		}
		if err := s.InsertFill(engine.FillRow{Seq: 2, RecvNs: ts.UnixNano(), OrderID: b.order, FillPriceIDR: b.price, FillLots: 1, FeeIDR: 4, SinkKind: "live"}); err != nil {
			t.Fatal(err)
		}
		s.Close()
	}
	return dir
}

func gateCfg(dir string) positionGateConfig {
	return positionGateConfig{
		priorStores: filepath.Join(dir, "*.db"), account: "TEST-SLEEVE",
		manualPath: filepath.Join(dir, "manual_trades.json"), ledgerPath: filepath.Join(dir, "broker_trades.json"),
		reconCommand: "account-recon --stores 'runs/*.db'",
	}
}

// The incident: the bot's stores say 2 lots, the owner sold 1 by hand, the
// broker shows 1. The gate refuses to start.
func TestPositionGate_RefusesOnMismatch(t *testing.T) {
	dir := gateStores(t, "TEST-SLEEVE")
	fp := &fakePortfolio{shares: map[string]float64{"GOTO": 100}}
	_, err := runPositionGate(context.Background(), gateCfg(dir), fp)
	if err == nil {
		t.Fatal("stores say 2 lots, broker 1: the gate must refuse")
	}
	for _, want := range []string{"GOTO", "expected 2", "broker holds 1", "--allow-position-mismatch", "account-recon --stores"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("message missing %q:\n%v", want, err)
		}
	}
	if fp.calls != 1 {
		t.Errorf("portfolio calls = %d, want 1", fp.calls)
	}
}

func TestPositionGate_PassesAndStartsFromBrokerConsistentPosition(t *testing.T) {
	dir := gateStores(t, "TEST-SLEEVE")
	// The manual sale has been imported by account-recon.
	if err := os.WriteFile(filepath.Join(dir, "broker_trades.json"), []byte(`{"trades":[{"broker_id":"900000002","source":"broker-import","date":"2026-10-13","time_wib":"14:14:15","symbol":"GOTO","side":"sell","lots":1,"price_idr":26,"fee_idr":6.5}]}`), 0o644); err != nil {
		t.Fatal(err)
	}
	fp := &fakePortfolio{shares: map[string]float64{"GOTO": 100}}
	res, err := runPositionGate(context.Background(), gateCfg(dir), fp)
	if err != nil {
		t.Fatalf("gate refused although the records now explain the broker: %v", err)
	}
	// The strategy must load 1 lot, not the stores' 2.
	if res.initial["GOTO"] != 1 || len(res.initial) != 1 {
		t.Fatalf("initial = %v, want GOTO=1", res.initial)
	}
	if res.override != "" {
		t.Errorf("override = %q on a clean pass", res.override)
	}
}

func TestPositionGate_OverrideIsExplicitAndRecorded(t *testing.T) {
	dir := gateStores(t, "TEST-SLEEVE")
	cfg := gateCfg(dir)
	cfg.allowMismatch = true
	fp := &fakePortfolio{shares: map[string]float64{"GOTO": 100}}
	res, err := runPositionGate(context.Background(), cfg, fp)
	if err != nil {
		t.Fatalf("override must let the run start: %v", err)
	}
	for _, want := range []string{"--allow-position-mismatch", "GOTO", "expected 2", "broker holds 1"} {
		if !strings.Contains(res.override, want) {
			t.Errorf("override text missing %q: %q", want, res.override)
		}
	}
}

func TestPositionGate_BrokerErrorRefusesEvenWithOverride(t *testing.T) {
	dir := gateStores(t, "TEST-SLEEVE")
	cfg := gateCfg(dir)
	cfg.allowMismatch = true
	fp := &fakePortfolio{err: account.ErrUnauthorized}
	if _, err := runPositionGate(context.Background(), cfg, fp); !errors.Is(err, account.ErrUnauthorized) {
		t.Fatalf("err = %v: an unreadable broker is not a mismatch and cannot be overridden", err)
	}
}

func TestPositionGate_NoPriorStoresAndFlatBrokerPasses(t *testing.T) {
	dir := t.TempDir()
	res, err := runPositionGate(context.Background(), gateCfg(dir), &fakePortfolio{})
	if err != nil || len(res.initial) != 0 {
		t.Fatalf("res = %+v err = %v", res, err)
	}
}

// Holdings the stores know nothing about (first run of the day, broker already
// holds a position) are exactly what the gate is for.
func TestPositionGate_BrokerHoldsWhatStoresDoNotKnow(t *testing.T) {
	dir := t.TempDir()
	_, err := runPositionGate(context.Background(), gateCfg(dir), &fakePortfolio{shares: map[string]float64{"GOTO": 100}})
	if err == nil || !strings.Contains(err.Error(), "no record") {
		t.Fatalf("err = %v, want a mismatch naming the missing record", err)
	}
}

// Fills recorded under another account are not this account's position.
func TestPositionGate_OtherAccountsFillsAreIgnored(t *testing.T) {
	dir := gateStores(t, "SOME-OTHER-ACCOUNT")
	res, err := runPositionGate(context.Background(), gateCfg(dir), &fakePortfolio{})
	if err != nil || len(res.initial) != 0 {
		t.Fatalf("res = %+v err = %v", res, err)
	}
}

// The gate must run next to the token-account guard and BEFORE the store, run
// row or any sink exists, and only for --sink live. run() is not unit-callable
// (it reads flags and the environment), so pin the order in the source.
func TestPositionGate_RunsAfterTokenGuardBeforeStoreAndSink(t *testing.T) {
	src, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	s := string(src)
	order := []string{"checkTokenAccount(tradingToken", "runPositionGate(gateCtx", "engine.OpenStore(*storePath)", "newInnerSink(", "store.BeginRun("}
	prev := -1
	for _, needle := range order {
		i := strings.Index(s, needle)
		if i < 0 {
			t.Fatalf("main.go lacks %q", needle)
		}
		if i < prev {
			t.Errorf("%q appears before the previous step: the gate must sit between the token guard and the store/sink", needle)
		}
		prev = i
	}
	gate := strings.Index(s, "runPositionGate(gateCtx")
	if !strings.Contains(s[strings.LastIndex(s[:gate], "if *sinkName"):gate], `*sinkName == "live"`) {
		t.Error("the gate must be inside an `if *sinkName == \"live\"` block (paper/shadow skip it)")
	}
}

func TestPositionGate_BadManualFileRefuses(t *testing.T) {
	dir := gateStores(t, "TEST-SLEEVE")
	if err := os.WriteFile(filepath.Join(dir, "manual_trades.json"), []byte(`not json`), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := runPositionGate(context.Background(), gateCfg(dir), &fakePortfolio{shares: map[string]float64{"GOTO": 200}}); err == nil {
		t.Fatal("an unreadable manual-trades file must stop the gate, not be treated as no trades")
	}
}
