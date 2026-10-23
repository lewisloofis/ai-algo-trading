"""Token rotation tests: fake tokens, a fake loopback HTTP server, a tmp env file. No real endpoint."""

from __future__ import annotations

import base64
import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from token_rotation import client, rotate
from token_rotation.__main__ import main as cli_main
from token_rotation.envstore import read_env, update_env

NOW = 1_800_000_000.0


def jwt(hours_left: float, tag: str = "x") -> str:
    """A fake JWT (fake signature) expiring hours_left from NOW."""
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{b({'alg': 'none'})}.{b({'exp': NOW + hours_left * 3600, 't': tag})}.fakesig{tag}"


class FakeServer:
    """Loopback server; ``routes`` maps path -> (status, body dict) or a callable(request_info)."""

    def __init__(self):
        self.routes = {}
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.do_POST()

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode() if n else ""
                outer.calls.append({"path": self.path, "method": self.command, "auth": self.headers.get("Authorization"),
                                    "ua": self.headers.get("User-Agent"), "body": body})
                route = outer.routes.get(self.path, (404, {}))
                status, doc = route(outer.calls[-1]) if callable(route) else route
                data = json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def srv():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture()
def envf(tmp_path):
    return tmp_path / ".env.local"


def write_env(path, **kv):
    path.write_text("# my notes\nOTHER_SECRET=keepme\n" + "".join(f"{k}={v}\n" for k, v in kv.items()))
    os.chmod(path, 0o644)


def pair_doc(access, refresh):
    return {"data": {"access": {"token": access}, "refresh": {"token": refresh}}}


def run(envf, srv, **kw):
    return rotate.ensure_tokens(envf, now=NOW, exodus=srv.url, carina=srv.url, carina_api=srv.url,
                               sleep=lambda s: None, **kw)


# ---- success -------------------------------------------------------------------------------

def test_main_refresh_success_writes_both_and_keeps_other_lines(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(0.5, "old"), SB_REFRESH_TOKEN="refold")
    new_a, new_r = jwt(24, "new"), "refnew"
    srv.routes["/login/refresh"] = (200, pair_doc(new_a, new_r))
    res = run(envf, srv)
    env = read_env(envf)
    assert res.ok and res.refreshed == ("main",) and res.exit_code == 0
    assert env["SB_ACCESS_TOKEN"] == new_a and "SB_BEARER_TOKEN" not in env and env["SB_REFRESH_TOKEN"] == new_r
    assert env["OTHER_SECRET"] == "keepme"
    assert "# my notes" in envf.read_text()
    assert srv.calls[0]["auth"] == "Bearer refold" and srv.calls[0]["ua"] == "okhttp/4.9.3"
    applied = {}
    res.apply_to(applied)
    assert applied["SB_ACCESS_TOKEN"] == new_a


# ---- no-op ---------------------------------------------------------------------------------

def test_noop_when_tokens_have_more_than_min_hours(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(20), SB_REFRESH_TOKEN=jwt(500))
    before = envf.read_text()
    res = run(envf, srv, min_hours=12)
    assert res.ok and res.refreshed == () and srv.calls == [] and res.exit_code == 0
    assert envf.read_text() == before


def test_force_refreshes_even_when_fresh(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(20), SB_REFRESH_TOKEN="r")
    srv.routes["/login/refresh"] = (200, pair_doc(jwt(24, "n"), "r2"))
    assert run(envf, srv, force=True).refreshed == ("main",)


# ---- refresh token expired -----------------------------------------------------------------

def test_refresh_expired_without_creds_is_explicit_manual_action(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="dead")
    before = envf.read_text()
    srv.routes["/login/refresh"] = (401, {"message": "bad token dead"})
    res = run(envf, srv)
    assert not res.ok and res.exit_code == 1
    assert "ACTION REQUIRED" in res.summary and "re-login" in res.summary
    assert envf.read_text() == before


def test_refresh_expired_falls_back_to_headless_login(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="dead",
              SB_USERNAME="fakeuser", SB_PASSWORD="fakepw", SB_PLAYER_ID="00000000-fake")
    srv.routes["/login/refresh"] = (401, {})
    srv.routes["/login/v6/username"] = (200, {"data": {"login": {"token_data": pair_doc(jwt(24, "l"), "rl")["data"]}}})
    res = run(envf, srv)
    assert res.ok and res.refreshed == ("main",)
    body = json.loads(srv.calls[1]["body"])
    assert body["player_id"] == "00000000-fake" and "signature" not in body


