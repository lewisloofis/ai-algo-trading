// Command strategy-run replays market-data segments (or, with --source live,
// streams a live feed) through a strategy and an order sink, recording the
// run into an append-only SQLite post-trade store.
//
// Two sinks exist: --sink paper (engine.PaperSink, simulated fills — the
// default) and --sink live (strategy/live.Sink, real Carina order routing).
// --sink live requires --source live (never place real orders against
// replayed data), an explicit --account (the sleeve sub-account, e.g.
// 008488L — no default, see docs/plans/18-alpha-and-infrastructure-split.md
// §3.4), and the ALGO_LIVE/ALGO_CONFIRM double-arm (strategy/live/armed.go).
//
// Whenever --source live, whichever sink is selected — paper or live — is
// wrapped in strategy/risk.RiskSink (limits, kill switch, the real
// endpoint-backed market gate) before it ever reaches engine.Run. A
// --source replay backtest is never risk-wrapped: there is no live market
// to gate against, and this binary must never make a live network call
// while replaying captured data. A live run always is risk-wrapped,
// regardless of which sink sits underneath it — see newInnerSink and
// wrapWithRisk.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"

	"collector/calendar"
	"collector/feed"
	"strategy/engine"
	"strategy/live"
	"strategy/market"
	"strategy/portfolio"
	"strategy/risk"
	"strategy/strategies/orb"
	"strategy/strategies/rebalance"
)

// sharesPerLot mirrors strategy/engine's, strategy/risk's, strategy/live's
// and strategy/strategies/rebalance's own constant: 1 lot = 100 shares on
// the IDX (docs/reference/06-pre-trade-rules.md §6). This package cannot
// import any of their unexported copies, so this is a fifth independent
// copy kept in sync by hand — see rebalance.go's snapTickIDR doc comment
// for the precedent.
const sharesPerLot = 100

// defaultRebalanceAtFlag is --rebalance-at's default: the rebalance window's
// opening time (WIB), matching portfolio.NullOptions' own CompleteBy default
// (15:00 WIB — see null.go's defaultCompleteByHourWIB). Continuous trading
// ends before 15:00 on every weekday, so with this default the market gate
// denies every order a live session would place; pass an in-session time
// (e.g. --rebalance-at 13:45) for a run that is meant to trade.
const defaultRebalanceAtFlag = "15:00"

// rebalanceAt is a parsed --rebalance-at value.
type rebalanceAt struct{ hour, min int }

// parseRebalanceAt parses a strict "HH:MM" (24h, WIB) --rebalance-at value.
func parseRebalanceAt(s string) (rebalanceAt, error) {
	if len(s) != 5 || s[2] != ':' {
		return rebalanceAt{}, fmt.Errorf("--rebalance-at %q must be HH:MM (24h, WIB)", s)
	}
	for _, i := range []int{0, 1, 3, 4} {
		if s[i] < '0' || s[i] > '9' {
			return rebalanceAt{}, fmt.Errorf("--rebalance-at %q must be HH:MM (24h, WIB)", s)
		}
	}
	h := int(s[0]-'0')*10 + int(s[1]-'0')
	m := int(s[3]-'0')*10 + int(s[4]-'0')
	if h > 23 || m > 59 {
		return rebalanceAt{}, fmt.Errorf("--rebalance-at %q is not a valid time of day", s)
	}
	return rebalanceAt{hour: h, min: m}, nil
}

// shutdownTimeout bounds the end-of-run work (live reconcile + close) that
// runs after the run context is gone: long enough for ReconcileTerminal's
// single List call, short enough that a hung broker cannot block exit.
const shutdownTimeout = 30 * time.Second

// newShutdownContext returns the context for end-of-run work. It is rooted in
// context.Background, not the run context: after Ctrl-C the run context is
// already cancelled, and reusing it would fail every reconcile HTTP call.
func newShutdownContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), shutdownTimeout)
}

