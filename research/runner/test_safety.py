"""Redaction, token expiry, env-file loading and the never-live guard."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from runner import envfile, guard, redact, token_expiry
from strategy_fit import emitter_specs as es

NOW = 1_800_000_000.0


def fake_jwt(exp=None, extra=None):
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    claims = dict(extra or {"sub": "someone"})
    if exp is not None:
        claims["exp"] = exp
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig_sig_sig"


# ---- redaction -------------------------------------------------------------------------------

def test_redact_replaces_known_secret_values_exactly():
    env = {"SB_ACCESS_TOKEN": "tok-abcdef-123", "TELEGRAM_CHAT_ID": "98765432", "HOME": "/Users/x"}
    out = redact.redact("failed with tok-abcdef-123 for chat 98765432 in /Users/x", env)
    assert "tok-abcdef-123" not in out and "98765432" not in out and "/Users/x" in out


def test_redact_masks_jwt_shaped_strings_even_when_not_in_env():
    out = redact.redact(f"header was {fake_jwt(NOW)} end")
    assert "eyJ" not in out and "end" in out


def test_redact_masks_telegram_bot_token_and_bearer_and_webhook_url():
    text = ("bot 123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx, Authorization: Bearer abcdefghijkl, "
            "post https://hooks.example.com/services/T0/B0/XYZ failed")
    out = redact.redact(text)
    assert "AAFxxxx" not in out and "abcdefghijkl" not in out and "hooks.example.com" not in out


def test_redact_masks_long_opaque_blob_but_keeps_store_names():
    blob = "A" * 48
    out = redact.redact(f"x {blob} y 2026-10-15-wifi_momo_h5-paper-a1.db")
    assert blob not in out and "2026-10-15-wifi_momo_h5-paper-a1.db" in out


def test_redact_ignores_trivially_short_values():
    assert redact.redact("ALGO_CONFIRM is on", {"ALGO_CONFIRM": "on"}) == "ALGO_CONFIRM is on"


# ---- token expiry ----------------------------------------------------------------------------

def test_expiry_reports_hours_left_only():
    tok = fake_jwt(NOW + 3.5 * 3600, extra={"sub": "very-secret-subject"})
    msg = token_expiry.describe("SB_ACCESS_TOKEN", tok, NOW)
    assert msg == "SB_ACCESS_TOKEN expires in 3.5h"
    assert "very-secret-subject" not in msg and tok not in msg


def test_expiry_expired_and_unreadable():
    assert "expired 2.0h ago" in token_expiry.describe("T", fake_jwt(NOW - 7200), NOW)
    assert "not readable" in token_expiry.describe("T", "opaque-token-value", NOW)
    assert "opaque-token-value" not in token_expiry.describe("T", "opaque-token-value", NOW)
    assert token_expiry.seconds_left(fake_jwt(None), NOW) is None
    assert token_expiry.seconds_left("a.!!!.c", NOW) is None


# ---- env file --------------------------------------------------------------------------------

def test_envfile_loads_without_overriding_and_reports_no_values(tmp_path):
    f = tmp_path / "env"
    f.write_text("# c\nexport A=1\nB='two words'\nC=3 # trailing\nbad line\nA=ignored\n")
    env = {"C": "shell"}
    added, problems = envfile.load_env_file(f, env)
    assert env == {"C": "shell", "A": "1", "B": "two words"} and added == 2
    assert problems == ["env file line 5 is not KEY=VALUE"]


def test_envfile_missing_is_fine(tmp_path):
    assert envfile.load_env_file(tmp_path / "nope", {}) == (0, [])


# ---- never-live guard ------------------------------------------------------------------------

def test_guard_allows_paper_day_for_paper_spec():
    guard.validate_argv(["python3", "-m", "strategy_fit.paper_day", "--strategy", "wifi_momo_h5"])


def test_guard_refuses_non_paper_spec():
    reg = {"c": replace(es.SPECS["wifi_momo_h5"], strategy_id="c", status=es.STATUS_CANDIDATE)}
    with pytest.raises(guard.LiveGuardError):
        guard.validate_argv(["python3", "-m", "strategy_fit.paper_day", "--strategy", "c"], registry=reg)
    with pytest.raises(guard.LiveGuardError):
        guard.validate_argv(["python3", "-m", "strategy_fit.paper_day", "--strategy", "bnbr_momo_h2"])
    with pytest.raises(guard.LiveGuardError):
        guard.validate_argv(["make", "paper-scorecard", "STRATEGY=bnbr_momo_h2"])


@pytest.mark.parametrize("argv", [
    ["bin/live-session", "--sink", "paper"],
    ["python3", "-m", "strategy_fit.paper_day", "--strategy", "wifi_momo_h5", "--retry", "1"],
    ["make", "emit-target", "STRATEGY=wifi_momo_h5"],
    ["make", "rm-everything"],
    ["python3", "-m", "strategy_fit.target_emitter"],
    ["bash", "-c", "echo hi"],
    ["python3", "x.py", "--sink", "live"],
    ["python3", "-m", "strategy_fit.paper_day", "--strategy", "wifi_momo_h5", "--sink=live"],
    [],
])
def test_guard_refuses_dangerous_commands(argv):
    with pytest.raises(guard.LiveGuardError):
        guard.validate_argv(argv)


def test_guard_allows_make_targets_used_by_the_runner():
    for t in ("build-strategy", "refresh-prices", "recon", "dashboard"):
        guard.validate_argv(["make", t, "DASH_PY=/x/python"])
    guard.validate_argv(["make", "paper-scorecard", "STRATEGY=ptro_momo_h5"])


def test_child_env_strips_arming_and_trading_token():
    env = {"ALGO_LIVE": "true", "ALGO_CONFIRM": "I_UNDERSTAND", "SB_TRADING_ACCESS_TOKEN": "t", "SB_ACCESS_TOKEN": "a"}
    assert guard.child_env(env) == {"SB_ACCESS_TOKEN": "a"}
    assert guard.child_env(env, allow_trading_token=True) == {"SB_ACCESS_TOKEN": "a", "SB_TRADING_ACCESS_TOKEN": "t"}
