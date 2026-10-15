"""Live-only preflight: the extra checks an ARMED candidate needs before it may trade.

Presence and expiry only; no value (token, account number) is ever returned, printed or logged.
These do not fail the day: a stale trading token must not stop the paper sessions, it only
keeps the armed strategy from going live (and says so)."""

from __future__ import annotations

import base64
import json
from typing import Mapping, Optional, Tuple

from runner import token_expiry
from runner.config import ACCESS_TOKEN_ENV, ACCOUNT_ENV, TRADING_TOKEN_ENV


def trading_token_account(token: str) -> Optional[str]:
    """The ``data.acn`` claim (the account orders route to), or None. Never raises, never logs."""
    try:
        t = token.strip().strip('"').strip()
        if t.lower().startswith("bearer "):
            t = t[7:].strip()
        parts = t.split(".")
        if len(parts) != 3:
            return None
        seg = parts[1].rstrip("=")
        claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        acn = claims.get("data", {}).get("acn")
        return acn.strip() if isinstance(acn, str) and acn.strip() else None
    except Exception:  # noqa: BLE001 - nothing about the token may leak through an error
        return None


def live_problems(env: Mapping[str, str], now_epoch: float, min_hours: float) -> Tuple[str, ...]:
    """Why an armed candidate cannot go live right now. Empty = the live preflight passed."""
    problems = []
    if not env.get(ACCESS_TOKEN_ENV):
        problems.append(f"{ACCESS_TOKEN_ENV} is not set (market data)")
    token = env.get(TRADING_TOKEN_ENV)
    if not token:
        problems.append(f"{TRADING_TOKEN_ENV} is not set")
    else:
        left = token_expiry.seconds_left(token, now_epoch)
        if left is None:
            problems.append(f"{TRADING_TOKEN_ENV} expiry not readable (live needs a JWT with an exp claim)")
        elif left < min_hours * 3600:
            problems.append(token_expiry.describe(TRADING_TOKEN_ENV, token, now_epoch) + f" (need >= {min_hours:g}h)")
    account = (env.get(ACCOUNT_ENV) or "").strip()
    if not account:
        problems.append(f"{ACCOUNT_ENV} is not set (the sleeve account live-session must trade)")
    elif token:
        acn = trading_token_account(token)
        if acn is None:
            problems.append(f"{TRADING_TOKEN_ENV} has no readable account claim, so where its orders would land is unknown")
        elif acn != account:
            problems.append(f"the account the {TRADING_TOKEN_ENV} routes to is not {ACCOUNT_ENV}: orders follow the token "
                            "(re-mint it under the sleeve account)")
    return tuple(problems)
