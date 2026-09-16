// Command market-probe prints what strategy/market's Gate would answer,
// right now, for one or more symbols — the hand verification step for plan
// 18 §5 B2's reference-06 gate, in the same spirit as cmd/account-recon.
//
// It sends no orders and needs no sleeve: every call is a GET.
//
//	SB_ACCESS_TOKEN=…  SB_TRADING_ACCESS_TOKEN=…  \
//	  market-probe --symbols BBCA,BBRI --price 1000
//
// Exit codes distinguish the two things an operator cares about:
//
//	0  every source answered (even if the answer is "deny — market closed")
//	1  at least one source could not be reached or could not be parsed
//	2  usage or configuration error
//
// A deny is not a failure. Running this at 22:00 WIB should print a clean
// market_open=deny and exit 0; that is the gate working. Exit 1 means the
// gate would be blind, which is the condition worth alerting on, because a
// blind gate denies every order and looks exactly like "no signal today".
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"collector/calendar"
	"strategy/market"
)

func main() {
	symbols := flag.String("symbols", "", "comma-separated symbols to probe (required)")
	price := flag.Int64("price", 0, "order price in IDR to test against the ARA/ARB band (required)")
	showParsed := flag.Bool("parsed", false, "also print the parsed endpoint payloads, to verify the wire contract")
	timeout := flag.Duration("timeout", 30*time.Second, "overall timeout")
	flag.Parse()

	if *symbols == "" || *price <= 0 {
		flag.Usage()
		os.Exit(2)
	}

	accessToken := os.Getenv("SB_ACCESS_TOKEN")
	tradingToken := os.Getenv("SB_TRADING_ACCESS_TOKEN")
	if accessToken == "" || tradingToken == "" {
		fmt.Fprintln(os.Stderr, "market-probe: both SB_ACCESS_TOKEN (main session, Exodus) and SB_TRADING_ACCESS_TOKEN (trading session, Carina) are required")
		fmt.Fprintln(os.Stderr, "  the two are different sessions — see docs/reference/README.md")
		os.Exit(2)
	}

	client, err := market.NewClient(
		market.StaticTokens{Access: accessToken, Trading: tradingToken},
		market.ClientOptions{
			ExodusHost: os.Getenv("SB_EXODUS_HOST"),
			CarinaHost: os.Getenv("SB_CARINA_HOST"),
		},
	)
	if err != nil {
		fmt.Fprintln(os.Stderr, "market-probe:", err)
		os.Exit(2)
	}

	cal, err := calendar.Load()
	if err != nil {
		fmt.Fprintln(os.Stderr, "market-probe: calendar:", err)
		os.Exit(2)
	}

	gate, err := market.NewGate(cal, client, market.GateOptions{})
	if err != nil {
		fmt.Fprintln(os.Stderr, "market-probe:", err)
		os.Exit(2)
	}

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	fmt.Printf("market-probe — reference 06 pre-trade gate\nrun at %s\n", time.Now().Format(time.RFC3339))

	blind := false
	for _, symbol := range splitSymbols(*symbols) {
		d := gate.Detail(ctx, symbol, *price)
		fmt.Printf("\n%s @ %d IDR\n", symbol, *price)
		for _, f := range d.Fields {
			fmt.Printf("  %s\n", f)
		}
		verdict := "DENY — an order would be rejected by the risk layer"
		if d.Allowed() {
			verdict = "ALLOW — an order would pass the market gate"
		}
		fmt.Printf("  => %s\n", verdict)
		if err := d.Err(); err != nil {
			blind = true
			fmt.Printf("  !! unresolved: %v\n", err)
		}
		if *showParsed {
			printParsed(ctx, client, symbol)
		}
	}

	if blind {
		fmt.Println("\nat least one source could not be consulted — the gate is blind, and a blind gate denies everything")
		os.Exit(1)
	}
}

// printParsed dumps what each endpoint actually parsed into. It is the
// contract check: a field the API renamed shows up here as a zero value
// (is_tradable=false, a 0/0 band, STATE_NAME_UNSPECIFIED) rather than as a
// mysterious deny.
func printParsed(ctx context.Context, c *market.Client, symbol string) {
	fmt.Println("  parsed payloads:")
	dump := func(label string, v any, err error) {
		if err != nil {
			fmt.Printf("    %-14s ERROR %v\n", label, err)
			return
		}
		b, _ := json.Marshal(v)
		fmt.Printf("    %-14s %s\n", label, b)
	}
	mt, err := c.MarketTime(ctx)
	dump("market-time", mt, err)
	sd, err := c.SessionDetail(ctx)
	dump("session", sd, err)
	row, err := c.Tradable(ctx, symbol)
	dump("tradable", row, err)
	band, err := c.AutoRejectPrice(ctx, symbol)
	dump("auto-reject", band, err)
}

func splitSymbols(csv string) []string {
	parts := strings.Split(csv, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if s := strings.ToUpper(strings.TrimSpace(p)); s != "" {
			out = append(out, s)
		}
	}
	return out
}
