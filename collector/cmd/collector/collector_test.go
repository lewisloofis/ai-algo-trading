package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---------- watchlist tests ----------

func TestParseInlineWatchlist(t *testing.T) {
	tests := []struct {
		name    string
		raw     string
		want    []string
		wantErr bool
	}{
		{
			name: "basic csv",
			raw:  "BBCA,BMRI,TLKM",
			want: []string{"BBCA", "BMRI", "TLKM"},
		},
		{
			name: "lowercase uppercased",
			raw:  "bbca,bmri",
			want: []string{"BBCA", "BMRI"},
		},
		{
			name: "deduplication",
			raw:  "BBCA,BBCA,BMRI",
			want: []string{"BBCA", "BMRI"},
		},
		{
			name: "whitespace trimmed",
			raw:  " BBCA , BMRI ",
			want: []string{"BBCA", "BMRI"},
		},
		{
			name:    "empty string",
			raw:     "",
			wantErr: true,
		},
		{
			name:    "all whitespace",
			raw:     "  ,  ",
			wantErr: true,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := parseInlineWatchlist(tc.raw)
			if tc.wantErr {
				if err == nil {
					t.Errorf("expected error, got nil (symbols=%v)", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if len(got) != len(tc.want) {
				t.Fatalf("len mismatch: got %v, want %v", got, tc.want)
			}
			for i := range tc.want {
				if got[i] != tc.want[i] {
					t.Errorf("index %d: got %q, want %q", i, got[i], tc.want[i])
				}
			}
		})
	}
}

func TestParseWatchlistFile(t *testing.T) {
	tests := []struct {
		name    string
		content string
		want    []string
		wantErr bool
	}{
		{
			name:    "basic file",
			content: "BBCA\nBMRI\nTLKM\n",
			want:    []string{"BBCA", "BMRI", "TLKM"},
		},
		{
			name:    "comments and blank lines skipped",
			content: "# this is a comment\nBBCA\n\n# another comment\nBMRI\n",
			want:    []string{"BBCA", "BMRI"},
		},
		{
			name:    "lowercase uppercased",
			content: "bbca\nbmri\n",
			want:    []string{"BBCA", "BMRI"},
		},
		{
			name:    "deduplication across lines",
			content: "BBCA\nBBCA\nBMRI\n",
			want:    []string{"BBCA", "BMRI"},
		},
		{
			name:    "comment with leading whitespace",
			content: "  # leading-space comment\nBBCA\n",
			want:    []string{"BBCA"},
		},
		{
			name:    "empty file",
			content: "",
			wantErr: true,
		},
		{
			name:    "only comments",
			content: "# just a comment\n",
			wantErr: true,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			tmp := t.TempDir()
			path := filepath.Join(tmp, "watchlist.txt")
			if err := os.WriteFile(path, []byte(tc.content), 0644); err != nil {
				t.Fatalf("write temp file: %v", err)
			}

			got, err := parseWatchlistFile(path)
			if tc.wantErr {
				if err == nil {
					t.Errorf("expected error, got nil (symbols=%v)", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if len(got) != len(tc.want) {
				t.Fatalf("len mismatch: got %v, want %v", got, tc.want)
			}
			for i := range tc.want {
				if got[i] != tc.want[i] {
					t.Errorf("index %d: got %q, want %q", i, got[i], tc.want[i])
				}
			}
		})
	}
}

func TestLoadWatchlistEnv(t *testing.T) {
	t.Cleanup(func() {
		os.Unsetenv("CAPTURE_WATCHLIST")
		os.Unsetenv("CAPTURE_WATCHLIST_FILE")
	})

	t.Run("env var takes priority", func(t *testing.T) {
		os.Setenv("CAPTURE_WATCHLIST", "BBCA,BMRI")
		os.Unsetenv("CAPTURE_WATCHLIST_FILE")

		got, err := loadWatchlist()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(got) != 2 || got[0] != "BBCA" || got[1] != "BMRI" {
			t.Errorf("got %v, want [BBCA BMRI]", got)
		}
	})

	t.Run("file used when env unset", func(t *testing.T) {
		os.Unsetenv("CAPTURE_WATCHLIST")

		tmp := t.TempDir()
		path := filepath.Join(tmp, "wl.txt")
		os.WriteFile(path, []byte("TLKM\nASII\n"), 0644)
		os.Setenv("CAPTURE_WATCHLIST_FILE", path)

		got, err := loadWatchlist()
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(got) != 2 || got[0] != "TLKM" || got[1] != "ASII" {
			t.Errorf("got %v, want [TLKM ASII]", got)
		}
	})

	t.Run("error when neither is set", func(t *testing.T) {
		os.Unsetenv("CAPTURE_WATCHLIST")
		os.Unsetenv("CAPTURE_WATCHLIST_FILE")

		_, err := loadWatchlist()
		if err == nil {
			t.Error("expected error, got nil")
		}
	})
}

// ---------- manifest tests ----------

func TestBuildManifest(t *testing.T) {
	symbols := []string{"BBCA", "BMRI", "TLKM"}
	channels := []string{"orderBook", "liveprice"}

	start := time.Date(2026, 1, 15, 1, 45, 2, 0, time.UTC)
	end := time.Date(2026, 1, 15, 9, 15, 0, 0, time.UTC)

	p := manifestParams{
		watchlistID:       "default",
		symbols:           symbols,
		channels:          channels,
		segmentStart:      start,
		segmentEnd:        end,
		startNs:           start.UnixNano(),
		endNs:             end.UnixNano(),
		totalData:         847291,
		totalControl:      12,
		compressedBytes:   47382019,
		uncompressedBytes: 391847203,
	}

	m := buildManifest(p)

	if m.SchemaVersion != 1 {
		t.Errorf("schema_version: got %d, want 1", m.SchemaVersion)
	}
	if m.CollectorVersion != collectorVersion {
		t.Errorf("collector_version: got %q, want %q", m.CollectorVersion, collectorVersion)
	}
	if m.WatchlistID != "default" {
		t.Errorf("watchlist_id: got %q, want default", m.WatchlistID)
	}
	if len(m.Symbols) != 3 || m.Symbols[0] != "BBCA" {
		t.Errorf("symbols: got %v", m.Symbols)
	}
	if len(m.Channels) != 2 || m.Channels[0] != "orderBook" || m.Channels[1] != "liveprice" {
		t.Errorf("channels: got %v", m.Channels)
	}
	if m.TotalDataRecords != 847291 {
		t.Errorf("total_data_records: got %d, want 847291", m.TotalDataRecords)
	}
	if m.TotalControlRecords != 12 {
		t.Errorf("total_control_records: got %d, want 12", m.TotalControlRecords)
	}
	if m.CompressedSizeBytes != 47382019 {
		t.Errorf("compressed_size_bytes: got %d", m.CompressedSizeBytes)
	}
	if m.UncompressedSizeBytes != 391847203 {
		t.Errorf("uncompressed_size_bytes: got %d", m.UncompressedSizeBytes)
	}

	// Verify RFC3339Nano round-trips.
	parsedStart, err := time.Parse(time.RFC3339Nano, m.SegmentStartUTC)
	if err != nil {
		t.Fatalf("parse segment_start_utc: %v", err)
	}
	if !parsedStart.Equal(start) {
		t.Errorf("segment_start_utc mismatch: got %v, want %v", parsedStart, start)
	}

	parsedEnd, err := time.Parse(time.RFC3339Nano, m.SegmentEndUTC)
	if err != nil {
		t.Fatalf("parse segment_end_utc: %v", err)
	}
	if !parsedEnd.Equal(end) {
		t.Errorf("segment_end_utc mismatch: got %v, want %v", parsedEnd, end)
	}
}

func TestWriteManifestJSON(t *testing.T) {
	p := manifestParams{
		watchlistID:       "test",
		symbols:           []string{"BBCA"},
		channels:          []string{"orderBook", "liveprice"},
		segmentStart:      time.Date(2026, 1, 15, 1, 45, 0, 0, time.UTC),
		segmentEnd:        time.Date(2026, 1, 15, 9, 15, 0, 0, time.UTC),
		startNs:           1000000,
		endNs:             2000000,
		totalData:         100,
		totalControl:      5,
		compressedBytes:   1024,
		uncompressedBytes: 8192,
	}
	m := buildManifest(p)

	tmp := t.TempDir()
	path := filepath.Join(tmp, "seg-014500.manifest.json")
	if err := writeManifest(path, m); err != nil {
		t.Fatalf("writeManifest: %v", err)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}

	// Verify the JSON is valid and contains expected field names.
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("json.Unmarshal: %v", err)
	}

	requiredFields := []string{
		"schema_version",
		"collector_version",
		"watchlist_id",
		"symbols",
		"channels",
		"segment_start_utc",
		"segment_end_utc",
		"total_data_records",
		"total_control_records",
		"compressed_size_bytes",
		"uncompressed_size_bytes",
	}
	for _, field := range requiredFields {
		if _, ok := raw[field]; !ok {
			t.Errorf("manifest JSON missing field %q", field)
		}
	}

	if v, ok := raw["schema_version"].(float64); !ok || int(v) != 1 {
		t.Errorf("schema_version: got %v, want 1", raw["schema_version"])
	}
	if v, ok := raw["watchlist_id"].(string); !ok || v != "test" {
		t.Errorf("watchlist_id: got %v, want \"test\"", raw["watchlist_id"])
	}
}

func TestManifestSidecarPath(t *testing.T) {
	tests := []struct {
		zstPath string
		want    string
	}{
		{
			zstPath: "/segments/dt=2026-01-15/default/seg-014502.binpb.zst",
			want:    "/segments/dt=2026-01-15/default/seg-014502.manifest.json",
		},
		{
			// Fallback when suffix is not recognised.
			zstPath: "/segments/seg-014502.binpb",
			want:    "/segments/seg-014502.binpb.manifest.json",
		},
	}
	for _, tc := range tests {
		got := manifestSidecarPath(tc.zstPath)
		if got != tc.want {
			t.Errorf("manifestSidecarPath(%q) = %q, want %q", tc.zstPath, got, tc.want)
		}
	}
}
