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
	"time"

	"strategy/account"
	"strategy/engine"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("account-recon", flag.ContinueOnError)
	fs.SetOutput(stderr)
	storePath := fs.String("store", "", "path to the SQLite post-trade store (required)")
	sinkKind := fs.String("sink-kind", "live", "sink_kind to reconcile against (e.g. live, paper)")
	format := fs.String("format", "text", "output format: text|json")
	timeout := fs.Duration("timeout", 20*time.Second, "HTTP timeout for the Carina request")
	if err := fs.Parse(args); err != nil {
		return 2
	}

	if *storePath == "" {
		fmt.Fprintln(stderr, "account-recon: --store is required")
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

	store, err := engine.OpenStore(*storePath)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: open store: %v\n", err)
		return 1
	}
	defer store.Close()

	derived, err := store.DerivedPositions(*sinkKind)
	if err != nil {
		fmt.Fprintf(stderr, "account-recon: read derived positions: %v\n", err)
		return 1
	}

	client := account.New(host, staticToken(token), &http.Client{Timeout: *timeout})

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	broker, err := client.PortfolioList(ctx, "")
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

	report := account.Reconcile(*sinkKind, broker, openOrders, derived)

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

	fmt.Fprintf(w, "Reconciliation report — sink_kind=%s\n", r.SinkKind)
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

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// staticToken adapts a plain string token to account.TokenSource.
type staticToken string

func (t staticToken) TradingToken() string { return string(t) }
