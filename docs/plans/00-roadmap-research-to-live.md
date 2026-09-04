# Roadmap — From Research Loop to a Live, Profitable Trading System

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-09-04
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

- [x] **Universe screening** — choose names from *data*, not memory. Rank/filter
      on median daily turnover (value), L1 displayed depth, time-weighted relative
      spread (bps), **tick-size-as-fraction-of-price** (this bounds the achievable
      edge for an imbalance signal), volatility, and quote/trade update rate.
      *Upstream of everything — the current 5–6 miners are a convenience sample, so
      every verdict so far inherits that selection bias.* → [plan 09](./09-universe-screener.md)
      **(Tier A done & live-verified).** Which strategy fits each name is now
      [plan 10](./10-strategy-fit-and-portfolio.md).
- [ ] **More data across regimes** — multi-day capture so a verdict rests on
      trending/choppy/high-low-vol days, not one session. Capture is running
      (pillar 4.1) but the data is **not yet reachable by research** — see §6 W1.1.
      Note 2.5 months is still a *single regime*; regime breadth remains untested.
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
      *Which family to test next* is ranked by → [plan 16](./16-strategy-triage-and-prioritization.md)
      (the factory's triage brain; plan 08's Workflow B is the muscle).

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
      → [plan 15](./15-measured-fill-and-impact-calibration.md) (capture-based
      calibration, now) / [plan 17](./17-execution-and-replay-parity-engine.md) §6.6 (live loop, deferred).
- [ ] **Live/replay parity** — the event-driven **Go** engine that runs the *same*
      `strategy.go` interface live and in replay (the whole point of [plan 02](./02-storage-format-and-replay.md));
      deliberately deferred in plan 06 until a signal survives.
      → [plan 17](./17-execution-and-replay-parity-engine.md) (engine + paper sink now; live order routing gated).

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
      footprint at real size? → [plan 13](./13-capital-allocation-and-capacity.md) (C* caps) /
      [plan 15](./15-measured-fill-and-impact-calibration.md) (measured impact coefficient).

---

## 4. Pillar 4 — Infrastructure & operations

*Capture is the easy 10%. Real-money operation is the rest.*

### Phase 4.1 — Unattended capture ✔️ running

*Closed 2026-09-04 by an S3 audit of `marketdata-047915756052`: 58 date
partitions 2026-06-17 → 2026-09-04, 514 segment pairs, 18.84M data records,
13.93 GB uncompressed. Every segment has a matching manifest; no zero-byte
objects. See [plan 12](./12-data-gap-backlog-and-demand-driven-capture.md) §8.4
for the durable inventory this should become.*

- [x] Capture loop + segment storage + S3 ship — [plan 01](./01-market-data-capture.md) / [02](./02-storage-format-and-replay.md)
- [x] **EventBridge start/stop** around IDX hours *(Verified 2026-09-04 — every
      session from 2026-06-22 on runs 01:31→09:25 UTC (08:31→16:25 WIB) with
      zero inter-segment gaps >60s. Note the original parenthetical is stale:
      segments now rotate **hourly** (~9/day), not only at midnight/clean stop.)*
- [x] **Token rotation health** — [plan 03](./03-token-rotation.md) live and
      self-healing, or an unattended multi-day run dies on auth mid-week.
      *(Verified 2026-09-04 — 53 consecutive unattended sessions over ~2.5
      months with no auth-related loss. Rotation is working in production.)*
- [x] **Verify it is actually accruing data** before counting on "just wait."
      *(Done 2026-09-04 — audited; see the note above. Also closes the second
      ground truth [plan 14](./14-market-calendar-and-time-system.md) §7.9 asked
      for: the calendar predicts exactly two closures in the window
      (2026-08-17 Proklamasi Kemerdekaan, 2026-08-25 Maulid Nabi Muhammad SAW)
      and capture went silent on exactly those two days — zero divergence over
      55 partitions.)*

**Two defects the audit surfaced, both open.** Both are now scheduled as
§6 Wave 0 items (W0.4 and W0.3) — they are deadline-driven, not merely known:

- [ ] **Deployed collector binary is stale.** `s3://marketdata-047915756052/artifacts/collector`
      was last uploaded 2026-06-19 18:46; the non-trading-day self-skip
      ([plan 14](./14-market-calendar-and-time-system.md) §7.5) landed in commit
      `261c93c` on 2026-06-26. That is why the collector booted and shipped 9
      heartbeat segments on both 2026-08-17 and 2026-08-25 instead of skipping.
      Harmless (~30 KB/day) but the running binary is ~2.5 months behind `main`.
