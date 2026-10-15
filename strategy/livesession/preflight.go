package livesession

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"collector/calendar"
	"strategy/portfolio"
	"strategy/strategies/rebalance"
	"strategy/tokenacct"
)

// Calendar is the trading-day check (*calendar.Calendar satisfies it).
type Calendar interface {
	IsTradingDay(d time.Time) bool
}

// PreflightInput is everything Preflight needs; nothing is read from the
// process environment so tests stay hermetic.
type PreflightInput struct {
	Cfg          Config
	Now          time.Time
	Cal          Calendar
	TradingToken string // SB_TRADING_ACCESS_TOKEN; never logged
	Armed        bool   // live.ArmingFromEnv().Armed()
	// Restart relaxes the lead rule for a crash-restart: --rebalance-at may
	// already have passed as long as the stop deadline (at + StopAfter) has
	// not. The strategy's attempted-window recovery, not this check, is what
	// prevents a second submission.
	Restart bool
}

// PreflightResult is what a passing preflight hands to the rest of the session.
type PreflightResult struct {
	StrategyID string                     // the store-naming / prior-store partition key
	Portfolio  *portfolio.TargetPortfolio // rebalance only
	At         time.Time                  // --rebalance-at on today's date (WIB); zero for replay
	BinarySHA  string                     // hex sha256 of the strategy-run binary
}

// Preflight checks every refusal condition and returns ALL failures, not just
// the first, so the operator fixes a bad session in one pass. It runs before
// any store, snapshot or order path exists, and is re-run before every restart.
func Preflight(in PreflightInput) (PreflightResult, []error) {
	cfg := in.Cfg.withDefaults()
	var errs []error
	fail := func(format string, a ...any) { errs = append(errs, fmt.Errorf(format, a...)) }
	res := PreflightResult{StrategyID: cfg.Strategy}

	isLive := cfg.Sink == "live"
	switch cfg.Sink {
	case "live", "paper":
	default:
		fail("--sink %q: want paper|live", cfg.Sink)
	}
	switch cfg.Source {
	case "live", "replay":
	default:
		fail("--source %q: want live|replay", cfg.Source)
	}
	if isLive && cfg.Source != "live" {
		fail("--sink live requires --source live (never place real orders against replayed data)")
	}
	if cfg.Strategy != "rebalance" && cfg.Strategy != "orb" {
		fail("--strategy %q: want orb|rebalance", cfg.Strategy)
	}
	if err := rebalance.ValidateRetry(cfg.Retry); err != nil {
		fail("--retry: %v", err)
	}
	if cfg.Retry != 0 && cfg.Strategy != "rebalance" {
		fail("--retry is only valid with --strategy rebalance")
	}

	if cfg.MaxBuyNotionalIDR < 0 {
		fail("--max-buy-notional-idr must not be negative (got %d)", cfg.MaxBuyNotionalIDR)
	}
	if cfg.MaxBuyNotionalIDR > 0 && cfg.Source != "live" {
		fail("--max-buy-notional-idr needs --source live (the risk layer that enforces it only wraps live-source runs)")
	}

	// Account / token / arming.
	if isLive {
		if strings.TrimSpace(cfg.Account) == "" {
			fail("--account is required with --sink live (no default)")
		}
		switch {
		case in.TradingToken == "":
			fail("SB_TRADING_ACCESS_TOKEN is not set")
		case strings.TrimSpace(cfg.Account) != "":
			acn, err := tokenacct.Account(in.TradingToken)
			switch {
			case err != nil:
				fail("cannot determine the trading token's account: %v", err)
			case acn != strings.TrimSpace(cfg.Account):
				fail("trading token is for account %q but --account is %q: orders route by the token's account (re-mint the token under the right account)", acn, strings.TrimSpace(cfg.Account))
			}
		}
		if !in.Armed {
			fail("not armed: set ALGO_LIVE=true and ALGO_CONFIRM=<phrase> (see docs/runbooks/live-session.md)")
		}
	}
	if cfg.Source == "live" {
		if cfg.EquityIDR <= 0 {
			fail("--equity-idr must be > 0 with --source live")
		}
		if strings.TrimSpace(cfg.Symbols) == "" {
			fail("--symbols is required with --source live")
		}
	} else if cfg.Source == "replay" && strings.TrimSpace(cfg.Segments) == "" {
		fail("--segments is required with --source replay")
	}

	// Only a "candidate" emitter spec may arm a live sink; a paper spec is paper-only.
	if isLive && strings.TrimSpace(cfg.TargetPortfolio) != "" {
		if err := portfolio.RequireLiveCandidate(cfg.TargetPortfolio); err != nil {
			fail("%v", err)
		}
	}

	// Target portfolio and rebalance window.
	var at time.Time
	if cfg.Strategy == "rebalance" {
		if strings.TrimSpace(cfg.TargetPortfolio) == "" {
			fail("--target-portfolio is required with --strategy rebalance")
		} else if tp, err := portfolio.Load(cfg.TargetPortfolio); err != nil {
			fail("target portfolio: %v", err)
		} else {
			res.Portfolio = &tp
			res.StrategyID = tp.StrategyID
		}
		if strings.TrimSpace(cfg.RebalanceAt) == "" {
			fail("--rebalance-at is required (strategy-run's 15:00 default is after the close)")
		}
	}
	if strings.TrimSpace(cfg.RebalanceAt) != "" {
		t, err := ParseRebalanceAt(cfg.RebalanceAt, in.Now)
		if err != nil {
			fail("%v", err)
		} else {
			at = t
			res.At = t
		}
	}
	if cfg.Source == "live" && !at.IsZero() && res.Portfolio != nil {
		tp := res.Portfolio
		if at.Before(tp.AsOf) || !at.Before(tp.Constraints.CompleteBy) {
			fail("--rebalance-at %s is outside the target's window [as_of %s, complete_by %s)", cfg.RebalanceAt,
				tp.AsOf.In(calendar.Jakarta).Format("2006-01-02 15:04"), tp.Constraints.CompleteBy.In(calendar.Jakarta).Format("2006-01-02 15:04"))
		}
	}

	// Market / clock. Only a live source has a market to wait for.
	if cfg.Source == "live" {
		if in.Cal != nil && !in.Cal.IsTradingDay(in.Now.In(calendar.Jakarta)) {
			fail("market is closed today (not an IDX trading day)")
		}
		if !at.IsZero() {
			if !InSession(at) {
				fail("--rebalance-at %s is not in a continuous-trading session (Mon-Thu 09:00-12:00 & 13:30-15:49, Fri 09:00-11:30 & 14:00-15:49 WIB)", cfg.RebalanceAt)
			}
			deadline := at
			if in.Restart {
				deadline = at.Add(cfg.StopAfter)
			}
			if !deadline.After(in.Now.Add(cfg.MinLead)) {
				fail("--rebalance-at %s is not at least %v in the future (now %s WIB; restart=%v)", cfg.RebalanceAt, cfg.MinLead, in.Now.In(calendar.Jakarta).Format("15:04:05"), in.Restart)
			}
		}
	}

	// Kill switch: fail closed on anything but "definitely absent".
	if _, err := os.Stat(cfg.KillSwitchFile); err == nil {
		fail("kill-switch file %s is present: remove it deliberately before starting", cfg.KillSwitchFile)
	} else if !errors.Is(err, os.ErrNotExist) {
		fail("kill-switch file %s could not be checked (%v): failing closed", cfg.KillSwitchFile, err)
	}

	// strategy-run binary.
	sha, err := FileSHA256(cfg.StrategyRunBin)
	switch {
	case err != nil:
		fail("strategy-run binary %q unreadable: %v", cfg.StrategyRunBin, err)
	case cfg.ExpectBinarySHA != "" && !strings.EqualFold(cfg.ExpectBinarySHA, sha):
		fail("strategy-run binary sha256 does not match --expect-binary-sha256")
	default:
		res.BinarySHA = sha
	}
	return res, errs
}

