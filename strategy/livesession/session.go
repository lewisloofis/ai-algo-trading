package livesession

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"strategy/recon"
)

// Exit codes.
const (
	ExitClean   = 0 // ran to a clean end; recon matched; no rejects
	ExitProblem = 1 // divergence, reject or kill-switch trip
	ExitRefused = 2 // refused to start, or crashed past the restart budget, or integrity failure
)

// Deps are the session's collaborators; all are injectable for tests.
type Deps struct {
	Now          func() time.Time
	Cal          Calendar
	Broker       recon.PortfolioSource // nil for a paper sink
	TradingToken string                // never logged
	Armed        bool
	Child        ChildRunner
	Notifier     Notifier
	Out          io.Writer
	Sleep        func(time.Duration)
	// StopContext derives the child's context with the stop deadline. The
	// default is context.WithTimeout.
	StopContext func(parent context.Context, d time.Duration) (context.Context, context.CancelFunc)
}

// Session runs one supervised session.
type Session struct {
	cfg Config
	d   Deps

	strategyID string
	storeKey   string // store-name / prior-stores key: id, or id-paper for a paper sink
}

// New builds a Session, defaulting any unset dependency.
func New(cfg Config, d Deps) *Session {
	if d.Now == nil {
		d.Now = time.Now
	}
	if d.Out == nil {
		d.Out = io.Discard
	}
	if d.Notifier == nil {
		d.Notifier = LogNotifier{W: d.Out}
	}
	if d.Sleep == nil {
		d.Sleep = time.Sleep
	}
	if d.StopContext == nil {
		d.StopContext = context.WithTimeout
	}
	return &Session{cfg: cfg.withDefaults(), d: d, strategyID: cfg.Strategy}
}

func (s *Session) logf(format string, a ...any) {
	fmt.Fprintf(s.d.Out, "live-session: "+format+"\n", a...)
}

