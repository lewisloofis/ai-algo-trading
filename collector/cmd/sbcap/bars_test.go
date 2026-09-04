package main

import (
	"bytes"
	"encoding/csv"
	"io"
	"os"
	"strconv"
	"testing"
	"time"
)

// ---------- test helpers ----------

// wibNs returns the Unix nanosecond timestamp for the given WIB wall-clock
// instant. Segment records are keyed by this value (see writeTradeSegF /
// replay.Replay, which sets MarketEvent.RecvNs = the record's timestamp).
func wibNs(year, month, day, hour, min, sec int) int64 {
	return time.Date(year, time.Month(month), day, hour, min, sec, 0, wib).UnixNano()
}

// runBarsCapture runs runBars with args, capturing everything it writes to
// os.Stdout, and returns the result parsed as CSV records (including the
// header row as records[0]). trades_test.go and features_test.go never call
// runTrades/runFeatures directly (they replicate the filtering logic against
// the decoded stream instead), so there is no existing stdout-capture pattern
// to follow here — this swaps os.Stdout for a pipe and restores it via defer.
func runBarsCapture(t *testing.T, args []string) [][]string {
	t.Helper()

	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("os.Pipe: %v", err)
	}
	orig := os.Stdout
	os.Stdout = w
	defer func() { os.Stdout = orig }()

	runErr := runBars(args)
	if cerr := w.Close(); cerr != nil {
		t.Fatalf("close pipe writer: %v", cerr)
	}
	if runErr != nil {
		t.Fatalf("runBars: %v", runErr)
	}

	out, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("read pipe: %v", err)
	}
	records, err := csv.NewReader(bytes.NewReader(out)).ReadAll()
	if err != nil {
		t.Fatalf("parse csv output: %v\noutput:\n%s", err, out)
	}
	return records
}

// ---------- bar.add ----------

// TestBarAdd verifies the accumulation rules in bar.add: the first trade
// seeds open/high/low, high/low track extremes across subsequent trades,
// close is always the most recent price, volume/value accumulate, and
// buy/sell volumes and counts split on side while an untagged side ("")
// lands in the untagged bucket rather than buy or sell. The invariant
// volume == buyVol + sellVol + untaggedVol is checked for every case.
func TestBarAdd(t *testing.T) {
	t.Parallel()

	type trade struct {
		price, vol float64
		side       string
	}

	tests := []struct {
		name   string
		trades []trade
		want   bar
	}{
		{
			name:   "first trade seeds open high low close",
			trades: []trade{{100, 10, "B"}},
			want: bar{
				open: 100, high: 100, low: 100, close: 100,
				volume: 10, value: 1000, trades: 1,
				buyVol: 10, buyTrades: 1,
			},
		},
		{
			name: "high and low track extremes, close is last",
			trades: []trade{
				{100, 10, "B"},
				{105, 5, "S"},
				{95, 20, "B"},
				{102, 1, "S"},
			},
			want: bar{
				open: 100, high: 105, low: 95, close: 102,
				volume: 36, value: 100*10 + 105*5 + 95*20 + 102*1, trades: 4,
				buyVol: 30, buyTrades: 2,
				sellVol: 6, sellTrades: 2,
			},
		},
		{
			name: "untagged side lands in the untagged bucket, not buy or sell",
			trades: []trade{
				{100, 10, ""},
				{101, 5, "B"},
			},
			want: bar{
				open: 100, high: 101, low: 100, close: 101,
				volume: 15, value: 100*10 + 101*5, trades: 2,
				buyVol: 5, buyTrades: 1,
				untaggedVol: 10, untaggedTrades: 1,
			},
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			var b bar
			for _, tr := range tc.trades {
				b.add(tr.price, tr.vol, tr.side)
			}

			// Compare field-by-field (rather than the whole struct) so a
			// failure message names the specific accumulator that's wrong.
			if b.open != tc.want.open {
				t.Errorf("open: got %v want %v", b.open, tc.want.open)
			}
			if b.high != tc.want.high {
				t.Errorf("high: got %v want %v", b.high, tc.want.high)
			}
			if b.low != tc.want.low {
				t.Errorf("low: got %v want %v", b.low, tc.want.low)
			}
			if b.close != tc.want.close {
				t.Errorf("close: got %v want %v", b.close, tc.want.close)
			}
			if b.volume != tc.want.volume {
				t.Errorf("volume: got %v want %v", b.volume, tc.want.volume)
			}
			if b.value != tc.want.value {
				t.Errorf("value: got %v want %v", b.value, tc.want.value)
			}
			if b.trades != tc.want.trades {
				t.Errorf("trades: got %d want %d", b.trades, tc.want.trades)
			}
			if b.buyVol != tc.want.buyVol {
				t.Errorf("buyVol: got %v want %v", b.buyVol, tc.want.buyVol)
			}
			if b.sellVol != tc.want.sellVol {
				t.Errorf("sellVol: got %v want %v", b.sellVol, tc.want.sellVol)
			}
			if b.buyTrades != tc.want.buyTrades {
				t.Errorf("buyTrades: got %d want %d", b.buyTrades, tc.want.buyTrades)
			}
			if b.sellTrades != tc.want.sellTrades {
				t.Errorf("sellTrades: got %d want %d", b.sellTrades, tc.want.sellTrades)
			}
			if b.untaggedVol != tc.want.untaggedVol {
				t.Errorf("untaggedVol: got %v want %v", b.untaggedVol, tc.want.untaggedVol)
			}
			if b.untaggedTrades != tc.want.untaggedTrades {
				t.Errorf("untaggedTrades: got %d want %d", b.untaggedTrades, tc.want.untaggedTrades)
			}
			if b.volume != b.buyVol+b.sellVol+b.untaggedVol {
				t.Errorf("invariant broken: volume %v != buyVol %v + sellVol %v + untaggedVol %v",
					b.volume, b.buyVol, b.sellVol, b.untaggedVol)
			}
		})
	}
}

