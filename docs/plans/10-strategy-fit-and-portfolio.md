# Strategy Fit & Portfolio Backtester — Data-Chosen Strategies per Name

> **Status:** ✔️ Done · **Owner:** @lewis · **Updated:** 2026-06-29
> **Related:** [09-universe-screener.md](./09-universe-screener.md) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) (Workflow B) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [05-ofi-trade-flow-study.md](./05-ofi-trade-flow-study.md) · [04-microstructure-study.md](./04-microstructure-study.md) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (§1.2) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) · [ADR-0009: universe = funding intersection](../adr/0009-universe-is-funding-intersection-strategy-fit-downstream.md) · [ADR-0011: capital model](../adr/0011-capital-model-300m-leverage-sleeve.md) · [Plans index](./README.md)

> **What this is.** The layer that decides *which strategy fits which name*, from
> data — not from a strategy assumption baked into the universe. Plan 09 hands us
> a clean, signal-agnostic universe (funding-eligible ∩ all-funding-options-
> unlocked, annotated with structural friction). This plan runs a **full catalog
> of strategy families across horizons and shapes** against every name in that
> universe, nets each against *that name's* friction floor, and keeps the
> `(name, strategy)` pairs that survive a multiple-testing-honest backtest. The
> portfolio is the union of survivors. The objective is the **combination of
> profitable strategies** across every testable (horizon × shape) cell — whatever
> the data says clears the cost gate. Families that cannot be validated on current
> data are tagged `data_blocked` and tracked explicitly; nothing is silently
> excluded.

## 1. The reframe — why this is a separate plan from the universe

Plan 09 originally folded an **edge-cap drop** (one-tick spread ≥ 40 bps → reject)
into the universe definition. That was a smuggled strategy assumption: the cap
only rejects a name *if you have committed to thin-edge intraday liquidity-taking*.
For that strategy class the cap is correct (plans 04/05/06 proved the tick-scale
OBI/OFI edge does **not** clear 40 bps on cheap, high-friction names — plan 06 is
a NO-GO). But "edge-capped for an intraday taker" is not "untradeable". A name
whose realistic round-trip friction is ~100 bps is perfectly tradeable by a
strategy whose *per-trade* edge is reliably > 100 bps net (swing, momentum,
event-driven) — it simply trades less often.

So friction belongs to the **(name, strategy)** pair, not to the name alone.
Plan 09 now leaves edge-cap and thinness as **annotations** (`min_edge_to_clear_bps`,
`is_edge_capped`, `is_thin`) and drops nothing for strategy-specific reasons.
**This plan is where a strategy gets assigned — by backtest, per name.**

## 2. Locked decisions

**Universe = plan 09 output, unchanged.** Membership is the funding intersection
(day-trade ∩ trading-limit). This plan consumes `universe_screen.md` / the ranked
list; it never re-filters membership.

**Friction is per-name and fixed; edge is per-strategy.** The pass test for any
`(name, strategy)` cell is `net_edge_per_trade > 0` after that name's real cost
stack, where the structural floor is `min_edge_to_clear_bps` (one-tick spread +
40 bps round-trip fee) plus modelled impact/slippage. The 40 bps gate
(`research/backtest/backtest.py` FEE) stays the single source of truth.

**Financing is part of the cost stack (per ADR-0011).** The cost stack carries
a financing term: `financing_bps = 10 × calendar_days_outstanding(entry_dow,
hold)` — 10 bps per calendar day (including weekends and holidays) that the
borrow is outstanding under T+2 settlement. The full pass test is therefore
`net_edge_per_trade > 0` after `min_edge_to_clear_bps + modelled_impact +
financing_bps`. The 40 bps round-trip FEE in `research/backtest/backtest.py`
remains the single source of truth for round-trip fees; financing is added on
top of it, not a second copy of it. Because financing is a function of hold
length and entry day-of-week, the gate is **day-of-week-dependent**: a hold
whose settlement window straddles a weekend is punishing, and per
[ADR-0014](../adr/0014-financing-accrues-between-settlement-dates.md) that is a
**Wednesday** entry on a 1-day hold — not an early-week/late-week split.

