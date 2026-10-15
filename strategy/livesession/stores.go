package livesession

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"strategy/engine"
	"strategy/recon"
)

var unsafeName = regexp.MustCompile(`[^a-z0-9._-]+`)

// SafeName lowercases s and replaces anything outside [a-z0-9._-] with "-".
func SafeName(s string) string {
	s = strings.Trim(unsafeName.ReplaceAllString(strings.ToLower(s), "-"), "-")
	if s == "" {
		return "strategy"
	}
	return s
}

// attemptGlob matches every attempt store of strategyID on any date.
func attemptGlob(runsDir, strategyID string) string {
	return filepath.Join(runsDir, "*-"+SafeName(strategyID)+"-a[0-9]*.db")
}

var attemptName = regexp.MustCompile(`-a([0-9]+)\.db$`)

// NextStorePath returns runs/<date>-<strategy>-aN.db with N one past the
// highest attempt number already on disk for that date and strategy. It never
// returns a path that exists (a reused store dies on UNIQUE decision.seq), and
// does not create the file: strategy-run opens it.
func NextStorePath(runsDir, strategyID string, now time.Time) (string, error) {
	date := engine.JakartaDate(now.UnixNano())
	prefix := date + "-" + SafeName(strategyID) + "-a"
	matches, err := filepath.Glob(filepath.Join(runsDir, prefix+"[0-9]*.db"))
	if err != nil {
		return "", err
	}
	highest := 0
	for _, m := range matches {
		if sub := attemptName.FindStringSubmatch(filepath.Base(m)); sub != nil {
			if n, err := strconv.Atoi(sub[1]); err == nil && n > highest {
				highest = n
			}
		}
	}
	p := filepath.Join(runsDir, fmt.Sprintf("%s%d.db", prefix, highest+1))
	if _, err := os.Stat(p); err == nil {
		return "", fmt.Errorf("store %s already exists", p)
	}
	return p, nil
}

// PriorStoreGlobs is the --prior-stores value for a child run: the operator's
// extra globs (earlier days, legacy store names) plus every attempt store of
// this strategy on any date, so a crash-restart sees what the crashed attempt
// placed. strategy-run accepts comma-separated globs.
func PriorStoreGlobs(cfg Config, strategyID string) []string {
	cfg = cfg.withDefaults()
	return append(append([]string{}, cfg.PriorStores...), attemptGlob(cfg.RunsDir, strategyID))
}

// PositionCheck is the read-only holdings check shared by the pre-snapshot and
// the post-reconcile.
type PositionCheck struct {
	Result recon.GateResult
	Stores []string // base names of the stores that were read
}

// CheckPosition reads every store matching globs (plus extra explicit paths),
// strictly this account's live fills, the manual-trade file and the broker
// ledger, and compares the derived position with the broker's holdings via
// recon.CheckPositions. It places nothing: src has no order method.
func CheckPosition(ctx context.Context, src recon.PortfolioSource, cfg Config, globs, extraPaths []string) (PositionCheck, error) {
	cfg = cfg.withDefaults()
	paths, err := recon.ResolveStorePaths(append(append([]string{}, globs...), extraPaths...), nil)
	if err != nil {
		return PositionCheck{}, err
	}
	names, fills, err := recon.LoadStoreFills(paths, "live", strings.TrimSpace(cfg.Account), false)
	if err != nil {
		return PositionCheck{}, err
	}
	manual, err := recon.LoadManual(cfg.ManualTrades)
	if err != nil {
		return PositionCheck{}, err
	}
	imported, err := recon.LoadImports(cfg.BrokerLedger)
	if err != nil {
		return PositionCheck{}, err
	}
	res, err := recon.CheckPositions(ctx, src, recon.GateInputs{Fills: fills, Manual: manual, Imported: imported})
	if err != nil {
		return PositionCheck{}, err
	}
	return PositionCheck{Result: res, Stores: names}, nil
}

// ExpectedPosition is the precise meaning of "the broker's holdings are
// expected": for every symbol the broker holds or the stores/manual/imported
// trades imply, the broker's balance equals the net lots derived from (a) the
// live fills of THIS account in the prior-store globs, plus (b) manual trades
// in the manual file, plus (c) broker-imported trades in the ledger. Anything
// else, including a broker position no store explains and a store position the
// broker does not hold, is a divergence. The pre-snapshot refuses on it; the
// post-reconcile alerts and exits 1.
func ExpectedPosition(pc PositionCheck) bool { return pc.Result.Match }

// StoreSummary is one store's one-screen facts.
type StoreSummary struct {
	Path        string
	Exists      bool
	HasRun      bool
	Outcome     string
	BinaryHash  string
	Orders      int
	Fills       int
	FeesIDR     float64
	Rejects     int
	KillRejects int
	RejectText  string // distinct reasons, truncated
}

// ReadStoreSummary reads a store's run row and reject counts. A missing file
// is Exists=false with no error (the child may have died before creating it).
func ReadStoreSummary(path string) (StoreSummary, error) {
	sum := StoreSummary{Path: path}
	if _, err := os.Stat(path); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return sum, nil
		}
		return sum, err
	}
	sum.Exists = true
	st, err := engine.OpenStore(path)
	if err != nil {
		return sum, fmt.Errorf("open %s: %w", filepath.Base(path), err)
	}
	defer st.Close()

	var orders, fills sql.NullInt64
	var fees sql.NullFloat64
	err = st.QueryRow(`SELECT outcome, binary_hash, orders_attempted, fills, total_fees_idr FROM run LIMIT 1`).
		Scan(&sum.Outcome, &sum.BinaryHash, &orders, &fills, &fees)
	switch {
	case err == nil:
		sum.HasRun = true
		sum.Orders, sum.Fills, sum.FeesIDR = int(orders.Int64), int(fills.Int64), fees.Float64
	case !errors.Is(err, sql.ErrNoRows):
		return sum, fmt.Errorf("read run %s: %w", filepath.Base(path), err)
	}
	err = st.QueryRow(`SELECT COUNT(*), COALESCE(SUM(CASE WHEN LOWER(reason) LIKE '%kill switch%' THEN 1 ELSE 0 END), 0), COALESCE(group_concat(DISTINCT reason), '') FROM order_reject`).
		Scan(&sum.Rejects, &sum.KillRejects, &sum.RejectText)
	if err != nil {
		return sum, fmt.Errorf("read rejects %s: %w", filepath.Base(path), err)
	}
	if len(sum.RejectText) > 300 {
		sum.RejectText = sum.RejectText[:300] + "..."
	}
	return sum, nil
}
