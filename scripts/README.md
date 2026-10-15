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

## collector_livecheck.py

LIVE check of the collector on EC2 (today's capture only reaches S3 at midnight/shutdown, so
`collector_healthcheck.py` cannot confirm the current session). Finds the instance by Name tag
`sb-algo-trading-prod-collector`, runs a fixed read-only script via SSM `AWS-RunShellScript`
(systemd state/restarts, process VmRSS, today's in-progress segment count/bytes/newest-write age,
disk free, last 60 journal lines redacted) and gives an OK/WARN/FAIL verdict. In IDX session
hours (trading day, 09:00-16:00 WIB) OK needs: unit active, newest segment write < 5 min, and a
`[capture] N frames / ...` log line with N>0 in the last 2 min (`frames total` resets per
reconnect and is not used). Outside session hours it reports state only. A stopped instance
outside 08:30-16:25 WIB / on non-trading days is OK ("stopped by schedule", exit 0,
`infra/scheduler.tf`); stopped inside that window is FAIL.

```bash
make collector-live AWS_PROFILE=lewis              # or: python3 scripts/collector_livecheck.py --profile lewis
make collector-live AWS_PROFILE=lewis ARGS=--json
```

Credentials: `--profile`, else `AWS_PROFILE`, else boto3's default chain; the script never reads
`~/.aws/credentials` itself or prints key values. Exit: 0 OK, 1 WARN, 2 FAIL/auth/SSM error.
Needs `ec2:DescribeInstances`, `ssm:SendCommand` (document `AWS-RunShellScript` + the instance) and
`ssm:GetCommandInvocation`, and the SSM agent online (the instance profile already attaches
AmazonSSMManagedInstanceCore). Tests: `python3 -m pytest scripts/test_collector_livecheck.py -q`.
