package main

import (
	"context"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"strategy/account"
	"strategy/recon"
)

// historyWindowSlack widens the 3-month history window by a day so a fill on
// the boundary is not misjudged.
const historyWindowSlack = 24 * time.Hour

// reconBroker is the two read-only broker calls the multi-store check makes.
// *account.Client satisfies it; tests use a fake.
type reconBroker interface {
	recon.PortfolioSource
	History(ctx context.Context, q account.HistoryQuery) (account.History, error)
}

type multiConfig struct {
	Stores   []string // globs or paths
	Exclude  []string // store base names to skip
	SinkKind string
	AccNo    string // used to scope the stores; never written out
	Label    string // short label written to the report instead
	// TokenAccount is the account the trading token routes to ("" unknown).
	// /history is token-scoped, so it is only trusted when this equals AccNo.
	TokenAccount string

	ManualPath string
	LedgerPath string
	OutDir     string

	UseHistory bool
	DoImport   bool
}

// runMulti is the all-stores check: expected position from every live store
// for the account plus manual and imported broker trades, compared with the
// broker's holdings; then a trade-by-trade check against /history. Read-only
// towards the broker; it writes only the report and the import ledger.
func runMulti(ctx context.Context, cfg multiConfig, b reconBroker, now time.Time, stdout, stderr io.Writer) int {
	paths, err := recon.ResolveStorePaths(cfg.Stores, cfg.Exclude)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 2
	}
	if len(paths) == 0 {
		fmt.Fprintf(stderr, "account-recon: --stores %q matched no files\n", strings.Join(cfg.Stores, ","))
		return 2
	}
	names, fills, err := recon.LoadStoreFills(paths, cfg.SinkKind, cfg.AccNo, true)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 1
	}
	manual, err := recon.LoadManual(cfg.ManualPath)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 1
	}
	imported, err := recon.LoadImports(cfg.LedgerPath)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 1
	}

	holdings, err := recon.BrokerHoldings(ctx, b)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 1
	}

	in := recon.Inputs{
		AccountLabel: cfg.Label, AsOf: now, StoreFiles: names, StoreFills: fills,
		Manual: manual, Imported: imported, Holdings: holdings, Tolerance: recon.DefaultMatchTolerance,
	}
	switch {
	case !cfg.UseHistory:
		in.HistoryError = "broker history skipped (--no-history)"
	case strings.TrimSpace(cfg.TokenAccount) == "" || strings.TrimSpace(cfg.TokenAccount) != strings.TrimSpace(cfg.AccNo):
		// /history takes no account parameter: it answers for the token's own
		// account. Using it for another account would import the wrong
		// account's trades.
		in.HistoryError = "broker history skipped: the trading token's account is not the account being reconciled (or is unknown), and /history is token-scoped"
	default:
		h, err := b.History(ctx, account.DefaultHistoryQuery())
		if err != nil {
			in.HistoryError = fmt.Sprintf("broker history fetch failed: %v", err)
		} else {
			in.History = &h
			in.WindowStart = now.AddDate(0, -3, 0).Add(-historyWindowSlack)
		}
	}

	out := recon.Reconcile(in)

	if in.History != nil {
		logUnknown(stderr, in.History)
	}
	if cfg.DoImport && len(out.NewImports) > 0 {
		merged, _ := recon.MergeImports(imported, out.NewImports)
		if err := recon.SaveImports(cfg.LedgerPath, merged); err != nil {
			fmt.Fprintf(stderr, "account-recon: write broker ledger: %v\n", err)
			return 1
		}
	}
	reportPath := recon.ReportPath(cfg.OutDir, out.Report.Date)
	if err := recon.WriteReport(reportPath, out.Report); err != nil {
		fmt.Fprintf(stderr, "account-recon: write report: %v\n", err)
		return 1
	}

	printMulti(stdout, out.Report, reportPath, cfg.DoImport)
	if out.Report.Status != recon.StatusMatch || len(out.Report.Errors) > 0 {
		return 1
	}
	return 0
}

// logUnknown reports commands the parser did not recognise: counted in the
// report, and logged here so they are never silent.
func logUnknown(w io.Writer, h *account.History) {
	keys := make([]string, 0, len(h.Unknown))
	for k := range h.Unknown {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(w, "account-recon: history: unknown command %q on %d row(s): counted, not treated as a trade\n", k, h.Unknown[k])
	}
}

func printMulti(w io.Writer, r recon.Report, path string, imported bool) {
	label := "Positions match the broker"
	if r.Status != recon.StatusMatch {
		label = "Positions DO NOT match the broker"
	}
	fmt.Fprintf(w, "%s (%s, as of %s)\n", label, r.AccountLabel, r.AsOf)
	for _, p := range r.Positions {
		fmt.Fprintf(w, "  [%s] %s: expected %d lot(s), broker %v — %s\n", p.Status, p.Symbol, p.ExpectedLots, p.BrokerLots, p.Explanation)
	}
	if len(r.Positions) == 0 {
		fmt.Fprintln(w, "  flat on both sides")
	}
	if tc := r.TradeCheck; tc != nil {
		if !tc.Checked {
			fmt.Fprintf(w, "Trade check: NOT done — %s\n", tc.Error)
		} else {
			fmt.Fprintf(w, "Trade check: %d broker trade(s): %d matched, %d broker-only (manual), %d store-only (phantom), %d awaiting broker history; %d fee gap(s) over Rp 0.5\n",
				tc.BrokerTrades, tc.Matched, tc.BrokerOnly, tc.StoreOnly, tc.PendingHistory, tc.FeeGapsFlagged)
			if tc.NewlyImported > 0 {
				verb := "would import"
				if imported {
					verb = "imported"
				}
				fmt.Fprintf(w, "  %s %d broker trade(s) into the ledger (source %q)\n", verb, tc.NewlyImported, recon.SourceBrokerImport)
			}
		}
	}
	for _, wn := range r.Warnings {
		fmt.Fprintf(w, "WARNING: %s\n", wn)
	}
	for _, e := range r.Errors {
		fmt.Fprintf(w, "ERROR: %s\n", e)
	}
	fmt.Fprintf(w, "Report: %s\n", path)
}