def test_login_challenge_is_manual_action(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="dead",
              SB_USERNAME="u", SB_PASSWORD="p", SB_PLAYER_ID="pid")
    srv.routes["/login/refresh"] = (401, {})
    srv.routes["/login/v6/username"] = (200, {"data": {"new_device": {"multi_factor": {"login_token": "zzz"}}}})
    res = run(envf, srv)
    assert not res.ok and "challenge" in res.summary and res.exit_code == 1


def test_refresh_token_nearly_dead_without_creds_warns_early(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(24))
    res = run(envf, srv)
    assert res.ok and res.exit_code == 3 and "re-login soon" in res.summary


# ---- trading session: minted from SB_TRADING_PIN (its refresh token is not usable) ---------

PIN = "135790"     # synthetic
ACCOUNT = "0000001X"     # synthetic


def trading_routes(srv, target="2", access=None):
    srv.routes["/sekuritas/auth/token"] = (200, {"message": "OK", "data": {"token": "logintok", "target": target}})
    srv.routes["/auth/v2/login"] = (200, {"access_token": jwt(8, "primary"), "refresh_token": "unusable"})
    srv.routes["/auth/account/switch"] = (200, {"message": "OK", "data": {"access_token": access or jwt(8, "tn")}})


def test_trading_minted_from_pin_when_missing(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30, "main"), SB_REFRESH_TOKEN=jwt(500), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    new_t = jwt(8, "tn")
    trading_routes(srv, access=new_t)
    res = run(envf, srv)
    env = read_env(envf)
    assert res.ok and res.refreshed == ("trading",) and res.exit_code == 0
    assert env["SB_TRADING_ACCESS_TOKEN"] == new_t and "SB_TRADING_REFRESH_TOKEN" not in env
    assert env["SB_TRADING_PIN"] == PIN                                    # left as the operator wrote it
    get, post, switch = srv.calls
    assert (get["method"], get["path"], get["auth"]) == ("GET", "/sekuritas/auth/token", "Bearer " + jwt(30, "main"))
    assert post["method"] == "POST" and json.loads(post["body"]) == {"login_token": "logintok", "pin": PIN}
    assert (switch["method"], switch["path"], switch["auth"]) == ("POST", "/auth/account/switch",
                                                                  "Bearer " + jwt(8, "primary"))
    assert json.loads(switch["body"]) == {"account_number": ACCOUNT}


def test_primary_account_token_is_never_kept_when_switch_fails(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500),
              SB_TRADING_ACCESS_TOKEN=jwt(-1), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    before = envf.read_text()
    trading_routes(srv)
    srv.routes["/auth/account/switch"] = (400, {"echo": ACCOUNT})
    res = run(envf, srv)
    assert res.ok and res.exit_code == 3 and "account switch refused" in res.summary
    assert envf.read_text() == before and "SB_TRADING_ACCESS_TOKEN" not in res.values
    assert [c["path"] for c in srv.calls].count("/auth/v2/login") == 1


def test_switch_5xx_retries_without_resending_the_pin(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    new_t = jwt(8, "tn")
    trading_routes(srv)
    answers = iter([(503, {}), (200, {"data": {"access_token": new_t}})])
    srv.routes["/auth/account/switch"] = lambda _c: next(answers)
    res = run(envf, srv)
    assert res.refreshed == ("trading",) and read_env(envf)["SB_TRADING_ACCESS_TOKEN"] == new_t
    assert [c["path"] for c in srv.calls].count("/auth/v2/login") == 1


def test_missing_trading_account_sends_no_pin(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500), SB_TRADING_PIN=PIN)
    res = run(envf, srv)
    assert res.exit_code == 3 and "SB_TRADING_ACCOUNT is not set" in res.summary and srv.calls == []