**Horizon-spanning and shape-spanning, data-chosen.** We do not pre-pick a
horizon or a strategy shape. The panel spans from intraday (seconds–minutes,
order-book) through multi-week / multi-month / position horizons. The data
assigns the horizon by which cell survives. **Horizons beyond what ~1 year of
daily bars can validate are `data_blocked`, not excluded** — they become tracked
items that unblock as multi-year history accrues (see §4 taxonomy).

**Multiple-testing discipline is mandatory.** Running a panel × universe × shape
is a large grid — exactly the false-discovery trap plan 08 Workflow B exists to
prevent. Survivors must clear de-overlapped effective-N (≥ MIN_EFF_N=30),
block-bootstrap nulls, and a deflated-Sharpe / FDR correction across the whole
grid before they count.

**The MIN_EFF_N floor is applied on each shape's natural observation unit —
not naively 30-per-name.** For event-driven families: pool observations across
all universe names and require ≥ 30 non-overlapping pooled events. For
cross-sectional families: the unit is a cross-sectional period (one day across
all names), so require ≥ 30 independent cross-sectional periods. For pairs: ≥ 30
non-overlapping residual-spread observations at the pair level. For single-name
time-series: the existing per-symbol de-overlap and 30-per-symbol floor from
plans 04/05. This preserves the rigour that caught the ANTM false-positive in
plan 05 while not structurally killing low-frequency or cross-name families via
an inappropriate unit.

**Walk-forward re-validation is mandatory — not just a single chronological OOS split.**
The grid backtest must use **rolling walk-forward windows**: fit on a trailing
window, test on the next forward window, roll forward, repeat. This is the
research-time persistence test — it asks whether the edge repeats across multiple
past windows, not merely whether it existed once. A single 60/40 chronological
split can only test that the edge existed at some point; a walk-forward sweep
tests whether it *persisted*. Feasible now for the daily-bar families because
chartbit supplies roughly one year of OHLCV per name (§5), giving enough history
for several roll iterations.

Each cell's walk-forward output defines a **decay metric**: a rolling Sharpe (or
rolling hit-rate) computed on each forward test window. If this metric falls
below a threshold across a majority of forward windows, the `(name, strategy)`
cell is marked **decayed / non-persistent** and does not enter the portfolio,
even if the full-period average looks acceptable. This decay-metric definition is
shared verbatim with the live decay monitor ([plan 11](./11-decay-monitoring-and-live-revalidation.md)):
the same rolling-Sharpe / hit-rate threshold used here to gate entry is the same
criterion plan 11 uses to pull a live position. Backtest and live kill-switch
speak the same language by construction.

## 3. The per-name friction floor

From plan 09's annotations, each name carries:

```
min_edge_to_clear_bps = min_spread_bps(price) + ROUND_TRIP_BPS
                      = tick_size(price)/price · 10_000  +  40
```

This is the *minimum* a strategy's net per-trade edge must beat on that name
(best case: passive fills, no adverse selection). Reference values:

| Name | Price | one-tick spread | min_edge_to_clear | Survivable by |
|---|---|---|---|---|
| ITMG | 27,800 | ~9 bps | ~49 bps | almost any horizon |
| BUMI | 168 | ~59.5 bps | ~100 bps | swing / momentum (not intraday taker) |
| DEWA | 53 | ~189 bps | ~229 bps | event-driven large moves only |

The floor is computable from price alone (no look-ahead). Impact/slippage on top
is modelled per strategy from ADV and realised vol (plan 09 §3 square-root law).

### Financing as a per-(hold, entry-dow) cost-stack addition

Financing is distinct from the per-name floor above: `min_edge_to_clear_bps`
is fixed for a given name and price level; financing varies by hold length and
entry day-of-week. The two are additive — both must be beaten for the full pass
test to clear.

**Calendar days outstanding is the gap between the two legs' settlement dates**
([ADR-0014](../adr/0014-financing-accrues-between-settlement-dates.md)). The
borrowed cash is out from the moment the *buy* settles until the *sell* settles,
and both legs settle T+2, so the shift cancels:

```
calendar_days_outstanding = settle(entry + hold) − settle(entry),   settle(d) = d + 2 trading days
```

