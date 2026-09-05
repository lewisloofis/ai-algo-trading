# Capital Allocation & Capacity — Sizing the 300M Leverage Sleeve as a Periodic Batch Allocator

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-20
> **Related:** [ADR-0011: capital model](../adr/0011-capital-model-300m-leverage-sleeve.md) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [09-universe-screener.md](./09-universe-screener.md) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (§2–3) · [Plans index](./README.md)

> **What this is.** The layer that decides *how much to deploy and when*, given
> the 300M leverage facility's hard mechanics. Plan 10 decides which `(name,
> strategy)` cells have edge after the full cost stack (fees + financing); this
> plan operationalises ADR-0011's capital model into the allocation and capacity
> layer — basket construction, per-name capacity caps, duty-cycle scheduling, and
> drawdown guardrails — so that the sleeve can be run as the periodic batch
> allocator it structurally is. Most of this plan is **design-now / run-after-
> live**: the allocation logic can be specified and tested off-line, but the real
> loop requires live execution (pillar 2) and a post-trade store (pillar 4.2).

## 1. The reframe — why this is a separate plan from plan 10

Plan 10 answers the *strategy* question: given this name and this strategy
family, does the expected per-trade edge survive the full cost stack? It emits
a `strategy_portfolio.md` — a set of `(name, strategy, horizon)` survivors
annotated with decay metrics and friction floors.

This plan answers the *capital* question: given those survivors and a 300M
one-shot budget, which basket do we deploy, at what size, on which day, and
how do we bound the downside? The two questions are cleanly separable — plan 10
knows nothing about position sizing; this plan knows nothing about whether a
signal has edge. Folding them together would force plan 10 to carry facility
mechanics that belong here, and would force this plan to re-run backtests it
should simply consume.

> **Status note.** The core design is buildable now and does not depend on live
> trading — basket optimisation, C* calculations, and duty-cycle scheduling can
> all be run and unit-tested against plan 10's output. The live allocation loop
> (§7 steps 7.5–7.7) is blocked on live execution (pillar 2) and the post-trade
> store (pillar 4.2), consistent with plan 11's blocked status.

## 2. Locked decisions (inherited from ADR-0011)

All mechanics below are pinned by ADR-0011. This plan does not re-derive them;
it builds against them.

- **300M IDR leverage facility only.** Drawn against the 1.1B IDR collateral
  book. No idle cash; any strategy un-fundable by this facility is un-fundable,
  independent of signal quality (ADR-0011 §1).
- **T+2 sell-safe / T+3 warning / T+4 core-book forced liquidation.** The T+4
  event is not a fee — it is a broker-priced forced unwind of the 1.1B core book.
  A hard exit-by-T+2 rule applies to every position in the basket (ADR-0011 §2).
- **Re-borrow does not reset the clock.** To reset, the facility must sit flat
  for approximately 2 trading days. This produces a ~4–5 trading-day duty cycle:
  ~2 days deployed, ~2 days flat — a single running clock per cycle (ADR-0011 §2).
- **No staggering, no top-ups.** Drawing any part of the 300M locks the entire
  facility until repay or reset. There is exactly one borrow event per cycle,
  sized once at entry; the portfolio is a **single synchronised basket entered
  atomically and exited within tenor** (ADR-0011 §3). No averaging down.
- **Financing: 10 bps/calendar-day** on the borrowed amount. The full pass test
  from plan 10 applies: `net_edge_per_trade > 0` after `min_edge_to_clear_bps +
  modelled_impact + financing_bps`, where `financing_bps = 10 ×
  calendar_days_outstanding(entry_dow, hold)` (ADR-0011 §4; plan 10 §2–3).
  Calendar days outstanding is the gap between the two legs' T+2 **settlement**
  dates, so with the entry day chosen freely it is 10 bps per trading day held
  ([ADR-0014](../adr/0014-financing-accrues-between-settlement-dates.md)).
- **~15M IDR/month salary** is the only owned-cash drip. At full deployment,
  financing consumes ~0.6–1.2M/cycle, ~4 cycles/month ≈ 2.4–4.8M/month — a
  meaningful fraction of the salary inflow. Owned cash carries no T+4 clock.

## 3. The duty-cycle throughput model

Because the facility must sit flat for ~2 trading days between borrows, average
utilisation is bounded at roughly **40–50% of calendar time**. The annualised
return of the sleeve is therefore:

```
annualised_return ≈ per_cycle_net_edge × cycles_per_year
```

where `cycles_per_year` is bounded by the ~4–5 trading-day round (≈ 50 cycles/
year at the high end). This ceiling is **a hard facility constraint independent
of signal quality**: a strategy with twice the per-cycle edge does not double
the utilisation rate — it only doubles the per-cycle multiplier. The lever to
raise utilisation above ~50% is owned cash (no T+4 clock), not more leverage.

This framing also sizes the financing nut. Every cycle must clear
`financing_bps × deployed_capital` before it is net positive. At 10 bps/cal-day
and a 2-trading-day hold whose settlement window avoids the weekend, the
financing term is **20 bps** on top of the 40 bps round-trip fee gate — roughly
60 bps total hurdle before the cycle is in profit. A Tue or Wed entry on that
same 2-day hold pays 40 bps instead, which is why the entry day is a scheduling
decision and not a detail (ADR-0014).

## 4. Per-cycle basket construction & sizing

Each deployment cycle, the allocator:

1. **Consumes plan 10's survivors** — the `strategy_portfolio.md` set of grid-
   corrected, walk-forward-persistent `(name, strategy, horizon)` cells annotated
   with expected net edge and friction floor.
2. **Screens on the day-of-week financing cost** — recalculates the full pass
   test (`min_edge_to_clear_bps + modelled_impact + financing_bps`) for the
   current entry day-of-week and intended hold. Cells that cleared the static
   gate in plan 10 may fail the dynamic day-of-week recheck on the one weekday
   whose settlement window straddles the weekend: **Wednesday** for a 1-day hold,
   **Tuesday or Wednesday** for a 2-day hold. Every other weekday is equally
   cheap — there is no early-week preference (plan 10 §3, ADR-0014). Because the
   allocator picks the entry day, this screen should normally be a scheduling
   input ("do not open a 1-day basket on Wednesday") rather than a cell filter.
3. **Caps each name at C\*** — see §5.
4. **Builds a single basket** that fits the 300M one-shot budget subject to per-
   name C* caps. The basket is entered atomically; no second tranche, no add-to-
   winner, no average-down with leverage (ADR-0011 §3).
5. **Sizes once at entry.** Position sizes are fixed at entry and not adjusted
   intra-cycle. Any partial exit before T+2 for risk reasons closes the position;
   it does not free the facility for a new tranche.

## 5. Capacity / C*

For each name in the basket, C* is the maximum notional that can be **fully
exited within T+2 at acceptable market impact**. Forced liquidation of the core
book is a tail risk, not a fee — so exit liquidity within tenor is a hard
constraint, not a soft cost term.

C* is derived from the square-root impact model established in plan 09 §3:

```
C*(name) = max notional such that expected_impact(C*, ADV, vol) ≤ impact_budget_bps
```

where `impact_budget_bps` is set conservatively to keep expected slippage well
below the net edge of the weakest surviving cell for that name. Practically: C*
is a per-name cap that bounds basket weights — over-weighting a name past its
C* risks a forced exit that realises worse than the backtest cost model assumed.

C* bounds are computed from the plan 09 annotations (`ADV`, `realised_vol`)
already present on every universe name; no new data sources are required.

## 6. Drawdown bounded by core-book maintenance margin

The sleeve's worst-case loss must never threaten the maintenance margin on the
1.1B core book — the event that triggers T+4 forced liquidation is a broker-
level margin call on the collateral book, not a sleeve-level stop.

This translates to a drawdown limit on the basket: the maximum tolerable basket
loss (in IDR) is set such that, in combination with any concurrent mark-to-market
decline in the core book, the combined loss stays below the maintenance margin
buffer. The exact parameter is a function of the core book's current margin
headroom and is updated at the start of each cycle; it is not a fixed constant.

This is a risk backstop that sizing must respect — positions that individually
clear the C* cap may need to be further reduced if the aggregate basket loss
limit would otherwise be breached.

## 7. Implementation

Output convention: `research/out/cycle_allocation.md` — the allocation artifact
for the current cycle, listing basket constituents, weights, entry-dow financing
recalculation, C* caps applied, and the drawdown limit in force. Matches the
`*.md` output pattern from plans 04–06 and plan 09.

- [ ] **7.1** Consume plan 10's `strategy_portfolio.md` survivors — the input
  contract. Read annotated `(name, strategy, horizon, net_edge, friction_floor)`
  rows; fail fast if the file is absent or malformed.
- [ ] **7.2** Per-name C* calculator — compute `C*(name)` from plan 09 ADV and
  realised vol annotations using the square-root impact law (plan 09 §3). Emit a
  per-name C* table as part of `cycle_allocation.md`. Unit-test against the three
  reference names from plan 09 (ITMG-class, BUMI-class, DEWA-class).
- [ ] **7.3** Day-of-week financing recheck — recalculate `financing_bps` for the
  current entry day and intended hold; filter out survivors whose full pass test
  fails the dynamic recheck. Log the drop with the reason (day-of-week + hold
  combination).
- [ ] **7.4** Basket optimiser — select and weight a basket from the day-of-week-
  screened survivors subject to: (a) total notional ≤ 300M, (b) per-name weight ≤
  C*(name), (c) aggregate basket loss ≤ drawdown limit (§6). Start with a simple
  equal-weight-up-to-C* heuristic; a proper mean-variance or Kelly-fraction
  optimiser is a future refinement.
- [ ] **7.5** (Deferred — needs live execution, pillar 2) Live cycle scheduler —
  gates on market open, checks current facility state (flat or drawn), decides
  whether to enter this cycle based on day-of-week and net edge after financing.
  Emits an entry signal to the order router.
- [ ] **7.6** (Deferred — needs pillar 2 + pillar 4.2 post-trade store) Intra-cycle
  exit monitor — tracks mark-to-market vs the T+2 deadline; fires hard exits
  before T+2 if the drawdown limit is approached. Logs all exits to the post-trade
  store.
- [ ] **7.7** (Deferred — needs 7.6) Cycle close and reset — confirms all positions
  are flat after exit; resets the facility state; waits the ~2-day flat period
  before allowing the next cycle to begin.

## 8. Verification

- **C* consistency** — C* for each name reproduces the plan 09 §3 impact model;
  no name is over-allocated past its C* cap in the emitted basket.
- **Financing recheck fires correctly** — a **Wednesday** entry with a
  1-trading-day hold (3 calendar days: Fri settle → Mon settle, +Sat +Sun;
  ADR-0014) raises the hurdle by 30 bps and correctly rejects any survivor whose
  net edge is 30 bps or less, while the same cell entered Mon/Tue/Thu/Fri pays
  only 10 bps and passes. A test that still expects a Thursday penalty is
  asserting the superseded ADR-0011 arithmetic.
- **Basket fits the facility** — total basket notional ≤ 300M in all test cases;
  no second tranche is ever emitted by the optimiser.
- **Drawdown limit applied** — a synthetic basket whose worst-case loss exceeds
  the margin guardrail is correctly clipped or rejected by the optimiser, not
  silently passed.
- **Plan 10 round-trip** — if plan 10's `strategy_portfolio.md` contains zero
  survivors, the allocator emits an empty basket and logs a clear "no deployable
  cells this cycle" message; it does not error or deploy a partial basket.

## 9. Out of scope

- Universe membership — that is plan 09's job; this plan only sizes within the
  surviving `(name, strategy)` cells plan 10 hands it.
- Strategy edge validation and walk-forward backtesting — plan 10's job; this
  plan consumes the output, not the methodology.
- Live execution and order routing — downstream pillar 2; this plan emits an
  allocation artifact, not live orders.
- Alpha decay monitoring and the global kill-switch — plan 11; that plan's decay
  metric definition is shared with plan 10, not re-derived here.
- Re-deriving the capital model mechanics — they are pinned by ADR-0011; any
  change to facility terms (300M limit, 10 bps/cal-day, T+2/T+4 clock) requires
  an ADR update, not an edit here.

---

Prev: [10-strategy-fit-and-portfolio.md ←](./10-strategy-fit-and-portfolio.md) ·
Index: [README](./README.md)
