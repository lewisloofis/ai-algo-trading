"""The guard is the single gate: a live command exists only if decide_mode said LIVE, and it is
re-verified at the moment of use. Everything else stays refused."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner import arm, guard, modes, supervise
from runner.conftest import ACCOUNT, CAND_SPEC, DAY, REGISTRY, trading_jwt
from runner.executor import Executor
from runner.test_live_modes import gate_for, write_arm
from strategy_fit import emitter_specs as es

REPO_GO_ARMED = Path(__file__).resolve().parents[2] / "strategy" / "live" / "armed.go"


def token(tmp_path, max_idr=500_000):
    write_arm(tmp_path / "runs", strategies={"cand_x": {"max_idr": max_idr}})
    m = modes.decide_mode(CAND_SPEC, gate_for(tmp_path / "runs"))
    assert m.mode == modes.LIVE
    return m.token


def command(tmp_path, max_idr=500_000, account=ACCOUNT):
    return guard.build_live_command(token(tmp_path, max_idr), registry=REGISTRY, repo_root=tmp_path,
                                    rebalance_at="09:30", account=account)


# ---- hand-built live commands are refused everywhere --------------------------------------------

@pytest.mark.parametrize("argv", [
    ["bin/live-session", "--sink", "live", "--source", "live", "--account", "X-1234", "--equity-idr", "1"],
    ["/repo/bin/live-session", "--sink", "paper"],
    ["bin/strategy-run", "--sink", "live"],
    ["python3", "-m", "strategy_fit.paper_day", "--strategy", "cand_x"],
    ["make", "emit-target", "STRATEGY=cand_x"],
    ["env", "ALGO_LIVE=true", "bin/live-session"],
    ["python3", "-m", "runner.supervise", "--live-grant", "g.json", "--", "bin/live-session"],
    ["bin/live-session", "--max-buy-notional-idr", "1", "--retry", "2"],
])
def test_validate_argv_still_refuses_every_hand_built_live_command(argv):
    with pytest.raises(guard.LiveGuardError):
        guard.validate_argv(argv, registry=REGISTRY)


def test_executor_run_and_spawn_refuse_live_argv(tmp_path):
    called = []
    e = Executor(run=lambda *a, **k: called.append(1), popen=lambda *a, **k: called.append(2), registry=REGISTRY)
    live = ["bin/live-session", "--sink", "live", "--account", "X-1234"]
    with pytest.raises(guard.LiveGuardError):
        e.run(live, {}, tmp_path, tmp_path / "l")
    with pytest.raises(guard.LiveGuardError):
        e.spawn(live, {}, tmp_path, tmp_path / "l", tmp_path / "rc", "python3")
    assert not called


def test_supervise_paper_path_refuses_live(tmp_path):
    rc = tmp_path / "rc.json"
    assert supervise.main(["--rc-file", str(rc), "--", "bin/live-session", "--sink", "live"],
                          popen=lambda *a, **k: 1 / 0) == supervise.RC_REFUSED


@pytest.mark.parametrize("fake", [
    ["bin/live-session", "--sink", "live"],
    SimpleNamespace(argv=("bin/live-session",), token=None),
    None,
])
def test_spawn_live_refuses_anything_that_is_not_a_sealed_live_command(tmp_path, fake):
    popen_calls = []
    e = Executor(popen=lambda *a, **k: popen_calls.append(1), registry=REGISTRY)
    with pytest.raises(guard.LiveGuardError):
        e.spawn_live(fake, {}, tmp_path, tmp_path / "l", tmp_path / "rc", "python3", tmp_path / "g.json", True)
    assert not popen_calls


def test_a_live_command_cannot_be_constructed_by_hand(tmp_path):
    with pytest.raises(TypeError):
        guard.LiveCommand(object(), None, (), (), tmp_path, tmp_path, tmp_path, tmp_path, "09:30", "X")
    with pytest.raises(guard.LiveGuardError):
        guard.build_live_command(SimpleNamespace(strategy_id="cand_x", day=DAY, max_idr=1), registry=REGISTRY,
                                 repo_root=tmp_path, rebalance_at="09:30", account=ACCOUNT)


# ---- what a legitimate live command looks like -------------------------------------------------

def test_live_command_shape_and_masking(tmp_path):
    cmd = command(tmp_path)
    argv = list(cmd.argv)
    assert Path(argv[0]).name == "live-session"
    assert argv[argv.index("--sink") + 1] == "live" and argv[argv.index("--source") + 1] == "live"
    assert argv[argv.index("--max-restarts") + 1] == "0"
    assert argv[argv.index("--max-buy-notional-idr") + 1] == "500000"
    assert argv[argv.index("--equity-idr") + 1] == "500000"
    assert argv[argv.index("--symbols") + 1] == "BBBB" and argv[argv.index("--rebalance-at") + 1] == "09:30"
    assert "--retry" not in argv and "--allow-position-mismatch" not in argv
    assert not any("ALGO_" in a for a in argv)
    assert ACCOUNT in argv and ACCOUNT not in cmd.display() and "[ACCOUNT]" in cmd.display()
    assert cmd.emit_args[:2] == ("emit", "--strategy") and "--as-of" in cmd.emit_args
    guard.validate_live_command(cmd, REGISTRY)


@pytest.mark.parametrize("account", ["", "-rm", "a b", "x;y", "ab"])
def test_live_command_refuses_a_bad_account_value(tmp_path, account):
    with pytest.raises(guard.LiveGuardError):
        command(tmp_path, account=account)


def test_live_command_refuses_a_non_candidate_spec(tmp_path):
    tok = token(tmp_path)
    reg = {**REGISTRY, "cand_x": replace(CAND_SPEC, status=es.STATUS_PAPER)}
    with pytest.raises(guard.LiveGuardError, match="candidate"):
        guard.build_live_command(tok, registry=reg, repo_root=tmp_path, rebalance_at="09:30", account=ACCOUNT)


# ---- re-verification at the moment of use ---------------------------------------------------------

def test_validate_refuses_when_the_arm_file_is_removed_changed_or_invalid(tmp_path):
    cmd = command(tmp_path)
    arm.disarm(tmp_path / "runs", DAY)
    with pytest.raises(guard.LiveGuardError, match="arm file"):
        guard.validate_live_command(cmd, REGISTRY)
    write_arm(tmp_path / "runs", strategies={"cand_x": {"max_idr": 400_000}})
    with pytest.raises(guard.LiveGuardError, match="arm file"):
        guard.validate_live_command(cmd, REGISTRY)
    write_arm(tmp_path / "runs", strategies={"cand_x": {"max_idr": 0}})
    with pytest.raises(guard.LiveGuardError):
        guard.validate_live_command(cmd, REGISTRY)


def test_validate_refuses_with_the_kill_file(tmp_path):
    cmd = command(tmp_path)
    (tmp_path / "runs" / "KILL").write_text("")
    with pytest.raises(guard.LiveGuardError, match="kill"):
        guard.validate_live_command(cmd, REGISTRY)


def test_validate_refuses_when_the_spec_stops_being_a_candidate(tmp_path):
    cmd = command(tmp_path)
    for status in (es.STATUS_PAPER, es.STATUS_RETIRED):
        reg = {**REGISTRY, "cand_x": replace(CAND_SPEC, status=status)}
        with pytest.raises(guard.LiveGuardError, match="candidate"):
            guard.validate_live_command(cmd, reg)


def test_validate_refuses_a_tampered_command(tmp_path):
    cmd = command(tmp_path)
    for tampered in (cmd.argv + ("--retry", "2"),
                     tuple(a if a != "0" else "5" for a in cmd.argv),               # --max-restarts 5
                     tuple("paper" if a == "live" else a for a in cmd.argv),
                     tuple("999999" if a == "500000" else a for a in cmd.argv),     # cap inflated
                     cmd.argv[:-2] + ("--allow-position-mismatch", "true")):
        forged = guard.LiveCommand.__new__(guard.LiveCommand)
        for name in guard.LiveCommand.__slots__:
            object.__setattr__(forged, name, getattr(cmd, name))
        object.__setattr__(forged, "argv", tampered)
        with pytest.raises(guard.LiveGuardError):
            guard.validate_live_command(forged, REGISTRY)


def test_a_live_command_is_immutable_and_not_copyable(tmp_path):
    import copy
    cmd = command(tmp_path)
    with pytest.raises(AttributeError):
        cmd.argv = ()                                                   # type: ignore[misc]
    with pytest.raises(TypeError):
        copy.copy(cmd)


# ---- the arming variables ---------------------------------------------------------------------

def test_arming_variables_exist_only_in_the_live_child_env(tmp_path):
    cmd = command(tmp_path)
    env = {"SB_TRADING_ACCESS_TOKEN": "t", "SB_ACCESS_TOKEN": "a", "ALGO_LIVE": "no", "ALGO_CONFIRM": "stale"}
    live = guard.live_child_env(cmd, env, REGISTRY)
    assert live["ALGO_LIVE"] == "true" and live["ALGO_CONFIRM"] == guard.LIVE_CONFIRM_PHRASE
    assert live["SB_TRADING_ACCESS_TOKEN"] == "t"
    wrapper = guard.wrapper_env(cmd, env, REGISTRY)
    assert "ALGO_LIVE" not in wrapper and "ALGO_CONFIRM" not in wrapper and wrapper["SB_TRADING_ACCESS_TOKEN"] == "t"
    assert "ALGO_LIVE" not in guard.child_env(env) and "SB_TRADING_ACCESS_TOKEN" not in guard.child_env(env)


def test_live_env_is_refused_for_an_unauthorised_command(tmp_path):
    cmd = command(tmp_path)
    arm.disarm(tmp_path / "runs", DAY)
    with pytest.raises(guard.LiveGuardError):
        guard.live_child_env(cmd, {}, REGISTRY)


def test_confirm_phrase_matches_the_go_constant():
    text = REPO_GO_ARMED.read_text()
    assert re.search(r'LiveConfirmPhrase\s*=\s*"' + re.escape(guard.LIVE_CONFIRM_PHRASE) + '"', text)


def test_executor_spawn_live_wraps_supervise_with_no_arming_in_its_env(tmp_path):
    cmd = command(tmp_path)
    seen = {}

    class Proc:
        pid = 4242

    def popen(argv, **kw):
        seen.update(argv=argv, env=kw["env"])
        return Proc()
    e = Executor(popen=popen, registry=REGISTRY)
    env = {"SB_TRADING_ACCESS_TOKEN": "t", "ALGO_LIVE": "true", "ALGO_CONFIRM": "x", "SB_ACCOUNT_NO": ACCOUNT}
    pid = e.spawn_live(cmd, env, tmp_path, tmp_path / "log", tmp_path / "rc.json", "python3", tmp_path / "g.json", True)
    assert pid == 4242
    assert seen["argv"][:4] == ["python3", "-m", "runner.supervise", "--rc-file"]
    assert "--live-grant" in seen["argv"] and seen["argv"][seen["argv"].index("--") + 1:] == list(cmd.argv)
    assert "ALGO_LIVE" not in seen["env"] and "ALGO_CONFIRM" not in seen["env"]
    grant = json.loads((tmp_path / "g.json").read_text())
    assert grant["strategy_id"] == "cand_x" and grant["max_idr"] == 500_000 and grant["preflight_ok"] is True
    assert "t" != grant.get("token") and "SB_TRADING_ACCESS_TOKEN" not in json.dumps(grant)


def test_executor_spawn_live_refuses_when_disarmed_in_between(tmp_path):
    cmd = command(tmp_path)
    arm.disarm(tmp_path / "runs", DAY)
    calls = []
    e = Executor(popen=lambda *a, **k: calls.append(1), registry=REGISTRY)
    with pytest.raises(guard.LiveGuardError):
        e.spawn_live(cmd, {}, tmp_path, tmp_path / "log", tmp_path / "rc.json", "python3", tmp_path / "g.json", True)
    assert not calls and not (tmp_path / "g.json").exists()


# ---- supervise: the live path re-authorises before it starts anything ---------------------------

class FakePopen:
    def __init__(self, rc=0):
        self.rc, self.sent = rc, []

    def wait(self):
        return self.rc

    def send_signal(self, s):
        self.sent.append(s)


def live_run(tmp_path, *, mutate=None, emit_rc=0, target=None, env=None, today=DAY, argv_edit=None):
    """Run supervise's live path with fakes. Returns (rc, popen_calls, emit_calls)."""
    cmd = command(tmp_path)
    grant = {"strategy_id": "cand_x", "day": str(DAY), "max_idr": 500_000, "repo_root": str(tmp_path),
             "rebalance_at": "09:30", "data_dir": str(cmd.data_dir), "preflight_ok": True}
    gp = tmp_path / "grant.json"
    gp.write_text(json.dumps(grant))
    if mutate:
        mutate()
    popen_calls, emit_calls = [], []

    def popen(argv, **kw):
        popen_calls.append((argv, kw))
        return FakePopen()

    def emit(args, registry):
        emit_calls.append(list(args))
        if target is not None:
            cmd.target.parent.mkdir(parents=True, exist_ok=True)
            cmd.target.write_text(json.dumps(target))
        return emit_rc
    env = env if env is not None else {"SB_TRADING_ACCESS_TOKEN": trading_jwt(1_800_000_000 + 99_999 + 20_000),
                                       "SB_ACCESS_TOKEN": "a", "SB_ACCOUNT_NO": ACCOUNT}
    argv = list(cmd.argv) if argv_edit is None else argv_edit(list(cmd.argv))
    rc = supervise.main(["--rc-file", str(tmp_path / "rc.json"), "--live-grant", str(gp), "--", *argv],
                        popen=popen, registry=REGISTRY, env=env, emit=emit, today=today, now_epoch=1_800_000_000 + 99_999)
    return rc, popen_calls, emit_calls


