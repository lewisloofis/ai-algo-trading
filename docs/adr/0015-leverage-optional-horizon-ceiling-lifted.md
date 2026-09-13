# ADR 0015 — Leverage is optional; the ~2-trading-day horizon ceiling is lifted for owned-cash strategies

- **Status:** Accepted
- **Date:** 2026-09-13
- **Deciders:** Lewis
- **Tags:** capital, leverage, financing, cost-gate, horizon, capacity
- **Supersedes:** [ADR-0011](./0011-capital-model-300m-leverage-sleeve.md) (300M leverage sleeve as the *only* capital model)
- **Amends:** none — [ADR-0014](./0014-financing-accrues-between-settlement-dates.md)'s settlement-clock arithmetic is unchanged and still applies whenever leverage is used

> ADR-0011 treated the 300M IDR leverage facility as the sleeve's *only*
> source of capital, and derived a hard ~2-trading-day horizon ceiling and a
> mandatory financing charge from that assumption. Both were correct
> descriptions of the leverage facility — but they were never actually
> properties of *the strategies*, only of *how they were funded*. This ADR
> makes the funding source a first-class, swappable choice
> (`strategy_fit.capital_model.CapitalModel`) and changes the default to
> owned cash, which carries neither constraint.

## Context

ADR-0011 pinned two structural constraints into the strategy-fit harness and
cost gate, both derived from the mechanics of the 300M IDR T+2 leverage
facility:

1. **A horizon ceiling of ~2 trading days**, enforced by
   `strategy_fit.harness.reject_horizon_exceeded` — any family with a longer
   intended hold was rejected as `capital_blocked` before it was ever tested.
2. **A financing charge of 10 bps/calendar-day** on the borrowed amount,
   added to every cost hurdle by `strategy_fit.financing.financing_bps` (rate
   correct per ADR-0011 §4; the settlement-day arithmetic under it was later
   corrected by ADR-0014).

Both constraints assumed the sleeve had no alternative funding source. That
assumption no longer holds: the sleeve may now be funded with **owned cash**,
which has no tenor clock and no borrowing cost. Re-examining the two
constraints under owned cash:

- **Financing was the only hold-period-dependent cost in the gate.** With it
  removed, the cost *drag* from turnover falls sharply. At the old ~2-day
  band, financing plus round-trip fees implied roughly 125 round-trips/year
  at ~100 bps per round-trip — on the order of 100%/year of drag. At a
  monthly-rebalance band (≈19% turnover/month, 12 rebalances/year, ~80 bps
  round-trip) the drag is ≈1.8%/year — a **~50x reduction**. The per-trade
  hurdle itself barely moves (impact and the fee floor are unchanged); what
  changes is that it now amortises over ~20 trading days instead of ~2,
  because there is no financing meter running in between.
- **ADR-0011's horizon ceiling rested on two grounds, and the second was
  already known false.** ADR-0011 §5 justified the ~2-day ceiling partly on
  "plan 10's data-length ceiling" coinciding with the capital ceiling. That
  coincidence was never real: chartbit serves daily history back to each
  symbol's own listing date (PTRO from 2003, MEDC from 2000), not the
  ~636-row 2024-onward window that plan 13's OOS split happened to use. Data
  length was never a ~2-day constraint; only the leverage facility's tenor
  clock was.

With the financing term optional and the data-length justification void, the
horizon ceiling has no remaining basis for owned-cash strategies. It is
specific to *funding by the leverage facility*, not to the sleeve as a whole.

## Decision

1. **Leverage is optional.** Capital funding is now modelled explicitly via
   `strategy_fit.capital_model.CapitalModel`, a frozen dataclass with two
   presets:
   - `LEVERAGED_SLEEVE` — the ADR-0011 model: `funded_by_leverage=True`,
     `max_hold_trading_days=2`, `financing_bps_per_cal_day=10`.
   - `OWNED_CASH` — `funded_by_leverage=False`, `max_hold_trading_days=None`
     (no ceiling), `financing_bps_per_cal_day=0`.

2. **`OWNED_CASH` is the new default** (`ACTIVE_CAPITAL_MODEL`). This is a
   breaking change to default behaviour, made deliberately and dated here:
   before 2026-09-13, every hurdle and every horizon check assumed
   `LEVERAGED_SLEEVE`; after, they assume `OWNED_CASH` unless a caller passes
   `capital_model=LEVERAGED_SLEEVE` explicitly.

3. **The ~2-trading-day horizon ceiling is lifted under `OWNED_CASH`.**
   `reject_horizon_exceeded` never rejects a family when the active model's
   `max_hold_trading_days` is `None`. It still enforces the ceiling exactly as
   before when a `LEVERAGED_SLEEVE` (or any future model with a numeric
   ceiling) is active.

