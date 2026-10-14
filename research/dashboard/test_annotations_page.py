"""Page-level annotation entries: the current decision and the timeline."""
from __future__ import annotations

import json

from dashboard.annotations import (
    DEFAULT_PATH,
    Decision,
    load_annotations,
    load_decision,
    load_timeline,
)


def test_reserved_keys_are_not_run_annotations(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps(
            {
                "_current_decision": {"headline": "h", "reason": "r", "as_of": "d", "source": "s"},
                "r1": {"note": "n", "source": "s"},
            }
        )
    )
    assert list(load_annotations(str(p))) == ["r1"]


def test_current_decision_is_read(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps(
            {
                "_current_decision": {
                    "headline": "No strategy is live-trading.",
                    "reason": "why",
                    "as_of": "2026-10-14",
                    "source": "s",
                }
            }
        )
    )
    assert load_decision(str(p)) == Decision("No strategy is live-trading.", "why", "2026-10-14", "s")


def test_missing_or_malformed_decision_is_none_not_a_crash(tmp_path):
    assert load_decision(str(tmp_path / "nope.json")) is None
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"_current_decision": {"headline": "only"}}))
    assert load_decision(str(p)) is None


def test_timeline_merges_run_notes_by_date_oldest_first(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(
        json.dumps(
            {
                "_timeline": [
                    {"date": "2026-10-14", "note": "b", "source": "s"},
                    {"date": "2026-09-12", "note": "a", "source": "s"},
                ],
                "20261013T065258Z-a383cdef": {"note": "run note", "source": "s"},
            }
        )
    )
    assert [e.date for e in load_timeline(str(p))] == ["2026-09-12", "2026-10-13", "2026-10-14"]


def test_the_checked_in_file_has_a_decision_and_a_timeline():
    d = load_decision(DEFAULT_PATH)
    assert d is not None and d.headline.startswith("No strategy is live-trading")
    assert len(load_timeline(DEFAULT_PATH)) >= 4
