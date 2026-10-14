package main

import (
	"bytes"
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

const testAcc = "SYNTH-ACC-1"

type fakeBroker struct {
	shares  map[string]float64
	history account.History
	histErr error
	pfErr   error
	hCalls  int
}

func (f *fakeBroker) PortfolioList(ctx context.Context) (account.PortfolioList, error) {
	if f.pfErr != nil {
		return account.PortfolioList{}, f.pfErr
	}
	var pl account.PortfolioList
	for s, n := range f.shares {
		pl.Results = append(pl.Results, account.Position{Symbol: s, Qty: account.Settlement{Balance: account.LotShare{Share: n, Lot: n / 100}}})
	}
	return pl, nil
}

func (f *fakeBroker) History(ctx context.Context, q account.HistoryQuery) (account.History, error) {
	f.hCalls++
	return f.history, f.histErr
}

func wibTime(s string) time.Time {
	t, err := time.ParseInLocation("2006-01-02 15:04:05", s, account.WIB)
	if err != nil {
		panic(err)
	}
	return t
}

func makeLiveStore(t *testing.T, dir, name, account, orderID string, price int64, fee float64, at time.Time) {
	t.Helper()
	s, err := engine.OpenStore(filepath.Join(dir, name))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.BeginRun(engine.RunRow{RunID: "run-" + name, StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: "live", StrategyID: "s", InputSpec: "GOTO", Account: account}); err != nil {
		t.Fatal(err)
	}
	if err := s.InsertIntent(engine.IntentRow{Seq: 1, RecvNs: at.UnixNano(), StrategyID: "s", Symbol: "GOTO", Side: "buy", PriceIDR: price, Lots: 1, OrderID: orderID, SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}
	if err := s.InsertFill(engine.FillRow{Seq: 2, RecvNs: at.UnixNano(), OrderID: orderID, FillPriceIDR: price, FillLots: 1, FeeIDR: fee, SinkKind: "live"}); err != nil {
		t.Fatal(err)
	}
}

func historyTrade(id, side, at string, price float64, fee float64) account.HistoryTrade {
	return account.HistoryTrade{ID: id, Symbol: "GOTO", Side: side, PriceIDR: price, Lots: 1, FeeIDR: fee, Time: wibTime(at)}
}

// scenario reproduces the 2026-10-14 incident with synthetic data: legacy
// store (no account) holds the 10-13 buy at fee 0, the sleeve store holds the
// 10-14 buy at fee 4; the broker's history also shows a manual sell.
func scenario(t *testing.T) (multiConfig, *fakeBroker, string) {
	t.Helper()
	dir := t.TempDir()
	makeLiveStore(t, dir, "2026-10-13-live-2.db", "", "o1", 27, 0, wibTime("2026-10-13 14:12:02"))
	makeLiveStore(t, dir, "2026-10-14-roundtrip-buy.db", testAcc, "o2", 28, 4, wibTime("2026-10-14 15:38:01"))
	fb := &fakeBroker{
		shares: map[string]float64{"GOTO": 100},
		history: account.History{Trades: []account.HistoryTrade{
			historyTrade("900000003", "buy", "2026-10-14 15:38:00", 28, 4.2),
			historyTrade("900000002", "sell", "2026-10-13 14:14:15", 26, 6.5),
			historyTrade("900000001", "buy", "2026-10-13 14:12:00", 27, 4.05),
		}, Unknown: map[string]int{"MYSTERY": 3}},
	}
	cfg := multiConfig{
		Stores: []string{filepath.Join(dir, "*.db")}, SinkKind: "live", AccNo: testAcc, Label: "sleeve", TokenAccount: testAcc,
		ManualPath: filepath.Join(dir, "manual_trades.json"), LedgerPath: filepath.Join(dir, "broker_trades.json"),
		OutDir: filepath.Join(dir, "recon"), UseHistory: true, DoImport: true,
	}
	return cfg, fb, dir
}

var now = wibTime("2026-10-14 21:00:00")

func TestRunMulti_IncidentMatchesAndImportsTheManualSell(t *testing.T) {
	cfg, fb, _ := scenario(t)
	var out, errb bytes.Buffer
	code := runMulti(context.Background(), cfg, fb, now, &out, &errb)
	if code != 0 {
		t.Fatalf("exit = %d\nstdout:\n%s\nstderr:\n%s", code, out.String(), errb.String())
	}
	if !strings.Contains(out.String(), "Positions match the broker") {
		t.Errorf("stdout:\n%s", out.String())
	}
	if !strings.Contains(errb.String(), "MYSTERY") {
		t.Errorf("unknown command not logged: %q", errb.String())
	}

	raw, err := os.ReadFile(filepath.Join(cfg.OutDir, "2026-10-14.json"))
	if err != nil {
		t.Fatalf("report not written: %v", err)
	}
	report := string(raw)
	for _, want := range []string{`"status": "match"`, `"account_label": "sleeve"`, `"expected_lots": 1`, `"broker_lots": 1`, `"fee_gap_idr": 4.05`, `"newly_imported": 1`} {
		if !strings.Contains(report, want) {
			t.Errorf("report missing %s\n%s", want, report)
		}
	}
	if strings.Contains(report, testAcc) {
		t.Error("report contains the account number")
	}

	ledger, err := os.ReadFile(cfg.LedgerPath)
	if err != nil {
		t.Fatalf("ledger not written: %v", err)
	}
	if !strings.Contains(string(ledger), `"broker_id": "900000002"`) || !strings.Contains(string(ledger), `"source": "broker-import"`) {
		t.Errorf("ledger = %s", ledger)
	}
	if strings.Contains(string(ledger), testAcc) {
		t.Error("ledger contains the account number")
	}
}

func TestRunMulti_RerunIsIdempotent(t *testing.T) {
	cfg, fb, _ := scenario(t)
	var out, errb bytes.Buffer
	if code := runMulti(context.Background(), cfg, fb, now, &out, &errb); code != 0 {
		t.Fatalf("first run exit %d: %s%s", code, out.String(), errb.String())
	}
	first, _ := os.ReadFile(cfg.LedgerPath)
	out.Reset()
	if code := runMulti(context.Background(), cfg, fb, now, &out, &errb); code != 0 {
		t.Fatalf("second run exit %d: %s", code, out.String())
	}
	second, _ := os.ReadFile(cfg.LedgerPath)
	if !bytes.Equal(first, second) {
		t.Errorf("ledger changed on re-run:\n%s\n---\n%s", first, second)
	}
}

func TestRunMulti_MismatchExitsNonZeroWhenHistoryUnavailable(t *testing.T) {
	cfg, fb, _ := scenario(t)
	fb.histErr = errors.New("boom")
	var out, errb bytes.Buffer
	code := runMulti(context.Background(), cfg, fb, now, &out, &errb)
	if code != 1 {
		t.Fatalf("exit = %d, want 1 (stores say 2, broker 1, history unavailable)\n%s", code, out.String())
	}
	if !strings.Contains(out.String(), "DO NOT match") || !strings.Contains(out.String(), "NOT done") {
		t.Errorf("stdout:\n%s", out.String())
	}
	if _, err := os.Stat(cfg.LedgerPath); err == nil {
		t.Error("ledger written although history failed")
	}
}

func TestRunMulti_HistorySkippedWhenTokenAccountDiffers(t *testing.T) {
	cfg, fb, _ := scenario(t)
	cfg.TokenAccount = "SOME-OTHER-ACCOUNT"
	var out, errb bytes.Buffer
	code := runMulti(context.Background(), cfg, fb, now, &out, &errb)
	if fb.hCalls != 0 {
		t.Error("/history is token-scoped: it must not be called for a different account")
	}
	if code != 1 || !strings.Contains(out.String(), "token's account") {
		t.Errorf("exit=%d\n%s", code, out.String())
	}
}

func TestRunMulti_NoImportLeavesLedgerAlone(t *testing.T) {
	cfg, fb, _ := scenario(t)
	cfg.DoImport = false
	var out, errb bytes.Buffer
	runMulti(context.Background(), cfg, fb, now, &out, &errb)
	if _, err := os.Stat(cfg.LedgerPath); err == nil {
		t.Error("--no-import wrote the ledger")
	}
}

func TestRunMulti_BrokerFailureIsAnError(t *testing.T) {
	cfg, fb, _ := scenario(t)
	fb.pfErr = account.ErrUnauthorized
	var out, errb bytes.Buffer
	if code := runMulti(context.Background(), cfg, fb, now, &out, &errb); code != 1 {
		t.Fatalf("exit = %d, want 1", code)
	}
	if _, err := os.Stat(filepath.Join(cfg.OutDir, "2026-10-14.json")); err == nil {
		t.Error("a report was written although the broker could not be read")
	}
}

func TestRunMulti_NoStoresMatchedIsUsageError(t *testing.T) {
	cfg, fb, dir := scenario(t)
	cfg.Stores = []string{filepath.Join(dir, "nothing-*.db")}
	var out, errb bytes.Buffer
	if code := runMulti(context.Background(), cfg, fb, now, &out, &errb); code != 2 {
		t.Fatalf("exit = %d, want 2", code)
	}
}
