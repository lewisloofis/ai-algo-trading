package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"collector/replay"
)

// seqcheck.go implements `sbcap seqcheck`: measured per-symbol market-data
// frame loss, per docs/plans/12-data-gap-backlog-and-demand-driven-capture.md
// §5d ("Measured frame loss"). Before this subcommand existed, the analysis
// was an ad-hoc pass over `sbcap export --format ndjson`; this makes it a
// routine, repeatable check.
//
// Method (fixed by §5d — do not tune it to chase a particular number):
//
//	sequence_number is a per-symbol counter, but it is a single space SHARED
//	between order-book and running-trade messages. The collector populates
//	it only on book records (collector/feed/codec.go), so most of the
//	apparent gap in the book sequence is just the interleaved trades.
//	Per symbol, over the analysed window:
//
//	  seq_span    = max(book sequence) - min(book sequence) + 1
//	  accounted   = (distinct book sequence numbers seen) + (running-trade records)
//	  unexplained = seq_span - accounted
//	  loss_pct    = unexplained / seq_span * 100
//
//	unexplained can be slightly negative (the shared-space model very
//	slightly over-accounts); it is reported as-is, never clamped to zero.

// seqSymbolStats accumulates the raw counters needed to compute one symbol's
// measured frame loss: the set of distinct book sequence numbers observed
// (a set, not a count, because a duplicate or replayed sequence number must
// not be counted twice), the observed sequence range, and the running-trade
// record count.
type seqSymbolStats struct {
	bookSeqsSeen map[int64]struct{}
	seqMin       int64
	seqMax       int64
	haveSeq      bool // true once at least one book sequence number has been observed
	trades       int
}

func newSeqSymbolStats() *seqSymbolStats {
	return &seqSymbolStats{bookSeqsSeen: make(map[int64]struct{})}
}

// observeBook folds one order-book sequence number into the accumulator.
func (s *seqSymbolStats) observeBook(seq int64) {
	s.bookSeqsSeen[seq] = struct{}{}
	if !s.haveSeq || seq < s.seqMin {
		s.seqMin = seq
	}
	if !s.haveSeq || seq > s.seqMax {
		s.seqMax = seq
	}
	s.haveSeq = true
}

// observeTrade folds one running-trade record into the accumulator.
func (s *seqSymbolStats) observeTrade() {
	s.trades++
}

// loss computes seq_span, unexplained and loss_pct per §5d. ok is false (and
// the other results are zero) when no book sequence number was ever observed
// for this symbol: there is no span to measure, and returning early here is
// how this package avoids ever dividing by zero, rather than reporting a
// meaningless 0/0 result.
func (s *seqSymbolStats) loss() (span, unexplained int64, lossPct float64, ok bool) {
	if !s.haveSeq {
		return 0, 0, 0, false
	}
	span = s.seqMax - s.seqMin + 1
	if span <= 0 {
		// Unreachable in practice (seqMin/seqMax are only ever set together,
		// in observeBook, so seqMax >= seqMin always) but guarded anyway:
		// never divide by a non-positive span.
		return span, 0, 0, false
	}
	accounted := int64(len(s.bookSeqsSeen)) + int64(s.trades)
	unexplained = span - accounted
	lossPct = float64(unexplained) / float64(span) * 100
	return span, unexplained, lossPct, true
}

// seqAgg combines already-computed (span, unexplained) pairs from independent
// windows (e.g. one busiest-minute bucket per symbol) into a single loss
// percentage. It sums span and accounted directly rather than re-deriving
// them from merged seqMin/seqMax, because merging raw sequence numbers
// across non-contiguous windows would produce a bogus combined span (the
// sequence counter only makes sense as a contiguous range within one window).
type seqAgg struct {
	span         int64
	accounted    int64
	bookSeqsSeen int64
	trades       int64
}

func (a *seqAgg) add(span, unexplained, bookSeqsSeen, trades int64) {
	a.span += span
	a.accounted += span - unexplained
	a.bookSeqsSeen += bookSeqsSeen
	a.trades += trades
}

func (a *seqAgg) loss() (unexplained int64, lossPct float64) {
	unexplained = a.span - a.accounted
	if a.span <= 0 {
		return unexplained, 0
	}
	return unexplained, float64(unexplained) / float64(a.span) * 100
}

