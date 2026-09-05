# ADR 0014 — Financing accrues between settlement dates, not from the trade date

- **Status:** Accepted
- **Date:** 2026-09-05
- **Deciders:** Lewis
- **Tags:** capital, financing, cost-gate, settlement, calendar

> **Amends [ADR-0011](./0011-capital-model-300m-leverage-sleeve.md)** on one point
> only: how `calendar_days_outstanding` is counted. ADR-0011's capital model —
> the 300M facility, the T+2/T+3/T+4 tenor clock, one borrow per cycle, the
> ~2-trading-day horizon ceiling, and the 10 bps/calendar-day rate — all stand
> unchanged. Its **worked example and its day-of-week conclusion do not**; see
> "What ADR-0011 gets wrong" below.

## Context

ADR-0011 put financing into the plan-10 cost gate as
`financing_bps = 10 × calendar_days_outstanding(entry_dow, hold)` and left the
counting rule to the implementation. `research/strategy_fit/financing.py`
counted **calendar days from the entry trade date** through a settlement leg
placed one trading day after exit:

```
old:  calendar_days_outstanding = cal_days(entry_date → entry + hold + 1 trading days)
```

That produced a Thu-entry / Fri-exit cost of 4 calendar days (40 bps) and made
late-week entries the expensive ones.

**The constant was fitted to the assumption, not to the broker's terms.** The
module's own docstring said `SETTLEMENT_TRADING_DAYS = 1` was *"empirically
derived to match plan-10 §3 table examples"* — but that table was itself written
from the same belief. The tests then pinned the fitted values, so the whole stack
was internally consistent and externally wrong. Nothing in it was ever checked
against how the facility actually bills.

**How the facility actually bills.** The fee is a late charge measured against a
settlement deadline, and *both legs settle T+2 trading days*:

- the **buy** settles T+2 — that is when the facility funds the purchase and the
  borrowed money is actually out;
- the **sell** settles T+2 — that is when the proceeds arrive and repay it.

So the charged period is `[settle(buy), settle(sell)]`, and the T+2 shift is
common to both endpoints and **cancels**. What survives is not the entry weekday
but the weekday the *settlement* lands on. The broker's own two examples:

| | buy settles | sell settles | charged | old model said |
|---|---|---|---|---|
| buy Thu, sell Fri | Mon | Tue | **1 day** (10 bps) | 4 days (40 bps) |
| buy Wed, sell Thu | Fri | Mon | **3 days** (30 bps) | 2 days (20 bps) |

The Wednesday case is the whole structure in one row: Wed settles Friday, Thu
settles Monday, so the weekend falls *inside* the charged window and Saturday and
Sunday are billed on top of the ordinary one day.

## Decision

1. **Financing is charged over `[settle(entry), settle(exit)]`**, where
   `settle(d) = add_trading_days(d, 2)` on the shared IDX market calendar
   (ADR-0012):

   ```
   charged_calendar_days = settle(entry + hold) − settle(entry)
   ```

   `SETTLEMENT_TRADING_DAYS` becomes **2** and now means what its name says — the
   regular-way T+2 lag applied to both legs — rather than a fudge factor.

2. **The rate is unchanged at 10 bps per charged calendar day.** ADR-0011 §4's
   formula stands; only the day count under it changes.

3. **The day-of-week lever is "avoid the entry whose settlement window straddles
   a weekend," not "enter early in the week."** For a 1-trading-day hold that
   means avoiding **Wednesday** (3 days); Mon, Tue, Thu and Fri all cost 1 day.
   For a 2-day hold the expensive entries are Tue and Wed (4 days) against 2 for
   the rest. With the entry day chosen freely, the best-feasible financing is
   exactly **10 bps per trading day held** for every hold within ADR-0011's
   ≤2-day ceiling.

4. **Same-day round-trips remain free**, but now as a consequence rather than a
   special case: both legs settle on the same date, so the window has zero
   length. The `hold == 0` branch is deleted from the implementation.

5. **Holidays extend the window exactly as weekends do**, and compose for free
   through `add_trading_days`. A holiday that shifts *both* settlements equally
   cancels, just as the T+2 shift does; only a holiday landing *inside* the
   charged window is billed.

## What ADR-0011 gets wrong

Two passages in ADR-0011 are superseded by this ADR and must not be reused:

