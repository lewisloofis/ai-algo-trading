// Command strategy-run replays market-data segments (or, with --source live,
// streams a live feed) through a strategy and a paper fill simulator,
// recording the run into an append-only SQLite store.
//
// Live order routing is not implemented: only --sink paper exists, in both
// --source modes. --source live only changes where MarketEvents come from.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"collector/calendar"
	"collector/feed"
	"strategy/engine"
	"strategy/strategies/orb"
)

func main() {
	source := flag.String("source", "replay", "event source: replay|live")
	segmentsGlob := flag.String("segments", "", "glob of .binpb.zst segment files (required for --source replay)")
	symbolsFlag := flag.String("symbols", "", "comma-separated symbol watchlist (required for --source live)")
	strategyName := flag.String("strategy", "orb", "strategy id (orb)")
	sinkName := flag.String("sink", "paper", "order sink (paper)")
	fillMode := flag.String("fill-mode", "taker", "paper fill mode: taker|mid")
	storePath := flag.String("store", "", "path to the SQLite post-trade store (required)")
	pacing := flag.String("pacing", "fast", "pacing mode: fast|paced (--source replay only)")
	flag.Parse()

	if *storePath == "" {
		flag.Usage()
		os.Exit(2)
	}

	strat, err := newStrategy(*strategyName)
	if err != nil {
		log.Fatal(err)
	}

	store, err := engine.OpenStore(*storePath)
	if err != nil {
		log.Fatalf("open store: %v", err)
	}
	defer store.Close()

	sink, err := newSink(*sinkName, *fillMode)
	if err != nil {
		log.Fatal(err)
	}
	ctx := context.Background()

	src, err := newSource(ctx, *source, *segmentsGlob, *symbolsFlag, *pacing)
	if err != nil {
		if errors.Is(err, engine.ErrMarketClosed) {
			// Not a failure: the market is simply not open today. Exit 0 so
			// a scheduler does not mistake "closed today" for a crash (see
			// engine.ErrMarketClosed's doc — this is the same distinction
			// plan 14 §1 requires of the collector).
			fmt.Println("strategy-run: market is closed today — not starting a live run")
			return
		}
		log.Fatal(err)
	}

	counters, err := engine.Run(ctx, src, strat, sink, store, *strategyName)
	if err != nil {
		log.Fatalf("run: %v", err)
	}

	fmt.Printf("Trades attempted: %d | Fills: %d | Net PnL bps: %.2f\n",
		counters.OrdersAttempted, counters.Fills, counters.NetPnlBps)
	fmt.Printf("Round-trips: %d | Total fees: %d IDR\n", counters.RoundTrips, counters.TotalFeesIDR)
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

func newStrategy(name string) (engine.Strategy, error) {
	switch name {
	case "orb":
		return orb.New(), nil
	default:
		return nil, fmt.Errorf("unknown strategy %q", name)
	}
}

func newSink(sinkName, fillMode string) (engine.OrderSink, error) {
	if sinkName != "paper" {
		return nil, fmt.Errorf("unsupported sink %q: only 'paper' is implemented", sinkName)
	}
	switch fillMode {
	case "taker":
		return engine.NewPaperSink(), nil
	case "mid":
		return engine.NewMidPriceSink(), nil
	default:
		return nil, fmt.Errorf("unknown fill-mode %q: want taker|mid", fillMode)
	}
}
