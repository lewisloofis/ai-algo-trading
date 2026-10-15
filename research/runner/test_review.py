"""Daily ops reviewer: rules, redaction, summary writer (subprocess mocked), post-close wiring."""

from __future__ import annotations

import json
import subprocess
import types
from dataclasses import replace
from datetime import date

import pytest

from runner import review, review_rules as rr, review_summary as rs
from runner.config import at
from runner.conftest import (ACCOUNT, DAY, PREV, REGISTRY, CAND_SPEC, PAPER_SPEC, arm_for, jwt, write_live_status,
                             write_live_store, write_status)
from runner.postclose import post_close
from runner.review_redact import sanitize
from runner.state import save_state

SID = "cand_x"
FAKE_ACCOUNT = "TEST001L"
FAKE_JWT = "eyJhbGciOiJub25lIn0.eyJleHAiOjk5OTk5OTk5OTl9.c2lnc2lnc2ln"
ORDER_UUID = "3f2b8c1e-9d4a-4e7b-8a61-5c0d2e9f7a10"
ORDER_NUM = "202610150001234567"


def state_for(**strategies):
    return {"date": str(DAY), "preflight": {"ok": True}, "strategies": strategies, "post_close": None}


def codes(facts, severity=None):
    return [f["code"] for f in facts["flags"] if severity in (None, f["severity"])]


def facts_for(cfg, state, now, **kw):
    return rr.build_facts(cfg, DAY, state, now.timestamp(), **kw)


@pytest.fixture
def hold2_cfg(cfg):
    spec = replace(CAND_SPEC, hold=2)
    return replace(cfg, registry={**REGISTRY, SID: spec})


def buy(price=1000, fill=1000, lots=1, oid="o1"):
    return dict(id=oid, symbol="BBBB", side="buy", price=price, lots=lots, fills=[(fill, lots, 5.0)])


# ---- rules ---------------------------------------------------------------------------------------

def test_clean_day_has_no_alerts_or_warnings(cfg, now):
    cfg = replace(cfg, env={**cfg.env, "SB_ACCESS_TOKEN": jwt(now.timestamp() + 30 * 3600),
                            "SB_BEARER_TOKEN": jwt(now.timestamp() + 30 * 3600)})
    write_status(cfg, "paper_x", now, 30, outcome="complete")
    f = facts_for(cfg, state_for(paper_x={"status": "done", "restarts": 0}), now)
    assert f["counts"]["ALERT"] == 0 and "session_not_started" not in codes(f)
    assert [c for c in codes(f, "WARN") if c not in ("recon_missing",)] == []


@pytest.mark.parametrize("fill,sev", [(1015, "WARN"), (1050, "ALERT"), (1005, None)])
def test_fill_far_from_decision_price(hold2_cfg, now, fill, sev):
    write_live_store(hold2_cfg, SID, [buy(1000, fill)])
    write_live_status(hold2_cfg, SID, now, 5, outcome="complete")
    f = facts_for(hold2_cfg, state_for(**{SID: {"status": "done", "live": True, "max_idr": 500_000}}), now)
    hits = [x for x in f["flags"] if x["code"] == "fill_far_from_decision"]
    assert (hits[0]["severity"] if hits else None) == sev
    assert f["fills"][0]["dev_bps"] == pytest.approx((fill - 1000) * 10)


def test_position_open_past_planned_hold(hold2_cfg, now):
    entry = date(2026, 10, 12)          # Mon; hold 2 -> planned out Wed 14th; now Thu 15th = 3 sessions
    write_live_store(hold2_cfg, SID, [buy()], day=entry)
    f = facts_for(hold2_cfg, state_for(), now)
    hit = [x for x in f["flags"] if x["code"] == "position_past_hold"]
    assert hit and hit[0]["severity"] == "ALERT" and "BBBB" in hit[0]["text"]
    assert f["positions"][0]["sessions_held"] == 3