// minuteBucket accumulates per-symbol seqSymbolStats restricted to one
// market-wide minute, plus the minute's total record count (book + trade
// events, summed across every symbol in scope) used to rank "busiest".
type minuteBucket struct {
	totalRecords int64
	bySymbol     map[string]*seqSymbolStats
}

// seqRow is one reportable line: either a symbol's measured frame loss, or
// the synthetic "ALL" aggregate across every reported symbol.
type seqRow struct {
	Symbol       string  `json:"symbol"`
	BookSeqsSeen int64   `json:"book_seqs_seen"`
	Trades       int64   `json:"trades"`
	SeqSpan      int64   `json:"seq_span"`
	Unexplained  int64   `json:"unexplained"`
	LossPct      float64 `json:"loss_pct"`
}

// runSeqCheck replays one or more captured segments and reports measured
// per-symbol frame loss to stdout.
//
// Segment paths may appear before, after, or interleaved with flags; all
// non-flag tokens are treated as segment paths (see extractSegmentPaths).
//
// Flags:
//
//	--symbols A,B,C      (optional) restrict to these symbols; default is
//	                      every symbol observed in the replayed segments
//	--format table|json|csv   (default table)
//	--busiest-minutes N  (optional) additionally report loss aggregated over
//	                      the N busiest market-wide minutes (by total book +
//	                      trade record count, all symbols in scope combined).
//	                      This is §5d's discriminating test: a starved
//	                      collector must drop indiscriminately across
//	                      symbols in the same minute, while upstream
//	                      conflation tracks each symbol's own update rate.
//	                      0 (the default) disables it.
func runSeqCheck(args []string) error {
	segs, flagArgs := extractSegmentPaths(args)
	if len(segs) == 0 {
		return fmt.Errorf("usage: sbcap seqcheck <seg>... [--symbols A,B,C] [--format table|json|csv] [--busiest-minutes N]")
	}

	fs := flag.NewFlagSet("seqcheck", flag.ContinueOnError)
	symbolsFlag := fs.String("symbols", "", "comma-separated symbol filter (default: all symbols present)")
	formatFlag := fs.String("format", "table", "output format: table, json, or csv")
	busiestFlag := fs.Int("busiest-minutes", 0, "additionally report loss over the N busiest market-wide minutes (0 disables)")

	if err := fs.Parse(flagArgs); err != nil {
		return err
	}
	switch *formatFlag {
	case "table", "json", "csv":
	default:
		return fmt.Errorf("--format must be table, json, or csv (got %q)", *formatFlag)
	}
	if *busiestFlag < 0 {
		return fmt.Errorf("--busiest-minutes must be >= 0, got %d", *busiestFlag)
	}

	wantSymbol := parseSymbolFilter(*symbolsFlag)
	trackMinutes := *busiestFlag > 0

	overall := make(map[string]*seqSymbolStats)
	minutes := make(map[int64]*minuteBucket) // left empty unless trackMinutes

	replayErr := replay.Replay(segs, replay.ModeFast, func(evt replay.MarketEvent) {
		if evt.Kind != replay.EventOrderbook && evt.Kind != replay.EventRunningTrade {
			return
		}
		if !wantSymbol(evt.Symbol) {
			return
		}

		sym := overall[evt.Symbol]
		if sym == nil {
			sym = newSeqSymbolStats()
			overall[evt.Symbol] = sym
		}

		var minSym *seqSymbolStats
		if trackMinutes {
			// Epoch-minute buckets are correct here even though timestamps
			// are reported in WIB elsewhere in sbcap: WIB is a fixed whole-
			// hour offset (UTC+7), so it never shifts a minute boundary
			// relative to UTC.
			minuteKey := evt.RecvNs / int64(time.Minute)
			mb := minutes[minuteKey]
			if mb == nil {
				mb = &minuteBucket{bySymbol: make(map[string]*seqSymbolStats)}
				minutes[minuteKey] = mb
			}
			mb.totalRecords++
			minSym = mb.bySymbol[evt.Symbol]
			if minSym == nil {
				minSym = newSeqSymbolStats()
				mb.bySymbol[evt.Symbol] = minSym
			}
		}

		switch evt.Kind {
		case replay.EventOrderbook:
			if evt.Decoded == nil {
				return
			}
			sym.observeBook(evt.Decoded.SequenceNumber)
			if trackMinutes {
				minSym.observeBook(evt.Decoded.SequenceNumber)
			}
		case replay.EventRunningTrade:
			sym.observeTrade()
			if trackMinutes {
				minSym.observeTrade()
			}
		}
	})
	if replayErr != nil {
		return fmt.Errorf("replay: %w", replayErr)
	}

	rows := buildSeqRows(overall)

	var busiest []seqRow
	if trackMinutes {
		busiest = buildBusiestRows(minutes, *busiestFlag)
	}

	return writeSeqCheckReport(*formatFlag, rows, busiest, *busiestFlag)
}