// ---------- parseDaySeconds ----------

// TestBarParseDaySeconds verifies the HH:MM / HH:MM:SS parsing and the "no
// bound" sentinel for an empty string.
func TestBarParseDaySeconds(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		in      string
		want    int64
		wantErr bool
	}{
		{name: "empty string means no bound", in: "", want: -1},
		{name: "HH:MM", in: "09:30", want: 9*3600 + 30*60},
		{name: "HH:MM:SS", in: "09:30:15", want: 9*3600 + 30*60 + 15},
		{name: "invalid input errors", in: "not-a-time", wantErr: true},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			got, err := parseDaySeconds(tc.in)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("parseDaySeconds(%q): want error, got nil (result %d)", tc.in, got)
				}
				return
			}
			if err != nil {
				t.Fatalf("parseDaySeconds(%q): unexpected error: %v", tc.in, err)
			}
			if got != tc.want {
				t.Errorf("parseDaySeconds(%q): got %d want %d", tc.in, got, tc.want)
			}
		})
	}
}

// TestBarsFromToBoundAppliesToEveryDay verifies the intent documented on
// parseDaySeconds: --from/--to carry no date, so the same wall-clock bound
// applies independently on every day of a multi-day replay, not just the
// first. Two segments a day apart each carry one trade inside the window and
// one outside it; both in-window trades must survive and both out-of-window
// trades must be dropped.
func TestBarsFromToBoundAppliesToEveryDay(t *testing.T) {
	const sym = "BUMI"

	seg1 := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 0, 0)},  // before window: day 1
		{sym: sym, price: 101, vol: 10, action: 1, tradeNum: 2, ts: wibNs(2026, 1, 5, 10, 0, 0)}, // inside window: day 1
	})
	seg2 := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 102, vol: 10, action: 1, tradeNum: 3, ts: wibNs(2026, 1, 6, 10, 0, 0)}, // inside window: day 2
		{sym: sym, price: 103, vol: 10, action: 1, tradeNum: 4, ts: wibNs(2026, 1, 6, 15, 0, 0)}, // after window: day 2
	})

	records := runBarsCapture(t, []string{seg1, seg2, "--symbol", sym, "--from", "09:30", "--to", "11:00"})
	rows := records[1:]

	if len(rows) != 2 {
		t.Fatalf("row count: got %d want 2 (one surviving trade per day); rows=%v", len(rows), rows)
	}
	if rows[0][1] != "2026-01-05" {
		t.Errorf("row 0 date: got %q want 2026-01-05", rows[0][1])
	}
	if rows[1][1] != "2026-01-06" {
		t.Errorf("row 1 date: got %q want 2026-01-06", rows[1][1])
	}
	// volume=10 on each day confirms exactly the one in-window trade landed,
	// not the out-of-window trade on that same day.
	if rows[0][6] != "10" {
		t.Errorf("row 0 volume: got %q want 10 (only the in-window trade)", rows[0][6])
	}
	if rows[1][6] != "10" {
		t.Errorf("row 1 volume: got %q want 10 (only the in-window trade)", rows[1][6])
	}
}

