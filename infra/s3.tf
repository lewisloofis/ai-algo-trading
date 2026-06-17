# §7.4 / §12.3 — S3 bucket with versioning, SSE-S3, public-access-block, and lifecycle
# Bucket name includes account ID to guarantee global uniqueness.

resource "aws_s3_bucket" "marketdata" {
  bucket = "sb-marketdata-${data.aws_caller_identity.current.account_id}"
}

# Enable versioning — noncurrent-version expiry handled in lifecycle below.
resource "aws_s3_bucket_versioning" "marketdata" {
  bucket = aws_s3_bucket.marketdata.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 (AES-256) — free, no KMS cost at this data volume.
resource "aws_s3_bucket_server_side_encryption_configuration" "marketdata" {
  bucket = aws_s3_bucket.marketdata.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block all public access — this bucket is private.
resource "aws_s3_bucket_public_access_block" "marketdata" {
  bucket = aws_s3_bucket.marketdata.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle policy on raw/ prefix (§7.4 / §4 of 02-storage-format-and-replay.md):
#   0–7 days   → Standard (fast access for recent backtests)
#   8–30 days  → Standard-IA (~45% saving, millisecond access)
#   31+ days   → Glacier Deep Archive (~$0.00099/GB-month cold archive)
# Additional rules:
#   abort incomplete multipart uploads after 7 days (cost hygiene)
#   expire noncurrent versions after 30 days (versioning cost cap)
resource "aws_s3_bucket_lifecycle_configuration" "marketdata" {
  bucket = aws_s3_bucket.marketdata.id

  # Depend on versioning being enabled before applying lifecycle rules.
  depends_on = [aws_s3_bucket_versioning.marketdata]

  rule {
    id     = "raw-lifecycle"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = 7
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 30
      storage_class = "DEEP_ARCHIVE"
    }

    # Abort incomplete multipart uploads — avoids orphaned MPU storage charges.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    # Expire old versions after 30 days to keep versioning costs under control.
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}
