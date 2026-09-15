"""
strategy_fit/sleeve_sizing.py — Per-name order notional derived from the
sleeve's MEASURED capital (ADR-0017), not from a facility it doesn't use.

.. important::
    **ADR-0011's ``POSITION_NOTIONAL_IDR = 30_000_000`` is VOID as of
    2026-09-14** (ADR-0017).  It was derived as "300M IDR leverage sleeve
    (ADR-0011) / ~10 concurrent names" — but ADR-0011 was already superseded
    by ADR-0015 (leverage made optional, owned cash the default), and neither
    ADR ever measured where the sleeve's capital actually lives or how much
    of it there is.  ADR-0017 measured it: the sleeve is a separate broker
    sub-account (``008488L``) holding **15,000,000 IDR** of owned cash as of
    2026-09-14 — HALF the old per-name constant, for the WHOLE account, not
    one name.  Every impact and capacity figure `strategy_fit` has produced
    against the 30M constant was priced at a size the account cannot take.

This module mirrors ``strategy_fit/capital_model.py``'s shape: a frozen
descriptor, named presets, and one ``ACTIVE_*`` module-level selection that
every caller reads through rather than hard-coding a number.

Why a descriptor and not just a smaller constant
-------------------------------------------------
The old constant conflated two independent facts — how much capital the
sleeve has, and how many names it is meant to hold concurrently — into one
number, so nothing downstream could tell which one was wrong when the
figure needed correcting.  ``SleeveSizing`` keeps them separate and derives
``per_name_notional_idr`` from both, so a future equity re-measurement (a
top-up, a drawdown) and a future portfolio-construction decision (more or
fewer concurrent names) can each change independently and the derivation
stays auditable.

The concurrent-name count is an OPEN QUESTION
----------------------------------------------
ADR-0017's "Open questions" section states plainly: "How many concurrent
names should the sleeve hold? ... It is a portfolio-construction decision
(Track A, plan 13's successor), not an execution one." This module does NOT
answer that question — it picks ``ACTIVE_SLEEVE_SIZING`` at N=5 as a
PLACEHOLDER so the harness has *some* single number to run its default grid
against, and documents that choice as provisional right here. The actual
answer is meant to come from sweeping N, not assuming it — see
``strategy_fit/impact_sweep.py``, which sweeps the measured sleeve across
N ∈ {10, 5, 3, 1} precisely so the harness's output does not silently
smuggle in an assumed portfolio size as if it were measured.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SleeveSizing:
    """
    Descriptor for the sleeve's measured capital and an assumed concurrent-
    name count, and what that implies for per-name order notional.

    Attributes
    ----------
    name : str
        Identifier used in reports and skip reasons (e.g. "sleeve_n5").
    equity_idr : float
        Measured sleeve equity in IDR — what the source actually reported,
        not a derived or rounded figure.
    concurrent_names : int
        Intended number of simultaneous positions this equity is split
        across.  A portfolio-construction assumption, not a measurement —
        see the module docstring's "Open Question" note.
    measured_at : str
        ISO date (YYYY-MM-DD) the equity was measured.
    source : str
        How the equity was obtained (an API call, a doc reference, or
        "n/a (void reference point)" for the retained legacy preset).
    """

    name: str
    equity_idr: float
    concurrent_names: int
    measured_at: str
    source: str

    @property
    def per_name_notional_idr(self) -> float:
        """Per-name order notional: equity split evenly across concurrent_names."""
        return self.equity_idr / self.concurrent_names


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
#
# All measured presets below share the same equity_idr and measured_at — the
# 2026-09-14 reading of the sleeve sub-account 008488L — and differ only in
# the assumed concurrent_names.  This is deliberate: it is the exact
# decomposition ADR-0017 asked for (§"The trading limit does not follow the
# cash", §"Open questions"), and it is what lets impact_sweep.py sweep N
# without re-deriving equity each time.

_MEASURED_EQUITY_IDR: float = 15_000_000.0
_MEASURED_AT: str = "2026-09-14"
_MEASURED_SOURCE: str = (
    "GET /balance/withdrawable?balance_scope=BALANCE_SCOPE_SELECTED_ACCOUNT"
    "&account_number=008488L"
)

SLEEVE_N1 = SleeveSizing(
    name="sleeve_n1",
    equity_idr=_MEASURED_EQUITY_IDR,
    concurrent_names=1,
    measured_at=_MEASURED_AT,
    source=_MEASURED_SOURCE,
)
"""Whole sleeve concentrated in one name: 15,000,000 IDR/name."""

SLEEVE_N3 = SleeveSizing(
    name="sleeve_n3",
    equity_idr=_MEASURED_EQUITY_IDR,
    concurrent_names=3,
    measured_at=_MEASURED_AT,
    source=_MEASURED_SOURCE,
)
"""Three concurrent names: 5,000,000 IDR/name."""

SLEEVE_N5 = SleeveSizing(
    name="sleeve_n5",
    equity_idr=_MEASURED_EQUITY_IDR,
    concurrent_names=5,
    measured_at=_MEASURED_AT,
    source=_MEASURED_SOURCE,
)
"""Five concurrent names: 3,000,000 IDR/name."""

SLEEVE_N10 = SleeveSizing(
    name="sleeve_n10",
    equity_idr=_MEASURED_EQUITY_IDR,
    concurrent_names=10,
    measured_at=_MEASURED_AT,
    source=_MEASURED_SOURCE,
)
"""Ten concurrent names: 1,500,000 IDR/name."""

LEGACY_ADR0011 = SleeveSizing(
    name="legacy_adr0011_void",
    equity_idr=300_000_000.0,
    concurrent_names=10,
    measured_at="n/a",
    source=(
        "n/a — VOID reference point (ADR-0017 decision 5). Recorded ONLY so "
        "runs priced under the old assumption remain comparable to the "
        "historical record (every strategy_portfolio.md before 2026-09-14). "
        "Not a measurement: ADR-0011's 300M IDR facility was against the "
        "core book, was superseded by ADR-0015, and the sleeve never drew "
        "on it. Do not select this preset for a new run."
    ),
)
"""
The void ADR-0011 assumption: 300M IDR / 10 names = 30,000,000 IDR/name.
Retained so every historical strategy_portfolio.md / impact_sweep.md run
(priced at 30M) stays legible against sleeve-sized runs, per ADR-0017
decision 6 ("results priced at 30M/name are not comparable to sleeve-sized
results ... reports must record the notional they were priced at").
"""

# All presets, for iteration (tests, reports) without hand-maintaining a list.
ALL_PRESETS: tuple[SleeveSizing, ...] = (
    SLEEVE_N1, SLEEVE_N3, SLEEVE_N5, SLEEVE_N10, LEGACY_ADR0011,
)


# ---------------------------------------------------------------------------
# Active sizing
# ---------------------------------------------------------------------------

ACTIVE_SLEEVE_SIZING: SleeveSizing = SLEEVE_N5
"""
The sizing preset used by default throughout strategy_fit wherever a
per-name order notional is needed (see ``strategy_fit/impact.py``'s
``POSITION_NOTIONAL_IDR``).

CHANGED 2026-09-14 (ADR-0017): previously ``strategy_fit.impact`` hard-coded
``POSITION_NOTIONAL_IDR = 30_000_000`` — twice the sleeve's entire measured
equity. This module makes the derivation explicit and swappable.

**N=5 is a PLACEHOLDER, not a decision.** ADR-0017's "Open questions"
section leaves "how many concurrent names should the sleeve hold" genuinely
unresolved — it is a portfolio-construction question for Track A, not
something this module can answer. N=5 was picked only so the harness's
default (non-swept) run has a single number to price against, landing
roughly in the middle of the {1, 3, 5, 10} range this module also exposes.
This is exactly why ``strategy_fit/impact_sweep.py`` sweeps N instead of
trusting it: any reader who cares about the sensitivity of a verdict to this
placeholder should read the sweep, not this default.
"""
