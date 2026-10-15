package livesession

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSession_CleanLiveRun(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA, orders: 1, fills: 1, buyLots: 1})
		e.broker.set("GOTO", 100)
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d, want 0\n%s", code, e.out)
	}
	if e.child.count() != 1 {
		t.Fatalf("child calls = %d", e.child.count())
	}
	args := e.child.calls[0]
	if got := filepath.Base(argValue(args, "--store")); got != "2026-10-15-calibration-a1.db" {
		t.Errorf("store = %s", got)
	}
	if !strings.Contains(argValue(args, "--prior-stores"), "*-calibration-a[0-9]*.db") {
		t.Errorf("prior-stores = %q", argValue(args, "--prior-stores"))
	}
	if hasArg(args, "--retry") {
		t.Error("--retry must never be added automatically")
	}
	if argValue(args, "--rebalance-at") != "13:45" || argValue(args, "--account") != testAcc {
		t.Errorf("args = %v", args)
	}
	for _, a := range args {
		if strings.Contains(a, "h.") && strings.HasSuffix(a, ".s") {
			t.Error("a token leaked onto the child command line")
		}
	}
	for _, want := range []string{"orders 1 | fills 1 | fees 4 IDR | rejects 0", "MATCH", "exit 0"} {
		if !strings.Contains(e.out.String(), want) {
			t.Errorf("summary missing %q:\n%s", want, e.out)
		}
	}
	if len(e.notif.alerts) != 0 {
		t.Errorf("unexpected alerts %v", e.notif.kinds())
	}
}

func TestSession_PreflightFailureStartsNothing(t *testing.T) {
	e := newEnv(t)
	e.deps.TradingToken = tokenFor("OTHER-ACCOUNT")
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d, want 2", code)
	}
	if e.child.count() != 0 {
		t.Fatal("child must not start after a refused preflight")
	}
	if !e.notif.has(KindRefused) {
		t.Fatal("no refused alert")
	}
	if entries, _ := os.ReadDir(e.runs); len(entries) != 0 {
		t.Fatalf("a refused session created files: %v", entries)
	}
}

func TestSession_PreSnapshotMismatchRefuses(t *testing.T) {
	e := newEnv(t)
	e.broker.set("GOTO", 100) // broker holds a position no store explains
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d, want 2", code)
	}
	if e.child.count() != 0 {
		t.Fatal("child started despite an unexplained broker position")
	}
	a, _ := e.notif.find(KindRefused)
	if !strings.Contains(a.Message, "GOTO") {
		t.Errorf("refusal should name the symbol: %s", a.Message)
	}
}

// The 2026-10-15 case: the broker holds GOTO, which a prior store explains,
// so the pre-snapshot passes (it is divergent only against an empty store).
func TestSession_PreSnapshotExplainedByPriorStoresPasses(t *testing.T) {
	e := newEnv(t)
	writeStore(t, filepath.Join(e.runs, "2026-10-14-calibration-a1.db"), storeSpec{hash: "x", orders: 1, fills: 1, buyLots: 1})
	e.broker.set("GOTO", 100)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d\n%s", code, e.out)
	}
	if got := filepath.Base(argValue(e.child.calls[0], "--store")); got != "2026-10-15-calibration-a1.db" {
		t.Errorf("store = %s", got)
	}
}

func TestSession_CrashRestartsWithFreshStoreAndNoRetry(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		if n == 1 {
			return 1
		}
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d\n%s", code, e.out)
	}
	if e.child.count() != 2 {
		t.Fatalf("child calls = %d, want 2", e.child.count())
	}
	s1, s2 := argValue(e.child.calls[0], "--store"), argValue(e.child.calls[1], "--store")
	if s1 == s2 || !strings.HasSuffix(s2, "-a2.db") {
		t.Errorf("stores %s, %s: want a fresh -a2", s1, s2)
	}
	if hasArg(e.child.calls[1], "--retry") {
		t.Error("restart must not add --retry")
	}
	if !e.notif.has(KindCrash) || e.notif.has(KindRepeatedCrash) {
		t.Errorf("alerts = %v", e.notif.kinds())
	}
}

