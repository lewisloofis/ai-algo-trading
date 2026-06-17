package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"sb-algo-prototype/internal/feed"
	"sb-algo-prototype/internal/replay"
)

// runBook reconstructs the order book for a symbol at a given point in time.
//
// Flags:
//
//	--symbol S        (required) symbol to reconstruct
//	--at HH:MM:SS     (required) WIB wall-clock time to stop replay
//	--json            emit all levels as JSON instead of the text ladder
//
// BID and OFFER arrive as separate orderbook messages. This subcommand caches
// the latest BID message and the latest OFFER message per symbol and prints
// the assembled ladder at the requested time.
func runBook(args []string) error {
	fs := flag.NewFlagSet("book", flag.ContinueOnError)
	symbolFlag := fs.String("symbol", "", "symbol to reconstruct (required)")
	atFlag := fs.String("at", "", "stop time HH:MM:SS WIB (required)")
	jsonFlag := fs.Bool("json", false, "emit all levels as JSON")

	if err := fs.Parse(args); err != nil {
		return err
	}
	if fs.NArg() < 1 {
		return fmt.Errorf("usage: sbcap book --symbol S --at HH:MM:SS [--json] <segment>")
	}
	segPath := fs.Arg(0)

	if *symbolFlag == "" {
		return fmt.Errorf("--symbol is required")
	}
	if *atFlag == "" {
		return fmt.Errorf("--at is required")
	}

	atNs, err := parseBookAt(*atFlag)
	if err != nil {
		return err
	}

	// Replay up to atNs, keeping the latest BID and OFFER BookSide per symbol.
	type sides struct {
		bid   *feed.BookSide
		offer *feed.BookSide
		// sequence numbers of the last bid/offer seen
		bidSeq   int64
		offerSeq int64
	}

	book := &sides{}
	sym := *symbolFlag

	replayErr := replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		if evt.RecvNs > atNs {
			return
		}
		if evt.Kind != replay.EventOrderbook {
			return
		}
		if evt.Symbol != sym {
			return
		}
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
			book.bid = &bsCopy
			book.bidSeq = seq
		case "OFFER":
			book.offer = &bsCopy
			book.offerSeq = seq
		}
	})
	if replayErr != nil {
		return fmt.Errorf("replay: %w", replayErr)
	}

	if book.bid == nil && book.offer == nil {
		return fmt.Errorf("no orderbook data found for %s at or before %s", sym, *atFlag)
	}

	if *jsonFlag {
		return printBookJSON(sym, *atFlag, atNs, book.bid, book.offer, book.bidSeq, book.offerSeq)
	}
	printBook(sym, *atFlag, book.bid, book.offer, book.bidSeq, book.offerSeq)
	return nil
}

// parseBookAt parses --at as a WIB HH:MM:SS offset into today's date.
func parseBookAt(s string) (int64, error) {
	now := time.Now().In(wib)
	date := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, wib)

	layouts := []string{"15:04:05", "15:04"}
	for _, layout := range layouts {
		if t, err := time.Parse(layout, s); err == nil {
			resolved := date.Add(
				time.Duration(t.Hour())*time.Hour +
					time.Duration(t.Minute())*time.Minute +
					time.Duration(t.Second())*time.Second,
			)
			return resolved.UnixNano(), nil
		}
	}
	return 0, fmt.Errorf("invalid --at time %q: expected HH:MM:SS or HH:MM", s)
}

// levelJSON is the per-level shape used in the JSON output.
type levelJSON struct {
	Price  float64 `json:"price"`
	Volume float64 `json:"volume"`
}

// bookJSON is the top-level shape of the --json output.
type bookJSON struct {
	Symbol   string      `json:"symbol"`
	At       string      `json:"at"`
	AtWIB    string      `json:"at_wib"`
	BidSeq   int64       `json:"bid_seq"`
	OfferSeq int64       `json:"offer_seq"`
	Bid      []levelJSON `json:"bid"`
	Offer    []levelJSON `json:"offer"`
}