The consequence is that the entry weekday only matters through the weekday its
*settlement* lands on. A Wednesday buy settles Friday and its next-day exit
settles Monday, so the weekend falls inside the charged window; every other
weekday's 1-day hold is charged a single day.

| Entry → exit | buy settles | sell settles | Calendar days | financing\_bps |
|---|---|---|---|---|
| Same-day round-trip | — | same date | 0 | 0 |
| Mon→Tue | Wed | Thu | 1 | 10 |
| Thu→Fri | Mon | Tue | 1 | 10 |
| **Wed→Thu** | **Fri** | **Mon** | **3** (+Sat, +Sun) | **30** |
| Mon→Wed (2-day hold) | Wed | Fri | 2 | 20 |
| **Tue→Thu** (2-day hold) | **Thu** | **Mon** | **4** | **40** |

An IDX holiday inside the charged window costs like a weekend day; one that
shifts both settlements equally costs nothing.

This is a real scheduling optimisation lever for the allocator (plan 13), but it
is a *one-day-to-avoid* lever, not an early-week preference: skip the Wednesday
entry on a 1-day hold, skip Tue/Wed on a 2-day hold. With the entry day chosen
freely the best-feasible financing is exactly **10 bps per trading day held**
within ADR-0011's ≤2-day ceiling. Same-day round-trips avoid financing entirely
and reopen the larger-move intraday band (see §4 below).

## 4. The strategy panel — full catalog, status-tagged

A full catalog spanning both axes. Each family is a hypothesis to be *refuted*,
not assumed. Families that cannot be validated on current data are tagged
`data_blocked` with an explicit unblock condition; nothing is silently excluded.

**Shape axis — four test shapes (consistent with plan 08 §4a):**

| Shape | Natural observation unit | Test primitives |
|---|---|---|
| `single_name_time_series` | per-symbol (t, return) rows | decile curve, HAC t-stat, conditional hit-rate |
| `cross_sectional` | one cross-sectional period = one day across all names | Spearman IC, IC t-stat, ICIR |
| `event_driven` | pooled events across all universe names | pooled event-return, block-bootstrap on pooled obs |
| `pairs` | a two-instrument relationship | ADF / Johansen cointegration, spread half-life, spread-reversion OOS |

**Strategy families — horizon × shape, with status:**

| Family | Shape | Horizon | Status | Reason / unblock |
|---|---|---|---|---|
| Order-flow imbalance (OBI/OFI) | `single_name_time_series` | seconds–minutes | `testable_now` | captured order-book data exists; NO-GO on cheap names (plan 06) — re-test low-friction only |
| Intraday momentum / breakout | `single_name_time_series` | minutes–hours | `testable_now` | intraday bars derivable from tick data |
| Daily momentum / swing | `single_name_time_series` | days | `testable_now` | ~1yr OHLCV via chartbit; **primary new bet** |
| Mean-reversion (overextension) | `single_name_time_series` | days | `testable_now` | same daily-bar source |
| Event-driven (gap / volume spike / news) | `event_driven` | days | `testable_now` | MIN_EFF_N=30 on pooled cross-universe events (not per-name); only viable path for very-wide-tick names |
| Cross-sectional momentum / factor | `cross_sectional` | days | `testable_now` | daily cross-sectional rank; MIN_EFF_N=30 on cross-sectional periods |
| Pairs / stat-arb / cointegration | `pairs` | days–weeks | `testable_now` | same daily-bar window; spread-reversion half-life via cointegration test |
| Multi-week trend / position hold | `single_name_time_series` | weeks | `data_blocked` | de-overlap on 5-day hold gives ~50 independent windows per year — barely feasible; unblocks clearly at 2+ years of history |
| Multi-month / macro momentum | `single_name_time_series` | months | `data_blocked` | a 20-day hold gives ~12 independent windows per year per name, below `MIN_EFF_N=30`; unblocks at 3+ years of daily bars |
| Multi-year carry / structural | any | years | `data_blocked` | requires 5+ years of history; unblocks at that horizon |

> **Data-length constraint (not a preference).** The horizon ceiling today is a
> genuine data limit: chartbit supplies ~1 year of daily bars per name, not a
> design choice. At a 20-day hold there are ~12 non-overlapping windows per name
> per year — below the MIN_EFF_N=30 floor. This is not a permanent exclusion;
> it is a tracked `data_blocked` item. As multi-year history accrues, multi-week
> and multi-month families graduate to `testable_now` without any change to the
> methodology.