def test_trading_mint_uses_the_freshly_rotated_main_access(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(0.5, "old"), SB_REFRESH_TOKEN="r", SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    new_main = jwt(24, "newmain")
    srv.routes["/login/refresh"] = (200, pair_doc(new_main, "r2"))
    trading_routes(srv)
    res = run(envf, srv)
    assert res.refreshed == ("main", "trading")
    assert srv.calls[1]["auth"] == "Bearer " + new_main


def test_trading_not_reminted_while_it_has_more_than_trading_min_hours(envf, srv):
    # an 8h token minted at 08:45 has ~5h left at noon: no PIN login, even though main uses a 12h threshold
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500),
              SB_TRADING_ACCESS_TOKEN=jwt(5), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    res = run(envf, srv, min_hours=12)
    assert res.ok and res.refreshed == () and srv.calls == []


def test_trading_reminted_before_post_close_when_nearly_expired(envf, srv):
    # 16:20 recon on a token minted at 08:45: ~25 minutes left
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500),
              SB_TRADING_ACCESS_TOKEN=jwt(0.4), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    trading_routes(srv)
    assert run(envf, srv).refreshed == ("trading",)


def test_wrong_pin_is_one_attempt_and_a_manual_action(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500),
              SB_TRADING_ACCESS_TOKEN=jwt(-1), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    before = envf.read_text()
    trading_routes(srv)
    srv.routes["/auth/v2/login"] = (400, {"message": "PIN salah", "echo": PIN})
    res = run(envf, srv)
    assert res.ok and res.exit_code == 3                                   # paper still runs
    assert "SB_TRADING_PIN" in res.summary and "lock" in res.summary
    assert PIN not in res.summary + repr(res)
    assert [c["path"] for c in srv.calls].count("/auth/v2/login") == 1
    assert envf.read_text() == before


def test_pin_login_5xx_is_not_retried(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500),
              SB_TRADING_ACCESS_TOKEN=jwt(-1), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    trading_routes(srv)
    srv.routes["/auth/v2/login"] = (503, {})
    res = run(envf, srv)
    assert res.ok and "network/server error" in res.summary
    assert [c["path"] for c in srv.calls].count("/auth/v2/login") == 1


def test_stale_trading_token_without_pin_is_a_manual_action(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500), SB_TRADING_ACCESS_TOKEN=jwt(-2))
    res = run(envf, srv)
    assert res.ok and res.exit_code == 3 and "SB_TRADING_PIN is not set" in res.summary
    assert srv.calls == []


def test_no_trading_session_configured_is_left_alone(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500))
    res = run(envf, srv)
    assert res.ok and res.exit_code == 0 and srv.calls == []


def test_legacy_target_never_receives_the_pin(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN=jwt(500), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    trading_routes(srv, target="1")
    res = run(envf, srv)
    assert "legacy" in res.summary and [c["path"] for c in srv.calls] == ["/sekuritas/auth/token"]


def test_no_mint_when_main_session_is_unusable(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="dead", SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    srv.routes["/login/refresh"] = (401, {})
    res = run(envf, srv)
    assert not res.ok and [c["path"] for c in srv.calls] == ["/login/refresh"]


def test_force_remints_trading(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(30), SB_REFRESH_TOKEN="r",
              SB_TRADING_ACCESS_TOKEN=jwt(7), SB_TRADING_PIN=PIN, SB_TRADING_ACCOUNT=ACCOUNT)
    srv.routes["/login/refresh"] = (200, pair_doc(jwt(24, "n"), "r2"))
    trading_routes(srv)
    assert run(envf, srv, force=True).refreshed == ("main", "trading")


# ---- network error -------------------------------------------------------------------------

def test_network_error_is_transient_and_leaves_file_untouched(envf):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="r")
    before = envf.read_text()
    res = rotate.ensure_tokens(envf, now=NOW, exodus="http://127.0.0.1:1", sleep=lambda s: None)
    assert not res.ok and res.transient and res.exit_code == 2
    assert envf.read_text() == before


def test_5xx_retries_then_succeeds(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="r")
    answers = iter([(503, {}), (200, pair_doc(jwt(24, "n"), "r2"))])
    srv.routes["/login/refresh"] = lambda _c: next(answers)
    assert run(envf, srv).ok and len(srv.calls) == 2


def test_403_waf_is_reported_not_retried(envf, srv):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="r")
    srv.routes["/login/refresh"] = (403, {"echo": "Bearer r"})
    res = run(envf, srv)
    assert not res.ok and "WAF" in res.summary and len(srv.calls) == 1


