# Strategy Fit & Portfolio Backtester — Data-Chosen Strategies per Name

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-20
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
length and entry day-of-week, the gate is now **day-of-week-dependent**: early-
week entries are structurally cheaper; weekend-spanning holds are punishing.

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
test to clear. Calendar days outstanding is the settlement-driven count: a
Thursday borrow, sold Friday, settles Monday = 4 calendar days = 40 bps of
financing, regardless of the 1-trading-day hold. Same-day round-trips complete
within the trading session and carry 0 calendar days outstanding, so financing
is zero. Weekend-spanning holds roughly double the worst-case hurdle versus a
fee-only gate (per ADR-0011).

| Entry → exit | Calendar days outstanding | financing\_bps | Effective hurdle (≈ fee + financing) |
|---|---|---|---|
| Same-day round-trip | 0 | 0 | ~40 bps |
| Mon→Tue (overnight) | ~1–3 | ~10–30 | ~50–70 bps |
| Thu→Fri (settles Mon, carries weekend) | 4 | 40 | ~80 bps |

This day-of-week structure creates a real scheduling optimisation lever: early-
week entries are structurally cheaper and should be preferred by the allocator
(plan 13). Same-day round-trips avoid financing entirely and reopen the
larger-move intraday band (see §4 below).

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

- [ ] **7.1** Consume plan 09's ranked universe (read `universe_screen.md` or call
  the screener as a library) — the input contract.
- [ ] **7.2** `research/strategy_fit/` — strategy harness with a **pluggable shape
  interface**: each family declares its shape (`single_name_time_series`,
  `cross_sectional`, `event_driven`, or `pairs`) and the harness dispatches to the
  correct test primitive and effective-N unit (§2, §6). The three-state verdict
  mirrors plans 04/05/06 in all shapes.
- [ ] **7.3** The single-name daily-bar families (momentum, mean-reversion, breakout)
  as pure signal functions over chartbit OHLCV + foreign flow —
  `shape=single_name_time_series`.
- [ ] **7.4** Event-driven family (gap / volume spike / foreign-flow spike) with
  pooled cross-universe event returns and MIN_EFF_N=30 on pooled non-overlapping
  events — `shape=event_driven`. This is the primary (and often only) viable shape
  for very-wide-tick names.
- [ ] **7.5** Cross-sectional momentum / factor: daily cross-sectional rank, IC
  series, ICIR — `shape=cross_sectional`. MIN_EFF_N=30 on cross-sectional periods.
- [ ] **7.6** Pairs / cointegration: pairwise ADF / Johansen on the universe names,
  spread-reversion half-life, OOS block-bootstrap on residual-spread obs —
  `shape=pairs`.
- [ ] **7.7** Walk-forward harness + decay metric — rolling fit→test→roll loop
  over available history (by shape: daily rows, cross-sectional periods, pooled
  events, spread obs); compute rolling Sharpe and hit-rate per forward window; mark
  cells decayed/non-persistent when the metric falls below threshold. Shared
  verbatim with plan 11.
- [ ] **7.8** Grid runner + coverage table + multiple-testing correction (deflated
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

## Event-driven re-validation status (plan 17)

**Harness:** live as of commit `4d3e51a` — `strategy/engine/` event-driven
backtester wired to the plan-02 replayer, PaperSink taker fill model, SQLite
post-trade store. See [plan 17](./17-execution-and-replay-parity-engine.md).

**5-day ORB stub run (Jun 22–26 plan10-survivors, `strategy-run --strategy orb`):**

| Metric | Result |
|---|---|
| Segments | 45 (5 days × 9/day) |
| Decisions | 2,470,000 |
| Round-trips | 397 |
| Mean gross PnL | −19.1 bps |
| Mean net PnL | −59.1 bps |
| Hit rate | 26.4% |

By symbol (gross / net / hit rate): ENRG +10.6/−29.4/31.6% · PTRO −12.7/−52.7/35.1% · TPIA −10.0/−50.0/31.6% · BRPT −11.4/−51.4/25.9% · BREN −19.5/−59.5/26.3% · RATU −35.2/−75.2/16.7% · RAJA −56.4/−96.4/17.5%

**Divergence from plan-10 CSV result: confirmed — §8 red flag tripped (>±5 bps gross).**

**Root cause — not a kill.** The stub run uses the placeholder PaperSink (taker
at far touch) and a fixed 30-minute timer exit. The taker model pays the full
spread on both entry and exit, making gross negative by construction before fees
— this is the fill-model gap plan 15 exists to close. The stub exit rule is also
not plan-10's actual ORB signal exit logic. The engine and post-trade store
operated correctly end-to-end (2.47M decisions, attribution table populated,
append-only verified).

**Next step:** re-run with (a) plan-15 calibrated fills wired into `PaperSink`
(true impact coeff ~0.30 for survivors vs assumed 1.0) and (b) plan-10's actual
ORB exit rule ported to the Go strategy. That comparison is the honest
event-driven confirmation plan-10 still needs.

---

Prev: [09-universe-screener.md ←](./09-universe-screener.md) ·
Index: [README](./README.md)