OK_TARGET = {"base_notional_idr": 400_000, "targets": [{"symbol": "BBBB", "weight": 1.0}]}


def test_supervise_live_starts_live_session_with_arming_only_in_its_env(tmp_path):
    rc, popen_calls, emit_calls = live_run(tmp_path, target=OK_TARGET)
    assert rc == 0 and len(emit_calls) == 1 and len(popen_calls) == 1
    argv, kw = popen_calls[0]
    assert Path(argv[0]).name == "live-session"
    assert kw["env"]["ALGO_LIVE"] == "true" and kw["env"]["ALGO_CONFIRM"] == guard.LIVE_CONFIRM_PHRASE
    assert json.loads((tmp_path / "rc.json").read_text())["rc"] == 0


@pytest.mark.parametrize("name,mutate", [
    ("disarmed", lambda tp: arm.disarm(tp, DAY)),
    ("kill file", lambda tp: (tp / "KILL").write_text("")),
])
def test_supervise_live_refuses_when_the_world_changed(tmp_path, name, mutate):
    rc, popen_calls, emit_calls = live_run(tmp_path, mutate=lambda: mutate(tmp_path / "runs"), target=OK_TARGET)
    assert rc == supervise.RC_REFUSED and not popen_calls and not emit_calls, name


def test_supervise_live_refuses_a_command_line_that_differs_from_the_authorised_one(tmp_path):
    for edit in (lambda a: a + ["--retry", "2"], lambda a: [x if x != "500000" else "999999" for x in a],
                 lambda a: [x if x != "live" else "paper" for x in a]):
        rc, popen_calls, emit_calls = live_run(tmp_path, target=OK_TARGET, argv_edit=edit)
        assert rc == supervise.RC_REFUSED and not popen_calls and not emit_calls