- [ ] **Lifecycle sends `raw/` to DEEP_ARCHIVE at 90 days**, so the first objects
      lock 2026-09-15 and the `plan10-survivors` series from 2026-09-20 (12–48 h
      restore + 180-day minimum billing). Costed 2026-09-04: the transition saves
      only ≈ $0.33/yr and bills the 709 sub-128 KB manifests at ~3× their current
      footprint. Scheduled as §6 **W0.3** — drop it, keep STANDARD_IA at 30 days.

### Phase 4.2 — Live trading runtime (none)

- [ ] **Event-driven runtime** — signal → sizing → risk → order, in real time (ties to pillar 2).
- [ ] **Monitoring & alerting** — connection, order rejects, PnL-vs-expectation,
      within seconds (not discovered at EOD).
- [ ] **Reconciliation** — our view of positions/cash vs. the broker's, daily.
- [ ] **Logging & post-trade store** — every decision + fill, for attribution and decay.
      → [plan 17](./17-execution-and-replay-parity-engine.md) §5.4 (append-only store; **unblocks [plan 11](./11-decay-monitoring-and-live-revalidation.md)**).
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

## 6. Sequencing — waves of execution

*Re-sequenced 2026-09-04 after a robustness review of the whole stack. The
original order (screen → capture → find alpha → build machinery) was correct for
where we stood at plan 06, and it is now spent: the screen ran, capture is
running, and [plan 10](./10-strategy-fit-and-portfolio.md) reported a PASS. The
review found the binding constraint has moved. It is no longer "is there an
edge" — it is that **the substrate the edge was measured on is stale, narrow and
unmonitored**, so the PASS cannot be trusted either way. The waves below reorder
around that.*

### The state that forced the reorder

```
✔️ Capture running unattended      53 clean sessions, 2026-06-22 → today
✔️ Universe screened (Tier A)      plan 09
✔️ Strategy grid says PASS         plan 10 — but on 5 days, 9/11 cells OOS eff-N = 0
🔴 Tick replay says LOSS           plan 17 §7.8 — −76 bps mid / −112 bps taker
🔴 Research substrate stale        chartbit frozen at 2026-06-19; survivors dir = 5 days
🔴 Deployed binary 2.5 months old  no hash check, no zero-record alert
```

**The contradiction is the whole problem.** The CSV/daily harness and the only
end-to-end test that has walked the tape disagree about the same 7 names. Both
ran on ~5 days. Neither is currently worth believing, and no amount of additional
statistical machinery resolves it — only more and better-plumbed data does.

### The ordering principle

Waves are ordered by **cost of delay**, not by dependency alone.

- **Wave 0** items get *permanently worse* if deferred — data that cannot be
  backfilled at any price. They are not the most important work; they are the
  only work with a deadline set by someone other than us.
- **Waves 1–2** are a straight line to a trustworthy verdict.
- **Waves 3–5** stay exactly where the original roadmap put them, behind the
  edge gate. Nothing below Wave 2 should start early.

```
   ┌─ Wave 0  Stop irreversible loss        ← deadline-driven, days
   │            (chartbit re-pull · widen capture · lifecycle · redeploy)
   ▼
   ┌─ Wave 1  Make the substrate trustworthy
   │            (segments→bars · data inventory + freshness · deploy self-checks)
   ▼
   ┌─ Wave 2  Make the verdict trustworthy   ← THE GATE, replaces old step 3
   │            (blocking gates FIRST, then one clean re-run of 15 → 10 → 17 §7.8)
   │            if the harness and the tape still disagree → stop, do not proceed
   ▼
   ┌─ Wave 3  Portfolio reality (pillar 3)
   ▼
   ┌─ Wave 4  Execution + paper forward test (pillars 2 / 5)
   ▼
   └─ Wave 5  Live tiny → scale on realized PnL; decay loop forever (plan 11)
```

---

### Wave 0 — Stop irreversible loss *(this week)*

*Every item here is losing something that cannot be bought back later. Ordered by
deadline.*

- [ ] **W0.1 — Chartbit intraday re-pull.** Due **2026-09-10** (`calendar/deadlines.yaml`).
      The chartbit endpoint serves a rolling ~90-day window; `research/data/intraday/`
      is frozen at 2026-06-19, so the 2026-03-25 → 06-19 history is the only copy
      that will ever exist. Run `python -m screener.intraday_store snapshot` with
      `SB_BEARER_TOKEN` in the **environment** (never argv). Blocked only on a live
      token. Add BREN, RAJA and RATU while there — they were never in the store, so
      they can only reach back ~90 days, but that aligns it with the capture universe.
      Then convert this deadline to **recurring**, since the 90-day clock restarts.
