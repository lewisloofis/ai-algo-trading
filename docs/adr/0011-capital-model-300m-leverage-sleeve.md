# ADR 0011 — Capital model — AI sleeve is a 300M IDR T+2 leverage facility run as a periodic batch allocator

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Lewis
- **Tags:** capital, leverage, financing, cost-gate, sizing, risk
- **Amended by:** [ADR-0014](./0014-financing-accrues-between-settlement-dates.md) (2026-09-05) — financing arithmetic only

> ⚠️ **Amended 2026-09-05.** The capital model below stands in full — the 300M
> facility, the T+2/T+3/T+4 clock, one borrow per cycle, the ~2-trading-day
> horizon ceiling, and the 10 bps/calendar-day rate are unchanged. But the
> **worked example and the day-of-week conclusion in this ADR are wrong**:
> financing accrues between the two legs' T+2 *settlement* dates, so a Thu-entry /
> Fri-sell costs 1 calendar day (10 bps), not 4 (40 bps), and **Wednesday** is the
> expensive entry day rather than Thursday and Friday. The passages affected are
> flagged inline below. Read
> [ADR-0014](./0014-financing-accrues-between-settlement-dates.md) before using any
> financing number from this document.

> Pins the capital model that feeds [plan 10](../plans/10-strategy-fit-and-portfolio.md)'s
> cost gate and motivates a forthcoming plan 13 (capital allocation & capacity).
> Builds on [ADR-0009](./0009-universe-is-funding-intersection-strategy-fit-downstream.md)
> (the universe is already scoped to trading-limit-eligible names) and makes
> capital a first-class validation constraint alongside data and strategy fit.

## Context

The AI trading sleeve has no idle cash. The **1.1B IDR core stock book** is
already fully invested in live positions and serves as collateral. The only
new capital available to the AI system is a **300M IDR trading-limit (leverage)
facility** drawn against that book. Any strategy that cannot be funded by this
facility is effectively un-fundable.

Until this ADR, capital was treated as a downstream concern — something plan 10
would handle when it got there. Two properties of the facility make that
deferral dangerous:

1. **The tenor clock is punishing and non-resettable by re-borrow.** The
   facility runs T+2 (sell-safe) / T+3 (warning) / T+4 (forced liquidation of
   the *core 1.1B book*, not a fee — a broker-priced catastrophic event). A new
   borrow does **not** reset the clock; you must go approximately 2 flat trading
   days to reset the limit. The facility therefore imposes a natural **duty
   cycle**: ~2 days deployed, ~2 days out → roughly a 4–5 trading-day round on
   a single clock.

2. **No staggering, no top-ups.** Drawing *any* part of the 300M locks the
   *entire* facility. Borrow 100M and the remaining 200M is frozen until repay
   or reset. There is exactly one borrow event per cycle, sized once at entry;
   you cannot open a second tranche on a different clock mid-cycle. The
   portfolio is therefore a **single synchronised basket entered atomically and
   exited within tenor** — the system is a **periodic batch allocator, not a
   continuous trader**.

Financing runs at **10 bps/calendar-day** on the borrowed amount (not trading
days). A Thu-entry / Fri-sell, for example, draws settlement-driven carry
through the weekend: Fri–Mon = 4 calendar days = 40 bps of financing on top of
the round-trip fee gate. Weekend-spanning holds are structurally punishing.
This roughly **doubles** the real cost hurdle compared with a fee-only gate and
introduces a **day-of-week structure** (early-week entries beat late-week and
weekend-spanning ones).

> ⚠️ **Superseded by [ADR-0014](./0014-financing-accrues-between-settlement-dates.md).**
> The rate is right; the arithmetic above is not. Financing is charged over
> `[settle(buy), settle(sell)]` with both legs at T+2, so the shift cancels and
> the Thu→Fri example costs **1 calendar day (10 bps)**. The expensive entry is
> **Wednesday** (settles Fri, exit settles Mon → the weekend lands inside the
> charged window → 3 days), not Thursday or Friday. "Roughly doubles" and
> "early-week entries beat late-week" are both artefacts of the overstated count.