def test_plain_http_to_non_loopback_is_refused():
    with pytest.raises(ValueError):
        client.refresh_main("http://exodus.example.com", "r")


# ---- atomic write + permissions ------------------------------------------------------------

def test_update_env_is_mode_0600_and_replaces_atomically(envf):
    write_env(envf, SB_ACCESS_TOKEN="old")
    inode_before = envf.stat().st_ino
    update_env(envf, {"SB_ACCESS_TOKEN": "new", "SB_REFRESH_TOKEN": "r"})
    assert stat.S_IMODE(envf.stat().st_mode) == 0o600
    assert envf.stat().st_ino != inode_before          # rename over, not in-place rewrite
    assert not [p for p in envf.parent.iterdir() if p.name.endswith(".tmp")]
    assert read_env(envf)["SB_ACCESS_TOKEN"] == "new"


def test_failed_write_leaves_original_and_no_temp(envf, monkeypatch):
    write_env(envf, SB_ACCESS_TOKEN="old")
    before = envf.read_text()
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        update_env(envf, {"SB_ACCESS_TOKEN": "new"})
    assert envf.read_text() == before
    assert not [p for p in envf.parent.iterdir() if p.name.endswith(".tmp")]


def test_unsafe_value_rejected_without_echoing_it(envf):
    with pytest.raises(ValueError) as ei:
        update_env(envf, {"SB_ACCESS_TOKEN": "abc def"})
    assert "abc" not in str(ei.value)


def test_persist_failure_is_critical_action(envf, srv, monkeypatch):
    write_env(envf, SB_ACCESS_TOKEN=jwt(-1), SB_REFRESH_TOKEN="r")
    srv.routes["/login/refresh"] = (200, pair_doc(jwt(24, "n"), "r2"))
    monkeypatch.setattr(rotate, "update_env", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    res = run(envf, srv)
    assert not res.ok and "could NOT be written" in res.summary


# ---- redaction -----------------------------------------------------------------------------

def test_no_secret_in_summary_repr_status_or_cli_output(envf, srv, capsys):
    secrets = [jwt(-1, "oldA"), "refresh-secret-old", "fakepw", "player-secret", "864213"]
    write_env(envf, SB_ACCESS_TOKEN=secrets[0], SB_REFRESH_TOKEN=secrets[1],
              SB_USERNAME="fakeuser", SB_PASSWORD=secrets[2], SB_PLAYER_ID=secrets[3], SB_TRADING_PIN=secrets[4])
    new_a = jwt(24, "newA")
    # server echoes secrets in an error body; they must not surface
    srv.routes["/login/refresh"] = (401, {"echo": secrets[1]})
    srv.routes["/login/v6/username"] = (403, {"echo": secrets[2] + secrets[3]})
    res = run(envf, srv)
    text = res.summary + repr(res) + repr(client.TokenPair(new_a, "r"))
    for s in secrets + [new_a]:
        assert s not in text

    assert cli_main(["status", "--env-file", str(envf)]) == 0     # status never touches the network
    out = capsys.readouterr().out
    for s in secrets + [new_a]:
        assert s not in out
    assert "SB_ACCESS_TOKEN: " in out


def test_status_lines_only_expiry_words(envf):
    write_env(envf, SB_ACCESS_TOKEN=jwt(3.2), SB_REFRESH_TOKEN="opaque")
    lines = {s.name: s.line() for s in rotate.token_status(envf, now=NOW)}
    assert lines["SB_ACCESS_TOKEN"] == "SB_ACCESS_TOKEN: expires in 3.2h"
    assert lines["SB_REFRESH_TOKEN"].startswith("SB_REFRESH_TOKEN: unknown")
    assert lines["SB_TRADING_ACCESS_TOKEN"] == "SB_TRADING_ACCESS_TOKEN: not set"
    assert "opaque" not in "".join(lines.values())


def test_post_passes_non_none_context_for_https(monkeypatch):
    import ssl

    seen = {}

    def fake_urlopen(req, timeout=None, context=None):
        seen["context"] = context
        raise OSError("no network in tests")

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(client.Transient):
        client._post("https://exodus.example.com/x", "t", {}, b"{}", {})
    assert isinstance(seen["context"], ssl.SSLContext)
