#!/usr/bin/env python3
"""
scripts/s3_readonly.py — a minimal, read-only S3 access layer.

This module exists to make one property obvious and enforced in one place:
every operation here is read-only (`head_object`, `get_object`, and
`list_objects_v2` only — no writes, no deletes). That is what makes it safe
to run this against the production `marketdata-*` bucket from a laptop.

`S3Client` is a tiny Protocol so callers (collector_healthcheck.py and its
tests) can depend on an interface rather than a concrete backend. Two
backends implement it:

  - Boto3S3Client — used when boto3 is importable. Fast: one process, one
    connection, real pagination.
  - CliS3Client   — a fallback that shells out to the `aws` CLI with
    explicit argv lists (never shell=True). This exists so the health check
    still works with nothing but the aws CLI installed, but it is roughly
    50x slower in wall-clock time: each `aws` invocation costs ~1-2s of
    subprocess/interpreter startup, and a full run makes dozens of S3 calls.
    `build_s3_client` prints a prominent warning whenever it falls back to
    this path — a silent 50x slowdown is its own kind of silent failure.

Install boto3 to avoid the fallback (see research/requirements.txt).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

# S3 listing/pagination bounds — keeps callers' listing calls bounded even if
# a prefix unexpectedly contains far more keys than expected.
S3_LIST_PAGE_SIZE: int = 1000
MAX_LIST_PAGES: int = 50

# Subprocess timeout for each aws CLI invocation (CliS3Client only).
CLI_TIMEOUT_SECONDS: int = 30


@dataclass(frozen=True)
class ObjectMeta:
    last_modified: datetime
    size: int


@dataclass(frozen=True)
class ListPage:
    keys: tuple[str, ...]
    common_prefixes: tuple[str, ...]


class S3Error(RuntimeError):
    """A non-credential S3 access failure (missing bucket, network, etc.)."""


class S3AuthError(S3Error):
    """AWS credentials are missing, expired, or invalid.

    Kept deliberately generic in its user-facing message — some AWS error
    bodies for auth failures echo back the access key id, so this error's
    str() is never printed to the report; only a fixed, safe message is.
    """


class S3Client(Protocol):
    def list_keys(self, bucket: str, prefix: str, delimiter: str = "") -> ListPage: ...

    def head_object(self, bucket: str, key: str) -> Optional[ObjectMeta]: ...

    def get_object_bytes(self, bucket: str, key: str) -> bytes: ...


_AUTH_ERROR_CODES = frozenset(
    {
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "ExpiredToken",
        "ExpiredTokenException",
        "UnrecognizedClientException",
        "AccessDenied",
    }
)


def _parse_timestamp(value: str) -> datetime:
    """Parse a timestamp that may be ISO-8601 or RFC-1123 (aws CLI vs botocore)."""
    text = value.strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return parsedate_to_datetime(value)


class Boto3S3Client:
    """S3Client backed by boto3, used when boto3 is importable."""

    def __init__(self, region: str) -> None:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        self._client = boto3.client("s3", region_name=region)
        self._client_error = ClientError
        self._no_credentials_error = NoCredentialsError

    def _error_code(self, exc: Exception) -> str:
        return exc.response.get("Error", {}).get("Code", "")  # type: ignore[attr-defined]

    def list_keys(self, bucket: str, prefix: str, delimiter: str = "") -> ListPage:
        keys: list[str] = []
        prefixes: list[str] = []
        token: Optional[str] = None
        for _ in range(MAX_LIST_PAGES):
            kwargs: dict = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": S3_LIST_PAGE_SIZE}
            if delimiter:
                kwargs["Delimiter"] = delimiter
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = self._client.list_objects_v2(**kwargs)
            except self._no_credentials_error as exc:
                raise S3AuthError("no credentials") from exc
            except self._client_error as exc:
                code = self._error_code(exc)
                if code in _AUTH_ERROR_CODES:
                    raise S3AuthError(code) from exc
                raise S3Error(f"list_objects_v2 {bucket}/{prefix}: {code or exc}") from exc
            keys.extend(o["Key"] for o in resp.get("Contents", []) or [])
            prefixes.extend(p["Prefix"] for p in resp.get("CommonPrefixes", []) or [])
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return ListPage(keys=tuple(keys), common_prefixes=tuple(prefixes))

    def head_object(self, bucket: str, key: str) -> Optional[ObjectMeta]:
        try:
            resp = self._client.head_object(Bucket=bucket, Key=key)
        except self._no_credentials_error as exc:
            raise S3AuthError("no credentials") from exc
        except self._client_error as exc:
            code = self._error_code(exc)
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            if code in _AUTH_ERROR_CODES:
                raise S3AuthError(code) from exc
            raise S3Error(f"head_object {bucket}/{key}: {code or exc}") from exc
        return ObjectMeta(last_modified=resp["LastModified"], size=resp["ContentLength"])

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        except self._no_credentials_error as exc:
            raise S3AuthError("no credentials") from exc
        except self._client_error as exc:
            code = self._error_code(exc)
            if code in _AUTH_ERROR_CODES:
                raise S3AuthError(code) from exc
            raise S3Error(f"get_object {bucket}/{key}: {code or exc}") from exc


def _cli_error_is_auth(stderr: str) -> bool:
    return bool(
        re.search(
            r"Unable to locate credentials|InvalidAccessKeyId|SignatureDoesNotMatch|"
            r"ExpiredToken|UnrecognizedClientException|AccessDenied|token has expired",
            stderr,
            re.IGNORECASE,
        )
    )


def _cli_error_is_not_found(stderr: str) -> bool:
    return bool(re.search(r"Not Found|NoSuchKey|404", stderr, re.IGNORECASE))


class CliS3Client:
    """S3Client backed by shelling out to the aws CLI (no boto3 available).

    Every invocation is an explicit argv list — never shell=True — per the
    read-only, no-surprises requirement for this module. See the module
    docstring for why this path is much slower than Boto3S3Client.
    """

    def __init__(self, region: str) -> None:
        if shutil.which("aws") is None:
            raise S3Error("neither boto3 nor the aws CLI is available")
        self._region = region

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            raise S3Error(f"aws CLI timed out: {' '.join(argv)}") from exc

    def _raise_for_error(self, argv: list[str], stderr: str) -> None:
        if _cli_error_is_auth(stderr):
            raise S3AuthError("cli auth failure")
        raise S3Error(f"{' '.join(argv)}: {stderr.strip()[:500]}")

    def list_keys(self, bucket: str, prefix: str, delimiter: str = "") -> ListPage:
        keys: list[str] = []
        prefixes: list[str] = []
        token: Optional[str] = None
        for _ in range(MAX_LIST_PAGES):
            argv = [
                "aws", "s3api", "list-objects-v2",
                "--bucket", bucket,
                "--prefix", prefix,
                "--region", self._region,
                "--max-keys", str(S3_LIST_PAGE_SIZE),
                "--no-paginate",
                "--output", "json",
            ]
            if delimiter:
                argv += ["--delimiter", delimiter]
            if token:
                argv += ["--continuation-token", token]
            proc = self._run(argv)
            if proc.returncode != 0:
                self._raise_for_error(argv, proc.stderr)
            data = json.loads(proc.stdout) if proc.stdout.strip() else {}
            keys.extend(o["Key"] for o in data.get("Contents", []) or [])
            prefixes.extend(p["Prefix"] for p in data.get("CommonPrefixes", []) or [])
            token = data.get("NextContinuationToken") if data.get("IsTruncated") else None
            if not token:
                break
        return ListPage(keys=tuple(keys), common_prefixes=tuple(prefixes))

    def head_object(self, bucket: str, key: str) -> Optional[ObjectMeta]:
        argv = [
            "aws", "s3api", "head-object",
            "--bucket", bucket,
            "--key", key,
            "--region", self._region,
            "--output", "json",
        ]
        proc = self._run(argv)
        if proc.returncode != 0:
            if _cli_error_is_not_found(proc.stderr):
                return None
            self._raise_for_error(argv, proc.stderr)
        data = json.loads(proc.stdout)
        return ObjectMeta(
            last_modified=_parse_timestamp(data["LastModified"]),
            size=int(data["ContentLength"]),
        )

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        fd, tmp_name = tempfile.mkstemp(prefix="collector-healthcheck-obj-")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            argv = [
                "aws", "s3api", "get-object",
                "--bucket", bucket,
                "--key", key,
                "--region", self._region,
                str(tmp_path),
                "--output", "json",
            ]
            proc = self._run(argv)
            if proc.returncode != 0:
                self._raise_for_error(argv, proc.stderr)
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)


_BOTO3_FALLBACK_WARNING = (
    "WARNING: boto3 is not importable — falling back to the aws CLI. "
    "This is roughly 50x slower wall-clock (each `aws` call pays ~1-2s of "
    "subprocess startup, and a full run makes dozens of them); a 30-day "
    "lookback can take 15-20+ minutes instead of well under a minute. "
    "Install boto3 (see research/requirements.txt) to avoid this."
)


def build_s3_client(region: str, progress: Optional[Callable[[str], None]] = None) -> S3Client:
    """Prefer boto3 when importable; otherwise fall back to the aws CLI.

    `progress`, if given, receives the fallback warning as a single line so
    the ~50x slowdown is never silent. Defaults to printing to stderr
    immediately (flushed) so it's visible before a long CLI-backed run even
    produces its first result.
    """
    emit = progress if progress is not None else (lambda msg: print(msg, file=sys.stderr, flush=True))
    try:
        import boto3  # noqa: F401
    except ImportError:
        emit(_BOTO3_FALLBACK_WARNING)
        return CliS3Client(region)
    return Boto3S3Client(region)
