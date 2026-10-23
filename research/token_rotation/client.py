"""Stockbit auth HTTP calls (stdlib only). Errors carry a status/kind, never a body or header.

Only https is allowed, except loopback hosts (test fakes), so a token is never sent in clear."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from lib.tls import ssl_context

USER_AGENT = "okhttp/4.9.3"     # the WAF 403s other agents before auth is even checked
EXODUS_DEFAULT = "https://exodus.stockbit.com"
CARINA_DEFAULT = "https://carina.stockbit.com"
CARINA_API_DEFAULT = "https://carina.api.stockbit.com"
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


def _post(url: str, what: str, headers: Dict[str, str], body: Optional[bytes],
          env: Optional[Dict[str, str]] = None) -> Any:
    return _request("POST", url, what, headers, body, env)


def _request(method: str, url: str, what: str, headers: Dict[str, str], body: Optional[bytes],
             env: Optional[Dict[str, str]] = None) -> Any:
    """Send and return parsed JSON. Maps every failure to a typed, value-free AuthError."""
    _check_url(url)
    hdrs = {"User-Agent": USER_AGENT, "X-Platform": "android", "X-AppVersion": "7.0.0",
            "X-DeviceType": "algo-token-rotation", "Accept-Language": "id", "Accept": "application/json"}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    ctx = ssl_context(env or {}) if url.startswith("https") else None
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


@dataclass(frozen=True)
class LoginToken:
    token: str = field(repr=False)
    target: str = ""            # "2" NEW_CORE (carina /auth/v2/login), "1" LEGACY


def trading_login_token(exodus: str, access_token: str, env: Optional[Dict[str, str]] = None) -> LoginToken:
    """Step 1 of the trading login: a one-time login_token derived from the main session."""
    doc = _request("GET", exodus.rstrip("/") + "/sekuritas/auth/token", "trading login token",
                   {"Authorization": "Bearer " + access_token}, None, env)
    data = doc.get("data") if isinstance(doc, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        raise Rejected(200, "trading login token (no token in response)")
    return LoginToken(token, str(data.get("target") or ""))


def trading_login(carina: str, login_token: str, pin: str, env: Optional[Dict[str, str]] = None) -> TokenPair:
    """Step 2: the PIN check (NEW_CORE). The trading refresh token it returns is not usable, so a
    session is renewed by calling this again, never by /auth/refresh."""
    body = json.dumps({"login_token": login_token, "pin": pin}).encode()
    doc = _post(carina.rstrip("/") + "/auth/v2/login", "trading login",
                {"Content-Type": "application/json"}, body, env)
    pair = find_pair(doc)
    if not pair:
        raise Rejected(200, "trading login (no tokens in response)")
    return pair


def switch_account(carina_api: str, trading_access: str, account_number: str,
                   env: Optional[Dict[str, str]] = None) -> TokenPair:
    """Step 3: the PIN login always lands on the primary account; trade its temporary access token
    for a session on ``account_number``. The new access token is in the response ``data``."""
    body = json.dumps({"account_number": account_number}).encode()
    doc = _post(carina_api.rstrip("/") + "/auth/account/switch", "account switch",
                {"Authorization": "Bearer " + trading_access, "Content-Type": "application/json"}, body, env)
    pair = find_pair(doc.get("data") if isinstance(doc, dict) else None)
    if not pair:
        raise Rejected(200, "account switch (no tokens in response)")
    return pair
