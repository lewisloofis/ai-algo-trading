#!/usr/bin/env python3
"""
scripts/collector_healthcheck.py — deployment self-checks for the market-data
collector (roadmap W1.3, docs/plans/00-roadmap-research-to-live.md §6 Wave 1).

Two silent failures motivated this: a deployed binary ~2.5 months behind
`main`, and a capture session that recorded nothing. Neither was detectable
before this script existed. It runs two read-only checks against S3 (via
scripts/s3_readonly.py — head_object, get_object, and list_objects_v2 only):

  1. Binary freshness — build the collector for linux/arm64 from the current
     checkout, hash it, and compare against the deployed S3 artifact. Go
     builds are not bit-for-bit reproducible across toolchain/env
     differences, so a hash mismatch alone is NOT treated as proof of
     staleness — only the artifact's *age* (LastModified) is authoritative
     for that verdict. The hash is reported for context, never scored.

  2. Zero-record session — for each IDX trading day in a lookback window,
     verify segments exist and carry a non-trivial number of records, AND
     that a non-trivial share of those records come from manifests that
     subscribed to the `runningTradeBatch` channel. A real prior incident
     (2026-06-17 `default` session) had healthy-looking total record counts
     that were almost entirely orderBook/liveprice, with a negligible
     runningTradeBatch contribution — "looks like coverage but carries no
     trade data."

Credentials are read from the ambient environment (this script never reads
`.env.local` itself — the caller sources it). No credential value is ever
printed.

This script prints progress as it works (build status, S3 backend choice,
one line per trading day) — a check that can run for minutes must never look
indistinguishable from a hang. See scripts/s3_readonly.py for why the aws
CLI fallback path in particular is much slower than the boto3 path.

Usage:
    python scripts/collector_healthcheck.py [--json]

Exit codes: 0 = all clear, 1 = warnings, 2 = failures (or a setup error).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

# lib/market_calendar.py lives under research/, a sibling of scripts/. Reuse
# it rather than reimplementing IDX holiday rules (see calendar/idx_holidays.json).
sys.path.insert(0, str(REPO_ROOT / "research"))
from lib.market_calendar import CALENDAR, MarketCalendar  # noqa: E402

from s3_readonly import (  # noqa: E402
    S3AuthError,
    S3Client,
    S3Error,
    build_s3_client,
)

# ---------------------------------------------------------------------------
# Config discovery
# ---------------------------------------------------------------------------

TERRAFORM_TFVARS = REPO_ROOT / "infra" / "terraform.tfvars"
COLLECTOR_MODULE_DIR = REPO_ROOT / "collector"
COLLECTOR_MAIN_PKG = "./cmd/collector"

# infra/variables.tf default for aws_region ("Jakarta for lowest latency to
# Stockbit servers"). Passed literally to both the boto3 client and the aws
# CLI's --region flag.
AWS_REGION = "ap-southeast-3"

# infra/s3.tf raw-lifecycle prefix. Local segments/dt=.../<watchlist_id>/...
# mirrors this exactly (infra/user_data.sh.tftpl comment).
S3_RAW_PREFIX = "raw"

RUNNING_TRADE_CHANNEL = "runningTradeBatch"

# ---------------------------------------------------------------------------
# Thresholds — every one a named constant, none a bare number in the logic.
# ---------------------------------------------------------------------------

# Go builds are not reproducible bit-for-bit across toolchain/OS versions, so
# a hash mismatch is only ever reported, never scored. Age is the
# authoritative signal: an artifact older than this is flagged as a
# WARNING regardless of hash agreement.
STALE_ARTIFACT_DAYS: int = 14

# How many trailing calendar days to scan for the zero-record check. Trading
# days are filtered out of this window via CALENDAR; today is excluded
# because its session may still be in progress.
LOOKBACK_DAYS: int = 30

# The EC2 schedule stops the collector at 16:25 WIB, and shutdown flushes and
# uploads the day's segments. From this WIB time on, today's session is
# complete in S3 and is scanned too.
TODAY_COMPLETE_AFTER_WIB = time(16, 45)
WIB = timezone(timedelta(hours=7))

# A trading day's segments summed across all watchlists must clear this many
# total_data_records or the day is flagged as near-zero. Real healthy days
# run 300k-480k records; this floor sits far below that, high enough only to
# catch a session that captured almost nothing.
MIN_DAILY_DATA_RECORDS: int = 1_000

# Of a day's total_data_records, the portion attributable to manifests whose
# `channels` list includes runningTradeBatch must also clear this floor.
# This is what catches "coverage without trade data": the 2026-06-17
# `default` session had 47,728 total_data_records but only 58 of them came
# from runningTradeBatch-subscribed manifests.
MIN_DAILY_TRADE_RECORDS: int = 1_000

# Subprocess timeouts.
BUILD_TIMEOUT_SECONDS: int = 180

SEVERITY_WARNING = "WARNING"
SEVERITY_FAILURE = "FAILURE"

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_FAILURE = 2

_ARTIFACT_URI_RE = re.compile(r'collector_artifact_s3_uri\s*=\s*"(s3://[^"]+)"')

# No-op default for every `progress` parameter below, so callers (and every
# existing test) that don't care about live progress don't have to pass one.
_NO_PROGRESS: Callable[[str], None] = lambda _msg: None  # noqa: E731


class ConfigError(RuntimeError):
    """Raised for local configuration problems (not an AWS error)."""


def parse_artifact_s3_uri(tfvars_path: Path) -> str:
    """Extract collector_artifact_s3_uri from infra/terraform.tfvars.

    That file is gitignored (per its own header) and local to each checkout,
    so a missing file or missing key is a configuration problem, not a bug.
    """
    if not tfvars_path.exists():
        raise ConfigError(
            f"{tfvars_path} not found; set COLLECTOR_ARTIFACT_S3_URI in the "
            "environment or pass --bucket/--artifact-key explicitly."
        )
    text = tfvars_path.read_text()
    match = _ARTIFACT_URI_RE.search(text)
    if not match:
        raise ConfigError(f"collector_artifact_s3_uri not found in {tfvars_path}")
    return match.group(1)


def split_s3_uri(uri: str) -> tuple[str, str]:
    """Split an s3://bucket/key URI into (bucket, key)."""
    if not uri.startswith("s3://"):
        raise ConfigError(f"not an s3:// URI: {uri}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ConfigError(f"malformed S3 URI: {uri}")
    return bucket, key


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    severity: str  # SEVERITY_WARNING or SEVERITY_FAILURE
    check: str
    message: str


def _exit_code_for(findings: Sequence[Finding]) -> int:
    if any(f.severity == SEVERITY_FAILURE for f in findings):
        return EXIT_FAILURE
    if any(f.severity == SEVERITY_WARNING for f in findings):
        return EXIT_WARNING
    return EXIT_OK


# ---------------------------------------------------------------------------
# Check 1 — binary freshness
# ---------------------------------------------------------------------------


def get_git_head_info(repo_root: Path = REPO_ROOT) -> tuple[str, bool]:
    """Return (short SHA, is_dirty) for the current checkout."""
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root), capture_output=True, text=True, check=True,
    ).stdout
    return sha, bool(status.strip())


