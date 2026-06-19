# ADR 0005 — Suspend S3 bucket versioning to cap storage cost

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** infra, cost, storage

> Backfilled to record a decision already implemented in `infra/s3.tf`
> (`aws_s3_bucket_versioning.marketdata`) during the 2026-06-17 activation session;
> see commit `ad50898`.

## Context

The S3 bucket `marketdata-<account-id>` was initially created with versioning
**Enabled**. This accumulates noncurrent object versions for every PUT or DELETE,
billing storage for each retained version indefinitely — a meaningful cost drag
for a write-heavy collector that uploads a new compressed segment every hour.

The objects in this bucket are write-once by convention: segment names encode
wall-clock time (`seg-<HHMMSS>.binpb.zst` under `raw/dt=<YYYY-MM-DD>/<watchlist>/`,
as specified in [ADR-0002](./0002-segment-storage-and-compression.md)). Because
the key is derived from the segment's open timestamp, a collector running normally
never overwrites an existing key. Versioning therefore provided little protective
value while silently accumulating cost for any noncurrent versions created during
early experimentation when versioning was still Enabled.

An additional AWS constraint shapes the decision: once versioning has ever been
enabled on a bucket, it cannot be removed — only **Suspended**. A suspended bucket
stops creating new versions on future PUTs while leaving existing noncurrent
versions in place (until a lifecycle rule removes them).

Rejected alternative considered: keep versioning Enabled and add a lifecycle rule
to expire noncurrent versions after N days. This achieves the same cost bound but
keeps versioning machinery active (delete markers, noncurrent-version accounting,
more moving lifecycle parts) for data that is immutable-by-key and therefore gains
nothing from version history.

## Decision

Set `aws_s3_bucket_versioning.marketdata` to **`Suspended`**.

In addition, add a `noncurrent_version_expiration` rule (1 day) inside the existing
`raw-lifecycle` lifecycle configuration. This rule cleans up any noncurrent versions
that accumulated while versioning was Enabled, preventing ongoing storage charges
for those historical copies. Future PUTs under the Suspended state create no new
noncurrent versions.

The provider lock file (`infra/.terraform.lock.hcl`) was refreshed in the same
commit to keep dependency pins current.

## Consequences

**Positive**

- No new noncurrent versions are created; S3 storage charges track only the live
  object set.
- The 1-day noncurrent-version expiry purges any legacy versions from the Enabled
  era within one day of apply, eliminating lingering storage overhang.
- Configuration is simple: one `status = "Suspended"` line plus one lifecycle clause,
  both declarative Terraform. No Lambda, no external scheduler.

**Negative / costs**

- An accidental overwrite or delete of a segment key is no longer recoverable via a
  prior S3 version. This is acceptable because:
  1. Keys are immutable by naming convention — normal operation never reuses a key.
  2. Uploaded segments are also retained locally (the `.zst` file is only removed
     from disk after a confirmed upload), providing a secondary recovery path.
  3. The raw data can be re-derived from other already-uploaded segments if needed.
- The bucket cannot be returned to a "never versioned" state; Suspended is a
  permanent mode once any versioning has been enabled. This is an AWS platform
  constraint, not a reversible configuration choice.

## Related

- [ADR-0002 — segment storage & compression lifecycle](./0002-segment-storage-and-compression.md)
  — defines the immutable-key naming convention (`seg-<HHMMSS>.binpb.zst`) that
  makes versioning redundant.
- `infra/s3.tf` — `aws_s3_bucket_versioning.marketdata` and
  `aws_s3_bucket_lifecycle_configuration.marketdata` (implementation).
