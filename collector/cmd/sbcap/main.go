// Command sbcap is a read-only CLI for inspecting market-data segment files.
//
// Usage:
//
//	sbcap <subcommand> <segment-path> [flags]
//
// Subcommands:
//
//	stat     Print a health report for a segment file.
//	dump     Print decoded events, one per line, with optional filters.
//	export   Emit decoded events as NDJSON or CSV.
//	book     Reconstruct the order book at a given point in time.
//	features Emit a consolidated book+price feature CSV for study use.
//	trades   Emit a per-trade CSV (continuous-session, side-tagged only).
//	bars     Emit an OHLCV bar CSV on a fixed interval (chartbit-comparable).
//
// Segment paths may be uncompressed (.binpb) or zstd-compressed (.binpb.zst).
// Both local paths and S3 paths (s3://) are accepted in principle; S3 support
// is deferred to a later phase.
//
// Reference: docs/plans/02-storage-format-and-replay.md §7.1
package main

import (
	"fmt"
	"os"
)

func main() {
	if len(os.Args) < 3 {
		usage()
		os.Exit(1)
	}

	subcommand := os.Args[1]
	// Remaining args starting at os.Args[2] are passed to each subcommand's
	// Run function; the segment path is always os.Args[2].

	switch subcommand {
	case "stat":
		if err := runStat(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap stat: %v\n", err)
			os.Exit(1)
		}
	case "dump":
		if err := runDump(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap dump: %v\n", err)
			os.Exit(1)
		}
	case "export":
		if err := runExport(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap export: %v\n", err)
			os.Exit(1)
		}
	case "book":
		if err := runBook(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap book: %v\n", err)
			os.Exit(1)
		}
	case "features":
		if err := runFeatures(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap features: %v\n", err)
			os.Exit(1)
		}
	case "trades":
		if err := runTrades(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap trades: %v\n", err)
			os.Exit(1)
		}
	case "bars":
		if err := runBars(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "sbcap bars: %v\n", err)
			os.Exit(1)
		}
	default:
		fmt.Fprintf(os.Stderr, "sbcap: unknown subcommand %q\n\n", subcommand)
		usage()
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `sbcap — market-data segment inspector

Usage:
  sbcap stat     <seg>
  sbcap dump     <seg> [--symbol S] [--channel trades|orderbook|liveprice] [--from HH:MM] [--to HH:MM]
  sbcap export   <seg> --format ndjson|csv
  sbcap book     <seg> --symbol S --at HH:MM:SS
  sbcap features <seg>... --symbol S [--depth N] [--from HH:MM] [--to HH:MM]
  sbcap trades   <seg>... --symbol S [--from HH:MM] [--to HH:MM]
  sbcap bars     <seg>... --symbol S [--interval 1m] [--from HH:MM] [--to HH:MM] [--tagged-only]

Segment files: .binpb (raw) or .binpb.zst (zstd-compressed)`)
}
