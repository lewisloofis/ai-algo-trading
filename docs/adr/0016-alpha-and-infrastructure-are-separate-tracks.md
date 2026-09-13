# ADR 0016 — Alpha discovery and trading infrastructure are separate tracks; live routing is gated on size, not on edge

- **Status:** Accepted
- **Date:** 2026-09-13
- **Deciders:** Lewis
- **Tags:** sequencing, execution, calibration, risk, governance, cost-model
- **Supersedes:** [plan 17 §2](../plans/17-execution-and-replay-parity-engine.md) "The confirmed-edge gate" (conditions 1 and 3); [plan 00 §6](../plans/00-roadmap-research-to-live.md) wave ordering for Waves 3–5
- **Amends:** none — [ADR-0013](./0013-one-strategy-interface-and-collector-public-packages.md)'s one-Strategy-interface rule is unchanged and is what makes this split cheap

> Every strategy verdict this project has produced was scored against a
> **modelled** cost stack. The model's impact term is a hand-picked constant for
> most of the universe. Confirming or refuting that model requires realized
> fills; realized fills require live orders; and live orders were gated behind a
> confirmed edge. That is a cycle, and it is the mechanical reason the research
> loop has not terminated. This ADR cuts it by making the two concerns
> independently schedulable and re-gating live order routing on **bounded size
> and enforced controls** instead of on proven alpha.

## Context

### The cycle

```
  strategy verdict  ──requires──>  trustworthy cost model
  trustworthy cost model ──requires──>  realized-fill measurement
  realized-fill measurement ──requires──>  live orders
  live orders ──gated by plan 17 §2──>  confirmed edge
  confirmed edge ──requires──>  strategy verdict
```

> **Scope correction, 2026-09-13.** This cycle diagram is accurate for the
> *fill-experience* half of the cost model and overstated for the *impact* half,
> which is measurable offline from the captured tape. See the amendment under
> decision 5. The cycle still binds — Track B is still required — but it was
> never the only route to a usable impact coefficient, and treating it as such
> deferred work that could have run at any time.

Plan 17 §2 states the gate explicitly: *"No live order routing is built or
enabled by this plan… Any code that would route a real order to
`https://carina.stockbit.com` must be behind an explicit `--enable-live-routing`
flag that is absent from the binary unless this gate has been passed."* Its
condition 1 is edge confirmation; condition 3 is calibrated kill criteria, which
themselves need live PnL. The gate was written to prevent committing capital to
unproven alpha — a correct instinct — but it also forbids the measurement the
alpha verdict depends on.

### Evidence that the cycle is binding, not theoretical

- **The search never terminated because it never left one corner.** The plan-10
  coverage table holds **1,389 cells**: 1,034 `single_name_time_series` + 345
  `intraday_same_day` = **99.3%**, against 6 `event_driven`, 2 `pairs`, 2
  `cross_sectional`. A further 345 cells were skipped `capital_blocked` and 194
  on `MIN_EFF_N=30`. Three cells reached PASS; all three died on re-test against
  an always-long null.
- **Every one of those deaths was attributed to the hurdle, not the signal.**
  The 2026-09-12 breadth test (85,245 trades, 47 names) recorded the mean 2-day
  round-trip hurdle at **125.1 bps** and concluded *"the gap is an order of
  magnitude, not a tuning problem."*
- **The hurdle's impact term is measured for 7 names and guessed for the rest.**
  `research/strategy_fit/impact_coeffs.json` (refit 2026-09-05, 58 sessions)
  carries `high_liq: 0.377` at `eff_N=40,351` — genuinely measured — but
  `mid_liq: null` and `low_liq: null`, both falling back to
  `global_default: 1.0`, the hand-picked `SQRT_IMPACT_COEFF`. Every NO-GO on a
  name outside the 47-name capture was decided by that constant.
- **Plan 15 cannot close the remaining gap on its own.** Captured book data
  yields a *modelled* impact coefficient from participation and volatility. It
  cannot observe queue position, fill rate, reject rate, partial-fill behaviour,
  or the spread actually crossed — those exist only once an order has been
  worked. Plan 15 got `high_liq` and stalled, because the mid- and low-liquidity
  names are not in the capture.

### What is not in doubt