The only genuine owned-cash inflow is ~15M IDR/month from salary. At full
deployment, financing costs approximately 0.6–1.2M IDR per cycle, ~4
cycles/month ≈ 2.4–4.8M/month — a meaningful fraction of the salary drip.
The sleeve must clear its financing nut before it is net positive. Owned cash
carries no T+4 clock and can pay down financing, build a buffer, or serve as
fresh deposit to unlock more limit.

These properties are foundational constraints that shape the cost gate, the
horizon ceiling, and the entire allocation model. They must be locked now so
plan 10 and plan 13 can build on them.

## Decision

1. **The AI sleeve is funded only by the 300M IDR trading-limit facility drawn
   against the 1.1B IDR collateral book.** There is no idle cash. Any strategy
   that cannot be funded by this facility is un-fundable, independent of its
   signal quality.

2. **The tenor clock governs every borrow: T+2 sell-safe / T+3 warning / T+4
   core-book forced liquidation.** Re-borrowing does not reset the clock. To
   reset, the facility must sit flat for approximately 2 trading days. This
   produces a ~4–5 trading-day duty cycle (≈ 2 deployed, ≈ 2 flat) with a
   single running clock per cycle. Average utilisation is therefore bounded at
   roughly 40–50% of calendar time.

3. **No staggering, no top-ups — one borrow event per cycle, sized once at
   entry.** Drawing any part of the 300M locks the whole facility; partial draws
   cannot be topped up without a fresh deposit. The sleeve is therefore a
   **periodic batch allocator**: a single atomic basket is entered, held within
   tenor, and fully exited before the next cycle begins. No averaging down with
   leverage.

4. **Financing is 10 bps/calendar-day on the borrowed amount.** The cost gate
   gains a `financing_bps = 10 × calendar_days_outstanding(entry_dow, hold)`
   term. *(The formula stands; `calendar_days_outstanding` is redefined by
   [ADR-0014](./0014-financing-accrues-between-settlement-dates.md) as
   `settle(entry + hold) − settle(entry)`, both legs T+2.)* This makes the effective hurdle day-of-week-dependent and roughly
   doubles it relative to fees alone. The 40 bps round-trip fee gate from
   ADR-0009 / `research/backtest/backtest.py` remains the single source of
   truth for round-trip fees; financing is *added on top*, not a second copy of
   it.

5. **The capital horizon ceiling is approximately 2 trading days.** This
   coincides with plan 10's data-length ceiling: weeks-plus strategy families
   are both `data_blocked` and capital-blocked. The deployable band is
   same-day through 2-day swing/breakout/event. Same-day larger-move intraday
   strategies are structurally **re-opened** by this framing — the plan-06
   NO-GO killed tick-scale 5–10 bps intraday strategies on illiquid names, not
   larger-move same-day strategies on liquid ones.

6. **Forced liquidation is a tail risk, not a fee; exit liquidity is a hard
   constraint.** A hard unwind-by-T+2 rule applies. The sleeve may only deploy
   into names whose full position can be exited within tenor at acceptable
   market impact (exit-liquidity-within-tenor capacity constraint). Sleeve
   drawdown limits must be bounded so that the worst-case outcome never
   threatens maintenance margin on the 1.1B core book.

## Rejected alternatives

**Treat the sleeve as a continuous trader with rolling positions.** Run
multiple independent positions with staggered entry and exit times, re-entering
freely as capital becomes available. Rejected: the no-stagger / no-top-up
property and the single reset clock make overlapping independent positions on
separate clocks impossible within the facility. The sleeve is one synchronised
borrow per cycle by construction.

