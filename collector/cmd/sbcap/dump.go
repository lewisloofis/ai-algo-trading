package main

import (
	"flag"
	"fmt"
	"strings"
	"time"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/replay"
)

// runDump decodes and prints events one per line with optional filters.
//
// Flags:
//
//	--symbol S              only events for symbol S
//	--channel trades|orderbook|liveprice
//	--from HH:MM[:SS]       skip events before this WIB wall-clock time
//	--to   HH:MM[:SS]       skip events after this WIB wall-clock time
func runDump(args []string) error {
	fs := flag.NewFlagSet("dump", flag.ContinueOnError)
	symbolFlag := fs.String("symbol", "", "filter by symbol")
	channelFlag := fs.String("channel", "", "filter by channel: trades|orderbook|liveprice")
	fromFlag := fs.String("from", "", "start time HH:MM[:SS] WIB (inclusive)")
	toFlag := fs.String("to", "", "end time HH:MM[:SS] WIB (inclusive)")

	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() < 1 {
		return fmt.Errorf("usage: sbcap dump <segment> [--symbol S] [--channel C] [--from HH:MM] [--to HH:MM]")
	}
	segPath := fs.Arg(0)

	fromNs, toNs, err := parseTimeRange(*fromFlag, *toFlag)
	if err != nil {
		return err
	}

	wantKind := parseDumpChannel(*channelFlag)

	err = replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		// Time filter.
		if fromNs > 0 && evt.RecvNs < fromNs {
			return
		}
		if toNs > 0 && evt.RecvNs > toNs {
			return
		}

		// Symbol filter (skip control events when symbol filter is active).
		if *symbolFlag != "" && evt.Symbol != *symbolFlag {
			return
		}

		// Channel filter.
		if wantKind != "" && evt.Kind != wantKind {
			return
		}

		// Skip control events in dump output (they have no symbol / decoded data).
		if evt.Kind == replay.EventControl {
			return
		}

		printDumpLine(evt)
	})
	return err
}

// parseDumpChannel converts the --channel flag value to a replay.EventKind.
// An empty flag means "all channels".
func parseDumpChannel(ch string) replay.EventKind {
	switch strings.ToLower(ch) {
	case "trades", "trade", "running_trade":
		return replay.EventRunningTrade
	case "orderbook", "book":
		return replay.EventOrderbook
	case "liveprice", "price", "live":
		return replay.EventLivePrice
	default:
		return "" // no filter
	}
}

// parseTimeRange parses --from and --to flags as WIB HH:MM[:SS] offsets into
// the current WIB day. Returns (0, 0, nil) when both flags are empty.
//
// Because segment files may span multiple WIB calendar days, we resolve the
// time strings against today's date in WIB. For production use, a full
// timestamp would be better — but for Phase 1 inspection of single-day
// segments this is sufficient.
func parseTimeRange(fromStr, toStr string) (fromNs, toNs int64, err error) {
	now := time.Now().In(wib)
	date := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, wib)

	parse := func(s string) (int64, error) {
		if s == "" {
			return 0, nil
		}
		layouts := []string{"15:04:05", "15:04"}
		for _, layout := range layouts {
			if t, e := time.Parse(layout, s); e == nil {
				resolved := date.Add(
					time.Duration(t.Hour())*time.Hour +
						time.Duration(t.Minute())*time.Minute +
						time.Duration(t.Second())*time.Second,
				)
				return resolved.UnixNano(), nil
			}
		}
		return 0, fmt.Errorf("invalid time %q: expected HH:MM or HH:MM:SS", s)
	}

	fromNs, err = parse(fromStr)
	if err != nil {
		return 0, 0, err
	}
	toNs, err = parse(toStr)
	if err != nil {
		return 0, 0, err
	}
	return fromNs, toNs, nil
}

// printDumpLine formats and prints one decoded event to stdout.
func printDumpLine(evt replay.MarketEvent) {
	ts := time.Unix(0, evt.RecvNs).In(wib)
	timeStr := ts.Format("15:04:05.000")

	switch evt.Kind {
	case replay.EventRunningTrade:
		// Running-trade frames carry the parsed body in Decoded.Body; the
		// feed package extracts only symbol and last price from liveprice
		// sub-messages. For running_trade the decoded body is the full batch
		// body — print what we have.
		price := 0.0
		if evt.Decoded != nil {
			price = evt.Decoded.LastPrice
		}
		fmt.Printf("%s  TRADE  %-8s  price=%.0f\n", timeStr, evt.Symbol, price)

	case replay.EventOrderbook:
		if evt.Decoded == nil {
			fmt.Printf("%s  BOOK   %-8s  (no data)\n", timeStr, evt.Symbol)
			return
		}
		bs, ok := feed.ParseOrderbookBody(evt.Decoded.Body)
		if !ok {
			fmt.Printf("%s  BOOK   %-8s  body=%q\n", timeStr, evt.Symbol, evt.Decoded.Body)
			return
		}
		// Format top levels (up to 3).
		var levels strings.Builder
		max := 3
		if len(bs.Levels) < max {
			max = len(bs.Levels)
		}
		for i := 0; i < max; i++ {
			l := bs.Levels[i]
			if i > 0 {
				levels.WriteString("  ")
			}
			fmt.Fprintf(&levels, "%.0f×%.0f", l.Price, l.Volume)
		}
		fmt.Printf("%s  BOOK   %-8s  %-6s  %s\n", timeStr, evt.Symbol, bs.Side, levels.String())

	case replay.EventLivePrice:
		price := 0.0
		if evt.Decoded != nil {
			price = evt.Decoded.LastPrice
		}
		fmt.Printf("%s  PRICE  %-8s  last=%.0f\n", timeStr, evt.Symbol, price)

	default:
		// "ping", "error", "other" — print minimally.
		fmt.Printf("%s  %-6s %-8s\n", timeStr, strings.ToUpper(string(evt.Kind)), evt.Symbol)
	}
}