The infrastructure is needed **regardless of which alpha eventually wins**.
Position reconciliation, a pre-trade risk layer, an order state machine, and
realized-cost measurement are required by every strategy this project could
plausibly run. None of that work is speculative, and none of it is cheaper to
do later. Sequencing it behind an unresolved research question buys nothing.

## Decision

1. **Two tracks, independently sequenced.** *Track A — alpha research*
   (`research/`, Python) and *Track B — trading infrastructure* (`strategy/`,
   Go, plus ops). Neither blocks the other. Roadmap Waves 3–5 are re-cut along
   this split by [plan 18](../plans/18-alpha-and-infrastructure-split.md).

2. **The seam is the target portfolio, not the signal.** Track A emits a
   `target_portfolio.json` — `{as_of, strategy_id, targets:[{symbol, weight}],
   constraints}`. Track B diffs it against actual holdings and derives orders.
   Track A never models an order; Track B never models a signal. The contract is
   specified in plan 18 §3.

3. **A null alpha is a legitimate — and the preferred — Track B input.** An
   equal-weight basket of liquid names, rebalanced on a schedule, exercises the
   entire path end-to-end while carrying no alpha claim. Track B is developed and
   validated against it.

4. **Live order routing is gated on size and controls, not on edge.** Plan 17
   §2's conditions 1 (edge confirmation) and 3 (calibrated kill criteria) are
   void as preconditions for the router's *existence*. They are replaced by:

   | # | Condition | Where |
   |---|---|---|
   | G1 | Position & PnL reconciliation passes against the live account | plan 18 B1 |
   | G2 | Risk layer enforced in code: daily loss limit, gross-exposure cap, kill switch | plan 18 B2 |
   | G3 | Per-order **and** per-day notional caps set at de-minimis | plan 18 B2 |
   | G4 | The existing double-arm — `ALGO_LIVE=true` **and** the verbatim `ALGO_CONFIRM` phrase | `strategy/config.go` |

   Plan 17 §2 condition 2 (paper forward test) is **retained**, re-scoped: it
   gates an increase in *size*, not the existence of the router.

5. **Track B's definition of done is a measurement, not a feature list.**
   Track B is complete when `impact_coeffs.json` carries realized-fill
   coefficients in all three liquidity buckets at `eff_N ≥ MIN_IMPACT_OBS` that
   **reconcile with the tape-derived coefficients** produced offline, replacing
   `global_default: 1.0`. Feature completeness is not an exit criterion and must
   not be treated as one.

   > **Amended 2026-09-13, same day, before any work began under this ADR.** As
   > first written, this criterion read *"carries measured `mid_liq` and
   > `low_liq` coefficients … sourced from realized fills"*. That was wrong about
   > which measurement requires live orders. Price impact is the response of
   > price to aggressive flow, and the captured tape records other participants'
   > aggressive flow in full — so `mid_liq` and `low_liq` can be, and should be,
   > filled **offline**. `research/strategy_fit/fill_calibration.py` already does
   > this and has run twice; the buckets were empty because of two defects, not
   > because the measurement needed live orders. That offline work is now
   > **Track C** ([plan 18 §4](../plans/18-alpha-and-infrastructure-split.md)) and
   > runs ahead of both tracks. What genuinely requires live orders is *fill
   > experience* — queue position, fill rate, adverse selection, rejects — so
   > Track B's exit criterion becomes a cross-check between two independent
   > methods, which is a stronger condition than the one it replaces. Decisions
   > 1–4 and 6 are unaffected.

6. **Calibration spend is a research budget, not trading PnL.** Orders sent to
   measure execution are recorded in the post-trade store under a reserved
   `strategy_id` (`calibration`), accounted separately, and never reported as
   strategy performance. Their expected alpha is zero by construction; their
   purpose is the cost observation.

## Rejected alternatives

**Keep the edge gate; calibrate from captured book only (plan 15 as-is).**
Rejected. Captured book yields modelled impact from participation, not realized
slippage. The quantities that decide whether a 10–20 bps signal is tradeable —
queue position, fill rate, reject rate, spread actually crossed — are
unobservable without an order in the book. Plan 15 remains necessary; it is not
sufficient, and its own coverage stalled at the 7 captured names.

**Build infrastructure only after an edge appears.** Rejected empirically. That
was the standing policy and it produced 1,389 cells, three PASSes, and zero
survivors of a null gate, while the cost model that killed them stayed
uncalibrated for most of the universe. Restating the policy would restate the
cycle.

