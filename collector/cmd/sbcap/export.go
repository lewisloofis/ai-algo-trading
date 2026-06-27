package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"collector/feed"
	"collector/replay"
)

// exportRecord is the flat schema emitted for every decoded event.
// JSON tags are snake_case for jq-friendliness; CSV columns follow the same order.
type exportRecord struct {
	RecvNs         int64   `json:"recv_ns"`
	ISOTime        string  `json:"iso_time"`
	Kind           string  `json:"kind"`
	Symbol         string  `json:"symbol"`
	LastPrice      float64 `json:"last_price"`
	SequenceNumber int64   `json:"sequence_number"`
	SwitchReset    int     `json:"switch_reset"`
	Side           string  `json:"side"`
	TopPrice       float64 `json:"top_price"`
	TopVolume      float64 `json:"top_volume"`
	LevelCount     int     `json:"level_count"`
	// Trade fields (populated when kind == "running_trade")
	TradePrice   float64 `json:"trade_price,omitempty"`
	TradeVolume  float64 `json:"trade_volume,omitempty"`
	TradeSide    string  `json:"trade_side,omitempty"`
	TradeNumber  int32   `json:"trade_number,omitempty"`
	ErrCode      int     `json:"err_code,omitempty"`
	ErrMsg       string  `json:"err_msg,omitempty"`
	ControlEvent string  `json:"control_event,omitempty"`
}

// csvHeader is the column order for CSV output. Must match toStringSlice.
var csvHeader = []string{
	"recv_ns", "iso_time", "kind", "symbol",
	"last_price", "sequence_number", "switch_reset",
	"side", "top_price", "top_volume", "level_count",
	"trade_price", "trade_volume", "trade_side", "trade_number",
	"err_code", "err_msg", "control_event",
}

func (r exportRecord) toStringSlice() []string {
	return []string{
		fmt.Sprintf("%d", r.RecvNs),
		r.ISOTime,
		r.Kind,
		r.Symbol,
		fmt.Sprintf("%g", r.LastPrice),
		fmt.Sprintf("%d", r.SequenceNumber),
		fmt.Sprintf("%d", r.SwitchReset),
		r.Side,
		fmt.Sprintf("%g", r.TopPrice),
		fmt.Sprintf("%g", r.TopVolume),
		fmt.Sprintf("%d", r.LevelCount),
		fmt.Sprintf("%g", r.TradePrice),
		fmt.Sprintf("%g", r.TradeVolume),
		r.TradeSide,
		fmt.Sprintf("%d", r.TradeNumber),
		fmt.Sprintf("%d", r.ErrCode),
		r.ErrMsg,
		r.ControlEvent,
	}
}

// runExport emits all decoded events as NDJSON or CSV.
//
// Flags:
//
//	--format ndjson|csv  (required)
//
// Flags may appear before or after the segment path.
func runExport(args []string) error {
	fs := flag.NewFlagSet("export", flag.ContinueOnError)
	formatFlag := fs.String("format", "", "output format: ndjson or csv")

	segPath, flagArgs, err := extractPositionalArg(args)
	if err != nil {
		return fmt.Errorf("usage: sbcap export <segment> --format ndjson|csv")
	}
	if err := fs.Parse(flagArgs); err != nil {
		return err
	}

	switch strings.ToLower(*formatFlag) {
	case "ndjson":
		return exportNDJSON(segPath)
	case "csv":
		return exportCSV(segPath)
	default:
		return fmt.Errorf("--format must be ndjson or csv (got %q)", *formatFlag)
	}
}

// exportNDJSON writes one JSON object per line to stdout.
func exportNDJSON(segPath string) error {
	enc := json.NewEncoder(os.Stdout)
	return replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		rec := buildExportRecord(evt)
		_ = enc.Encode(rec) // errors writing to stdout are not recoverable
	})
}

// exportCSV writes a header row followed by one CSV row per event to stdout.
func exportCSV(segPath string) error {
	w := csv.NewWriter(os.Stdout)
	if err := w.Write(csvHeader); err != nil {
		return fmt.Errorf("write csv header: %w", err)
	}

	err := replay.Replay([]string{segPath}, replay.ModeFast, func(evt replay.MarketEvent) {
		rec := buildExportRecord(evt)
		_ = w.Write(rec.toStringSlice())
	})
	w.Flush()
	if err != nil {
		return err
	}
	return w.Error()
}

// buildExportRecord converts a MarketEvent into a flat exportRecord.
func buildExportRecord(evt replay.MarketEvent) exportRecord {
	ts := time.Unix(0, evt.RecvNs).In(wib)
	rec := exportRecord{
		RecvNs:  evt.RecvNs,
		ISOTime: ts.Format(time.RFC3339Nano),
		Kind:    string(evt.Kind),
		Symbol:  evt.Symbol,
	}

	if evt.Decoded != nil {
		d := evt.Decoded
		rec.LastPrice = d.LastPrice
		rec.SequenceNumber = d.SequenceNumber
		rec.SwitchReset = d.SwitchReset
		rec.ErrCode = d.ErrCode
		rec.ErrMsg = d.ErrMsg

		// For orderbook events, parse the body to extract side and top level.
		if evt.Kind == replay.EventOrderbook && d.Body != "" {
			if bs, ok := feed.ParseOrderbookBody(d.Body); ok {
				rec.Side = bs.Side
				rec.LevelCount = len(bs.Levels)
				if len(bs.Levels) > 0 {
					rec.TopPrice = bs.Levels[0].Price
					rec.TopVolume = bs.Levels[0].Volume
				}
			}
		}

		// For running_trade events, populate trade-specific fields.
		if evt.Kind == replay.EventRunningTrade && len(d.Trades) > 0 {
			tr := d.Trades[0]
			rec.TradePrice = tr.Price
			rec.TradeVolume = tr.Volume
			rec.TradeSide = tr.Side
			rec.TradeNumber = tr.TradeNumber
		}
	}

	if evt.Control != nil {
		rec.ControlEvent = evt.Control.Event
	}

	return rec
}
