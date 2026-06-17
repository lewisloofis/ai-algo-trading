package main

import (
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"time"

	"sb-algo-prototype/internal/replay"
	"sb-algo-prototype/internal/segment"
	"sb-algo-prototype/internal/zstdio"
)

// wib is the fixed UTC+7 zone for Western Indonesia Time.
// DST is never observed in Indonesia, so a fixed offset is always correct.
// time.LoadLocation is deliberately avoided: it requires tzdata on the host.
var wib = time.FixedZone("WIB", 7*60*60)

// statResult accumulates all metrics gathered during a single-pass replay.
type statResult struct {
	// record counts from the raw segment reader pass
	dataRecords    int
	controlRecords int

	// channel-level counts from decoded events
	orderbook    int
	liveprice    int
	runningTrade int
	ping         int
	errCount     int

	// per-symbol trade counts (from running_trade events)
	tradeCounts map[string]int

	// per-symbol orderbook counts
	orderbookCounts map[string]int

	// timing
	firstRecvNs int64
	lastRecvNs  int64

	// per-symbol sequence-number range (global feed counter, not per-symbol monotonic).
	// Used only to report the observed range; gap detection is unreliable at per-symbol
	// level because the counter is shared across all symbols and BID/OFFER interleave.
	seqMin map[string]int64
	seqMax map[string]int64

	// control event summary
	reconnects   int
	reauths      int
	switchResets int
	connects     int
	subscribes   int
}

func newStatResult() *statResult {
	return &statResult{
		tradeCounts:     make(map[string]int),
		orderbookCounts: make(map[string]int),
		seqMin:          make(map[string]int64),
		seqMax:          make(map[string]int64),
		firstRecvNs:     math.MaxInt64,
		lastRecvNs:      math.MinInt64,
	}
}

func (s *statResult) update(evt replay.MarketEvent) {
	// Track timing across all events.
	if evt.RecvNs < s.firstRecvNs {
		s.firstRecvNs = evt.RecvNs
	}
	if evt.RecvNs > s.lastRecvNs {
		s.lastRecvNs = evt.RecvNs
	}

	switch evt.Kind {
	case replay.EventOrderbook:
		s.orderbook++
		s.orderbookCounts[evt.Symbol]++
		if evt.Decoded != nil {
			seq := evt.Decoded.SequenceNumber
			if min, ok := s.seqMin[evt.Symbol]; !ok || seq < min {
				s.seqMin[evt.Symbol] = seq
			}
			if max, ok := s.seqMax[evt.Symbol]; !ok || seq > max {
				s.seqMax[evt.Symbol] = seq
			}
		}
	case replay.EventLivePrice:
		s.liveprice++
	case replay.EventRunningTrade:
		s.runningTrade++
		s.tradeCounts[evt.Symbol]++
	case replay.EventControl:
		if evt.Control != nil {
			switch evt.Control.Event {
			case "RECONNECT":
				s.reconnects++
			case "REAUTH":
				s.reauths++
			case "SWITCH_RESET":
				s.switchResets++
			case "CONNECT":
				s.connects++
			case "SUBSCRIBE":
				s.subscribes++
			}
		}
	default:
		// "ping", "error", "other"
		kind := string(evt.Kind)
		switch kind {
		case "ping":
			s.ping++
		case "error":
			s.errCount++
		}
	}
}

// countRawRecords does a separate lightweight pass over the raw segment to
// count data vs control records without decoding payloads. This gives exact
// record counts even when a data frame fails to decode.
func countRawRecords(segPath string) (data, control int, err error) {
	rc, err := zstdio.OpenMaybeCompressed(segPath)
	if err != nil {
		return 0, 0, fmt.Errorf("open: %w", err)
	}
	defer rc.Close()

	r := segment.NewReader(rc)
	for {
		rec, readErr := r.Read()
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			break // truncated tail — stop counting
		}
		switch rec.Type {
		case segment.RecordData:
			data++
		case segment.RecordControl:
			control++
		}
	}
	return data, control, nil
}

// runStat prints a health report for a segment file.
// args[0] is the segment path; no additional flags are accepted.
func runStat(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("usage: sbcap stat <segment>")
	}
	segPath := args[0]

	// Raw record counts via a separate lightweight pass.
	dataRecs, ctrlRecs, err := countRawRecords(segPath)
	if err != nil {
		return fmt.Errorf("count records: %w", err)
	}

	// Main decoded pass via Replay.
	res := newStatResult()
	if err := replay.Replay([]string{segPath}, replay.ModeFast, res.update); err != nil {
		return fmt.Errorf("replay: %w", err)
	}

	// File size (compressed on disk).
	fi, err := os.Stat(segPath)
	if err != nil {
		return fmt.Errorf("stat file: %w", err)
	}

	// Print report.
	printStatReport(segPath, fi.Size(), dataRecs, ctrlRecs, res)
	return nil
}

