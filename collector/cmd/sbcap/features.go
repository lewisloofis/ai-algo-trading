package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"sort"
	"strconv"
	"time"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/replay"
)

// runFeatures replays one or more captured segments and emits a consolidated
// book+price feature CSV to stdout. One row is written per orderbook update,
// but only once both the BID and OFFER sides have been seen at least once.
//
// Segment paths may appear before, after, or interleaved with flags; all
// non-flag tokens are treated as segment paths (see extractSegmentPaths).
//
// Flags:
//
//	--symbol S        (required) symbol to extract features for
//	--depth N         (default 5) number of price levels to include per side
//	--from HH:MM      (optional) skip events before this WIB wall-clock time
//	--to   HH:MM      (optional) skip events after this WIB wall-clock time
//
// CSV columns:
//
//	recv_ns, iso_wib, bid_seq, offer_seq, last_price, iep,
//	delta_bid_vol, delta_ask_vol,
//	bid_px_0..N-1, bid_vol_0..N-1, ask_px_0..N-1, ask_vol_0..N-1
//	(interleaved per level: bid_px_i, bid_vol_i, ask_px_i, ask_vol_i)
//
// delta_bid_vol and delta_ask_vol are the change in best-bid/ask volume since
// the previous emitted row. They are empty on the first row and after any
// RECONNECT/REAUTH control event (stale state).
//
// Missing depth levels are emitted as empty strings, not zeros.
func runFeatures(args []string) error {
	segs, flagArgs := extractSegmentPaths(args)
	if len(segs) == 0 {
		return fmt.Errorf("usage: sbcap features <seg>... --symbol S [--depth N] [--from HH:MM] [--to HH:MM]")
	}

	fs := flag.NewFlagSet("features", flag.ContinueOnError)
	symbolFlag := fs.String("symbol", "", "symbol to extract features for (required)")
	depthFlag := fs.Int("depth", 5, "number of book levels to include per side")
	fromFlag := fs.String("from", "", "start time HH:MM WIB (inclusive)")
	toFlag := fs.String("to", "", "end time HH:MM WIB (inclusive)")

	if err := fs.Parse(flagArgs); err != nil {
		return err
	}
	if *symbolFlag == "" {
		return fmt.Errorf("--symbol is required")
	}
	depth := *depthFlag
	sym := *symbolFlag

	fromNs, toNs, err := parseTimeRange(*fromFlag, *toFlag)
	if err != nil {
		return err
	}

	// Rolling state: latest BID and OFFER sides, their sequence numbers,
	// the most recent last_price and IEP carried forward, and delta-volume
	// tracking for OFI computation.
	type state struct {
		bid      *feed.BookSide
		offer    *feed.BookSide
		bidSeq   int64
		offerSeq int64

		lastPrice    float64
		hasLastPrice bool

		iep    float64
		hasIEP bool

		// Delta-volume tracking for delta_bid_vol / delta_ask_vol columns.
		prevBidVol float64
		prevAskVol float64
		hasPrev    bool
		dirty      bool // set on RECONNECT/REAUTH; suppresses next delta
	}
	st := &state{}

	w := csv.NewWriter(os.Stdout)
	defer w.Flush()

	// Write header.
	header := buildFeaturesHeader(depth)
	if err := w.Write(header); err != nil {
		return fmt.Errorf("csv write header: %w", err)
	}

	replayErr := replay.Replay(segs, replay.ModeFast, func(evt replay.MarketEvent) {
		// Time window filter.
		if fromNs > 0 && evt.RecvNs < fromNs {
			return
		}
		if toNs > 0 && evt.RecvNs > toNs {
			return
		}

		// Symbol filter for data events.
		if evt.Symbol != sym && evt.Kind != replay.EventControl {
			return
		}

		switch evt.Kind {
		case replay.EventControl:
			st.dirty = true

		case replay.EventLivePrice:
			if evt.Decoded != nil {
				st.lastPrice = evt.Decoded.LastPrice
				st.hasLastPrice = true
			}

		case replay.EventIEPIEV:
			if evt.Decoded != nil {
				st.iep = evt.Decoded.IEP
				st.hasIEP = true
			}

		case replay.EventOrderbook:
			if evt.Decoded == nil || evt.Decoded.Body == "" {
				return
			}
			bs, ok := feed.ParseOrderbookBody(evt.Decoded.Body)
			if !ok {
				return
			}
			seq := evt.Decoded.SequenceNumber
			bsCopy := bs // avoid aliasing the loop variable
			switch bs.Side {
			case "BID":
				st.bid = &bsCopy
				st.bidSeq = seq
			case "OFFER":
				st.offer = &bsCopy
				st.offerSeq = seq
			}

			// Emit only once both sides have been seen.
			if st.bid == nil || st.offer == nil {
				return
			}

			// Compute delta columns using top-of-book volumes.
			bidSorted := sortedLevels(st.bid.Levels, true)
			askSorted := sortedLevels(st.offer.Levels, false)
			var curBidVol, curAskVol float64
			if len(bidSorted) > 0 {
				curBidVol = bidSorted[0].Volume
			}
			if len(askSorted) > 0 {
				curAskVol = askSorted[0].Volume
			}
			var deltaBidVol, deltaAskVol *float64
			if st.hasPrev && !st.dirty {
				d1 := curBidVol - st.prevBidVol
				d2 := curAskVol - st.prevAskVol
				deltaBidVol = &d1
				deltaAskVol = &d2
			}
			st.prevBidVol = curBidVol
			st.prevAskVol = curAskVol
			st.hasPrev = true
			st.dirty = false

			row := assembleRow(evt.RecvNs, st.bid, st.offer, st.bidSeq, st.offerSeq,
				st.lastPrice, st.hasLastPrice, st.iep, st.hasIEP, depth,
				deltaBidVol, deltaAskVol)
			_ = w.Write(row) // flush deferred; errors surface on Flush
		}
	})
	if replayErr != nil {
		return fmt.Errorf("replay: %w", replayErr)
	}
	w.Flush()
	return w.Error()
}