> **Capital horizon ceiling (ADR-0011).** The leverage facility's T+2-sell /
> T+4-liquidation tenor caps practical holds at **~2 trading days** — a *capital*
> ceiling that **coincides with** the data-length ceiling above. The weeks-plus
> families (`Multi-week trend`, `Multi-month / macro momentum`, `Multi-year carry`
> rows) are therefore both `data_blocked` **and** capital-blocked: they do not
> graduate to deployable on data length alone once multi-year history accrues —
> they also require owned cash (no T+4 clock) per ADR-0011. The deployable band
> under the current leverage facility is **same-day through ~2-day** swing,
> breakout, and event strategies; "days"-horizon families (daily momentum/swing,
> mean-reversion) should be read as **≤2 trading-day holds** under leverage.
> Same-day larger-move intraday is **structurally re-opened** by this framing:
> the plan-06 NO-GO killed tick-scale thin-edge (5–10 bps) intraday on cheap
> high-friction names, NOT larger-move same-day intraday on liquid names — and
> same-day round-trips avoid financing entirely (see §3 above).

**Daily-bar families and cross-sectional / pairs families** are the cheap first
pass: chartbit gives a full year of OHLCV + `value` + foreign-flow in one call
per name (plan 09 live endpoints). Event-driven and cross-sectional families pool
across the universe, so the effective-N check is on the pooled count. The
order-book family stays gated on captured data (plan 01) and earns a re-test only
on the low-friction names the intersection surfaces (e.g. ITMG-class).

## 5. Data sources & reuse

- **Daily OHLCV + foreign flow:** chartbit endpoint already wired in plan 09's
  `LiveDataSource.daily_history` (verified, 1-year window). Foreign-flow columns
  feed the event-driven family.
- **Order-book / trades:** `sbcap features` / `sbcap trades` CSVs (plans 04/05) —
  only for names in the capture pipeline.
- **Cost stack:** `research/backtest/backtest.py` FEE (40 bps) — single source.
- **Stats hygiene:** `research/lib/stats.py` bootstrap / decile / effective-N
  utilities (do not reimplement) and the de-overlap discipline from plans 04/05.
- **Orchestration:** plan 08 Workflow B is the natural executor — `pipeline`
  validate → cost gate → adversarial refutation, then a **barrier** multiple-
  testing correction across the full `(name, strategy, horizon)` grid.

## 6. Method — per `(name, strategy)` cell

1. For each name in the plan-09 universe, for each strategy family in the §4
   taxonomy with status `testable_now`, run the family's backtest using **rolling
   walk-forward windows** over the available history (daily first; order-book where
   captured). Fit on a trailing window, test on the next forward window, roll
   forward, repeat. Families tagged `data_blocked`, `cost_gated`, or `deferred` are
   recorded in the coverage table (§7.5) with their status; they are not silently
   skipped.
2. Compute the **decay metric** per forward window: rolling Sharpe and rolling
   hit-rate on the out-of-sample test window. A cell whose decay metric falls
   below the threshold across a majority of forward windows is marked
   **decayed / non-persistent** and removed from candidacy at this step.
3. Net surviving cells against that name's real cost stack (`min_edge_to_clear_bps`
   floor + modelled impact). A cell is a *candidate* only if mean net edge/trade
   > 0, with effective-N and bootstrap hygiene applied on **each shape's natural
   observation unit** (see §2 and plan 08 §4a): pooled events for `event_driven`,
   cross-sectional periods for `cross_sectional`, residual-spread obs for `pairs`,
   per-symbol rows for `single_name_time_series`. The de-overlap discipline from
   plans 04/05 is preserved in all cases; only the *what counts as one observation*
   changes by shape.
4. Barrier: apply deflated-Sharpe / FDR across the entire grid (plan 08 §B). Only
   grid-corrected survivors are kept.
5. Output: the surviving `(name, strategy, horizon)` pairs = the **strategy
   portfolio**, each annotated with its decay metric and the friction floor it beat.
   Expectation, given priors: low-friction names (ITMG-class) admit several
   families; cheap high-friction names (BUMI/DEWA) survive only under wide-edge
   daily/event strategies, if at all.

