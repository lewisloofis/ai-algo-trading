package s3upload_test

import (
	"testing"
	"time"

	"collector/internal/s3upload"
)

// TestSegmentKey verifies the S3 key format for segment files and manifests.
func TestSegmentKey(t *testing.T) {
	t.Parallel()

	date := time.Date(2026, 1, 15, 1, 45, 2, 0, time.UTC)

	tests := []struct {
		name        string
		watchlistID string
		localPath   string
		wantKey     string
	}{
		{
			name:        "compressed segment",
			watchlistID: "default",
			localPath:   "/segments/dt=2026-01-15/default/seg-014502.binpb.zst",
			wantKey:     "raw/dt=2026-01-15/default/seg-014502.binpb.zst",
		},
		{
			name:        "manifest sidecar",
			watchlistID: "default",
			localPath:   "/segments/dt=2026-01-15/default/seg-014502.manifest.json",
			wantKey:     "raw/dt=2026-01-15/default/seg-014502.manifest.json",
		},
		{
			name:        "different watchlist",
			watchlistID: "bluechips-30",
			localPath:   "/var/data/seg-090000.binpb.zst",
			wantKey:     "raw/dt=2026-01-15/bluechips-30/seg-090000.binpb.zst",
		},
	}

	for _, tc := range tests {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			got := s3upload.SegmentKey(tc.watchlistID, date, tc.localPath)
			if got != tc.wantKey {
				t.Errorf("SegmentKey: got %q want %q", got, tc.wantKey)
			}
		})
	}
}

// TestSegmentKeyUsesUTCDate verifies that SegmentKey always uses the UTC date
// of the provided time, regardless of local timezone.
func TestSegmentKeyUsesUTCDate(t *testing.T) {
	t.Parallel()

	// 23:30 UTC on Jan 14 = 06:30 WIB Jan 15
	late := time.Date(2026, 1, 14, 23, 30, 0, 0, time.UTC)
	got := s3upload.SegmentKey("default", late, "/seg-233000.binpb.zst")
	if got != "raw/dt=2026-01-14/default/seg-233000.binpb.zst" {
		t.Errorf("unexpected key: %q", got)
	}
}
