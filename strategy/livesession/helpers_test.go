package livesession

import (
	"bytes"
	"context"
	"encoding/base64"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"collector/calendar"
	"strategy/account"
	"strategy/engine"
	"strategy/portfolio"
)

// testAcc is a synthetic sub-account number.
const testAcc = "TEST-SLEEVE"

func tokenFor(acn string) string {
	return "h." + base64.RawURLEncoding.EncodeToString([]byte(`{"data":{"acn":"`+acn+`"}}`)) + ".s"
}

type fakeCal struct{ open bool }

func (c fakeCal) IsTradingDay(time.Time) bool { return c.open }

// fakeBroker is a recon.PortfolioSource with mutable holdings (shares).
type fakeBroker struct {
	mu     sync.Mutex
	shares map[string]float64
	err    error
}

func (b *fakeBroker) set(sym string, shares float64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.shares == nil {
		b.shares = map[string]float64{}
	}
	b.shares[sym] = shares
}

func (b *fakeBroker) PortfolioList(context.Context) (account.PortfolioList, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.err != nil {
		return account.PortfolioList{}, b.err
	}
	var pl account.PortfolioList
	for s, n := range b.shares {
		pl.Results = append(pl.Results, account.Position{Symbol: s, Qty: account.Settlement{Balance: account.LotShare{Share: n, Lot: n / 100}}})
	}
	return pl, nil
}

type recNotifier struct {
	mu     sync.Mutex
	alerts []Alert
}

func (r *recNotifier) Notify(_ context.Context, a Alert) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.alerts = append(r.alerts, a)
	return nil
}

func (r *recNotifier) kinds() []Kind {
	r.mu.Lock()
	defer r.mu.Unlock()
	var ks []Kind
	for _, a := range r.alerts {
		ks = append(ks, a.Kind)
	}
	return ks
}

func (r *recNotifier) has(k Kind) bool {
	for _, x := range r.kinds() {
		if x == k {
			return true
		}
	}
	return false
}

func (r *recNotifier) find(k Kind) (Alert, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, a := range r.alerts {
		if a.Kind == k {
			return a, true
		}
	}
	return Alert{}, false
}

// fakeChild stands in for strategy-run.
type fakeChild struct {
	mu     sync.Mutex
	calls  [][]string
	behave func(n int, args []string, ctx context.Context) int
}

func (f *fakeChild) Run(ctx context.Context, args []string) (int, error) {
	f.mu.Lock()
	f.calls = append(f.calls, append([]string{}, args...))
	n := len(f.calls)
	f.mu.Unlock()
	return f.behave(n, args, ctx), nil
}

func (f *fakeChild) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

func argValue(args []string, flag string) string {
	for i, a := range args {
		if a == flag && i+1 < len(args) {
			return args[i+1]
		}
	}
	return ""
}

func hasArg(args []string, flag string) bool {
	for _, a := range args {
		if a == flag {
			return true
		}
	}
	return false
}

type storeSpec struct {
	hash     string
	orders   int
	fills    int
	buyLots  int // writes one live buy fill of this many lots for GOTO when > 0
	rejects  []string
	account  string
	noRun    bool
	sink     string // default "live"; "paper" writes paper rows with run.account ""
	symbol   string // default GOTO
	sellLots int    // writes one sell fill of this many lots when > 0 (after the buy)
}