func TestSession_RepeatedCrashExhaustsBudgetAndPrintsRetryCommand(t *testing.T) {
	e := newEnv(t)
	e.cfg.MaxRestarts = 1
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		return 1
	}
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d, want 2", code)
	}
	if e.child.count() != 2 {
		t.Fatalf("child calls = %d, want 2 (1 + 1 restart)", e.child.count())
	}
	a, ok := e.notif.find(KindRepeatedCrash)
	if !ok {
		t.Fatalf("alerts = %v", e.notif.kinds())
	}
	if !strings.Contains(a.Message, "--retry 2") || !strings.Contains(a.Message, "No automatic resubmission") {
		t.Errorf("message = %s", a.Message)
	}
	for _, c := range e.child.calls {
		if hasArg(c, "--retry") {
			t.Error("a crash restart passed --retry")
		}
	}
}

func TestSession_RestartAllowedAfterRebalanceAtButBeforeStopDeadline(t *testing.T) {
	e := newEnv(t)
	cur := time.Date(2026, 10, 15, 13, 40, 0, 0, testNow().Location())
	e.deps.Now = func() time.Time { return cur }
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		if n == 1 {
			cur = time.Date(2026, 10, 15, 13, 46, 0, 0, cur.Location()) // crashed after the window opened
			return 1
		}
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d\n%s", code, e.out)
	}
	if e.child.count() != 2 {
		t.Fatalf("child calls = %d", e.child.count())
	}
}

func TestSession_NoRestartAfterStopDeadline(t *testing.T) {
	e := newEnv(t)
	cur := time.Date(2026, 10, 15, 13, 40, 0, 0, testNow().Location())
	e.deps.Now = func() time.Time { return cur }
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		cur = time.Date(2026, 10, 15, 13, 49, 0, 0, cur.Location()) // past 13:45 + 3m
		return 1
	}
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d", code)
	}
	if e.child.count() != 1 {
		t.Fatalf("restarted after the stop deadline: %d calls", e.child.count())
	}
	if !e.notif.has(KindRefused) {
		t.Errorf("alerts = %v", e.notif.kinds())
	}
}

func TestSession_KillSwitchFileStopsRunAndAlerts(t *testing.T) {
	e := newEnv(t)
	kill := filepath.Join(e.runs, "KILL")
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		if err := os.WriteFile(kill, []byte("stop"), 0o644); err != nil {
			t.Error(err)
		}
		select {
		case <-ctx.Done(): // the supervisor asked it to stop (SIGINT in production)
		case <-time.After(5 * time.Second):
			t.Error("kill switch did not stop the child")
		}
		return 0
	}
	if code := e.run(); code != ExitProblem {
		t.Fatalf("exit %d, want 1\n%s", code, e.out)
	}
	if !e.notif.has(KindKillSwitch) {
		t.Fatalf("alerts = %v", e.notif.kinds())
	}
	if e.child.count() != 1 {
		t.Fatalf("restarted after a kill switch: %d", e.child.count())
	}
}

func TestSession_InProcessKillSwitchRejectAlerts(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA, orders: 1, rejects: []string{"risk: kill switch is tripped"}})
		return 0
	}
	if code := e.run(); code != ExitProblem {
		t.Fatalf("exit %d, want 1", code)
	}
	if !e.notif.has(KindKillSwitch) || e.notif.has(KindReject) {
		t.Errorf("alerts = %v", e.notif.kinds())
	}
}

func TestSession_RejectAlertsAndExits1(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA, orders: 1, rejects: []string{"carina: insufficient buying power"}})
		return 0
	}
	if code := e.run(); code != ExitProblem {
		t.Fatalf("exit %d, want 1", code)
	}
	a, ok := e.notif.find(KindReject)
	if !ok || !strings.Contains(a.Message, "insufficient buying power") {
		t.Fatalf("reject alert = %+v", a)
	}
}

func TestSession_PostReconcileDivergenceAlertsAndExits1(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA, orders: 1, fills: 1, buyLots: 1})
		e.broker.set("GOTO", 200) // broker holds more than the store says
		return 0
	}
	if code := e.run(); code != ExitProblem {
		t.Fatalf("exit %d, want 1\n%s", code, e.out)
	}
	a, ok := e.notif.find(KindDivergence)
	if !ok || !strings.Contains(a.Message, "GOTO") {
		t.Fatalf("alert = %+v", a)
	}
	if !strings.Contains(e.out.String(), "DIVERGENT") {
		t.Errorf("summary: %s", e.out)
	}
}

