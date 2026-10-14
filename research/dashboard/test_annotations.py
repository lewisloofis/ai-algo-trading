"""Operator annotations: checked-in, run_id-keyed, rendered verbatim and labelled."""
from __future__ import annotations

import json

from dashboard.annotations import Annotation, DEFAULT_PATH, load_annotations


def test_a_missing_file_is_no_annotations(tmp_path):
    assert load_annotations(str(tmp_path / "nope.json")) == {}


def test_none_is_no_annotations():
    assert load_annotations(None) == {}


def test_reads_note_source_and_optional_reject_reason(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"r1": {"note": "n", "source": "s", "reject_reason": "x"},
                             "r2": {"note": "m", "source": "t"}}))
    got = load_annotations(str(p))
    assert got["r1"] == Annotation("r1", "n", "s", "x")
    assert got["r2"].reject_reason is None


def test_an_unparseable_file_is_treated_as_absent(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{ nope")
    assert load_annotations(str(p)) == {}


def test_the_checked_in_file_parses_and_every_entry_names_its_source():
    got = load_annotations(DEFAULT_PATH)
    assert got
    assert all(a.note and a.source for a in got.values())
