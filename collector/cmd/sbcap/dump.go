package main

import (
	"flag"
	"fmt"
	"strings"
	"time"

	"collector/feed"
	"collector/replay"
)

// runDump decodes and prints events one per line with optional filters.
//
// Flags:
//
//	--symbol S              only events for symbol S
//	--channel trades|orderbook|liveprice
//	--from HH:MM[:SS]       skip events before this WIB wall-clock time
//	--to   HH:MM[:SS]       skip events after this WIB wall-clock time
//
// Flags may appear before or after the segment path.
func runDump(args []string) error {
	fs := flag.NewFlagSet("dump", flag.ContinueOnError)
	symbolFlag := fs.String("symbol", "", "filter by symbol")
	channelFlag := fs.String("channel", "", "filter by channel: trades|orderbook|liveprice")
	fromFlag := fs.String("from", "", "start time HH:MM[:SS] WIB (inclusive)")
	toFlag := fs.String("to", "", "end time HH:MM[:SS] WIB (inclusive)")

	segPath, flagArgs, err := extractPositionalArg(args)
	if err != nil {
		return fmt.Errorf("usage: sbcap dump <segment> [--symbol S] [--channel C] [--from HH:MM] [--to HH:MM]")
	}
	if err := fs.Parse(flagArgs); err != nil {
		return err
	}

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

// extractPositionalArg separates the first non-flag token from args, returning
// it as the positional argument and the remaining tokens as flag args.
//
// This lets callers place flags before or after the positional argument:
//
//	sbcap dump   <path> --symbol X      (path first)
//	sbcap export --format ndjson <path> (flags first)
//
// An error is returned when no non-flag token is found.
func extractPositionalArg(args []string) (positional string, rest []string, err error) {
	skipNext := false
	for i, a := range args {
		if skipNext {
			skipNext = false
			continue
		}
		if strings.HasPrefix(a, "-") {
			// Flag token: if it carries no inline value (no "="), the next token
			// is its value — skip it so we don't confuse it for the positional arg.
			if !strings.Contains(a, "=") {
				skipNext = true
			}
			continue
		}
		// First non-flag, non-value token is the positional argument.
		rest = append(rest, args[:i]...)
		rest = append(rest, args[i+1:]...)
		return a, rest, nil
	}
	return "", nil, fmt.Errorf("no segment path provided")
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
	case "iepiev":
		return replay.EventIEPIEV
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
		price := 0.0
		vol := 0.0
		side := ""
		tradeNum := int32(0)
		if evt.Decoded != nil && len(evt.Decoded.Trades) > 0 {
			tr := evt.Decoded.Trades[0]
			price = tr.Price
			vol = tr.Volume
			side = tr.Side
			tradeNum = tr.TradeNumber
		}
		fmt.Printf("%s  TRADE  %-8s  price=%-8.0f  vol=%-8.0f  side=%-2s  trade#=%d\n",
			timeStr, evt.Symbol, price, vol, side, tradeNum)

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

	case replay.EventIEPIEV:
		iep, iev, chg, pct := 0.0, 0.0, 0.0, 0.0
		seq := int64(0)
		dt := ""
		if evt.Decoded != nil {
			iep = evt.Decoded.IEP
			iev = evt.Decoded.IEV
			chg = evt.Decoded.IEPChange
			pct = evt.Decoded.IEPChangePct
			seq = evt.Decoded.SequenceNumber
			dt = evt.Decoded.Datetime
		}
		fmt.Printf("%s  IEPIEV %-8s  iep=%.0f  iev=%.0f  chg=%+.0f  pct=%+.4f  seq=%d  dt=%s\n",
			timeStr, evt.Symbol, iep, iev, chg, pct, seq, dt)

	default:
		// "ping", "error", "other" — print minimally.
		fmt.Printf("%s  %-6s %-8s\n", timeStr, strings.ToUpper(string(evt.Kind)), evt.Symbol)
	}
}
