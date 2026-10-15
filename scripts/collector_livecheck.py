#!/usr/bin/env python3
"""
scripts/collector_livecheck.py — LIVE health check of the market-data collector
on EC2 (companion to scripts/collector_healthcheck.py).

collector_healthcheck.py reads S3, but today's capture only reaches S3 at
midnight/shutdown (segments flush then), so it cannot confirm the CURRENT
session. This command asks the box itself, via SSM Run Command
(AWS-RunShellScript) with a fixed, read-only shell script:

  * systemd: is-active, ActiveEnterTimestamp, NRestarts, MainPID
  * process RSS from /proc/<pid>/status VmRSS (NOT systemd MemoryCurrent,
    which counts page cache and reads huge on a healthy box)
  * in-progress segment files for today (UTC dt= dir, what the collector
    uses): count, total bytes, newest mtime age
  * disk free on the segment volume
  * the last journal lines (redacted before printing)

The remote script never reads env files, SSM parameters, credentials or token
files, and never writes anything. Credentials locally come from the ambient
environment / named profile (never read here) and are never printed.

Data-frame signal: the collector logs every 15s either
    "[capture] N frames / X KB in last 15s (M frames total)"   (data)
    "[capture] no frames in last 15s (market closed or quiet) ..."
"frames total" resets per reconnect, so only the per-interval N is used.

Verdict (IDX session = trading day, 09:00-16:00 WIB, research/lib calendar):
  OK    active, newest segment write < 5 min old, a data-frame line recently
  WARN  active but exactly one of those freshness signals is missing, or
        low disk / stopped outside session
  FAIL  not active / instance stopped in session / no fresh segment AND no frames
Outside session hours it reports state and says freshness is not expected.

Usage:
    python scripts/collector_livecheck.py [--json] [--timeout SECONDS]

    python scripts/collector_livecheck.py --profile NAME     (or AWS_PROFILE=NAME)

Exit codes: 0 = OK, 1 = WARN, 2 = FAIL or setup/auth error (missing/invalid credentials).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

try:  # the repo's shared calendar; fall back to a plain Mon-Fri rule
    sys.path.insert(0, str(REPO_ROOT / "research"))
    from lib.market_calendar import CALENDAR  # noqa: E402
except Exception:  # pragma: no cover - only when research/ is absent
    CALENDAR = None

# ---------------------------------------------------------------------------
# Config (infra/variables.tf defaults: project "sb-algo-trading", env "prod";
# infra/ec2.tf Name tag = "<project>-<env>-collector"; infra/user_data.sh.tftpl
# unit collector.service, CAPTURE_DIR /var/lib/collector/segments)
# ---------------------------------------------------------------------------
AWS_REGION = "ap-southeast-3"
INSTANCE_NAME_TAG = "sb-algo-trading-prod-collector"
UNIT_NAME = "collector.service"
SEGMENT_DIR = "/var/lib/collector/segments"
JOURNAL_LINES = 60

WIB = timezone(timedelta(hours=7))
SESSION_OPEN_HOUR = 9
SESSION_CLOSE_HOUR = 16

SCHEDULE_START_MIN = 8 * 60 + 30   # infra/scheduler.tf start 08:30 WIB
SCHEDULE_STOP_MIN = 16 * 60 + 25    # stop 16:25 WIB
SCHEDULE_GRACE_MIN = 5              # boot + service start time after the scheduled start

MAX_SEGMENT_AGE_S = 300         # newest segment write must be < 5 min old
MAX_FRAME_LINE_AGE_S = 120       # a data-frame stats line (every 15s) within 2 min
MIN_DISK_FREE_KB = 500 * 1024    # warn below ~500 MiB free

SSM_TOTAL_TIMEOUT_S = 90
SSM_POLL_INTERVAL_S = 2.0
SSM_INITIAL_WAIT_S = 1.0

EXIT_OK, EXIT_WARN, EXIT_FAIL = 0, 1, 2

# ---------------------------------------------------------------------------
# The fixed remote script. Read-only: systemctl show/is-active, /proc, find,
# df, journalctl. No cat of env/credential files, no sudo, no writes.
# ---------------------------------------------------------------------------
REMOTE_SCRIPT = f"""#!/bin/bash
UNIT={UNIT_NAME}
SEGDIR={SEGMENT_DIR}
echo "NOW=$(date +%s)"
echo "ACTIVE=$(systemctl is-active $UNIT 2>&1 | head -n1)"
echo "ENTER=$(systemctl show $UNIT -p ActiveEnterTimestamp --value 2>/dev/null)"
echo "NRESTARTS=$(systemctl show $UNIT -p NRestarts --value 2>/dev/null)"
PID=$(systemctl show $UNIT -p MainPID --value 2>/dev/null)
echo "MAINPID=$PID"
if [ -n "$PID" ] && [ "$PID" != "0" ] && [ -r /proc/$PID/status ]; then
  echo "RSS_KB=$(awk '/^VmRSS:/ {{print $2}}' /proc/$PID/status)"
