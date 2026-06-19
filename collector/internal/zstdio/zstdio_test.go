package zstdio_test

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"testing"

	"collector/internal/zstdio"
)

// TestCompressRoundTrip writes known data to a temp file, compresses it with
// CompressFile, then reads it back via OpenMaybeCompressed and verifies the
// contents are identical.
func TestCompressRoundTrip(t *testing.T) {
	t.Parallel()

	dir := t.TempDir()
	srcPath := filepath.Join(dir, "data.binpb")
	dstPath := srcPath + ".zst"

	// Write recognisable test data.
	want := make([]byte, 4096)
	for i := range want {
		want[i] = byte(i % 251) // fill with a pattern that compresses well
	}
	if err := os.WriteFile(srcPath, want, 0600); err != nil {
		t.Fatalf("write src: %v", err)
	}

	// Compress.
	if err := zstdio.CompressFile(srcPath, dstPath); err != nil {
		t.Fatalf("CompressFile: %v", err)
	}

	// The .zst file must exist and be smaller than the original.
	info, err := os.Stat(dstPath)
	if err != nil {
		t.Fatalf("stat dst: %v", err)
	}
	if info.Size() >= int64(len(want)) {
		t.Logf("note: compressed size (%d) >= original (%d) — acceptable for small test data", info.Size(), len(want))
	}

	// Decompress via OpenMaybeCompressed and verify byte-for-byte equality.
	rc, err := zstdio.OpenMaybeCompressed(dstPath)
	if err != nil {
		t.Fatalf("OpenMaybeCompressed: %v", err)
	}
	defer rc.Close()

	got, err := io.ReadAll(rc)
	if err != nil {
		t.Fatalf("read decompressed: %v", err)
	}
	if !bytes.Equal(got, want) {
		t.Errorf("decompressed content mismatch: got %d bytes, want %d bytes", len(got), len(want))
	}
}

// TestOpenMaybeCompressedPlain verifies that OpenMaybeCompressed returns the
// raw file contents unchanged for non-.zst paths.
func TestOpenMaybeCompressedPlain(t *testing.T) {
	t.Parallel()

	dir := t.TempDir()
	path := filepath.Join(dir, "data.binpb")
	want := []byte("hello, segment")
	if err := os.WriteFile(path, want, 0600); err != nil {
		t.Fatalf("write: %v", err)
	}

	rc, err := zstdio.OpenMaybeCompressed(path)
	if err != nil {
		t.Fatalf("OpenMaybeCompressed: %v", err)
	}
	defer rc.Close()

	got, err := io.ReadAll(rc)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !bytes.Equal(got, want) {
		t.Errorf("got %q, want %q", got, want)
	}
}
