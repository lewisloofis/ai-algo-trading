"""
strategy_fit/emitter_specs.py — registry of daily-target emitter specs.

One entry per forward-tested candidate. Adding a candidate is ONE ``register``
call below: strategy id -> the scored signal function (the very function object
the grid scored, never a reimplementation), symbol(s), hold, sizing.

    register(EmitterSpec(
        strategy_id="xyz_momo_h2",          # [a-z0-9_]+, also the store partition key
        symbols=("XYZ", "ABC"),             # one or many names, one target file
        family="daily_momentum",
        hold=2,                             # sessions, as scored
        signal_fn=daily_momentum_signal,    # imported from the family module
        lots=1,                             # exactly N lots per active name
        status=STATUS_CANDIDATE,            # only candidates emit without a flag
    ))

Status gates the emitter: only ``candidate`` specs may go live. ``paper``
specs emit like any other but only ever print a paper-sink command and are
refused by anything that would arm live (the emitter's live command, the
sidecar check in live-session / strategy-run). ``retired`` (and ``example``)
specs are refused unless the operator passes the explicit ``--allow-retired``
flag, so a dead strategy cannot be emitted by accident. A spec is never
inferred to be a candidate; the entry says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pandas as pd

from strategy_fit.families_single_name import daily_momentum_signal

STATUS_CANDIDATE = "candidate"
STATUS_PAPER = "paper"
STATUS_RETIRED = "retired"
STATUS_EXAMPLE = "example"
STATUSES = (STATUS_CANDIDATE, STATUS_PAPER, STATUS_RETIRED, STATUS_EXAMPLE)

_ID_RE = re.compile(r"^[a-z0-9_]+$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+$")


class SpecError(ValueError):
    """A registry entry is malformed."""


@dataclass(frozen=True)
class EmitterSpec:
    strategy_id: str
    symbols: tuple[str, ...]
    family: str
    hold: int
    signal_fn: Callable[..., pd.DataFrame]
    capital_model: str = "owned_cash"
    lots: int = 1
    status: str = STATUS_EXAMPLE
    status_note: str = ""

    @property
    def symbol(self) -> str:
        """The sole symbol of a single-name spec (multi-name specs have none)."""
        if len(self.symbols) != 1:
            raise SpecError(f"{self.strategy_id} covers {len(self.symbols)} symbols; use .symbols")
        return self.symbols[0]

    @property
    def is_candidate(self) -> bool:
        return self.status == STATUS_CANDIDATE

    @property
    def is_paper(self) -> bool:
        return self.status == STATUS_PAPER

    @property
    def can_go_live(self) -> bool:
        """Only a ``candidate`` may arm a live sink; ``paper`` never may."""
        return self.status == STATUS_CANDIDATE


def validate_spec(spec: EmitterSpec) -> EmitterSpec:
    if not _ID_RE.match(spec.strategy_id):
        raise SpecError(f"strategy_id {spec.strategy_id!r} must match [a-z0-9_]+")
    if spec.strategy_id == "calibration":
        raise SpecError("strategy_id 'calibration' is reserved (ADR-0016 section 6)")
    if not spec.symbols or len(set(spec.symbols)) != len(spec.symbols):
        raise SpecError(f"{spec.strategy_id}: symbols must be non-empty and unique")
    for s in spec.symbols:
        if not _SYMBOL_RE.match(s):
            raise SpecError(f"{spec.strategy_id}: symbol {s!r} must be upper-case alphanumeric")
    if spec.hold < 1:
        raise SpecError(f"{spec.strategy_id}: hold must be >= 1 session")
    if spec.lots < 1:
        raise SpecError(f"{spec.strategy_id}: lots must be >= 1")
    if spec.status not in STATUSES:
        raise SpecError(f"{spec.strategy_id}: status {spec.status!r} not in {STATUSES}")
    if not callable(spec.signal_fn):
        raise SpecError(f"{spec.strategy_id}: signal_fn must be callable")
    if spec.capital_model not in ("owned_cash", "leveraged_sleeve"):
        raise SpecError(f"{spec.strategy_id}: unknown capital_model {spec.capital_model!r}")
    return spec


SPECS: dict[str, EmitterSpec] = {}


def register(spec: EmitterSpec, registry: dict[str, EmitterSpec] = SPECS) -> EmitterSpec:
    validate_spec(spec)
    if spec.strategy_id in registry:
        raise SpecError(f"duplicate strategy_id {spec.strategy_id!r}")
    registry[spec.strategy_id] = spec
    return spec


# ---------------------------------------------------------------------------
# Entries. A new candidate goes here.
# ---------------------------------------------------------------------------

# RETIRED, NOT A LIVE CANDIDATE. The plan-10 BNBR daily_momentum hold=2 cell
# failed on entry timing (D1); kept as the worked example the tests exercise.
register(EmitterSpec(
    strategy_id="bnbr_momo_h2",
    symbols=("BNBR",),
    family="daily_momentum",
    hold=2,
    signal_fn=daily_momentum_signal,
    status=STATUS_RETIRED,
    status_note="retired, not a live candidate (BNBR fails on D1 entry timing)",
))

# PAPER ONLY, zero-cost forward test (docs/research/2026-10-15-first-candidate-
# shortlist.md section 1/6). The cells are exactly the shortlist's: daily_momentum
# (default parameters of daily_momentum_signal, the function SINGLE_NAME_SIGNAL_FNS
# scores), hold 5, next-session entry, sequential de-overlap, long-only. Not a
# live candidate: no cell cleared the blocking bar (t 0.55 / 0.73).
register(EmitterSpec(
    strategy_id="wifi_momo_h5",
    symbols=("WIFI",),
    family="daily_momentum",
    hold=5,
    signal_fn=daily_momentum_signal,
    status=STATUS_PAPER,
    status_note="paper forward test only (shortlist 2026-10-15 #1); not a live candidate",
))

register(EmitterSpec(
    strategy_id="ptro_momo_h5",
    symbols=("PTRO",),
    family="daily_momentum",
    hold=5,
    signal_fn=daily_momentum_signal,
    status=STATUS_PAPER,
    status_note="paper forward test only (shortlist 2026-10-15 #2); not a live candidate",
))