4. **Financing is 0 under `OWNED_CASH`.** `financing_bps`,
   `full_pass_hurdle_bps`, `best_feasible_hurdle_bps`, and `passes_gate` all
   accept an optional `capital_model` and return/consume a 0.0 financing term
   whenever `funded_by_leverage` is `False` (or the model's rate is 0). The
   rest of the hurdle — the fee floor and modelled impact — is unchanged.

5. **Results under the two models are not comparable.** A run of
   `strategy_portfolio.md` (or any cell result) produced under `OWNED_CASH`
   cannot be compared directly against one produced under `LEVERAGED_SLEEVE`:
   the horizon gate and the hurdle differ. Every report and comparison must
   record which capital model produced it.

## Rejected alternatives

**Keep `LEVERAGED_SLEEVE` as the sole model and add owned cash as a
special-cased override inside `reject_horizon_exceeded` / `financing_bps`.**
Rejected: this reproduces exactly the failure mode ADR-0011 itself warned
about (fitting the implementation to one assumption). A first-class
`CapitalModel` value keeps the harness capital-agnostic and lets a future
funding source (e.g. a different facility, or a blended model) be added
without touching the gate logic again.

**Leave the default at `LEVERAGED_SLEEVE` and require every owned-cash caller
to opt in.** Rejected: leverage funding is now the exception, not the rule —
the sleeve's primary funding going forward is owned cash, so the default
should reflect the common case. Every call site that specifically wants the
leveraged-sleeve behaviour is updated to pass `capital_model=LEVERAGED_SLEEVE`
explicitly (see the strategy_fit test suite), so the exception is visible at
its call site rather than hidden in a global default.

**ADR-0011's rejected alternative "Fund longer-horizon (weeks-plus)
strategies" is now void.** ADR-0011 rejected funding multi-week strategies on
the grounds that the T+2/T+4 clock makes them un-fundable on leverage
regardless of signal quality. That reasoning is specific to the leverage
facility and does not apply to `OWNED_CASH`, which has no clock. This ADR
does not re-accept that alternative as a decision — no long-horizon family
exists in the catalog yet (see Consequences) — but it records that the
rejection no longer holds as a blanket constraint on the sleeve.

## Consequences

**Positive**

- The strategy-fit harness and cost gate are now capital-agnostic: adding a
  third funding model (e.g. a different facility, or a blended owned-cash /
  leverage sleeve) is a new `CapitalModel` value, not a rewrite of
  `reject_horizon_exceeded` or `financing_bps`.
- Multi-week and monthly-rebalance strategy families — previously rejected at
  the harness gate before they were ever tested — are now admissible under
  `OWNED_CASH`. The ~50x drag reduction at a monthly-rebalance band (§Context)
  makes that band worth exploring for the first time.
- ADR-0014's settlement-clock arithmetic (`calendar_days_outstanding`,
  the T+2/T+2 settlement-window logic) is untouched and remains the correct
  model whenever `LEVERAGED_SLEEVE` (or any leverage-funded model) is active.

**Negative / costs**

- **This unblocks, but produces nothing by itself.** Every family in the
  current catalog (`strategy_fit/catalog_meta.py`) is `≤2`-trading-day or
  same-day. Lifting the horizon gate does not create a single new tested
  cell until longer-horizon strategy families are actually written — that is
  follow-on work, not a consequence of this ADR.
- **Open question: `MIN_EFF_N=30` on the de-overlapped sample needs
  rethinking at a ~20-day hold band.** The effective-N accounting
  (`strategy_fit/harness.py:deoverlap_daily` and friends) was built and tuned
  around ≤2-day holds, where non-overlapping windows are abundant. At a
  20-trading-day hold, non-overlapping windows over a multi-year history
  become scarce (a handful of independent observations per year per name),
  and it is not yet decided whether `MIN_EFF_N=30` should stay a flat
  per-family floor, scale with hold length, or be replaced by a pooled
  (cross-name) effective-N count for long-horizon families. This ADR flags
  the question; it does not resolve it.
- **The universe available to test the new band is currently unusable for
  it.** Verified 2026-09-13: the 201-name funding-eligible universe fetched
  into `research/data/daily/` is **201/201 still trading as of 2026 — zero
  delistings**. The emitten funding endpoints return *currently tradeable*
  names by construction, so they can never contain a delisted one, and there
  is no `company_id → ticker` lookup with which to enumerate the historical
  set (every probed path 404s). chartbit *does* serve any ticker named
  explicitly — SRIL, GOLL, BTEL all return full history to listing date — so
  the data exists; only the ticker list must come from outside this API.
  Until it does, a long-horizon backtest on this store measures survivorship
  rather than edge: a first pass produced a +25%/yr momentum spread
  (t=3.17, and +42%/yr in 2016-2026) that is an artifact of exactly this.
  Lifting the horizon ceiling is therefore necessary but **not sufficient** —
  the point-in-time universe gap is the binding blocker on the new band.
- Every downstream doc or report that assumed `LEVERAGED_SLEEVE` unconditionally
  (plan 10, plan 13, prior `strategy_portfolio.md` runs) must be read with the
  capital model that produced it in mind — none of them are wrong, but none
  are automatically comparable to a future `OWNED_CASH` run.

## Related

- [ADR-0011 — capital model (300M leverage sleeve)](./0011-capital-model-300m-leverage-sleeve.md)
  — superseded by this ADR as the sole capital model; its leveraged-sleeve
  parameters (300M facility, T+2/T+3/T+4 clock, 10 bps/cal-day rate) live on
  unchanged as the `LEVERAGED_SLEEVE` preset.
- [ADR-0014 — financing accrues between settlement dates](./0014-financing-accrues-between-settlement-dates.md)
  — the settlement-clock arithmetic this ADR does not touch; still correct
  and still applies whenever leverage is used.
- `research/strategy_fit/capital_model.py` — the `CapitalModel` dataclass and
  the `LEVERAGED_SLEEVE` / `OWNED_CASH` presets this ADR introduces.
- `research/strategy_fit/harness.py` — `reject_horizon_exceeded`, now
  capital-model-aware.
- `research/strategy_fit/financing.py` — `financing_bps` and the hurdle
  functions, now capital-model-aware.
- [Plan 13 — capital allocation & capacity](../plans/13-capital-allocation-and-capacity.md)
  — the duty-cycle and basket-construction work that was scoped entirely
  around the leveraged sleeve; needs revisiting for an owned-cash sleeve with
  no tenor clock.
