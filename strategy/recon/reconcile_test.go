package recon

import (
	"encoding/json"
	"regexp"
	"strings"
	"testing"
	"time"

	"strategy/account"
	"strategy/engine"
)

var asOf = time.Date(2026, 10, 14, 21, 0, 0, 0, wib)

func baseInputs() Inputs {
	broker, fills := realScenario()
	return Inputs{
		AccountLabel: "sleeve",
		AsOf:         asOf,
		StoreFiles:   []string{"2026-10-13-live-2.db", "2026-10-14-roundtrip-buy.db"},
		StoreFills:   fills,
		Holdings:     map[string]float64{"GOTO": 100},
		History:      &account.History{Trades: broker},
		Tolerance:    DefaultMatchTolerance,
	}
}

func symRow(t *testing.T, r Report, sym string) SymbolRow {
	t.Helper()
	for _, s := range r.Positions {
		if s.Symbol == sym {
			return s
		}
	}
	t.Fatalf("no row for %s in %+v", sym, r.Positions)
	return SymbolRow{}
}

// The incident: stores say 2 lots, the owner sold 1 by hand, the broker holds
// 1. With the broker history the manual sell is imported and positions match.
func TestReconcile_ManualSellIsImportedAndPositionsMatch(t *testing.T) {
	out := Reconcile(baseInputs())
	r := out.Report
	if r.Status != StatusMatch {
		t.Fatalf("status = %s, want match: %+v", r.Status, r.Positions)
	}
	g := symRow(t, r, "GOTO")
	if g.ExpectedLots != 1 || g.BrokerLots != 1 || g.Status != StatusMatch {
		t.Errorf("GOTO row = %+v", g)
	}
	if g.Breakdown.BotLots != 2 || g.Breakdown.BrokerImportLots != -1 || g.Breakdown.ManualLots != 0 {
		t.Errorf("breakdown = %+v, want bot +2, broker-import -1", g.Breakdown)
	}
	if !strings.Contains(g.Explanation, "broker-import") || !strings.Contains(g.Explanation, "900000002") {
		t.Errorf("explanation should say where the sell came from: %q", g.Explanation)
	}

	tc := r.TradeCheck
	if tc == nil || !tc.Checked || tc.Matched != 2 || tc.BrokerOnly != 1 || tc.StoreOnly != 0 {
		t.Fatalf("trade check = %+v", tc)
	}
	if len(out.NewImports) != 1 || out.NewImports[0].BrokerID != "900000002" || out.NewImports[0].Source != SourceBrokerImport ||
		out.NewImports[0].Side != "sell" || out.NewImports[0].PriceIDR != 26 || out.NewImports[0].FeeIDR != 6.5 ||
		out.NewImports[0].Date != "2026-10-13" || out.NewImports[0].TimeWIB != "14:14:15" {
		t.Fatalf("new imports = %+v", out.NewImports)
	}
	// Fee gaps are reported for the two matched buys.
	gaps := map[string]float64{}
	for _, m := range tc.MatchedTrades {
		gaps[m.BrokerID] = m.FeeGapIDR
	}
	if gaps["900000001"] != 4.05 || gaps["900000003"] != 0.2 {
		t.Errorf("fee gaps = %v", gaps)
	}
	if tc.FeeGapsFlagged != 1 {
		t.Errorf("flagged = %d, want 1 (only the 4.05 gap exceeds Rp 0.5)", tc.FeeGapsFlagged)
	}
}

// Idempotent: running again with the ledger already holding the import adds
// nothing, and expected still counts the sell exactly once.
func TestReconcile_ImportIsIdempotent(t *testing.T) {
	in := baseInputs()
	first := Reconcile(in)
	in.Imported = first.NewImports
	second := Reconcile(in)
	if len(second.NewImports) != 0 {
		t.Fatalf("second run imported again: %+v", second.NewImports)
	}
	if g := symRow(t, second.Report, "GOTO"); g.ExpectedLots != 1 || g.Status != StatusMatch {
		t.Errorf("second run GOTO = %+v", g)
	}
	if second.Report.TradeCheck.AlreadyImported != 1 {
		t.Errorf("already_imported = %d, want 1", second.Report.TradeCheck.AlreadyImported)
	}
}

