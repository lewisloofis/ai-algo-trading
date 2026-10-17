"""serve.py: loopback-only, no path traversal, atomic swap, rebuild policy, live.json."""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from dashboard import serve


def _site(root, body="v1"):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.html"), "w") as f:
        f.write(f"<html>{body}</html>")
    with open(os.path.join(root, "trades.html"), "w") as f:
        f.write("trades")
    return str(root)


# --- path safety ------------------------------------------------------------

@pytest.mark.parametrize("url", ["/../secret.txt", "/%2e%2e/secret.txt", "/a/../../secret.txt",
                                 "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt", "//etc/passwd", "/\\..\\secret.txt",
                                 "/%00", "/sub/../../secret.txt"])
def test_resolve_path_never_leaves_the_root(tmp_path, url):
    root = _site(tmp_path / "site")
    (tmp_path / "secret.txt").write_text("nope")
    got = serve.resolve_path(root, url)
    assert got is None or os.path.realpath(got).startswith(os.path.realpath(root) + os.sep)
    assert got is None or "secret" not in got


def test_resolve_path_blocks_symlink_escape(tmp_path):
    root = _site(tmp_path / "site")
    (tmp_path / "secret.txt").write_text("nope")
    os.symlink(str(tmp_path / "secret.txt"), os.path.join(root, "link.txt"))
    assert serve.resolve_path(root, "/link.txt") is None


def test_resolve_path_basics(tmp_path):
    root = _site(tmp_path / "site")
    assert serve.resolve_path(root, "/").endswith("index.html")
    assert serve.resolve_path(root, "/trades.html?x=1").endswith("trades.html")
    assert serve.resolve_path(root, "/missing.html") is None


# --- bind policy ------------------------------------------------------------

@pytest.mark.parametrize("host", ["0.0.0.0", "", "192.168.1.5", "::", "example.com"])
def test_non_loopback_bind_is_refused(host):
    with pytest.raises(serve.BindRefused):
        serve.check_host(host)
    with pytest.raises(serve.BindRefused):
        serve.create_server(host, 0, serve.SiteHolder(), ".")


def test_loopback_hosts_accepted():
    assert serve.check_host("127.0.0.1") == "127.0.0.1" and serve.check_host("localhost") == "127.0.0.1"


# --- holder + rebuilder -----------------------------------------------------

def test_holder_swaps_and_deletes_the_generation_before_last(tmp_path):
    h = serve.SiteHolder()
    dirs = [_site(tmp_path / n) for n in "abc"]
    for d in dirs:
        h.swap(d, datetime.now(timezone.utc))
    assert h.snapshot()[0] == dirs[2]
    assert not os.path.exists(dirs[0]) and os.path.exists(dirs[1]) and os.path.exists(dirs[2])
    h.discard_all()
    assert not os.path.exists(dirs[1]) and not os.path.exists(dirs[2])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _rebuilder(tmp_path, fp, build, every=300, gap=30, log=None):
    clock = Clock()
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    holder = serve.SiteHolder()
    rb = serve.Rebuilder(holder, build, lambda: fp["v"], str(work), every, gap, clock, log or (lambda _m: None))
    return rb, holder, clock


def test_first_step_builds_then_periodic_and_change_rules(tmp_path):
    fp, builds = {"v": 1}, []
    rb, _holder, clock = _rebuilder(tmp_path, fp, lambda out: builds.append(_site(out, f"b{len(builds)}")))
    assert rb.step() and len(builds) == 1
    clock.t += 10
    assert not rb.step()  # nothing changed, not yet due
    fp["v"] = 2
    clock.t += 10
    assert not rb.step()  # changed, first sighting (not settled) and inside the gap
    clock.t += 31
    assert rb.step() and len(builds) == 2  # settled, outside the gap
    clock.t += 301
    assert rb.step() and len(builds) == 3  # periodic