// printBookJSON marshals and writes the full order book as a single JSON line.
func printBookJSON(symbol, at string, atNs int64, bid, offer *feed.BookSide, bidSeq, offerSeq int64) error {
	toLevels := func(bs *feed.BookSide) []levelJSON {
		out := make([]levelJSON, 0)
		if bs == nil {
			return out
		}
		for _, l := range bs.Levels {
			out = append(out, levelJSON{Price: l.Price, Volume: l.Volume})
		}
		return out
	}

	bidLevels := toLevels(bid)
	offerLevels := toLevels(offer)

	// BID descending (best bid first), OFFER ascending (best offer first).
	sort.Slice(bidLevels, func(i, j int) bool { return bidLevels[i].Price > bidLevels[j].Price })
	sort.Slice(offerLevels, func(i, j int) bool { return offerLevels[i].Price < offerLevels[j].Price })

	out := bookJSON{
		Symbol:   symbol,
		At:       at,
		AtWIB:    time.Unix(0, atNs).In(wib).Format("2006-01-02 15:04:05 WIB"),
		BidSeq:   bidSeq,
		OfferSeq: offerSeq,
		Bid:      bidLevels,
		Offer:    offerLevels,
	}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		return fmt.Errorf("json encode: %w", err)
	}
	return nil
}

// printBook prints the assembled BID/OFFER ladder in the format from §7.1.
func printBook(symbol, at string, bid, offer *feed.BookSide, bidSeq, offerSeq int64) {
	fmt.Printf("Order book: %s  @%s WIB  (bid seq %d / offer seq %d)\n\n",
		symbol, at, bidSeq, offerSeq)

	// Determine the maximum number of levels to display.
	bidLevels := 0
	if bid != nil {
		bidLevels = len(bid.Levels)
	}
	offerLevels := 0
	if offer != nil {
		offerLevels = len(offer.Levels)
	}
	maxLevels := bidLevels
	if offerLevels > maxLevels {
		maxLevels = offerLevels
	}
	if maxLevels > 10 {
		maxLevels = 10
	}
	if maxLevels == 0 {
		fmt.Println("(no levels)")
		return
	}

	// Gather all prices from both sides for alignment.
	bidPrices := make([]float64, 0, bidLevels)
	bidVols := make([]float64, 0, bidLevels)
	if bid != nil {
		for _, l := range bid.Levels {
			bidPrices = append(bidPrices, l.Price)
			bidVols = append(bidVols, l.Volume)
		}
	}
	offerPrices := make([]float64, 0, offerLevels)
	offerVols := make([]float64, 0, offerLevels)
	if offer != nil {
		for _, l := range offer.Levels {
			offerPrices = append(offerPrices, l.Price)
			offerVols = append(offerVols, l.Volume)
		}
	}

	// Sort BID descending (best bid first), OFFER ascending (best offer first).
	sort.Slice(bidPrices, func(i, j int) bool { return bidPrices[i] > bidPrices[j] })
	sort.Slice(offerPrices, func(i, j int) bool { return offerPrices[i] < offerPrices[j] })

	// Header.
	fmt.Printf("%-5s  %-10s  %-10s    %-10s  %-10s\n",
		"Level", "BID Price", "BID Vol", "OFFER Price", "OFFER Vol")
	fmt.Println(strings.Repeat("-", 60))

	for i := 0; i < maxLevels; i++ {
		bidPart := ""
		offerPart := ""

		if i < len(bidPrices) {
			vol := 0.0
			if i < len(bidVols) {
				vol = bidVols[i]
			}
			bidPart = fmt.Sprintf("%-10.0f  %-10.0f", bidPrices[i], vol)
		} else {
			bidPart = fmt.Sprintf("%-10s  %-10s", "-", "-")
		}

		if i < len(offerPrices) {
			vol := 0.0
			if i < len(offerVols) {
				vol = offerVols[i]
			}
			offerPart = fmt.Sprintf("%-10.0f  %-10.0f", offerPrices[i], vol)
		} else {
			offerPart = fmt.Sprintf("%-10s  %-10s", "-", "-")
		}

		fmt.Printf("%-5d  %s    %s\n", i, bidPart, offerPart)
	}
}
