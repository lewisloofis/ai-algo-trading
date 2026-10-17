"""dashboard/recheck_view: the closed-family re-check as one evidence line per family (plan 26)."""
from __future__ import annotations

import json
import os

from dashboard import recheck_view as RV
from dashboard import strategy_families as F
from dashboard import strategy_sections as SEC
from dashboard import strategy_status as S
from dashboard.test_strategy_status import reg


def _doc(as_of="2026-12-01", families=(), excluded=()):
    return {"schema": 1, "as_of": as_of, "families": list(families), "excluded": list(excluded)}


def _fam(fid, status="waiting", n=12, nxt=30, ev=None, **kw):
    return {"family_id": fid, "verdict_date": "2026-10-15", "status": status, "n": n, "next_milestone": nxt,
            "evaluation": ev, **kw}


def _ev(m=30, alert=False):
    return {"milestone": m, "as_of": "2026-12-01", "edge": 4.0, "p": 0.3, "drop5": -2.0, "alert": alert}


def _write(tmp_path, name, doc):
    d = tmp_path / "results" / "recheck"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return str(tmp_path)


def _line(doc, fid):
    return RV.parse(doc).lines[fid]


def test_waiting_line():
    doc = _doc(families=[_fam("daily_momentum")])
    assert _line(doc, "daily_momentum") == "waiting, 12 of 30"


def test_evaluated_no_alert_line():
    doc = _doc(families=[_fam("daily_momentum", "evaluated_now", 31, 60, _ev(30))])
    assert _line(doc, "daily_momentum") == "milestone 30 evaluated, no alert; waiting, 31 of 60"
    done = _doc(families=[_fam("daily_momentum", "evaluated_earlier", 130, None, _ev(120))])
    assert _line(done, "daily_momentum") == "milestone 120 evaluated, no alert"


def test_alert_line_says_a_new_plan_is_needed():
    doc = _doc(families=[_fam("daily_momentum", "evaluated_now", 31, 60, _ev(30, True))])
    assert _line(doc, "daily_momentum").startswith("ALERT at milestone 30 — needs a new pre-registered plan")


def test_excluded_and_untested_use_their_reason():
    doc = _doc(excluded=[{"family_id": "indicator_rules", "reason": "excluded (owner, 2026-10-17): unspent holdout"},
                         {"family_id": "multi_week_trend", "reason": "not re-checked: untested"}])
    assert _line(doc, "indicator_rules") == "excluded (owner, 2026-10-17): unspent holdout"
    assert _line(doc, "multi_week_trend") == "not re-checked: untested"


def test_no_verdict_family_shows_its_reason():
    doc = _doc(families=[{"family_id": "x", "status": "no_verdict", "reason": "not checked: no readable verdict file",
                          "n": None, "next_milestone": None, "evaluation": None, "verdict_date": None}])
    assert _line(doc, "x") == "not checked: no readable verdict file"


def test_load_uses_the_newest_dated_file_and_ignores_the_ledger(tmp_path):
    root = _write(tmp_path, "2026-11-01.json", _doc("2026-11-01", [_fam("daily_momentum", n=3)]))
    _write(tmp_path, "2026-12-01.json", _doc("2026-12-01", [_fam("daily_momentum", n=9)]))
    _write(tmp_path, "milestones.json", {"daily_momentum": [{"milestone": 30}]})
    rc, problem = RV.load_recheck(os.path.join(root, "results"))
    assert problem == "" and rc.as_of == "2026-12-01" and rc.lines["daily_momentum"] == "waiting, 9 of 30"


def test_missing_output_means_no_recheck_and_no_problem(tmp_path):
    assert RV.load_recheck(str(tmp_path / "results")) == (None, "")
    (tmp_path / "results" / "recheck").mkdir(parents=True)
    _write(tmp_path, "milestones.json", {})
    assert RV.load_recheck(str(tmp_path / "results")) == (None, "")


def test_unreadable_file_is_a_problem_not_a_crash(tmp_path):
    root = _write(tmp_path, "2026-12-01.json", "{not json")
    rc, problem = RV.load_recheck(os.path.join(root, "results"))
    assert rc is None and "2026-12-01.json" in problem
    root = _write(tmp_path, "2026-12-02.json", {"schema": 1, "as_of": "2026-12-02", "families": "nope", "excluded": []})
    rc, problem = RV.load_recheck(os.path.join(root, "results"))
    assert rc is None and "2026-12-02.json" in problem


def test_family_rows_append_the_recheck_line_after_the_other_evidence():
    rc = RV.parse(_doc(families=[_fam("daily_momentum")], excluded=[{"family_id": "pairs_stat_arb",
                                                                    "reason": "skipped: fails structurally (no shorting)"}]))
    rows = {f.family_id: f for f in F.family_rows([], None, reg(), None, rc)}
    assert rows["daily_momentum"].evidence[-1] == "Re-check (2026-12-01): waiting, 12 of 30"
    assert rows["pairs_stat_arb"].evidence[-1] == "Re-check (2026-12-01): skipped: fails structurally (no shorting)"
    assert not any(e.startswith("Re-check") for e in rows["mean_reversion"].evidence)


def test_no_recheck_means_no_line():
    rows = F.family_rows([], None, reg(), None, None)
    assert not any(e.startswith("Re-check") for f in rows for e in f.evidence)


def test_line_is_escaped_in_the_page():
    rc = RV.parse(_doc(excluded=[{"family_id": "indicator_rules", "reason": "<b>bad</b> & worse"}]))
    html = SEC.families_section(F.family_rows([], None, reg(), None, rc))
    assert "&lt;b&gt;bad&lt;/b&gt; &amp; worse" in html and "<b>bad</b>" not in html


def test_gather_inputs_loads_recheck_and_reports_a_bad_file(tmp_path):
    results = tmp_path / "results" / "recheck"
    results.mkdir(parents=True)
    (results / "2026-12-01.json").write_text(json.dumps(_doc(families=[_fam("daily_momentum")])))
    inp = S.gather_inputs(str(tmp_path), str(tmp_path))
    assert inp.recheck is not None and inp.recheck.lines["daily_momentum"] == "waiting, 12 of 30"
    (results / "2026-12-01.json").write_text("{bad")
    inp = S.gather_inputs(str(tmp_path), str(tmp_path))
    assert inp.recheck is None and "2026-12-01.json" in inp.recheck_problem
