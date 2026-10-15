"""Stockbit auth HTTP calls (stdlib only). Errors carry a status/kind, never a body or header.

Only https is allowed, except loopback hosts (test fakes), so a token is never sent in clear."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

USER_AGENT = "okhttp/4.9.3"     # the WAF 403s other agents before auth is even checked
EXODUS_DEFAULT = "https://exodus.stockbit.com"
CARINA_DEFAULT = "https://carina.stockbit.com"
TIMEOUT_S = 20.0
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


class AuthError(Exception):
    """Base class. Messages never contain tokens, bodies or headers."""


class RefreshExpired(AuthError):
    """The server answered 401: the refresh token is dead, a login is required."""


class LoginChallenge(AuthError):
    """Login returned a new-device / unfreeze challenge instead of tokens (trust revoked)."""


class Transient(AuthError):
    """Network error, timeout, 429 or 5xx: retry later, tokens are untouched."""


class Rejected(AuthError):
    def __init__(self, status: int, what: str):
        self.status = status
        hint = ", possible WAF/User-Agent block" if status == 403 else ""
        super().__init__(f"{what} rejected (HTTP {status}{hint})")


@dataclass(frozen=True)
class TokenPair:
    access: str = field(repr=False)
    refresh: str = field(repr=False, default="")


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in _LOOPBACK):
        return
    raise ValueError("auth host must be https (http only for loopback)")


def _ssl_context(env: Dict[str, str]) -> Optional[ssl.SSLContext]:
    if env.get("SB_INSECURE_TLS") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if env.get("SB_CA_BUNDLE"):
        return ssl.create_default_context(cafile=env["SB_CA_BUNDLE"])
    return None


def _post(url: str, what: str, headers: Dict[str, str], body: Optional[bytes],
          env: Optional[Dict[str, str]] = None) -> Any:
    """POST and return parsed JSON. Maps every failure to a typed, value-free AuthError."""
    _check_url(url)
    hdrs = {"User-Agent": USER_AGENT, "X-Platform": "android", "X-AppVersion": "7.0.0",
            "X-DeviceType": "algo-token-rotation", "Accept-Language": "id", "Accept": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    ctx = _ssl_context(env or {}) if url.startswith("https") else None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=ctx) as resp:  # noqa: S310 - scheme checked
            raw = resp.read(1 << 20)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 401:
            raise RefreshExpired(f"{what}: HTTP 401") from None
        if status == 429 or status >= 500:
            raise Transient(f"{what}: HTTP {status}") from None
        raise Rejected(status, what) from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise Transient(f"{what}: network error ({type(exc).__name__})") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise Rejected(200, f"{what} (unreadable response)") from None


def find_pair(node: Any) -> Optional[TokenPair]:
    """Locate a token pair anywhere in the JSON: {access:{token}, refresh:{token}} or flat *_token."""
    if isinstance(node, dict):
        a, r = node.get("access"), node.get("refresh")
        if isinstance(a, dict) and isinstance(a.get("token"), str) and a["token"]:
            rt = r.get("token") if isinstance(r, dict) else ""
            return TokenPair(a["token"], rt if isinstance(rt, str) else "")
        if isinstance(node.get("access_token"), str) and node["access_token"]:
            rt = node.get("refresh_token")
            return TokenPair(node["access_token"], rt if isinstance(rt, str) else "")
        for v in node.values():
            found = find_pair(v)
            if found:
                return found
    return None


def _has_key(node: Any, *keys: str) -> bool:
    if isinstance(node, dict):
        return any(k in node for k in keys) or any(_has_key(v, *keys) for v in node.values())
    return False


def refresh_main(exodus: str, refresh_token: str, env: Optional[Dict[str, str]] = None) -> TokenPair:
    doc = _post(exodus.rstrip("/") + "/login/refresh", "main refresh",
                {"Authorization": "Bearer " + refresh_token}, None, env)
    pair = find_pair(doc)
    if not pair:
        raise Rejected(200, "main refresh (no tokens in response)")
    return pair


def login(exodus: str, user: str, password: str, player_id: str,
          env: Optional[Dict[str, str]] = None) -> TokenPair:
    """Password login with a trusted player_id (no signature). Challenge -> LoginChallenge."""
    body = json.dumps({"user": user, "password": password, "player_id": player_id}).encode()
    try:
        doc = _post(exodus.rstrip("/") + "/login/v6/username", "login",
                    {"Content-Type": "application/json"}, body, env)
    except RefreshExpired:
        raise Rejected(401, "login (credentials)") from None
    pair = find_pair(doc)
    if pair:
        return pair
    if _has_key(doc, "new_device", "unfreeze"):
        raise LoginChallenge("login returned a new-device/unfreeze challenge")
    raise Rejected(200, "login (no tokens in response)")


def refresh_trading(carina: str, refresh_token: str, env: Optional[Dict[str, str]] = None) -> TokenPair:
    body = urllib.parse.urlencode({"refresh_token": refresh_token}).encode()
    doc = _post(carina.rstrip("/") + "/auth/refresh", "trading refresh",
                {"Authorization": "Bearer " + refresh_token,
                 "Content-Type": "application/x-www-form-urlencoded"}, body, env)
    pair = find_pair(doc)
    if not pair:
        raise Rejected(200, "trading refresh (no tokens in response)")
    return pair