- [ ] **W0.2 — Widen the capture watchlist.** The collector records only the 7 names
      that already passed plan 10 (`infra/terraform.tfvars`). Order book cannot be
      backfilled, so every further session on the narrow list is permanently lost
      breadth, *and* every forward result is conditioned on names selected from the
      same period — the selection bias plan 09 exists to remove has re-entered
      through the capture layer. Add the screener top-N plus a **random control
      slice**. Note the coupling: changing the set changes `watchlist_id`, which
      changes the S3 key prefix — keep the 7 survivors in the set and teach the
      research readers to span both ids, or the series forks.
- [ ] **W0.3 — Drop the `raw/` DEEP_ARCHIVE transition** (`infra/s3.tf:68`). First
      objects lock **2026-09-15** (2026-06-17 + 90 d); the `plan10-survivors` series
      starts **2026-09-20**. *This is a deadline item, not a cost item* — measured
      2026-09-04, the transition saves ≈ **$0.33/yr** (0.91 GB today, ~3.9 GB/yr
      projected at 15.6 MB/day; IA ≈ $0.054/mo vs DA ≈ $0.007/mo, less ~$0.23/yr in
      per-object transition requests). It is worth doing anyway because:
      - **Restore latency lands on exactly what Wave 2 re-reads** — 12–48 h per
        interactive pass over the full window.
      - **180-day minimum billing makes it hard to undo.** Free to prevent before
        2026-09-15; costs a restore + re-upload cycle after.
      - **It is counterproductive for most objects.** 709 of 1028 objects are <128 KB
        (the manifests, ~10 MB total) — too small to have transitioned to IA, which is
        why 832 still sit in STANDARD. Deep Archive bills +32 KB/object at archive
        rates and +8 KB/object at Standard rates, so the manifests would occupy **~3×
        more billed storage archived than they do now** — and manifests are the index
        you scan first to find a session.

      Keep STANDARD_IA at 30 days. If an archive tier is wanted on principle, push it
      to 365 days rather than deleting it. Re-enable bucket versioning at the same
      time (currently Suspended).
- [ ] **W0.4 — Redeploy the collector.** `artifacts/collector` in S3 is from
      2026-06-19; the holiday self-skip landed 2026-06-26 (`261c93c`). Low harm today,
      but it means *the box has never once run current `main`* — every operational
      conclusion drawn from it is about code that is 2.5 months old.

### Wave 1 — Make the substrate trustworthy

*The review's core finding: there is capture but no pipeline. 0.91 GB of ticks
sits in S3 and research consumed five days of it. Nothing turns segments into
research inputs, and nothing notices when an input goes stale — the chartbit
store was dead for ten weeks and an ad-hoc audit found it, not the system.*

- [ ] **W1.1 — Segments → bars builder.** There is no path from captured protobuf to
      a research-ready series. `sbcap` has `stat / dump / export / book / features /
      trades` and no `bars`; the 1-minute bars in `research/data/intraday/` come from
      a *different substrate* (the chartbit HTTP endpoint). Build the missing edge —
      a `bars` subcommand or a Python job over `export` — so the 53 captured sessions
      become usable input. **This single piece unblocks plans 10, 15 and 17
      simultaneously**, and is the reason all three are stuck on a 5-day sample.
- [ ] **W1.2 — Durable data inventory + freshness checks.** Build the
      [plan 12](./12-data-gap-backlog-and-demand-driven-capture.md) §8.4 inventory
      (names × date-ranges × granularity × fields) for real and wire a staleness
      assertion into `calendar/check_deadlines.py`. Today `DataInventory` in
      `research/strategy_fit/triage_scorer.py:35` is a frozen dataclass of
      hand-written priors that still claims `intraday_trading_days_survivors = 5` and
      `universe_size = 5` — **the triage brain is ranking families against a data
      picture that is ten weeks out of date**, and nothing in the system can tell.
- [ ] **W1.3 — Deployment self-checks.** Assert deployed binary hash vs. `main` in the
      health check; alert on a **zero-record non-holiday session**. W0.4 fixes today's
      instance of a stale deploy; this is what makes the next one impossible to miss.

### Wave 2 — Make the verdict trustworthy *(the gate)*

*This replaces the original step 3, "find alpha that clears 40 bps." That step
nominally succeeded and the result did not survive contact with the tape. The
gate is now agreement, not a PASS.*

