// Command live-session runs one supervised trading session end to end:
// preflight, pre-trade position snapshot, strategy-run with a fresh store per
// attempt (crash-restart without double submission), post-trade reconcile, a
// one-screen summary and alerts. See docs/runbooks/live-session.md.
//
// It places no orders itself. The only order path is the strategy-run child,
// which keeps its own ALGO_LIVE/ALGO_CONFIRM double-arm, token-account guard
// and position gate. Tokens are read from the environment
// (SB_TRADING_ACCESS_TOKEN, SB_ACCESS_TOKEN) and inherited by the child; they
// are never put on a command line, in a log or in an alert.
//
// Exit codes: 0 clean, 1 divergence/reject/kill switch, 2 refused or crashed.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"collector/calendar"
	"strategy/account"
	"strategy/live"
	"strategy/livesession"
)

func main() {
	os.Exit(run(os.Args, os.Stdout, os.Stderr))
}

type listFlag []string

func (l *listFlag) String() string     { return strings.Join(*l, ",") }
func (l *listFlag) Set(v string) error { *l = append(*l, v); return nil }

func run(argv []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("live-session", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var cfg livesession.Config
	var priors listFlag
	fs.StringVar(&cfg.Sink, "sink", "live", "order sink: live (real orders) | paper (simulated fills; no trading token, no arming; with --source live needs only SB_ACCESS_TOKEN)")
	fs.StringVar(&cfg.Source, "source", "", "market data: live|replay (default: live for --sink live, replay for --sink paper; use --sink paper --source live for a shadow run on live prices)")
	fs.StringVar(&cfg.Strategy, "strategy", "rebalance", "strategy: rebalance|orb")
	fs.StringVar(&cfg.Account, "account", "", "sleeve sub-account (required for --sink live; must equal the trading token's account)")
	fs.Int64Var(&cfg.EquityIDR, "equity-idr", 0, "sleeve equity in IDR (required with --source live)")
	fs.Int64Var(&cfg.MaxBuyNotionalIDR, "max-buy-notional-idr", 0, "hard per-day cap on cumulative BUY notional in IDR (0 = off); forwarded to strategy-run, which refuses any buy that would exceed it. The unattended runner always sets it")
	fs.StringVar(&cfg.TargetPortfolio, "target-portfolio", "", "target_portfolio.json (--strategy rebalance)")
	fs.StringVar(&cfg.RebalanceAt, "rebalance-at", "", "rebalance window opening, HH:MM WIB, in-session and in the future (required for rebalance; no default)")
	fs.StringVar(&cfg.Symbols, "symbols", "", "comma-separated symbol watchlist (--source live)")
	fs.StringVar(&cfg.Segments, "segments", "", "glob of .binpb.zst segments (--source replay)")
	fs.IntVar(&cfg.Retry, "retry", 0, "deliberate same-day resubmission level, passed to strategy-run as given; never raised automatically (0 = off, N >= 2)")
	fs.StringVar(&cfg.RunsDir, "runs-dir", "runs", "directory for stores (runs/<date>-<strategy>-aN.db), the kill-switch file and the manual/ledger files")
	fs.Var(&priors, "prior-stores", "extra glob of earlier stores (repeatable), e.g. earlier days or legacy store names; this strategy's own -aN stores are always included")
	fs.StringVar(&cfg.ManualTrades, "manual-trades", "", "manual trades file (default <runs-dir>/manual_trades.json)")
	fs.StringVar(&cfg.BrokerLedger, "broker-ledger", "", "broker-imported trades ledger (default <runs-dir>/broker_trades.json)")
	fs.StringVar(&cfg.KillSwitchFile, "kill-switch-file", "", "kill-switch file (default <runs-dir>/KILL); presence refuses a start and stops a running session")
	fs.StringVar(&cfg.StrategyRunBin, "strategy-run-bin", "", "strategy-run binary (default: next to live-session, else PATH)")
	fs.StringVar(&cfg.ExpectBinarySHA, "expect-binary-sha256", "", "refuse unless the strategy-run binary has this sha256 (optional pin)")
	fs.IntVar(&cfg.MaxRestarts, "max-restarts", livesession.DefaultMaxRestarts, "restarts after a crash, each with a fresh store")
	fs.DurationVar(&cfg.StopAfter, "stop-after", livesession.DefaultStopAfter, "stop the run (graceful SIGINT) this long after --rebalance-at")
	fs.DurationVar(&cfg.MinLead, "min-lead", livesession.DefaultMinLead, "--rebalance-at must be at least this far in the future at start")
	fs.DurationVar(&cfg.ReconSettle, "recon-settle", livesession.DefaultReconSettle, "wait between post-reconcile retries (the broker portfolio can lag a fill)")
	fs.IntVar(&cfg.ReconTries, "recon-tries", livesession.DefaultReconTries, "post-reconcile attempts before declaring divergence")
	if err := fs.Parse(argv[1:]); err != nil {
		return livesession.ExitRefused
	}
	cfg.PriorStores = priors
	cfg.ExtraArgs = fs.Args() // everything after `--` goes to strategy-run verbatim
	cfg.Invocation = argv
	if cfg.Source == "" {
		cfg.Source = "live"
		if cfg.Sink == "paper" {
			cfg.Source = "replay"
		}
	}
	if cfg.StrategyRunBin == "" {
		cfg.StrategyRunBin = defaultStrategyRun()
	}

	cal, err := calendar.Load()
	if err != nil {
		fmt.Fprintf(stderr, "live-session: calendar: %v\n", err)
		return livesession.ExitRefused
	}

	notifiers := livesession.Multi{livesession.LogNotifier{W: stderr}}
	if u := os.Getenv(livesession.WebhookEnv); u != "" {
		wh, err := livesession.NewWebhookNotifier(u, nil)
		if err != nil {
			fmt.Fprintf(stderr, "live-session: %v (alerts go to stderr only)\n", err)
		} else {
			notifiers = append(notifiers, wh)
		}
	}

	token := os.Getenv("SB_TRADING_ACCESS_TOKEN")
	deps := livesession.Deps{
		Cal:          cal,
		TradingToken: token,
		Armed:        live.ArmingFromEnv().Armed(), // same double-arm strategy-run enforces
		Child:        livesession.ExecRunner{Bin: cfg.StrategyRunBin, Stdout: stdout, Stderr: stderr},
		Notifier:     notifiers,
		Out:          stdout,
	}
	if cfg.Sink == "live" && token != "" && strings.TrimSpace(cfg.Account) != "" {
		host := os.Getenv("SB_CARINA_HOST")
		if host == "" {
			host = account.DefaultHost
		}
		client, err := account.New(host, strings.TrimSpace(cfg.Account), staticToken(token), &http.Client{Timeout: 20 * time.Second})
		if err != nil {
			fmt.Fprintf(stderr, "live-session: broker client: %v\n", err)
			return livesession.ExitRefused
		}
		deps.Broker = client
	}

	// An operator Ctrl-C ends the run gracefully (the child drains open
	// orders) and still reaches the post-reconcile and summary.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return livesession.New(cfg, deps).Run(ctx)
}

// defaultStrategyRun prefers a strategy-run next to this executable (the
// `make build` layout), then PATH.
func defaultStrategyRun() string {
	if exe, err := os.Executable(); err == nil {
		p := filepath.Join(filepath.Dir(exe), "strategy-run")
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	return "strategy-run"
}

type staticToken string

func (t staticToken) TradingToken() string { return string(t) }
func (t staticToken) AccessToken() string  { return string(t) }
