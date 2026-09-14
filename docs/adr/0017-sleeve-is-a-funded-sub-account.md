# ADR 0017 — The sleeve is a separate funded broker sub-account; its capital is measured, not assumed

- **Status:** Proposed
- **Date:** 2026-09-14
- **Deciders:** Lewis
- **Tags:** capital, accounts, risk, execution, cost-gate, capacity
- **Amends:** [ADR-0015](./0015-leverage-optional-horizon-ceiling-lifted.md) — it made the capital model's *type* swappable but left its *size* and *location* unstated; both are now measured
- **Related:** [ADR-0011](./0011-capital-model-300m-leverage-sleeve.md) (superseded by 0015), [ADR-0016](./0016-alpha-and-infrastructure-are-separate-tracks.md), [plan 18 §3.4](../plans/18-alpha-and-infrastructure-split.md)

> ADR-0011 assumed the sleeve was funded by a 300M IDR leverage facility.
> ADR-0015 made leverage optional and defaulted to owned cash. Neither said
> **how much** capital the sleeve has, or **where it lives** — and the harness
> meanwhile hard-codes a per-name order notional of 30M IDR. Measured on
> 2026-09-14, the sleeve is a distinct broker sub-account holding **15,000,000
> IDR** of owned cash. The per-name constant is therefore twice the entire
> sleeve, and every impact figure the grid has ever produced was priced at a
> size the account cannot take.

## Context

### The sleeve now has an address

The brokerage login exposes two portfolios
(`GET /portfolio/v2/summary` → `summary_per_portfolios[]`):

| account | name | state before | state after |
|---|---|---|---|
| `0001958` | LEWIS | core book, ~1.3B invested, 29,746,598 cash | same book, 14,746,598 cash |
| `008488L` | Trading | empty | **15,000,000 cash**, 0 positions |

15,000,000 IDR was transferred into `008488L` on 2026-09-14. The core book's
cash fell by exactly that amount, so the transfer is clean and the two accounts
are genuinely separate balances rather than two views of one.

### Why a sub-account rather than a book-keeping baseline

Both accounts share one login, so before the split the bot and the core book
were the same book to every endpoint. That breaks three things, set out in full
in [plan 18 §3.4](../plans/18-alpha-and-infrastructure-split.md): B1 could never
reach MATCH (the core book has no fills to reconcile against, so it reports as
permanent CRITICAL divergences), B2's exposure caps would measure the core book
rather than the sleeve, and B4's first mistake could reach positions the bot
never bought.

The alternative considered was an opening-balance baseline table — a recorded
assertion that "the broker was right on day one", plus an operator
re-baselining step after every manual trade in the core book. A separate
account replaces that assertion with a boundary: the sleeve starts empty, so
derived position equals broker balance from the first fill with nothing assumed.

### The trading limit does not follow the cash

This is the part that changes the capital model rather than merely relocating it.

`GET /balance/cash/info` reports **byte-identical figures for both account
numbers** — it ignores `acc_no` entirely. Before and after the transfer:

| field | before | after | change |
|---|---:|---:|---|
| `trade_limit` | 392,013,889.11 | 377,013,889.11 | **−15,000,000** |
| `trade_balance` | 29,746,598.11 | 14,746,598.11 | −15,000,000 |

The limit is computed against the core book, and moving cash into the sleeve
**reduced** it by the amount moved. Nothing observable suggests the sleeve
acquired a limit of its own.

> **Stated at its true strength.** This shows `cash/info` reports no limit for
> the sleeve; it does not *prove* the sleeve has none, since that endpoint
> cannot address the sleeve at all. The question is settled only by attempting
> an order larger than 15M in `008488L`, which is gated behind B4. Until then
> the sleeve is sized as owned cash, which is the conservative reading.

### The per-name notional is larger than the account

`research/strategy_fit/impact.py:78` sets `POSITION_NOTIONAL_IDR = 30_000_000`,
documented as the 300M sleeve divided across ~10 names. Impact is priced as

```
participation = position_notional_idr / adv_notional_idr
impact_bps    ≈ 2 · coeff · daily_vol_bps · sqrt(participation)
```

so impact scales with the square root of assumed order size. At a 15M sleeve the
per-name notional cannot exceed 15M even fully concentrated in one name, and a
realistically diversified sleeve puts it far lower. Against the 30M constant:

| real per-name notional | vs 30M | impact multiplier (`sqrt`) |
|---|---:|---:|
| 15M (whole sleeve, one name) | 0.50× | **0.71×** |
| 5M (three names) | 0.17× | **0.41×** |
| 3M (five names) | 0.10× | **0.32×** |

This compounds with the error Track C already corrected. Plan 18 §4.4 found the
impact *coefficient* over-charged ~3× for every uncalibrated name; this is a
second, independent over-charge in the *size* term, and it applies to every cell
the grid has ever scored — including the C4 re-judgement and both surviving
candidates (BNBR, HRTA).

