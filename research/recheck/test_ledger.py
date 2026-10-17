"""
recheck/test_ledger.py — the milestone ledger: a milestone is written once and never re-evaluated.
"""

from __future__ import annotations

import json

import pytest

from recheck import ledger as L


def _entry(m=30, **kw):
    base = dict(milestone=m, as_of="2026-12-01", edge=4.2, p=0.5, drop5=-1.0, alert=False)
    base.update(kw)
    return base


def test_missing_ledger_is_empty(tmp_path):
    assert L.load(str(tmp_path / "milestones.json")) == {}


def test_append_then_load_round_trips_and_keeps_other_families(tmp_path):
    path = str(tmp_path / "milestones.json")
    L.append(path, "alpha", _entry(30))
    L.append(path, "beta", _entry(30, alert=True))
    L.append(path, "alpha", _entry(60))
    got = L.load(path)
    assert [e["milestone"] for e in got["alpha"]] == [30, 60]
    assert got["beta"][0]["alert"] is True


def test_a_milestone_already_in_the_ledger_is_refused(tmp_path):
    path = str(tmp_path / "milestones.json")
    L.append(path, "alpha", _entry(30))
    with pytest.raises(ValueError, match="already"):
        L.append(path, "alpha", _entry(30, edge=99.0))
    assert L.load(path)["alpha"][0]["edge"] == 4.2


def test_done_milestones_and_latest(tmp_path):
    path = str(tmp_path / "milestones.json")
    L.append(path, "alpha", _entry(30))
    L.append(path, "alpha", _entry(60))
    led = L.load(path)
    assert L.done(led, "alpha") == [30, 60] and L.done(led, "beta") == []
    assert L.latest(led, "alpha")["milestone"] == 60 and L.latest(led, "beta") is None


def test_unreadable_ledger_raises_instead_of_resetting(tmp_path):
    p = tmp_path / "milestones.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="milestones.json"):
        L.load(str(p))
    p.write_text(json.dumps({"alpha": "nope"}))
    with pytest.raises(ValueError):
        L.load(str(p))