## 7. Implementation

- [x] **7.1** Consume plan 09's ranked universe (read `universe_screen.md` or call
  the screener as a library) — the input contract.
- [x] **7.2** `research/strategy_fit/` — strategy harness with a **pluggable shape
  interface**: each family declares its shape (`single_name_time_series`,
  `cross_sectional`, `event_driven`, or `pairs`) and the harness dispatches to the
  correct test primitive and effective-N unit (§2, §6). The three-state verdict
  mirrors plans 04/05/06 in all shapes.
- [x] **7.3** The single-name daily-bar families (momentum, mean-reversion, breakout)
  as pure signal functions over chartbit OHLCV + foreign flow —
  `shape=single_name_time_series`.
- [x] **7.4** Event-driven family (gap / volume spike / foreign-flow spike) with
  pooled cross-universe event returns and MIN_EFF_N=30 on pooled non-overlapping
  events — `shape=event_driven`. This is the primary (and often only) viable shape
  for very-wide-tick names.
- [x] **7.5** Cross-sectional momentum / factor: daily cross-sectional rank, IC
  series, ICIR — `shape=cross_sectional`. MIN_EFF_N=30 on cross-sectional periods.
- [x] **7.6** Pairs / cointegration: pairwise ADF / Johansen on the universe names,
  spread-reversion half-life, OOS block-bootstrap on residual-spread obs —
  `shape=pairs`.
- [x] **7.7** Walk-forward harness + decay metric — rolling fit→test→roll loop
  over available history (by shape: daily rows, cross-sectional periods, pooled
  events, spread obs); compute rolling Sharpe and hit-rate per forward window; mark
  cells decayed/non-persistent when the metric falls below threshold. Shared
  verbatim with plan 11.
- [x] **7.8** Grid runner + coverage table + multiple-testing correction (deflated
  Sharpe / FDR) across `(name, strategy, horizon, shape)`; emit
  `research/out/strategy_portfolio.md` annotated with each survivor's decay metric,
  friction floor, and shape. Coverage table lists every `data_blocked` /
  `cost_gated` / `deferred` cell with its status and unblock condition — no silent
  omissions.
- [ ] **7.9** (Deferred) re-test the order-book OBI/OFI family on the low-friction
  intersection names once captured (plan 01) — the only honest re-open of the
  plan-06 NO-GO.
- [ ] **7.10** (Deferred) wire as plan 08 Workflow B when grid size makes the
  fan-out + adversarial refutation worth it.

## 8. Verification

- **Cost-stack consistency** — every cell nets against the same 40 bps FEE and
  the plan-09 `min_edge_to_clear_bps`; no duplicated friction constant.
- **Reproduces known priors** — the order-book OBI family on BUMI reproduces the
  plan-06 NO-GO (sanity that the harness nets costs the same way).
- **Multiple-testing honesty** — a shuffled-label / random-strategy control
  produces ~zero grid-corrected survivors (the FDR layer works).
- **Portfolio is non-trivial and explainable** — surviving pairs map to the
  friction story (low-friction names admit more families; very-wide-tick names
  admit few or none), and `strategy_portfolio.md` states each survivor's net
  edge, effective-N, and the friction floor it beat.

## 9. Out of scope

- Re-filtering universe membership — that is plan 09's job; this plan only
  assigns strategies within it.
- Live execution / order routing — downstream pillars (roadmap §2–3).
- Per-cycle position sizing, exit-within-tenor capacity (C*), and duty-cycle
  scheduling — these are **[plan 13](./13-capital-allocation-and-capacity.md)**
  (capital allocation & capacity), per ADR-0011; out of scope here.
- Adding strategy families beyond the §4 full catalog speculatively — the catalog
  already spans all current data-supported shapes; new additions require a data
  reason and an ADR.
- Re-opening the order-book NO-GO on the cheap high-friction names — the data
  already answered that; re-test only on low-friction names, and only once
  captured.
- Validating `data_blocked` families before their unblock condition is met — they
  are tracked, not attempted early.

---

## ⛔ VERDICT SUPERSEDED 2026-09-05 — the PASS does not survive correction