// Without history (or before it is imported) the stores alone say 2 lots and
// the broker says 1: a loud mismatch with a useful explanation.
func TestReconcile_MismatchWithoutHistory(t *testing.T) {
	in := baseInputs()
	in.History = nil
	in.HistoryError = "history not fetched"
	r := Reconcile(in).Report
	if r.Status != StatusMismatch {
		t.Fatalf("status = %s, want mismatch", r.Status)
	}
	g := symRow(t, r, "GOTO")
	if g.ExpectedLots != 2 || g.BrokerLots != 1 || g.Status != StatusMismatch {
		t.Errorf("GOTO = %+v", g)
	}
	if !strings.Contains(g.Explanation, "broker holds 1 fewer") {
		t.Errorf("explanation should state the direction: %q", g.Explanation)
	}
	if r.TradeCheck == nil || r.TradeCheck.Checked || r.TradeCheck.Error == "" {
		t.Errorf("trade check should say history was not checked: %+v", r.TradeCheck)
	}
}

// A manual entry for the same trade is superseded by the broker import: the
// sell is counted once, not twice.
func TestReconcile_BrokerImportSupersedesManualEntry(t *testing.T) {
	in := baseInputs()
	in.Manual = []ManualTrade{{Date: "2026-10-13", TimeWIB: "", Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5, Note: "sold in app"}}
	out := Reconcile(in)
	g := symRow(t, out.Report, "GOTO")
	if g.ExpectedLots != 1 || g.Status != StatusMatch {
		t.Fatalf("double counted the sale: %+v", g)
	}
	if g.Breakdown.ManualLots != 0 || g.Breakdown.BrokerImportLots != -1 {
		t.Errorf("breakdown = %+v", g.Breakdown)
	}
	if out.Report.TradeCheck.ManualSuperseded != 1 {
		t.Errorf("manual_superseded = %d, want 1", out.Report.TradeCheck.ManualSuperseded)
	}
}

// A manual entry with no broker counterpart still counts (no history).
func TestReconcile_ManualTradeCountsWhenBrokerHistoryUnavailable(t *testing.T) {
	in := baseInputs()
	in.History = nil
	in.HistoryError = "skipped"
	in.Manual = []ManualTrade{{Date: "2026-10-13", Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5}}
	r := Reconcile(in).Report
	g := symRow(t, r, "GOTO")
	if g.ExpectedLots != 1 || g.Status != StatusMatch || g.Breakdown.ManualLots != -1 {
		t.Fatalf("GOTO = %+v", g)
	}
}

func TestReconcile_PhantomFillIsAnError(t *testing.T) {
	in := baseInputs()
	in.StoreFills = append(in.StoreFills, sf("phantom", "buy", "2026-10-13", "10:00:00", 30, 2, 9))
	out := Reconcile(in)
	tc := out.Report.TradeCheck
	if tc.StoreOnly != 1 || len(tc.StoreOnlyTrades) != 1 || tc.StoreOnlyTrades[0].OrderID != "phantom" {
		t.Fatalf("trade check = %+v", tc)
	}
	if len(out.Report.Errors) == 0 || !strings.Contains(strings.Join(out.Report.Errors, " "), "phantom") {
		t.Errorf("errors = %v, want the phantom fill named", out.Report.Errors)
	}
	// And the phantom inflates expected: positions mismatch too.
	if out.Report.Status != StatusMismatch {
		t.Errorf("status = %s, want mismatch", out.Report.Status)
	}
}

func TestReconcile_StoreFillsBeforeHistoryWindowAreNotPhantoms(t *testing.T) {
	in := baseInputs()
	in.WindowStart = time.Date(2026, 7, 14, 0, 0, 0, 0, wib)
	in.StoreFills = append(in.StoreFills, sf("old", "buy", "2026-05-01", "10:00:00", 30, 2, 9))
	in.Holdings = map[string]float64{"GOTO": 300}
	out := Reconcile(in)
	if out.Report.TradeCheck.StoreOnly != 0 {
		t.Fatalf("a fill before the history window cannot be called phantom: %+v", out.Report.TradeCheck)
	}
	if out.Report.TradeCheck.OutOfWindow != 1 {
		t.Errorf("out_of_window = %d, want 1", out.Report.TradeCheck.OutOfWindow)
	}
	if out.Report.Status != StatusMatch {
		t.Errorf("status = %s: %+v", out.Report.Status, out.Report.Positions)
	}
}