- **Context, ¶"Financing runs at 10 bps/calendar-day"** — the worked example
  "A Thu-entry / Fri-sell … Fri–Mon = 4 calendar days = 40 bps" is wrong in both
  the day count and the mechanism. The correct answer is 1 day / 10 bps.
- **Context ¶ and Consequences ¶"Day-of-week structure"** — "early-week entries
  beat late-week and weekend-spanning ones" is **inverted**. Wednesday is the
  expensive day; Thursday and Friday are cheap.

The claim that financing "roughly doubles the real cost hurdle" was also derived
from the overstated day count and does not survive: at the ≤2-day ceiling,
best-feasible financing is 10–20 bps against a round-trip fee gate of 40 bps plus
a per-name spread, so it is a meaningful additive term but not a doubling.

## Rejected alternatives

**Keep the trade-date-anchored model as a conservative overestimate.** Leave the
old count in place on the grounds that overcharging financing only rejects
marginal strategies. Rejected on two counts. A gate that is wrong in a known
direction is not conservatism, it is an unquantified bias that silently reshapes
which families look viable. And it was not uniformly conservative — it
*under*charged the one genuinely expensive case (Wednesday, 20 bps against a true
30), so it biased selection *toward* the worst entry day.

**Count only the non-business days in the gap** — read the broker's "you pay for
Saturday and Sunday" literally and charge 2 days for the Wednesday case, 0 for
the rest. Rejected: it contradicts the other stated example, where a Thursday buy
sold on Friday is charged 1 day despite no weekend in the gap. The settlement-
difference rule reproduces both examples; this one reproduces neither fully.

**Model a two-day interest-free grace.** Treat T+2 as free time and charge only
beyond it. Rejected: a sale cannot draw on the grace, because the sale settles
T+2 as well — the grace shifts rather than accumulates. The only way to hold the
position and pay nothing is to repay from **owned cash** by the buy's settlement
date, which is a use for the salary drip, not a property of the trade.

**Re-derive the count from the plan-10 §3 table again.** Rejected: that table is
downstream of the belief, not evidence for it. It has been corrected to match
this ADR, and the regression tests now pin the superseded values explicitly as
values the implementation must *not* return.

## Consequences

**Positive**

- The cost gate is now derived from the facility's stated terms rather than
  fitted to a table this repo wrote. `SETTLEMENT_TRADING_DAYS` is a broker
  parameter with a checkable meaning, so a change in broker terms is a
  one-constant edit rather than a re-derivation.
- The best-feasible hurdle falls by a flat **10 bps for every hold ≥ 1** and is
  unchanged at hold = 0.
- Day-of-week scheduling becomes a cheap, real lever for plan 13: one weekday to
  avoid per hold length, worth 20 bps on a 1-day hold.
- Holiday handling composes correctly instead of needing a separate rule, which
  closes the gap plan 14 §4 opened.

**Negative / costs**

- **No verdict changes.** Measured against the 819 cost-gated cells of the W2.3
  run, the closest hold ≥ 1 cell misses its hurdle by **107 bps** (p10 151, median
  210). A 10 bps correction flips nothing, and the plan-10 `INCONCLUSIVE` verdict
  stands. This ADR buys correctness, not candidates.
- ADR-0011's day-of-week conclusion is inverted, and every downstream doc that
  repeated it (plan 10 §3, plan 13 §3/§7, plan 16, plan 14) needed correcting.
  Anything written against "prefer early-week entries" must be re-read.
- The rule assumes both legs settle regular-way T+2. A broker change, a cash
  market with a different settlement cycle, or a special-settlement instrument
  invalidates it — and unlike the old model, this one will be *silently* wrong
  rather than loudly conservative if that happens.

## Related

- [ADR-0011 — capital model](./0011-capital-model-300m-leverage-sleeve.md) — the
  capital model this amends on the financing-arithmetic point only.
- [ADR-0012 — market calendar](./0012-market-calendar-single-source-cross-language.md)
  — supplies the `add_trading_days` used by both settlement legs.
- [Plan 10 — strategy fit & portfolio](../plans/10-strategy-fit-and-portfolio.md)
  — §3's financing table, corrected to match.
- [Plan 13 — capital allocation & capacity](../plans/13-capital-allocation-and-capacity.md)
  — the day-of-week entry scheduling this re-points at Wednesday.
- [Plan 14 — market calendar & time system](../plans/14-market-calendar-and-time-system.md)
  — the holiday-aware financing clock this makes structurally correct.