fi
D=$SEGDIR/dt=$(date -u +%F)
echo "SEG_DIR=$D"
if [ -d "$D" ]; then
  find "$D" -type f -name 'seg-*' ! -name '*.zst' ! -name '*.json' -printf '%s %T@\\n' 2>/dev/null \\
    | awk -v now="$(date +%s)" 'BEGIN{{n=0;b=0;m=0}} {{n++;b+=$1; if($2>m)m=$2}} END{{print "SEG_COUNT="n; print "SEG_BYTES="b; if(n>0) printf "SEG_NEWEST_AGE_S=%d\\n", now-m}}'
else
  echo "SEG_COUNT=0"; echo "SEG_BYTES=0"
fi
echo "DISK_FREE_KB=$(df -Pk $SEGDIR 2>/dev/null | awk 'NR==2 {{print $4}}')"
echo "---JOURNAL---"
journalctl -u $UNIT -n {JOURNAL_LINES} --no-pager -o short-unix 2>&1
"""

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
_REDACTIONS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]*)?"), "[REDACTED-JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b((?:access_?|refresh_?|auth_?)?token|password|passwd|secret|api_?key|authorization)"
                r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2[REDACTED]"),
    (re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}"), "[REDACTED-BLOB]"),
]


def redact(line: str) -> str:
    for pattern, repl in _REDACTIONS:
        line = pattern.sub(repl, line)
    return line


# ---------------------------------------------------------------------------
# Errors and AWS access (boto3, injected for tests)
# ---------------------------------------------------------------------------
class LiveCheckError(RuntimeError):
    pass


class AuthError(LiveCheckError):
    pass


class SsmTimeout(LiveCheckError):
    pass


_AUTH_CODES = {"ExpiredToken", "ExpiredTokenException", "RequestExpired", "InvalidClientTokenId",
               "UnrecognizedClientException", "AuthFailure", "InvalidToken"}


class Boto3Aws:
    """Minimal EC2 + SSM facade. Raises AuthError for missing/expired creds."""

    def __init__(self, region: str = AWS_REGION, profile: Optional[str] = None) -> None:
        try:
            import boto3
            from botocore.exceptions import (ClientError, NoCredentialsError, ProfileNotFound,
                                             TokenRetrievalError)
        except ImportError as exc:
            raise LiveCheckError("boto3 is not installed (see research/requirements.txt)") from exc
        try:
            # profile=None -> boto3's default chain (honours AWS_PROFILE / env keys).
            session = boto3.Session(profile_name=profile, region_name=region)
        except ProfileNotFound as exc:
            raise AuthError("profile not found") from exc
        self._ec2 = session.client("ec2")
        self._ssm = session.client("ssm")
        self._client_error = ClientError
        self._auth_types = (NoCredentialsError, TokenRetrievalError)

    def _call(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except self._auth_types as exc:
            raise AuthError("no usable credentials") from exc
        except self._client_error as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _AUTH_CODES:
                raise AuthError(code) from exc
            raise LiveCheckError(f"{code or 'AWS error'}: {exc.response.get('Error', {}).get('Message', '')}") from exc

    def find_instance(self, name_tag: str) -> Optional[dict]:
        resp = self._call(self._ec2.describe_instances, Filters=[
            {"Name": "tag:Name", "Values": [name_tag]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ])
        found = [i for r in resp.get("Reservations", []) for i in r.get("Instances", [])]
        if not found:
            return None
        inst = found[0]
        return {"id": inst["InstanceId"], "state": inst["State"]["Name"]}

    def send_command(self, instance_id: str, script: str) -> str:
        resp = self._call(self._ssm.send_command, InstanceIds=[instance_id],
                          DocumentName="AWS-RunShellScript",
                          Parameters={"commands": [script]},
                          TimeoutSeconds=60, Comment="collector_livecheck (read-only)")
        return resp["Command"]["CommandId"]

    def get_invocation(self, command_id: str, instance_id: str) -> dict:
        try:
            resp = self._call(self._ssm.get_command_invocation, CommandId=command_id, InstanceId=instance_id)
        except LiveCheckError as exc:
            if "InvocationDoesNotExist" in str(exc):
                return {"Status": "Pending", "StandardOutputContent": ""}
            raise
        return {"Status": resp["Status"], "StandardOutputContent": resp.get("StandardOutputContent", ""),
                "StandardErrorContent": resp.get("StandardErrorContent", "")}


_TERMINAL_BAD = {"Failed", "TimedOut", "Cancelled", "Cancelling", "DeliveryTimedOut", "ExecutionTimedOut"}


def run_remote(aws, instance_id: str, timeout_s: float = SSM_TOTAL_TIMEOUT_S,
               sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic) -> str:
    """Send the fixed script and poll (sleeping between polls) until done."""
    try:
        cmd_id = aws.send_command(instance_id, REMOTE_SCRIPT)
    except LiveCheckError as exc:
        if "InvalidInstanceId" in str(exc):
            raise LiveCheckError("SSM agent not reachable on the instance (InvalidInstanceId): "
                                 "is amazon-ssm-agent running and the instance profile attached?") from exc
        raise
    deadline = clock() + timeout_s
    sleep(SSM_INITIAL_WAIT_S)
    while True:
        inv = aws.get_invocation(cmd_id, instance_id)
        status = inv["Status"]
        if status == "Success":
            return inv["StandardOutputContent"]
        if status in _TERMINAL_BAD:
            raise LiveCheckError(f"SSM command {status}: {redact(inv.get('StandardErrorContent', '')[:300])}")
        if clock() >= deadline:
            raise SsmTimeout(f"SSM command still {status} after {timeout_s:.0f}s")
        sleep(SSM_POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Parsing and verdict
# ---------------------------------------------------------------------------
_FRAMES_RE = re.compile(r"\[capture\]\s+(\d+)\s+frames\s*/")
_JOURNAL_TS_RE = re.compile(r"^(\d{9,}(?:\.\d+)?)\s+")


@dataclass
class Snapshot:
    now: Optional[int] = None
    active: str = "unknown"
    active_enter: str = ""
    n_restarts: Optional[int] = None
    main_pid: Optional[int] = None
    rss_kb: Optional[int] = None
    seg_dir: str = ""
    seg_count: int = 0
    seg_bytes: int = 0
    seg_newest_age_s: Optional[int] = None
    disk_free_kb: Optional[int] = None
    last_frames_age_s: Optional[float] = None   # age of newest line with N>0 frames
    journal: list = field(default_factory=list)  # redacted lines only


def _int(v: str) -> Optional[int]:
    try:
        return int(v.strip())
    except (ValueError, AttributeError):
        return None


def parse_remote_output(text: str) -> Snapshot:
    head, _, journal = text.partition("---JOURNAL---")
    kv = {}
    for line in head.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            kv[k.strip()] = v.strip()
    snap = Snapshot(
        now=_int(kv.get("NOW", "")), active=kv.get("ACTIVE", "unknown") or "unknown",
        active_enter=kv.get("ENTER", ""), n_restarts=_int(kv.get("NRESTARTS", "")),
        main_pid=_int(kv.get("MAINPID", "")), rss_kb=_int(kv.get("RSS_KB", "")),
        seg_dir=kv.get("SEG_DIR", ""), seg_count=_int(kv.get("SEG_COUNT", "")) or 0,
        seg_bytes=_int(kv.get("SEG_BYTES", "")) or 0, seg_newest_age_s=_int(kv.get("SEG_NEWEST_AGE_S", "")),
        disk_free_kb=_int(kv.get("DISK_FREE_KB", "")),
    )
    newest_frames_ts = None
    for raw in journal.splitlines():
        if not raw.strip():
            continue
        ts_m, fr_m = _JOURNAL_TS_RE.match(raw), _FRAMES_RE.search(raw)
        if ts_m and fr_m and int(fr_m.group(1)) > 0:
            ts = float(ts_m.group(1))
            newest_frames_ts = ts if newest_frames_ts is None else max(newest_frames_ts, ts)
        snap.journal.append(redact(raw))
    if newest_frames_ts is not None and snap.now is not None:
        snap.last_frames_age_s = max(0.0, snap.now - newest_frames_ts)
    return snap


def in_session(now_utc: datetime) -> bool:
    wib = now_utc.astimezone(WIB)
    trading = CALENDAR.is_trading_day(wib.date()) if CALENDAR is not None else wib.weekday() < 5
    return trading and SESSION_OPEN_HOUR <= wib.hour < SESSION_CLOSE_HOUR


def in_scheduled_window(now_utc: datetime) -> bool:
    """EventBridge runs the instance 08:30-16:25 WIB on trading days (grace after the start)."""
    wib = now_utc.astimezone(WIB)
    trading = CALENDAR.is_trading_day(wib.date()) if CALENDAR is not None else wib.weekday() < 5
    minutes = wib.hour * 60 + wib.minute
    return trading and SCHEDULE_START_MIN + SCHEDULE_GRACE_MIN <= minutes < SCHEDULE_STOP_MIN


@dataclass
class Verdict:
    level: str            # OK / WARN / FAIL
    reasons: list
    in_session: bool

    @property
    def exit_code(self) -> int:
        return {"OK": EXIT_OK, "WARN": EXIT_WARN}.get(self.level, EXIT_FAIL)


def judge(snap: Snapshot, session: bool) -> Verdict:
    if snap.active != "active":
        if session:
            return Verdict("FAIL", [f"collector.service is '{snap.active}' during session hours"], True)
        return Verdict("WARN", [f"collector.service is '{snap.active}' (freshness not expected outside "
                                "session hours)"], False)
    if not session:
        return Verdict("OK", ["outside IDX session hours: segment/frame freshness is not expected"], False)

    notes: list = []
    seg_ok = (snap.seg_count > 0 and snap.seg_newest_age_s is not None
              and snap.seg_newest_age_s < MAX_SEGMENT_AGE_S)
    frames_ok = snap.last_frames_age_s is not None and snap.last_frames_age_s < MAX_FRAME_LINE_AGE_S
    if not seg_ok:
        if snap.seg_count == 0:
            notes.append("no in-progress segment file for today")
        else:
            notes.append(f"newest segment write is {snap.seg_newest_age_s}s old (limit {MAX_SEGMENT_AGE_S}s)")
    if not frames_ok:
        notes.append("no recent '[capture] N frames' log line with N>0 "
                     f"(within {MAX_FRAME_LINE_AGE_S}s) - feed quiet or disconnected")
    low_disk = snap.disk_free_kb is not None and snap.disk_free_kb < MIN_DISK_FREE_KB
    if low_disk:
        notes.append(f"low disk: {snap.disk_free_kb // 1024} MiB free")
    if seg_ok and frames_ok and not low_disk:
        return Verdict("OK", ["active, segment fresh, data frames flowing"], True)
    if not seg_ok and not frames_ok:
        return Verdict("FAIL", notes, True)
    return Verdict("WARN", notes, True)


# ---------------------------------------------------------------------------
# Orchestration and output
# ---------------------------------------------------------------------------
def check(aws, now_utc: datetime, timeout_s: float = SSM_TOTAL_TIMEOUT_S,
          sleep: Callable[[float], None] = time.sleep) -> tuple:
    """Return (exit_code, report dict). Raises AuthError / LiveCheckError."""
    session = in_session(now_utc)
    inst = aws.find_instance(INSTANCE_NAME_TAG)
    if inst is None:
        raise LiveCheckError(f"no instance tagged Name={INSTANCE_NAME_TAG} in {AWS_REGION}")
    report = {"instance": inst, "in_session": session}
    if inst["state"] != "running":
        if in_scheduled_window(now_utc):
            report["verdict"] = {"level": "FAIL", "reasons": [
                f"instance is {inst['state']} but the scheduler should have it running "
                "(08:30-16:25 WIB, trading days)"]}
            return EXIT_FAIL, report
        report["verdict"] = {"level": "OK", "reasons": [
            f"instance is {inst['state']}: stopped by schedule (infra/scheduler.tf: start 08:30, stop 16:25 "
            "WIB, trading days); nothing to check"]}
        return EXIT_OK, report
    out = run_remote(aws, inst["id"], timeout_s=timeout_s, sleep=sleep)
    snap = parse_remote_output(out)
    v = judge(snap, session)
    report["snapshot"] = asdict(snap)
    report["verdict"] = {"level": v.level, "reasons": v.reasons}
    return v.exit_code, report


def render(report: dict) -> str:
    inst, v = report["instance"], report["verdict"]
    lines = [f"instance {inst['id']}: {inst['state']}",
             f"session: {'IDX session hours' if report['in_session'] else 'outside IDX session hours'}"]
    s = report.get("snapshot")
    if s:
        rss = f"{s['rss_kb'] / 1024:.0f} MiB" if s["rss_kb"] is not None else "n/a"
        disk = f"{s['disk_free_kb'] / 1024:.0f} MiB" if s["disk_free_kb"] is not None else "n/a"
        age = f"{s['seg_newest_age_s']}s" if s["seg_newest_age_s"] is not None else "n/a"
        fr = (f"{s['last_frames_age_s']:.0f}s ago" if s["last_frames_age_s"] is not None
              else "none in last journal lines")
        lines += [f"unit: {s['active']}  since: {s['active_enter']}  restarts: {s['n_restarts']}  pid: {s['main_pid']}",
                  f"process RSS (VmRSS): {rss}   disk free: {disk}",
                  f"segments today: {s['seg_count']} files, {s['seg_bytes']:,} bytes, newest write {age} ago",
                  f"last data-frame log line: {fr}",
                  "journal (redacted, last 8):"] + [f"  {l}" for l in s["journal"][-8:]]
    lines.append(f"VERDICT: {v['level']} - " + "; ".join(v["reasons"]))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None, aws=None,
         now: Optional[datetime] = None, sleep: Callable[[float], None] = time.sleep) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--profile", default=None,
                    help="AWS profile name (default: $AWS_PROFILE, else boto3's default chain)")
    ap.add_argument("--timeout", type=float, default=SSM_TOTAL_TIMEOUT_S, help="SSM result timeout (seconds)")
    args = ap.parse_args(argv)
    profile = args.profile or os.environ.get("AWS_PROFILE") or None
    try:
        aws = aws or Boto3Aws(profile=profile)
        code, report = check(aws, now or datetime.now(timezone.utc), args.timeout, sleep)
    except AuthError:
        print(f"AWS credentials missing/invalid for profile {profile or '(default chain)'} - "
              "check ~/.aws/credentials or AWS_PROFILE", file=sys.stderr)
        return EXIT_FAIL
    except SsmTimeout as exc:
        print(f"FAIL: {exc} (instance up but SSM agent not responding?)", file=sys.stderr)
        return EXIT_FAIL
    except LiveCheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return EXIT_FAIL
    print(json.dumps(report, indent=2) if args.json else render(report))
    return code


if __name__ == "__main__":
    sys.exit(main())
