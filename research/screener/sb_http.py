"""
screener/sb_http.py — shared Stockbit/chartbit HTTP constants and error typing.

Single source of truth for the User-Agent (mirrors collector/feed.DefaultUserAgent:
Stockbit's WAF returns HTTP 403 to non-okhttp agents) and for turning an HTTP
failure into an error whose class distinguishes a token problem (401) from a
possible WAF/User-Agent block (403). Never carries tokens or headers.
"""
from __future__ import annotations

import urllib.error
import urllib.request

SB_USER_AGENT = "okhttp/4.9.3"


class SBHttpError(RuntimeError):
    """HTTP failure from Stockbit/chartbit. ``status`` is the HTTP code."""

    def __init__(self, status: int, url: str) -> None:
        self.status = status
        # Strip the query string; never include headers.
        self.url = url.split("?", 1)[0]
        super().__init__(f"HTTP {status} from {self.url}: {describe_status(status)}")


def describe_status(status: int) -> str:
    if status == 401:
        return "401 Unauthorized - bearer token missing/expired/invalid"
    if status == 403:
        return "403 Forbidden - possible WAF/User-Agent block (not a token problem)"
    return f"HTTP {status}"


def classify_error(exc: BaseException) -> str:
    """Short, token-free label for a fetch failure."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        if status in (401, 403):
            return describe_status(status)
        return f"HTTP {status} ({type(exc).__name__})"
    return type(exc).__name__


def urlopen_bytes(req: urllib.request.Request, *, timeout: float, context) -> bytes:
    """urlopen that re-raises HTTPError as SBHttpError (no headers/body leaked)."""
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise SBHttpError(exc.code, req.full_url) from None