- [ ] **W2.1 — Make the gates blocking, before re-running anything.** Ordered first
      on purpose: re-running under today's semantics just manufactures another
      unreliable PASS. A gate that annotates but cannot change the verdict is a
      footnote — `strategy_portfolio.md` currently reports **PASS** while 9 of 11
      cells carry `OOS Eff-N = 0` and the other 2 carry `Impact 0.0`.
      - OOS effective-N below floor → **INCONCLUSIVE**, never PASS.
      - A cell whose impact was never priced (`impact.py` covers same-day cells only)
        → **excluded**, not passed with a zero.
      - Derive the OOS floor from the cost gate and observed per-trade σ.
        `MIN_OOS_EFF_N = 10` is a not-zero check, not a power calculation: seeing
        20 bps at 2σ against a ~150 bps per-trade spread needs on the order of **225**
        round-trips, and the full 53-session re-run yields roughly **200–370** before
        the 60/40 split. Expect "underpowered" to be an honest, common verdict.
- [ ] **W2.2 — Re-run plan 15 impact calibration** on the full window. Current coeffs
      come from 5 days and the report itself warns the thin vol estimate *deflates*
      them.
- [ ] **W2.3 — Re-run the plan 10 grid + intraday walk-forward** on the rebuilt
      substrate, under W2.1 semantics.
- [ ] **W2.4 — Re-run plan 17 §7.8 replay parity — the arbiter.** 21 round-trips over
      5 days becomes ~200 over 53. This is the number that decides.

> **Wave 2 exit criterion.** The daily harness and the tick replay must agree on
> the same names over the same window. If they still disagree, **stop and fix the
> disagreement** — do not proceed to Wave 3 on the strength of whichever result is
> more convenient. If they agree that there is no edge, that is a successful
> outcome of this wave and the correct place to kill the strategy family, exactly
> as plan 06 was killed.

### Wave 3 — Portfolio reality *(pillar 3 — only if Wave 2 clears)*

- [ ] **W3.1 — Model cross-cell correlation.** The 11 survivors are 2 families on
      6 names, same session, long-only, one sleeve, one atomic basket per cycle.
      That is one bet wearing eleven hats; nothing in plan 10 or 13 currently
      estimates the correlation between cells.
- [ ] **W3.2 — Per-cycle loss limit in [plan 13](./13-capital-allocation-and-capacity.md).**
      A hard per-basket stop that does **not** wait for plan 11 — the sleeve can
      trade before the decay loop exists, so it needs a floor that exists before it.
- [ ] **W3.3 — C\* capacity caps** from the W2.2 measured coefficients.
- [ ] Position sizing, portfolio construction, regime/global off-switch (§3).

### Wave 4 — Execution + paper forward test *(pillars 2 / 5)*

- [ ] **W4.1 — [Plan 17](./17-execution-and-replay-parity-engine.md) §7.11–7.16**,
      still behind the §2 three-condition gate. No HTTP order call enters the binary
      until Wave 2 clears and the paper test runs.
- [ ] **W4.2 — Wire the plan-14 calendar reader into `strategy/engine/`.**
      `hold_calendar_days` is a schema column with no calendar import behind it; the
      caller supplies the value (plan 17 §7.4 deviation).
- [ ] **W4.3 — Paper trade in wall-clock**, forward, no money. Condition 2 of the gate.
- [ ] Order lifecycle, pre-trade risk checks in code, reconciliation, failover (§2, §4.2).

### Wave 5 — Live, small, then scale

- [ ] Tiny size → scale **only on realized PnL**, never on backtested PnL.
- [ ] **[Plan 11](./11-decay-monitoring-and-live-revalidation.md)** decay loop and
      kill criteria — condition 3 of the plan-17 gate, and then forever.

---

**What changed and why.** The original sequencing said: prove an edge, then build
machinery. That was right and it still is — Waves 3–5 have not moved. What the
review changed is the recognition that *we do not yet know whether step 3
succeeded*, because both answers came off a five-day sample drawn from a
seven-name universe that those same names were selected into. Waves 0–2 exist to
produce one trustworthy answer to that question. The single riskiest thing this
project could do now is add more statistical machinery on top of a substrate that
went stale for ten weeks without anyone noticing.

## 7. Immediate next actions

1. **Run the chartbit snapshot** (W0.1) — hard deadline 2026-09-10, six days out,
   blocked only on `SB_BEARER_TOKEN`. Missing it costs the March–June intraday
   history permanently.
2. **Widen the capture watchlist** (W0.2) — every additional session on the
   7-survivor list is book breadth that cannot be recovered at any price.
3. **Build the segments→bars edge** (W1.1) — the one piece that unblocks plans
   10, 15 and 17 at once, and the precondition for the Wave 2 re-run.

---

Back to [Plans index](./README.md)