// ---------- bar.row ----------

// TestBarRowColumnCount verifies row() always emits exactly
// len(barsCSVHeader) columns, both for a populated bar and for a bar that
// never saw a trade (the zero value).
func TestBarRowColumnCount(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		b    *bar
	}{
		{name: "zero-value bar (no trades)", b: &bar{}},
		{name: "populated bar", b: func() *bar {
			b := &bar{}
			b.add(100, 10, "B")
			b.add(105, 5, "S")
			return b
		}()},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			row := tc.b.row()
			if len(row) != len(barsCSVHeader) {
				t.Fatalf("column count: got %d want %d (row=%v)", len(row), len(barsCSVHeader), row)
			}
		})
	}
}

// TestBarRowVWAP verifies vwap = value/volume, formatted to a fixed four
// decimal places, and that a bar with zero volume renders vwap as "0.0000"
// rather than NaN or a divide-by-zero panic.
func TestBarRowVWAP(t *testing.T) {
	t.Parallel()

	const vwapCol = 15
	if barsCSVHeader[vwapCol] != "vwap" {
		t.Fatalf("test assumes vwap is column %d, but barsCSVHeader[%d] = %q", vwapCol, vwapCol, barsCSVHeader[vwapCol])
	}

	tests := []struct {
		name          string
		volume, value float64
		want          string
	}{
		{name: "zero volume yields zero vwap, not NaN", volume: 0, value: 0, want: "0.0000"},
		{name: "vwap is value divided by volume", volume: 10, value: 1005, want: "100.5000"},
		{name: "whole-number vwap still renders four decimals", volume: 5, value: 500, want: "100.0000"},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			b := &bar{volume: tc.volume, value: tc.value}
			row := b.row()
			if row[vwapCol] != tc.want {
				t.Errorf("vwap: got %q want %q", row[vwapCol], tc.want)
			}
		})
	}
}

// TestBarRowFullRendering exercises row() end-to-end against a populated bar
// with a known startNs, checking every column at once so the column-order
// contract with barsCSVHeader is pinned down explicitly.
func TestBarRowFullRendering(t *testing.T) {
	t.Parallel()

	b := &bar{startNs: wibNs(2026, 1, 5, 9, 30, 0)}
	b.add(100, 10, "B")
	b.add(105, 5, "S")

	want := []string{
		"2026-01-05T09:30:00", "2026-01-05",
		"100", "105", "100", "105", // open high low close
		"15",      // volume
		"1525",    // value = 100*10 + 105*5
		"2",       // trades
		"10", "5", // buy_vol sell_vol
		"1", "1", // buy_trades sell_trades
		"0", "0", // untagged_vol untagged_trades (no untagged trades here)
		strconv.FormatFloat(1525.0/15.0, 'f', 4, 64), // vwap
	}

	row := b.row()
	if len(row) != len(want) {
		t.Fatalf("row length: got %d want %d", len(row), len(want))
	}
	for i, col := range barsCSVHeader {
		if row[i] != want[i] {
			t.Errorf("column %q (index %d): got %q want %q", col, i, row[i], want[i])
		}
	}
}

// mustParseFloatBars parses a CSV field as a float64, failing the test on a
// parse error rather than silently comparing against 0.
func mustParseFloatBars(t *testing.T, s string) float64 {
	t.Helper()
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		t.Fatalf("parse float %q: %v", s, err)
	}
	return v
}

