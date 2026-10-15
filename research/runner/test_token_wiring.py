"""Token rotation wired into the morning run (fake ensure_tokens; no network, no real .env.local)."""

from __future__ import annotations

import types
from dataclasses import replace

import pytest

from runner import daily
from runner.conftest import DAY, REGISTRY, jwt, write_bars, PREV
from token_rotation.rotate import EnsureResult

SAT = DAY.replace(day=17)
ROTATED_TOKEN = "ROTATED-ACCESS-VALUE-1234567890"      # synthetic, never a real token


class FakeEnsure:
    def __init__(self, result=None, raises=None, on_call=None):
        self.result, self.raises, self.on_call, self.calls = result, raises, on_call, []

    def __call__(self, path, **kw):
        self.calls.append((path, kw))
        if self.on_call:
            self.on_call()
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def env_cfg(cfg):
    (cfg.repo_root / ".env.local").write_text("")          # presence only; the fake never reads it
    write_bars(cfg, "AAAA", PREV)
    write_bars(cfg, "BBBB", PREV)
    return cfg


def texts(post):
    return [p[1]["text"] for p in post.posts]


def fresh_result(now, **kw):
    vals = {"SB_ACCESS_TOKEN": jwt(now.timestamp() + 30 * 3600), "SB_BEARER_TOKEN": jwt(now.timestamp() + 30 * 3600)}
    return EnsureResult(True, ("main",), values=vals, **kw)


def test_ensure_runs_before_preflight_and_updates_env(env_cfg, clock, alerter, now):
    env_cfg.env["SB_ACCESS_TOKEN"] = jwt(now.timestamp() + 0.5 * 3600)       # would fail preflight (< 1.5h)
    order = []
    ens = FakeEnsure(fresh_result(now), on_call=lambda: order.append("ensure"))
    real = daily.pf.run_preflight
    daily.pf.run_preflight = lambda *a, **k: (order.append("preflight"), real(*a, **k))[1]
    try:
        assert daily.run_preflight_phase(env_cfg, DAY, clock, alerter, ensure=ens) is True
    finally:
        daily.pf.run_preflight = real
    assert order == ["ensure", "preflight"]
    assert ens.calls[0][0] == env_cfg.repo_root / ".env.local"
    assert env_cfg.env["SB_ACCESS_TOKEN"].endswith("sigsigsig")             # in-memory env refreshed in place


def test_rotated_names_are_info_only_never_values_never_pushed(env_cfg, clock, alerter, post, now):
    res = EnsureResult(True, ("main",), values={"SB_ACCESS_TOKEN": ROTATED_TOKEN + "A" * 20,
                                                "SB_BEARER_TOKEN": jwt(now.timestamp() + 30 * 3600)})
    daily.rotate_tokens(env_cfg, DAY, clock, alerter, ensure=FakeEnsure(res))
    assert not post.posts                                                    # INFO stays local
    log = env_cfg.log_file.read_text()
    assert "[INFO]" in log and "SB_ACCESS_TOKEN" in log and "SB_BEARER_TOKEN" in log
    assert ROTATED_TOKEN not in log


def test_actions_alert_and_warnings_warn(env_cfg, clock, alerter, post, now):
    res = EnsureResult(True, (), actions=("trading session needs a PIN login",), warnings=("SB_REFRESH_TOKEN dies in 40h",))
    daily.rotate_tokens(env_cfg, DAY, clock, alerter, ensure=FakeEnsure(res))
    t = texts(post)
    assert any(x.startswith("[ALERT]") and "ACTION REQUIRED" in x and "PIN login" in x for x in t)
    assert any(x.startswith("[WARN]") and "dies in 40h" in x for x in t)


def test_unusable_tokens_still_fail_preflight_closed(env_cfg, clock, alerter, post, now):
    env_cfg.env["SB_ACCESS_TOKEN"] = jwt(now.timestamp() - 3600)             # expired
    res = EnsureResult(False, (), actions=("main session needs a manual re-login",))
    assert daily.run_preflight_phase(env_cfg, DAY, clock, alerter, ensure=FakeEnsure(res)) is False
    t = "\n".join(texts(post))
    assert "token rotation: ACTION REQUIRED" in t and "preflight FAILED" in t


def test_a_crashing_ensure_never_raises_and_preflight_decides(env_cfg, clock, alerter, post):
    ens = FakeEnsure(raises=ValueError("boom with SB_ACCESS_TOKEN=secretvalue"))
    assert daily.run_preflight_phase(env_cfg, DAY, clock, alerter, ensure=ens) is True     # existing tokens are fine
    t = "\n".join(texts(post))
    assert "token rotation crashed (ValueError)" in t and "secretvalue" not in t


def test_no_call_on_a_non_trading_day_or_without_env_file(cfg, env_cfg, clock, alerter):
    ens = FakeEnsure(EnsureResult(True))
    daily.run_preflight_phase(env_cfg, SAT, clock, alerter, ensure=ens)
    (env_cfg.repo_root / ".env.local").unlink()
    daily.run_preflight_phase(env_cfg, DAY, clock, alerter, ensure=ens)
    daily.run_preflight_phase(replace(env_cfg, rotate_tokens=False), DAY, clock, alerter, ensure=ens)
    assert ens.calls == []


def test_immutable_env_is_replaced_not_mutated(env_cfg, clock, alerter, now):
    frozen = replace(env_cfg, env=types.MappingProxyType(dict(env_cfg.env)))
    new = daily.rotate_tokens(frozen, DAY, clock, alerter, ensure=FakeEnsure(fresh_result(now)))
    assert new.env["SB_ACCESS_TOKEN"].endswith("sigsigsig") and new.env is not frozen.env


def test_rotation_is_recorded_in_the_state_file(env_cfg, clock, alerter, now):
    daily.rotate_tokens(env_cfg, DAY, clock, alerter, ensure=FakeEnsure(fresh_result(now, warnings=("w",))))
    st = daily.load_state(env_cfg.state_path(DAY), str(DAY))["token_rotation"]
    assert st == {"ok": True, "refreshed": ["main"], "actions": [], "warnings": 1}


def test_dry_run_shows_the_step_and_calls_nothing(cfg, capsys, monkeypatch):
    (cfg.repo_root / ".env.local").write_text("")
    monkeypatch.setattr(daily, "_default_ensure", lambda *a, **k: pytest.fail("network ensure_tokens called in a dry run"))
    assert daily.main(["morning", "--date", str(DAY), "--dry-run"], cfg=cfg) == 0
    out = capsys.readouterr().out
    assert "token rotation: token_rotation.ensure_tokens" in out and "A dry run does not call it" in out
    assert out.index("token rotation") < out.index("preflight:")