// buildFeaturesHeader returns the CSV header row for the given depth.
func buildFeaturesHeader(depth int) []string {
	cols := []string{
		"recv_ns", "iso_wib", "bid_seq", "offer_seq", "last_price", "iep",
		"delta_bid_vol", "delta_ask_vol",
	}
	for i := 0; i < depth; i++ {
		cols = append(cols,
			fmt.Sprintf("bid_px_%d", i),
			fmt.Sprintf("bid_vol_%d", i),
			fmt.Sprintf("ask_px_%d", i),
			fmt.Sprintf("ask_vol_%d", i),
		)
	}
	return cols
}

// assembleRow builds one CSV data row from the current book state and carried
// prices. BID levels are sorted descending (best bid first); OFFER levels are
// sorted ascending (best offer first). Missing depth levels are empty strings.
// deltaBidVol and deltaAskVol are nil on the first row and after a control
// event (dirty state), in which case the columns are emitted as empty strings.
func assembleRow(
	recvNs int64,
	bid, offer *feed.BookSide,
	bidSeq, offerSeq int64,
	lastPrice float64, hasLastPrice bool,
	iep float64, hasIEP bool,
	depth int,
	deltaBidVol, deltaAskVol *float64,
) []string {
	isoWIB := time.Unix(0, recvNs).In(wib).Format("2006-01-02T15:04:05.000")

	// Sort a copy of each side so we don't mutate the cached state.
	bidLevels := sortedLevels(bid.Levels, true)    // descending
	askLevels := sortedLevels(offer.Levels, false) // ascending

	lpStr := ""
	if hasLastPrice {
		lpStr = strconv.FormatFloat(lastPrice, 'f', -1, 64)
	}
	iepStr := ""
	if hasIEP {
		iepStr = strconv.FormatFloat(iep, 'f', -1, 64)
	}
	fmtOpt := func(v *float64) string {
		if v == nil {
			return ""
		}
		return strconv.FormatFloat(*v, 'f', -1, 64)
	}

	row := []string{
		strconv.FormatInt(recvNs, 10),
		isoWIB,
		strconv.FormatInt(bidSeq, 10),
		strconv.FormatInt(offerSeq, 10),
		lpStr,
		iepStr,
		fmtOpt(deltaBidVol),
		fmtOpt(deltaAskVol),
	}

	for i := 0; i < depth; i++ {
		row = append(row, levelCell(bidLevels, i, true), levelCell(bidLevels, i, false))
		row = append(row, levelCell(askLevels, i, true), levelCell(askLevels, i, false))
	}
	return row
}

// sortedLevels returns a copy of levels sorted by price. descending=true gives
// BID order (best bid first); descending=false gives OFFER order (best ask first).
func sortedLevels(levels []feed.Level, descending bool) []feed.Level {
	out := make([]feed.Level, len(levels))
	copy(out, levels)
	if descending {
		sort.Slice(out, func(i, j int) bool { return out[i].Price > out[j].Price })
	} else {
		sort.Slice(out, func(i, j int) bool { return out[i].Price < out[j].Price })
	}
	return out
}

// levelCell returns the price (isPx=true) or volume (isPx=false) for levels[i]
// as a formatted string, or "" if i is out of range.
func levelCell(levels []feed.Level, i int, isPx bool) string {
	if i >= len(levels) {
		return ""
	}
	v := levels[i].Volume
	if isPx {
		v = levels[i].Price
	}
	return strconv.FormatFloat(v, 'f', -1, 64)
}

// extractSegmentPaths separates non-flag tokens (segment paths) from flag
// tokens in args. Flags and their values are collected into flagArgs; all
// other tokens become segment paths.
//
// This mirrors the spirit of dump.go's extractPositionalArg but collects
// all positional tokens rather than just one, so flags and paths may appear
// in any order:
//
//	sbcap features seg1 seg2 --symbol BUMI
//	sbcap features --symbol BUMI seg1 seg2
func extractSegmentPaths(args []string) (segs, flagArgs []string) {
	skipNext := false
	for _, a := range args {
		if skipNext {
			// This token is the value of the previous flag.
			flagArgs = append(flagArgs, a)
			skipNext = false
			continue
		}
		if len(a) > 0 && a[0] == '-' {
			flagArgs = append(flagArgs, a)
			// A flag without an inline "=" value consumes the next token.
			if !containsEq(a) {
				skipNext = true
			}
			continue
		}
		segs = append(segs, a)
	}
	return segs, flagArgs
}

// containsEq reports whether s contains an '=' byte (flag written as --key=val).
func containsEq(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] == '=' {
			return true
		}
	}
	return false
}