**Stagger tranches / average down with leverage.** Enter a partial position,
then add to it on dips using the remaining 200M facility headroom. Rejected:
drawing any part of the 300M freezes the remainder until repay or reset. There
is no headroom to draw incrementally without triggering a new cycle from zero.
No partial-tranche strategy is feasible without a fresh deposit between
tranches.

**Ignore financing in the cost gate (treat the 40 bps round-trip as the whole
hurdle).** Apply the existing backtest fee gate and defer financing to
post-trade P&L accounting. Rejected: 10 bps/cal-day roughly doubles the real
hurdle and is day-of-week-dependent. Omitting it from the gate would pass
strategies that are net-negative after carry, and would fail to surface the
weekend-spanning penalty at selection time. The gate must be economically honest
at strategy-fit time, not corrected post-hoc.

**Fund longer-horizon (weeks-plus) strategies.** Accept that some positions will
need to be rolled or held for 2–4 weeks for the signal to resolve. Rejected:
the T+2/T+4 clock makes them un-fundable on leverage regardless of signal
quality. They are capital-blocked independent of the data-length ceiling, and
rolling is not available without a reset gap that erases the position.

## Consequences

**Positive**

- Capital is now in the validation loop from the start: strategies that are
  un-fundable on this facility are filtered at plan 10 alongside `data_blocked`
  and signal-quality rejections. The cost gate is economically honest.
- The deployable strategy band is sharply defined: same-day through ~2-day
  swing, breakout, and event strategies. Larger-move intraday is re-admitted
  (plan-06 NO-GO correctly scoped to thin-edge tick-scale strategies, not
  larger-move same-day).
- Core-book protection is explicit: the T+4 liquidation risk is named and
  bounded at the ADR level; exit-liquidity and drawdown constraints follow
  directly.
- Day-of-week structure in the cost gate creates a real scheduling optimisation
  lever. *(Direction corrected by
  [ADR-0014](./0014-financing-accrues-between-settlement-dates.md): the lever is
  to avoid the entry day whose settlement window straddles the weekend —
  **Wednesday** on a 1-day hold, Tue/Wed on a 2-day hold — not to prefer
  early-week entries.)*

**Negative / costs**

- Duty-cycle utilisation is bounded at ~40–50% of calendar time, which throttles
  annualisable return to approximately `per-cycle edge × cycles/year` — a hard
  ceiling the facility imposes regardless of signal quality.
- The financing nut must be cleared every cycle before the sleeve is net
  positive; at full deployment this consumes a meaningful fraction of the ~15M
  IDR/month salary drip.
- The parameters pinned here — 300M limit, 1.1B collateral book, 10 bps/cal-day
  financing, 15M/month salary, ~2-day reset gap — are point-in-time. They will
  move as the account grows or broker terms change; this ADR will need a
  superseding update when they do.
- Plan 10 must implement the `financing_bps` term in its cost gate; a new
  plan 13 (capital allocation & capacity) must be drafted to model position
  sizing, cycle scheduling, and day-of-week optimisation. Neither exists yet.

## Related

- [ADR-0009 — universe is the funding intersection](./0009-universe-is-funding-intersection-strategy-fit-downstream.md)
  — the universe is already scoped to trading-limit-eligible names; this ADR
  explains why that gate matters.
- [Plan 10 — strategy fit & portfolio](../plans/10-strategy-fit-and-portfolio.md)
  — the cost gate this tightens with the `financing_bps` term.
- [Plan 09 — universe screener](../plans/09-universe-screener.md) — the funded
  universe the sleeve draws from.
- [Plan 06 — fill simulator & backtest](../plans/06-fill-simulator-and-backtest.md)
  — the intraday NO-GO this re-contextualises (tick-scale thin-edge, not
  larger-move same-day).
- Plan 13 — capital allocation & capacity (proposed, not yet drafted) — the
  position-sizing, cycle-scheduling, and day-of-week optimisation work this ADR
  motivates.