def test_position_on_its_exit_day_is_a_warning_and_a_closed_one_is_clean(hold2_cfg, now):
    write_live_store(hold2_cfg, SID, [buy()], day=date(2026, 10, 13))               # 2 sessions held == hold
    f = facts_for(hold2_cfg, state_for(), now)
    assert [x["severity"] for x in f["flags"] if x["code"] == "position_past_hold"] == ["WARN"]
    sell = dict(id="o2", symbol="BBBB", side="sell", price=1000, lots=1, fills=[(1000, 1, 5.0)])
    write_live_store(hold2_cfg, SID, [buy(), sell], day=date(2026, 10, 13))
    assert "position_past_hold" not in codes(facts_for(hold2_cfg, state_for(), now))


def _recon(cfg, **doc):
    d = cfg.runs_dir / "recon"
    d.mkdir(parents=True, exist_ok=True)
    base = {"status": "match", "as_of": f"{DAY}T16:21:00+07:00", "positions": [], "errors": [],
            "trade_check": {"checked": True, "matched": 1, "broker_only": 0, "store_only": 0}}
    (d / f"{DAY}.json").write_text(json.dumps({**base, **doc}))


def test_recon_mismatch_and_phantom_are_alerts(cfg, now):
    _recon(cfg, status="mismatch",
           positions=[{"symbol": "BBBB", "status": "mismatch", "expected_lots": 2, "broker_lots": 0}],
           trade_check={"checked": True, "matched": 0, "broker_only": 0, "store_only": 1})
    f = facts_for(cfg, state_for(), now)
    assert {"recon_mismatch", "recon_phantom"} <= set(codes(f, "ALERT"))
    assert f["recon"]["differences"][0]["symbol"] == "BBBB"


def test_recon_clean_stale_and_missing(cfg, now):
    assert "recon_missing" in codes(facts_for(cfg, state_for(), now), "WARN")
    _recon(cfg)
    assert not [c for c in codes(facts_for(cfg, state_for(), now)) if c.startswith("recon")]
    _recon(cfg, as_of="2026-10-12T16:21:00+07:00")
    assert "recon_stale" in codes(facts_for(cfg, state_for(), now), "WARN")


def test_unreadable_source_is_a_warning_not_a_crash(cfg, now):
    (cfg.runs_dir / "recon").mkdir(parents=True)
    (cfg.runs_dir / "recon" / f"{DAY}.json").write_text("{not json")
    f = facts_for(cfg, state_for(), now)
    assert "source_unreadable" in codes(f, "WARN")


def test_missing_stale_status_and_unstarted_session(cfg, now):
    f = facts_for(cfg, state_for(), now)                                       # paper_x never started
    assert "session_not_started" in codes(f, "WARN")
    f = facts_for(cfg, state_for(paper_x={"status": "done"}), now)             # started, no status file
    assert "status_missing" in codes(f, "WARN")
    write_status(cfg, "paper_x", now, 5000, outcome="running")
    f = facts_for(cfg, state_for(paper_x={"status": "started"}), now)
    assert {"status_stale", "session_unfinished"} <= set(codes(f, "WARN"))
    write_status(cfg, "paper_x", now, 5, outcome="error")
    assert "status_error" in codes(facts_for(cfg, state_for(paper_x={"status": "failed"}), now), "ALERT")


def test_preflight_failure_and_missing_preflight(cfg, now):
    assert "no_preflight" in codes(facts_for(cfg, {**state_for(), "preflight": None}, now), "ALERT")
    assert "preflight_failed" in codes(facts_for(cfg, {**state_for(), "preflight": {"ok": False}}, now), "ALERT")


def test_token_flags_use_durations_only(cfg, now):
    env = {**cfg.env, "SB_ACCESS_TOKEN": jwt(now.timestamp() + 10 * 3600),
           "SB_BEARER_TOKEN": jwt(now.timestamp() - 60)}
    c2 = replace(cfg, env=env)
    st = {**state_for(), "token_rotation": {"ok": True, "actions": ["trading session needs a PIN login"]}}
    f = facts_for(c2, st, now)
    assert {"token_expiring": "WARN", "token_expired": "ALERT", "token_action": "ALERT"}.items() <= {
        x["code"]: x["severity"] for x in f["flags"]}.items()
    dump = json.dumps(f)
    assert env["SB_ACCESS_TOKEN"] not in dump and "hours_left" in dump


