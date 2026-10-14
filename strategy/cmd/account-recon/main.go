// Command account-recon fetches broker-reported holdings from Carina and
// reconciles them against the position derived from a post-trade store,
// printing a typed divergence report. It sends zero orders: strategy/account
// and this command are both structurally read-only (see
// strategy/account/readonly_test.go).
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"strategy/account"
	"strategy/engine"
	"strategy/tokenacct"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("account-recon", flag.ContinueOnError)
	fs.SetOutput(stderr)
	storePath := fs.String("store", "", "path to ONE SQLite post-trade store (single-store position check; use --stores for the full reconciliation)")
	storesFlag := fs.String("stores", "", "comma-separated globs of post-trade stores, e.g. 'runs/*.db': derive the expected position from ALL live stores + manual + imported broker trades, check every trade against broker history, write runs/recon/<date>.json")
	excludeStores := fs.String("exclude-stores", "", "comma-separated store file base names to skip (--stores only)")
	manualPath := fs.String("manual-trades", "runs/manual_trades.json", "manual trades file (--stores only; a missing file means none)")
	ledgerPath := fs.String("broker-ledger", "runs/broker_trades.json", "broker-imported trades ledger (--stores only; created and updated idempotently)")
	outDir := fs.String("out-dir", "runs/recon", "directory for <date>.json (--stores only)")
	accountLabel := fs.String("account-label", "sleeve", "short label written to the report instead of the account number (--stores only)")
	noHistory := fs.Bool("no-history", false, "skip the broker trade-history check (--stores only)")
	noImport := fs.Bool("no-import", false, "report broker-only trades but do not write them to the ledger (--stores only)")
	accNo := fs.String("acc-no", "", "brokerage sub-account number to reconcile, e.g. the trading sleeve (required, no default)")
	sinkKind := fs.String("sink-kind", "live", "sink_kind to reconcile against (e.g. live, paper)")
	format := fs.String("format", "text", "output format: text|json")
	timeout := fs.Duration("timeout", 20*time.Second, "HTTP timeout for the Carina request")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	if (*storePath == "") == (*storesFlag == "") {
		fmt.Fprintln(stderr, "account-recon: exactly one of --store (one store) or --stores (all stores, full reconciliation) is required")
		fs.Usage()
		return 2
	}
	if *accNo == "" {
		// No default on purpose: falling back to some "default" account
		// would silently reconcile whichever account the trading token
		// itself belongs to (confirmed live: that can be a different,
		// pre-existing core book, not the sleeve this bot trades).
		fmt.Fprintln(stderr, "account-recon: --acc-no is required (the sub-account to reconcile; there is no safe default)")
		fs.Usage()
		return 2
	}
	if *format != "text" && *format != "json" {
		fmt.Fprintf(stderr, "account-recon: --format must be text or json, got %q\n", *format)
		return 2
	}

	token := os.Getenv("SB_TRADING_ACCESS_TOKEN")
	if token == "" {
		fmt.Fprintln(stderr, "account-recon: SB_TRADING_ACCESS_TOKEN is not set")
		return 2
	}
	host := envDefault("SB_CARINA_HOST", account.DefaultHost)

	if *storesFlag != "" {
		client, err := account.New(host, *accNo, staticToken(token), &http.Client{Timeout: *timeout})
		if err != nil {
			fmt.Fprintf(stderr, "account-recon: %v\n", err)
			return 2
		}
		tokenAcc, err := tokenacct.Account(token)
		if err != nil {
			fmt.Fprintf(stderr, "account-recon: cannot read the trading token's account (%v): broker history will be skipped\n", err)
			tokenAcc = ""
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3**timeout)
		defer cancel()
		return runMulti(ctx, multiConfig{
			Stores: splitList(*storesFlag), Exclude: splitList(*excludeStores), SinkKind: *sinkKind,
			AccNo: *accNo, Label: *accountLabel, TokenAccount: tokenAcc,
			ManualPath: *manualPath, LedgerPath: *ledgerPath, OutDir: *outDir,
			UseHistory: !*noHistory, DoImport: !*noImport,
		}, client, time.Now(), stdout, stderr)
	}

	store, err := engine.OpenStore(*storePath)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: open store: %v\n", err)
		return 1
	}
	defer store.Close()

	derived, err := store.DerivedPositions(*sinkKind, *accNo)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: read derived positions: %v\n", err)
		return 1
	}

	// Our own order IDs — the only reliable discriminator between "our
	// resting orders" and "some other sub-account's", since /order/v2/list
	// returns every sub-account's orders with no account attribution at all
	// (see account.OpenOrders' doc comment).
	ourOrderIDs, err := store.OrderIDs(*sinkKind, *accNo)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: read our order ids: %v\n", err)
		return 1
	}

	client, err := account.New(host, *accNo, staticToken(token), &http.Client{Timeout: *timeout})
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 2
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	broker, err := client.PortfolioList(ctx)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: fetch broker portfolio: %v\n", err)
		return 1
	}

	// Open orders are needed to compute how many shares of each symbol are
	// already committed to a resting sell order — the broker's `available`
	// figure nets that out, and without it a perfectly normal account with
	// a resting sell reads as DivergenceSettlementInconsistent (see
	// account.Reconcile's doc comment).
	openOrders, err := client.OpenOrders(ctx)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: fetch open orders: %v\n", err)
		return 1
	}

	report := account.Reconcile(*sinkKind, *accNo, broker, openOrders, ourOrderIDs, derived)

	if err := printReport(stdout, report, *format); err != nil {
		fmt.Fprintf(stderr, "account-recon: %v\n", err)
		return 1
	}

	return report.ExitCode()
}

func printReport(w io.Writer, r account.Report, format string) error {
	if format == "json" {
		enc := json.NewEncoder(w)
		enc.SetIndent("", "  ")
		return enc.Encode(r)
	}

	fmt.Fprintf(w, "Reconciliation report — sink_kind=%s acc_no=%s\n", r.SinkKind, r.AccountNumber)
	fmt.Fprintf(w, "Verdict: %s (broker symbols=%d, store symbols=%d)\n", r.Verdict, r.BrokerSymbols, r.StoreSymbols)
	if len(r.Divergences) == 0 {
		fmt.Fprintln(w, "No divergences.")
		return nil
	}
	for _, d := range r.Divergences {
		fmt.Fprintf(w, "[%s] %s: %s\n", d.Severity, d.Kind, d.Detail)
	}
	return nil
}

// splitList splits a comma-separated flag, trimming blanks.
func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// staticToken adapts a plain string token to account.TokenSource.
type staticToken string

func (t staticToken) TradingToken() string { return string(t) }
