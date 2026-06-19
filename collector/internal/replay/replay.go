// Package replay reads market-data segment files and emits a MarketEvent
// stream to a caller-supplied handler. It is the backbone of both backtesting
// (as-fast-as-possible mode) and realistic simulation (event-time paced mode).
//
// Design reference: docs/plans/02-storage-format-and-replay.md §6.
package replay

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"time"

	"collector/internal/feed"
	"collector/internal/segment"
	"collector/internal/zstdio"
)

// EventKind identifies the category of a MarketEvent.
type EventKind string

const (
	// EventLivePrice is emitted for "liveprice" frames.
	EventLivePrice EventKind = "liveprice"
	// EventOrderbook is emitted for "orderbook" frames.
	EventOrderbook EventKind = "orderbook"
	// EventRunningTrade is emitted for "running_trade" frames.
	EventRunningTrade EventKind = "running_trade"
	// EventIEPIEV is emitted for "iepiev" frames (indicative equilibrium price/volume).
	EventIEPIEV EventKind = "iepiev"
	// EventControl is emitted for segment control records (CONNECT, SUBSCRIBE,
	// REAUTH, RECONNECT, SWITCH_RESET).
	EventControl EventKind = "control"
)

// PacingMode controls how the replayer spaces events in time.
type PacingMode int

const (
	// ModeFast emits events as quickly as the CPU can decode them. RecvNs is
	// still set from the recorded timestamp; the wall clock is not consulted.
	// This is the default and is suitable for batch backtesting.
	ModeFast PacingMode = iota
	// ModePaced sleeps between events to honour the original inter-arrival
	// gaps recorded in RecvNs, capped at 5 seconds to skip long silences.
	// Useful for visual inspection or realism testing.
	ModePaced
)

// maxPaceGap is the longest inter-event sleep in ModePaced. Gaps longer than
// this (mid-day breaks, weekends) are skipped rather than replayed in real time.
const maxPaceGap = 5 * time.Second

// MarketEvent is one decoded event from the replay stream.
type MarketEvent struct {
	RecvNs  int64 // arrival timestamp (ns since Unix epoch)
	Kind    EventKind
	Symbol  string                // empty for control events
	Decoded *feed.Decoded         // non-nil for data events (liveprice, orderbook, running_trade)
	Control *segment.ControlEvent // non-nil for EventControl
}

// Replay reads each segment path in order, decodes every record, and calls
// handler for each resulting MarketEvent. Segment paths may be uncompressed
// (.binpb) or zstd-compressed (.binpb.zst).
//
// On a data record, DecodeWrap is called on the payload; decode errors are
// logged as warnings and the record is skipped. On a control record, the JSON
// payload is unmarshalled into a ControlEvent.
//
// In ModePaced the replayer sleeps the inter-arrival gap before emitting each
// event, capped at maxPaceGap (5 s) to avoid replaying long silences. In
// ModeFast no sleeping occurs.
func Replay(segments []string, mode PacingMode, handler func(MarketEvent)) error {
	var prevNs int64

	for _, segPath := range segments {
		if err := replaySegment(segPath, mode, &prevNs, handler); err != nil {
			return fmt.Errorf("replay segment %s: %w", segPath, err)
		}
	}
	return nil
}

// replaySegment opens one segment file and emits its events.
//
// For running_trade records that carry a batch of N trades, the handler is
// called once per trade (N times total).  Pacing sleeps once per raw record,
// not once per trade, so a burst of trades does not slow replay.
func replaySegment(path string, mode PacingMode, prevNs *int64, handler func(MarketEvent)) error {
	rc, err := openSegment(path)
	if err != nil {
		return err
	}
	defer rc.Close()

	r := segment.NewReader(rc)
	for {
		rec, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			// A non-EOF error from segment.Reader should not normally occur
			// (the reader maps truncation to io.EOF), but handle defensively.
			log.Printf("replay: warn: read error in %s: %v", path, err)
			break
		}

		evts, ok := decodeRecord(rec, path)
		if !ok {
			continue
		}

		if mode == ModePaced && *prevNs > 0 {
			gap := time.Duration(evts[0].RecvNs - *prevNs)
			if gap > 0 && gap < maxPaceGap {
				time.Sleep(gap)
			}
		}
		*prevNs = evts[0].RecvNs
		for _, evt := range evts {
			handler(evt)
		}
	}
	return nil
}

// decodeRecord converts a raw segment.Record into one or more MarketEvents.
// Returns (nil, false) if the record should be skipped.
//
// For running_trade records carrying a batch of N trades, N events are
// returned — one per trade. For all other record types exactly one event is
// returned.
func decodeRecord(rec segment.Record, segPath string) ([]MarketEvent, bool) {
	switch rec.Type {
	case segment.RecordData:
		d, err := feed.DecodeWrap(rec.Payload)
		if err != nil {
			log.Printf("replay: warn: decode error in %s: %v", segPath, err)
			return nil, false
		}
		kind := dataKind(d.Kind)

		// Expand running_trade batches: emit one event per trade so that
		// per-symbol counters and dump output work naturally.
		if kind == EventRunningTrade && len(d.Trades) > 0 {
			evts := make([]MarketEvent, 0, len(d.Trades))
			for i := range d.Trades {
				tr := d.Trades[i]
				single := feed.Decoded{
					Kind:   "running_trade",
					Symbol: tr.Symbol,
					Trades: []feed.Trade{tr},
				}
				evts = append(evts, MarketEvent{
					RecvNs:  rec.TimestampNs,
					Kind:    EventRunningTrade,
					Symbol:  tr.Symbol,
					Decoded: &single,
				})
			}
			if len(evts) > 0 {
				return evts, true
			}
			// Batch was empty — skip.
			return nil, false
		}

		return []MarketEvent{{
			RecvNs:  rec.TimestampNs,
			Kind:    kind,
			Symbol:  d.Symbol,
			Decoded: &d,
		}}, true

	case segment.RecordControl:
		var ctrl segment.ControlEvent
		if err := json.Unmarshal(rec.Payload, &ctrl); err != nil {
			log.Printf("replay: warn: control unmarshal error in %s: %v", segPath, err)
			return nil, false
		}
		return []MarketEvent{{
			RecvNs:  rec.TimestampNs,
			Kind:    EventControl,
			Control: &ctrl,
		}}, true

	default:
		log.Printf("replay: warn: unknown record type 0x%02x in %s", rec.Type, segPath)
		return nil, false
	}
}

// dataKind maps a feed.Decoded.Kind string to the EventKind enum. "other"
// and "ping" frames are preserved as-is (callers may inspect evt.Decoded.Kind).
func dataKind(kind string) EventKind {
	switch kind {
	case "liveprice":
		return EventLivePrice
	case "orderbook":
		return EventOrderbook
	case "running_trade":
		return EventRunningTrade
	case "iepiev":
		return EventIEPIEV
	default:
		// "ping", "error", "other" — keep the raw string as EventKind so
		// callers can still filter on evt.Kind if needed.
		return EventKind(kind)
	}
}

// openSegment opens a segment file for reading. Files ending in ".zst" are
// transparently decompressed; all others are returned as plain *os.File.
func openSegment(path string) (io.ReadCloser, error) {
	rc, err := zstdio.OpenMaybeCompressed(path)
	if err != nil {
		return nil, fmt.Errorf("open segment: %w", err)
	}
	return rc, nil
}
