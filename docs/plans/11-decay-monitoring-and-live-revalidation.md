# Decay Monitoring & Live Revalidation — Alpha Kill-Switch and Global Off-Switch

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-09-05
> **Related:** [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) (Workflow B/C, regime refutation) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (pillar 3 regime/off-switch, pillar 4.2 post-trade store, pillar 5 decay/kill) · [Plans index](./README.md)

> ⛔ **BLOCKED — cannot run before live trading exists (pillar 4.2).** This plan
> monitors *live* PnL against backtest expectations. There is no live PnL yet;
> the entire plan is deferred until the event-driven runtime and order routing
> are in place. It is correctly sequenced last in the roadmap
> (§6 step 8: "Monitoring + decay loop ← forever"). Do not build any part of
> this before live trading is live.
>
> **Updated 2026-09-05 — the block is narrower than it was, and §2b below is a
> material revision to how this plan should work.** The post-trade store is no
> longer a blocker: `strategy/cmd/strategy-run` already writes one
> (`decision` / `fill` / `order_intent` / `attribution`), 938 MB of it in the
> W2.4 replay. The only remaining blocker is **live fills**. More importantly,
> Wave 2 established that this plan's central mechanism — a statistical decay
> metric — **cannot be the primary control at this edge size and trade
> frequency**. See §2b.

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

## 2b. What Wave 2 changed about this plan (2026-09-05)

*Three findings from the Wave 2 re-run bear directly on this plan. The first
narrows what this plan is FOR; the second says its headline mechanism cannot
carry the load; the third makes it cheaper to build than §7 claims.*

### 2b.1 This plan does not protect against the failure that actually happened

**Decay is an edge that was real and stopped working. That is not what Wave 2
found.** The plan-10 intraday ORB edge existed only in the window used to
*select* the names — chartbit ORB split at 2026-06-22 (the day forward capture
of those seven names began) reads ENRG +94.2 → −0.8, TPIA +54.0 → −0.7,
BRPT +38.0 → +0.7 bps, and PTRO was negative in *both* halves. That date is not
a regime change; it is the selection boundary. Even in-sample, once the
unrealizable short leg was removed (`c62eba8`), every ORB p-value was ≥ 0.22.

**A decay monitor is structurally incapable of catching this**, and it is worth
being precise about why: this plan compares live PnL against the plan-10
expectation (§2, "Expectation for live comparison comes from plan 10"). If that
expectation was inflated by selecting and evaluating on the same data, the
*threshold is inflated by the same amount*. The monitor would fire — correctly,
and immediately — but only after real money had traded. **It is a stop-loss, not
a filter.**

The controls that do address it sit earlier in the pipeline and are not this
plan's job: never selecting and evaluating on the same window (roadmap W0.2's
random control slice is the instrument), and the wall-clock paper forward test
(roadmap W4.3 / plan 17 §2 gate condition 2) — the one out-of-sample test that
cannot be contaminated, because the data does not exist yet when the names are
chosen. **Wave 2 makes the paper test non-optional rather than a formality.**

### 2b.2 A statistical decay metric cannot be the primary control here

Detecting an edge of size Δ against per-trade dispersion σ at z standard errors
needs N ≥ (z·σ/Δ)² observations (`research/lib/stats.py: required_effective_n`).
Measured per-trade σ on this universe is **400–650 bps** — not the ~150 the
roadmap originally assumed. A 2-day-hold daily strategy generates roughly
**110 round-trips per year**. Against the cells W2.3 actually surfaced:

| Cell | net edge (bps) | N to resolve (z=2) | years to resolve |
|------|---------------:|-------------------:|-----------------:|
| BUVA volume_breakout h2 | 54.2 |   341 |  **3.1** |
| WIFI daily_momentum h2  | 51.9 |   372 |  **3.4** |
| BREN daily_momentum h2  | 28.1 |   831 |  **7.6** |
| PTRO daily_momentum h2  | 39.0 |   957 |  **8.7** |
| TINS mean_reversion h2  | 16.4 | 3,012 | **27.4** |
| SSIA daily_momentum h2  |  6.6 | 18,596 | **169** |

And detecting *decay* means distinguishing two regimes, which needs roughly
**twice** that sample. **You cannot detect the decay of a signal you cannot
detect in the first place.**

**Two consequences, both locked decisions:**

- **The pre-agreed hard loss cap is the PRIMARY control; the rolling-Sharpe test
  is a slow confirmatory signal.** §2 currently frames this the other way round
  and defers the cap to "set during paper-trade validation". That inverts the
  real load-bearing order. A drawdown cap needs no statistical power — it acts on
  realised loss, which is observable immediately and is the thing that actually
  hurts. Reorder §3's loop accordingly: the cap is checked first and can fire on
  day one; the metric is checked second and may take quarters to say anything.
- **Deployability rule — do not deploy a cell whose edge is too small to be
  monitorable inside a year.** On the table above that is a net edge of roughly
  **50 bps or more**. Below it, the cell is unfalsifiable in production: there is
  no observation sequence within a useful horizon that would tell you it had
  stopped working, so you would be running it on faith and learning the answer
  through drawdown. This is a *portfolio admission* criterion and belongs
  alongside plan 10's cost gate — a cell can clear the cost gate and still be
  undeployable on these grounds.

### 2b.3 The post-trade store already exists

