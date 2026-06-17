package main

import (
	"bufio"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"sb-algo-prototype/internal/segment"
	"sb-algo-prototype/internal/zstdio"
)

// segmentFile manages the currently open segment: the backing *os.File, the
// segment.Writer layered on top, and per-segment counters for the manifest.
//
// All exported methods are safe to call from the capture goroutine; roll is
// called from the midnight-roll watcher.
type segmentFile struct {
	mu          sync.Mutex
	captureDir  string
	watchlistID string

	// current segment state
	file        *os.File
	sw          *segment.Writer
	bw          *bufio.Writer // kept so we can Flush before sw.Flush
	openPath    string
	openTime    time.Time

	// per-segment counters
	dataRecords    int64
	controlRecords int64
	startNs        int64 // UnixNano of first record written; 0 if nothing written yet
	endNs          int64 // UnixNano of most recent record written
}

// newSegmentFile creates a segmentFile but does not open any file yet.
// Call open() to start the first segment.
func newSegmentFile(captureDir, watchlistID string) *segmentFile {
	return &segmentFile{
		captureDir:  captureDir,
		watchlistID: watchlistID,
	}
}

// open creates the directory hierarchy and opens a new segment file.
// The segment name encodes the UTC open time as seg-HHMMSS.binpb.
//
// Layout: <captureDir>/dt=<YYYY-MM-DD>/<watchlistID>/seg-<HHMMSS>.binpb
func (sf *segmentFile) open(t time.Time) error {
	sf.mu.Lock()
	defer sf.mu.Unlock()

	utc := t.UTC()
	dateStr := utc.Format("2006-01-02")
	timeStr := utc.Format("150405")

	dir := filepath.Join(sf.captureDir, "dt="+dateStr, sf.watchlistID)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("create segment dir %q: %w", dir, err)
	}

	name := fmt.Sprintf("seg-%s.binpb", timeStr)
	path := filepath.Join(dir, name)

	f, err := os.Create(path)
	if err != nil {
		return fmt.Errorf("create segment file %q: %w", path, err)
	}

	// bufio.Writer under segment.Writer; segment.NewWriter wraps with its own
	// 8 KiB buffer, so we add a larger outer buffer for file I/O efficiency.
	bw := bufio.NewWriterSize(f, 256<<10) // 256 KiB outer buffer
	sw := segment.NewWriter(bw)

	sf.file = f
	sf.bw = bw
	sf.sw = sw
	sf.openPath = path
	sf.openTime = utc
	sf.dataRecords = 0
	sf.controlRecords = 0
	sf.startNs = 0
	sf.endNs = 0

	log.Printf("[segment] opened %s", path)
	return nil
}

// WriteData writes a data record and updates counters. Thread-safe.
func (sf *segmentFile) WriteData(recvNs int64, frame []byte) error {
	sf.mu.Lock()
	defer sf.mu.Unlock()
	if sf.sw == nil {
		return fmt.Errorf("no open segment")
	}
	if err := sf.sw.WriteData(recvNs, frame); err != nil {
		return err
	}
	sf.dataRecords++
	sf.trackTime(recvNs)
	return nil
}

// WriteControl writes a control record and updates counters. Thread-safe.
func (sf *segmentFile) WriteControl(eventNs int64, jsonPayload []byte) error {
	sf.mu.Lock()
	defer sf.mu.Unlock()
	if sf.sw == nil {
		return fmt.Errorf("no open segment")
	}
	if err := sf.sw.WriteControl(eventNs, jsonPayload); err != nil {
		return err
	}
	sf.controlRecords++
	sf.trackTime(eventNs)
	return nil
}

// trackTime must be called with sf.mu held.
func (sf *segmentFile) trackTime(ns int64) {
	if sf.startNs == 0 {
		sf.startNs = ns
	}
	sf.endNs = ns
}

// closeAndCompress flushes, closes, compresses, and writes the manifest for
// the current segment. The uncompressed .binpb is removed on success.
// It returns the path to the compressed file and the manifest path.
//
// This is intended to be called from a goroutine (roll or shutdown).
func (sf *segmentFile) closeAndCompress(symbols []string, channels []string) {
	sf.mu.Lock()
	if sf.sw == nil {
		sf.mu.Unlock()
		return
	}

	// Capture state before releasing the lock.
	sw := sf.sw
	bw := sf.bw
	f := sf.file
	openPath := sf.openPath
	openTime := sf.openTime
	dataRecords := sf.dataRecords
	controlRecords := sf.controlRecords
	startNs := sf.startNs
	endNs := sf.endNs

	// Clear the active segment so new writes are rejected until open() is called.
	sf.sw = nil
	sf.bw = nil
	sf.file = nil
	sf.openPath = ""
	sf.mu.Unlock()

	// Flush and close outside the lock — I/O can be slow.
	if err := sw.Flush(); err != nil {
		log.Printf("[segment] flush error for %s: %v", openPath, err)
	}
	if err := bw.Flush(); err != nil {
		log.Printf("[segment] outer buffer flush error for %s: %v", openPath, err)
	}
	if err := f.Close(); err != nil {
		log.Printf("[segment] close error for %s: %v", openPath, err)
	}

	// Uncompressed size before compression.
	uncompressedBytes := fileSize(openPath)

	// Compress to .binpb.zst.
	zstPath := openPath + ".zst"
	if err := zstdio.CompressFile(openPath, zstPath); err != nil {
		log.Printf("[segment] compress error for %s: %v", openPath, err)
		return
	}

	compressedBytes := fileSize(zstPath)

	// Remove the uncompressed file now that compression succeeded.
	if err := os.Remove(openPath); err != nil {
		log.Printf("[segment] remove uncompressed error for %s: %v", openPath, err)
	}

	// Write the manifest sidecar.
	m := buildManifest(manifestParams{
		watchlistID:        sf.watchlistID,
		symbols:            symbols,
		channels:           channels,
		segmentStart:       openTime,
		segmentEnd:         time.Unix(0, endNs).UTC(),
		startNs:            startNs,
		endNs:              endNs,
		totalData:          dataRecords,
		totalControl:       controlRecords,
		compressedBytes:    compressedBytes,
		uncompressedBytes:  uncompressedBytes,
	})
	manifestPath := manifestSidecarPath(zstPath)
	if err := writeManifest(manifestPath, m); err != nil {
		log.Printf("[segment] write manifest error for %s: %v", manifestPath, err)
		return
	}

	log.Printf("[segment] closed and compressed %s (data=%d ctrl=%d unc=%d cmp=%d)",
		zstPath, dataRecords, controlRecords, uncompressedBytes, compressedBytes)
}

// fileSize returns the file size in bytes, or 0 on error.
func fileSize(path string) int64 {
	fi, err := os.Stat(path)
	if err != nil {
		return 0
	}
	return fi.Size()
}

// manifestSidecarPath converts seg-HHMMSS.binpb.zst to
// seg-HHMMSS.manifest.json by replacing the .binpb.zst suffix.
func manifestSidecarPath(zstPath string) string {
	const suffix = ".binpb.zst"
	if len(zstPath) > len(suffix) && zstPath[len(zstPath)-len(suffix):] == suffix {
		return zstPath[:len(zstPath)-len(suffix)] + ".manifest.json"
	}
	return zstPath + ".manifest.json"
}