*Everything in this plan describing surviving cells, an impact-robust ORB core, or a
provisional PASS is superseded. The full reasoning, tables and method live in
[roadmap §6 "Wave 2 exit criterion MET"](./00-roadmap-research-to-live.md); this is the
short form so nobody reads the sections below without it.*

Three independent defects each inflated the reported result, and they compound:

1. **The report scored trades a retail account cannot make.**
   `research/out_live/strategy_portfolio.md` has mtime **2026-06-27 22:37**;
   `c62eba8` "constrain to long-only; remove short leg" landed **2026-06-29 12:12** and
   the report was never regenerated. IDX retail cannot short. Signature: it shows ~54 ORB
   trades for PTRO in ~55 days (~98 % of days firing) where the corrected long-only code
   finds 27. That fix had been applied to **ORB only** — every other family in the
   library still scored `sign(signal) × forward_return` until 2026-09-05.
2. **The gates could not change the verdict.** 9 of 11 surviving cells carried
   `OOS Eff-N = 0` and the other 2 an impact of `0.0` that had never actually been
   priced. Both are now blocking (roadmap W2.1), and the verdict becomes
   **INCONCLUSIVE with an empty survivor table**.
3. **The evaluation was in-sample.** This plan's live run was **2026-06-20** against
   chartbit data spanning 2026-03-25 → 06-19; forward capture of exactly those seven
   survivors began **2026-06-22**. Splitting at that date, the ORB edge is
   ENRG +94.2 → −0.8, TPIA +54.0 → −0.7, BRPT +38.0 → +0.7 bps, and PTRO was negative
   in both. The edge exists only in the window the names were chosen on.

**Cross-substrate check (the [plan 17](./17-execution-and-replay-parity-engine.md) §7.8
contradiction, resolved).** Restricted to the same 53 sessions, the tick-tagged bars and
the chartbit bars select the **same breakout days** — trade counts match exactly on 5 of
7 names — and 5 of 7 are negative gross on both. There was never a substrate
disagreement; §7.8's replay was simply measuring the out-of-sample period while this
plan measured the in-sample one. **The replay was right.**

**Also, independently: ORB cannot be tested on the captured tick window at all.** It
fires on ~45–50 % of days, so 53 sessions yields 20–28 trades per name — every one below
`MIN_EFF_N = 30`.

**Recommendation: kill the intraday ORB / intraday-momentum family**, as plan 06 was
killed. Not because the machinery failed — it worked, and is what produced this answer —
but because the answer is *no edge*. Do not re-tune thresholds to keep cells alive. The
§8 red flag is now closed, in the negative.

**One defect ran the other way (2026-09-05).** The financing clock overcharged every
hold ≥ 1 cell by 10 bps — it counted from the trade date rather than between the two
legs' T+2 settlement dates
([ADR-0014](../adr/0014-financing-accrues-between-settlement-dates.md); §3 above is
corrected). Re-running the grid on the corrected clock over the identical universe and
the identical 1,390 coverage rows moves **815** cells to cost-gated (from 819), **21**
through the cost gate (from 17), and **17** FDR-accepted cells blocked solely on
`impact_not_priced` (from 13). It changes no verdict: the four newly-passing cells all
clear by less than the 10 bps correction itself, so any non-zero daily impact charge
re-kills them, and the intraday band is hold = 0 where financing was always zero. The
kill above is unaffected.

## Event-driven re-validation status (plan 17)

**Harness:** live as of commit `4d3e51a` — `strategy/engine/` event-driven
backtester wired to the plan-02 replayer, PaperSink fill model (taker or mid),
SQLite post-trade store. See [plan 17](./17-execution-and-replay-parity-engine.md).

**Original stub run — INVALID (wrong exit + phantom trades).** The first pass
(commit `4d3e51a`) used a 30-minute timer exit and had a post-session phantom
re-entry bug: after the 15:49 WIB EOD exit, further post-close events found
`pos=flat` and price still above the opening-range upper, firing instant
entry+exit pairs at zero gross. That run's 397 round-trips (of which the vast
majority were phantom), gross −19.1 bps, net −59.1 bps are discarded.

