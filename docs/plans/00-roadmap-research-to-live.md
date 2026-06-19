# Roadmap — From Research Loop to a Live, Profitable Trading System

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-18
> **Related:** [01-market-data-capture.md](./01-market-data-capture.md) · [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) · [03-token-rotation.md](./03-token-rotation.md) · [04-microstructure-study.md](./04-microstructure-study.md) · [05-ofi-trade-flow-study.md](./05-ofi-trade-flow-study.md) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md) · [Plans index](./README.md) · [ADR-0001](../adr/0001-treat-plans-as-tickets.md)

> **What this is.** A map, not a buildable ticket. Plans 01–06 each fulfil one
> slice; this doc places them in the larger picture and names everything still
> missing between "we ran one rigorous research loop" and "this takes money out
> of the market." Each pillar below links to (or seeds) a concrete plan.

## 0. Where we actually are

We have completed **one full, rigorous research loop on one signal family** —
hypothesis → data → signal → statistical validation → cost/fill simulation →
net-of-cost verdict — and ended it the way a disciplined desk does: with an
honest kill ([plan 06](./06-fill-simulator-and-backtest.md): **NO-GO**).

That is the front quarter of a complete system, and it is the *correct* quarter
to have built first, because it is the part that tells you whether to keep going.
It is **not** a trading system. A complete operation is five pillars; we have a
slice of pillar 1 and scattered pieces of pillar 4.

### The one economic fact that governs everything

[Reference 07](../reference/07-fees-and-costs.md): the all-in round-trip cost on
retail IDX is **40 bps**. Plan 06 proved a tick-scale (~1-tick, ~5–10 bps) edge
cannot clear it. This is not a detail — it is the **design constraint on the
entire strategy class**:

- High-frequency / tick-scale alpha is **dead on arrival** at this fee tier (proven).
- Only alpha whose expected per-round-trip edge is **materially larger than 40 bps
  after slippage** can survive — which points at **lower-frequency, larger-move**
  signals (multi-hour to multi-day holds) where 40 bps is a small fraction of the
  target move.

**Every pillar below is worthless until a signal clears this gate.** A flawless
execution/risk/ops stack wrapped around a no-edge signal loses money efficiently.
So the sequencing (§6) front-loads "find an edge that clears 40 bps" and defers
the expensive machinery until one exists.

---

## 1. Pillar 1 — The Alpha Factory (research engine)

*We built one rigorous instance. A "factory" cranks the handle on many hypotheses
cheaply, with statistics that hold up when you've tested dozens of ideas and a
few look good by chance.*

### Phase 1.1 — What exists (one loop)

