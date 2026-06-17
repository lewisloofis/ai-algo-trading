package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

// manifest is the JSON schema for the segment sidecar, per
// docs/plans/02-storage-format-and-replay.md §5.
//
// Field names match the spec exactly so the JSON output is normative.
type manifest struct {
	SchemaVersion         int      `json:"schema_version"`
	CollectorVersion      string   `json:"collector_version"`
	WatchlistID           string   `json:"watchlist_id"`
	Symbols               []string `json:"symbols"`
	Channels              []string `json:"channels"`
	SegmentStartUTC       string   `json:"segment_start_utc"`
	SegmentEndUTC         string   `json:"segment_end_utc"`
	TotalDataRecords      int64    `json:"total_data_records"`
	TotalControlRecords   int64    `json:"total_control_records"`
	CompressedSizeBytes   int64    `json:"compressed_size_bytes"`
	UncompressedSizeBytes int64    `json:"uncompressed_size_bytes"`
}

// manifestParams groups the inputs needed to build a manifest value.
type manifestParams struct {
	watchlistID       string
	symbols           []string
	channels          []string
	segmentStart      time.Time
	segmentEnd        time.Time
	startNs           int64
	endNs             int64
	totalData         int64
	totalControl      int64
	compressedBytes   int64
	uncompressedBytes int64
}

// buildManifest constructs a manifest from the given parameters.
func buildManifest(p manifestParams) manifest {
	// Use actual first/last record timestamps when available; fall back to
	// the segment open time so the field is always non-zero.
	startUTC := p.segmentStart
	if p.startNs > 0 {
		startUTC = time.Unix(0, p.startNs).UTC()
	}
	endUTC := p.segmentEnd
	if p.endNs > 0 {
		endUTC = time.Unix(0, p.endNs).UTC()
	}

	return manifest{
		SchemaVersion:         1,
		CollectorVersion:      collectorVersion,
		WatchlistID:           p.watchlistID,
		Symbols:               p.symbols,
		Channels:              p.channels,
		SegmentStartUTC:       startUTC.Format(time.RFC3339Nano),
		SegmentEndUTC:         endUTC.Format(time.RFC3339Nano),
		TotalDataRecords:      p.totalData,
		TotalControlRecords:   p.totalControl,
		CompressedSizeBytes:   p.compressedBytes,
		UncompressedSizeBytes: p.uncompressedBytes,
	}
}

// writeManifest serialises m to JSON and writes it to path.
func writeManifest(path string, m manifest) error {
	data, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal manifest: %w", err)
	}
	if err := os.WriteFile(path, data, 0644); err != nil {
		return fmt.Errorf("write manifest %q: %w", path, err)
	}
	return nil
}
