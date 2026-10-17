"""
strategy_fit/test_study_verdicts.py — verdict files: validate, record (upsert), load (latest wins).
"""

from __future__ import annotations

import json
import os

import pytest

from strategy_fit import study_verdicts as SV

FAMILIES = frozenset({"alpha", "beta"})


def _v(family="alpha", outcome="FAIL", decided="2026-10-17", doc="docs/plans/x.md", **kw) -> SV.Verdict:
    base = dict(family_id=family, outcome=outcome, decided=decided, source="plan 99 §10",
                doc=doc, summary="net −50 bps")
    base.update(kw)
    return SV.Verdict(**base)


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "x.md").write_text("# x\n")
    results = tmp_path / "research" / "results"
    results.mkdir(parents=True)
    return tmp_path, results


def _record(repo, study, v):
    root, results = repo
    return SV.record(str(results), study, v, family_ids=FAMILIES, repo_root=str(root))


def _load(repo):
    root, results = repo
    return SV.load_verdicts(str(results), family_ids=FAMILIES, repo_root=str(root))


def test_record_then_load_round_trips(repo):
    path = _record(repo, "study_a", _v())
    assert path.endswith(os.path.join("study_a", "verdict.json"))
    loaded = _load(repo)
    assert loaded.problems == ()
    assert loaded.verdicts["alpha"] == _v()
    assert loaded.outcome("alpha") == "FAIL"
    assert loaded.outcome("beta") == "untested"


def test_record_upserts_by_family_and_keeps_others(repo):
    _record(repo, "s", _v("alpha", "FAIL"))
    _record(repo, "s", _v("beta", "INCONCLUSIVE"))
    _record(repo, "s", _v("alpha", "NO_GO"))
    _, results = repo
    doc = json.loads((results / "s" / "verdict.json").read_text())
    assert doc["schema"] == SV.SCHEMA_VERSION
    assert [(r["family_id"], r["outcome"]) for r in doc["verdicts"]] == [("alpha", "NO_GO"), ("beta", "INCONCLUSIVE")]


@pytest.mark.parametrize("bad, why", [
    (dict(outcome="MAYBE"), "outcome"),
    (dict(outcome="untested"), "outcome"),
    (dict(family="gamma"), "not in the family catalog"),
    (dict(decided="17-10-2026"), "decided"),
    (dict(doc="docs/plans/missing.md"), "does not exist"),
    (dict(doc="/etc/passwd"), "repo-relative"),
    (dict(doc="../outside.md"), "repo-relative"),
    (dict(summary=" "), "summary"),
])
def test_record_rejects_invalid_verdicts(repo, bad, why):
    with pytest.raises(ValueError, match=why):
        _record(repo, "s", _v(**bad))
    _, results = repo
    assert not (results / "s" / "verdict.json").exists()


@pytest.mark.parametrize("study", ["", "a/b", "..", ".hidden"])
def test_record_rejects_bad_study_names(repo, study):
    with pytest.raises(ValueError, match="study"):
        _record(repo, study, _v())


def test_latest_decision_wins_across_studies(repo):
    _record(repo, "old", _v(outcome="INCONCLUSIVE", decided="2026-06-01"))
    _record(repo, "new", _v(outcome="FAIL", decided="2026-10-01"))
    loaded = _load(repo)
    assert loaded.outcome("alpha") == "FAIL"
    assert loaded.problems == ()


def test_same_day_conflict_is_a_problem_not_a_silent_pick(repo):
    _record(repo, "a", _v(outcome="INCONCLUSIVE"))
    _record(repo, "b", _v(outcome="FAIL"))
    loaded = _load(repo)
    assert "alpha" not in loaded.verdicts
    assert any("alpha" in p and "same day" in p for p in loaded.problems)


def test_unreadable_and_invalid_files_are_reported_and_others_still_load(repo):
    _, results = repo
    _record(repo, "good", _v("beta"))
    (results / "broken").mkdir()
    (results / "broken" / "verdict.json").write_text("{not json")
    (results / "invalid").mkdir()
    (results / "invalid" / "verdict.json").write_text(json.dumps(
        {"schema": SV.SCHEMA_VERSION, "verdicts": [{"family_id": "alpha", "outcome": "MAYBE"}]}))
    (results / "future").mkdir()
    (results / "future" / "verdict.json").write_text(json.dumps({"schema": 99, "verdicts": []}))
    loaded = _load(repo)
    assert loaded.outcome("beta") == "FAIL"
    assert "alpha" not in loaded.verdicts
    joined = " | ".join(loaded.problems)
    for name in ("broken", "invalid", "future"):
        assert os.path.join(name, "verdict.json") in joined


def test_missing_results_dir_loads_empty(tmp_path):
    loaded = SV.load_verdicts(str(tmp_path / "nope"), family_ids=FAMILIES, repo_root=str(tmp_path))
    assert loaded.verdicts == {} and loaded.problems == ()


def test_cli_record(repo, capsys):
    root, results = repo
    rc = SV.main([
        "record", "--results-dir", str(results), "--repo-root", str(root),
        "--study", "s", "--family", "alpha", "--outcome", "FAIL", "--decided", "2026-10-17",
        "--source", "plan 99 §10", "--doc", "docs/plans/x.md", "--summary", "net −50 bps",
    ], family_ids=FAMILIES)
    assert rc == 0
    assert "alpha" in capsys.readouterr().out
    assert _load(repo).verdicts["alpha"] == _v()


def test_cli_record_invalid_exits_nonzero(repo, capsys):
    root, results = repo
    rc = SV.main([
        "record", "--results-dir", str(results), "--repo-root", str(root),
        "--study", "s", "--family", "gamma", "--outcome", "FAIL", "--decided", "2026-10-17",
        "--source", "x", "--doc", "docs/plans/x.md", "--summary", "y",
    ], family_ids=FAMILIES)
    assert rc == 2
    assert "not in the family catalog" in capsys.readouterr().err


def test_committed_verdict_files_are_valid():
    """The real research/results verdict files load cleanly against the real catalog."""
    loaded = SV.load_verdicts()
    assert loaded.problems == (), loaded.problems
    assert loaded.outcome("obi_ofi_tick") == "NO_GO"