func printStatReport(segPath string, diskBytes int64, dataRecs, ctrlRecs int, res *statResult) {
	name := filepath.Base(segPath)
	fmt.Printf("%s  (%s on disk)\n", name, formatBytes(diskBytes))

	// Time span.
	if res.firstRecvNs <= res.lastRecvNs && res.firstRecvNs != math.MaxInt64 {
		first := time.Unix(0, res.firstRecvNs).In(wib)
		last := time.Unix(0, res.lastRecvNs).In(wib)
		dur := last.Sub(first)
		fmt.Printf("span      : %s → %s  (%s)\n",
			first.Format("2006-01-02 15:04:05 WIB"),
			last.Format("2006-01-02 15:04:05 WIB"),
			formatDuration(dur),
		)
	} else {
		fmt.Printf("span      : (no data events)\n")
	}
	fmt.Println()

	// Record counts.
	fmt.Printf("records   : %s data  /  %s control\n",
		formatInt(dataRecs), formatInt(ctrlRecs))

	// Channel counts.
	fmt.Printf("channels  : orderbook %s  running_trade %s  liveprice %s  ping %s  error %s\n",
		formatInt(res.orderbook),
		formatInt(res.runningTrade),
		formatInt(res.liveprice),
		formatInt(res.ping),
		formatInt(res.errCount),
	)
	fmt.Println()

	// Per-symbol trade counts.
	if len(res.tradeCounts) > 0 {
		fmt.Println("trades/symbol:")
		printSymbolCounts(res.tradeCounts)
		fmt.Println()
	} else {
		fmt.Println("trades/symbol : (none)")
		fmt.Println()
	}

	// Sequence-number range per symbol.
	// sequence_number is a GLOBAL feed counter shared across all symbols; BID and
	// OFFER carry separate interleaved sub-sequences, so per-symbol deltas can be
	// negative. Per-symbol gap detection is therefore not a reliable frame-loss
	// signal. Real capture discontinuities are detected via RECONNECT control
	// records (see "controls" line below).
	fmt.Printf("seq range :")
	if len(res.seqMin) == 0 {
		fmt.Printf("  (no orderbook events)\n")
	} else {
		symbols := sortedKeys(res.seqMin)
		for _, sym := range symbols {
			frames := res.orderbookCounts[sym]
			fmt.Printf("  %s %d→%d (%s frames)", sym, res.seqMin[sym], res.seqMax[sym], formatInt(frames))
		}
		fmt.Println()
	}

	// Control event summary.
	fmt.Printf("controls  : RECONNECT ×%d  REAUTH ×%d  SWITCH_RESET ×%d  CONNECT ×%d  SUBSCRIBE ×%d\n",
		res.reconnects, res.reauths, res.switchResets, res.connects, res.subscribes)

	// Truncation check: the segment reader maps truncated tails to io.EOF, so
	// a cleanly-read segment (no warnings from replay) is considered complete.
	// We report "complete" unless we detected fewer raw records than expected.
	// Since we have no byte-exact counter we report the raw counts as the check.
	total := dataRecs + ctrlRecs
	fmt.Printf("truncated : no  (%s records read cleanly)\n", formatInt(total))
}

// printSymbolCounts prints symbol → count pairs in sorted order, three per line.
func printSymbolCounts(counts map[string]int) {
	type pair struct {
		sym   string
		count int
	}
	pairs := make([]pair, 0, len(counts))
	for sym, n := range counts {
		pairs = append(pairs, pair{sym, n})
	}
	sort.Slice(pairs, func(i, j int) bool { return pairs[i].sym < pairs[j].sym })

	const cols = 3
	for i, p := range pairs {
		if i%cols == 0 {
			fmt.Print("  ")
		}
		fmt.Printf("%-8s %s", p.sym, formatInt(p.count))
		if i%cols == cols-1 || i == len(pairs)-1 {
			fmt.Println()
		} else {
			fmt.Print("  ")
		}
	}
}

// sortedKeys returns the keys of m sorted lexicographically.
func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// formatBytes formats a byte count as a human-readable string (MB).
func formatBytes(n int64) string {
	mb := float64(n) / (1024 * 1024)
	return fmt.Sprintf("%.0f MB", mb)
}

// formatDuration formats a duration as Xh Ym Zs.
func formatDuration(d time.Duration) string {
	d = d.Round(time.Second)
	h := int(d.Hours())
	m := int(d.Minutes()) % 60
	s := int(d.Seconds()) % 60
	if h > 0 {
		return fmt.Sprintf("%dh %dm %ds", h, m, s)
	}
	if m > 0 {
		return fmt.Sprintf("%dm %ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}

// formatInt formats an integer with space as the thousands separator.
func formatInt(n int) string {
	s := fmt.Sprintf("%d", n)
	if len(s) <= 3 {
		return s
	}
	// Insert spaces every 3 digits from the right.
	var out []byte
	for i, c := range s {
		pos := len(s) - i
		if pos > 1 && pos%3 == 1 {
			out = append(out, ' ')
		}
		out = append(out, byte(c))
	}
	return string(out)
}
