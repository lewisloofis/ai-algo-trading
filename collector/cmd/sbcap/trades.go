package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"strconv"
	"time"

	"collector/internal/replay"
)

// runTrades replays one or more captured segments and emits a per-trade CSV
// to stdout. One row is written per trade event with a side tag ("B" or "S").
//
// Pre-opening auction batch trades (side="") are SKIPPED — they carry no
// aggressor-side tag and are not useful for signed-flow analysis.
//
// Segment paths may appear before, after, or interleaved with flags; all
// non-flag tokens are treated as segment paths (see extractSegmentPaths).
//
// Flags:
//
//	--symbol S        (required) symbol to extract trades for
//	--from HH:MM      (optional) skip events before this WIB wall-clock time
//	--to   HH:MM      (optional) skip events after this WIB wall-clock time
//
// CSV columns:
//
//	recv_ns, iso_wib, price, vol, side, trade_num
func runTrades(args []string) error {
	segs, flagArgs := extractSegmentPaths(args)
	if len(segs) == 0 {
		return fmt.Errorf("usage: sbcap trades <seg>... --symbol S [--from HH:MM] [--to HH:MM]")
	}

	fs := flag.NewFlagSet("trades", flag.ContinueOnError)
	symbolFlag := fs.String("symbol", "", "symbol to extract trades for (required)")
	fromFlag := fs.String("from", "", "start time HH:MM WIB (inclusive)")
	toFlag := fs.String("to", "", "end time HH:MM WIB (inclusive)")

	if err := fs.Parse(flagArgs); err != nil {
		return err
	}
	if *symbolFlag == "" {
		return fmt.Errorf("--symbol is required")
	}
	sym := *symbolFlag

	fromNs, toNs, err := parseTimeRange(*fromFlag, *toFlag)
	if err != nil {
		return err
	}

	w := csv.NewWriter(os.Stdout)
	defer w.Flush()

	header := []string{"recv_ns", "iso_wib", "price", "vol", "side", "trade_num"}
	if err := w.Write(header); err != nil {
		return fmt.Errorf("csv write header: %w", err)
	}

	replayErr := replay.Replay(segs, replay.ModeFast, func(evt replay.MarketEvent) {
		if evt.Kind != replay.EventRunningTrade {
			return
		}
		if evt.Symbol != sym {
			return
		}
		if fromNs > 0 && evt.RecvNs < fromNs {
			return
		}
		if toNs > 0 && evt.RecvNs > toNs {
			return
		}
		if evt.Decoded == nil || len(evt.Decoded.Trades) == 0 {
			return
		}
		tr := evt.Decoded.Trades[0]
		if tr.Side == "" {
			return // skip pre-opening batch trades
		}

		isoWIB := time.Unix(0, evt.RecvNs).In(wib).Format("2006-01-02T15:04:05.000")
		row := []string{
			strconv.FormatInt(evt.RecvNs, 10),
			isoWIB,
			strconv.FormatFloat(tr.Price, 'f', -1, 64),
			strconv.FormatFloat(tr.Volume, 'f', -1, 64),
			tr.Side,
			strconv.FormatInt(int64(tr.TradeNumber), 10),
		}
		_ = w.Write(row)
	})
	if replayErr != nil {
		return fmt.Errorf("replay: %w", replayErr)
	}
	w.Flush()
	return w.Error()
}
