package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"collector/internal/segment"
)

// writeOrphanSegment writes a valid uncompressed segment file with the given
// number of data and control records, returning its path.
func writeOrphanSegment(t *testing.T, captureDir, date, watchlistID, segName string, dataRecs, ctrlRecs int) string {
	t.Helper()
	dir := filepath.Join(captureDir, "dt="+date, watchlistID)
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	path := filepath.Join(dir, segName)
	f, err := os.Create(path)
	if err != nil {
		t.Fatalf("create: %v", err)
	}
	w := segment.NewWriter(f)
	ns := int64(1_000_000)
	for i := 0; i < dataRecs; i++ {
		if err := w.WriteData(ns, []byte{0x0a, 0x0b, 0x0c}); err != nil {
			t.Fatalf("write data: %v", err)
		}
		ns += 1000
	}
	for i := 0; i < ctrlRecs; i++ {
		payload, _ := segment.ReauthEvent("proactive")
		if err := w.WriteControl(ns, payload); err != nil {
			t.Fatalf("write control: %v", err)
		}
		ns += 1000
	}
	if err := w.Flush(); err != nil {
		t.Fatalf("flush: %v", err)
	}
	if err := f.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	return path
}

func TestRecoverOrphansCompressesAndRemoves(t *testing.T) {
	tmp := t.TempDir()
	path := writeOrphanSegment(t, tmp, "2026-06-18", "mining-sample", "seg-013634.binpb", 7, 2)

	recoverOrphans(tmp, nil, []string{"BUMI", "DEWA"}, []string{"orderBook", "iepiev"})

	// The uncompressed orphan must be gone.
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Errorf("orphan .binpb still present (err=%v), want removed", err)
	}
	// A compressed segment must exist.
	zstPath := path + ".zst"
	if _, err := os.Stat(zstPath); err != nil {
		t.Errorf("compressed segment missing: %v", err)
	}
	// A manifest sidecar must exist with the correct counts and watchlist id.
	manifestPath := manifestSidecarPath(zstPath)
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		t.Fatalf("read manifest: %v", err)
	}
	var m map[string]interface{}
	if err := json.Unmarshal(data, &m); err != nil {
		t.Fatalf("unmarshal manifest: %v", err)
	}
	if v, _ := m["total_data_records"].(float64); int(v) != 7 {
		t.Errorf("total_data_records: got %v, want 7", m["total_data_records"])
	}
	if v, _ := m["total_control_records"].(float64); int(v) != 2 {
		t.Errorf("total_control_records: got %v, want 2", m["total_control_records"])
	}
	if v, _ := m["watchlist_id"].(string); v != "mining-sample" {
		t.Errorf("watchlist_id: got %v, want mining-sample", m["watchlist_id"])
	}
}

func TestRecoverOrphansSkipsWhenCompressedExists(t *testing.T) {
	tmp := t.TempDir()
	path := writeOrphanSegment(t, tmp, "2026-06-18", "mining-sample", "seg-013634.binpb", 3, 0)
	// Simulate a prior compress that finished but did not remove the source.
	zstPath := path + ".zst"
	if err := os.WriteFile(zstPath, []byte("preexisting"), 0644); err != nil {
		t.Fatalf("write preexisting zst: %v", err)
	}

	recoverOrphans(tmp, nil, []string{"BUMI"}, []string{"orderBook"})

	// Both files must be left untouched for manual review.
	if _, err := os.Stat(path); err != nil {
		t.Errorf("orphan .binpb should be left in place, got: %v", err)
	}
	if got, _ := os.ReadFile(zstPath); string(got) != "preexisting" {
		t.Errorf("preexisting .zst was overwritten: %q", got)
	}
}

func TestRecoverOrphansIgnoresCompressedOnlyFiles(t *testing.T) {
	tmp := t.TempDir()
	dir := filepath.Join(tmp, "dt=2026-06-18", "mining-sample")
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	zstOnly := filepath.Join(dir, "seg-013634.binpb.zst")
	if err := os.WriteFile(zstOnly, []byte("done"), 0644); err != nil {
		t.Fatalf("write: %v", err)
	}

	orphans, err := findOrphans(tmp)
	if err != nil {
		t.Fatalf("findOrphans: %v", err)
	}
	if len(orphans) != 0 {
		t.Errorf("findOrphans picked up a compressed-only segment: %v", orphans)
	}
}

func TestRecoverOrphansMissingDirIsNoop(t *testing.T) {
	orphans, err := findOrphans(filepath.Join(t.TempDir(), "does-not-exist"))
	if err != nil {
		t.Fatalf("findOrphans on missing dir: %v", err)
	}
	if len(orphans) != 0 {
		t.Errorf("expected no orphans, got %v", orphans)
	}
}

func TestParseSegmentPath(t *testing.T) {
	captureDir := "/var/lib/collector/segments"
	tests := []struct {
		name     string
		path     string
		wantWID  string
		wantTime time.Time
		wantOK   bool
	}{
		{
			name:     "valid path",
			path:     captureDir + "/dt=2026-06-18/mining-sample/seg-013634.binpb",
			wantWID:  "mining-sample",
			wantTime: time.Date(2026, 6, 18, 1, 36, 34, 0, time.UTC),
			wantOK:   true,
		},
		{
			name:   "missing dt prefix",
			path:   captureDir + "/2026-06-18/mining-sample/seg-013634.binpb",
			wantOK: false,
		},
		{
			name:   "wrong filename prefix",
			path:   captureDir + "/dt=2026-06-18/mining-sample/data-013634.binpb",
			wantOK: false,
		},
		{
			name:   "too few path components",
			path:   captureDir + "/dt=2026-06-18/seg-013634.binpb",
			wantOK: false,
		},
		{
			name:   "bad time in filename",
			path:   captureDir + "/dt=2026-06-18/mining-sample/seg-99XX99.binpb",
			wantOK: false,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			wid, ot, ok := parseSegmentPath(captureDir, tc.path)
			if ok != tc.wantOK {
				t.Fatalf("ok: got %v, want %v", ok, tc.wantOK)
			}
			if !tc.wantOK {
				return
			}
			if wid != tc.wantWID {
				t.Errorf("watchlistID: got %q, want %q", wid, tc.wantWID)
			}
			if !ot.Equal(tc.wantTime) {
				t.Errorf("openTime: got %v, want %v", ot, tc.wantTime)
			}
		})
	}
}

func TestNextRollBoundary(t *testing.T) {
	tests := []struct {
		name     string
		now      time.Time
		interval time.Duration
		want     time.Time
	}{
		{
			name:     "mid-hour rolls to top of next hour",
			now:      time.Date(2026, 6, 18, 1, 36, 34, 0, time.UTC),
			interval: time.Hour,
			want:     time.Date(2026, 6, 18, 2, 0, 0, 0, time.UTC),
		},
		{
			name:     "exact boundary advances to next",
			now:      time.Date(2026, 6, 18, 2, 0, 0, 0, time.UTC),
			interval: time.Hour,
			want:     time.Date(2026, 6, 18, 3, 0, 0, 0, time.UTC),
		},
		{
			name:     "crosses midnight to new date partition",
			now:      time.Date(2026, 6, 18, 23, 45, 0, 0, time.UTC),
			interval: time.Hour,
			want:     time.Date(2026, 6, 19, 0, 0, 0, 0, time.UTC),
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := nextRollBoundary(tc.now, tc.interval)
			if !got.Equal(tc.want) {
				t.Errorf("nextRollBoundary(%v, %v): got %v, want %v", tc.now, tc.interval, got, tc.want)
			}
		})
	}
}