def test_supervise_live_refuses_on_a_bad_trading_token_or_date(tmp_path):
    stale = {"SB_TRADING_ACCESS_TOKEN": trading_jwt(1_800_000_000 + 99_999 + 60), "SB_ACCESS_TOKEN": "a",
             "SB_ACCOUNT_NO": ACCOUNT}
    rc, popen_calls, _ = live_run(tmp_path, target=OK_TARGET, env=stale)
    assert rc == supervise.RC_REFUSED and not popen_calls
    wrong_acct = {"SB_TRADING_ACCESS_TOKEN": trading_jwt(1_800_000_000 + 99_999 + 20_000, acn="CORE-BOOK-1"),
                  "SB_ACCESS_TOKEN": "a", "SB_ACCOUNT_NO": ACCOUNT}
    rc, popen_calls, _ = live_run(tmp_path, target=OK_TARGET, env=wrong_acct)
    assert rc == supervise.RC_REFUSED and not popen_calls
    rc, popen_calls, _ = live_run(tmp_path, target=OK_TARGET, today=DAY.replace(day=14))
    assert rc == supervise.RC_REFUSED and not popen_calls


def test_supervise_live_refuses_a_target_bigger_than_the_cap(tmp_path):
    big = {"base_notional_idr": 700_000, "targets": [{"symbol": "BBBB", "weight": 1.0}]}
    rc, popen_calls, emit_calls = live_run(tmp_path, target=big)
    assert rc == supervise.RC_TARGET_TOO_BIG and len(emit_calls) == 1 and not popen_calls
    flat = {"base_notional_idr": 700_000, "targets": [{"symbol": "BBBB", "weight": 0.0}]}     # an exit day buys nothing
    rc, popen_calls, _ = live_run(tmp_path, target=flat)
    assert rc == 0 and len(popen_calls) == 1