Both errors point the same way: hurdles were too high, so surviving cells'
margins are understated and killed cells may not deserve to be dead. That is the
safe direction for capital and the wrong direction for a verdict — the same
asymmetry ADR-0016 recorded about `global_default: 1.0`.

## Decision

1. **Track B trades `008488L` exclusively.** The account number is a required
   input to every account-scoped call, never defaulted. Falling back to the
   token's own account (`0001958`) silently reconciles, sizes against, or trades
   the core book, which is the failure this decision exists to prevent.

2. **The sleeve's capital is measured, not declared.** Sleeve equity comes from
   `GET /balance/withdrawable?balance_scope=BALANCE_SCOPE_SELECTED_ACCOUNT&account_number=008488L`,
   which does scope correctly (15,000,000 vs 14,746,598). It is **15,000,000 IDR
   as of 2026-09-14** and will change; it is read, not hard-coded.

3. **`cash/info` is not a source of sleeve buying power.** It reports the core
   book regardless of `acc_no`. Any sizing path that reads it is reading the
   wrong account, and the type must make that impossible to confuse rather than
   relying on a comment.

4. **`OWNED_CASH` is confirmed as the sleeve's capital model** (ADR-0015 §2
   already made it the default). `LEVERAGED_SLEEVE` remains a valid preset but is
   **not available to `008488L` as currently constituted** — the 300M facility
   exists against the core book, not this sub-account.

5. **`POSITION_NOTIONAL_IDR = 30_000_000` is void.** Per-name order notional must
   derive from measured sleeve equity and the intended number of concurrent
   names, not from a constant fitted to a facility the sleeve does not use. Until
   it is re-derived, every impact and capacity figure in `strategy_fit` is
   priced at a size the account cannot take.

6. **Results priced at 30M/name are not comparable to sleeve-sized results**, on
   the same terms ADR-0015 §5 set for the two capital models. Reports must record
   the notional they were priced at, exactly as they now record the capital model.

## Consequences

- **Every cost verdict is provisional again, in the favourable direction.** The
  C4 re-judgement (plan 18 §4.4) stands as a *lower bound* on how much the
  hurdles were overstated. Re-pricing at sleeve size is now the cheapest
  outstanding piece of Track A work, and it needs no new data — the same shape as
  Track C, and for the same reason: the ruler was wrong.

- **Capacity caps (C\*) change direction.** Plan 13's per-name caps were derived
  to bound impact at 30M orders. At 15M total the binding constraint is no longer
  impact but *minimum viable position size* — a 3M position in a name with a
  40 bps round-trip fee floor has very little room, and lot granularity starts to
  matter (one lot of a 5,000 IDR name is 500,000 IDR, a third of a 1.5M slice).

- **B2's caps must source from `withdrawable`, scoped.** Gross-exposure and
  per-name caps computed from `cash/info` would be denominated in the core book's
  377M limit — roughly 25× the real sleeve.

- **The always-long null gate gets harder to clear, not easier.** Lower hurdles
  revive cells, but the null gate compares against buy-and-hold, which is
  unaffected by our order size. Cells revived purely by cheaper impact must still
  beat the null (the lesson recorded for PTRO).

- **This does not resurrect leverage.** The 300M facility still exists on the core
  book and `LEVERAGED_SLEEVE` still models it correctly. Using it would mean
  trading the core book, which decision 1 forbids for Track B.

- **The sleeve is small enough that fixed costs dominate.** At 15M, a single
  round-trip at the 40 bps fee floor costs 60,000 IDR. This is a research budget
  being spent to measure the fill half of the cost model (ADR-0016 §5), booked
  under `strategy_id = "calibration"`, and it should be read as such rather than
  as strategy performance.

## Rejected alternatives

**Trade the core book with an opening-balance baseline.** Rejected: an assertion
in place of a boundary, requiring operator upkeep after every manual trade, and
leaving B2's caps and B4's blast radius spanning 1.3B of positions the bot did
not open. The sub-account was available and costs nothing.

**Keep `POSITION_NOTIONAL_IDR = 30_000_000` and treat 15M as temporary.**
Rejected: it prices orders at twice the account's entire capital, so every
hurdle it produces is wrong by construction. If the sleeve is funded further
later, a measured value tracks that automatically; a constant does not.

**Wait for B4 to settle the trading-limit question before deciding.** Rejected:
the conservative reading (owned cash, no leverage) is safe to build against, and
the question only matters if we later want to exceed 15M. Blocking on it would
stall B1/B2 for a measurement that requires B4, which is the shape of cycle
ADR-0016 was written to cut.

## Open questions

- **Does `008488L` have a trading limit of its own?** Unresolved above;
  answerable only by an order attempt, so it is a B4 measurement.
- **How many concurrent names should the sleeve hold?** This sets per-name
  notional and therefore every impact number. It is a portfolio-construction
  decision (Track A, plan 13's successor), not an execution one.
- **Does the sleeve settle on the same T+2 clock?** Assumed yes; the
  `settlement_schedules` for `008488L` show T+0/T+1/T+2 tiers like the core book,
  but no trade has settled in it yet to confirm.
