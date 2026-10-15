package livesession

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"collector/calendar"
)

func (e *env) preflight() (PreflightResult, []error) {
	return Preflight(PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: true}, TradingToken: tokenFor(testAcc), Armed: true})
}

func TestPreflight_Passes(t *testing.T) {
	e := newEnv(t)
	res, errs := e.preflight()
	if len(errs) != 0 {
		t.Fatalf("errs = %v", errs)
	}
	if res.StrategyID != "calibration" || res.BinarySHA != e.binSHA || res.At.Hour() != 13 || res.At.Minute() != 45 {
		t.Errorf("res = %+v", res)
	}
}

func TestPreflight_Refusals(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(e *env, in *PreflightInput)
		want   string
	}{
		{"token for another account", func(e *env, in *PreflightInput) { in.TradingToken = tokenFor("OTHER") }, "route by the token's account"},
		{"no token", func(e *env, in *PreflightInput) { in.TradingToken = "" }, "SB_TRADING_ACCESS_TOKEN"},
		{"garbage token", func(e *env, in *PreflightInput) { in.TradingToken = "nope" }, "cannot determine"},
		{"not armed", func(e *env, in *PreflightInput) { in.Armed = false }, "not armed"},
		{"no account", func(e *env, in *PreflightInput) { in.Cfg.Account = "" }, "--account is required"},
		{"rebalance-at missing", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "" }, "--rebalance-at is required"},
		{"rebalance-at malformed", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "1345" }, "HH:MM"},
		{"rebalance-at past", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "09:30" }, "in the future"},
		{"rebalance-at too soon", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "10:00" }, "in the future"},
		{"rebalance-at in the lunch break", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "12:30" }, "not in a continuous-trading session"},
		{"rebalance-at after close", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "15:55" }, "not in a continuous-trading session"},
		{"rebalance-at past complete_by", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "15:30" }, "outside the target's window"},
		{"rebalance-at before as_of", func(e *env, in *PreflightInput) { in.Cfg.RebalanceAt = "07:30"; in.Now = in.Now.Add(-4 * time.Hour) }, "outside the target's window"},
		{"target missing", func(e *env, in *PreflightInput) { in.Cfg.TargetPortfolio = filepath.Join(e.runs, "nope.json") }, "target portfolio"},
		{"target not given", func(e *env, in *PreflightInput) { in.Cfg.TargetPortfolio = "" }, "--target-portfolio is required"},
		{"market closed", func(e *env, in *PreflightInput) { in.Cal = fakeCal{open: false} }, "market is closed"},
		{"binary missing", func(e *env, in *PreflightInput) { in.Cfg.StrategyRunBin = filepath.Join(e.runs, "nobin") }, "unreadable"},
		{"binary pin mismatch", func(e *env, in *PreflightInput) { in.Cfg.ExpectBinarySHA = "00" }, "does not match"},
		{"equity missing", func(e *env, in *PreflightInput) { in.Cfg.EquityIDR = 0 }, "--equity-idr"},
		{"retry 1", func(e *env, in *PreflightInput) { in.Cfg.Retry = 1 }, "--retry"},
		{"live sink on replay", func(e *env, in *PreflightInput) { in.Cfg.Source = "replay" }, "requires --source live"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t)
			in := PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: true}, TradingToken: tokenFor(testAcc), Armed: true}
			tc.mutate(e, &in)
			_, errs := Preflight(in)
			if len(errs) == 0 {
				t.Fatal("preflight passed, want refusal")
			}
			var all []string
			for _, err := range errs {
				all = append(all, err.Error())
			}
			if joined := strings.Join(all, "\n"); !strings.Contains(joined, tc.want) {
				t.Fatalf("errors %q do not mention %q", joined, tc.want)
			}
			for _, a := range all {
				if strings.Contains(a, tokenFor(testAcc)) {
					t.Fatal("error leaks the token")
				}
			}
		})
	}
}

