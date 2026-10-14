package recon

import (
	"context"
	"fmt"
	"strings"
	"time"

	"strategy/account"
	"strategy/engine"
)

// PortfolioSource is the one broker call the holdings check needs.
// *account.Client satisfies it; tests use a fake. It has no order method, so
// the recon path cannot place an order through it.
type PortfolioSource interface {
	PortfolioList(ctx context.Context) (account.PortfolioList, error)
}

// BrokerHoldings returns the broker's balance per symbol in shares, omitting
// flat symbols. A failure to read the portfolio is an error — never an empty
// map, which would read as "the account is flat" and could even match.
func BrokerHoldings(ctx context.Context, src PortfolioSource) (map[string]float64, error) {
	pl, err := src.PortfolioList(ctx)
	if err != nil {
		return nil, fmt.Errorf("fetch broker portfolio: %w", err)
	}
	out := map[string]float64{}
	for _, p := range pl.Results {
		if shares := p.Qty.Balance.Shares(); shares != 0 {
			out[p.Symbol] += shares
		}
	}
	return out, nil
}

// importAsTrade converts a ledger import to a history trade so it can be
// matched against fills with the same rule. A missing time is noon, which a
// time tolerance cannot satisfy: an import with no time never matches by time.
func importAsTrade(i ImportedTrade) account.HistoryTrade {
	clock := i.TimeWIB
	switch {
	case clock == "":
		clock = "12:00:00"
	case len(clock) == 5:
		clock += ":00"
	}
	ts, err := time.ParseInLocation("2006-01-02 15:04:05", i.Date+" "+clock, account.WIB)
	if err != nil {
		ts = time.Time{}
	}
	return account.HistoryTrade{ID: i.BrokerID, Symbol: i.Symbol, Side: i.Side, PriceIDR: i.PriceIDR, Lots: i.Lots, FeeIDR: i.FeeIDR, Time: ts}
}

// explainedImportIDs returns the broker ids of imports that a store fill
// already explains, so they are not counted twice.
func explainedImportIDs(imports []ImportedTrade, fills []engine.StoreFill, tol time.Duration) map[string]bool {
	trades := make([]account.HistoryTrade, 0, len(imports))
	for _, i := range imports {
		trades = append(trades, importAsTrade(i))
	}
	out := map[string]bool{}
	for _, p := range MatchTrades(trades, fills, tol).Matched {
		out[p.Broker.ID] = true
	}
	return out
}

// GateInputs is what the pre-trade gate derives the expected position from.
// Fills must be strictly this account's (engine.Fills with
// includeUnattributed=false): the gate guards real orders, so a fill whose
// account is unknown is not assumed to be ours.
type GateInputs struct {
	Fills    []engine.StoreFill
	Manual   []ManualTrade
	Imported []ImportedTrade
}

// GateResult is the holdings check's outcome.
type GateResult struct {
	Match bool
	// Rows are the per-symbol comparisons (only non-flat symbols).
	Rows []SymbolRow
	// ExpectedLots is the broker-consistent position the strategy should
	// start from: stores plus manual plus imported trades, flat symbols
	// omitted. Meaningful when Match is true.
	ExpectedLots map[string]int
	// IgnoredBeforeStores counts manual/imported trades dated before the
	// earliest loaded fill. The gate sees only the stores --prior-stores
	// names; a manual sale from before them sold lots bought in stores that
	// are not loaded, so subtracting it from the loaded fills would
	// undercount. Widen --prior-stores to cover the position's whole life if
	// that is wrong.
	IgnoredBeforeStores int
}

// legBefore reports whether a ledger leg dated date (YYYY-MM-DD) at clock
// (HH:MM[:SS], empty when unknown) is earlier than at. A leg with no time is
// before only if its whole date is.
func legBefore(date, clock string, at time.Time) bool {
	atDate := at.Format("2006-01-02")
	if date != atDate {
		return date < atDate
	}
	if clock == "" {
		return false
	}
	if len(clock) == 5 {
		clock += ":00"
	}
	return clock < at.Format("15:04:05")
}

// CheckPositions compares the broker's holdings with the position the run
// would load: the prior stores' fills plus manual and imported trades. It does
// not fetch trade history; run account-recon to import manual app trades into
// the ledger first.
func CheckPositions(ctx context.Context, src PortfolioSource, in GateInputs) (GateResult, error) {
	holdings, err := BrokerHoldings(ctx, src)
	if err != nil {
		return GateResult{}, err
	}
	explained := explainedImportIDs(in.Imported, in.Fills, DefaultMatchTolerance)
	var imports []ImportedTrade
	for _, i := range in.Imported {
		if !explained[i.BrokerID] {
			imports = append(imports, i)
		}
	}
	manual, _ := supersede(in.Manual, imports)

	// Only trades from the loaded stores' own period apply: see
	// GateResult.IgnoredBeforeStores.
	ignored := 0
	if len(in.Fills) > 0 {
		start := in.Fills[0]
		for _, f := range in.Fills {
			if fillLess(f, start) {
				start = f
			}
		}
		startAt := fillTime(start)
		var keepImports []ImportedTrade
		for _, i := range imports {
			if legBefore(i.Date, i.TimeWIB, startAt) {
				ignored++
			} else {
				keepImports = append(keepImports, i)
			}
		}
		var keepManual []ManualTrade
		for _, m := range manual {
			if legBefore(m.Date, m.TimeWIB, startAt) {
				ignored++
			} else {
				keepManual = append(keepManual, m)
			}
		}
		imports, manual = keepImports, keepManual
	}

	bd, by := netBySymbol(legsFor(in.Fills, manual, imports))
	rows := compareHoldings(bd, by, holdings, false)

	res := GateResult{Match: true, Rows: rows, ExpectedLots: map[string]int{}, IgnoredBeforeStores: ignored}
	for _, r := range rows {
		if r.Status != StatusMatch {
			res.Match = false
		}
		if r.ExpectedLots != 0 {
			res.ExpectedLots[r.Symbol] = r.ExpectedLots
		}
	}
	return res, nil
}

// OverrideFlag is the flag that lets a run start despite a mismatch.
const OverrideFlag = "--allow-position-mismatch"

// Message is the refusal text: every mismatched symbol with its explanation,
// and what to do about it. reconCommand is shown verbatim as the fix.
func (g GateResult) Message(reconCommand string) string {
	var b strings.Builder
	b.WriteString("refusing to start --sink live: the broker's holdings do not match the position this run would load.\n")
	for _, r := range g.Rows {
		if r.Status == StatusMatch {
			continue
		}
		fmt.Fprintf(&b, "  %s: expected %d lot(s), broker holds %v lot(s) — %s\n", r.Symbol, r.ExpectedLots, r.BrokerLots, r.Explanation)
	}
	b.WriteString("A bot that loads a wrong position places wrong orders. Fix the records first:\n")
	fmt.Fprintf(&b, "  %s\n", reconCommand)
	fmt.Fprintf(&b, "(this imports any trade made by hand in the app; re-run strategy-run afterwards). To start anyway, knowing the position is wrong, pass %s.", OverrideFlag)
	return b.String()
}