// writeStore creates a store file the way strategy-run would.
func writeStore(t *testing.T, path string, sp storeSpec) {
	t.Helper()
	st, err := engine.OpenStore(path)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	if sp.noRun {
		return
	}
	acc := sp.account
	if acc == "" && sp.sink != "paper" {
		acc = testAcc
	}
	sink, sym := sp.sink, sp.symbol
	if sink == "" {
		sink = "live"
	}
	if sym == "" {
		sym = "GOTO"
	}
	if err := st.BeginRun(engine.RunRow{RunID: "run-" + filepath.Base(path), StartedNs: 1, Source: "live", FillMode: "taker", SinkKind: sink, StrategyID: "calibration", InputSpec: sym, BinaryHash: sp.hash, Account: acc}); err != nil {
		t.Fatal(err)
	}
	ts := time.Date(2026, 10, 15, 13, 45, 1, 0, account.WIB).UnixNano()
	seq := int64(1)
	if sp.buyLots > 0 {
		id := "o-" + filepath.Base(path)
		if err := st.InsertIntent(engine.IntentRow{Seq: seq, RecvNs: ts, StrategyID: "calibration", Symbol: sym, Side: "buy", PriceIDR: 28, Lots: sp.buyLots, OrderID: id, SinkKind: sink}); err != nil {
			t.Fatal(err)
		}
		seq++
		if err := st.InsertFill(engine.FillRow{Seq: seq, RecvNs: ts, OrderID: id, FillPriceIDR: 28, FillLots: sp.buyLots, FeeIDR: 4, SinkKind: sink}); err != nil {
			t.Fatal(err)
		}
		seq++
	}
	if sp.sellLots > 0 {
		id := "s-" + filepath.Base(path)
		if err := st.InsertIntent(engine.IntentRow{Seq: seq, RecvNs: ts + 1, StrategyID: "calibration", Symbol: sym, Side: "sell", PriceIDR: 29, Lots: sp.sellLots, OrderID: id, SinkKind: sink}); err != nil {
			t.Fatal(err)
		}
		seq++
		if err := st.InsertFill(engine.FillRow{Seq: seq, RecvNs: ts + 1, OrderID: id, FillPriceIDR: 29, FillLots: sp.sellLots, FeeIDR: 7, SinkKind: sink}); err != nil {
			t.Fatal(err)
		}
		seq++
	}
	for i, reason := range sp.rejects {
		if err := st.InsertOrderReject(engine.RejectRow{Seq: seq, RecvNs: ts, StrategyID: "calibration", Symbol: "BBCA", Side: "buy", PriceIDR: 100, Lots: 1, OrderID: "r" + string(rune('a'+i)), Reason: reason, SinkKind: "live"}); err != nil {
			t.Fatal(err)
		}
		seq++
	}
	if err := st.FinishRun(2, engine.Counters{OrdersAttempted: sp.orders, Fills: sp.fills, TotalFeesIDR: 4}, nil); err != nil {
		t.Fatal(err)
	}
}

type env struct {
	t      *testing.T
	runs   string
	cfg    Config
	deps   Deps
	broker *fakeBroker
	child  *fakeChild
	notif  *recNotifier
	out    *bytes.Buffer
	binSHA string
}

func testNow() time.Time { return time.Date(2026, 10, 15, 10, 0, 0, 0, calendar.Jakarta) } // a Thursday

func newEnv(t *testing.T) *env {
	t.Helper()
	dir := t.TempDir()
	runs := filepath.Join(dir, "runs")
	if err := os.MkdirAll(runs, 0o755); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(dir, "strategy-run")
	if err := os.WriteFile(bin, []byte("fake strategy-run binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	sha, err := FileSHA256(bin)
	if err != nil {
		t.Fatal(err)
	}
	tp, err := portfolio.NullPortfolio(portfolio.NullOptions{AsOf: time.Date(2026, 10, 15, 8, 0, 0, 0, calendar.Jakarta), BaseNotionalIDR: 15_000_000})
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	if err := tp.Encode(&buf); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(dir, "target.json")
	if err := os.WriteFile(target, buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}

	e := &env{t: t, runs: runs, broker: &fakeBroker{}, child: &fakeChild{}, notif: &recNotifier{}, out: &bytes.Buffer{}, binSHA: sha}
	e.cfg = Config{
		Sink: "live", Source: "live", Strategy: "rebalance",
		Account: testAcc, EquityIDR: 10_000_000, TargetPortfolio: target, RebalanceAt: "13:45", Symbols: "GOTO",
		RunsDir: runs, StrategyRunBin: bin, MaxRestarts: 2, StopAfter: 3 * time.Minute, MinLead: time.Minute,
		KillPoll: 5 * time.Millisecond, ReconTries: 2,
		Invocation: []string{"live-session", "--account", testAcc, "--rebalance-at", "13:45"},
	}
	e.deps = Deps{
		Now: testNow, Cal: fakeCal{open: true}, Broker: e.broker, TradingToken: tokenFor(testAcc), Armed: true,
		Child: e.child, Notifier: e.notif, Out: e.out, Sleep: func(time.Duration) {},
		// Stop deadline far away unless a test overrides it.
		StopContext: func(p context.Context, _ time.Duration) (context.Context, context.CancelFunc) {
			return context.WithCancel(p)
		},
	}
	// Default child: a clean run that writes its store.
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(e.t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		return 0
	}
	return e
}

func (e *env) run() int { return New(e.cfg, e.deps).Run(context.Background()) }