def build_local_collector_binary() -> tuple[Optional[bytes], Optional[str]]:
    """Cross-compile the collector for linux/arm64 from the current checkout.

    Returns (binary_bytes, error_message) — exactly one of the two is None.
    A failure here (no Go toolchain, compile error) degrades the freshness
    check to age-only rather than aborting the whole script.
    """
    with tempfile.TemporaryDirectory(prefix="collector-healthcheck-build-") as tmp:
        out_path = Path(tmp) / "collector"
        env = dict(os.environ, GOOS="linux", GOARCH="arm64", CGO_ENABLED="0")
        try:
            subprocess.run(
                ["go", "build", "-o", str(out_path), COLLECTOR_MAIN_PKG],
                cwd=str(COLLECTOR_MODULE_DIR),
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=BUILD_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return None, "go toolchain not found on PATH"
        except subprocess.TimeoutExpired:
            return None, f"go build timed out after {BUILD_TIMEOUT_SECONDS}s"
        except subprocess.CalledProcessError as exc:
            return None, f"go build failed: {exc.stderr.strip()[:2000]}"
        return out_path.read_bytes(), None


def evaluate_binary_freshness(
    s3: S3Client,
    bucket: str,
    artifact_key: str,
    local_bytes: Optional[bytes],
    build_error: Optional[str],
    git_sha: str,
    git_dirty: bool,
    now: datetime,
    stale_days: int = STALE_ARTIFACT_DAYS,
    progress: Optional[Callable[[str], None]] = None,
) -> tuple[list[Finding], dict]:
    """Pure(ish) evaluation given an S3Client and already-gathered local state.

    Only artifact *age* ever produces a finding. Hash agreement is reported
    in details for a human to read, never scored — Go builds are not
    bit-for-bit reproducible across toolchain/environment differences, so a
    mismatch is not proof of staleness.

    `progress`, if given, is called with one line per finding as it is
    raised, plus a couple of milestone lines — so this check never runs
    silently even though it makes network calls.
    """
    emit = progress or _NO_PROGRESS
    findings: list[Finding] = []
    details: dict = {
        "artifact_uri": f"s3://{bucket}/{artifact_key}",
        "git_head": git_sha,
        "git_dirty": git_dirty,
        "build_error": build_error,
    }

    def _raise(severity: str, message: str) -> None:
        findings.append(Finding(severity, "binary_freshness", message))
        emit(f"  [{severity}] {message}")

    meta = s3.head_object(bucket, artifact_key)
    if meta is None:
        details["artifact_found"] = False
        _raise(SEVERITY_FAILURE, f"S3 artifact not found: s3://{bucket}/{artifact_key}")
        return findings, details

    details["artifact_found"] = True
    details["artifact_last_modified"] = meta.last_modified.isoformat()
    details["artifact_size_bytes"] = meta.size
    age_days = (now - meta.last_modified).total_seconds() / 86400.0
    details["artifact_age_days"] = round(age_days, 2)
    emit(f"  artifact modified {meta.last_modified.isoformat()} ({age_days:.1f}d ago), {meta.size} bytes")

    if build_error is not None:
        details["hash_match"] = None
        _raise(SEVERITY_WARNING, f"could not build a local binary for hash comparison: {build_error}")
    else:
        assert local_bytes is not None
        local_sha256 = hashlib.sha256(local_bytes).hexdigest()
        artifact_bytes = s3.get_object_bytes(bucket, artifact_key)
        artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        details["local_build_size_bytes"] = len(local_bytes)
        details["local_build_sha256"] = local_sha256
        details["artifact_sha256"] = artifact_sha256
        details["hash_match"] = local_sha256 == artifact_sha256
        details["hash_note"] = (
            "Go builds are not bit-for-bit reproducible across toolchain/env "
            "differences — a hash mismatch alone is NOT proof of staleness. "
            "Artifact age vs. STALE_ARTIFACT_DAYS is the authoritative signal."
        )
        emit(f"  hash match: {'YES' if details['hash_match'] else 'NO'} (informational only, see note)")

    if age_days > stale_days:
        _raise(
            SEVERITY_WARNING,
            f"S3 artifact is {age_days:.1f} days old "
            f"(> STALE_ARTIFACT_DAYS={stale_days}); current git HEAD is "
            f"{git_sha}{' (dirty)' if git_dirty else ''} — verify a deploy is not overdue.",
        )

    return findings, details


# ---------------------------------------------------------------------------
# Check 2 — zero-record session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestRecord:
    watchlist_id: str
    channels: tuple[str, ...]
    total_data_records: int


def fetch_day_manifests(s3: S3Client, bucket: str, day: date) -> list[ManifestRecord]:
    """List and fetch every manifest under raw/dt=<day>/<watchlist_id>/ for all
    watchlists present that day (a day may span more than one watchlist_id
    across a capture-config change — see infra/terraform.tfvars history)."""
    day_prefix = f"{S3_RAW_PREFIX}/dt={day.isoformat()}/"
    day_page = s3.list_keys(bucket, day_prefix, delimiter="/")

    manifests: list[ManifestRecord] = []
    for watchlist_prefix in day_page.common_prefixes:
        wl_page = s3.list_keys(bucket, watchlist_prefix, delimiter="")
        manifest_keys = [k for k in wl_page.keys if k.endswith(".manifest.json")]
        for key in manifest_keys:
            raw = s3.get_object_bytes(bucket, key)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue  # a corrupt manifest shouldn't sink the whole check
            manifests.append(
                ManifestRecord(
                    watchlist_id=data.get("watchlist_id", ""),
                    channels=tuple(data.get("channels", [])),
                    total_data_records=int(data.get("total_data_records", 0)),
                )
            )
    return manifests


def evaluate_day(
    day: date,
    manifests: list[ManifestRecord],
    min_total: int = MIN_DAILY_DATA_RECORDS,
    min_trade: int = MIN_DAILY_TRADE_RECORDS,
) -> tuple[Optional[Finding], dict]:
    """Score one trading day's manifests.

    Checks are mutually exclusive per day, most-specific problem first: a day
    with near-zero total records is reported as that, not *also* as missing
    trade data (which would be a redundant restatement of the same fact).
    """
    if not manifests:
        message = f"{day.isoformat()}: no segments found for this IDX trading day"
        return (
            Finding(SEVERITY_FAILURE, "zero_record_session", message),
            {"date": day.isoformat(), "status": "no_segments", "message": message},
        )

    watchlists = sorted({m.watchlist_id for m in manifests})
    total = sum(m.total_data_records for m in manifests)

    if total < min_total:
        message = (
            f"{day.isoformat()}: only {total} total_data_records across "
            f"{len(manifests)} segment(s) (< MIN_DAILY_DATA_RECORDS={min_total}) "
            f"for watchlist(s) {watchlists}"
        )
        return (
            Finding(SEVERITY_FAILURE, "zero_record_session", message),
            {
                "date": day.isoformat(),
                "status": "near_zero_records",
                "total_data_records": total,
                "watchlists": watchlists,
                "message": message,
            },
        )

    trade_total = sum(
        m.total_data_records for m in manifests if RUNNING_TRADE_CHANNEL in m.channels
    )
    if trade_total < min_trade:
        message = (
            f"{day.isoformat()}: {total} total_data_records but only {trade_total} "
            f"from manifests carrying '{RUNNING_TRADE_CHANNEL}' "
            f"(< MIN_DAILY_TRADE_RECORDS={min_trade}) for watchlist(s) {watchlists} — "
            "looks like coverage but carries no trade data"
        )
        return (
            Finding(SEVERITY_FAILURE, "zero_record_session", message),
            {
                "date": day.isoformat(),
                "status": "missing_trade_data",
                "total_data_records": total,
                "trade_records": trade_total,
                "watchlists": watchlists,
                "message": message,
            },
        )

    return (
        None,
        {
            "date": day.isoformat(),
            "status": "ok",
            "total_data_records": total,
            "trade_records": trade_total,
            "watchlists": watchlists,
        },
    )


def _progress_line_for_day(index: int, total_days: int, detail: dict) -> str:
    status = detail["status"]
    tag = {
        "ok": "OK",
        "skipped_non_trading_day": "skipped (non-trading day)",
        "no_segments": "FAILURE (no segments)",
        "near_zero_records": "FAILURE (near-zero records)",
        "missing_trade_data": "FAILURE (no trade data)",
    }.get(status, status)
    extra = ""
    if status in ("ok", "near_zero_records", "missing_trade_data"):
        extra = f" — {detail.get('total_data_records', 0)} records"
    return f"  [{index}/{total_days}] {detail['date']}: {tag}{extra}"


def check_zero_record_sessions(
    s3: S3Client,
    bucket: str,
    calendar: MarketCalendar,
    lookback_days: int,
    today: date,
    min_total: int = MIN_DAILY_DATA_RECORDS,
    min_trade: int = MIN_DAILY_TRADE_RECORDS,
    progress: Optional[Callable[[str], None]] = None,
    include_today: bool = False,
) -> tuple[list[Finding], list[dict]]:
    """Walk the lookback window, skipping non-trading days, scoring the rest.

    `progress`, if given, is called once per calendar day in the window (not
    only at the end) — a 30-day scan over the aws-CLI fallback can take
    minutes, and a check that prints nothing until it's done is
    indistinguishable from one that's hung.
    """
    emit = progress or _NO_PROGRESS
    findings: list[Finding] = []
    day_details: list[dict] = []

    start = today - timedelta(days=lookback_days)
    # Today only once its session has ended and been flushed (see TODAY_COMPLETE_AFTER_WIB).
    end = today if include_today else today - timedelta(days=1)
    total_days = (end - start).days + 1

    current = start
    index = 0
    while current <= end:
        index += 1
        if not calendar.is_trading_day(current):
            detail = {"date": current.isoformat(), "status": "skipped_non_trading_day"}
        else:
            manifests = fetch_day_manifests(s3, bucket, current)
            finding, detail = evaluate_day(current, manifests, min_total, min_trade)
            if finding is not None:
                findings.append(finding)
        day_details.append(detail)
        emit(_progress_line_for_day(index, total_days, detail))
        current += timedelta(days=1)

    return findings, day_details


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthCheckResult:
    generated_at: datetime
    bucket: str
    artifact_key: str
    binary_details: dict
    lookback_days: int
    day_details: list
    findings: list
    exit_code: int


def render_report(result: HealthCheckResult) -> str:
    lines: list[str] = []
    lines.append(f"=== Collector deployment self-check — {result.generated_at.isoformat()} ===")
    lines.append("")
    lines.append("[1] Binary freshness")
    bd = result.binary_details
    lines.append(f"  artifact:           s3://{result.bucket}/{result.artifact_key}")
    lines.append(f"  git HEAD:           {bd.get('git_head')}{' (dirty)' if bd.get('git_dirty') else ''}")
    if not bd.get("artifact_found", True):
        lines.append("  ARTIFACT NOT FOUND")
    else:
        lines.append(
            f"  artifact modified:  {bd.get('artifact_last_modified')} "
            f"({bd.get('artifact_age_days')} days ago)"
        )
        lines.append(f"  artifact size:      {bd.get('artifact_size_bytes')} bytes")
        if bd.get("build_error"):
            lines.append(f"  local build:        FAILED — {bd['build_error']}")
        else:
            lines.append(f"  local build size:   {bd.get('local_build_size_bytes')} bytes")
            lines.append(f"  local build sha256: {bd.get('local_build_sha256')}")
            lines.append(f"  artifact sha256:    {bd.get('artifact_sha256')}")
            lines.append(f"  hash match:         {'YES' if bd.get('hash_match') else 'NO'}")
            lines.append(f"  NOTE: {bd.get('hash_note')}")
    lines.append("")

    lines.append(f"[2] Zero-record session check (lookback={result.lookback_days}d, today included only after {TODAY_COMPLETE_AFTER_WIB:%H:%M} WIB)")
    problem_days = [d for d in result.day_details if d["status"] not in ("ok", "skipped_non_trading_day")]
    ok_count = sum(1 for d in result.day_details if d["status"] == "ok")
    skipped_count = sum(1 for d in result.day_details if d["status"] == "skipped_non_trading_day")
    if not problem_days:
        lines.append(f"  all clear — {ok_count} trading day(s) checked, {skipped_count} non-trading day(s) skipped")
    else:
        for d in problem_days:
            lines.append(f"  [FAILURE] {d['message']}")
        lines.append(f"  ({ok_count} clean trading day(s), {skipped_count} non-trading day(s) skipped)")
    lines.append("")

    warnings = [f for f in result.findings if f.severity == SEVERITY_WARNING]
    failures = [f for f in result.findings if f.severity == SEVERITY_FAILURE]
    lines.append(f"=== Summary: {len(warnings)} warning(s), {len(failures)} failure(s) — exit {result.exit_code} ===")
    return "\n".join(lines)


def _result_to_dict(result: HealthCheckResult) -> dict:
    return {
        "generated_at": result.generated_at.isoformat(),
        "bucket": result.bucket,
        "artifact_key": result.artifact_key,
        "binary_freshness": result.binary_details,
        "lookback_days": result.lookback_days,
        "days": result.day_details,
        "findings": [dataclasses.asdict(f) for f in result.findings],
        "exit_code": result.exit_code,
    }


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    parser.add_argument("--stale-days", type=int, default=STALE_ARTIFACT_DAYS)
    parser.add_argument("--min-daily-records", type=int, default=MIN_DAILY_DATA_RECORDS)
    parser.add_argument("--min-trade-records", type=int, default=MIN_DAILY_TRADE_RECORDS)
    parser.add_argument("--bucket", default=None, help="override the S3 bucket")
    parser.add_argument("--artifact-key", default=None, help="override the S3 artifact key")
    parser.add_argument("--region", default=AWS_REGION)
    parser.add_argument("--today", default=None, help="override 'today' as YYYY-MM-DD (testing)")
    parser.add_argument("--quiet", action="store_true", help="suppress live progress output")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    # Progress goes to stderr, unconditionally separate from the report/JSON
    # on stdout, so `--json` output stays machine-parseable even with
    # progress narration turned on.
    say: Callable[[str], None] = (
        _NO_PROGRESS if args.quiet else (lambda msg: print(msg, file=sys.stderr, flush=True))
    )

    try:
        if args.bucket and args.artifact_key:
            bucket, artifact_key = args.bucket, args.artifact_key
        else:
            uri = os.environ.get("COLLECTOR_ARTIFACT_S3_URI") or parse_artifact_s3_uri(TERRAFORM_TFVARS)
            parsed_bucket, parsed_key = split_s3_uri(uri)
            bucket = args.bucket or parsed_bucket
            artifact_key = args.artifact_key or parsed_key
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    now = datetime.now(timezone.utc)
    now_wib = now.astimezone(WIB)
    today = date.fromisoformat(args.today) if args.today else now_wib.date()
    include_today = args.today is None and now_wib.time() >= TODAY_COMPLETE_AFTER_WIB

    say(f"=== Collector deployment self-check — {now.isoformat()} ===")

    try:
        git_sha, git_dirty = get_git_head_info()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Could not read git HEAD: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    say(f"git HEAD: {git_sha}{' (dirty)' if git_dirty else ''}")

    say(f"[1/2] Binary freshness: building linux/arm64 collector (up to {BUILD_TIMEOUT_SECONDS}s)...")
    local_bytes, build_error = build_local_collector_binary()
    say("  build failed — freshness check will be age-only" if build_error else "  build OK")

    say(f"Connecting to S3 (region={args.region})...")
    try:
        s3 = build_s3_client(args.region, progress=say)
    except S3Error as exc:
        print(f"Could not create an S3 client: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        binary_findings, binary_details = evaluate_binary_freshness(
            s3, bucket, artifact_key, local_bytes, build_error,
            git_sha, git_dirty, now, args.stale_days, progress=say,
        )
        say(f"[2/2] Zero-record session check: scanning {args.lookback_days} calendar day(s) back from {today.isoformat()}...")
        session_findings, day_details = check_zero_record_sessions(
            s3, bucket, CALENDAR, args.lookback_days, today,
            args.min_daily_records, args.min_trade_records, progress=say,
            include_today=include_today,
        )
    except S3AuthError:
        print(
            "AWS credentials missing, expired, or invalid.\n"
            "Set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (see .env.local) "
            "or configure the aws CLI, then retry.",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    except S3Error as exc:
        print(f"AWS S3 error: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    findings = binary_findings + session_findings
    exit_code = _exit_code_for(findings)

    result = HealthCheckResult(
        generated_at=now,
        bucket=bucket,
        artifact_key=artifact_key,
        binary_details=binary_details,
        lookback_days=args.lookback_days,
        day_details=day_details,
        findings=findings,
        exit_code=exit_code,
    )

    if args.json:
        print(json.dumps(_result_to_dict(result), indent=2, default=str))
    else:
        say("")
        print(render_report(result))

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
