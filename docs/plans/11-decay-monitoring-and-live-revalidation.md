# Decay Monitoring & Live Revalidation — Alpha Kill-Switch and Global Off-Switch

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-19
> **Related:** [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) (Workflow B/C, regime refutation) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (pillar 3 regime/off-switch, pillar 4.2 post-trade store, pillar 5 decay/kill) · [Plans index](./README.md)

> ⛔ **BLOCKED — cannot run before live trading exists (pillar 4.2).** This plan
> monitors *live* PnL against backtest expectations. There is no live PnL yet;
> the entire plan is deferred until the event-driven runtime, order routing, and
> post-trade store are in place. It is correctly sequenced last in the roadmap
> (§6 step 8: "Monitoring + decay loop ← forever"). Do not build any part of
> this before live trading is live.

> **What this is.** The production-time answer to "a strategy that is real today
> may die later." It consolidates several currently-loose roadmap bullets into one
> buildable ticket: *alpha decay monitoring* (pillar 5: "detect when a live signal
> dies and pull it"), *regime detection / global off-switch* (pillar 3: "the
> cheapest risk control there is"), *kill criteria* (pillar 5: "pre-agreed
> conditions under which the whole system stops"), and the *post-trade store*
> (pillar 4.2: "every decision + fill, for attribution and decay"). The plan's
> central design goal is **unification**: it reuses plan 10's decay-metric
> definition verbatim so that the backtest walk-forward gate and the live
> kill-switch use the same degradation criterion.

## 1. Why this is a separate plan from plan 10

Plan 10 is a research-time tool: it runs walk-forward windows over historical
daily bars, computes a rolling Sharpe / hit-rate decay metric, and uses that
metric to decide which `(name, strategy)` cells enter the portfolio. That is a
one-time batch job on static history.

This plan is the production-time continuation: once strategies are live, the
same rolling decay metric must be recomputed on *live PnL* as each day closes.
When the live metric falls below the same threshold plan 10 used to admit the
strategy, the system pulls it — no discretion required. The threshold is not
re-derived here; it is imported from plan 10. The backtest and the live monitor
speak the same language by design.

The separation is clean: plan 10 is research, plan 11 is ops. Neither can
substitute for the other.

## 2. Locked decisions

**Blocked on live trading.** Nothing in this plan can be built or meaningfully
tested until the event-driven runtime (pillar 2), order routing, and the
post-trade store (pillar 4.2) exist and are generating real fills. Building a
decay monitor with no live PnL to monitor is theatre.

**Decay-metric definition is inherited from plan 10, not re-derived.** The
rolling Sharpe and rolling hit-rate computed on each forward test window in plan
10's walk-forward harness are the canonical decay metric. Plan 11 applies the
same computation to live daily PnL. The threshold (below which a cell is marked
decayed) is set once in plan 10 and reused here unchanged. This is the whole
point: the same signal that blocked a `(name, strategy)` cell from entering the
portfolio in backtesting will pull it from live trading when performance
degrades to the same level.

**Expectation for live comparison comes from plan 10.** For every live
`(name, strategy)` pair, plan 10's `strategy_portfolio.md` records the expected
net edge, rolling Sharpe, and decay-metric threshold that cell had to clear.
Plan 11 tracks live rolling Sharpe against that expectation. A cell is
decaying when live rolling Sharpe drops below the plan-10 threshold, not when
it falls below zero or some arbitrary level.

**The post-trade store is the data substrate.** Every decision (signal,
sizing input, order sent) and every fill (execution price, slippage vs.
expectation) must be logged to the post-trade store (roadmap pillar 4.2:
"every decision + fill, for attribution and decay"). The decay monitor
consumes that store; without it there is nothing to compute the rolling
metric on.

**Regime detection is the global off-switch.** A portfolio-wide off-switch
based on detected regime change (e.g. persistent drawdown across all active
strategies, unusual correlation breakdown, macro regime shift flagged by
simple cross-strategy PnL correlation) is the cheapest risk control available
(roadmap pillar 3). It does not require knowing *why* the regime changed — only
that live performance across the portfolio has diverged from backtest
expectations in a way that triggers the pre-agreed kill criterion.

**Kill criteria are pre-agreed, not discretionary.** The conditions under which
the whole system stops (roadmap pillar 5: "kill criteria") must be written down
before go-live and enforced mechanically. Typical candidates: rolling portfolio
Sharpe below a floor for N consecutive days, total drawdown exceeding a hard cap,
or more than K strategies simultaneously below their individual decay thresholds.
The exact thresholds are set during paper-trade validation (roadmap §6 step 6),
not here.

## 3. Core design — the decay loop

```
[post-trade store]  ←  every decision + fill, logged by the live runtime
        │
        ▼  (end of each trading day)
[rolling metric computation]
  - per (name, strategy): rolling Sharpe + rolling hit-rate
    over the trailing N-day live window
  - same formula as plan 10 walk-forward forward-window computation
        │
        ├─ cell metric < plan-10 threshold → PULL that (name, strategy) pair
        │                                   log reason, alert operator
        │
        └─ portfolio-wide metric < kill threshold → GLOBAL OFF-SWITCH
                                                   halt all strategies, alert
```

The loop runs end-of-day (or intraday if fills warrant it). Each iteration
either confirms the strategy is within expectation, pulls individual cells that
have decayed, or fires the global off-switch if the portfolio-wide criterion is
met.

## 4. Roadmap consolidation — what this plan replaces

These are the loose roadmap bullets this plan formalises into a buildable ticket:

| Roadmap item | Location | Resolved here |
|---|---|---|
| Alpha decay monitoring — "detect when a live signal dies and pull it" | Pillar 5 | Per-cell rolling decay metric vs. plan-10 threshold |
| Regime detection / global off-switch — "the cheapest risk control" | Pillar 3 | Portfolio-wide metric → global halt |
| Kill criteria — "pre-agreed conditions under which the whole system stops" | Pillar 5 | Pre-agreed thresholds enforced mechanically |
| Post-trade store — "every decision + fill, for attribution and decay" | Pillar 4.2 | Data substrate the monitor consumes |

## 5. Data sources

- **Post-trade store** (pillar 4.2) — the live daily PnL and fill records the
  decay computation runs on. Must exist before this plan can operate.
- **`strategy_portfolio.md`** (plan 10 output) — the expected net edge, rolling
  Sharpe, and decay-metric threshold for every admitted `(name, strategy)` pair.
  The live monitor imports these expectations; it does not re-derive them.
- **Plan 10 decay-metric code** (`research/strategy_fit/`) — the rolling-Sharpe
  and hit-rate functions are imported, not reimplemented, to keep the backtest and
  live computation provably identical.

## 6. Method — end-of-day decay check

1. Read that day's fills and decisions from the post-trade store.
2. For each active `(name, strategy)` pair, update the trailing rolling window
   with today's realised PnL.
3. Compute rolling Sharpe and rolling hit-rate using the same functions as plan 10.
4. Compare to the plan-10 threshold for that cell:
   - **Above threshold** → no action; log metric.
   - **Below threshold for the first time** → warn; flag cell as on watch.
   - **Below threshold for M consecutive days** → PULL: deactivate the strategy
     for that `(name, strategy)` pair; log the pull with the decay trace; alert
     operator.
5. Compute the portfolio-wide off-switch metric (e.g. fraction of active cells
   currently below threshold, or portfolio rolling Sharpe):
   - **Below kill criterion** → GLOBAL OFF-SWITCH: halt all active strategies;
     alert operator; require manual re-enable.
6. Emit a daily decay report analogous to `strategy_portfolio.md` — current
   rolling metrics, on-watch cells, pulled cells, global status.

## 7. Implementation

- [ ] **7.1** (Blocked on pillar 4.2) Post-trade store schema — every decision
  (signal value, sizing input, order id, timestamp) and fill (execution price,
  slippage vs. expected) written to a queryable store (SQLite or append-log).
  This is pillar 4.2 work; it is a prerequisite, not part of this plan to build.
- [ ] **7.2** (Blocked on live trading) Decay-metric runner — imports rolling-Sharpe
  and hit-rate functions from `research/strategy_fit/` (plan 10, step 7.4);
  applies them to the post-trade store's trailing window; emits per-cell metrics
  and a portfolio-wide aggregate.
- [ ] **7.3** (Blocked on live trading) Pull logic — when a cell's metric falls
  below the plan-10 threshold for M consecutive days, deactivate it and emit a
  structured pull record (cell id, last N days of metrics, pull timestamp).
- [ ] **7.4** (Blocked on live trading) Global off-switch — when the portfolio-wide
  metric hits the kill criterion, halt all active strategies; require operator
  confirmation to re-enable.
- [ ] **7.5** (Blocked on paper trade, pillar 5 §6 step 6) Kill-criteria
  calibration — during paper trade, set the M (consecutive days below threshold)
  and portfolio-wide kill threshold from observed metric volatility; record them
  in a config file before go-live.
- [ ] **7.6** (Blocked on live trading) Daily decay report — `research/out/decay_report_YYYYMMDD.md`
  listing current rolling metrics, on-watch cells, pulled cells since last report,
  global off-switch status. Same `*.md` output convention as plans 04–06.

## 8. Verification

- **Same-language test** — given synthetic PnL that reproduces a plan-10
  walk-forward forward window, the decay metric computed here matches plan 10's
  output exactly (same code path, same numbers).
- **Pull fires correctly** — a simulated sequence where a cell degrades below
  threshold for M days results in a pull record and deactivation; a transient
  dip for fewer than M days does not.
- **Global off-switch fires correctly** — a simulated portfolio-wide drawdown
  beyond the kill criterion halts all cells; individual cells below threshold
  but below the portfolio kill criterion do not trigger the global halt.
- **Post-trade store round-trip** — a decision + fill written by the live runtime
  is readable by the decay runner with no lossy transformation.

## 9. Out of scope

- Position sizing, portfolio construction, or order routing — those are pillars
  2 and 3; this plan only monitors and halts, it does not size or route.
- Building any part of this before live trading exists — the block is hard; a
  decay monitor with no live PnL is a no-op.
- Re-deriving the decay threshold — plan 10 owns the threshold; this plan imports
  it. Any change to the threshold must happen in plan 10 and propagate here.
- The execution runtime itself — that is pillar 2 / pillar 4.2 work; this plan
  only consumes its output.

---

Prev: [10-strategy-fit-and-portfolio.md ←](./10-strategy-fit-and-portfolio.md) ·
Index: [README](./README.md)
