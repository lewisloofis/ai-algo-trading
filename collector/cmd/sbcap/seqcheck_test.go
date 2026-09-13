package main

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"io"
	"os"
	"testing"

	"collector/segment"
)

// ---------- test helpers ----------

// newTestSeqStats builds a seqSymbolStats by replaying observeBook for each
// value in bookSeqs (duplicates collapse, as they would for a real repeated
// sequence number) followed by trades calls to observeTrade.
func newTestSeqStats(bookSeqs []int64, trades int) *seqSymbolStats {
	s := newSeqSymbolStats()
	for _, seq := range bookSeqs {
		s.observeBook(seq)
	}
	for i := 0; i < trades; i++ {
		s.observeTrade()
	}
	return s
}

// runSeqCheckCapture runs runSeqCheck with args, capturing everything it
// writes to os.Stdout, and returns the raw bytes. Mirrors runBarsCapture in
// bars_test.go (there is no existing stdout-capture helper shared across
// subcommands, so each follows the same swap-and-restore pattern).
func runSeqCheckCapture(t *testing.T, args []string) []byte {
	t.Helper()

	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	orig := os.Stdout
	os.Stdout = w
	defer func() { os.Stdout = orig }()

	runErr := runSeqCheck(args)
	if cerr := w.Close(); cerr != nil {
		t.Fatalf("close pipe writer: %v", cerr)
	}
	if runErr != nil {
		t.Fatalf("runSeqCheck: %v", runErr)
	}

	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("read pipe: %v", err)
	}
	return out
}

// writeSeqSegF writes a segment file containing one orderbook frame per
// (symbol, seq) pair and one running-trade frame per (symbol, tradeNum) pair,
// all at strictly increasing timestamps starting at baseNs, in the order
// given. This lets a test lay out book/trade interleaving explicitly.
type seqFrame struct {
	sym    string
	isBook bool
	seq    int64 // book sequence number, when isBook
}

func writeSeqSegF(t *testing.T, baseNs int64, frames []seqFrame) string {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), "seg-seqcheck-*.binpb")
	if err != nil {
		t.Fatalf("create temp: %v", err)
	}
	defer f.Close()

	w := segment.NewWriter(f)
	for i, fr := range frames {
		ts := baseNs + int64(i)
		var payload []byte
		if fr.isBook {
			payload = buildObFrameF(fr.sym, "", fr.seq)
		} else {
			payload = buildTradeFrameF(fr.sym, 100, 1, 1, i) // action=1 ("B"); side is irrelevant to seqcheck
		}
		if err := w.WriteData(ts, payload); err != nil {
			t.Fatalf("WriteData: %v", err)
		}
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}
	return f.Name()
}

// parseSeqCSV parses seqcheck's CSV output (see seqCSVHeader) into rows keyed
// by "scope/symbol", skipping the header.
func parseSeqCSV(t *testing.T, out []byte) map[string][]string {
	t.Helper()
	records, err := csv.NewReader(bytes.NewReader(out)).ReadAll()
	if err != nil {
		t.Fatalf("parse csv output: %v\noutput:\n%s", err, out)
	}
	if len(records) == 0 {
		t.Fatalf("csv output has no rows, not even a header")
	}
	rows := make(map[string][]string, len(records)-1)
	for _, rec := range records[1:] {
		rows[rec[0]+"/"+rec[1]] = rec
	}
	return rows
}

// ---------- seqSymbolStats.loss arithmetic ----------

