"""Unit tests for scripts/collector_livecheck.py — fakes only, no AWS, no network."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collector_livecheck as lc  # noqa: E402

NOW_EPOCH = 1_760_000_000
SESSION = datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc)    # Wed 11:00 WIB
AFTER_HOURS = datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc)  # Wed 18:00 WIB
SATURDAY = datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def plain_calendar(monkeypatch):
    monkeypatch.setattr(lc, "CALENDAR", None)  # deterministic Mon-Fri rule


def remote_out(active="active", seg_age=20, frames_age=10, frames=250, count=1, disk_kb=4_000_000):
    lines = [f"NOW={NOW_EPOCH}", f"ACTIVE={active}", "ENTER=Wed 2026-10-14 08:30:05 UTC", "NRESTARTS=0",
             "MAINPID=812", "RSS_KB=48000", "SEG_DIR=/var/lib/collector/segments/dt=2026-10-14",
             f"SEG_COUNT={count}", "SEG_BYTES=1234567"]
    if seg_age is not None:
        lines.append(f"SEG_NEWEST_AGE_S={seg_age}")
    lines += [f"DISK_FREE_KB={disk_kb}", "---JOURNAL---"]
    if frames_age is not None:
        lines.append(f"{NOW_EPOCH - frames_age}.1 host collector[812]: 2026/10/14 [capture] {frames} frames / 80.1 KB "
                     "in last 15s (9000 frames total)")
    lines.append(f"{NOW_EPOCH - 5}.1 host collector[812]: [capture] no frames in last 15s (market closed or quiet)"
                 " - 0 frames total")
    return "\n".join(lines)


# ---- verdict logic ---------------------------------------------------------
def verdict(text, now=SESSION):
    return lc.judge(lc.parse_remote_output(text), lc.in_session(now))


def test_ok_in_session():
    v = verdict(remote_out())
    assert (v.level, v.exit_code) == ("OK", 0)


def test_not_active_in_session_fails():
    assert verdict(remote_out(active="failed")).level == "FAIL"


def test_stale_segment_but_frames_is_warn():
    v = verdict(remote_out(seg_age=900))
    assert v.level == "WARN" and "900s old" in v.reasons[0]


def test_no_frames_but_fresh_segment_is_warn():
    assert verdict(remote_out(frames_age=None)).level == "WARN"


def test_frames_line_too_old_counts_as_missing():
    assert verdict(remote_out(frames_age=600)).level == "WARN"


def test_no_segment_and_no_frames_fails():
    v = verdict(remote_out(count=0, seg_age=None, frames_age=None))
    assert v.level == "FAIL" and v.exit_code == 2


def test_zero_frame_lines_are_not_data():
    assert verdict(remote_out(frames=0)).level == "WARN"


def test_frames_total_reset_is_irrelevant():
    text = remote_out().replace("(9000 frames total)", "(3 frames total)")
    assert verdict(text).level == "OK"


def test_low_disk_warns():
    assert verdict(remote_out(disk_kb=1000)).level == "WARN"


def test_outside_session_does_not_require_freshness():
    v = verdict(remote_out(seg_age=99999, frames_age=None), AFTER_HOURS)
    assert v.level == "OK" and "not expected" in v.reasons[0]


def test_weekend_is_outside_session():
    assert not lc.in_session(SATURDAY)


def test_inactive_outside_session_is_warn_not_fail():
    assert verdict(remote_out(active="inactive"), AFTER_HOURS).level == "WARN"


# ---- redaction ---------------------------------------------------------------
@pytest.mark.parametrize("raw,secret", [
    ("auth eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl ok", "eyJhbGci"),
    ("Authorization: Bearer abc123.def456-ghi", "abc123"),
    ("login failed token=s3cr3tvalue retry", "s3cr3tvalue"),
    ("password: hunter2hunter2", "hunter2"),
    ("blob QWxhZGRpbjpvcGVuIHNlc2FtZTEyMzQ1Njc4OTBhYmNkZWY= end", "QWxhZGRpbjpv"),
])
def test_redaction(raw, secret):
    out = lc.redact(raw)
    assert secret not in out and "REDACTED" in out


def test_redaction_keeps_normal_log_lines():
    line = "[capture] 250 frames / 80.1 KB in last 15s (9000 frames total)"
    assert lc.redact(line) == line


def test_journal_is_redacted_in_snapshot_and_output():
    text = remote_out() + "\n1.0 h c[1]: refresh token=SECRETVALUE99 sent"
    snap = lc.parse_remote_output(text)
    assert not any("SECRETVALUE99" in l for l in snap.journal)


# ---- fake AWS ----------------------------------------------------------------
class FakeAws:
    def __init__(self, state="running", out=None, statuses=("Success",), auth_fail=False, send_exc=None):
        self.state, self.out, self.statuses = state, out if out is not None else remote_out(), list(statuses)
        self.auth_fail, self.send_exc = auth_fail, send_exc
        self.sent: list = []

    def find_instance(self, name):
        if self.auth_fail:
            raise lc.AuthError("ExpiredToken")
        return {"id": "i-0fake", "state": self.state}

    def send_command(self, iid, script):
        if self.send_exc:
            raise self.send_exc
        self.sent.append(script)
        return "cmd-1"

    def get_invocation(self, cid, iid):
        st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return {"Status": st, "StandardOutputContent": self.out if st == "Success" else "",
                "StandardErrorContent": "boom"}


def test_check_ok_polls_with_sleep():
    sleeps = []
    aws = FakeAws(statuses=("Pending", "InProgress", "Success"))
    code, report = lc.check(aws, SESSION, sleep=sleeps.append)
    assert code == 0 and report["verdict"]["level"] == "OK"
    assert len(sleeps) == 3  # initial wait + 2 polls


def test_stopped_instance_in_session_exits_nonzero():
    code, report = lc.check(FakeAws(state="stopped"), SESSION, sleep=lambda s: None)
    assert code == 2 and "stopped" in report["verdict"]["reasons"][0]
    assert not FakeAws(state="stopped").sent


def test_stopped_instance_outside_schedule_is_ok():
    for when in (AFTER_HOURS, SATURDAY):
        code, report = lc.check(FakeAws(state="stopped"), when, sleep=lambda s: None)
        assert code == 0 and report["verdict"]["level"] == "OK"
        assert "stopped by schedule" in report["verdict"]["reasons"][0]


def test_stopped_in_premarket_window_fails_after_grace():
    pre = datetime(2026, 10, 14, 1, 50, tzinfo=timezone.utc)  # 08:50 WIB, before 09:00 open
    assert lc.check(FakeAws(state="stopped"), pre, sleep=lambda s: None)[0] == 2
    early = datetime(2026, 10, 14, 1, 32, tzinfo=timezone.utc)  # 08:32 WIB, within grace
    assert lc.check(FakeAws(state="stopped"), early, sleep=lambda s: None)[0] == 0


def test_ssm_timeout():
    ticks = iter(range(0, 10_000, 40))
    aws = FakeAws(statuses=("InProgress",))
    with pytest.raises(lc.SsmTimeout):
        lc.run_remote(aws, "i-0fake", timeout_s=90, sleep=lambda s: None, clock=lambda: next(ticks))


def test_ssm_failed_status_raises():
    with pytest.raises(lc.LiveCheckError, match="Failed"):
        lc.run_remote(FakeAws(statuses=("Failed",)), "i-0fake", sleep=lambda s: None)


def test_ssm_agent_unreachable_message():
    aws = FakeAws(send_exc=lc.LiveCheckError("InvalidInstanceId: not in a valid state"))
    with pytest.raises(lc.LiveCheckError, match="SSM agent"):
        lc.run_remote(aws, "i-0fake", sleep=lambda s: None)


# ---- main(): exit codes and messages ---------------------------------------------
def test_main_expired_creds(capsys, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    rc = lc.main(["--profile", "myprof"], aws=FakeAws(auth_fail=True), now=SESSION, sleep=lambda s: None)
    err = capsys.readouterr().err
    assert rc == 2 and "myprof" in err and "~/.aws/credentials or AWS_PROFILE" in err and "sso" not in err


def test_main_uses_aws_profile_env(capsys, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "envprof")
    rc = lc.main([], aws=FakeAws(auth_fail=True), now=SESSION, sleep=lambda s: None)
    assert rc == 2 and "envprof" in capsys.readouterr().err


def test_main_ok_prints_report(capsys):
    rc = lc.main([], aws=FakeAws(), now=SESSION, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert rc == 0 and "VERDICT: OK" in out and "process RSS (VmRSS): 47 MiB" in out


def test_main_ssm_timeout_exit_2(capsys, monkeypatch):
    def boom(*a, **k):
        raise lc.SsmTimeout("still InProgress after 90s")
    monkeypatch.setattr(lc, "check", boom)
    assert lc.main([], aws=object(), now=SESSION) == 2


def test_remote_script_is_read_only():
    s = lc.REMOTE_SCRIPT
    for banned in ("sudo", "cat ", "rm ", ">>", "tee", "ssm get", "EnvironmentFile", "/etc/", "get-parameter"):
        assert banned not in s
    assert "MemoryCurrent" not in s and "VmRSS" in s