**Corrected re-validation (EOD exit, no-re-entry guard; Jun 22–26, 7 symbols):**

Exit now fires on `isSessionEnd` (08:49 UTC = 15:49 WIB). Post-session events
are gated. Two fill modes compared, 21 legitimate round-trips total (3/symbol,
days where OR breakout triggered within session):

| Fill mode | Round-trips | Mean gross (bps) | Mean net (bps) | Hit rate |
|---|---|---|---|---|
| Taker (far touch) | 21 | −73.0 | −112.3 | 42.9% (3/7 symbols +ve) |
| Mid ((bid+ask)/2) | 21 | −36.9 | −76.2 | 42.9% (3/7 symbols +ve) |

Per-symbol gross / net (taker \| mid):
BREN −249/−289 \| −214/−253 · BRPT −690/−729 \| −654/−693 · ENRG −489/−528 \| −439/−477 ·
PTRO +39/−0 \| +63/+23 · RAJA +591/+551 \| +623/+583 · RATU −368/−407 \| −335/−374 · TPIA +655/+615 \| +697/+658

**Interpretation.** The ~36 bps mean gross spread between taker and mid fill
modes is consistent with 1–2 tick bid-ask on these symbols, confirming fill
model choice matters. Three symbols (PTRO, RAJA, TPIA) are gross-positive under
both modes; four (BREN, BRPT, ENRG, RATU) are negative even at mid — suggesting
genuine adverse selection or unfavourable fixture windows for those names. N=3
round-trips per symbol makes per-symbol estimates very noisy.

**§8 status.** Mid fills bring PTRO/RAJA/TPIA to positive gross, aligning with
plan-10's close-price methodology (close ≈ mid). BRPT/BREN/ENRG/RATU remain
negative under both modes. The red flag is not fully closed — but this is the
Jun 22–26 stub universe (7 survivors), not plan-10's fixture universe
(ANTM/BUMI/DEWA/ITMG/MDKA). A live-data confirmation on the plan-10 names
(blocked on `SB_BEARER_TOKEN`) is the honest §8 close.

**Plan-10 fixture grid — calibrated vs uncalibrated impact (commit `b5a60ec`):**

Both `--use-calibrated` and uncalibrated runs produce identical survivors:

| Symbol | Strategy | Hold | Net edge (bps) | Impact (bps) |
|---|---|---|---|---|
| ITMG | intraday_momentum | 0 | +50.1 | 0.0 |
| ITMG | mean_reversion | 1 | +525.0 | 0.0 |
| ITMG | orb | 0 | +50.1 | 0.0 |

Calibration (coeff 0.30 vs 1.0) does lower intraday hurdles for high-friction
names: BUMI ORB 150.0 → 128.5 bps, DEWA ORB 262.7 → 188.9 bps. But all three
ITMG survivors have impact=0 (low friction × low participation rate), so the
final survivor list is unchanged. The fixture grid pipeline is confirmed
correct end-to-end with calibrated coefficients wired.

**Live grid re-run — Jun 27 (115 symbols, token confirmed valid):**

Bearer token validated as session-based (server accepts beyond JWT `exp`).
Full 115-symbol universe re-run completed. 11 survivors — same 6 symbols,
one addition (RATU intraday_momentum +1.4 bps marginal). ORB margins
strengthened across most names week-on-week:

| Cell | Jun 20 | Jun 27 |
|---|---|---|
| TPIA ORB | +40.7 bps | +87.0 bps |
| BRPT ORB | +40.6 bps | +59.7 bps |
| RAJA ORB | +31.3 bps | +43.4 bps |
| RATU ORB | +5.1 bps | +17.6 bps |
| RAJA intraday_mom | +5.3 bps | +16.8 bps |
| PTRO ORB | +52.0 bps | +51.2 bps |
| ENRG ORB | +35.0 bps | +26.4 bps |
| RATU volume_breakout | +24.3 bps | +14.7 bps |
| BREN daily_mom | +26.1 bps | +22.2 bps |

Cross-sectional / pairs / event-driven remain cleanly rejected (p=1.0).
The §8 divergence block is lifted — the token works, the live grid runs.

---

Prev: [09-universe-screener.md ←](./09-universe-screener.md) ·
Index: [README](./README.md)
