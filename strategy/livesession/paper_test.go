package livesession

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"collector/calendar"
	"strategy/portfolio"
	"time"
)

// paperLive turns the default live env into `--sink paper --source live`: market
// data only, no trading token, no arming, no broker.
func paperLive(e *env) {
	e.cfg.Sink, e.cfg.Source, e.cfg.Account = "paper", "live", ""
	e.deps.TradingToken, e.deps.Armed, e.deps.Broker = "", false, nil
}

func TestPreflight_PaperLiveNeedsNoTradingTokenArmingOrAccount(t *testing.T) {
	e := newEnv(t)
	paperLive(e)
	if _, errs := Preflight(PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: true}}); len(errs) != 0 {
		t.Fatalf("errs = %v", errs)
	}
}

func TestPreflight_PaperLiveStillNeedsEquitySymbolsAndAnOpenMarket(t *testing.T) {
	e := newEnv(t)
	paperLive(e)
	e.cfg.EquityIDR, e.cfg.Symbols = 0, ""
	_, errs := Preflight(PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: false}})
	joined := ""
	for _, err := range errs {
		joined += err.Error() + "\n"
	}
	for _, want := range []string{"--equity-idr", "--symbols", "market is closed"} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q in:\n%s", want, joined)
		}
	}
}

func writeSidecarNextTo(t *testing.T, target, status string) {
	t.Helper()
	side := strings.TrimSuffix(target, ".json") + ".signal.json"
	if err := os.WriteFile(side, []byte(`{"spec_status":"`+status+`"}`), 0o644); err != nil {
		t.Fatal(err)
	}
}

// Only a candidate may arm a live sink.
func TestPreflight_LiveSinkRefusesAPaperSpecTarget(t *testing.T) {
	e := newEnv(t)
	writeSidecarNextTo(t, e.cfg.TargetPortfolio, "paper")
	_, errs := e.preflight()
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), `"paper"`) {
		t.Fatalf("errs = %v", errs)
	}
	writeSidecarNextTo(t, e.cfg.TargetPortfolio, "candidate")
	if _, errs := e.preflight(); len(errs) != 0 {
		t.Fatalf("candidate must pass: %v", errs)
	}
}

func TestPreflight_PaperSinkAcceptsAPaperSpecTarget(t *testing.T) {
	e := newEnv(t)
	paperLive(e)
	writeSidecarNextTo(t, e.cfg.TargetPortfolio, "paper")
	if _, errs := Preflight(PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: true}}); len(errs) != 0 {
		t.Fatalf("errs = %v", errs)
	}
}

func TestSession_PaperLive_DayOneThenDayTwoSeesThePaperPosition(t *testing.T) {
	e := newEnv(t)
	paperLive(e)
	// Day 1: the child buys one lot of WIFI into its (paper-named) store.
	e.child.behave = func(n int, args []string, _ context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA, orders: 1, fills: 1, buyLots: 1, sink: "paper", symbol: "WIFI"})
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("day 1 exit %d\n%s", code, e.out)
	}
	args := e.child.calls[0]
	if got := filepath.Base(argValue(args, "--store")); got != "2026-10-15-calibration-paper-a1.db" {
		t.Errorf("store = %s", got)
	}
	if argValue(args, "--sink") != "paper" || argValue(args, "--source") != "live" || hasArg(args, "--account") {
		t.Errorf("args = %v", args)
	}
	if !strings.Contains(argValue(args, "--prior-stores"), "*-calibration-paper-a[0-9]*.db") ||
		strings.Contains(argValue(args, "--prior-stores"), "*-calibration-a[0-9]*.db") {
		t.Errorf("prior-stores = %q", argValue(args, "--prior-stores"))
	}
	for _, want := range []string{"sink paper | source live", "paper sink: no broker", "WIFI 1 lot(s)", "CLEAN"} {
		if !strings.Contains(e.out.String(), want) {
			t.Errorf("summary missing %q:\n%s", want, e.out)
		}
	}
	if strings.Contains(e.out.String(), "MATCH") || strings.Contains(e.out.String(), "DIVERGENT") {
		t.Errorf("a paper summary must not claim a broker reconcile:\n%s", e.out)
	}

	// Day 2 (next trading day): the pre-run line reports the carried paper position.
	e.out.Reset()
	e.deps.Now = func() time.Time { return time.Date(2026, 10, 16, 10, 0, 0, 0, calendar.Jakarta) }
	tp, err := portfolio.NullPortfolio(portfolio.NullOptions{AsOf: time.Date(2026, 10, 16, 8, 0, 0, 0, calendar.Jakarta), BaseNotionalIDR: 15_000_000})
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	if err := tp.Encode(&buf); err != nil {
		t.Fatal(err)
	}
	day2 := filepath.Join(filepath.Dir(e.cfg.TargetPortfolio), "target2.json")
	if err := os.WriteFile(day2, buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	e.cfg.TargetPortfolio, e.cfg.RebalanceAt = day2, "10:30"
	e.child.behave = func(n int, args []string, _ context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA, sink: "paper", symbol: "WIFI"})
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("day 2 exit %d\n%s", code, e.out)
	}
	if !strings.Contains(e.out.String(), "pre-run (paper): position from 1 prior paper store(s): WIFI 1 lot(s)") {
		t.Errorf("day 2 must report the carried position:\n%s", e.out)
	}
	if got := filepath.Base(argValue(e.child.calls[1], "--store")); got != "2026-10-16-calibration-paper-a1.db" {
		t.Errorf("day 2 store = %s", got)
	}
}

func TestSession_PaperSinkNeverTouchesTheBroker(t *testing.T) {
	e := newEnv(t)
	paperLive(e)
	e.broker.err = os.ErrInvalid // would fail any broker read
	e.deps.Broker = e.broker
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d\n%s", code, e.out)
	}
}