func TestReconcile_SymbolOnlyAtBrokerOrOnlyInStores(t *testing.T) {
	in := baseInputs()
	in.Holdings = map[string]float64{"GOTO": 100, "BBRI": 200}
	r := Reconcile(in).Report
	b := symRow(t, r, "BBRI")
	if b.ExpectedLots != 0 || b.BrokerLots != 2 || b.Status != StatusMismatch || !strings.Contains(b.Explanation, "no record") {
		t.Errorf("BBRI = %+v", b)
	}
	in = baseInputs()
	in.Holdings = map[string]float64{}
	r = Reconcile(in).Report
	if g := symRow(t, r, "GOTO"); g.Status != StatusMismatch || g.BrokerLots != 0 {
		t.Errorf("GOTO = %+v", g)
	}
}

func TestReconcile_FlatBothSidesOmitsSymbol(t *testing.T) {
	in := baseInputs()
	in.Holdings = map[string]float64{"GOTO": 0}
	in.History = &account.History{}
	in.StoreFills = nil
	r := Reconcile(in).Report
	if len(r.Positions) != 0 || r.Status != StatusMatch {
		t.Fatalf("report = %+v, want no rows and match", r)
	}
}

func TestReconcile_OddLotHoldingIsAMismatch(t *testing.T) {
	in := baseInputs()
	in.Holdings = map[string]float64{"GOTO": 150}
	g := symRow(t, Reconcile(in).Report, "GOTO")
	if g.Status != StatusMismatch || !strings.Contains(g.Explanation, "whole") {
		t.Errorf("GOTO = %+v", g)
	}
}

func TestReconcile_UnattributedStoreFillsAreDisclosed(t *testing.T) {
	in := baseInputs()
	in.StoreFills[0].Unattributed = true
	r := Reconcile(in).Report
	if r.Sources.UnattributedFills != 1 {
		t.Errorf("unattributed_fills = %d, want 1", r.Sources.UnattributedFills)
	}
}

// The report must carry no token and no account number: only the short label.
func TestReport_JSONHasNoAccountDetails(t *testing.T) {
	in := baseInputs()
	in.AccountLabel = "sleeve"
	in.History.CashEvents = []account.CashEvent{{ID: "c1", Command: "INTRA_MOVE_CASH_INCREASE", AmountIDR: 15000000, Status: "Success"}}
	in.History.Unknown = map[string]int{"WEIRD": 2}
	in.History.Skipped = map[string]int{"BUY(CANCELLED)": 1}
	out := Reconcile(in)
	raw, err := json.Marshal(out.Report)
	if err != nil {
		t.Fatal(err)
	}
	s := string(raw)
	for _, bad := range []string{"acc_no", "account_number", "move_stock_cash", "token", "Bearer"} {
		if strings.Contains(s, bad) {
			t.Errorf("report JSON contains %q", bad)
		}
	}
	// No brokerage-account-shaped value (6-7 digits, optional letter) may appear in the report.
	if m := regexp.MustCompile(`\b\d{6,7}[A-Z]?\b`).FindString(s); m != "" {
		t.Errorf("report JSON contains an account-shaped value %q", m)
	}
	for _, want := range []string{`"account_label":"sleeve"`, `"as_of":"2026-10-14T21:00:00+07:00"`, `"date":"2026-10-14"`, `"cash_events":1`, `"WEIRD":2`} {
		if !strings.Contains(s, want) {
			t.Errorf("report JSON missing %s\n%s", want, s)
		}
	}
	// Store names are basenames only.
	if strings.Contains(s, "/") {
		t.Errorf("report JSON contains a path separator: %s", s)
	}
}

// One bot fill can explain only one broker trade. Two identical broker trades
// and one fill: one matches, the other is a manual trade and must still count.
func TestReconcile_OneFillExplainsOnlyOneOfTwoIdenticalBrokerTrades(t *testing.T) {
	in := baseInputs()
	in.StoreFills = []engine.StoreFill{sf("f", "buy", "2026-10-13", "14:12:02", 27, 1, 4)}
	in.History = &account.History{Trades: []account.HistoryTrade{
		bt("A", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05),
		bt("B", "buy", "2026-10-13", "14:12:00", 27, 1, 4.05),
	}}
	in.Holdings = map[string]float64{"GOTO": 200}
	out := Reconcile(in)
	if out.Report.Status != StatusMatch || len(out.NewImports) != 1 {
		t.Fatalf("report = %+v imports = %+v", out.Report.Positions, out.NewImports)
	}
	// And again with the ledger holding that import.
	in.Imported = out.NewImports
	again := Reconcile(in)
	if again.Report.Status != StatusMatch || len(again.NewImports) != 0 {
		t.Fatalf("re-run: %+v imports = %+v", again.Report.Positions, again.NewImports)
	}
}