// parseSymbolFilter turns a comma-separated --symbols value into a predicate.
// An empty filter string accepts every symbol.
func parseSymbolFilter(raw string) func(symbol string) bool {
	if raw == "" {
		return func(string) bool { return true }
	}
	want := make(map[string]bool)
	for _, s := range strings.Split(raw, ",") {
		if s = strings.TrimSpace(s); s != "" {
			want[s] = true
		}
	}
	return func(symbol string) bool { return want[symbol] }
}

// buildSeqRows converts accumulated per-symbol stats into report rows,
// sorted by loss ascending (quietest first), with a trailing "ALL" row.
//
// ALL is derived from summed raw span/accounted totals (not by summing each
// symbol's already-rounded loss_pct, and not by summing unexplained with
// per-symbol values clamped to zero): span_total - accounted_total is the
// only aggregate consistent with the per-symbol rows, which are themselves
// reported signed, per §5d. See the seqcheck report for a worked comparison
// against docs/plans/12 §5d's published ALL row.
func buildSeqRows(stats map[string]*seqSymbolStats) []seqRow {
	rows := make([]seqRow, 0, len(stats))
	var allSpan, allAccounted, allBook, allTrades int64
	for sym, s := range stats {
		span, unexplained, lossPct, ok := s.loss()
		if !ok {
			continue // no book sequence observed for this symbol — nothing to report
		}
		book := int64(len(s.bookSeqsSeen))
		trades := int64(s.trades)
		rows = append(rows, seqRow{
			Symbol:       sym,
			BookSeqsSeen: book,
			Trades:       trades,
			SeqSpan:      span,
			Unexplained:  unexplained,
			LossPct:      lossPct,
		})
		allSpan += span
		allAccounted += span - unexplained
		allBook += book
		allTrades += trades
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].LossPct < rows[j].LossPct })

	if len(rows) > 0 {
		allUnexplained := allSpan - allAccounted
		allLoss := 0.0
		if allSpan > 0 {
			allLoss = float64(allUnexplained) / float64(allSpan) * 100
		}
		rows = append(rows, seqRow{
			Symbol:       "ALL",
			BookSeqsSeen: allBook,
			Trades:       allTrades,
			SeqSpan:      allSpan,
			Unexplained:  allUnexplained,
			LossPct:      allLoss,
		})
	}
	return rows
}

// buildBusiestRows ranks minutes by total record count (descending), keeps
// the top n, and folds each symbol's per-minute (span, unexplained) pairs
// across that set into one aggregate loss per symbol — §5d reports a single
// number per symbol for its whole busiest-minutes set, not one row per
// minute, because an individual minute rarely carries enough records for a
// stable percentage on its own.
func buildBusiestRows(minutes map[int64]*minuteBucket, n int) []seqRow {
	if n <= 0 || len(minutes) == 0 {
		return nil
	}
	keys := make([]int64, 0, len(minutes))
	for k := range minutes {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return minutes[keys[i]].totalRecords > minutes[keys[j]].totalRecords
	})
	if n < len(keys) {
		keys = keys[:n]
	}

	agg := make(map[string]*seqAgg)
	for _, k := range keys {
		for sym, s := range minutes[k].bySymbol {
			span, unexplained, _, ok := s.loss()
			if !ok {
				continue // no book record for this symbol in this minute — no span to fold in
			}
			a := agg[sym]
			if a == nil {
				a = &seqAgg{}
				agg[sym] = a
			}
			a.add(span, unexplained, int64(len(s.bookSeqsSeen)), int64(s.trades))
		}
	}

	rows := make([]seqRow, 0, len(agg))
	for sym, a := range agg {
		unexplained, lossPct := a.loss()
		rows = append(rows, seqRow{
			Symbol:       sym,
			BookSeqsSeen: a.bookSeqsSeen,
			Trades:       a.trades,
			SeqSpan:      a.span,
			Unexplained:  unexplained,
			LossPct:      lossPct,
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].LossPct < rows[j].LossPct })
	return rows
}