def test_live_cap_usage_and_armed_not_run(hold2_cfg, now):
    write_live_store(hold2_cfg, SID, [buy(1000, 1000, lots=5)])                # 500,000 IDR notional
    st = state_for(**{SID: {"status": "done", "live": True, "max_idr": 520_000}})
    f = facts_for(hold2_cfg, st, now)
    assert "live_cap_near" in codes(f, "WARN") and f["live_cap"][0]["pct"] == pytest.approx(96.2, abs=0.1)
    f = facts_for(replace(hold2_cfg), state_for(**{SID: {"status": "done", "live": True, "max_idr": 400_000}}), now)
    assert "live_cap_exceeded" in codes(f, "ALERT")
    arm_for(hold2_cfg, SID, 100_000)
    assert "armed_not_run" in codes(facts_for(hold2_cfg, state_for(), now), "ALERT")


def test_cost_report_facts_and_staleness(cfg, now):
    d = cfg.research_dir / "out_cost_report"
    d.mkdir(parents=True)
    (d / "cost_report.json").write_text(json.dumps({
        "generated": "2026-09-01T10:00:00+00:00", "flags": ["a", "b"],
        "aggregates": {"live": {"overall": {"slippage_decision_bps": {"n": 4, "mean": 12.345}}}}}))
    f = facts_for(cfg, state_for(), now)
    assert f["cost_report"]["n_fills"] == {"live": 4} and f["cost_report"]["mean_slippage_decision_bps"] == {"live": 12.3}
    assert "cost_report_stale" in codes(f, "INFO")


def test_log_alert_lines_are_collected(cfg, alerter, now):
    alerter.send("ALERT", f"{DAY} something broke", "detail")
    f = facts_for(cfg, state_for(), now)
    assert f["log"]["n_alert"] == 1 and "something broke" in f["log"]["alerts"][0]


# ---- redaction -------------------------------------------------------------------------------------

def test_redaction_strips_account_jwt_and_order_ids(cfg):
    env = {**cfg.env, "SB_ACCOUNT_NO": FAKE_ACCOUNT}
    dirty = {
        "log": {"alerts": [f"denied for account {FAKE_ACCOUNT}: Bearer {FAKE_JWT}", f"order {ORDER_UUID} rejected",
                           f"order_id={ORDER_NUM} broker id: BRK99812"]},
        "raw_jwt": FAKE_JWT, "account_no": FAKE_ACCOUNT, "order_id": ORDER_NUM, "broker_id": "BRK99812",
        "flags": [{"text": f"{FAKE_ACCOUNT} buy BBBB 5 lots (+1,234 bps) {ORDER_UUID}"}],
        "bought_idr": 300000000, "symbol": "BBBB", "date": "2026-10-15", "strategy": "wifi_momo_h5",
    }
    clean = sanitize(dirty, env)
    dump = json.dumps(clean)
    for secret in (FAKE_ACCOUNT, FAKE_JWT, "eyJhbGci", ORDER_UUID, ORDER_NUM, "BRK99812"):
        assert secret not in dump, secret
    assert "[REDACTED]" in dump
    assert clean["bought_idr"] == 300000000 and clean["symbol"] == "BBBB"           # numbers and symbols survive
    assert clean["date"] == "2026-10-15" and clean["strategy"] == "wifi_momo_h5"
    assert dirty["account_no"] == FAKE_ACCOUNT                                        # input untouched


def test_redaction_masks_runner_secret_values(cfg):
    env = {**cfg.env, "SB_ACCOUNT_NO": "ZZ9", "TELEGRAM_CHAT_ID": "555000111"}
    clean = sanitize({"x": "chat 555000111 acct ZZ9 token " + cfg.env["TELEGRAM_BOT_TOKEN"]}, env)
    assert "555000111" not in json.dumps(clean) and cfg.env["TELEGRAM_BOT_TOKEN"] not in json.dumps(clean)


# ---- summary writer (subprocess mocked) ---------------------------------------------------------------

