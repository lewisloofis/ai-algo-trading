# scripts/

Operational scripts for the market-data collector. Not part of the research
pipeline (`research/`) or the collector binary itself (`collector/`).

## sync-segments.sh

Pulls today's captured segments from S3 down to the local `segments/` tree
via `aws s3 sync`. Requires the `lewis` AWS CLI profile.

## collector_healthcheck.py

Deployment self-checks for the collector (roadmap W1.3,
`docs/plans/00-roadmap-research-to-live.md` §6 Wave 1). Answers two
questions that were previously invisible and both failed silently once
already: *is the binary actually deployed fresh*, and *is the running
session actually recording trade data*.

Two checks, both read-only against S3 (`head_object` / `get_object` /
`list_objects_v2` only — no writes):

1. **Binary freshness.** Cross-compiles the collector for linux/arm64 from
   the current checkout, hashes it, and compares against the deployed S3
   artifact (`infra/terraform.tfvars` → `collector_artifact_s3_uri`). Go
   builds are not bit-for-bit reproducible across toolchain/environment
   differences, so a hash mismatch is reported for context only and never
   scored — the artifact's **age** (`LastModified`) is the authoritative
   staleness signal. An artifact older than `STALE_ARTIFACT_DAYS` (14, a
   named constant in the script) is a warning regardless of hash agreement.

2. **Zero-record session.** For each IDX trading day in a trailing
   `LOOKBACK_DAYS` window (30, holidays and weekends excluded via
   `research/lib/market_calendar.py` — the shared calendar, not
   reimplemented here), verifies segments exist in S3 and carry a
   non-trivial `total_data_records` count, and that a non-trivial share of
   those records come from manifests whose `channels` include
   `runningTradeBatch`. This catches the real 2026-06-17 `default` session:
   47,728 total records looked like healthy coverage, but only 58 of them
   came from a `runningTradeBatch`-subscribed manifest — the rest was
   orderBook/liveprice only.

### Usage

```bash
set -a && . ./.env.local >/dev/null 2>&1 && set +a
research/.venv/bin/python scripts/collector_healthcheck.py           # human report
research/.venv/bin/python scripts/collector_healthcheck.py --json    # machine-readable
```

Credentials come from the environment (`.env.local`, sourced by the caller —
the script never reads that file itself). Region is fixed to
`ap-southeast-3`. Prefers `boto3` if importable, otherwise shells out to the
`aws` CLI with explicit argv (no `shell=True`).

Exit codes: `0` all clear, `1` warnings only, `2` any failure (including a
local setup problem such as missing credentials or a missing
`infra/terraform.tfvars`).

### Tests

```bash
research/.venv/bin/python -m pytest scripts/test_collector_healthcheck.py -q
```

Pure unit tests against a fake, in-memory S3 client — no network, no real
AWS, no real `go build`.