§5 and §7.1 treat the post-trade store as a prerequisite to be built. It has
been built: `strategy/cmd/strategy-run` writes SQLite with `decision`, `fill`,
`order_intent` and `attribution` tables — the W2.4 replay produced 938 MB of it
over 53 sessions, and the per-symbol attribution in
[roadmap §6 W2.4](./00-roadmap-research-to-live.md) was queried straight out of
it. The schema is the one §5 asks for. **§7.1 is done; the remaining block is
live fills, not the substrate.** That also means the decay runner (§7.2) can be
built and tested *now* against replay-generated PnL, before any live trading —
which is the honest way to verify the "same-language test" in §8 without waiting.

## 3. Core design — the decay loop

*Ordered by latency, not by sophistication (§2b.2). The loss cap can fire on day
one; the statistical metric may take quarters to say anything, so it must never
be the only thing standing between the system and a loss.*

```
[post-trade store]  ←  every decision + fill, logged by the live runtime
        │                (exists today — plan 17 §5.4, see §2b.3)
        ▼  (continuously / on each fill)
[LAYER 1 — hard loss cap]              ← PRIMARY. No statistics, no threshold
  - per-cell realised drawdown            derived from a backtest. Acts on
  - portfolio realised drawdown           realised loss, which is observable
        │                                 immediately.
        ├─ cell cap breached      → PULL that (name, strategy) pair, alert
        └─ portfolio cap breached → GLOBAL OFF-SWITCH, halt all, alert
        │
        ▼  (end of each trading day)
[LAYER 2 — rolling metric computation] ← CONFIRMATORY, slow by construction
  - per (name, strategy): rolling Sharpe + rolling hit-rate
    over the trailing N-day live window
  - same formula as plan 10 walk-forward forward-window computation
        │
        ├─ cell metric < plan-10 threshold for M days → PULL, log, alert
        │
        └─ portfolio-wide metric < kill threshold → GLOBAL OFF-SWITCH
```

Layer 1 runs continuously and needs no statistical power — it is the control
with useful latency at this edge size, and it must exist before the first live
order (§7.7). Layer 2 runs end-of-day and either confirms the strategy is within
expectation or pulls cells that have genuinely decayed; on the measured numbers
in §2b.2 it will take **3–8 years** to reach a verdict on a typical cell, so
treat a Layer-2 pull as valuable evidence and never as the safety net.

**A cell that reaches production should have passed the §7.8 deployability
screen**, so that Layer 2 can in principle say something about it inside a year.
Anything below that bar is running on Layer 1 alone — which is a decision that
can be made deliberately, but must not be made by accident.

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
  decay computation runs on. **Exists** (§2b.3): `strategy/cmd/strategy-run`
  writes `decision` / `fill` / `order_intent` / `attribution` to SQLite. What is
  missing is live fills, not the store — so the runner can be developed and
  verified against replay-generated PnL today.
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

- [x] **7.1** Post-trade store schema — every decision (signal value, sizing
  input, order id, timestamp) and fill (execution price, slippage vs. expected)
  written to a queryable store. **Done** — `strategy/cmd/strategy-run` writes
  SQLite with `decision` / `fill` / `order_intent` / `attribution`; verified at
  938 MB over the 53-session W2.4 replay (2026-09-05). Not built by this plan;
  it arrived via plan 17 §5.4.
- [ ] **7.2** (Buildable NOW against replay PnL — see §2b.3; only its *live*
  operation is blocked) Decay-metric runner — imports rolling-Sharpe
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
- [ ] **7.7** **Hard loss cap — the primary control (§2b.2).** A per-cell and
  portfolio-level realised-drawdown cap that fires without reference to any
  statistical metric. Must exist before the first live order, not after paper
  trade, because it is the only control with useful latency at this edge size.
  Plan 13's per-cycle loss limit (roadmap W3.2) is the portfolio half of this.
- [ ] **7.8** **Deployability screen (§2b.2).** Reject any candidate cell whose
  `required_effective_n` (from `lib/stats.py`, fed the cell's measured per-trade
  sigma) exceeds one year of its own expected trade count. Emit the horizon in
  the daily report so an operator can see which live cells are unfalsifiable.
- [ ] **7.6** (Blocked on live trading) Daily decay report — `research/out/decay_report_YYYYMMDD.md`
  listing current rolling metrics, on-watch cells, pulled cells since last report,
  global off-switch status. Same `*.md` output convention as plans 04–06.

## 8. Verification

- **Same-language test** — given synthetic PnL that reproduces a plan-10
  walk-forward forward window, the decay metric computed here matches plan 10's
  output exactly (same code path, same numbers). **Runnable now** against
  replay-generated PnL from `strategy-run` (§2b.3) — it does not need live fills.
- **Loss cap fires without the metric** — a simulated cell that breaches its
  realised-drawdown cap is pulled on the breaching fill, with no reference to
  rolling Sharpe and no M-day wait. This is the §2b.2 ordering; a test that only
  exercises the statistical path would let the ordering silently regress.
- **Deployability screen rejects an unmonitorable cell** — a cell whose
  `required_effective_n` exceeds a year of its own expected trades is refused
  admission (§7.8), and the daily report states the horizon in years for every
  live cell.
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
- **Protecting against selection bias** — explicitly NOT this plan's job (§2b.1).
  An expectation inflated by selecting and evaluating on the same window inflates
  this plan's threshold by the same amount. That failure is addressed upstream, by
  the out-of-sample discipline in roadmap W0.2 (random control slice) and the
  wall-clock paper forward test (W4.3 / plan 17 §2). Do not let the existence of a
  decay monitor be used as an argument for skipping either.
- The execution runtime itself — that is pillar 2 / pillar 4.2 work; this plan
  only consumes its output.

---

Prev: [10-strategy-fit-and-portfolio.md ←](./10-strategy-fit-and-portfolio.md) ·
Index: [README](./README.md)
