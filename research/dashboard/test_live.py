"""live.py: classify every heartbeat file; never crash on a bad one."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from dashboard import live
from dashboard.health import HEARTBEAT_STALE_S

NOW = datetime(2026, 10, 15, 3, 0, 0, tzinfo=timezone.utc)


def _doc(age_s, outcome="running", **kw):
    wall = NOW - timedelta(seconds=age_s)
    doc = {"run_id": "r", "wall_clock_ns": int(wall.timestamp() * 1e9), "source": "live", "sink_kind": "paper",
           "strategy_id": "wifi_momo_h5", "outcome": outcome,
           "progress": {"events_seen": 1234},
           "risk": {"kill_switch_tripped": False, "day_notional_idr": 1_500_000.0,
                    "positions_idr": {"WIFI": 750000.0, "PTRO": 0}}}
    doc.update(kw)
    return doc


def _write(runs, name, doc):
    (runs / name).write_text(doc if isinstance(doc, str) else json.dumps(doc))


def test_fresh_running_file_is_online(tmp_path):
    _write(tmp_path, "a.status.json", _doc(5))
    s = live.collect(str(tmp_path), NOW)
    assert s.online_count == 1
    r = s.runs[0]
    assert (r.state, r.tone, r.strategy_id, r.mode, r.source) == ("online", "green", "wifi_momo_h5", "practice (paper)", "live")
    assert r.events_seen == 1234 and r.positions_text == "WIFI Rp 750,000" and r.notional_text == "Rp 1,500,000"
    assert r.written_wib == "2026-10-15 09:59:55 WIB" and r.heartbeat_text == "5s ago"
    assert r.kill_text == "off"


def test_heartbeat_boundary(tmp_path):
    _write(tmp_path, "a.status.json", _doc(HEARTBEAT_STALE_S))
    _write(tmp_path, "b.status.json", _doc(HEARTBEAT_STALE_S + 1))
    by = {r.file: r.state for r in live.collect(str(tmp_path), NOW).runs}
    assert by == {"a.status.json": "online", "b.status.json": "stopped"}


def test_running_but_days_old_is_stopped_not_online(tmp_path):
    _write(tmp_path, "a.status.json", _doc(2 * 86400))
    s = live.collect(str(tmp_path), NOW)
    assert s.online_count == 0 and s.runs[0].state == "stopped"
    assert s.runs[0].state_label == "Stopped without ending" and s.runs[0].tone == "amber"


def test_all_fresh_files_are_listed_not_just_newest(tmp_path):
    _write(tmp_path, "a.status.json", _doc(5, strategy_id="wifi_momo_h5"))
    _write(tmp_path, "b.status.json", _doc(1, strategy_id="ptro_momo_h5"))
    s = live.collect(str(tmp_path), NOW)
    assert s.online_count == 2
    assert [r.strategy_id for r in s.runs] == ["ptro_momo_h5", "wifi_momo_h5"]


def test_finished_and_error_states_and_recency_window(tmp_path):
    _write(tmp_path, "fin.status.json", _doc(3600, outcome="complete"))
    _write(tmp_path, "err.status.json", _doc(7200, outcome="error", error="boom"))
    _write(tmp_path, "old.status.json", _doc(5 * 86400, outcome="complete"))
    _write(tmp_path, "oldrun.status.json", _doc(5 * 86400))  # stale "running", too old to list
    _write(tmp_path, "on.status.json", _doc(3))
    s = live.collect(str(tmp_path), NOW)
    by = {r.file: r for r in s.runs}
    assert set(by) == {"fin.status.json", "err.status.json", "on.status.json"}
    assert by["fin.status.json"].state == "finished" and by["err.status.json"].state == "error"
    assert by["err.status.json"].error == "boom" and by["err.status.json"].tone == "red"
    assert s.runs[0].state == "online"  # online always first


def test_malformed_and_odd_files_are_noted_not_fatal(tmp_path):
    _write(tmp_path, "bad.status.json", "{not json")
    _write(tmp_path, "list.status.json", "[1,2]")
    _write(tmp_path, "empty.status.json", {})  # no timestamp, no outcome: too old to list
    _write(tmp_path, "good.status.json", _doc(2))
    s = live.collect(str(tmp_path), NOW)
    assert [r.file for r in s.runs] == ["good.status.json"]
    assert len(s.notes) == 2 and any("bad.status.json" in n for n in s.notes)


def test_running_without_timestamp_is_never_online(tmp_path):
    _write(tmp_path, "a.status.json", {"outcome": "running", "strategy_id": "x"})
    assert live.collect(str(tmp_path), NOW).online_count == 0


def test_written_at_fallback_and_kill_switch_and_future_clock(tmp_path):
    doc = _doc(0, risk={"kill_switch_tripped": True, "kill_switch_reason": "file"})
    del doc["wall_clock_ns"]
    doc["written_at"] = (NOW + timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    _write(tmp_path, "a.status.json", doc)
    r = live.collect(str(tmp_path), NOW).runs[0]
    assert r.state == "online" and r.heartbeat_age_s == 0.0
    assert r.kill_switch and r.kill_text == "TRIPPED: file" and r.positions_text == "–"


def test_json_roundtrip_and_missing_dir(tmp_path):
    _write(tmp_path, "a.status.json", _doc(5, sink_kind="live"))
    j = live.collect(str(tmp_path), NOW).to_json()
    assert json.loads(json.dumps(j))["runs"][0]["mode"] == "real money"
    assert live.collect(str(tmp_path / "missing"), NOW).runs == ()