def facts_min(alerts=0):
    flags = [{"severity": "ALERT", "code": "recon_mismatch", "text": "recon positions do NOT match the broker: BBBB expected 2 broker 0"}] * alerts
    return {"date": "2026-10-15", "counts": {"ALERT": alerts, "WARN": 0, "INFO": 0}, "flags": flags,
            "sessions": [{"strategy": "paper_x", "mode": "paper", "status": "done", "restarts": 0, "heartbeat": "complete"}],
            "steps": [{"name": "dashboard", "result": "ok"}], "recon": None, "scorecards": [], "live_summary": [],
            "paper_summary": ["paper_x: 1 store(s) today, 2 fill(s)"]}


class FakeRun:
    def __init__(self, out="2026-10-15: all quiet. paper_x done.", rc=0, exc=None):
        self.out, self.rc, self.exc, self.calls = out, rc, exc, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(returncode=self.rc, stdout=self.out, stderr="")


def has_claude(_name):
    return "/fake/claude"


def test_claude_is_invoked_without_tools_with_facts_on_stdin(cfg):
    run = FakeRun()
    env = {**cfg.env, "SB_ACCESS_TOKEN": "tok-should-not-reach-child"}
    text, src = rs.summarise(facts_min(), env, run=run, which=has_claude)
    assert (text, src) == ("2026-10-15: all quiet. paper_x done.", "claude")
    cmd, kw = run.calls[0]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--tools") + 1] == ""                                  # all tools disabled
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd and "--disable-slash-commands" in cmd
    assert cmd[cmd.index("--permission-prompts") + 1] == "none" and "--bare" not in cmd
    assert "never invent numbers" in cmd[cmd.index("--system-prompt") + 1]
    assert kw["timeout"] == 60 and kw["input"].startswith("{") and json.loads(kw["input"])["date"] == "2026-10-15"
    assert "SB_ACCESS_TOKEN" not in kw["env"] and "TELEGRAM_BOT_TOKEN" not in kw["env"]
    assert "sb-algo-trading" not in str(kw["cwd"])


def test_model_is_configurable(cfg):
    run = FakeRun()
    rs.summarise(facts_min(), {**cfg.env, "RUNNER_REVIEWER_MODEL": "sonnet"}, run=run, which=has_claude)
    assert run.calls[0][0][run.calls[0][0].index("--model") + 1] == "sonnet"


@pytest.mark.parametrize("kw,why", [
    (dict(exc=FileNotFoundError("claude")), "FileNotFoundError"),
    (dict(exc=subprocess.TimeoutExpired("claude", 60)), "timeout"),
    (dict(rc=1), "RuntimeError"),
    (dict(out="   "), "RuntimeError"),
    (dict(out="paper_x done, 99 fills and 7 alerts"), "ungrounded"),
])
def test_failures_fall_back_to_deterministic_text(cfg, kw, why):
    text, src = rs.summarise(facts_min(), cfg.env, run=FakeRun(**kw), which=has_claude)
    assert src.startswith("fallback:") and why in src
    assert text == rs.fallback_text(facts_min())


def test_off_and_missing_binary_never_start_a_process(cfg):
    run = FakeRun()
    assert rs.summarise(facts_min(), {**cfg.env, "RUNNER_REVIEWER": "off"}, run=run, which=has_claude)[1] == "fallback:off"
    assert rs.summarise(facts_min(), cfg.env, run=run, which=lambda n: None)[1] == "fallback:claude not found"
    assert run.calls == []


def test_model_must_lead_with_an_alert_when_there_is_one(cfg):
    run = FakeRun(out="All fine, nothing to report.")
    assert rs.summarise(facts_min(alerts=1), cfg.env, run=run, which=has_claude)[1].startswith("fallback:")
    run = FakeRun(out="ALERT: recon positions do NOT match the broker: BBBB expected 2 broker 0")
    assert rs.summarise(facts_min(alerts=1), cfg.env, run=run, which=has_claude)[1] == "claude"


def test_output_is_clipped_to_ten_lines(cfg):
    run = FakeRun(out="\n".join(f"line {i}" for i in range(1, 30)))
    text, _ = rs.summarise({**facts_min(), "paper_summary": [f"line {i}" for i in range(1, 30)]}, cfg.env, run=run, which=has_claude)
    assert len(text.splitlines()) == 10


def test_fallback_text_leads_with_counts_then_flags():
    t = rs.fallback_text(facts_min(alerts=1))
    lines = t.splitlines()
    assert lines[0].startswith("2026-10-15 review: 1 ALERT") and lines[1].startswith("ALERT: recon positions")


