"""Token rotation: refresh what can be refreshed unattended, say loudly what cannot.

Automated:  main session (SB_ACCESS_TOKEN + SB_BEARER_TOKEN) via SB_REFRESH_TOKEN; if that is dead
            and SB_USERNAME/SB_PASSWORD/SB_PLAYER_ID are present, a headless re-login with the
            already-trusted player_id (plan 03 phase 0). Trading session via SB_TRADING_REFRESH_TOKEN.
Manual:     a challenge on login (trust revoked), a dead trading refresh token (needs the PIN, which
            this tool never stores), a missing refresh token. These come back as ``actions``.

Nothing here prints or returns a token except ``EnsureResult.apply_to`` (in-memory env update)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, MutableMapping, Optional, Tuple

from runner import token_expiry
from token_rotation import client
from token_rotation.envstore import read_env, update_env

ACCESS, BEARER, REFRESH = "SB_ACCESS_TOKEN", "SB_BEARER_TOKEN", "SB_REFRESH_TOKEN"
T_ACCESS, T_REFRESH = "SB_TRADING_ACCESS_TOKEN", "SB_TRADING_REFRESH_TOKEN"
USER, PASSWORD, PLAYER = "SB_USERNAME", "SB_PASSWORD", "SB_PLAYER_ID"
TOKEN_NAMES = (ACCESS, BEARER, REFRESH, T_ACCESS, T_REFRESH)

DEFAULT_MIN_HOURS = 12.0     # refresh when any main access token has less than this left
REFRESH_WARN_HOURS = 72.0    # a refresh token this close to death, with no relogin creds, is flagged
RETRIES = 3


@dataclass(frozen=True)
class TokenStatus:
    name: str
    state: str                  # fresh | expired | unknown | unset
    hours_left: Optional[float]

    def line(self) -> str:
        if self.state == "unset":
            return f"{self.name}: not set"
        if self.state == "unknown":
            return f"{self.name}: unknown (not a JWT with an exp claim)"
        if self.state == "expired":
            return f"{self.name}: expired"
        return f"{self.name}: expires in {self.hours_left:.1f}h"


@dataclass(frozen=True)
class EnsureResult:
    ok: bool                              # main session usable (access + bearer present and fresh)
    refreshed: Tuple[str, ...] = ()       # which sessions were rotated: "main", "trading"
    actions: Tuple[str, ...] = ()         # manual steps the operator MUST take (value-free)
    warnings: Tuple[str, ...] = ()
    transient: bool = False               # a network/server error; retrying later may succeed
    values: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def apply_to(self, env: MutableMapping[str, str]) -> None:
        """Copy rotated values into an in-memory env (the runner's, which loaded the old ones)."""
        env.update(self.values)

    @property
    def summary(self) -> str:
        parts = [f"main {'ok' if self.ok else 'NOT ok'}"]
        if self.refreshed:
            parts.append("rotated: " + ", ".join(self.refreshed))
        parts += [f"ACTION REQUIRED: {a}" for a in self.actions]
        parts += [f"warning: {w}" for w in self.warnings]
        return "; ".join(parts)

    @property
    def exit_code(self) -> int:
        """0 ok · 1 main session needs a manual re-login · 2 transient error · 3 only warnings/trading."""
        if not self.ok:
            return 2 if self.transient and not self.actions else 1
        return 3 if (self.actions or self.warnings) else 0


def _status(name: str, value: Optional[str], now: float) -> TokenStatus:
    if not value:
        return TokenStatus(name, "unset", None)
    left = token_expiry.seconds_left(value, now)
    if left is None:
        return TokenStatus(name, "unknown", None)
    if left <= 0:
        return TokenStatus(name, "expired", left / 3600)
    return TokenStatus(name, "fresh", left / 3600)


def token_status(env_path: Path, now: Optional[float] = None) -> List[TokenStatus]:
    now = time.time() if now is None else now
    env = read_env(env_path)
    return [_status(n, env.get(n), now) for n in TOKEN_NAMES]


def _with_retries(fn: Callable[[], client.TokenPair], sleep: Callable[[float], None]) -> client.TokenPair:
    for attempt in range(RETRIES):
        try:
            return fn()
        except client.Transient:
            if attempt == RETRIES - 1:
                raise
            sleep(2.0 ** attempt)
    raise AssertionError("unreachable")


def _rotate_main(env: Dict[str, str], exodus: str, sleep) -> Tuple[Optional[client.TokenPair], List[str], bool]:
    """-> (new pair or None, manual actions, transient). Tries refresh, then headless relogin."""
    actions: List[str] = []
    can_login = all(env.get(k) for k in (USER, PASSWORD, PLAYER))
    login_hint = (f"re-login in the Stockbit web app and put fresh {ACCESS}, {BEARER}, {REFRESH} in .env.local "
                  f"(or add {USER}/{PASSWORD}/{PLAYER} for headless re-login)")
    if env.get(REFRESH):
        try:
            return _with_retries(lambda: client.refresh_main(exodus, env[REFRESH], env), sleep), [], False
        except client.RefreshExpired:
            pass
        except client.Transient:
            return None, [], True
        except client.AuthError as exc:
            return None, [f"main refresh failed: {exc}; {login_hint}"], False
    if not can_login:
        why = "refresh token expired" if env.get(REFRESH) else f"{REFRESH} is not set"
        return None, [f"main session cannot be refreshed ({why}); {login_hint}"], False
    try:
        pair = _with_retries(lambda: client.login(exodus, env[USER], env[PASSWORD], env[PLAYER], env), sleep)
        return pair, [], False
    except client.LoginChallenge:
        actions.append("headless login hit a new-device challenge (player_id trust revoked): approve a login "
                       f"on your phone once, then {login_hint}")
    except client.Transient:
        return None, [], True
    except client.AuthError as exc:
        actions.append(f"headless re-login failed: {exc}; {login_hint}")
    return None, actions, False


def _rotate_trading(env: Dict[str, str], carina: str, sleep) -> Tuple[Optional[client.TokenPair], List[str], bool]:
    pin_hint = ("log in to the trading session again (PIN, see docs/runbooks/first-live-order.md) and put fresh "
                f"{T_ACCESS}/{T_REFRESH} in .env.local; the PIN is never stored, so this cannot be automated")
    if not env.get(T_REFRESH):
        return None, [f"trading session cannot be refreshed ({T_REFRESH} is not set); {pin_hint}"], False
    try:
        return _with_retries(lambda: client.refresh_trading(carina, env[T_REFRESH], env), sleep), [], False
    except client.RefreshExpired:
        return None, [f"trading refresh token expired; {pin_hint}"], False
    except client.Transient:
        return None, [], True
    except client.AuthError as exc:
        return None, [f"trading refresh failed: {exc}; {pin_hint}"], False


def _main_updates(pair: client.TokenPair, env: Mapping[str, str]) -> Dict[str, str]:
    return {ACCESS: pair.access, BEARER: pair.access, REFRESH: pair.refresh or env.get(REFRESH, "")}


def ensure_tokens(env_path: Path, *, min_hours: float = DEFAULT_MIN_HOURS, force: bool = False,
                  now: Optional[float] = None, exodus: Optional[str] = None, carina: Optional[str] = None,
                  sleep: Callable[[float], None] = time.sleep) -> EnsureResult:
    """THE runner hook. Refresh the sessions in ``env_path`` that have < min_hours left (or all, if
    ``force``), write the result atomically, and report. Never raises for auth/network problems."""
    now = time.time() if now is None else now
    env = read_env(env_path)
    exodus = exodus or env.get("SB_EXODUS_HOST") or client.EXODUS_DEFAULT
    carina = carina or env.get("SB_CARINA_HOST") or client.CARINA_DEFAULT
    actions: List[str] = []
    warnings: List[str] = []
    refreshed: List[str] = []
    updates: Dict[str, str] = {}
    transient = False

    main_sts = [_status(n, env.get(n), now) for n in (ACCESS, BEARER)]
    if force or any(s.state != "fresh" or s.hours_left < min_hours for s in main_sts):
        pair, acts, transient_main = _rotate_main(env, exodus, sleep)
        actions += acts
        transient = transient or transient_main
        if pair:
            updates.update(_main_updates(pair, env))
            refreshed.append("main")

    if env.get(T_ACCESS) or env.get(T_REFRESH):
        t_st = _status(T_ACCESS, env.get(T_ACCESS), now)
        if force or t_st.state != "fresh" or t_st.hours_left < min_hours:
            pair, acts, transient_t = _rotate_trading(env, carina, sleep)
            actions += acts
            transient = transient or transient_t
            if pair:
                updates[T_ACCESS] = pair.access
                updates[T_REFRESH] = pair.refresh or env.get(T_REFRESH, "")
                refreshed.append("trading")

    if updates:
        try:
            update_env(env_path, updates)
        except (OSError, ValueError) as exc:
            # The server already rotated the refresh token; failing to persist is critical.
            return EnsureResult(False, (), tuple(actions) + (
                f"rotated tokens could NOT be written ({type(exc).__name__}); the old refresh token may be dead: "
                f"re-login and update {ACCESS}/{BEARER}/{REFRESH}",), tuple(warnings), False)
        merged = {**env, **updates}
    else:
        merged = env

    final_main = [_status(n, merged.get(n), now) for n in (ACCESS, BEARER)]
    ok = all(s.state == "fresh" and s.hours_left > 0 for s in final_main)
    if ok:
        r = _status(REFRESH, merged.get(REFRESH), now)
        can_login = all(merged.get(k) for k in (USER, PASSWORD, PLAYER))
        if r.state == "fresh" and r.hours_left < REFRESH_WARN_HOURS and not can_login:
            warnings.append(f"{REFRESH} dies in {r.hours_left:.0f}h and no headless re-login credentials are set: "
                            "re-login soon")
        if r.state == "unset" and not can_login:
            warnings.append(f"{REFRESH} is not set: the access token cannot be renewed when it expires")
    if transient and ok:
        warnings.append("a refresh hit a network/server error and was skipped; it will be retried next run")
    return EnsureResult(ok, tuple(refreshed), tuple(actions), tuple(warnings),
                        transient and not ok, dict(updates))
