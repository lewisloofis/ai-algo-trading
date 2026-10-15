"""cost_view: reads the cost report JSON and shows it; never computes; labels paper as simulator-only."""

from __future__ import annotations

import json

from dashboard import cost_view as cv


def _stat(n, mean=0.0, enough=False):
    return {"n": n, "mean": mean if n else None, "median": mean if n else None, "sd": None, "ci95": None,
            "enough": enough, "note": ""}


def _doc():
    agg = lambda n: {"overall": {"fee_resid_bps": _stat(n), "slippage_decision_bps": _stat(n),
                                 "slippage_arrival_bps": _stat(0), "resid_bps": _stat(0)}}
    rt = lambda n: {"overall": {"realised_fee_bps": _stat(n, 38.6)}}
    return {"generated": "2026-10-15T07:34:51+00:00",
            "coverage": {"live": {"fills": 4}, "paper": {"fills": 1}},
            "aggregates": {"live": agg(4), "paper": agg(1)},
            "round_trip_aggregates": {"live": rt(2), "paper": rt(0)}}


def test_missing_report_says_how_to_build_it(tmp_path):
    assert cv.load_cost_report(str(tmp_path)) is None
    html = cv.cost_section(None)
    assert "Not measured yet" in html and "make cost-report" in html


def test_unreadable_or_malformed_report_is_treated_as_missing(tmp_path):
    d = tmp_path / "out_cost_report"
    d.mkdir()
    (d / "cost_report.json").write_text("{not json")
    assert cv.load_cost_report(str(tmp_path)) is None
    (d / "cost_report.json").write_text(json.dumps({"aggregates": 1}))
    assert cv.load_cost_report(str(tmp_path)) is None


def test_section_separates_real_and_practice_and_says_too_few():
    html = cv.cost_section(_doc())
    assert "Real money" in html and "Practice (simulated)" in html
    assert "only checks the simulator" in html and "too few to conclude" in html
    assert "not measured" in html or "Spread and market impact are not measured" in html


def test_section_round_trips_through_the_loader(tmp_path):
    d = tmp_path / "out_cost_report"
    d.mkdir()
    (d / "cost_report.json").write_text(json.dumps(_doc()))
    doc = cv.load_cost_report(str(tmp_path))
    assert doc is not None and "+0.0 bps (n=4)" in cv.cost_section(doc)