// TestSeqSymbolStatsLoss covers the §5d formula directly:
//
//	seq_span    = max(book sequence) - min(book sequence) + 1
//	accounted   = (distinct book sequence numbers seen) + (running-trade records)
//	unexplained = seq_span - accounted
//	loss_pct    = unexplained / seq_span * 100
//
// including the negative-unexplained (over-accounted) case, and the
// empty/one-record symbols that must not divide by zero.
func TestSeqSymbolStatsLoss(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name            string
		bookSeqs        []int64
		trades          int
		wantSpan        int64
		wantUnexplained int64
		wantLossPct     float64
		wantOK          bool
	}{
		{
			name: "ordinary loss: some frames genuinely missing",
			// distinct seqs 1..60 plus 100 (max), so span = 100-1+1 = 100,
			// distinct count = 61, trades = 29 -> accounted = 90, unexplained = 10.
			bookSeqs:        append(seqRange(1, 60), 100),
			trades:          29,
			wantSpan:        100,
			wantUnexplained: 10,
			wantLossPct:     10,
			wantOK:          true,
		},
		{
			name: "negative unexplained: shared-space model over-accounts",
			// span = 10-1+1 = 10, distinct count = 8, trades = 4 -> accounted = 12.
			bookSeqs:        []int64{1, 2, 3, 4, 5, 6, 7, 10},
			trades:          4,
			wantSpan:        10,
			wantUnexplained: -2,
			wantLossPct:     -20,
			wantOK:          true,
		},
		{
			name:            "empty symbol: no book sequence observed at all",
			bookSeqs:        nil,
			trades:          5, // trades with no book context can't be accounted for a span
			wantSpan:        0,
			wantUnexplained: 0,
			wantLossPct:     0,
			wantOK:          false,
		},
		{
			name:            "one-record symbol: span of 1, no division by zero",
			bookSeqs:        []int64{42},
			trades:          0,
			wantSpan:        1,
			wantUnexplained: 0,
			wantLossPct:     0,
			wantOK:          true,
		},
		{
			name:            "one-record symbol with trades: still no division by zero",
			bookSeqs:        []int64{42},
			trades:          3,
			wantSpan:        1,
			wantUnexplained: -3,
			wantLossPct:     -300,
			wantOK:          true,
		},
		{
			name: "duplicate sequence numbers collapse to one distinct value",
			// seq 5 observed twice must not inflate the distinct count.
			bookSeqs:        []int64{1, 2, 3, 3, 4, 5},
			trades:          0,
			wantSpan:        5,
			wantUnexplained: 0,
			wantLossPct:     0,
			wantOK:          true,
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			s := newTestSeqStats(tc.bookSeqs, tc.trades)
			span, unexplained, lossPct, ok := s.loss()
			if ok != tc.wantOK {
				t.Fatalf("ok: got %v want %v", ok, tc.wantOK)
			}
			if !ok {
				return // span/unexplained/lossPct are defined as zero and not otherwise meaningful
			}
			if span != tc.wantSpan {
				t.Errorf("span: got %d want %d", span, tc.wantSpan)
			}
			if unexplained != tc.wantUnexplained {
				t.Errorf("unexplained: got %d want %d", unexplained, tc.wantUnexplained)
			}
			if lossPct != tc.wantLossPct {
				t.Errorf("lossPct: got %v want %v", lossPct, tc.wantLossPct)
			}
		})
	}
}

// seqRange returns the inclusive integer range [lo, hi] as int64.
func seqRange(lo, hi int64) []int64 {
	out := make([]int64, 0, hi-lo+1)
	for v := lo; v <= hi; v++ {
		out = append(out, v)
	}
	return out
}

// ---------- buildSeqRows: sorting and the ALL row ----------

// TestBuildSeqRowsSortsByLossAscendingWithTrailingAllRow verifies rows come
// out sorted quietest-first and that ALL is a raw-total aggregate (summed
// span/accounted, not an average of per-symbol loss_pct or of already-
// clamped unexplained values) appended after the sorted symbols.
func TestBuildSeqRowsSortsByLossAscendingWithTrailingAllRow(t *testing.T) {
	t.Parallel()

	stats := map[string]*seqSymbolStats{
		// span 100, accounted 90 -> unexplained 10 -> loss 10%
		"NOISY": newTestSeqStats(append(seqRange(1, 60), 100), 29),
		// span 10, accounted 12 -> unexplained -2 -> loss -20%
		"QUIET": newTestSeqStats([]int64{1, 2, 3, 4, 5, 6, 7, 10}, 4),
	}

	rows := buildSeqRows(stats)
	if len(rows) != 3 {
		t.Fatalf("row count: got %d want 3 (QUIET, NOISY, ALL)", len(rows))
	}
	if rows[0].Symbol != "QUIET" || rows[1].Symbol != "NOISY" {
		t.Fatalf("sort order: got [%s, %s] want [QUIET, NOISY] (ascending loss)", rows[0].Symbol, rows[1].Symbol)
	}
	all := rows[2]
	if all.Symbol != "ALL" {
		t.Fatalf("last row: got %q want ALL", all.Symbol)
	}
	// span sums: 100 + 10 = 110; accounted sums: 90 + 12 = 102; unexplained = 8.
	if all.SeqSpan != 110 {
		t.Errorf("ALL seq span: got %d want 110", all.SeqSpan)
	}
	if all.Unexplained != 8 {
		t.Errorf("ALL unexplained: got %d want 8 (raw total, not clamped-then-summed)", all.Unexplained)
	}
}

// TestBuildSeqRowsSkipsSymbolWithNoBookData verifies a symbol with zero
// observed book sequence numbers (e.g. trades-only, or simply absent) is
// omitted from the report rather than producing a division-by-zero row, and
// that ALL is likewise omitted when nothing else qualifies.
func TestBuildSeqRowsSkipsSymbolWithNoBookData(t *testing.T) {
	t.Parallel()

	stats := map[string]*seqSymbolStats{
		"NODATA": newTestSeqStats(nil, 7),
	}
	rows := buildSeqRows(stats)
	if len(rows) != 0 {
		t.Fatalf("rows: got %d want 0 (no book data anywhere -> nothing reportable, no ALL row)", len(rows))
	}
}