func (s *Session) alert(kind Kind, sev Severity, store string, attempt int, format string, a ...any) {
	al := Alert{
		Kind: kind, Severity: sev, Strategy: s.strategyID, Attempt: attempt,
		Message: Redact(fmt.Sprintf(format, a...), s.cfg.Account),
		At:      s.d.Now(),
	}
	if store != "" {
		al.Store = filepath.Base(store)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := s.d.Notifier.Notify(ctx, al); err != nil {
		s.logf("alert delivery failed (%s): %v", kind, err)
	}
}

func (s *Session) refuse(attempt int, what string, errs ...error) {
	parts := make([]string, 0, len(errs))
	for _, e := range errs {
		parts = append(parts, e.Error())
		s.logf("REFUSED: %v", e)
	}
	s.alert(KindRefused, SeverityCritical, "", attempt, "%s: %s", what, strings.Join(parts, "; "))
}

type childResult struct {
	exit     int
	err      error
	killed   bool // kill-switch file appeared during the attempt
	stopping bool // the stop deadline passed or the operator interrupted
}

func (r childResult) clean() bool { return r.exit == 0 && r.err == nil }

// Run executes the session and returns the process exit code.
func (s *Session) Run(ctx context.Context) int {
	cfg := s.cfg
	maxAttempts := 1 + cfg.MaxRestarts
	var (
		stores    []string
		binarySHA string
		killed    bool
		finalOK   bool
		lastExit  int
	)

	for n := 1; n <= maxAttempts; n++ {
		now := s.d.Now()
		pf, errs := Preflight(PreflightInput{Cfg: cfg, Now: now, Cal: s.d.Cal, TradingToken: s.d.TradingToken, Armed: s.d.Armed, Restart: n > 1})
		if len(errs) > 0 {
			s.refuse(n, "preflight failed", errs...)
			break
		}
		s.strategyID, binarySHA = pf.StrategyID, pf.BinarySHA
		s.storeKey = StoreKey(cfg.Sink, pf.StrategyID)
		store, err := NextStorePath(cfg.RunsDir, s.storeKey, now)
		if err != nil {
			s.refuse(n, "store naming failed", err)
			break
		}
		globs := PriorStoreGlobs(cfg, s.storeKey)

		if cfg.Sink == "live" {
			if err := s.preSnapshot(ctx, globs); err != nil {
				s.refuse(n, "pre-snapshot failed", err)
				break
			}
		} else {
			s.preRunPaper(globs)
		}

		s.logf("attempt %d/%d: store %s, strategy-run sha256 %s", n, maxAttempts, store, shortHash(pf.BinarySHA))
		res := s.runChild(ctx, pf, store, s.childArgs(store, globs, pf), n)
		stores = append(stores, store)
		killed = killed || res.killed
		lastExit = res.exit
		if res.clean() {
			finalOK = true
			break
		}
		why := fmt.Sprintf("exit %d", res.exit)
		if res.err != nil {
			why = res.err.Error()
		}
		switch {
		case res.killed:
			s.alert(KindCrash, SeverityCritical, store, n, "attempt %d ended (%s) after the kill switch tripped; not restarting", n, why)
		case res.stopping:
			s.alert(KindCrash, SeverityCritical, store, n, "attempt %d failed during shutdown (%s); not restarting", n, why)
		case n == maxAttempts:
			s.alert(KindRepeatedCrash, SeverityCritical, store, n, "strategy-run failed %d time(s) (last: %s); restart budget spent. No automatic resubmission. %s", n, why, s.retryAdvice())
		default:
			s.alert(KindCrash, SeverityWarn, store, n, "attempt %d failed (%s); restarting with a fresh store (never with --retry)", n, why)
			continue
		}
		break
	}
	if len(stores) == 0 {
		return ExitRefused
	}
	return s.finalize(ctx, stores, binarySHA, finalOK, killed, lastExit)
}

func (s *Session) retryAdvice() string {
	if s.cfg.Strategy != "rebalance" {
		return ""
	}
	level := s.cfg.Retry + 1
	if level < 2 {
		level = 2
	}
	return "To resubmit deliberately after checking the broker and the stores, run: " + RetryCommand(s.cfg.Invocation, level)
}

func (s *Session) preSnapshot(ctx context.Context, globs []string) error {
	if s.d.Broker == nil {
		return fmt.Errorf("no broker client for a live sink")
	}
	cctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	pc, err := CheckPosition(cctx, s.d.Broker, s.cfg, globs, nil)
	if err != nil {
		return err
	}
	if !ExpectedPosition(pc) {
		return fmt.Errorf("%s", pc.Result.Message("account-recon --stores 'runs/*.db' --acc-no <account> --sink-kind live"))
	}
	s.logf("pre-snapshot: broker holdings match the position derived from %d prior store(s) + manual/imported trades", len(pc.Stores))
	return nil
}

// preRunPaper is the paper sink's pre-snapshot: there is no broker, so the
// position the run starts from is the one the paper stores imply, netted across
// every day. Informational: an unreadable store is logged, strategy-run's own
// prior-state load is what refuses a bad one.
func (s *Session) preRunPaper(globs []string) {
	pos, names, err := PaperPositions(globs)
	if err != nil {
		s.logf("pre-run (paper): cannot read prior paper stores: %v", err)
		return
	}
	s.logf("pre-run (paper): position from %d prior paper store(s): %s", len(names), FormatLots(pos))
}

func (s *Session) childArgs(store string, globs []string, pf PreflightResult) []string {
	cfg := s.cfg
	args := []string{
		"--source", cfg.Source, "--strategy", cfg.Strategy, "--sink", cfg.Sink,
		"--store", store,
		"--prior-stores", strings.Join(globs, ","),
		"--kill-switch-file", cfg.KillSwitchFile,
		"--manual-trades", cfg.ManualTrades, "--broker-ledger", cfg.BrokerLedger,
		"--status", strings.TrimSuffix(store, ".db") + ".status.json",
	}
	add := func(flag, val string) {
		if strings.TrimSpace(val) != "" {
			args = append(args, flag, val)
		}
	}
	add("--target-portfolio", cfg.TargetPortfolio)
	add("--symbols", cfg.Symbols)
	add("--segments", cfg.Segments)
	add("--account", strings.TrimSpace(cfg.Account))
	add("--rebalance-at", strings.TrimSpace(cfg.RebalanceAt))
	if cfg.EquityIDR > 0 {
		args = append(args, "--equity-idr", strconv.FormatInt(cfg.EquityIDR, 10))
	}
	if cfg.Retry != 0 {
		args = append(args, "--retry", strconv.Itoa(cfg.Retry)) // operator-supplied only
	}
	if cfg.MaxBuyNotionalIDR > 0 {
		args = append(args, "--max-buy-notional-idr", strconv.FormatInt(cfg.MaxBuyNotionalIDR, 10))
	}
	return append(args, cfg.ExtraArgs...)
}

func (s *Session) runChild(ctx context.Context, pf PreflightResult, store string, args []string, attempt int) childResult {
	var cctx context.Context
	var cancel context.CancelFunc
	if s.cfg.Source == "live" && !pf.At.IsZero() {
		cctx, cancel = s.d.StopContext(ctx, pf.At.Add(s.cfg.StopAfter).Sub(s.d.Now()))
	} else {
		cctx, cancel = context.WithCancel(ctx)
	}
	defer cancel()

	var tripped atomic.Bool
	stopWatch, watchDone := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(watchDone)
		t := time.NewTicker(s.cfg.KillPoll)
		defer t.Stop()
		for {
			select {
			case <-stopWatch:
				return
			case <-t.C:
				if _, err := os.Stat(s.cfg.KillSwitchFile); err == nil || !os.IsNotExist(err) {
					if tripped.CompareAndSwap(false, true) {
						s.alert(KindKillSwitch, SeverityCritical, store, attempt, "kill-switch file appeared: stopping the run (open orders are cancelled by the drain); no restart")
						cancel()
					}
					return
				}
			}
		}
	}()

	code, err := s.d.Child.Run(cctx, args)
	close(stopWatch)
	<-watchDone
	return childResult{exit: code, err: err, killed: tripped.Load(), stopping: cctx.Err() != nil}
}

func shortHash(h string) string {
	if len(h) > 12 {
		return h[:12]
	}
	return h
}
