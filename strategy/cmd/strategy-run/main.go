// Command strategy-run replays market-data segments through a strategy and a
// paper fill simulator, recording the run into an append-only SQLite store.
//
// Live order routing is not implemented: only --sink paper exists.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"

	"strategy/engine"
	"strategy/strategies/orb"
)

func main() {
	segmentsGlob := flag.String("segments", "", "glob of .binpb.zst segment files (required)")
	strategyName := flag.String("strategy", "orb", "strategy id (orb)")
	sinkName := flag.String("sink", "paper", "order sink (paper)")
	storePath := flag.String("store", "", "path to the SQLite post-trade store (required)")
	pacing := flag.String("pacing", "fast", "pacing mode: fast|paced")
	flag.Parse()

	if *segmentsGlob == "" || *storePath == "" {
		flag.Usage()
		os.Exit(2)
	}
	if *sinkName != "paper" {
		log.Fatalf("unsupported sink %q: only 'paper' is implemented", *sinkName)
	}

	segments, err := globSegments(*segmentsGlob)
	if err != nil {
		log.Fatalf("resolve segments: %v", err)
	}
	if len(segments) == 0 {
		log.Fatalf("no segments matched %q", *segmentsGlob)
	}

	mode, err := parsePacing(*pacing)
	if err != nil {
		log.Fatal(err)
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

	sink := engine.NewPaperSink()
	ctx := context.Background()
	src := engine.ReplaySource(ctx, segments, mode)

	counters, err := engine.Run(ctx, src, strat, sink, store, *strategyName)
	if err != nil {
		log.Fatalf("run: %v", err)
	}

	fmt.Printf("Trades attempted: %d | Fills: %d | Net PnL bps: %.2f\n",
		counters.OrdersAttempted, counters.Fills, counters.NetPnlBps)
	fmt.Printf("Round-trips: %d | Total fees: %d IDR\n", counters.RoundTrips, counters.TotalFeesIDR)
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