- [x] Data capture & raw storage — [plan 01](./01-market-data-capture.md) / [02](./02-storage-format-and-replay.md)
- [x] Signal construction (one family: OBI1, weighted-mid, OFI, signed flow, Kyle's λ) — [plan 04](./04-microstructure-study.md) / [05](./05-ofi-trade-flow-study.md)
- [x] Statistical validation (decile curves, HAC regression, conditional hit-rate, block-bootstrap null, chronological OOS, **de-overlap audit**, effective-N floor) — [plan 04](./04-microstructure-study.md)
- [x] Cost / fill modeling (taker + passive, 40 bps round-trip) — [plan 06](./06-fill-simulator-and-backtest.md)
- [x] Net-of-cost verdict + kill discipline — [plan 06](./06-fill-simulator-and-backtest.md)

### Phase 1.2 — What makes it a factory (missing)

- [ ] **Universe screening** — choose names from *data*, not memory. Rank/filter
      on median daily turnover (value), L1 displayed depth, time-weighted relative
      spread (bps), **tick-size-as-fraction-of-price** (this bounds the achievable
      edge for an imbalance signal), volatility, and quote/trade update rate.
      *Upstream of everything — the current 5–6 miners are a convenience sample, so
      every verdict so far inherits that selection bias.* → [plan 09](./09-universe-screener.md).
- [ ] **More data across regimes** — multi-day capture so a verdict rests on
      trending/choppy/high-low-vol days, not one session. Depends on pillar 4.1.
- [ ] **Day-aware multi-day pooling** in the study harness — concat N days, split
      OOS *by day* (train early days, test later days), keep effective-N and
      bootstrap respecting day boundaries. (Harness is single-day today.)
- [ ] **A signal pipeline** — add a new hypothesis and get the full validation
      battery in hours, not a bespoke script each time.
- [ ] **Multiple-testing discipline at scale** — when you test 50 signals, ~2–3
      pass at p<0.05 by chance. Needs corrections (e.g. deflated Sharpe / FDR) so
      the factory doesn't manufacture false alpha.
- [ ] **Signal combination** — how surviving signals blend into one view (the
      alpha *blend*, not isolated signals).
- [ ] **Lower-frequency signal families** — the class the cost gate actually
      permits (see §0). The next research targets, given tick-scale is dead.

---

## 2. Pillar 2 — Execution system

*None of this exists yet. On a 40 bps market with no maker rebate, execution
quality is often a bigger lever than the signal.*

- [ ] **Order placement engine** — live order router over the Carina order API
      ([reference 03](../reference/03-order-placement.md)). The zero-dep Go
      prototype is a sketch, not a router.
- [ ] **Smart execution** — limit vs. market, passive-vs-cross decision, slicing.
- [ ] **Pre-trade risk checks enforced in code** — the rules in
      [reference 06](../reference/06-pre-trade-rules.md) gate every order *before* it sends.
- [ ] **Order lifecycle management** — partial fills, cancels, rejects, reconnect
      mid-order ([reference 04](../reference/04-order-lifecycle.md)).
- [ ] **Realized vs. expected fill measurement** — close the loop between the
      simulated fills (plan 06) and live slippage; feed it back into cost modeling.
- [ ] **Live/replay parity** — the event-driven **Go** engine that runs the *same*
      `strategy.go` interface live and in replay (the whole point of [plan 02](./02-storage-format-and-replay.md));
      deliberately deferred in plan 06 until a signal survives.

---

## 3. Pillar 3 — Portfolio construction & risk management

*The two gaps already named explicitly, plus their pieces. Only meaningful once
an edge exists.*

- [ ] **Position sizing** — volatility-scaled / fractional-Kelly, not fixed lots.
- [ ] **Portfolio construction** — combine signals across names into target
      positions; net exposure, correlation, concentration limits.
- [ ] **Risk limits** — per-position, per-sector, gross/net caps, **max-drawdown
      kill-switch**.
- [ ] **Regime detection / global off-switch** — the cheapest risk control there is.
- [ ] **Capacity & market-impact analysis** — does the edge survive *your own*
      footprint at real size?

---

## 4. Pillar 4 — Infrastructure & operations

*Capture is the easy 10%. Real-money operation is the rest.*

### Phase 4.1 — Unattended capture (partly designed, not running)

- [x] Capture loop + segment storage + S3 ship — [plan 01](./01-market-data-capture.md) / [02](./02-storage-format-and-replay.md)
- [ ] **EventBridge start/stop** around IDX hours (graceful shutdown so the day's
      segment flushes — segments flush only at midnight/clean stop).
- [ ] **Token rotation health** — [plan 03](./03-token-rotation.md) live and
      self-healing, or an unattended multi-day run dies on auth mid-week.
- [ ] **Verify it is actually accruing data** before counting on "just wait."

### Phase 4.2 — Live trading runtime (none)

- [ ] **Event-driven runtime** — signal → sizing → risk → order, in real time (ties to pillar 2).
- [ ] **Monitoring & alerting** — connection, order rejects, PnL-vs-expectation,
      within seconds (not discovered at EOD).
- [ ] **Reconciliation** — our view of positions/cash vs. the broker's, daily.
- [ ] **Logging & post-trade store** — every decision + fill, for attribution and decay.
- [ ] **Failover** — what happens when the box dies mid-position.

---

## 5. Pillar 5 — Capital, governance & business reality

*The part engineers skip and traders obsess over.*

- [ ] **Funded account & capital** sized to position limits.
- [ ] **PnL attribution & accounting** — which signal made/lost money, net of all
      fees **and Indonesian transaction tax**.
- [ ] **Go/no-go-to-live ladder** — paper trade (forward test in wall-clock) →
      tiny size → scale **only on realized PnL**, never backtested.
- [ ] **Alpha decay monitoring** — detect when a live signal dies and pull it.
- [ ] **Kill criteria** — pre-agreed conditions under which the whole system stops.

---

## 6. Sequencing — the only order that makes sense

```
You are here:  [Pillar 1, one research loop] ✔️ (plan 06 NO-GO)
                      │
   1. Universe screening          ← cheap, upstream, uses data we already have
   2. Multi-day capture running   ← pillar 4.1; turns 1 day into a real sample
   3. Find alpha that clears 40bps← lower-frequency families; THE gate
        │   if nothing survives → stop; no system saves a no-edge strategy
        ▼
   4. Portfolio + risk (pillar 3) ← only meaningful once an edge exists
   5. Execution engine (pillar 2) ← Go event-driven runtime + order router
   6. Paper trade (pillar 5)      ← real-time, forward test, no money
   7. Live tiny → scale on realized PnL
   8. Monitoring + decay loop (pillars 4.2 / 5) ← forever
```

**The discipline:** steps 4–8 are large and almost entirely unstarted, but
building them is worthless until step 3 succeeds. Prove an edge clears the cost
gate first (screen the universe → gather more data → test the lower-frequency
signals the gate permits), and only then invest in the trading-system machinery.

## 7. Immediate next actions

1. **Universe screening pass** — compute turnover / relative-spread /
   tick-to-price across the names in existing captures and rank them, so the next
   study runs on a data-chosen universe instead of a remembered one. *(See [plan 09](./09-universe-screener.md).)*
2. **Verify + start unattended capture** — confirm the collector is deployed,
   EventBridge is scheduling it, and token rotation is live; then let multi-day
   data accrue. *(Pillar 4.1.)*
3. **Day-aware multi-day pooling** in the study harness — prerequisite for any
   verdict that rests on more than one session.

---

Back to [Plans index](./README.md)