// ParseRebalanceAt parses a strict HH:MM (24h) into today's date in WIB, where
// "today" is now's Jakarta date.
func ParseRebalanceAt(s string, now time.Time) (time.Time, error) {
	s = strings.TrimSpace(s)
	bad := fmt.Errorf("--rebalance-at %q must be HH:MM (24h, WIB)", s)
	if len(s) != 5 || s[2] != ':' {
		return time.Time{}, bad
	}
	for _, i := range []int{0, 1, 3, 4} {
		if s[i] < '0' || s[i] > '9' {
			return time.Time{}, bad
		}
	}
	h, m := int(s[0]-'0')*10+int(s[1]-'0'), int(s[3]-'0')*10+int(s[4]-'0')
	if h > 23 || m > 59 {
		return time.Time{}, fmt.Errorf("--rebalance-at %q is not a valid time of day", s)
	}
	y, mo, d := now.In(calendar.Jakarta).Date()
	return time.Date(y, mo, d, h, m, 0, 0, calendar.Jakarta), nil
}

// InSession reports whether t (any zone) falls in IDX continuous trading:
// Mon-Thu 09:00-12:00 and 13:30-15:49, Fri 09:00-11:30 and 14:00-15:49 WIB.
// Holidays are the Calendar's job. The endpoint-backed market gate inside
// strategy-run remains the authority at order time.
func InSession(t time.Time) bool {
	w := t.In(calendar.Jakarta)
	mins := w.Hour()*60 + w.Minute()
	var breakStart, breakEnd int
	switch w.Weekday() {
	case time.Saturday, time.Sunday:
		return false
	case time.Friday:
		breakStart, breakEnd = 11*60+30, 14*60
	default:
		breakStart, breakEnd = 12*60, 13*60+30
	}
	const sessionOpen, sessionEnd = 9 * 60, 15*60 + 50
	return mins >= sessionOpen && mins < sessionEnd && (mins < breakStart || mins >= breakEnd)
}

// FileSHA256 returns the hex SHA-256 of the file at path.
func FileSHA256(path string) (string, error) {
	if path == "" {
		return "", errors.New("no path")
	}
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