func TestSession_PostReconcileBrokerUnreadableIsDivergence(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		e.broker.mu.Lock()
		e.broker.err = context.DeadlineExceeded
		e.broker.mu.Unlock()
		return 0
	}
	if code := e.run(); code != ExitProblem {
		t.Fatalf("exit %d, want 1", code)
	}
	if !e.notif.has(KindDivergence) {
		t.Fatalf("alerts = %v", e.notif.kinds())
	}
}

func TestSession_BinaryHashMismatchFails(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: "deadbeefdeadbeefdeadbeef"})
		return 0
	}
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d, want 2", code)
	}
	if !e.notif.has(KindBinaryHash) {
		t.Fatalf("alerts = %v", e.notif.kinds())
	}
}

func TestSession_CleanExitWithoutRunRowFails(t *testing.T) {
	e := newEnv(t)
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{noRun: true})
		return 0
	}
	if code := e.run(); code != ExitRefused {
		t.Fatalf("exit %d, want 2", code)
	}
}

func TestSession_ZeroOrdersWarnsButStaysClean(t *testing.T) {
	e := newEnv(t)
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d", code)
	}
	if !e.notif.has(KindZeroRecord) {
		t.Fatalf("alerts = %v", e.notif.kinds())
	}
}

func TestSession_StopDeadlineCancelsChild(t *testing.T) {
	e := newEnv(t)
	e.deps.StopContext = func(p context.Context, d time.Duration) (context.Context, context.CancelFunc) {
		if d != 3*time.Hour+48*time.Minute { // 13:45 + 3m stop-after, from 10:00
			t.Errorf("stop delay = %v, want rebalance-at + stop-after - now", d)
		}
		return context.WithTimeout(p, 10*time.Millisecond)
	}
	e.child.behave = func(n int, args []string, ctx context.Context) int {
		writeStore(t, argValue(args, "--store"), storeSpec{hash: e.binSHA})
		select {
		case <-ctx.Done():
		case <-time.After(5 * time.Second):
			t.Error("stop deadline never fired")
		}
		return 0
	}
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d\n%s", code, e.out)
	}
}

// Rehearsal: paper sink, replay source, no token, no arming, no broker.
func TestSession_PaperRehearsalNeedsNoTokenOrArming(t *testing.T) {
	e := newEnv(t)
	e.cfg.Sink, e.cfg.Source = "paper", "replay"
	e.cfg.Account, e.cfg.EquityIDR, e.cfg.Symbols, e.cfg.Segments = "", 0, "", "segments/*.binpb.zst"
	e.cfg.RebalanceAt = "13:45"
	e.deps.TradingToken, e.deps.Armed, e.deps.Broker = "", false, nil
	e.deps.Now = func() time.Time { return time.Date(2026, 10, 17, 11, 0, 0, 0, testNow().Location()) } // a Saturday: no clock rules for replay
	if code := e.run(); code != ExitClean {
		t.Fatalf("exit %d\n%s", code, e.out)
	}
	args := e.child.calls[0]
	if argValue(args, "--sink") != "paper" || argValue(args, "--source") != "replay" || hasArg(args, "--account") {
		t.Errorf("args = %v", args)
	}
	if !strings.Contains(e.out.String(), "n/a (paper sink") {
		t.Errorf("summary: %s", e.out)
	}
}

func TestSession_StoresAreNeverReused(t *testing.T) {
	e := newEnv(t)
	for i := 1; i <= 3; i++ {
		if code := e.run(); code != ExitClean {
			t.Fatalf("run %d exit %d\n%s", i, code, e.out)
		}
	}
	seen := map[string]bool{}
	for _, c := range e.child.calls {
		s := argValue(c, "--store")
		if seen[s] {
			t.Fatalf("store %s reused", s)
		}
		seen[s] = true
	}
}

func TestSession_AlertMessagesCarryNoAccountOrToken(t *testing.T) {
	e := newEnv(t)
	e.broker.set("GOTO", 100)
	e.run()
	for _, a := range e.notif.alerts {
		if strings.Contains(a.Message, testAcc) || strings.Contains(a.Message, tokenFor(testAcc)) {
			t.Fatalf("alert leaks account/token: %s", a.Message)
		}
	}
}