**Paper trading only; never route live.** Rejected. A paper fill is a model of a
fill, and the model is precisely what is in question. Using `PaperSink` to
validate `PaperSink`'s assumptions is circular in the same way the edge gate is.

**Calibrate by trading the best available candidate (the monthly momentum tilt)
rather than a null portfolio.** Rejected as the *primary* instrument, because it
conflates two questions: if the basket loses money you cannot separate absent
alpha from bad execution. A null portfolio's expected alpha is zero, so all
deviation from arrival price is attributable to execution. The momentum tilt may
be run later as a second instrument once Track B is measuring cleanly.

**Split at the signal rather than at the target portfolio.** Rejected. A signal
seam forces Track B to own sizing, netting and constraint logic that belongs
with the research that produced it, and it makes the null-alpha input awkward to
express. The target-portfolio seam is the standard portfolio-construction /
execution boundary and keeps both sides independently testable.

## Consequences

**Positive**

- The cycle is cut. Track A's verdicts become conclusive once Track B supplies
  measured costs, instead of resting on `SQRT_IMPACT_COEFF = 1.0`.
- Track B work is on the critical path for every possible alpha outcome, so none
  of it is wasted if the current candidate fails.
- [Plan 11](../plans/11-decay-monitoring-and-live-revalidation.md) (decay
  monitoring) unblocks far earlier: it is hard-blocked on "live trading +
  post-trade store", and B1/B6 deliver real rows without waiting on an edge.
- Plan 17's §6 deferred design sketches (6.1–6.6) become tickable work.

**Negative / costs**

- **Real money is at risk before any edge is proven.** This is the substance of
  the decision and it is accepted deliberately. It is bounded by G3's de-minimis
  caps, G2's enforced limits, and G4's double-arm, and it is booked as a research
  expense under §6 — not as a bet on alpha.
- **Infrastructure-first has its own runaway failure mode:** gold-plating an
  execution stack for a strategy that never arrives. §5's measurement-based exit
  criterion is the guard, and it is the reason the exit criterion is stated as a
  number in a JSON file rather than as a checklist.
- **A null portfolio will not surface every requirement.** Short selling,
  intraday timing, and any strategy-specific order type stay unbuilt until a
  Track A strategy needs them. Accepted: they are additive, not structural.
- **This ADR does not touch Track A's own blocker.** ADR-0015 recorded that the
  point-in-time universe cannot be reconstructed from the emitten endpoints, so
  any long-horizon backtest on the current store measures survivorship. That gap
  is Track A's, it is time-sensitive (it can only be recorded forward), and
  plan 18 §5 schedules it — but nothing here resolves it.
- Plan 13's duty-cycle allocator was scoped entirely around the leveraged
  sleeve's tenor clock; combined with ADR-0015 it now needs re-derivation for
  owned cash. Tracked in plan 18 §6.

## Related

- [Plan 18 — alpha / infrastructure split](../plans/18-alpha-and-infrastructure-split.md)
  — the plan this ADR authorises: the track charters, the `target_portfolio.json`
  contract, the B1–B7 build order, and the reconciliation of every prior plan.
- [Plan 17 — execution & replay-parity engine](../plans/17-execution-and-replay-parity-engine.md)
  — §2 conditions 1 and 3 superseded here; §6.1–6.6 promoted from design sketch
  to tickable Track B work.
- [Plan 15 — measured fill & impact calibration](../plans/15-measured-fill-and-impact-calibration.md)
  — the capture-based half of cost calibration; retained, and now explicitly
  insufficient on its own.
- [ADR-0013 — one Strategy interface for live and replay](./0013-one-strategy-interface-and-collector-public-packages.md)
  — unchanged, and the reason Track B can swap `ReplaySource`/`PaperSink` for
  `LiveSource`/`LiveSink` without a second engine.
- [ADR-0015 — leverage is optional](./0015-leverage-optional-horizon-ceiling-lifted.md)
  — opened the long-horizon band for Track A and recorded the point-in-time
  universe gap that still blocks it.
- [ADR-0011 — capital model](./0011-capital-model-300m-leverage-sleeve.md) —
  superseded by ADR-0015; its `LEVERAGED_SLEEVE` parameters survive as a preset.
- `research/strategy_fit/impact_coeffs.json` — the file whose `mid_liq` /
  `low_liq` nulls are Track B's exit criterion.