def test_supervise_live_refuses_an_unreadable_target_and_a_failed_emit(tmp_path):
    rc, popen_calls, _ = live_run(tmp_path, target={"nope": 1})
    assert rc == supervise.RC_REFUSED and not popen_calls
    rc, popen_calls, _ = live_run(tmp_path, emit_rc=4, target=OK_TARGET)
    assert rc == 4 and not popen_calls


def test_the_emit_args_are_real_target_emitter_args(tmp_path):
    """The runner's emit step must be accepted by the real emitter (not just by a fake)."""
    from strategy_fit import target_emitter as te
    from strategy_fit.test_target_emitter import _bars, _closes_with_up_rows
    from runner.conftest import PREV
    cmd = command(tmp_path)
    cmd.data_dir.mkdir(parents=True)
    _bars(PREV, _closes_with_up_rows(40, {38}, start=1500.0)).to_parquet(cmd.data_dir / "BBBB.parquet")
    assert te.main(list(cmd.emit_args), registry=REGISTRY) == 0
    doc = json.loads(cmd.target.read_text())
    sidecar = json.loads(te.sidecar_path(cmd.target).read_text())
    assert doc["strategy_id"] == "cand_x" and sidecar["spec_status"] == "candidate"
    need = supervise.target_buy_notional(cmd.target)
    assert need == doc["base_notional_idr"] * sum(t["weight"] for t in doc["targets"])


def test_target_buy_notional():
    p = Path(__file__).parent / "does-not-exist.json"
    with pytest.raises(ValueError):
        supervise.target_buy_notional(p)