def test_a_constantly_changing_input_only_rebuilds_periodically(tmp_path):
    fp, builds = {"v": 0}, []
    rb, _h, clock = _rebuilder(tmp_path, fp, lambda out: builds.append(_site(out)))
    rb.step()
    for _ in range(20):
        fp["v"] += 1
        clock.t += 10
        rb.step()
    assert len(builds) == 1  # 200s elapsed < 300s; the input never settled, so no early rebuild


def test_failed_build_keeps_the_previous_site_and_logs(tmp_path):
    fp, logs, state = {"v": 1}, [], {"fail": False}

    def build(out):
        if state["fail"]:
            raise RuntimeError("boom")
        _site(out, "good")

    rb, holder, _clock = _rebuilder(tmp_path, fp, build, log=logs.append)
    assert rb.build_now()
    good = holder.snapshot()[0]
    state["fail"] = True
    assert not rb.build_now()
    assert holder.snapshot()[0] == good and os.path.exists(good)
    assert any("boom" in m and "previous site" in m for m in logs)
    assert len(os.listdir(tmp_path / "work")) == 1  # the failed temp dir was removed


def test_two_builds_never_overlap(tmp_path):
    fp = {"v": 1}
    inside, gate, results = threading.Event(), threading.Event(), []

    def build(out):
        _site(out)
        inside.set()
        gate.wait(5)

    rb, _h, _c = _rebuilder(tmp_path, fp, build)
    t = threading.Thread(target=lambda: results.append(rb.build_now()))
    t.start()
    assert inside.wait(5)
    assert rb.build_now() is False  # a second one is refused while the first runs
    gate.set()
    t.join(5)
    assert results == [True]


def test_fingerprint_ignores_heartbeat_mtime_but_sees_outcome_and_db_changes(tmp_path):
    runs, daily = tmp_path / "runs", tmp_path / "daily"
    runs.mkdir()
    daily.mkdir()
    st = runs / "a.status.json"
    st.write_text(json.dumps({"outcome": "running", "run_id": "r"}))
    base = serve.fingerprint(str(runs), str(daily))
    st.write_text(json.dumps({"outcome": "running", "run_id": "r", "progress": {"events_seen": 9}}))
    os.utime(st, (1, 1))
    assert serve.fingerprint(str(runs), str(daily)) == base
    st.write_text(json.dumps({"outcome": "complete", "run_id": "r"}))
    assert serve.fingerprint(str(runs), str(daily)) != base
    base = serve.fingerprint(str(runs), str(daily))
    (runs / "x.db").write_text("x")
    assert serve.fingerprint(str(runs), str(daily)) != base
    base = serve.fingerprint(str(runs), str(daily))
    (daily / "AAAA.parquet").write_text("x")
    assert serve.fingerprint(str(runs), str(daily)) != base


def _research_world(tmp_path):
    runs, daily = tmp_path / "runs", tmp_path / "daily"
    results, plans = tmp_path / "research" / "results", tmp_path / "repo" / "docs" / "plans"
    git = tmp_path / "repo" / ".git"
    for d in (runs, daily, results / "s1", plans, git / "refs" / "heads"):
        d.mkdir(parents=True)
    (plans / "01-x.md").write_text("plan")
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")

    def fp():
        return serve.fingerprint(str(runs), str(daily), results_dir=str(results),
                                 plans_dir=str(plans), git_dir=str(git))
    return fp, results, plans, git


def test_fingerprint_sees_new_verdict_but_not_identical_resave(tmp_path):
    fp, results, _plans, _git = _research_world(tmp_path)
    base = fp()
    v = results / "s1" / "verdict.json"
    v.write_text('{"schema": 1, "verdicts": []}')
    added = fp()
    assert added != base
    v.write_text('{"schema": 1, "verdicts": []}')
    os.utime(v, (1, 1))
    assert fp() == added
    v.write_text('{"schema": 1, "verdicts": [1]}')
    assert fp() != added


