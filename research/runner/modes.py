"""The ONE place that decides each strategy's mode for the day: paper, skip or live.

LIVE is returned only for a spec with status 'candidate' AND a valid arm file for TODAY naming
it AND a passing preflight (including a valid trading token) AND no kill file AND no live
session already started today. When it is returned it carries a ``LiveToken``: a sealed object
that exists only because this function made it. ``guard.build_live_command`` accepts nothing
else, so a live command cannot be constructed without passing through here.

Every other outcome is PAPER (spec status 'paper') or SKIP with a human-readable reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, FrozenSet, Optional, Tuple

from runner.arm import (  # noqa: F401  re-exported: callers reach the arm file through modes
    HARD_MAX_IDR, ArmFile, arm_path, read_arm_file)

PAPER, SKIP, LIVE = "paper", "skip", "live"

_SEAL = object()      # only decide_mode holds this; a hand-built LiveToken is refused


class LiveToken:
    """Proof that decide_mode authorised ONE strategy to trade live, today, up to ``max_idr``.

    Immutable, not copyable and not picklable. Python cannot make a class unforgeable against
    someone determined to import ``_SEAL``; this stops the realistic failure, code that builds a
    live command without having been through the decision. guard re-verifies everything anyway."""

    __slots__ = ("strategy_id", "day", "max_idr")

    def __init__(self, seal: object, strategy_id: str, day: date, max_idr: int) -> None:
        if seal is not _SEAL:
            raise TypeError("LiveToken can only be issued by runner.modes.decide_mode")
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "day", day)
        object.__setattr__(self, "max_idr", int(max_idr))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("LiveToken is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("LiveToken is immutable")

    def __reduce__(self):
        raise TypeError("LiveToken cannot be copied or pickled")

    def __repr__(self) -> str:
        return f"LiveToken({self.strategy_id}, {self.day}, max {self.max_idr:,} IDR)"


@dataclass(frozen=True)
class Mode:
    mode: str
    reason: str
    token: Optional[LiveToken] = None       # set if and only if mode == LIVE


@dataclass(frozen=True)
class LiveGate:
    """Every input to the live decision besides the spec itself. Built by the caller from files
    and the clock; decide_mode trusts none of it beyond what it checks here."""
    day: date                                # the trading day being decided
    today: date                              # the real date now (WIB); live only runs when day == today
    arm: ArmFile
    kill_present: bool
    preflight_ok: bool                       # the day's preflight passed
    live_problems: Tuple[str, ...] = ()      # live-only preflight failures (trading token, account, ...)
    already_started: FrozenSet[str] = frozenset()   # strategies with a live session already started today


def decide_mode(spec, gate: Optional[LiveGate] = None) -> Mode:
    """paper | skip | live for one spec."""
    if spec.is_paper:
        named = gate is not None and gate.arm.valid and spec.strategy_id in gate.arm.max_idr
        return Mode(PAPER, "status 'paper'" + ("; the arm file names it but a paper spec can never go live" if named else ""))
    if spec.status != "candidate":
        return Mode(SKIP, f"status {spec.status!r} is not 'paper' or 'candidate'")
    sid = spec.strategy_id
    if gate is None:
        return Mode(SKIP, "status 'candidate': not a paper spec; live is decided at start from the arm file")
    if not gate.arm.present:
        return Mode(SKIP, "status 'candidate', not armed (no arm file for today)")
    arm = gate.arm
    if arm.day != gate.day:
        return Mode(SKIP, f"arm file is for {arm.day}, not {gate.day}: not armed")
    if not arm.valid:
        return Mode(SKIP, f"status 'candidate': arm file invalid ({'; '.join(arm.problems)})")
    if sid not in arm.max_idr:
        return Mode(SKIP, f"status 'candidate', not armed (the arm file names {sorted(arm.max_idr)}, not {sid})")
    if gate.day != gate.today:
        return Mode(SKIP, f"live runs only on the actual date: arm file date {gate.day}, today is {gate.today}")
    if gate.kill_present:
        return Mode(SKIP, "armed, but the kill switch (runs/KILL) is present")
    if not gate.preflight_ok:
        return Mode(SKIP, "armed, but preflight failed")
    if gate.live_problems:
        return Mode(SKIP, "armed, but live preflight failed: " + "; ".join(gate.live_problems))
    if sid in gate.already_started:
        return Mode(SKIP, "armed, but a live session was already started today; a live session is never restarted")
    cap = arm.max_idr[sid]
    if not 0 < cap <= HARD_MAX_IDR:        # read_arm_file already enforces it; belt and braces
        return Mode(SKIP, f"armed cap {cap} is outside 1..{HARD_MAX_IDR:,}")
    return Mode(LIVE, f"status 'candidate', armed for {gate.day} with max {cap:,} IDR, preflight ok, no kill switch",
                LiveToken(_SEAL, sid, gate.day, cap))