func TestPreflight_KillSwitchFilePresentRefuses(t *testing.T) {
	e := newEnv(t)
	if err := os.WriteFile(filepath.Join(e.runs, "KILL"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, errs := e.preflight(); len(errs) == 0 || !strings.Contains(errs[0].Error(), "kill-switch") {
		t.Fatalf("errs = %v", errs)
	}
}

func TestPreflight_KillSwitchUnreadableFailsClosed(t *testing.T) {
	e := newEnv(t)
	// A path component that is a file makes Stat fail with ENOTDIR, not ENOENT.
	e.cfg.KillSwitchFile = filepath.Join(e.cfg.StrategyRunBin, "KILL")
	if _, errs := e.preflight(); len(errs) == 0 || !strings.Contains(errs[0].Error(), "failing closed") {
		t.Fatalf("errs = %v", errs)
	}
}

func TestPreflight_ReportsAllFailuresAtOnce(t *testing.T) {
	e := newEnv(t)
	e.cfg.RebalanceAt = ""
	e.cfg.EquityIDR = 0
	_, errs := Preflight(PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: false}, TradingToken: tokenFor("OTHER"), Armed: false})
	if len(errs) < 4 {
		t.Fatalf("want several errors at once, got %v", errs)
	}
}

func TestPreflight_PaperNeedsNoTokenAccountOrArming(t *testing.T) {
	e := newEnv(t)
	e.cfg.Sink, e.cfg.Account = "paper", ""
	_, errs := Preflight(PreflightInput{Cfg: e.cfg, Now: testNow(), Cal: fakeCal{open: true}})
	if len(errs) != 0 {
		t.Fatalf("errs = %v", errs)
	}
}

func TestPreflight_RestartMayBePastRebalanceAtButNotPastStopDeadline(t *testing.T) {
	e := newEnv(t)
	at := func(h, m int) time.Time { return time.Date(2026, 10, 15, h, m, 0, 0, calendar.Jakarta) }
	in := PreflightInput{Cfg: e.cfg, Now: at(13, 46), Cal: fakeCal{open: true}, TradingToken: tokenFor(testAcc), Armed: true}
	if _, errs := Preflight(in); len(errs) == 0 {
		t.Fatal("a fresh start after rebalance-at must be refused")
	}
	in.Restart = true
	if _, errs := Preflight(in); len(errs) != 0 {
		t.Fatalf("restart inside the window refused: %v", errs)
	}
	in.Now = at(13, 49)
	if _, errs := Preflight(in); len(errs) == 0 {
		t.Fatal("restart past the stop deadline must be refused")
	}
}

func TestInSession(t *testing.T) {
	jk := calendar.Jakarta
	cases := []struct {
		t    time.Time
		want bool
	}{
		{time.Date(2026, 10, 15, 9, 0, 0, 0, jk), true},
		{time.Date(2026, 10, 15, 8, 59, 0, 0, jk), false},
		{time.Date(2026, 10, 15, 12, 0, 0, 0, jk), false}, // Thu break
		{time.Date(2026, 10, 15, 13, 30, 0, 0, jk), true},
		{time.Date(2026, 10, 15, 15, 49, 0, 0, jk), true},
		{time.Date(2026, 10, 15, 15, 50, 0, 0, jk), false},
		{time.Date(2026, 10, 16, 11, 30, 0, 0, jk), false}, // Fri break
		{time.Date(2026, 10, 16, 13, 45, 0, 0, jk), false},
		{time.Date(2026, 10, 16, 14, 0, 0, 0, jk), true},
		{time.Date(2026, 10, 17, 10, 0, 0, 0, jk), false},     // Sat
		{time.Date(2026, 10, 15, 3, 0, 0, 0, time.UTC), true}, // 10:00 WIB
	}
	for _, c := range cases {
		if got := InSession(c.t); got != c.want {
			t.Errorf("InSession(%v) = %v, want %v", c.t, got, c.want)
		}
	}
}
