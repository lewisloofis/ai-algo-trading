"""live_view.py: the Online panel escapes everything and degrades to a static snapshot."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from dashboard import live, live_view

NOW = datetime(2026, 10, 15, 3, 0, 0, tzinfo=timezone.utc)


def _summary(tmp_path, **kw):
    doc = {"wall_clock_ns": int((NOW.timestamp() - 3) * 1e9), "outcome": "running", "sink_kind": "paper",
           "source": "live", "strategy_id": "wifi_momo_h5", **kw}
    (tmp_path / "a.status.json").write_text(json.dumps(doc))
    return live.collect(str(tmp_path), NOW)


def test_panel_escapes_values_from_the_status_file(tmp_path):
    html = live_view.panel(_summary(tmp_path, strategy_id="<img src=x onerror=alert(1)>", error="<b>x</b>"), "t")
    assert "<img" not in html and "&lt;img" in html and "<b>x</b>" not in html


def test_empty_panel_says_so_and_hides_the_table(tmp_path):
    html = live_view.panel(live.collect(str(tmp_path), NOW), "2026-10-15 10:00 WIB")
    assert "No bot session is online right now." in html
    assert '<table id="live-table" hidden>' in html and "(0)" in html
    assert "static snapshot" in html and "make dashboard-live" in html and "Last rebuilt" in html


def test_script_is_inline_safe_and_never_uses_innerhtml(tmp_path):
    summary = _summary(tmp_path)
    html = live_view.panel(summary, "t")
    assert "<script src" not in html and "innerHTML" not in html and "textContent" in html
    assert 'fetch("live.json"' in html and "5000" in html
    for _header, key in live_view.COLUMNS:  # every column the table shows exists on the run object
        assert hasattr(summary.runs[0], key)
