package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"sort"
	"strconv"
	"time"

	"collector/replay"
)

// defaultBarInterval is the bar width used when --interval is not given.
// One minute matches the chartbit intraday grid, so bars built from captured
// ticks can be diffed row-for-row against research/data/intraday/*.parquet.
const defaultBarInterval = time.Minute

// barsCSVHeader is the column order emitted by runBars.
//
// The first nine columns mirror the chartbit intraday schema exactly (minus
// net_foreign, which the tick feed does not carry) so the two substrates are
// directly comparable. The remaining columns are tick-only: signed flow
// (buy/sell) and the untagged split are the reason for capturing ticks at all
// and cannot be recovered from chartbit. Invariant, true for every row:
// volume == buy_vol + sell_vol + untagged_vol.
var barsCSVHeader = []string{
	"datetime", "date", "open", "high", "low", "close", "volume", "value",
	"trades", "buy_vol", "sell_vol", "buy_trades", "sell_trades",
	"untagged_vol", "untagged_trades", "vwap",
}

// bar accumulates one time bucket of trades for a single symbol.
type bar struct {
	startNs        int64
	open           float64
	high           float64
	low            float64
	close          float64
	volume         float64
	value          float64
	trades         int64
	buyVol         float64
	sellVol        float64
	buyTrades      int64
	sellTrades     int64
	untaggedVol    float64
	untaggedTrades int64
}

// add folds one trade into the bar. The first trade seeds open/high/low.
// Every trade counts toward volume/value/trades regardless of side. Side
// "B"/"S" additionally split into the buy/sell buckets; any other side (in
// practice only "", meaning the trade carries no aggressor tag) falls into
// the untagged bucket, so volume == buyVol + sellVol + untaggedVol always
// holds.
func (b *bar) add(price, vol float64, side string) {
	if b.trades == 0 {
		b.open = price
		b.high = price
		b.low = price
	}
	if price > b.high {
		b.high = price
	}
	if price < b.low {
		b.low = price
	}
	b.close = price
	b.volume += vol
	b.value += price * vol
	b.trades++
	switch side {
	case "B":
		b.buyVol += vol
		b.buyTrades++
	case "S":
		b.sellVol += vol
		b.sellTrades++
	default:
		b.untaggedVol += vol
		b.untaggedTrades++
	}
}