// runErrAfterShutdown maps the cancellation a SIGINT/SIGTERM causes to a
// clean stop: an operator-requested stop is how a live run normally ends, not
// a failure. Any other error — or a cancel with no signal behind it — is kept.
func runErrAfterShutdown(runErr error, interrupted bool) error {
	if interrupted && errors.Is(runErr, context.Canceled) {
		return nil
	}
	return runErr
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

// run is main's body, extracted so that every exit path runs the deferred
// store close and the run-header finalisation. With log.Fatal inline, an error
// exit skipped both, leaving the run row at "running" for a run that did in
// fact report a reason for stopping.
func run() error {
	source := flag.String("source", "replay", "event source: replay|live")
	segmentsGlob := flag.String("segments", "", "glob of .binpb.zst segment files (required for --source replay)")
	symbolsFlag := flag.String("symbols", "", "comma-separated symbol watchlist (required for --source live)")
	strategyName := flag.String("strategy", "orb", "strategy id (orb|rebalance)")
	targetPortfolioPath := flag.String("target-portfolio", "", "path to target_portfolio.json (required for --strategy rebalance)")
	sinkName := flag.String("sink", "paper", "order sink (paper|live)")
	fillMode := flag.String("fill-mode", "taker", "paper fill mode: taker|mid")
	storePath := flag.String("store", "", "path to the SQLite post-trade store (required; one file per run)")
	priorStores := flag.String("prior-stores", "", "glob of prior same-day store file(s), read once at startup to recover restart-durable state for --strategy rebalance (optional — see docs/plans/18-alpha-and-infrastructure-split.md §5 B4)")
	pacing := flag.String("pacing", "fast", "pacing mode: fast|paced (--source replay only)")
	statusPath := flag.String("status", "", "path to write the status.json heartbeat (optional)")
	statusInterval := flag.Duration("status-interval", engine.DefaultStatusInterval, "status.json rewrite period")

	account := flag.String("account", "", "brokerage sub-account number, e.g. 008488L (required for --sink live; no default)")
	equityIDR := flag.Int64("equity-idr", 0, "sleeve equity in IDR (required whenever --source live; strategy/risk caps are always denominated in it, and this binary never fetches it automatically — see risk.SleeveEquityIDR's doc comment)")
	killSwitchFile := flag.String("kill-switch-file", "", "path to an external kill-switch file (optional; see strategy/risk/killswitch.go)")
	riskPerOrderFrac := flag.Float64("risk-per-order-fraction", 0.25, "per-order notional cap, as a fraction of --equity-idr")
	riskPerDayFrac := flag.Float64("risk-per-day-fraction", 1.0, "per-day cumulative notional cap, as a fraction of --equity-idr")
	riskMaxGrossFrac := flag.Float64("risk-max-gross-fraction", 1.0, "max gross exposure cap, as a fraction of --equity-idr")
	riskMaxPositionFrac := flag.Float64("risk-max-position-fraction", 0.25, "max position-per-name cap, as a fraction of --equity-idr")
	riskDailyLossFrac := flag.Float64("risk-daily-loss-fraction", 0.05, "daily loss limit, as a fraction of --equity-idr")
	limitPriceIDR := flag.Int64("limit-price-idr", 0, "DIAGNOSTIC: submit the single rebalance order at exactly this tick-valid IDR price instead of the last price (0 = off; only with --strategy rebalance and a one-target portfolio; used for the first-live-order unfilled step, docs/runbooks/first-live-order.md §5)")
	rebalanceAtFlag := flag.String("rebalance-at", defaultRebalanceAtFlag, "rebalance window opening time, HH:MM WIB (--strategy rebalance only); the default 15:00 is after continuous trading ends, so pass an in-session time for a run meant to trade")
	flag.Parse()

	if *storePath == "" {
		flag.Usage()
		os.Exit(2)
	}
	if err := validateSinkSource(*sinkName, *source); err != nil {
		return err
	}
	if *sinkName == "live" && strings.TrimSpace(*account) == "" {
		return fmt.Errorf("--sink live requires --account (the sleeve sub-account number, e.g. 008488L; there is no default — falling back to any default would silently route real orders to the wrong account, docs/plans/18-alpha-and-infrastructure-split.md §3.4)")
	}
	if err := validatePriorStores(*sinkName, *priorStores); err != nil {
		return err
	}
	// Risk-gating tracks --source, not --sink: a live run's PAPER sink is
	// wrapped too (see package doc comment) so a shadow/dry-run session
	// against the live market still exercises every gate a real order
	// would face.
	riskGated := *source == "live"
	if riskGated && *equityIDR <= 0 {
		return fmt.Errorf("--source live requires --equity-idr > 0 (sleeve equity; strategy/risk caps are always denominated in it, and this binary never fetches it automatically)")
	}
	tradingToken := os.Getenv("SB_TRADING_ACCESS_TOKEN")
	if riskGated && tradingToken == "" {
		return fmt.Errorf("--source live requires SB_TRADING_ACCESS_TOKEN in the environment (used by the market gate, and by order routing itself when --sink live)")
	}

	var priorState *priorStateConfig
	if *priorStores != "" {
		priorState = &priorStateConfig{glob: *priorStores, sinkKind: *sinkName, account: strings.TrimSpace(*account)}
	}
	at, err := parseRebalanceAt(*rebalanceAtFlag)
	if err != nil {
		return err
	}
	strat, stratID, err := newStrategy(*strategyName, *targetPortfolioPath, priorState, *limitPriceIDR, at)
	if err != nil {
		return err
	}
	if *limitPriceIDR > 0 {
		log.Printf("WARNING: --limit-price-idr=%d is in force: every rebalance order is submitted at this FIXED diagnostic price, not the market price (first-live-order diagnostic, not a pricing policy)", *limitPriceIDR)
	}

	store, err := engine.OpenStore(*storePath)
	if err != nil {
		return fmt.Errorf("open store: %w", err)
	}
	defer store.Close()

	tokens := envTokens{access: os.Getenv("SB_ACCESS_TOKEN"), trading: tradingToken}
	hc := &http.Client{Timeout: 20 * time.Second}
	inner, liveSink, err := newInnerSink(*sinkName, *fillMode, *account, tokens, envDefault("SB_CARINA_HOST", live.DefaultHost), hc)
	if err != nil {
		return err
	}

	sink := engine.OrderSink(inner)
	if riskGated {
		marketGate, err := newMarketGate(tokens, hc)
		if err != nil {
			return fmt.Errorf("market gate: %w", err)
		}
		limits := riskLimits(*riskPerOrderFrac, *riskPerDayFrac, *riskMaxGrossFrac, *riskMaxPositionFrac, *riskDailyLossFrac)
		pnl := storePnL{store: store, sinkKind: *sinkName}
		sink, err = wrapWithRisk(inner, *equityIDR, limits, marketGate, pnl, *killSwitchFile, nil)
		if err != nil {
			return err
		}
	}
	// SIGINT/SIGTERM cancels ctx so engine.Run returns and the shutdown below
	// (live reconcile + close, final status, FinishRun) still runs. Before
	// this, Ctrl-C killed the process outright and left the run row at
	// "running". stopSignals is called once engine.Run returns, so a second
	// Ctrl-C during shutdown falls back to the default and kills immediately.
	ctx, stopSignals := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stopSignals()

	src, err := newSource(ctx, *source, *segmentsGlob, *symbolsFlag, *pacing)
	if err != nil {
		if errors.Is(err, engine.ErrMarketClosed) {
			// Not a failure: the market is simply not open today. Exit 0 so
			// a scheduler does not mistake "closed today" for a crash (see
			// engine.ErrMarketClosed's doc — this is the same distinction
			// plan 14 §1 requires of the collector). No run row is written:
			// no run started.
			fmt.Println("strategy-run: market is closed today — not starting a live run")
			return nil
		}
		return err
	}

	runID, err := engine.NewRunID(time.Now())
	if err != nil {
		return err
	}
	inputSpec := *segmentsGlob
	if *source == "live" {
		inputSpec = *symbolsFlag
	}
	if err := store.BeginRun(engine.RunRow{
		RunID:      runID,
		StartedNs:  time.Now().UTC().UnixNano(),
		Source:     *source,
		Pacing:     replayOnlyPacing(*source, *pacing),
		FillMode:   *fillMode,
		SinkKind:   *sinkName,
		StrategyID: stratID,
		InputSpec:  inputSpec,
		BinaryHash: binaryHash(),
		Account:    strings.TrimSpace(*account),
	}); err != nil {
		return err
	}

	progress := &engine.RunProgress{}
	status := &engine.StatusWriter{
		Path:       *statusPath,
		Interval:   *statusInterval,
		Progress:   progress,
		RunID:      runID,
		Source:     *source,
		SinkKind:   *sinkName,
		StrategyID: stratID,
		InputSpec:  inputSpec,
	}
	// A status-file write failure is logged, never fatal: losing the ability
	// to observe a run is not a reason to stop it.
	stopStatus := status.Start(ctx, func(err error) { log.Printf("status: %v", err) })

	counters, runErr := baselineThenRun(ctx, liveSink, func() (engine.Counters, error) {
		return engine.Run(ctx, src, strat, sink, store, stratID, engine.WithProgress(progress))
	})
	interrupted := ctx.Err() != nil
	stopSignals()
	if interrupted {
		log.Printf("strategy-run: signal received — shutting down (Ctrl-C again to force exit)")
	}
	runErr = runErrAfterShutdown(runErr, interrupted)

	if liveSink != nil {
		// Unconditional, whatever runErr says: "how did this run's own
		// orders end up" is worth knowing whether the run ended cleanly or
		// not — see reconcileAndClose's doc comment.
		shutdownCtx, cancelShutdown := newShutdownContext()
		reconcileAndClose(shutdownCtx, liveSink, runID)
		cancelShutdown()
	}

	// Stop the ticker before the terminal write, or it can overwrite the final
	// outcome with another "running".
	stopStatus()
	outcome := engine.RunOutcomeComplete
	if runErr != nil {
		outcome = engine.RunOutcomeError
	}
	if err := status.WriteOnce(outcome, runErr); err != nil {
		log.Printf("status: %v", err)
	}
	if err := store.FinishRun(time.Now().UTC().UnixNano(), counters, runErr); err != nil {
		log.Printf("finish run: %v", err)
	}
	if runErr != nil {
		return fmt.Errorf("run: %w", runErr)
	}

	fmt.Printf("Run: %s | store: %s\n", runID, *storePath)
	fmt.Printf("Trades attempted: %d | Fills: %d | Net PnL bps: %.2f\n",
		counters.OrdersAttempted, counters.Fills, counters.NetPnlBps)
	fmt.Printf("Round-trips: %d | Total fees: %d IDR\n", counters.RoundTrips, counters.TotalFeesIDR)
	return nil
}

// baselineThenRun starts liveSink's poller and takes its pre-trade baseline,
// then runs the engine. A baseline failure skips the engine and is returned
// as the run error rather than as an early return from run: the caller's
// shutdown path then still closes the sink and records the run as "error"
// (an early return left the run row at "running"). liveSink may be nil (a
// paper run), in which case this is just runEngine().
func baselineThenRun(ctx context.Context, liveSink *live.Sink, runEngine func() (engine.Counters, error)) (engine.Counters, error) {
	if liveSink != nil {
		// Start the poller before Baseline: Baseline itself only calls
		// List() once and does not depend on the poller, but starting it
		// first means the very first fill this run produces is already
		// being watched for, rather than racing Start against engine.Run's
		// first Submit.
		liveSink.Start(ctx)
		if err := liveSink.Baseline(ctx); err != nil {
			return engine.Counters{}, fmt.Errorf("live: baseline: %w", err)
		}
	}
	return runEngine()
}

// reconcileAndClose runs live.Sink.ReconcileTerminal and logs any
// divergence loudly (plan 18 §5 B4: "persisting or logging divergences
// loudly" — logging, not a new store table, since ReconcileTerminal's
// Divergence has no dedicated schema of its own and inventing one is out of
// this task's scope), then closes the sink. Called once, unconditionally,
// right after engine.Run returns.
func reconcileAndClose(ctx context.Context, sink *live.Sink, runID string) {
	div, err := sink.ReconcileTerminal(ctx)
	switch {
	case err != nil:
		log.Printf("live: run %s: ReconcileTerminal failed — divergences could not be checked: %v", runID, err)
	case !div.Empty():
		log.Printf("live: run %s: RECONCILE DIVERGENCE — not_terminal=%v lot_mismatch=%v unbaselined=%v",
			runID, div.NotTerminal, div.LotMismatch, div.Unbaselined)
	default:
		log.Printf("live: run %s: ReconcileTerminal clean — every order this run placed reached a terminal state with lots agreeing with Carina", runID)
	}
	if err := sink.Close(); err != nil {
		log.Printf("live: run %s: close: %v", runID, err)
	}
}

// validatePriorStores refuses --sink live without --prior-stores. Restart
// durability rests on reading the same day's earlier store files; leaving
// the flag optional for live would make "forgot a flag after a crash" a
// double-submission. A glob that matches nothing is fine — that is simply
// the first run of the day.
func validatePriorStores(sinkName, priorStores string) error {
	if sinkName == "live" && strings.TrimSpace(priorStores) == "" {
		return fmt.Errorf("--sink live requires --prior-stores (a glob over today's earlier store files, e.g. 'runs/2026-09-24-*.db'; matching nothing is fine on the first run) — without it a restart after a crash can resubmit orders the crashed run already placed")
	}
	return nil
}

// validateSinkSource refuses --sink live paired with --source replay: this
// binary must never place a real order while replaying captured market
// data. Every other combination is allowed.
func validateSinkSource(sinkName, source string) error {
	if sinkName == "live" && source != "live" {
		return fmt.Errorf("--sink live requires --source live (refuses to place real orders against replayed market data; got --source %q)", source)
	}
	return nil
}

// priorStateConfig names where newStrategy's --strategy rebalance path
// recovers restart-durable state from — see loadPriorState.
type priorStateConfig struct {
	glob     string
	sinkKind string
	account  string // --account; prior positions are only this account's
}

// newStrategy builds the named strategy and returns it together with the
// store's strategy_id partition key for this run. For "orb" that key is
// the literal flag value; for "rebalance" it is the loaded portfolio's OWN
// strategy_id (e.g. "calibration" for the null portfolio, per
// portfolio.CalibrationStrategyID) — the binary's --strategy flag selects
// which CODE runs, but the portfolio decides which of the store's
// strategy_id partitions the resulting rows belong to.
//
// limitPriceIDR is the --limit-price-idr diagnostic (0 = off). It is refused
// for any strategy but rebalance; rebalance.New refuses it for a
// multi-target portfolio or a non-tick-valid price. at is the parsed
// --rebalance-at window opening time (rebalance only).
func newStrategy(name, targetPortfolioPath string, prior *priorStateConfig, limitPriceIDR int64, at rebalanceAt) (engine.Strategy, string, error) {
	if limitPriceIDR < 0 {
		return nil, "", fmt.Errorf("--limit-price-idr must not be negative (got %d)", limitPriceIDR)
	}
	if limitPriceIDR > 0 && name != "rebalance" {
		return nil, "", fmt.Errorf("--limit-price-idr is only valid with --strategy rebalance (got --strategy %q)", name)
	}
	switch name {
	case "orb":
		return orb.New(), "orb", nil

	case "rebalance":
		if strings.TrimSpace(targetPortfolioPath) == "" {
			return nil, "", fmt.Errorf("--strategy rebalance requires --target-portfolio")
		}
		tp, err := portfolio.Load(targetPortfolioPath)
		if err != nil {
			return nil, "", fmt.Errorf("load target portfolio: %w", err)
		}

		opts := rebalance.Options{
			Portfolio: tp,
			Schedule:  rebalance.DailyAt(at.hour, at.min),

			LimitPriceIDR: limitPriceIDR,
		}
		if prior != nil && prior.glob != "" {
			attempted, initial, err := loadPriorState(prior.glob, tp.StrategyID, prior.sinkKind, prior.account)
			if err != nil {
				return nil, "", fmt.Errorf("recover restart state: %w", err)
			}
			opts.Attempted = attempted
			opts.Initial = initial
		}

		strat, err := rebalance.New(opts)
		if err != nil {
			return nil, "", fmt.Errorf("rebalance.New: %w", err)
		}
		return strat, tp.StrategyID, nil

	default:
		return nil, "", fmt.Errorf("unknown strategy %q: want orb|rebalance", name)
	}
}

// loadPriorState reads every store file matching glob and unions their
// AttemptedWindows and DerivedPositions for strategyID/sinkKind — the
// restart-durable recovery step behind rebalance.Options.Attempted/Initial
// (plan 18 §5 B4's idempotency gap).
//
// engine/store.go's own convention is ONE STORE FILE PER RUN: seq is a
// per-file primary key that collides across two runs sharing a file, so a
// same-day restart cannot simply reuse the crashed run's own store file —
// "today's prior attempts" live in the PRIOR run's file(s), which glob must
// name, never in the new run's own --store (which starts empty; see
// engine.PostTradeStore.AttemptedWindows' doc comment for the same point).
//
// Each matched file's DerivedPositions reflects only THAT file's own fills
// — one run's fills never appear in another run's store — so summing
// positions across files is correct, not double-counting.
func loadPriorState(glob, strategyID, sinkKind, account string) (map[string]bool, map[string]int, error) {
	paths, err := globFiles(glob)
	if err != nil {
		return nil, nil, fmt.Errorf("resolve %q: %w", glob, err)
	}

	attempted := make(map[string]bool)
	positions := make(map[string]int)
	for _, path := range paths {
		s, err := engine.OpenStore(path)
		if err != nil {
			return nil, nil, fmt.Errorf("open %s: %w", path, err)
		}

		aw, err := s.AttemptedWindows(strategyID, sinkKind)
		if err != nil {
			s.Close()
			return nil, nil, fmt.Errorf("attempted windows %s: %w", path, err)
		}
		for k := range aw {
			attempted[k] = true
		}

		dp, err := s.DerivedPositions(sinkKind, account)
		if err != nil {
			s.Close()
			return nil, nil, fmt.Errorf("derived positions %s: %w", path, err)
		}
		for _, pos := range dp {
			positions[pos.Symbol] += int(math.Round(pos.Shares / sharesPerLot))
		}

		if err := s.Close(); err != nil {
			return nil, nil, fmt.Errorf("close %s: %w", path, err)
		}
	}
	return attempted, positions, nil
}

// newInnerSink builds the unwrapped order sink named by sinkName. For
// "live" it also returns the concrete *live.Sink: Start/Baseline/
// ReconcileTerminal/Close are not part of engine.OrderSink, and the caller
// needs the concrete type to drive them. liveSink is nil for every other
// sink.
func newInnerSink(sinkName, fillMode, account string, tokens live.TokenSource, carinaHost string, hc *http.Client) (engine.OrderSink, *live.Sink, error) {
	switch sinkName {
	case "paper":
		switch fillMode {
		case "taker":
			return engine.NewPaperSink(), nil, nil
		case "mid":
			return engine.NewMidPriceSink(), nil, nil
		default:
			return nil, nil, fmt.Errorf("unknown fill-mode %q: want taker|mid", fillMode)
		}

	case "live":
		client, err := live.NewClient(carinaHost, account, tokens, hc, live.ArmingFromEnv())
		if err != nil {
			return nil, nil, fmt.Errorf("live sink: %w", err)
		}
		sink, err := live.NewSink(live.Options{Client: client})
		if err != nil {
			return nil, nil, fmt.Errorf("live sink: %w", err)
		}
		return sink, sink, nil

	default:
		return nil, nil, fmt.Errorf("unsupported sink %q: want paper|live", sinkName)
	}
}

// envTokens adapts a pair of pre-obtained session tokens (SB_ACCESS_TOKEN,
// SB_TRADING_ACCESS_TOKEN — never a fresh username/password login) to
// every TokenSource interface this binary needs (strategy/live,
// strategy/market): both are the same AccessToken()/TradingToken() shape
// strategy/auth.go's Session type satisfies elsewhere in this module. This
// mirrors account-recon's and this file's own pre-existing --source live
// path (newLiveSource), which already take tokens from the environment
// rather than performing a login.
type envTokens struct {
	access  string
	trading string
}

func (t envTokens) AccessToken() string  { return t.access }
func (t envTokens) TradingToken() string { return t.trading }

// newMarketGate builds the real, endpoint-backed strategy/market.Gate used
// whenever a run is risk-gated (--source live), so risk.RiskSink never
// falls back to risk.DenyAllGate's blanket deny for a run that genuinely
// has a live market to check against.
func newMarketGate(tokens market.TokenSource, hc *http.Client) (risk.MarketGate, error) {
	cal, err := calendar.Load()
	if err != nil {
		return nil, fmt.Errorf("calendar: %w", err)
	}
	client, err := market.NewClient(tokens, market.ClientOptions{
		ExodusHost: envDefault("SB_EXODUS_HOST", market.DefaultExodusHost),
		CarinaHost: envDefault("SB_CARINA_HOST", market.DefaultCarinaHost),
		HTTPClient: hc,
	})
	if err != nil {
		return nil, fmt.Errorf("client: %w", err)
	}
	gate, err := market.NewGate(cal, client, market.GateOptions{})
	if err != nil {
		return nil, fmt.Errorf("gate: %w", err)
	}
	return gate, nil
}

// riskLimits assembles risk.Limits from the --risk-* fraction flags. No
// value here is specified by plan 18 itself — the plan only requires that
// caps be fractions of injected sleeve equity (ADR-0017), not what the
// fractions should be — so these are operator-tunable defaults, not a
// modelled recommendation: 25% per order and per name, 100% per day and
// gross (a full rebalance into or out of the null portfolio legitimately
// spans the whole sleeve), 5% daily loss.
func riskLimits(perOrder, perDay, maxGross, maxPosition, dailyLoss float64) risk.Limits {
	return risk.Limits{
		PerOrderNotionalFraction:   perOrder,
		PerDayNotionalFraction:     perDay,
		MaxGrossExposureFraction:   maxGross,
		MaxPositionPerNameFraction: maxPosition,
		DailyLossLimitFraction:     dailyLoss,
	}
}

// storePnL adapts the post-trade store's own realized-PnL query to
// risk.PnLProvider, for "today" in Jakarta at the moment DailyPnLIDR is
// called.
//
// It reports REALIZED PnL only. An open position's unrealized leg is not
// visible to the post-trade store at all (see
// engine.PostTradeStore.RealizedPnLIDRByDate's doc comment on that
// division of labour), and a multi-partial order's realized leg is itself
// understated by an existing, already-documented gap (plan 18 §5 B4's
// status note on engine.recordRoundTrip keeping only the last partial
// fill's leg) — not a new gap introduced by this adapter.
type storePnL struct {
	store    *engine.PostTradeStore
	sinkKind string
}

func (p storePnL) DailyPnLIDR(ctx context.Context) (float64, error) {
	asOfDate := engine.JakartaDate(time.Now().UnixNano())
	idr, err := p.store.RealizedPnLIDRByDate(p.sinkKind, asOfDate)
	if err != nil {
		return 0, err
	}
	return float64(idr), nil
}

// wrapWithRisk wraps inner in a risk.RiskSink and returns
// RiskSink.EngineSink() — never a bare *RiskSink, see that method's doc
// comment for why — ready to hand to engine.Run.
func wrapWithRisk(inner engine.OrderSink, equityIDR int64, limits risk.Limits, marketGate risk.MarketGate, pnl risk.PnLProvider, killSwitchFile string, clock risk.Clock) (engine.OrderSink, error) {
	rs, err := risk.NewRiskSink(inner, risk.SleeveEquityIDR(equityIDR), limits, marketGate, pnl, killSwitchFile, clock)
	if err != nil {
		return nil, fmt.Errorf("risk sink: %w", err)
	}
	return rs.EngineSink(), nil
}

// replayOnlyPacing records pacing for a replay and the empty string for live,
// where the flag has no meaning. Recording "fast" against a live run would
// claim something about it that is not true.
func replayOnlyPacing(source, pacing string) string {
	if source == "replay" {
		return pacing
	}
	return ""
}

// binaryHash is the SHA-256 of the running executable, so a stored run can be
// tied to the code that produced it. Returns "" when the binary cannot be
// read (e.g. `go run`, which runs from a temp path that is then deleted) —
// empty means unknown and is never substituted with a guess.
func binaryHash() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	f, err := os.Open(exe)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return ""
	}
	return hex.EncodeToString(h.Sum(nil))
}

