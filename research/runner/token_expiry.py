"""Token expiry from the JWT ``exp`` claim, decoded in-process.

Only the expiry is ever exposed (as a duration). The token, its other claims and any decoding
error text never leave this module."""

from __future__ import annotations

import base64
import json
from typing import Optional


def expiry_epoch(token: str) -> Optional[float]:
    """The ``exp`` claim as epoch seconds, or None when the token is not a readable JWT."""
    try:
        parts = token.strip().split(".")
        if len(parts) != 3:
            return None
        seg = parts[1]
        claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        exp = claims.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return float(exp)
    except Exception:  # noqa: BLE001 - nothing about the token may leak through an error
        return None


def seconds_left(token: str, now_epoch: float) -> Optional[float]:
    exp = expiry_epoch(token)
    return None if exp is None else exp - now_epoch


def describe(name: str, token: str, now_epoch: float) -> str:
    """'<NAME> expires in 3.2h' / 'expired 1.0h ago' / 'expiry not readable' (never the token)."""
    left = seconds_left(token, now_epoch)
    if left is None:
        return f"{name} expiry not readable (not a JWT with an exp claim)"
    if left <= 0:
        return f"{name} expired {-left / 3600:.1f}h ago"
    return f"{name} expires in {left / 3600:.1f}h"