func TestReconcile_MatchedImportIsNotDoubleCounted(t *testing.T) {
	// A trade imported earlier that a store fill now explains must count once.
	in := baseInputs()
	in.Imported = []ImportedTrade{{BrokerID: "900000001", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:12:00", Symbol: "GOTO", Side: "buy", Lots: 1, PriceIDR: 27, FeeIDR: 4.05}}
	out := Reconcile(in)
	g := symRow(t, out.Report, "GOTO")
	if g.ExpectedLots != 1 || g.Status != StatusMatch {
		t.Fatalf("double counted a trade the stores explain: %+v", g)
	}
}

// A store-only fill dated today is not a bug: the broker lists today's trades
// from the next trading day. Positions still count it.
func pendingInputs(extra ...engine.StoreFill) Inputs {
	in := baseInputs()
	in.StoreFills = append(append([]engine.StoreFill{}, in.StoreFills...), extra...)
	return in
}

func TestReconcile_StoreOnlyFillDatedTodayIsPendingNotPhantom(t *testing.T) {
	in := pendingInputs(sf("rebalance-GOTO-2026-10-14-r2", "sell", "2026-10-14", "20:00:00", 26, 1, 0))
	r := Reconcile(in).Report
	if len(r.Errors) != 0 {
		t.Fatalf("errors = %v, want none", r.Errors)
	}
	tc := r.TradeCheck
	if tc.StoreOnly != 0 || tc.PendingHistory != 1 || len(tc.PendingHistoryTrades) != 1 ||
		tc.PendingHistoryTrades[0].OrderID != "rebalance-GOTO-2026-10-14-r2" {
		t.Fatalf("trade check = %+v", tc)
	}
	if len(r.Warnings) != 1 || !strings.Contains(r.Warnings[0], "awaiting broker history: store order rebalance-GOTO-2026-10-14-r2 (sell 1 GOTO @ 26 on 2026-10-14)") {
		t.Fatalf("warnings = %v", r.Warnings)
	}
	// The pending sell still counts in the expected position (bot: +2 bought, -1 pending sell).
	if g := symRow(t, r, "GOTO"); g.Breakdown.BotLots != 1 {
		t.Errorf("bot lots = %d, want 1 (pending sell counted)", g.Breakdown.BotLots)
	}
}

func TestReconcile_StoreOnlyFillBeforeTodayStaysPhantom(t *testing.T) {
	in := pendingInputs(sf("ghost", "sell", "2026-10-13", "10:00:00", 26, 1, 0))
	r := Reconcile(in).Report
	if len(r.Errors) != 1 || !strings.Contains(r.Errors[0], "phantom fill: store order ghost") {
		t.Fatalf("errors = %v", r.Errors)
	}
	tc := r.TradeCheck
	if tc.StoreOnly != 1 || tc.PendingHistory != 0 || len(r.Warnings) != 0 {
		t.Fatalf("trade check = %+v warnings = %v", tc, r.Warnings)
	}
}

func TestReconcile_MixedPendingAndPhantom(t *testing.T) {
	in := pendingInputs(
		sf("ghost", "sell", "2026-10-13", "10:00:00", 26, 1, 0),
		sf("today", "buy", "2026-10-14", "10:00:00", 28, 1, 0))
	r := Reconcile(in).Report
	tc := r.TradeCheck
	if tc.StoreOnly != 1 || tc.PendingHistory != 1 || len(r.Errors) != 1 || len(r.Warnings) != 1 ||
		!strings.Contains(r.Errors[0], "ghost") || !strings.Contains(r.Warnings[0], "today") {
		t.Fatalf("tc = %+v errors = %v warnings = %v", tc, r.Errors, r.Warnings)
	}
}

func TestReconcile_NoWarningsIsEmptyListInJSON(t *testing.T) {
	b, _ := json.Marshal(Reconcile(baseInputs()).Report)
	if !strings.Contains(string(b), `"warnings":[]`) || !strings.Contains(string(b), `"pending_history_trades":[]`) {
		t.Fatalf("json = %s", b)
	}
}