// TestBarRowInvariantVolumeEqualsSidesSum verifies, against the rendered CSV
// row (not just the in-memory struct), that volume == buy_vol + sell_vol +
// untagged_vol holds regardless of which sides are present in the bar.
func TestBarRowInvariantVolumeEqualsSidesSum(t *testing.T) {
	t.Parallel()

	type trade struct {
		price, vol float64
		side       string
	}

	tests := []struct {
		name   string
		trades []trade
	}{
		{name: "buy only", trades: []trade{{100, 10, "B"}}},
		{name: "sell only", trades: []trade{{100, 10, "S"}}},
		{name: "untagged only", trades: []trade{{100, 10, ""}}},
		{name: "mixed buy, sell, and untagged", trades: []trade{
			{100, 10, "B"},
			{101, 5, "S"},
			{102, 7, ""},
		}},
	}

	const (
		volumeCol      = 6
		buyVolCol      = 9
		sellVolCol     = 10
		untaggedVolCol = 13
	)

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			var b bar
			for _, tr := range tc.trades {
				b.add(tr.price, tr.vol, tr.side)
			}
			row := b.row()

			volume := mustParseFloatBars(t, row[volumeCol])
			buyVol := mustParseFloatBars(t, row[buyVolCol])
			sellVol := mustParseFloatBars(t, row[sellVolCol])
			untaggedVol := mustParseFloatBars(t, row[untaggedVolCol])

			if want := buyVol + sellVol + untaggedVol; volume != want {
				t.Errorf("invariant broken: volume=%v want buy_vol(%v)+sell_vol(%v)+untagged_vol(%v)=%v",
					volume, buyVol, sellVol, untaggedVol, want)
			}
		})
	}
}

// ---------- runBars end-to-end bucketing ----------

// TestBarsCollapsesTradesWithinMinute verifies that multiple trades falling
// in the same minute-wide bucket collapse into a single output row.
func TestBarsCollapsesTradesWithinMinute(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 10)},
		{sym: sym, price: 101, vol: 5, action: 2, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 30, 45)},
	})

	records := runBarsCapture(t, []string{segPath, "--symbol", sym})
	rows := records[1:]

	if len(rows) != 1 {
		t.Fatalf("row count: got %d want 1 (both trades share the 09:30 minute); rows=%v", len(rows), rows)
	}
	if rows[0][6] != "15" {
		t.Errorf("volume: got %q want 15 (10+5 collapsed into one bar)", rows[0][6])
	}
	if rows[0][8] != "2" {
		t.Errorf("trades: got %q want 2", rows[0][8])
	}
}

// TestBarsSeparatesTradesAcrossMinutesAscending verifies that trades in
// different minute buckets produce separate rows, emitted in ascending time
// order regardless of any particular replay order within the segment.
func TestBarsSeparatesTradesAcrossMinutesAscending(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 31, 0)},
		{sym: sym, price: 101, vol: 5, action: 1, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 30, 0)},
	})

	records := runBarsCapture(t, []string{segPath, "--symbol", sym})
	rows := records[1:]

	if len(rows) != 2 {
		t.Fatalf("row count: got %d want 2; rows=%v", len(rows), rows)
	}
	if rows[0][0] != "2026-01-05T09:30:00" {
		t.Errorf("row 0 datetime: got %q want 2026-01-05T09:30:00 (ascending order)", rows[0][0])
	}
	if rows[1][0] != "2026-01-05T09:31:00" {
		t.Errorf("row 1 datetime: got %q want 2026-01-05T09:31:00 (ascending order)", rows[1][0])
	}
}

// TestBarsOmitsEmptyBuckets verifies that a minute with no trades produces no
// row at all — gaps are omitted rather than emitted as zero-volume bars, to
// match chartbit's behaviour.
func TestBarsOmitsEmptyBuckets(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 0)},
		// 09:31 deliberately has no trades.
		{sym: sym, price: 102, vol: 10, action: 1, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 32, 0)},
	})

	records := runBarsCapture(t, []string{segPath, "--symbol", sym})
	rows := records[1:]

	if len(rows) != 2 {
		t.Fatalf("row count: got %d want 2 (the empty 09:31 minute must not appear); rows=%v", len(rows), rows)
	}
	if rows[0][0] != "2026-01-05T09:30:00" {
		t.Errorf("row 0 datetime: got %q want 2026-01-05T09:30:00", rows[0][0])
	}
	if rows[1][0] != "2026-01-05T09:32:00" {
		t.Errorf("row 1 datetime: got %q want 2026-01-05T09:32:00 (skips straight over the empty minute)", rows[1][0])
	}
}

