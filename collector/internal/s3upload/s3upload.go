// Package s3upload uploads compressed segment files and their manifest
// sidecars to S3 after a segment roll. Uploads are gated behind the S3_BUCKET
// environment variable — when unset, upload is skipped entirely.
//
// The S3 key layout follows docs/plans/02-storage-format-and-replay.md §4:
//
//	s3://<bucket>/raw/dt=<YYYY-MM-DD>/<watchlist-id>/seg-<HHMMSS>.binpb.zst
//	s3://<bucket>/raw/dt=<YYYY-MM-DD>/<watchlist-id>/seg-<HHMMSS>.manifest.json
package s3upload

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

// s3API is the subset of the S3 client used here. Defined as an interface so
// the key-building and retry logic can be unit-tested with a fake.
type s3API interface {
	PutObject(ctx context.Context, in *s3.PutObjectInput, opts ...func(*s3.Options)) (*s3.PutObjectOutput, error)
}

// Uploader uploads segment files to S3.
type Uploader struct {
	client s3API
	bucket string
}

// NewUploader constructs an Uploader using the default AWS configuration.
// It must only be called when S3_BUCKET is set and non-empty.
func NewUploader(bucket string) (*Uploader, error) {
	cfg, err := config.LoadDefaultConfig(context.Background())
	if err != nil {
		return nil, fmt.Errorf("s3 uploader: load aws config: %w", err)
	}
	return &Uploader{
		client: s3.NewFromConfig(cfg),
		bucket: bucket,
	}, nil
}

// newUploaderWithClient creates an Uploader with an injected client for
// testing.
func newUploaderWithClient(client s3API, bucket string) *Uploader {
	return &Uploader{client: client, bucket: bucket}
}

// SegmentKey returns the S3 key for a compressed segment file.
//
// Layout: raw/dt=<YYYY-MM-DD>/<watchlistID>/<filename>
//
// localPath must be an absolute path; only its base name is used in the key.
// date is the UTC calendar date the segment belongs to.
func SegmentKey(watchlistID string, date time.Time, localPath string) string {
	dateStr := date.UTC().Format("2006-01-02")
	return fmt.Sprintf("raw/dt=%s/%s/%s", dateStr, watchlistID, filepath.Base(localPath))
}

// UploadSegment uploads the compressed segment file and its manifest sidecar
// to S3 with up to 3 attempts and exponential backoff. On persistent failure it
// logs and returns an error; the caller should leave local files in place.
//
// watchlistID and date identify the S3 prefix partition. manifestPath is the
// sidecar JSON written alongside the .binpb.zst file.
func (u *Uploader) UploadSegment(zstPath, manifestPath, watchlistID string, date time.Time) error {
	if err := u.uploadFileWithRetry(zstPath, SegmentKey(watchlistID, date, zstPath)); err != nil {
		return fmt.Errorf("upload segment %s: %w", zstPath, err)
	}
	if err := u.uploadFileWithRetry(manifestPath, SegmentKey(watchlistID, date, manifestPath)); err != nil {
		return fmt.Errorf("upload manifest %s: %w", manifestPath, err)
	}
	return nil
}

// retryBaseBackoff is the starting sleep between upload attempts. Tests set
// this to 0 to avoid real sleeping; production uses 1 second.
var retryBaseBackoff = time.Second

// uploadFileWithRetry uploads one local file to S3 with 3-attempt exponential
// backoff (base retryBaseBackoff, doubling on each retry).
func (u *Uploader) uploadFileWithRetry(localPath, key string) error {
	const maxAttempts = 3
	backoff := retryBaseBackoff

	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if err := u.uploadFile(localPath, key); err == nil {
			return nil
		} else {
			lastErr = err
		}
		if attempt < maxAttempts {
			time.Sleep(backoff)
			backoff *= 2
		}
	}
	return fmt.Errorf("upload failed after %d attempts: %w", maxAttempts, lastErr)
}

// uploadFile performs a single PutObject call for one local file.
func (u *Uploader) uploadFile(localPath, key string) error {
	f, err := os.Open(localPath)
	if err != nil {
		return fmt.Errorf("open %s: %w", localPath, err)
	}
	defer f.Close()

	fi, err := f.Stat()
	if err != nil {
		return fmt.Errorf("stat %s: %w", localPath, err)
	}

	_, err = u.client.PutObject(context.Background(), &s3.PutObjectInput{
		Bucket:        aws.String(u.bucket),
		Key:           aws.String(key),
		Body:          f,
		ContentLength: aws.Int64(fi.Size()),
	})
	return err
}
