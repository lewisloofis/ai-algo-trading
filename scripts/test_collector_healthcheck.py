"""pytest suite for collector_healthcheck.py — roadmap W1.3.

Everything here is a pure unit test against a stubbed S3Client. No network,
no real AWS, no real `go build`. Coverage requested by the task:

  - a clean run
  - a missing trading day
  - a zero-record day
  - a day missing the runningTradeBatch channel (coverage-without-trade-data)
  - a holiday correctly NOT flagged
  - a stale artifact
  - exit-code mapping for each case
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))

from lib.market_calendar import MarketCalendar  # noqa: E402

from s3_readonly import (  # noqa: E402
    ListPage,
    ObjectMeta,
    S3AuthError,
    S3Error,
    build_s3_client,
)

from collector_healthcheck import (  # noqa: E402
    EXIT_FAILURE,
    EXIT_OK,
    EXIT_WARNING,
    STALE_ARTIFACT_DAYS,
    Finding,
    SEVERITY_FAILURE,
    SEVERITY_WARNING,
    check_zero_record_sessions,
    evaluate_binary_freshness,
    evaluate_day,
    _exit_code_for,
    fetch_day_manifests,
)


# ---------------------------------------------------------------------------
# Fake S3 client
# ---------------------------------------------------------------------------


@dataclass
class FakeS3Client:
    """In-memory stand-in for S3Client. `objects` maps key -> raw bytes;
    `heads` maps key -> ObjectMeta for head_object lookups."""

    objects: dict
    heads: Optional[dict] = None
    raise_auth_error: bool = False

    def __post_init__(self) -> None:
        if self.heads is None:
            self.heads = {}

    def list_keys(self, bucket: str, prefix: str, delimiter: str = "") -> ListPage:
        if self.raise_auth_error:
            raise S3AuthError("fake auth failure")
        matching = [k for k in self.objects if k.startswith(prefix)]
        if not delimiter:
            return ListPage(keys=tuple(sorted(matching)), common_prefixes=())
        prefixes = set()
        keys = []
        for key in matching:
            rest = key[len(prefix):]
            if delimiter in rest:
                prefixes.add(prefix + rest.split(delimiter, 1)[0] + delimiter)
            else:
                keys.append(key)
        return ListPage(keys=tuple(sorted(keys)), common_prefixes=tuple(sorted(prefixes)))

    def head_object(self, bucket: str, key: str) -> Optional[ObjectMeta]:
        if self.raise_auth_error:
            raise S3AuthError("fake auth failure")
        return self.heads.get(key)

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        if self.raise_auth_error:
            raise S3AuthError("fake auth failure")
        if key not in self.objects:
            raise S3Error(f"NoSuchKey: {key}")
        return self.objects[key]


BUCKET = "marketdata-test"


def _manifest_bytes(watchlist_id: str, channels: list, total_data_records: int) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "collector_version": "0.1.0",
            "watchlist_id": watchlist_id,
            "symbols": ["FOO"],
            "channels": channels,
            "segment_start_utc": "2026-06-23T04:00:00Z",
            "segment_end_utc": "2026-06-23T04:59:59Z",
            "total_data_records": total_data_records,
            "total_control_records": 3,
            "compressed_size_bytes": 1,
            "uncompressed_size_bytes": 1,
        }
    ).encode()


# ---------------------------------------------------------------------------
# evaluate_day — pure per-day scoring
# ---------------------------------------------------------------------------


def test_evaluate_day_clean_healthy_day_is_not_flagged() -> None:
    manifests_bytes = {
        f"raw/dt=2026-06-23/plan10-survivors/seg-0{i}0000.manifest.json": _manifest_bytes(
            "plan10-survivors", ["runningTradeBatch", "orderBook", "liveprice"], 50_000
        )
        for i in range(4)
    }
    s3 = FakeS3Client(objects=manifests_bytes)
    day = date(2026, 6, 23)
    manifests = fetch_day_manifests(s3, BUCKET, day)
    finding, detail = evaluate_day(day, manifests)

    assert finding is None
    assert detail["status"] == "ok"
    assert detail["total_data_records"] == 200_000
    assert _exit_code_for([]) == EXIT_OK


def test_evaluate_day_no_segments_is_failure() -> None:
    day = date(2026, 8, 5)
    finding, detail = evaluate_day(day, manifests=[])

    assert finding is not None
    assert finding.severity == SEVERITY_FAILURE
    assert "no segments found" in finding.message
    assert detail["status"] == "no_segments"
    assert _exit_code_for([finding]) == EXIT_FAILURE


def test_evaluate_day_near_zero_records_is_failure() -> None:
    day = date(2026, 6, 30)
    s3 = FakeS3Client(
        objects={
            "raw/dt=2026-06-30/default/seg-062153.manifest.json": _manifest_bytes(
                "default", ["orderBook", "liveprice"], 12
            )
        }
    )
    manifests = fetch_day_manifests(s3, BUCKET, day)
    finding, detail = evaluate_day(day, manifests, min_total=1000, min_trade=1000)

    assert finding is not None
    assert finding.severity == SEVERITY_FAILURE
    assert detail["status"] == "near_zero_records"
    assert detail["total_data_records"] == 12
    assert _exit_code_for([finding]) == EXIT_FAILURE


def test_evaluate_day_missing_running_trade_batch_is_failure() -> None:
    """Reproduces the real 2026-06-17 `default` incident: healthy-looking
    total record count, but almost all of it is orderBook/liveprice, not
    trade data — coverage without substance."""
    day = date(2026, 6, 17)
    objects = {
        "raw/dt=2026-06-17/default/seg-044221.manifest.json": _manifest_bytes(
            "default", ["orderBook", "liveprice"], 4_975
        ),
        "raw/dt=2026-06-17/default/seg-062241.manifest.json": _manifest_bytes(
            "default", ["orderBook", "liveprice"], 42_695
        ),
        "raw/dt=2026-06-17/default/seg-112231.manifest.json": _manifest_bytes(
            "default", ["runningTradeBatch", "orderBook", "liveprice", "iepiev"], 33
        ),
        "raw/dt=2026-06-17/default/seg-112812.manifest.json": _manifest_bytes(
            "default", ["runningTradeBatch", "orderBook", "liveprice", "iepiev"], 10
        ),
    }
    s3 = FakeS3Client(objects=objects)
    manifests = fetch_day_manifests(s3, BUCKET, day)
    finding, detail = evaluate_day(day, manifests, min_total=1000, min_trade=1000)

    assert finding is not None
    assert finding.severity == SEVERITY_FAILURE
    assert detail["status"] == "missing_trade_data"
    assert detail["total_data_records"] == 47_713
    assert detail["trade_records"] == 43
    assert "looks like coverage but carries no trade data" in finding.message
    assert _exit_code_for([finding]) == EXIT_FAILURE


# ---------------------------------------------------------------------------
# check_zero_record_sessions — orchestration across a lookback window
# ---------------------------------------------------------------------------


def test_check_zero_record_sessions_skips_holidays_and_weekends() -> None:
    # 2026-08-17 is Indonesian Independence Day (calendar/idx_holidays.json).
    calendar = MarketCalendar(holidays=frozenset({date(2026, 8, 17)}))
    today = date(2026, 8, 20)  # Thursday
    healthy_day = date(2026, 8, 19)  # Wednesday, trading day, has data
    missing_day = date(2026, 8, 18)  # Tuesday, trading day, NO data -> flagged

    objects = {
        f"raw/dt={healthy_day.isoformat()}/plan10-survivors/seg-050000.manifest.json": _manifest_bytes(
            "plan10-survivors", ["runningTradeBatch", "orderBook"], 300_000
        ),
    }
    s3 = FakeS3Client(objects=objects)

    findings, day_details = check_zero_record_sessions(
        s3, BUCKET, calendar, lookback_days=4, today=today,
    )

    by_date = {d["date"]: d for d in day_details}

    # Holiday and the weekend before it must be skipped, never flagged.
    assert by_date[date(2026, 8, 17).isoformat()]["status"] == "skipped_non_trading_day"
    assert by_date[date(2026, 8, 16).isoformat()]["status"] == "skipped_non_trading_day"  # Sunday

    # The healthy trading day is clean.
    assert by_date[healthy_day.isoformat()]["status"] == "ok"

    # The trading day with no segments at all is a failure.
    assert by_date[missing_day.isoformat()]["status"] == "no_segments"

    assert len(findings) == 1
    assert findings[0].severity == SEVERITY_FAILURE
    assert _exit_code_for(findings) == EXIT_FAILURE


def test_check_zero_record_sessions_excludes_today() -> None:
    """Today is in progress and must never be scored, even with zero data."""
    calendar = MarketCalendar(holidays=frozenset())
    today = date(2026, 6, 24)  # Wednesday, no segments seeded anywhere
    s3 = FakeS3Client(objects={})

    _, day_details = check_zero_record_sessions(s3, BUCKET, calendar, lookback_days=1, today=today)

    assert all(d["date"] != today.isoformat() for d in day_details)


# ---------------------------------------------------------------------------
# evaluate_binary_freshness
# ---------------------------------------------------------------------------


def _artifact_setup(bucket: str, key: str, body: bytes, last_modified: datetime) -> FakeS3Client:
    return FakeS3Client(
        objects={key: body},
        heads={key: ObjectMeta(last_modified=last_modified, size=len(body))},
    )


def test_binary_freshness_fresh_matching_artifact_is_clean() -> None:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    body = b"fake-collector-binary-bytes"
    s3 = _artifact_setup(BUCKET, "artifacts/collector", body, now - timedelta(days=2))

    findings, details = evaluate_binary_freshness(
        s3, BUCKET, "artifacts/collector",
        local_bytes=body, build_error=None,
        git_sha="1ccd46c", git_dirty=False, now=now,
    )

    assert findings == []
    assert details["hash_match"] is True
    assert details["artifact_age_days"] == 2.0
    assert _exit_code_for(findings) == EXIT_OK


def test_binary_freshness_hash_mismatch_alone_is_not_flagged() -> None:
    """A hash mismatch with a fresh artifact must NOT raise any finding —
    Go builds aren't bit-for-bit reproducible, so mismatch alone is not
    proof of staleness."""
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    s3 = _artifact_setup(BUCKET, "artifacts/collector", b"remote-bytes", now - timedelta(days=1))

    findings, details = evaluate_binary_freshness(
        s3, BUCKET, "artifacts/collector",
        local_bytes=b"different-local-bytes", build_error=None,
        git_sha="1ccd46c", git_dirty=True, now=now,
    )

    assert findings == []
    assert details["hash_match"] is False
    assert _exit_code_for(findings) == EXIT_OK


def test_binary_freshness_stale_artifact_is_warning() -> None:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    body = b"fake-collector-binary-bytes"
    age_days = STALE_ARTIFACT_DAYS + 1
    s3 = _artifact_setup(BUCKET, "artifacts/collector", body, now - timedelta(days=age_days))

    findings, details = evaluate_binary_freshness(
        s3, BUCKET, "artifacts/collector",
        local_bytes=body, build_error=None,
        git_sha="1ccd46c", git_dirty=False, now=now,
    )

    assert len(findings) == 1
    assert findings[0].severity == SEVERITY_WARNING
    assert "days old" in findings[0].message
    assert _exit_code_for(findings) == EXIT_WARNING


def test_binary_freshness_missing_artifact_is_failure() -> None:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    s3 = FakeS3Client(objects={}, heads={})

    findings, details = evaluate_binary_freshness(
        s3, BUCKET, "artifacts/collector",
        local_bytes=b"whatever", build_error=None,
        git_sha="1ccd46c", git_dirty=False, now=now,
    )

    assert details["artifact_found"] is False
    assert len(findings) == 1
    assert findings[0].severity == SEVERITY_FAILURE
    assert _exit_code_for(findings) == EXIT_FAILURE


def test_binary_freshness_build_error_degrades_to_warning_not_crash() -> None:
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    body = b"fake-collector-binary-bytes"
    s3 = _artifact_setup(BUCKET, "artifacts/collector", body, now - timedelta(days=1))

    findings, details = evaluate_binary_freshness(
        s3, BUCKET, "artifacts/collector",
        local_bytes=None, build_error="go toolchain not found on PATH",
        git_sha="1ccd46c", git_dirty=False, now=now,
    )

    assert details["hash_match"] is None
    assert len(findings) == 1
    assert findings[0].severity == SEVERITY_WARNING
    assert "could not build" in findings[0].message
    assert _exit_code_for(findings) == EXIT_WARNING


# ---------------------------------------------------------------------------
# Exit-code mapping
# ---------------------------------------------------------------------------


def test_exit_code_mapping_no_findings() -> None:
    assert _exit_code_for([]) == EXIT_OK


def test_exit_code_mapping_warning_only() -> None:
    findings = [Finding(SEVERITY_WARNING, "binary_freshness", "stale")]
    assert _exit_code_for(findings) == EXIT_WARNING


def test_exit_code_mapping_failure_only() -> None:
    findings = [Finding(SEVERITY_FAILURE, "zero_record_session", "no segments")]
    assert _exit_code_for(findings) == EXIT_FAILURE


def test_exit_code_mapping_failure_dominates_warning() -> None:
    findings = [
        Finding(SEVERITY_WARNING, "binary_freshness", "stale"),
        Finding(SEVERITY_FAILURE, "zero_record_session", "no segments"),
    ]
    assert _exit_code_for(findings) == EXIT_FAILURE


# ---------------------------------------------------------------------------
# S3 backend selection — the CLI fallback must warn loudly, never degrade
# silently (it's ~50x slower wall-clock — see scripts/s3_readonly.py).
# ---------------------------------------------------------------------------


def test_build_s3_client_warns_on_cli_fallback(monkeypatch) -> None:
    import s3_readonly

    # Simulate "boto3 is not importable" without needing it actually
    # uninstalled from this venv — the standard sys.modules trick: a module
    # mapped to None in sys.modules makes `import <name>` raise ImportError.
    monkeypatch.setitem(sys.modules, "boto3", None)
    # Don't depend on the aws CLI actually being on PATH wherever this suite runs.
    monkeypatch.setattr(s3_readonly.shutil, "which", lambda name: "/usr/local/bin/aws")

    warnings: list[str] = []
    client = build_s3_client("ap-southeast-3", progress=warnings.append)

    assert isinstance(client, s3_readonly.CliS3Client)
    assert len(warnings) == 1
    assert "boto3" in warnings[0]
    assert "50x" in warnings[0]