// writeSeqCheckReport renders rows (and, if non-nil, the busiest-minutes
// aggregate) to stdout in the requested format.
func writeSeqCheckReport(format string, rows, busiest []seqRow, busiestN int) error {
	switch format {
	case "json":
		return writeSeqCheckJSON(rows, busiest, busiestN)
	case "csv":
		return writeSeqCheckCSV(rows, busiest, busiestN)
	default:
		writeSeqCheckTable(rows, busiest, busiestN)
		return nil
	}
}

// writeSeqCheckTable prints a fixed-width, terminal-readable table. Numeric
// columns use formatInt's thousands separators (see stat.go) to match the
// style of `sbcap stat`.
func writeSeqCheckTable(rows, busiest []seqRow, busiestN int) {
	printSeqTable(rows)
	if busiest != nil {
		fmt.Printf("\nbusiest %d minutes (market-wide, by total records):\n", busiestN)
		printSeqTable(busiest)
	}
}

func printSeqTable(rows []seqRow) {
	fmt.Printf("%-8s %14s %8s %10s %12s %8s\n",
		"symbol", "book seqs seen", "trades", "seq span", "unexplained", "loss")
	for _, r := range rows {
		symbolCol, bookCol, tradesCol := r.Symbol, formatInt(int(r.BookSeqsSeen)), formatInt(int(r.Trades))
		if r.Symbol == "ALL" {
			// Per-symbol book-seq/trade counts are not meaningful once
			// summed across symbols with independent sequence spaces (see
			// buildSeqRows); leave those two cells blank for ALL, matching
			// docs/plans/12 §5d's own table.
			bookCol, tradesCol = "", ""
		}
		fmt.Printf("%-8s %14s %8s %10s %12s %7.1f%%\n",
			symbolCol, bookCol, tradesCol,
			formatInt(int(r.SeqSpan)), formatInt(int(r.Unexplained)), r.LossPct)
	}
}

// writeSeqCheckJSON writes {"overall": [...], "busiest_minutes": {"n": N, "rows": [...]}}.
// busiest_minutes is omitted entirely when --busiest-minutes was not given.
func writeSeqCheckJSON(rows, busiest []seqRow, busiestN int) error {
	out := struct {
		Overall        []seqRow `json:"overall"`
		BusiestMinutes *struct {
			N    int      `json:"n"`
			Rows []seqRow `json:"rows"`
		} `json:"busiest_minutes,omitempty"`
	}{Overall: rows}
	if busiest != nil {
		out.BusiestMinutes = &struct {
			N    int      `json:"n"`
			Rows []seqRow `json:"rows"`
		}{N: busiestN, Rows: busiest}
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(out)
}

// seqCSVHeader is the column order emitted by writeSeqCheckCSV. scope is
// "overall" or "busiest" so a single CSV can carry both tables when
// --busiest-minutes is set, without ambiguity about which set a row belongs to.
var seqCSVHeader = []string{"scope", "symbol", "book_seqs_seen", "trades", "seq_span", "unexplained", "loss_pct"}

func writeSeqCheckCSV(rows, busiest []seqRow, busiestN int) error {
	w := csv.NewWriter(os.Stdout)
	if err := w.Write(seqCSVHeader); err != nil {
		return fmt.Errorf("csv write header: %w", err)
	}
	for _, r := range rows {
		if err := w.Write(seqCSVRow("overall", r)); err != nil {
			return fmt.Errorf("csv write row: %w", err)
		}
	}
	for _, r := range busiest {
		if err := w.Write(seqCSVRow(fmt.Sprintf("busiest_%d", busiestN), r)); err != nil {
			return fmt.Errorf("csv write row: %w", err)
		}
	}
	w.Flush()
	return w.Error()
}

func seqCSVRow(scope string, r seqRow) []string {
	return []string{
		scope,
		r.Symbol,
		strconv.FormatInt(r.BookSeqsSeen, 10),
		strconv.FormatInt(r.Trades, 10),
		strconv.FormatInt(r.SeqSpan, 10),
		strconv.FormatInt(r.Unexplained, 10),
		strconv.FormatFloat(r.LossPct, 'f', -1, 64),
	}
}