def test_the_real_cli_flags_exist_locally():
    """Guards against a CLI rename: every flag we pass is documented in `claude --help` (skipped without claude)."""
    import shutil
    if shutil.which("claude") is None:
        pytest.skip("claude not installed")
    helptext = subprocess.run(["claude", "--help"], capture_output=True, text=True, timeout=30).stdout
    for flag in ("--model", "--tools", "--system-prompt", "--no-session-persistence", "--disable-slash-commands",
                 "--strict-mcp-config", "--permission-prompts", "--print"):
        assert flag in helptext, flag


# ---- pipeline + post-close ---------------------------------------------------------------------------------

def test_run_review_sends_only_redacted_facts_and_writes_files(cfg, alerter, now):
    alerter.log(f"order {ORDER_UUID} for account {FAKE_ACCOUNT} {FAKE_JWT}")      # log() already redacts, raw too
    cfg.log_file.write_text(cfg.log_file.read_text() + f"{DAY}T10:00:00+07:00 [ALERT] fill {ORDER_UUID} {FAKE_ACCOUNT} Bearer {FAKE_JWT}\n")
    run = FakeRun()
    rv = review.run_review(replace(cfg, env={**cfg.env, "SB_ACCOUNT_NO": FAKE_ACCOUNT}), DAY, state_for(), now.timestamp(),
                           run=run, which=has_claude)
    sent = run.calls[0][1]["input"]
    for secret in (FAKE_ACCOUNT, FAKE_JWT, ORDER_UUID):
        assert secret not in sent
    jp, tp = review.review_paths(cfg, DAY)
    saved = jp.read_text()
    assert json.loads(saved)["summary_source"] == "claude" and ORDER_UUID not in saved and FAKE_ACCOUNT not in saved
    assert tp.read_text().strip() == rv.text


def test_post_close_sends_one_message_and_writes_review_files(cfg, clock, alerter, ex, post, monkeypatch, now):
    cfg.env.update({"SB_ACCESS_TOKEN": jwt(now.timestamp() + 40 * 3600), "SB_BEARER_TOKEN": jwt(now.timestamp() + 40 * 3600)})
    monkeypatch.setattr(rs, "_run", FakeRun(out="2026-10-15: all quiet. refresh-prices ok."))
    save_state(cfg.state_path(DAY), state_for())
    clock.t = at(DAY, "16:20")
    post_close(cfg, DAY, clock, ex, alerter)
    msgs = [p[1]["text"] for p in post.posts]
    assert len(msgs) == 1 and "daily summary" in msgs[0] and "all quiet" in msgs[0]
    jp, tp = review.review_paths(cfg, DAY)
    assert jp.exists() and tp.exists() and json.loads(jp.read_text())["date"] == str(DAY)


def test_post_close_message_is_an_alert_when_the_rules_found_one(cfg, clock, alerter, ex, post):
    clock.t = at(DAY, "16:20")
    post_close(cfg, DAY, clock, ex, alerter)                 # fake claude unavailable -> fallback; no preflight -> ALERT
    assert len(post.posts) == 1 and post.posts[0][1]["text"].startswith("[ALERT]")
    assert "no preflight recorded" in post.posts[0][1]["text"]


def test_a_broken_reviewer_falls_back_to_the_plain_summary(cfg, clock, alerter, ex, post, monkeypatch):
    monkeypatch.setattr(review, "run_review", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    clock.t = at(DAY, "16:20")
    assert post_close(cfg, DAY, clock, ex, alerter) == 0
    assert any("daily summary" in p[1]["text"] and "sessions:" in p[1]["text"] for p in post.posts)


def test_reviewer_off_in_env_uses_plain_text(cfg, clock, alerter, ex, post, monkeypatch):
    seen = []
    monkeypatch.setattr(rs, "_run", lambda *a, **k: seen.append(1))
    clock.t = at(DAY, "16:20")
    post_close(replace(cfg, env={**cfg.env, "RUNNER_REVIEWER": "off"}), DAY, clock, ex, alerter)
    assert seen == [] and "review:" in post.posts[0][1]["text"]
