"""
strategy_fit/test_research_log.py — the research activity log: validate, append, load running/recent.
"""

from __future__ import annotations

import json

import pytest

from strategy_fit import research_log as RL

FAMILIES = frozenset({"alpha", "beta"})


def _e(event="start", study="s1", ts="2026-10-17T10:00:00+07:00", outcome=None, **kw) -> RL.Event:
    base = dict(ts=ts, event=event, study=study, plan="plan 26", families=("alpha",),
                outcome=outcome, note="looking at it")
    base.update(kw)
    return RL.Event(**base)


def _fin(**kw) -> RL.Event:
    return _e("finish", outcome=kw.pop("outcome", "FAIL"), **kw)


def _load(path):
    return RL.load(str(path), family_ids=FAMILIES)


def _append(path, ev):
    RL.append(str(path), ev, family_ids=FAMILIES)


def test_round_trip_and_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "log.jsonl"
    _append(p, _e())
    _append(p, _fin(ts="2026-10-17T11:00:00+07:00"))
    state = _load(p)
    assert [e.event for e in state.events] == ["start", "finish"]
    assert state.events[1].outcome == "FAIL"
    assert state.problems == ()
    row = json.loads(p.read_text().splitlines()[0])
    assert row["schema"] == 1 and "outcome" not in row


def test_running_and_recent(tmp_path):
    p = tmp_path / "log.jsonl"
    _append(p, _e(study="a", ts="2026-10-17T09:00:00+07:00"))
    _append(p, _e(study="b", ts="2026-10-17T09:30:00+07:00"))
    _append(p, _fin(study="a", ts="2026-10-17T10:00:00+07:00"))
    st = _load(p)
    assert [e.study for e in st.running] == ["b"]
    assert [e.study for e in st.recent] == ["a"]
    _append(p, _e(study="a", ts="2026-10-17T11:00:00+07:00"))
    st = _load(p)
    assert {e.study for e in st.running} == {"a", "b"}
    assert [e.event for e in st.running] == ["start", "start"]


def test_recent_is_newest_first_and_finish_without_start_is_fine(tmp_path):
    p = tmp_path / "log.jsonl"
    _append(p, _fin(study="old", ts="2026-10-16T10:00:00+07:00"))
    _append(p, _fin(study="new", ts="2026-10-17T10:00:00+07:00"))
    st = _load(p)
    assert [e.study for e in st.recent] == ["new", "old"]
    assert st.running == () and st.problems == ()


@pytest.mark.parametrize("bad", [
    dict(event="pause"),
    dict(event="start", outcome="FAIL"),
    dict(event="finish", outcome=None),
    dict(event="finish", outcome="MAYBE"),
    dict(families=("nope",)),
    dict(study="../x"),
    dict(study=""),
    dict(note=""),
    dict(note="   "),
    dict(note="two\nlines"),
    dict(ts="2026-10-17T10:00:00"),
    dict(ts="2026-10-17T10:00:00+00:00"),
    dict(plan=""),
])
def test_invalid_event_raises_and_writes_nothing(tmp_path, bad):
    p = tmp_path / "log.jsonl"
    with pytest.raises(ValueError):
        _append(p, _e(**bad))
    assert not p.exists()


def test_empty_families_allowed(tmp_path):
    p = tmp_path / "log.jsonl"
    _append(p, _e(families=()))
    assert _load(p).events[0].families == ()


def test_bad_lines_reported_and_others_load(tmp_path):
    p = tmp_path / "log.jsonl"
    _append(p, _e(study="a"))
    with open(p, "a") as f:
        f.write("not json\n")
        f.write(json.dumps({"schema": 1, "event": "start"}) + "\n")
        f.write(json.dumps({"schema": 2}) + "\n")
        f.write("\n")
    _append(p, _e(study="b"))
    st = _load(p)
    assert [e.study for e in st.events] == ["a", "b"]
    assert len(st.problems) == 3
    assert st.problems[0].startswith("line 2:")


def test_missing_file_is_empty_without_problem(tmp_path):
    st = _load(tmp_path / "none.jsonl")
    assert st.events == () and st.running == () and st.recent == () and st.problems == ()


def test_cli_start_finish_show(tmp_path, capsys):
    from strategy_fit.catalog_meta import ALL_FAMILIES

    fam = ALL_FAMILIES[0].family_id
    log = str(tmp_path / "log.jsonl")
    common = ["--study", "s1", "--plan", "plan 26", "--families", fam, "--note", "go"]
    assert RL.main(["start", "--log", log, *common]) == 0
    assert RL.main(["show", "--log", log]) == 0
    assert "s1" in capsys.readouterr().out
    assert RL.main(["finish", "--log", log, "--outcome", "FAIL", *common]) == 0
    st = RL.load(log)
    assert st.running == () and st.recent[0].outcome == "FAIL"
    assert st.events[0].ts.endswith("+07:00") and st.events[1].ts.endswith("+07:00")
    assert RL.main(["show", "--log", log]) == 0


def test_cli_ts_override_and_empty_families(tmp_path):
    log = str(tmp_path / "log.jsonl")
    assert RL.main(["start", "--log", log, "--study", "s", "--plan", "p", "--families", "",
                    "--note", "n", "--ts", "2026-01-02T03:04:05+07:00"]) == 0
    assert RL.load(log).events[0].ts == "2026-01-02T03:04:05+07:00"


def test_cli_invalid_input_exits_2_and_writes_nothing(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    base = ["--log", str(log), "--study", "s1", "--plan", "p", "--note", "n"]
    assert RL.main(["start", *base, "--families", "bogus"]) == 2
    assert RL.main(["finish", *base, "--families", "", "--outcome", "WRONG"]) == 2
    assert "not recorded" in capsys.readouterr().err
    assert not log.exists()


def test_cli_show_exits_1_on_problems(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    log.write_text("garbage\n")
    assert RL.main(["show", "--log", str(log)]) == 1
    assert "line 1" in capsys.readouterr().err


def test_cli_show_missing_file_exits_0(tmp_path, capsys):
    assert RL.main(["show", "--log", str(tmp_path / "x.jsonl")]) == 0
    out = capsys.readouterr().out
    assert "No research is running" in out
