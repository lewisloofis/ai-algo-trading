package recon

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeFile(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestLoadManual_SchemaAndMissingFile(t *testing.T) {
	dir := t.TempDir()
	got, err := LoadManual(filepath.Join(dir, "absent.json"))
	if err != nil || len(got) != 0 {
		t.Fatalf("missing file: got %v, %v; want none, nil", got, err)
	}
	p := writeFile(t, dir, "m.json", `{"_schema":"x","trades":[
	 {"date":"2026-10-13","time_wib":"14:14:15","symbol":"GOTO","side":"sell","lots":1,"price_idr":26,"fee_idr":6.5,"note":"sold in app","net_amount_idr":2593.5,"broker_realised_pnl_idr":-110.55,"closes_order_id":"o1"},
	 {"date":"2026-10-14","time_wib":null,"symbol":"BBCA","side":"buy","lots":2,"price_idr":9000,"fee_idr":27,"note":""}]}`)
	got, err = LoadManual(p)
	if err != nil {
		t.Fatalf("LoadManual: %v", err)
	}
	if len(got) != 2 || got[0].Symbol != "GOTO" || got[0].Side != "sell" || got[0].FeeIDR != 6.5 || got[1].TimeWIB != "" {
		t.Fatalf("parsed wrong: %+v", got)
	}
}

func TestLoadManual_StrictLikeTheDashboard(t *testing.T) {
	dir := t.TempDir()
	bad := map[string]string{
		"unknown field": `{"trades":[{"date":"2026-10-13","time_wib":null,"symbol":"GOTO","side":"buy","lots":1,"price_idr":26,"fee_idr":6,"note":"","surprise":1}]}`,
		"bad side":      `{"trades":[{"date":"2026-10-13","time_wib":null,"symbol":"GOTO","side":"hold","lots":1,"price_idr":26,"fee_idr":6,"note":""}]}`,
		"zero lots":     `{"trades":[{"date":"2026-10-13","time_wib":null,"symbol":"GOTO","side":"buy","lots":0,"price_idr":26,"fee_idr":6,"note":""}]}`,
		"bad date":      `{"trades":[{"date":"13/10/2026","time_wib":null,"symbol":"GOTO","side":"buy","lots":1,"price_idr":26,"fee_idr":6,"note":""}]}`,
		"no trades key": `{"rows":[]}`,
		"not json":      `nope`,
	}
	for name, body := range bad {
		if _, err := LoadManual(writeFile(t, dir, strings.ReplaceAll(name, " ", "_")+".json", body)); err == nil {
			t.Errorf("%s: want error", name)
		}
	}
}

func TestMergeImports_IdempotentByBrokerID(t *testing.T) {
	a := ImportedTrade{BrokerID: "2", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:14:15", Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5}
	b := ImportedTrade{BrokerID: "1", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:12:00", Symbol: "GOTO", Side: "buy", Lots: 1, PriceIDR: 27, FeeIDR: 4.05}

	once, added := MergeImports(nil, []ImportedTrade{a, b})
	if added != 2 || len(once) != 2 {
		t.Fatalf("first merge: added=%d len=%d", added, len(once))
	}
	twice, added := MergeImports(once, []ImportedTrade{a, b})
	if added != 0 || len(twice) != 2 {
		t.Fatalf("second merge must add nothing: added=%d len=%d", added, len(twice))
	}
	if twice[0].BrokerID != "1" {
		t.Errorf("not sorted by date/time: %+v", twice)
	}
}

func TestSaveLoadImports_RoundTripAndAtomic(t *testing.T) {
	p := filepath.Join(t.TempDir(), "sub", "broker_trades.json")
	in := []ImportedTrade{{BrokerID: "9", Source: SourceBrokerImport, Date: "2026-10-13", TimeWIB: "14:14:15", Symbol: "GOTO", Side: "sell", Lots: 1, PriceIDR: 26, FeeIDR: 6.5}}
	if err := SaveImports(p, in); err != nil {
		t.Fatal(err)
	}
	out, err := LoadImports(p)
	if err != nil || len(out) != 1 || out[0] != in[0] {
		t.Fatalf("round trip: %+v %v", out, err)
	}
	entries, _ := os.ReadDir(filepath.Dir(p))
	if len(entries) != 1 {
		t.Errorf("temp file left behind: %v", entries)
	}
	raw, _ := os.ReadFile(p)
	for _, bad := range []string{"acc_no", "move_stock_cash", "name"} {
		if strings.Contains(string(raw), bad) {
			t.Errorf("ledger file contains %q", bad)
		}
	}
}

func TestLoadImports_RejectsWrongSource(t *testing.T) {
	p := writeFile(t, t.TempDir(), "b.json", `{"trades":[{"broker_id":"1","source":"manual","date":"2026-10-13","time_wib":"14:12:00","symbol":"GOTO","side":"buy","lots":1,"price_idr":27,"fee_idr":4.05}]}`)
	if _, err := LoadImports(p); err == nil {
		t.Fatal("an entry whose source is not broker-import must be rejected")
	}
}