// newSource builds the MarketEvent channel for the requested --source mode.
// It is the only place strategy-run decides between replay and live; engine.Run
// itself is unchanged either way (engine.LiveSource and engine.ReplaySource
// share the same <-chan engine.MarketEvent contract).
func newSource(ctx context.Context, source, segmentsGlob, symbolsFlag, pacing string) (<-chan engine.MarketEvent, error) {
	switch source {
	case "replay":
		if segmentsGlob == "" {
			return nil, fmt.Errorf("--source replay requires --segments")
		}
		segments, err := globSegments(segmentsGlob)
		if err != nil {
			return nil, fmt.Errorf("resolve segments: %w", err)
		}
		if len(segments) == 0 {
			return nil, fmt.Errorf("no segments matched %q", segmentsGlob)
		}
		mode, err := parsePacing(pacing)
		if err != nil {
			return nil, err
		}
		return engine.ReplaySource(ctx, segments, mode), nil

	case "live":
		return newLiveSource(ctx, symbolsFlag)

	default:
		return nil, fmt.Errorf("unknown --source %q: want replay|live", source)
	}
}

// newLiveSource wires engine.LiveSource for --source live. Credentials come
// only from the environment (SB_ACCESS_TOKEN, a pre-obtained token — never
// hardcoded, never logged); this deliberately mirrors account-recon's
// pre-obtained-token pattern rather than performing a fresh username/password
// login, keeping this wiring minimal per plan 18 §5 B3.
func newLiveSource(ctx context.Context, symbolsFlag string) (<-chan engine.MarketEvent, error) {
	if symbolsFlag == "" {
		return nil, fmt.Errorf("--source live requires --symbols")
	}
	symbols := splitSymbols(symbolsFlag)

	accessToken := os.Getenv("SB_ACCESS_TOKEN")
	if accessToken == "" {
		return nil, fmt.Errorf("--source live requires SB_ACCESS_TOKEN in the environment")
	}

	cfg := feed.Config{
		AccessToken: accessToken,
		ExodusHost:  envDefault("SB_EXODUS_HOST", "https://exodus.stockbit.com"),
		WSURL:       envDefault("SB_WS_URL", "wss://wss-trading.stockbit.com/ws"),
		WSSubProto:  envDefault("SB_WS_SUBPROTOCOL", "web"),
	}
	session := feed.NewSession(cfg)
	// Login is a no-op network call when AccessToken is already set (see
	// feed.Session.Login's doc) — it only derives userID from the JWT.
	if err := session.Login(); err != nil {
		return nil, fmt.Errorf("session login: %w", err)
	}

	cal, err := calendar.Load()
	if err != nil {
		return nil, fmt.Errorf("calendar: %w", err)
	}

	channels := feed.Channels{OrderBook: true, RunningTradeBatch: true, LivePrice: true, IEPIEV: true}
	dialer := engine.NewWSDialer(session, cfg.WSURL, cfg.WSSubProto, symbols, channels)

	return engine.LiveSource(ctx, dialer, cal)
}

func splitSymbols(s string) []string {
	parts := strings.Split(s, ",")
	symbols := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			symbols = append(symbols, p)
		}
	}
	return symbols
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func globSegments(pattern string) ([]string, error) {
	matches, err := filepath.Glob(pattern)
	if err != nil {
		return nil, err
	}
	sort.Strings(matches)
	return matches, nil
}

// globFiles resolves pattern into a sorted list of matching file paths.
// Generic despite sitting next to globSegments (whose name predates this
// second, non-segment use — see --prior-stores/loadPriorState).
func globFiles(pattern string) ([]string, error) {
	matches, err := filepath.Glob(pattern)
	if err != nil {
		return nil, err
	}
	sort.Strings(matches)
	return matches, nil
}

func parsePacing(s string) (engine.PacingMode, error) {
	switch s {
	case "fast":
		return engine.ModeFast, nil
	case "paced":
		return engine.ModePaced, nil
	default:
		return 0, fmt.Errorf("unknown pacing %q: want fast|paced", s)
	}
}
