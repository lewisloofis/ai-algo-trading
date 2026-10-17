"""AwsS3Store: conditional-put semantics with the aws CLI faked (no AWS call)."""
from __future__ import annotations

import subprocess

import pytest

from screener import pit_store as S


def _store(monkeypatch, script):
    store = S.AwsS3Store("bkt", "ap-southeast-3")
    calls = []

    def fake_run(args):
        calls.append(args)
        rc, err = script(args)
        return subprocess.CompletedProcess(args, rc, "", err)

    monkeypatch.setattr(store, "_run", fake_run)
    return store, calls


def test_head_403_then_conditional_put_precondition_failed_is_exists(monkeypatch):
    def script(args):
        if args[0] == "head-object":
            return 254, "An error occurred (403) when calling HeadObject: Forbidden"
        return 254, "An error occurred (PreconditionFailed) ... 412"
    store, calls = _store(monkeypatch, script)
    assert store.exists("k") is None
    assert store.put("k", b"x", "application/json") == "exists"
    assert "--if-none-match" in calls[-1]


def test_missing_object_is_written_conditionally(monkeypatch):
    def script(args):
        return (254, "(404) Not Found") if args[0] == "head-object" else (0, "")
    store, calls = _store(monkeypatch, script)
    assert store.put("k", b"x", "application/json") == "written"
    assert calls[-1][-2:] == ["--if-none-match", "*"]


def test_present_object_is_never_put(monkeypatch):
    store, calls = _store(monkeypatch, lambda a: (0, ""))
    assert store.put("k", b"x", "t") == "exists"
    assert all(c[0] == "head-object" for c in calls)


def test_old_cli_writes_unguarded_when_absence_unconfirmed(monkeypatch):
    def script(args):
        if args[0] == "head-object":
            return 254, "(403) Forbidden"
        return (252, "Unknown options: --if-none-match, *") if "--if-none-match" in args else (0, "")
    store, _ = _store(monkeypatch, script)
    assert store.put("k", b"x", "t") == "written_unguarded"   # versioning is the backstop


def test_old_cli_falls_back_only_when_absence_confirmed(monkeypatch):
    def script(args):
        if args[0] == "head-object":
            return 254, "(404) Not Found"
        return (252, "Unknown options: --if-none-match") if "--if-none-match" in args else (0, "")
    store, _ = _store(monkeypatch, script)
    assert store.put("k", b"x", "t") == "written"
