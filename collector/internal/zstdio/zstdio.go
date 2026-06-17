// Package zstdio provides thin wrappers around github.com/klauspost/compress/zstd
// for compressing segment files and transparently decompressing them during
// replay. It is the single point in the codebase that imports the zstd package.
package zstdio

import (
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/klauspost/compress/zstd"
)

// CompressFile reads srcPath and writes a zstd-compressed copy to dstPath.
// It streams the data so that very large segment files (hundreds of MB) are
// handled without loading them into memory.
func CompressFile(srcPath, dstPath string) error {
	src, err := os.Open(srcPath)
	if err != nil {
		return fmt.Errorf("zstdio: open source %q: %w", srcPath, err)
	}
	defer src.Close()

	dst, err := os.Create(dstPath)
	if err != nil {
		return fmt.Errorf("zstdio: create dest %q: %w", dstPath, err)
	}

	enc, err := zstd.NewWriter(dst)
	if err != nil {
		dst.Close()
		os.Remove(dstPath)
		return fmt.Errorf("zstdio: create encoder: %w", err)
	}

	if _, err := io.Copy(enc, src); err != nil {
		enc.Close()
		dst.Close()
		os.Remove(dstPath)
		return fmt.Errorf("zstdio: compress: %w", err)
	}
	if err := enc.Close(); err != nil {
		dst.Close()
		os.Remove(dstPath)
		return fmt.Errorf("zstdio: close encoder: %w", err)
	}
	if err := dst.Close(); err != nil {
		return fmt.Errorf("zstdio: close dest: %w", err)
	}
	return nil
}

// zstdReadCloser wraps a zstd decoder so that Close also closes the underlying
// file, ensuring both handles are released when the caller is done.
type zstdReadCloser struct {
	dec  *zstd.Decoder
	file *os.File
}

func (z *zstdReadCloser) Read(p []byte) (int, error) { return z.dec.Read(p) }

func (z *zstdReadCloser) Close() error {
	z.dec.Close()
	return z.file.Close()
}

// OpenMaybeCompressed opens path for reading. If the path ends in ".zst" the
// returned ReadCloser transparently decompresses the stream; otherwise it
// returns a plain *os.File. In both cases the caller must call Close() to
// release resources.
func OpenMaybeCompressed(path string) (io.ReadCloser, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("zstdio: open %q: %w", path, err)
	}
	if !strings.HasSuffix(path, ".zst") {
		return f, nil
	}
	dec, err := zstd.NewReader(f)
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("zstdio: create decoder for %q: %w", path, err)
	}
	return &zstdReadCloser{dec: dec, file: f}, nil
}