// TestBarsLabelledByBucketStart verifies that a bucket's datetime column is
// its START time, not the time of the last (or any) trade inside it.
func TestBarsLabelledByBucketStart(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		// Both trades land in the [09:30:00, 09:31:00) bucket but arrive well
		// after its start; the label must still be 09:30:00.
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 1)},
		{sym: sym, price: 101, vol: 10, action: 1, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 30, 59)},
	})

	records := runBarsCapture(t, []string{segPath, "--symbol", sym})
	rows := records[1:]

	if len(rows) != 1 {
		t.Fatalf("row count: got %d want 1; rows=%v", len(rows), rows)
	}
	if rows[0][0] != "2026-01-05T09:30:00" {
		t.Errorf("datetime: got %q want 2026-01-05T09:30:00 (bucket start, not trade time)", rows[0][0])
	}
}

// TestBarsNonDefaultInterval verifies that a non-default --interval (5m)
// groups trades into 5-minute buckets aligned to WIB midnight, rather than
// the default 1-minute bucket.
func TestBarsNonDefaultInterval(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 31, 0)}, // 09:30 5m-bucket
		{sym: sym, price: 101, vol: 20, action: 1, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 33, 0)}, // 09:30 5m-bucket
		{sym: sym, price: 102, vol: 30, action: 1, tradeNum: 3, ts: wibNs(2026, 1, 5, 9, 36, 0)}, // 09:35 5m-bucket
	})

	records := runBarsCapture(t, []string{segPath, "--symbol", sym, "--interval", "5m"})
	rows := records[1:]

	if len(rows) != 2 {
		t.Fatalf("row count: got %d want 2 (two 5-minute buckets); rows=%v", len(rows), rows)
	}
	if rows[0][0] != "2026-01-05T09:30:00" {
		t.Errorf("row 0 datetime: got %q want 2026-01-05T09:30:00", rows[0][0])
	}
	if rows[0][6] != "30" {
		t.Errorf("row 0 volume: got %q want 30 (10+20 grouped into the 09:30 5m bucket)", rows[0][6])
	}
	if rows[1][0] != "2026-01-05T09:35:00" {
		t.Errorf("row 1 datetime: got %q want 2026-01-05T09:35:00", rows[1][0])
	}
	if rows[1][6] != "30" {
		t.Errorf("row 1 volume: got %q want 30", rows[1][6])
	}
}

// TestBarsSortsSegmentsPassedOutOfOrder verifies that bars are emitted in
// ascending time order even when the segment file arguments are passed in
// reverse chronological order.
func TestBarsSortsSegmentsPassedOutOfOrder(t *testing.T) {
	const sym = "BUMI"
	segLater := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 102, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 6, 9, 30, 0)},
	})
	segEarlier := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 0)},
	})

	// Pass the later segment first — out of chronological order.
	records := runBarsCapture(t, []string{segLater, segEarlier, "--symbol", sym})
	rows := records[1:]

	if len(rows) != 2 {
		t.Fatalf("row count: got %d want 2; rows=%v", len(rows), rows)
	}
	if rows[0][1] != "2026-01-05" {
		t.Errorf("row 0 date: got %q want 2026-01-05 (earlier segment sorted first)", rows[0][1])
	}
	if rows[1][1] != "2026-01-06" {
		t.Errorf("row 1 date: got %q want 2026-01-06 (later segment sorted second)", rows[1][1])
	}
}

// ---------- runBars end-to-end: untagged trades and --tagged-only ----------