// runBars replays one or more captured segments and emits an OHLCV bar CSV to
// stdout for a single symbol. This is the missing edge between captured
// protobuf segments and the research substrate: without it the 53 captured
// sessions are unreachable from plans 10, 15 and 17.
//
// Segment paths may appear before, after, or interleaved with flags; all
// non-flag tokens are treated as segment paths (see extractSegmentPaths).
//
// Flags:
//
//	--symbol S           (required) symbol to build bars for
//	--interval D         (default 1m) bar width, any Go duration (30s, 1m, 5m)
//	--from HH:MM         (optional) skip trades before this WIB wall-clock time
//	--to   HH:MM         (optional) skip trades after this WIB wall-clock time
//	--tagged-only        drop untagged trades (side == ""); see below
//
// Semantics, chosen to match the chartbit intraday grid:
//
//   - Buckets are left-closed, right-open, aligned to the WIB midnight
//     boundary, and labelled by their START time.
//   - Empty buckets are NOT emitted. Chartbit omits them too, so the lunch
//     break and thin periods appear as gaps in both substrates.
//   - Bars are bucketed on collector ARRIVAL time (RecvNs), not exchange
//     trade time — the feed carries no exchange trade timestamp. This is why
//     a handful of boundary trades can land one bar either side of
//     chartbit's placement; a tight cross-substrate diff should expect
//     occasional single-bar drift at bucket edges as a real limitation, not
//     a bug.
//   - Every trade is aggregated by default, tagged or not. Untagged trades
//     (side == "", i.e. not a recognised BUY/SELL TradeType) occur in three
//     distinct windows, confirmed against PTRO on 2026-06-22: pre-opening
//     auction prints (~08:58), scattered mid-session prints — likely
//     negotiated/crossing trades (e.g. 14:43, 15:14-15:39) — and the entire
//     post-trading session (16:00-16:16). The post-trading window alone can
//     exceed 10% of a symbol's daily volume, so silently dropping all
//     untagged trades (the previous behaviour, since --include-auction
//     defaulted to off) understates volume badly. Untagged volume/count is
//     never folded away: it is reported separately in the
//     untagged_vol/untagged_trades columns, and it contributes to volume but
//     to neither buy_vol nor sell_vol.
//   - --tagged-only restores the old side-tagged-only view: untagged trades
//     are dropped entirely, as `sbcap trades` does. Chartbit's continuous
//     session excludes the mid-session untagged prints and has no
//     post-trading window at all, so --tagged-only is the flag to reach for
//     when diffing continuous-session bars against chartbit; leave it off
//     (the default) to make all captured volume reachable.
//   - --from/--to filter on WIB wall-clock TIME OF DAY, not on an absolute
//     instant, so a single invocation spans many days correctly. (This is why
//     bars does not use parseTimeRange, which resolves HH:MM against today.)
//
// Bars are buffered until replay completes and then emitted in time order.
// Replay is already chronological within a segment, but segment paths may be
// passed out of order, and a memory cost of one float64 struct per populated
// minute per day is negligible against the correctness it buys.
func runBars(args []string) error {
	segs, flagArgs := extractSegmentPaths(args)
	if len(segs) == 0 {
		return fmt.Errorf("usage: sbcap bars <seg>... --symbol S [--interval 1m] [--from HH:MM] [--to HH:MM] [--tagged-only]")
	}

	fs := flag.NewFlagSet("bars", flag.ContinueOnError)
	symbolFlag := fs.String("symbol", "", "symbol to build bars for (required)")
	intervalFlag := fs.Duration("interval", defaultBarInterval, "bar width (e.g. 30s, 1m, 5m)")
	fromFlag := fs.String("from", "", "start time-of-day HH:MM WIB (inclusive)")
	toFlag := fs.String("to", "", "end time-of-day HH:MM WIB (inclusive)")
	taggedOnlyFlag := fs.Bool("tagged-only", false, "drop untagged trades (side unset); matches chartbit's continuous-session view")

	if err := fs.Parse(flagArgs); err != nil {
		return err
	}
	if *symbolFlag == "" {
		return fmt.Errorf("--symbol is required")
	}
	if *intervalFlag <= 0 {
		return fmt.Errorf("--interval must be positive, got %s", *intervalFlag)
	}
	sym := *symbolFlag
	interval := int64(*intervalFlag)

	fromSec, err := parseDaySeconds(*fromFlag)
	if err != nil {
		return fmt.Errorf("--from: %w", err)
	}
	toSec, err := parseDaySeconds(*toFlag)
	if err != nil {
		return fmt.Errorf("--to: %w", err)
	}

	bars := make(map[int64]*bar)

	replayErr := replay.Replay(segs, replay.ModeFast, func(evt replay.MarketEvent) {
		if evt.Kind != replay.EventRunningTrade || evt.Symbol != sym {
			return
		}
		if evt.Decoded == nil || len(evt.Decoded.Trades) == 0 {
			return
		}
		tr := evt.Decoded.Trades[0]
		if tr.Side == "" && *taggedOnlyFlag {
			return
		}

		ts := time.Unix(0, evt.RecvNs).In(wib)
		if fromSec >= 0 || toSec >= 0 {
			sec := int64(ts.Hour()*3600 + ts.Minute()*60 + ts.Second())
			if fromSec >= 0 && sec < fromSec {
				return
			}
			if toSec >= 0 && sec > toSec {
				return
			}
		}

		// Align to the WIB midnight boundary rather than the Unix epoch, so a
		// bucket never straddles a session for intervals that do not divide
		// the day evenly.
		midnight := time.Date(ts.Year(), ts.Month(), ts.Day(), 0, 0, 0, 0, wib).UnixNano()
		startNs := midnight + ((evt.RecvNs-midnight)/interval)*interval

		b := bars[startNs]
		if b == nil {
			b = &bar{startNs: startNs}
			bars[startNs] = b
		}
		b.add(tr.Price, tr.Volume, tr.Side)
	})
	if replayErr != nil {
		return fmt.Errorf("replay: %w", replayErr)
	}

	starts := make([]int64, 0, len(bars))
	for k := range bars {
		starts = append(starts, k)
	}
	sort.Slice(starts, func(i, j int) bool { return starts[i] < starts[j] })

	w := csv.NewWriter(os.Stdout)
	defer w.Flush()
	if err := w.Write(barsCSVHeader); err != nil {
		return fmt.Errorf("csv write header: %w", err)
	}
	for _, s := range starts {
		if err := w.Write(bars[s].row()); err != nil {
			return fmt.Errorf("csv write row: %w", err)
		}
	}
	w.Flush()
	return w.Error()
}

// row renders the bar as CSV fields in barsCSVHeader order.
func (b *bar) row() []string {
	ts := time.Unix(0, b.startNs).In(wib)
	vwap := 0.0
	if b.volume > 0 {
		vwap = b.value / b.volume
	}
	f := func(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }
	return []string{
		ts.Format("2006-01-02T15:04:05"),
		ts.Format("2006-01-02"),
		f(b.open), f(b.high), f(b.low), f(b.close),
		f(b.volume), f(b.value),
		strconv.FormatInt(b.trades, 10),
		f(b.buyVol), f(b.sellVol),
		strconv.FormatInt(b.buyTrades, 10),
		strconv.FormatInt(b.sellTrades, 10),
		f(b.untaggedVol),
		strconv.FormatInt(b.untaggedTrades, 10),
		strconv.FormatFloat(vwap, 'f', 4, 64),
	}
}

// parseDaySeconds converts an "HH:MM" or "HH:MM:SS" wall-clock string to
// seconds since WIB midnight. An empty string yields -1, meaning "no bound".
//
// Unlike parseTimeRange this carries no date, so the bound applies to every
// day in a multi-day replay.
func parseDaySeconds(s string) (int64, error) {
	if s == "" {
		return -1, nil
	}
	for _, layout := range []string{"15:04:05", "15:04"} {
		if t, err := time.Parse(layout, s); err == nil {
			return int64(t.Hour()*3600 + t.Minute()*60 + t.Second()), nil
		}
	}
	return 0, fmt.Errorf("invalid time %q: expected HH:MM or HH:MM:SS", s)
}
