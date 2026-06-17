package s3upload

// Internal tests for UploadSegment: happy path, retry-then-succeed, and
// exhaust-all-attempts. Uses package-internal access to newUploaderWithClient
// and retryBaseBackoff (set to 0 so tests run instantly).

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func init() {
	// Zero out backoff globally for all tests in this package so retries are
	// instantaneous. This is safe because _test.go files are not included in
	// production builds.
	retryBaseBackoff = 0
}

// fakePutObject records each PutObject call and optionally returns an error
// for the first N calls.
type fakePutObject struct {
	failFirst int32 // fail this many calls before succeeding
	calls     int32 // total PutObject calls made
	puts      []putRecord
}

type putRecord struct {
	bucket string
	key    string
}

func (f *fakePutObject) PutObject(_ context.Context, in *s3.PutObjectInput, _ ...func(*s3.Options)) (*s3.PutObjectOutput, error) {
	n := atomic.AddInt32(&f.calls, 1)
	if n <= atomic.LoadInt32(&f.failFirst) {
		return nil, errors.New("simulated PutObject failure")
	}
	f.puts = append(f.puts, putRecord{
		bucket: aws.ToString(in.Bucket),
		key:    aws.ToString(in.Key),
	})
	return &s3.PutObjectOutput{}, nil
}

// writeTempFile creates a small temporary file and returns its path. The file
// is removed when the test ends.
func writeTempFile(t *testing.T, name, content string) string {
	t.Helper()
	f, err := os.CreateTemp(t.TempDir(), name)
	if err != nil {
		t.Fatalf("create temp file: %v", err)
	}
	if _, err := fmt.Fprint(f, content); err != nil {
		t.Fatalf("write temp file: %v", err)
	}
	f.Close()
	return f.Name()
}

// TestUploadSegmentHappyPath verifies that UploadSegment issues exactly two
// PutObject calls — one for the .binpb.zst and one for the .manifest.json —
// using the correct bucket and key values.
func TestUploadSegmentHappyPath(t *testing.T) {
	t.Parallel()

	fake := &fakePutObject{}
	up := newUploaderWithClient(fake, "my-bucket")

	date := time.Date(2026, 1, 15, 1, 45, 2, 0, time.UTC)
	zstPath := writeTempFile(t, "seg-014502.binpb.zst", "zst-content")
	manifestPath := writeTempFile(t, "seg-014502.manifest.json", `{"schema_version":1}`)

	if err := up.UploadSegment(zstPath, manifestPath, "default", date); err != nil {
		t.Fatalf("UploadSegment: %v", err)
	}

	if int(fake.calls) != 2 {
		t.Fatalf("PutObject call count: got %d want 2", fake.calls)
	}

	wantZstKey := SegmentKey("default", date, zstPath)
	wantManifestKey := SegmentKey("default", date, manifestPath)

	if fake.puts[0].bucket != "my-bucket" {
		t.Errorf("puts[0].bucket: got %q want my-bucket", fake.puts[0].bucket)
	}
	if fake.puts[0].key != wantZstKey {
		t.Errorf("puts[0].key: got %q want %q", fake.puts[0].key, wantZstKey)
	}
	if fake.puts[1].bucket != "my-bucket" {
		t.Errorf("puts[1].bucket: got %q want my-bucket", fake.puts[1].bucket)
	}
	if fake.puts[1].key != wantManifestKey {
		t.Errorf("puts[1].key: got %q want %q", fake.puts[1].key, wantManifestKey)
	}
}

// TestUploadSegmentRetrySucceeds verifies that a transient failure on the
// first attempt is retried and ultimately succeeds.
func TestUploadSegmentRetrySucceeds(t *testing.T) {
	t.Parallel()

	// Fail the first 1 PutObject call, then succeed.
	fake := &fakePutObject{failFirst: 1}
	up := newUploaderWithClient(fake, "retry-bucket")

	date := time.Date(2026, 1, 15, 0, 0, 0, 0, time.UTC)
	zstPath := writeTempFile(t, "seg-000000.binpb.zst", "data")
	manifestPath := writeTempFile(t, "seg-000000.manifest.json", "{}")

	if err := up.UploadSegment(zstPath, manifestPath, "default", date); err != nil {
		t.Fatalf("UploadSegment expected success after retry, got: %v", err)
	}

	// 1 failed attempt + 1 success for zst = 2 calls for zst,
	// then 1 success for manifest = 3 total.
	if int(fake.calls) != 3 {
		t.Errorf("PutObject call count: got %d want 3 (1 fail + 1 success for zst, 1 success for manifest)", fake.calls)
	}
}

// TestUploadSegmentExhaustsAttempts verifies that when all 3 attempts fail,
// UploadSegment returns an error and PutObject was called exactly 3 times.
func TestUploadSegmentExhaustsAttempts(t *testing.T) {
	t.Parallel()

	// Always fail.
	fake := &fakePutObject{failFirst: 100}
	up := newUploaderWithClient(fake, "fail-bucket")

	date := time.Date(2026, 1, 15, 0, 0, 0, 0, time.UTC)
	zstPath := writeTempFile(t, "seg-000000.binpb.zst", "data")
	manifestPath := writeTempFile(t, "seg-000000.manifest.json", "{}")

	err := up.UploadSegment(zstPath, manifestPath, "default", date)
	if err == nil {
		t.Fatal("UploadSegment expected error when all attempts fail, got nil")
	}

	// Only the zst upload is attempted; after 3 failures it short-circuits and
	// never reaches the manifest upload.
	const maxAttempts = 3
	if int(fake.calls) != maxAttempts {
		t.Errorf("PutObject call count: got %d want %d", fake.calls, maxAttempts)
	}
}

// TestUploadSegmentRetryTwoFails verifies the mid-range case: 2 failures then
// success on the third (final) attempt.
func TestUploadSegmentRetryTwoFails(t *testing.T) {
	t.Parallel()

	// Fail first 2 calls for the zst file, succeed on the 3rd.
	fake := &fakePutObject{failFirst: 2}
	up := newUploaderWithClient(fake, "bucket")

	date := time.Date(2026, 1, 15, 0, 0, 0, 0, time.UTC)
	zstPath := writeTempFile(t, "seg-000000.binpb.zst", "data")
	manifestPath := writeTempFile(t, "seg-000000.manifest.json", "{}")

	if err := up.UploadSegment(zstPath, manifestPath, "default", date); err != nil {
		t.Fatalf("UploadSegment expected success on 3rd attempt, got: %v", err)
	}

	// 2 failures + 1 success for zst, then 1 success for manifest = 4 total.
	if int(fake.calls) != 4 {
		t.Errorf("PutObject call count: got %d want 4 (2 fail + 1 success for zst + 1 for manifest)", fake.calls)
	}
}