def test_fingerprint_sees_plan_touch_and_new_plan(tmp_path):
    fp, _results, plans, _git = _research_world(tmp_path)
    base = fp()
    os.utime(plans / "01-x.md", (5, 5))
    changed = fp()
    assert changed != base
    (plans / "02-y.md").write_text("new")
    assert fp() != changed


def test_fingerprint_sees_head_ref_and_commit_changes(tmp_path):
    fp, _results, _plans, git = _research_world(tmp_path)
    base = fp()
    (git / "refs" / "heads" / "main").write_text("b" * 40 + "\n")
    assert fp() != base
    base = fp()
    (git / "HEAD").write_text("c" * 40 + "\n")  # detached
    assert fp() != base


def test_fingerprint_reads_packed_refs_when_ref_file_is_missing(tmp_path):
    fp, _results, _plans, git = _research_world(tmp_path)
    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack-refs\n" + "d" * 40 + " refs/heads/main\n")
    packed = fp()
    (git / "packed-refs").write_text("# pack-refs\n" + "e" * 40 + " refs/heads/main\n")
    assert fp() != packed


def test_fingerprint_without_git_or_research_dirs_is_fine(tmp_path):
    runs, daily = tmp_path / "runs", tmp_path / "daily"
    runs.mkdir()
    daily.mkdir()
    gone = str(tmp_path / "nope")
    fp = serve.fingerprint(str(runs), str(daily), results_dir=gone, plans_dir=gone, git_dir=gone)
    assert fp == serve.fingerprint(str(runs), str(daily), results_dir=gone, plans_dir=gone, git_dir=gone)


# --- end to end over an ephemeral localhost port ------------------------------

@pytest.fixture
def running(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    holder = serve.SiteHolder()
    holder.swap(_site(tmp_path / "site1"), datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc))
    srv = serve.create_server("127.0.0.1", 0, holder, str(runs))
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", holder, runs, tmp_path
    srv.shutdown()
    srv.server_close()
    t.join(5)


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_serves_pages_and_blocks_traversal_over_http(running):
    base, _h, _runs, tmp = running
    (tmp / "secret.txt").write_text("TOPSECRET")
    code, body, hdr = _get(base + "/")
    assert code == 200 and b"v1" in body and hdr["Content-Type"].startswith("text/html")
    assert _get(base + "/trades.html")[0] == 200
    assert _get(base + "/nope.html")[0] == 404
    for bad in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        code, body, _ = _get(base + bad)
        assert code == 404 and b"TOPSECRET" not in body


def test_live_json_is_fresh_per_request_and_carries_build_time(running):
    base, _holder, runs, _tmp = running
    code, body, hdr = _get(base + "/live.json")
    doc = json.loads(body)
    assert code == 200 and hdr["Content-Type"].startswith("application/json")
    assert doc["runs"] == [] and doc["online_count"] == 0 and doc["site_built_wib"] == "2026-10-15 10:00:00 WIB"
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    (runs / "a.status.json").write_text(json.dumps({
        "wall_clock_ns": now_ns, "outcome": "running", "strategy_id": "wifi_momo_h5", "sink_kind": "paper"}))
    doc = json.loads(_get(base + "/live.json")[1])
    assert doc["online_count"] == 1 and doc["runs"][0]["strategy_id"] == "wifi_momo_h5"


def test_swap_is_seen_by_the_next_request(running):
    base, holder, _runs, tmp = running
    holder.swap(_site(tmp / "site2", "v2"), datetime.now(timezone.utc))
    assert b"v2" in _get(base + "/")[1]


def test_head_and_post(running):
    base, *_ = running
    req = urllib.request.Request(base + "/", method="HEAD")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200 and r.read() == b""
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(urllib.request.Request(base + "/", data=b"x", method="POST"), timeout=5)
    assert e.value.code == 501


def test_cli_refuses_a_public_bind_before_building_anything(capsys):
    from dashboard.__main__ import main

    assert main(["serve", "--host", "0.0.0.0", "--no-healthcheck"]) == 2
    assert "127.0.0.1 only" in capsys.readouterr().err
