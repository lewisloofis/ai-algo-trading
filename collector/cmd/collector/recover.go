package main

import (
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"collector/internal/s3upload"
	"collector/segment"
	"collector/zstdio"
)

// recoverOrphans scans captureDir for leftover uncompressed segment files
// (*.binpb) from a previous run that crashed or was killed before the segment
// could be closed, compressed, and uploaded. Each orphan is compressed, given a
// manifest sidecar, uploaded (when an uploader is configured), and the
// uncompressed source removed — mirroring closeAndCompress for the normal path.
//
// Recovery is best-effort and must never block startup: per-file errors are
// logged and skipped so one bad file cannot stop the collector from capturing
// the current session.
func recoverOrphans(captureDir string, up *s3upload.Uploader, symbols, channels []string) {
	orphans, err := findOrphans(captureDir)
	if err != nil {
		log.Printf("[recover] scan %q failed: %v", captureDir, err)
		return
	}
	if len(orphans) == 0 {
		return
	}
	log.Printf("[recover] found %d orphan segment(s) from a previous run", len(orphans))
	for _, path := range orphans {
		if err := recoverOrphan(path, captureDir, up, symbols, channels); err != nil {
			log.Printf("[recover] %s: %v", filepath.Base(path), err)
		}
	}
}

// findOrphans returns the paths of all uncompressed *.binpb segment files under
// captureDir. Already-compressed *.binpb.zst files are excluded (their suffix is
// ".zst", not ".binpb"). A missing captureDir is not an error — there is simply
// nothing to recover.
func findOrphans(captureDir string) ([]string, error) {
	var out []string
	err := filepath.Walk(captureDir, func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if !info.IsDir() && strings.HasSuffix(path, ".binpb") {
			out = append(out, path)
		}
		return nil
	})
	if os.IsNotExist(err) {
		return nil, nil
	}
	return out, err
}

// recoverOrphan recovers a single orphan .binpb file. It mirrors the order of
// closeAndCompress: compress, remove the uncompressed source, write the
// manifest, then upload (leaving local files in place on upload failure).
func recoverOrphan(path, captureDir string, up *s3upload.Uploader, symbols, channels []string) error {
	zstPath := path + ".zst"
	if _, err := os.Stat(zstPath); err == nil {
		// A compressed counterpart already exists: the previous run got as far as
		// compressing. Leave both files in place for manual review rather than
		// risk deleting data we cannot positively account for.
		return fmt.Errorf("compressed counterpart %s already exists — leaving for manual review", filepath.Base(zstPath))
	}

	watchlistID, openTime, ok := parseSegmentPath(captureDir, path)
	if !ok {
		return fmt.Errorf("unrecognized segment path layout — leaving in place")
	}

	dataRecords, controlRecords, startNs, endNs, cerr := countSegment(path)
	if cerr != nil {
		// A genuine read failure (not mere truncation, which Reader treats as EOF).
		// Recover the records counted before the failure rather than discard all.
		log.Printf("[recover] %s: partial read (%v) — recovering %d data record(s) up to the failure",
			filepath.Base(path), cerr, dataRecords)
	}

	uncompressedBytes := fileSize(path)
	if err := zstdio.CompressFile(path, zstPath); err != nil {
		return fmt.Errorf("compress: %w", err)
	}
	compressedBytes := fileSize(zstPath)

	// Remove the uncompressed source now that compression succeeded (matches the
	// normal close path, where the .binpb is dropped before upload).
	if err := os.Remove(path); err != nil {
		log.Printf("[recover] remove uncompressed %s: %v", filepath.Base(path), err)
	}

	m := buildManifest(manifestParams{
		watchlistID:       watchlistID,
		symbols:           symbols,
		channels:          channels,
		segmentStart:      openTime,
		segmentEnd:        time.Unix(0, endNs).UTC(),
		startNs:           startNs,
		endNs:             endNs,
		totalData:         dataRecords,
		totalControl:      controlRecords,
		compressedBytes:   compressedBytes,
		uncompressedBytes: uncompressedBytes,
	})
	manifestPath := manifestSidecarPath(zstPath)
	if err := writeManifest(manifestPath, m); err != nil {
		return fmt.Errorf("write manifest: %w", err)
	}

	if up != nil {
		if err := up.UploadSegment(zstPath, manifestPath, watchlistID, openTime); err != nil {
			return fmt.Errorf("upload (local files kept): %w", err)
		}
		log.Printf("[recover] uploaded %s and manifest to S3", filepath.Base(zstPath))
	}

	log.Printf("[recover] recovered %s (data=%d ctrl=%d unc=%d cmp=%d)",
		filepath.Base(zstPath), dataRecords, controlRecords, uncompressedBytes, compressedBytes)
	return nil
}

// countSegment reads an uncompressed segment file and tallies data/control
// records plus the first and last record timestamps. A truncated trailing
// record (common after a crash) is reported by Reader as a clean io.EOF, so the
// returned error is non-nil only on a genuine read failure; the counts gathered
// before that point are still returned.
func countSegment(path string) (dataRecords, controlRecords, startNs, endNs int64, err error) {
	f, oerr := os.Open(path)
	if oerr != nil {
		return 0, 0, 0, 0, oerr
	}
	defer f.Close()

	r := segment.NewReader(f)
	for {
		rec, rerr := r.Read()
		if rerr == io.EOF {
			return dataRecords, controlRecords, startNs, endNs, nil
		}
		if rerr != nil {
			return dataRecords, controlRecords, startNs, endNs, rerr
		}
		if startNs == 0 {
			startNs = rec.TimestampNs
		}
		endNs = rec.TimestampNs
		switch rec.Type {
		case segment.RecordData:
			dataRecords++
		case segment.RecordControl:
			controlRecords++
		}
	}
}

// parseSegmentPath extracts the watchlist id and UTC open time from a segment
// path of the form:
//
//	<captureDir>/dt=<YYYY-MM-DD>/<watchlistID>/seg-<HHMMSS>.binpb
//
// The open time is parsed from the dt= date and the seg- filename so a recovered
// segment lands in its original date partition rather than today's. ok is false
// when the path does not match this layout.
func parseSegmentPath(captureDir, path string) (watchlistID string, openTime time.Time, ok bool) {
	rel, err := filepath.Rel(captureDir, path)
	if err != nil {
		return "", time.Time{}, false
	}
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) != 3 {
		return "", time.Time{}, false
	}
	datePart, wid, file := parts[0], parts[1], parts[2]
	if !strings.HasPrefix(datePart, "dt=") {
		return "", time.Time{}, false
	}
	if !strings.HasPrefix(file, "seg-") || !strings.HasSuffix(file, ".binpb") {
		return "", time.Time{}, false
	}
	dateStr := strings.TrimPrefix(datePart, "dt=")
	timeStr := strings.TrimSuffix(strings.TrimPrefix(file, "seg-"), ".binpb")
	t, err := time.ParseInLocation("2006-01-02 150405", dateStr+" "+timeStr, time.UTC)
	if err != nil {
		return "", time.Time{}, false
	}
	return wid, t, true
}