// ---------- runSeqCheck integration: symbol filter and multi-segment accumulation ----------

// TestRunSeqCheckSymbolFilter verifies --symbols restricts both the reported
// symbols and the ALL aggregate to the requested set.
// t.Parallel is deliberately not used here: this test swaps the package-
// level os.Stdout to capture output (see runSeqCheckCapture), which is not
// safe across concurrently running tests (bars_test.go's runBarsCapture
// tests follow the same rule).
func TestRunSeqCheckSymbolFilter(t *testing.T) {
	const baseNs = int64(1_750_000_000_000_000_000)
	segPath := writeSeqSegF(t, baseNs, []seqFrame{
		{sym: "AAAA", isBook: true, seq: 1},
		{sym: "AAAA", isBook: true, seq: 2},
		{sym: "AAAA", isBook: false},
		{sym: "BBBB", isBook: true, seq: 1},
		{sym: "BBBB", isBook: true, seq: 5}, // BBBB has real gaps, unlike AAAA
	})

	out := runSeqCheckCapture(t, []string{segPath, "--symbols", "AAAA", "--format", "csv"})
	rows := parseSeqCSV(t, out)

	if _, ok := rows["overall/BBBB"]; ok {
		t.Fatalf("BBBB present in output despite --symbols AAAA")
	}
	aRow, ok := rows["overall/AAAA"]
	if !ok {
		t.Fatalf("AAAA missing from filtered output")
	}
	allRow, ok := rows["overall/ALL"]
	if !ok {
		t.Fatalf("ALL row missing from filtered output")
	}
	// AAAA: span 1..2 = 2, accounted = 2 book + 1 trade = 3, unexplained = -1.
	if got := aRow[4]; got != "2" { // seq_span column
		t.Errorf("AAAA seq_span: got %s want 2", got)
	}
	if got := aRow[5]; got != "-1" { // unexplained column
		t.Errorf("AAAA unexplained: got %s want -1", got)
	}
	// ALL must equal AAAA exactly, since BBBB is filtered out.
	if allRow[4] != aRow[4] || allRow[5] != aRow[5] {
		t.Errorf("ALL does not match the single included symbol: ALL=%v AAAA=%v", allRow, aRow)
	}
}

// TestRunSeqCheckMultiSegmentAccumulation verifies that sequence range,
// distinct-seq counts and trade counts accumulate correctly across multiple
// segment files passed in the same invocation, as they would for a full
// session split into hourly segments.
// t.Parallel is deliberately not used here; see TestRunSeqCheckSymbolFilter.
func TestRunSeqCheckMultiSegmentAccumulation(t *testing.T) {
	const sym = "MULT"
	const baseNs1 = int64(1_750_000_000_000_000_000)
	const baseNs2 = int64(1_750_000_100_000_000_000) // later segment, later timestamps

	seg1 := writeSeqSegF(t, baseNs1, []seqFrame{
		{sym: sym, isBook: true, seq: 1},
		{sym: sym, isBook: true, seq: 2},
		{sym: sym, isBook: false},
	})
	seg2 := writeSeqSegF(t, baseNs2, []seqFrame{
		{sym: sym, isBook: true, seq: 3},
		{sym: sym, isBook: true, seq: 4},
		{sym: sym, isBook: false},
		{sym: sym, isBook: false},
	})

	out := runSeqCheckCapture(t, []string{seg1, seg2, "--format", "json"})

	var parsed struct {
		Overall []seqRow `json:"overall"`
	}
	if err := json.Unmarshal(out, &parsed); err != nil {
		t.Fatalf("parse json output: %v\noutput:\n%s", err, out)
	}

	var got *seqRow
	for i := range parsed.Overall {
		if parsed.Overall[i].Symbol == sym {
			got = &parsed.Overall[i]
		}
	}
	if got == nil {
		t.Fatalf("%s missing from combined output: %+v", sym, parsed.Overall)
	}
	// Combined across both segments: seq 1..4 (span 4), 4 distinct book seqs,
	// 3 trades -> accounted 7, unexplained = 4-7 = -3.
	if got.SeqSpan != 4 {
		t.Errorf("combined seq_span: got %d want 4 (must span both segments' sequence numbers)", got.SeqSpan)
	}
	if got.BookSeqsSeen != 4 {
		t.Errorf("combined book_seqs_seen: got %d want 4", got.BookSeqsSeen)
	}
	if got.Trades != 3 {
		t.Errorf("combined trades: got %d want 3", got.Trades)
	}
	if got.Unexplained != -3 {
		t.Errorf("combined unexplained: got %d want -3", got.Unexplained)
	}
}
