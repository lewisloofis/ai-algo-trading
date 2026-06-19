# Strategy Fit & Portfolio Backtester — Data-Chosen Strategies per Name

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-19
> **Related:** [09-universe-screener.md](./09-universe-screener.md) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) (Workflow B) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [05-ofi-trade-flow-study.md](./05-ofi-trade-flow-study.md) · [04-microstructure-study.md](./04-microstructure-study.md) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (§1.2) · [Plans index](./README.md)

> **What this is.** The layer that decides *which strategy fits which name*, from
> data — not from a strategy assumption baked into the universe. Plan 09 hands us
> a clean, signal-agnostic universe (funding-eligible ∩ all-funding-options-
> unlocked, annotated with structural friction). This plan runs a **panel of
> strategy families across horizons** against every name in that universe, nets
> each against *that name's* friction floor, and keeps the `(name, strategy)`
> pairs that survive a multiple-testing-honest backtest. The portfolio is the
> union of survivors. The objective is the **combination of profitable
> strategies**, intraday-within-minutes through daily-hold — whatever the data
> says clears the cost gate.

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

**Horizon-spanning, data-chosen.** We do not pre-pick a horizon. The strategy
panel spans intraday (seconds–minutes, order-book) through daily-hold (days,
daily bars). The data assigns the horizon by which cell survives.

**Multiple-testing discipline is mandatory.** Running a panel × a universe is a
large grid — exactly the false-discovery trap plan 08 Workflow B exists to
prevent. Survivors must clear de-overlapped effective-N (≥ MIN_EFF_N=30, per
plans 04/05), block-bootstrap nulls, and a deflated-Sharpe / FDR correction
across the whole grid before they count.

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

## 4. The strategy panel

A small, honest set of families spanning the horizon axis. Each is a hypothesis
to be *refuted*, not assumed:

| Family | Horizon | Data | Edge source | Prior from our studies |
|---|---|---|---|---|
| Order-flow imbalance (OBI/OFI) | seconds–minutes | order book (`sbcap features`) | microstructure | NO-GO on tick-scale names (plan 06); directional but thin (04/05) |
| Intraday momentum / breakout | minutes–hours | intraday bars | continuation | untested |
| Daily momentum / swing | days | daily bars (chartbit) | trend persistence | untested — **primary new bet** |
| Mean-reversion (overextension) | days | daily bars | reversal | untested |
| Event-driven (gap / news / volume spike) | days | daily bars + foreign flow | repricing | untested; only path for very-wide-tick names |

Daily-bar families are the cheap first pass: chartbit gives a full year of
OHLCV + `value` + foreign-flow in one call per name (plan 09 live endpoints). The
order-book family stays gated on captured data (plan 01) and is already
characterised as NO-GO for the cheap names — it earns a re-test only on the
low-friction names the intersection surfaces (e.g. ITMG-class).

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

1. For each name in the plan-09 universe, for each strategy family, run the
   family's backtest over the available history (daily first; order-book where
   captured).
2. Net every cell against that name's real cost stack (`min_edge_to_clear_bps`
   floor + modelled impact). A cell is a *candidate* only if mean net edge/trade
   > 0 with the same effective-N / bootstrap hygiene as plans 04/05/06.
3. Barrier: apply deflated-Sharpe / FDR across the entire grid (plan 08 §B). Only
   grid-corrected survivors are kept.
4. Output: the surviving `(name, strategy, horizon)` pairs = the **strategy
   portfolio**. Expectation, given priors: low-friction names (ITMG-class) admit
   several families; cheap high-friction names (BUMI/DEWA) survive only under
   wide-edge daily/event strategies, if at all.

## 7. Implementation

- [ ] **7.1** Consume plan 09's ranked universe (read `universe_screen.md` or call
  the screener as a library) — the input contract.
- [ ] **7.2** `research/strategy_fit/` — daily-bar strategy harness: pluggable
  strategy family interface (`signals(df) -> trades`), a backtest that nets each
  family against the per-name cost stack, three-state verdict mirroring plans
  04/05/06.
- [ ] **7.3** The four daily-bar families (momentum, mean-reversion, breakout,
  event/gap) as pure signal functions over chartbit OHLCV + foreign flow.
- [ ] **7.4** Grid runner + multiple-testing correction (deflated Sharpe / FDR)
  across `(name, strategy, horizon)`; emit `research/out/strategy_portfolio.md`.
- [ ] **7.5** (Deferred) re-test the order-book OBI/OFI family on the low-friction
  intersection names once captured (plan 01) — the only honest re-open of the
  plan-06 NO-GO.
- [ ] **7.6** (Deferred) wire as plan 08 Workflow B when grid size makes the
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
- Live execution / order routing / portfolio risk sizing — downstream pillars
  (roadmap §2–3).
- Adding strategy families speculatively — start with the daily-bar four; add
  only when a data reason appears (YAGNI).
- Re-opening the order-book NO-GO on the cheap high-friction names — the data
  already answered that; re-test only on low-friction names, and only once
  captured.

---

Prev: [09-universe-screener.md ←](./09-universe-screener.md) ·
Index: [README](./README.md)
