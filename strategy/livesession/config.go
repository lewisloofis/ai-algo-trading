package livesession

import (
	"time"
)

// Defaults.
const (
	DefaultMaxRestarts = 2
	DefaultStopAfter   = 3 * time.Minute
	DefaultMinLead     = 60 * time.Second
	DefaultKillPoll    = 2 * time.Second
	DefaultReconTries  = 3
	DefaultReconSettle = 5 * time.Second
)

// Config is one session's settings. Every field maps to a live-session flag.
type Config struct {
	Sink     string // paper|live
	Source   string // live|replay
	Strategy string // orb|rebalance

	Account   string // required for Sink == live
	EquityIDR int64  // required for Source == live

	TargetPortfolio string // rebalance only
	RebalanceAt     string // HH:MM WIB; required for rebalance, no default
	Symbols         string // Source == live
	Segments        string // Source == replay
	Retry           int    // deliberate resubmission level, passed through; never raised automatically
	// MaxBuyNotionalIDR is the absolute per-day cap on cumulative BUY notional,
	// forwarded to strategy-run, which enforces it in its risk layer (0 = off).
	MaxBuyNotionalIDR int64

	RunsDir        string   // stores live here; default "runs"
	PriorStores    []string // extra globs (earlier days); the strategy's own -aN stores are always added
	ManualTrades   string   // default <RunsDir>/manual_trades.json
	BrokerLedger   string   // default <RunsDir>/broker_trades.json
	KillSwitchFile string   // default <RunsDir>/KILL

	StrategyRunBin  string // path to the strategy-run binary
	ExpectBinarySHA string // optional pin (hex sha256) of the strategy-run binary

	MaxRestarts int           // crash restarts after the first attempt
	StopAfter   time.Duration // SIGINT the child this long after --rebalance-at
	MinLead     time.Duration // rebalance-at must be at least this far in the future
	KillPoll    time.Duration
	ReconTries  int
	ReconSettle time.Duration

	ExtraArgs  []string // appended verbatim to the strategy-run command line
	Invocation []string // the live-session argv, to print a --retry command
}

// withDefaults fills unset fields. It returns a copy.
func (c Config) withDefaults() Config {
	if c.RunsDir == "" {
		c.RunsDir = "runs"
	}
	if c.ManualTrades == "" {
		c.ManualTrades = c.RunsDir + "/manual_trades.json"
	}
	if c.BrokerLedger == "" {
		c.BrokerLedger = c.RunsDir + "/broker_trades.json"
	}
	if c.KillSwitchFile == "" {
		c.KillSwitchFile = c.RunsDir + "/KILL"
	}
	if c.KillPoll <= 0 {
		c.KillPoll = DefaultKillPoll
	}
	if c.ReconTries <= 0 {
		c.ReconTries = DefaultReconTries
	}
	return c
}