// TestBarsUntaggedTradesLandInUntaggedBucket verifies the new default
// behaviour: every trade is aggregated, tagged or not. An untagged trade
// (side == "", action=0) sharing a bucket with a buy and a sell contributes
// to total volume/trades and to untagged_vol/untagged_trades, but to neither
// buy_vol/buy_trades nor sell_vol/sell_trades.
func TestBarsUntaggedTradesLandInUntaggedBucket(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 0)},  // B
		{sym: sym, price: 101, vol: 20, action: 0, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 30, 10)}, // untagged
		{sym: sym, price: 102, vol: 5, action: 2, tradeNum: 3, ts: wibNs(2026, 1, 5, 9, 30, 20)},  // S
	})

	// Default: no --tagged-only, so every trade is aggregated.
	records := runBarsCapture(t, []string{segPath, "--symbol", sym})
	rows := records[1:]

	if len(rows) != 1 {
		t.Fatalf("row count: got %d want 1; rows=%v", len(rows), rows)
	}
	row := rows[0]
	if row[6] != "35" {
		t.Errorf("volume: got %q want 35 (10+20+5, untagged included)", row[6])
	}
	if row[8] != "3" {
		t.Errorf("trades: got %q want 3", row[8])
	}
	if row[9] != "10" {
		t.Errorf("buy_vol: got %q want 10 (untagged trade excluded)", row[9])
	}
	if row[10] != "5" {
		t.Errorf("sell_vol: got %q want 5 (untagged trade excluded)", row[10])
	}
	if row[13] != "20" {
		t.Errorf("untagged_vol: got %q want 20", row[13])
	}
	if row[14] != "1" {
		t.Errorf("untagged_trades: got %q want 1", row[14])
	}
}

// TestBarsTaggedOnlyDropsUntaggedTrades verifies that --tagged-only removes
// the untagged trade's contribution from a bucket that also holds tagged
// trades, restoring the old side-tagged-only behaviour.
func TestBarsTaggedOnlyDropsUntaggedTrades(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 1, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 0)},  // B
		{sym: sym, price: 101, vol: 20, action: 0, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 30, 10)}, // untagged
		{sym: sym, price: 102, vol: 5, action: 2, tradeNum: 3, ts: wibNs(2026, 1, 5, 9, 30, 20)},  // S
	})

	records := runBarsCapture(t, []string{segPath, "--symbol", sym, "--tagged-only"})
	rows := records[1:]

	if len(rows) != 1 {
		t.Fatalf("row count: got %d want 1; rows=%v", len(rows), rows)
	}
	row := rows[0]
	if row[6] != "15" {
		t.Errorf("volume: got %q want 15 (10+5, untagged trade dropped by --tagged-only)", row[6])
	}
	if row[8] != "2" {
		t.Errorf("trades: got %q want 2", row[8])
	}
	if row[13] != "0" {
		t.Errorf("untagged_vol: got %q want 0", row[13])
	}
	if row[14] != "0" {
		t.Errorf("untagged_trades: got %q want 0", row[14])
	}
}

// TestBarsTaggedOnlyOmitsBarWithOnlyUntaggedTrades verifies that --tagged-only
// drops an untagged trade before bucketing, so a bucket whose only trade was
// untagged never becomes a bar at all — it disappears from the output
// entirely rather than surviving as a zero-volume row. Without --tagged-only
// the same bucket is emitted normally.
func TestBarsTaggedOnlyOmitsBarWithOnlyUntaggedTrades(t *testing.T) {
	const sym = "BUMI"
	segPath := writeTradeSegF(t, []struct {
		sym      string
		price    float64
		vol      float64
		action   int
		tradeNum int
		ts       int64
	}{
		{sym: sym, price: 100, vol: 10, action: 0, tradeNum: 1, ts: wibNs(2026, 1, 5, 9, 30, 0)}, // untagged-only bucket
		{sym: sym, price: 105, vol: 20, action: 1, tradeNum: 2, ts: wibNs(2026, 1, 5, 9, 32, 0)}, // tagged bucket
	})

	defaultRows := runBarsCapture(t, []string{segPath, "--symbol", sym})[1:]
	if len(defaultRows) != 2 {
		t.Fatalf("default row count: got %d want 2 (the untagged-only bucket is still emitted); rows=%v", len(defaultRows), defaultRows)
	}

	taggedOnlyRows := runBarsCapture(t, []string{segPath, "--symbol", sym, "--tagged-only"})[1:]
	if len(taggedOnlyRows) != 1 {
		t.Fatalf("--tagged-only row count: got %d want 1 (the untagged-only bucket must vanish entirely); rows=%v", len(taggedOnlyRows), taggedOnlyRows)
	}
	if taggedOnlyRows[0][0] != "2026-01-05T09:32:00" {
		t.Errorf("--tagged-only surviving bucket: got %q want 2026-01-05T09:32:00 (the tagged bucket)", taggedOnlyRows[0][0])
	}
}
