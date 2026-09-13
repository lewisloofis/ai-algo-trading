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
      (pillar 4.1). The data became reachable by research on 2026-09-04 (§6 W1.1).
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

- [x] **Deployed collector binary is stale.** `s3://marketdata-047915756052/artifacts/collector`
      was last uploaded 2026-06-19 18:46; the non-trading-day self-skip
      ([plan 14](./14-market-calendar-and-time-system.md) §7.5) landed in commit
      `261c93c` on 2026-06-26. That is why the collector booted and shipped 9
      heartbeat segments on both 2026-08-17 and 2026-08-25 instead of skipping.
      Harmless (~30 KB/day) but the running binary is ~2.5 months behind `main`.
      *(Fixed 2026-09-05 — see §6 W0.4. Health check now reads 0.0d.)*
- [x] **Lifecycle sends `raw/` to DEEP_ARCHIVE at 90 days**, so the first objects
      lock 2026-09-15 and the `plan10-survivors` series from 2026-09-20 (12–48 h
      restore + 180-day minimum billing). Costed 2026-09-04: the transition saves
      only ≈ $0.33/yr and bills the 709 sub-128 KB manifests at ~3× their current
      footprint. Scheduled as §6 **W0.3** — drop it, keep STANDARD_IA at 30 days.
      *(Done 2026-09-05: transition removed, versioning enabled, zero objects had
      locked. See §6 W0.3 for the SSM landmine the apply surfaced.)*

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

- [x] **W0.1 — Chartbit intraday re-pull.** Due **2026-09-10** (`calendar/deadlines.yaml`).
      The chartbit endpoint serves a rolling ~90-day window; `research/data/intraday/`
      is frozen at 2026-06-19, so the 2026-03-25 → 06-19 history is the only copy
      that will ever exist. Run `python -m screener.intraday_store snapshot` with
      `SB_BEARER_TOKEN` in the **environment** (never argv). Blocked only on a live
      token. Add BREN, RAJA and RATU while there — they were never in the store, so
      they can only reach back ~90 days, but that aligns it with the capture universe.
      Then convert this deadline to **recurring**, since the 90-day clock restarts.

      > ✔️ Done 2026-09-04 — store now spans 2026-03-25 → 2026-09-04
      > (PTRO/BRPT/TPIA/ENRG 55 → 108 trading days; BREN/RAJA/RATU seeded at 62), with
      > no gap at the 06-19 junction. Deadline rolled to 2026-11-26. Three latent
      > defects blocked the pull: `pyarrow` and `certifi` missing from
      > `requirements.txt`, and an empty TLS trust store presenting as "self-signed
      > certificate in certificate chain" — which reads like a MITM proxy and means
      > "root not trusted".

- [x] **W0.2 — Widen the capture watchlist.** The collector records only the 7 names
      that already passed plan 10 (`infra/terraform.tfvars`). Order book cannot be
      backfilled, so every further session on the narrow list is permanently lost
      breadth, *and* every forward result is conditioned on names selected from the
      same period — the selection bias plan 09 exists to remove has re-entered
      through the capture layer. Add the screener top-N plus a **random control
      slice**. Note the coupling: changing the set changes `watchlist_id`, which
      changes the S3 key prefix — keep the 7 survivors in the set and teach the
      research readers to span both ids, or the series forks.

      > ✔️ Done 2026-09-05 — **7 → 47 names**, `watchlist_id` `plan10-survivors` →
      > `w02-widened`. Applied in two steps the same day: 43 symbols first (SSM watchlist
      > v4→v5, watchlist_id v3→v4), then **BNBR, VKTR, TINS, SSIA** added (watchlist
      > v5→v6). `watchlist_id` was deliberately **not** bumped for the second step because
      > the 43-name set had captured nothing — a live S3 check found **zero objects** under
      > `w02-widened` — so the id still maps to exactly one symbol set in the data. The box
      > is stopped and the market closed, so it takes effect on Monday 2026-09-07's
      > 01:30 UTC boot.
      >
      > The four added names are the plan-10 cells that clear the cost gate and are
      > FDR-accepted but blocked on `impact_not_priced`, and that had **no tick capture at
      > all** — so plan 13's daily impact model could only ever have priced them with the
      > `high_liq` bucket coefficient (0.377) fit on TPIA/BREN/BRPT/PTRO/ENRG/RATU. BNBR is
      > the sharpest case: sub-Rp-100, ~170 bps friction hurdle, and the single largest
      > blocked cell in the grid. Borrowing a large-cap coefficient for it would be a guess
      > wearing a decimal point.
      > Proposal and sampling procedure in
      > [plan 12 §5c](./12-data-gap-backlog-and-demand-driven-capture.md): all 7
      > survivors kept unconditionally, + the 30-name screener slice, + a **10-name
      > random control slice** (`random.Random(20260620)` over the 81 unselected names
      > in the 114-name funding universe). The control slice is the substantive part —
      > survivors were chosen by a backtest grid and the screener slice by a liquidity
      > rule, so a forward result on either alone cannot separate real edge from the
      > selection process.
      >
      > **The fork precondition was verified before applying, not assumed.**
      > `research/lib/data_inventory.py:scan_segments()` traverses all four ids present
      > locally (`default`, `lq45-mining`, `mining-sample`, `plan10-survivors`) and
      > `symbol_coverage()` unions across them — BUMI resolves as one symbol spanning
      > three ids. `screener/tick_bars.py` carries no hardcoded id. One real fork bug
      > was found and fixed on the way: `research/data/extract_survivors.sh` hardcoded
      > `WATCHLIST="plan10-survivors"` and would have silently dropped every date
      > captured under the new id.
      >
      > **Left on t4g.nano deliberately.** The capacity question is real — the 36 added
      > names include BBCA/BBRI/BMRI/ASII/TLKM, among the most heavily traded on IDX —
      > but the frame-loss measurement below ([plan 12 §5d](./12-data-gap-backlog-and-demand-driven-capture.md))
      > shows the collector is **not** the bottleneck at 7 symbols: loss tracks each
      > symbol's own update rate, not process load. **The first widened session (Monday
      > 2026-09-07, checked Tuesday 2026-09-08) needs an explicit check** — `scripts/collector_healthcheck.py` catches a zero-record
      > session but NOT partial degradation, so re-run the §5d sequence-gap analysis on
      > the first `w02-widened` session and compare the survivors' loss rates against
      > the 2026-09-04 baseline (RATU −1.1% … TPIA 20.6%). If they worsen materially,
      > the collector *is* saturating at 47 and the box needs resizing. Rollback is
      > `git checkout infra/terraform.tfvars && tofu apply`.
- [x] **W0.3 — Drop the `raw/` DEEP_ARCHIVE transition** (`infra/s3.tf:68`). First
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

      > ✔️ Done 2026-09-05 — applied and verified against live S3, with 10 days of
      > margin. Lifecycle now carries a single `STANDARD_IA @ 30d` transition; a
      > storage-class census over `raw/` reads **832 STANDARD + 196 STANDARD_IA, zero
      > DEEP_ARCHIVE**, so nothing had locked and this cost nothing to prevent.
      > Versioning **Suspended → Enabled**, and `noncurrent_version_expiration` raised
      > 1 d → 30 d: 1 day was the leftover purge setting from the Suspended era and
      > would have been a 24-hour hole in the new undo window. Cost is ~0 because
      > `raw/` is append-only under unique keys. The rule is scoped `prefix = "raw/"`,
      > so `artifacts/` versions are kept indefinitely — which is what makes the W0.4
      > binary swap reversible, and is why W0.3 was done first.
      >
      > **The apply was not routine.** An untargeted `tofu plan` read `3 to add`:
      > `infra/ssm.tf` declared `/sb-collector/{username,password,player_id}` as managed
      > resources with `value = "PLACEHOLDER_SEED_OUT_OF_BAND"`, but all three hold real
      > hand-seeded values in AWS and were **never in state** — `742d7e2` (2026-06-19
      > 18:42) added the resources ~8 minutes *after* the values were seeded by hand,
      > and no apply has run in the 78 days since. Applying would have overwritten the
      > live Stockbit credentials and the server-trusted `player_id` with placeholders.
      > `lifecycle { ignore_changes = [value] }` was on all three and does **not**
      > protect — it applies to updates of an already-managed resource, never to create.
      > That is what made it invisible: the guard reads as protection. W0.3 was landed
      > with `-target` on the two S3 resources only, then the three were deleted from
      > `ssm.tf` (out-of-band is their correct lifecycle; `tofu import` would have
      > pulled real secrets into state, breaking that file's own stated invariant).
      > A full untargeted plan is now **clean**, so the next apply by anyone is safe.
- [x] **W0.4 — Redeploy the collector.** `artifacts/collector` in S3 is from
      2026-06-19; the holiday self-skip landed 2026-06-26 (`261c93c`). Low harm today,
      but it means *the box has never once run current `main`* — every operational
      conclusion drawn from it is about code that is 2.5 months old.

      > ✔️ Done 2026-09-05 — uploaded on a Saturday with the box stopped and the
      > market closed, so the swap lands on Monday's 01:30 UTC boot with a full
      > weekend of margin. `scripts/collector_healthcheck.py` now reads **0.0d ago**
      > (was 77.2d) and exits 0; 20/20 trading days OK, both holidays correctly
      > skipped. Built from a **clean detached worktree at `0fb9c0d`** with
      > `-trimpath`, sha256 `05f91ca0…`, round-tripped from S3 to confirm the bytes.
      > The old artifact is recoverable twice over: an explicit
      > `artifacts/collector.bak-20260905` key (following the `collector.bak-20260618`
      > precedent) and, now that W0.3 enabled versioning, as a noncurrent version —
      > the upload returned a real `VersionId`.
      >
      > Three things the build surfaced that are worth keeping:
      > - **Provenance cannot live in the binary here.** Go omits `vcs.*` stamping
      >   when building from a git worktree (`.git` is a pointer file it will not
      >   follow), and building from the main tree instead would stamp
      >   `vcs.modified=true`. So provenance travels in **S3 object metadata**
      >   (`git-commit`, `sha256`, `goos`, `goarch`, `built-from`), which is better
      >   anyway — `head-object` reads it without downloading 12 MB.
      > - **The build is not path-reproducible**, even with
      >   `-trimpath -ldflags="-s -w -buildid="`; ~2.5 MB of 8.5 MB still differs
      >   across checkout locations, root cause not isolated. This is why W1.3's
      >   health check scores artifact **age** and treats hash agreement as
      >   informational — that design is correct, not merely cautious.
      > - **`update.sh` has no integrity check.** `infra/user_data.sh.tftpl` does
      >   `aws s3 cp` → `chmod 0755` → `mv` with no checksum or signature. It falls
      >   back to the last-known-good binary on fetch *failure*, but a *successful*
      >   fetch of a wrong-architecture or corrupt object would be executed on the
      >   next boot. The `sha256` now in object metadata is the material for a fix;
      >   the fix itself is not yet written. **Open.**
      >
      > Note for W0.2: the watchlist is **not** baked into the binary —
      > `collector/cmd/collector/watchlist.go:23` reads `<ssm_prefix>/watchlist` from
      > SSM, falling back to `CAPTURE_WATCHLIST`. Widening the capture set is a
      > config change and needs no redeploy.

### Wave 1 — Make the substrate trustworthy

*The review's core finding: there is capture but no pipeline. 0.91 GB of ticks
sits in S3 and research consumed five days of it. Nothing turns segments into
research inputs, and nothing notices when an input goes stale — the chartbit
store was dead for ten weeks and an ad-hoc audit found it, not the system.*

- [x] **W1.1 — Segments → bars builder.** There is no path from captured protobuf to
      a research-ready series. `sbcap` has `stat / dump / export / book / features /
      trades` and no `bars`; the 1-minute bars in `research/data/intraday/` come from
      a *different substrate* (the chartbit HTTP endpoint). Build the missing edge —
      a `bars` subcommand or a Python job over `export` — so the 53 captured sessions
      become usable input. **This single piece unblocks plans 10, 15 and 17
      simultaneously**, and is the reason all three are stuck on a 5-day sample.

      > ✔️ Done 2026-09-04 — `sbcap bars` plus `screener/tick_bars.py`, building two
      > stores: `data/tick_bars/` (full tape) and `data/tick_bars_tagged/` (book-
      > accessible, the default for anything keyed on a price level). Validated
      > against the independent chartbit substrate: OHLC 96–99 % exact across 4
      > symbols × 5 sessions. **Capture fidelity is not uniform** — see [plan 12
      > §5b](./12-data-gap-backlog-and-demand-driven-capture.md). Volume loss is
      > concentrated in the busiest bars, so volume- and flow-keyed families are
      > measured on biased data while price-keyed families are not.

- [x] **W1.2 — Durable data inventory + freshness checks.** Build the
      [plan 12](./12-data-gap-backlog-and-demand-driven-capture.md) §8.4 inventory
      (names × date-ranges × granularity × fields) for real and wire a staleness
      assertion into `calendar/check_deadlines.py`. Today `DataInventory` in
      `research/strategy_fit/triage_scorer.py:35` is a frozen dataclass of
      hand-written priors that still claims `intraday_trading_days_survivors = 5` and
      `universe_size = 5` — **the triage brain is ranking families against a data
      picture that is ten weeks out of date**, and nothing in the system can tell.

      > ✔️ Done 2026-09-04 — `research/lib/data_inventory.py` plus a freshness section
      > in `calendar/check_deadlines.py`. Measured 239/60/5/5 → **242/108/62/7**.
      > `daily_bar_trading_days` is a source *capability*, deliberately a distinct
      > type from on-disk coverage: deriving it from the intraday store gives 62,
      > because the 90-day rolling cap applies only to the intraday endpoint. Knock-
      > on: `event_driven` feasible N is 7×242/20 = **84** against the old prior's 59
      > — more feasible, not less.

- [x] **W1.3 — Deployment self-checks.** Assert deployed binary hash vs. `main` in the
      health check; alert on a **zero-record non-holiday session**. W0.4 fixes today's
      instance of a stale deploy; this is what makes the next one impossible to miss.

      > ✔️ Done 2026-09-04 — `scripts/collector_healthcheck.py`. The first real run
      > confirmed W0.4 with evidence: the S3 artifact is **77.2 days old**
      > (2026-06-19) while the collector itself is healthy (20/20 trading days
      > checked, 0 zero-record sessions). Capture has been running current data on
      > 2.5-month-old code.


### Wave 2 — Make the verdict trustworthy *(the gate)*

*This replaces the original step 3, "find alpha that clears 40 bps." That step
nominally succeeded and the result did not survive contact with the tape. The
gate is now agreement, not a PASS.*

- [x] **W2.1 — Make the gates blocking, before re-running anything.** Ordered first
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

      > ✔️ Done 2026-09-05 — `report.py` now gates PASS on `_is_pass_eligible`:
      > cost gate **and** FDR **and** positive net edge **and** `impact_priced`
      > **and** `_clears_oos_power_gate`. `impact_priced` is an explicit boolean, so
      > "impact was never priced" is no longer indistinguishable from "impact was
      > measured at 0.0" — that conflation is what carried the two daily survivors.
      > `oos_eff_n is None` correctly means "this shape has no OOS split", so the
      > gate does not fire on daily/event/cross/pairs cells that never had one.
      >
      > **Effect on the standing table, traced cell by cell:** the 9 intraday cells
      > all carry `OOS Eff-N = 0 < MIN_OOS_EFF_N(10)` and the 2 daily cells never set
      > `impact_priced`. Those 11 were the *entire* set that previously had
      > `passes_cost_gate AND fdr_accepted`, so nothing else can newly qualify.
      > **Verdict flips PASS → INCONCLUSIVE with an empty survivor table.**
      >
      > **The power floor is derived, not asserted.** `lib/stats.required_effective_n`
      > implements N ≥ (z·σ/Δ)², and σ is `per_trade_sigma_bps` — the sample std of
      > the *same* signed-returns array that produces `gross_edge_bps` and `eff_n`.
      > Never a proxy: an initial wiring to `impact.compute_daily_vol_bps`
      > (unconditional close-to-close vol) was removed, because ORB enters only after
      > the opening range forms and is exposed to less than the full day's variance.
      > "Sigma unavailable" is an explicit state that suppresses the power verdict
      > rather than substituting a default. A cell clearing the hard floor but missing
      > the power-derived N reports **UNDERPOWERED** — an honest verdict, not a
      > pipeline failure.
      >
      > Three real bugs were found while wiring this, none of them cosmetic:
      > - **`apply_fdr_to_grid` was dropping fields.** It rebuilt `CellResult` by
      >   enumerating fields by hand, so every field added since was silently reset to
      >   its default. Uniquely dangerous there: that function's input is exactly the
      >   cost-gate-passing pool that can become survivors, and `impact_priced`
      >   defaults to False — so it would have permanently blocked every real intraday
      >   cell from PASS, with no error anywhere. Now `dataclasses.replace`, which
      >   copies every field by construction so the next field added cannot
      >   reintroduce it.
      > - **The permutation p-value degenerates under long-only.**
      >   `bootstrap_hit_rate_pvalue` permutes the *signal array* and recomputes the
      >   hit rate. Once signals are filtered to long-only they are all positive, so
      >   no permutation changes any sign, every draw reproduces the observed hit rate,
      >   and it returns **exactly p = 1.0 regardless of the data** — verified against
      >   an injected +20 bps edge over 200 observations (p=1.0 long-only vs p=0.71
      >   with mixed signs). Both call sites moved to the one-sample resample test the
      >   ORB family already used.
      > - **`impact_sweep.py` crashed on a never-cost-gated cell.** It reconstructed
      >   `floor = min_edge_hurdle_bps − modelled_impact_bps`, but `_run_intraday_cell`'s
      >   non-tested branch sets impact (computed before the family runs) and leaves
      >   the hurdle None. The line already carried a `# type: ignore[operator]` — the
      >   type checker had flagged it and it was silenced. Latent for months; made
      >   reachable by long-only halving `eff_n`. Guarded and regression-tested.
- [x] **W2.2 — Re-run plan 15 impact calibration** on the full window. Current coeffs
      come from 5 days and the report itself warns the thin vol estimate *deflates*
      them.

      > ✔️ Done 2026-09-05 — re-extracted the plan-15 inputs from **5 days → 58** (the
      > extraction was still the original 5-day sample; `research/data/survivors/` is now
      > 2.7 GB) and refit. Bucket effective-N **6,602 → 40,351**.
      >
      > **Plan 15's own warning is confirmed: the 5-day coefficients were deflated.**
      > Six of seven names rose — TPIA 0.313 → 0.471 (1.51x), RATU 0.199 → 0.298 (1.50x),
      > BRPT 0.302 → 0.409 (1.36x), PTRO 0.327 → 0.393 (1.20x), BREN 0.362 → 0.417,
      > ENRG 0.344 → 0.370. `high_liq` bucket 0.278 → **0.377**.
      >
      > **But the headline cuts the other way, and it must be said plainly: the measured
      > coefficient is ~0.38 against the assumed `SQRT_IMPACT_COEFF = 1.0`.** The
      > original PASS was *over*-charging impact by ~2.6x. Impact scales linearly in the
      > coefficient, so re-pricing the surviving cells at measured impact roughly halves
      > their impact charge — worth 6–24 bps each. **It rescues nothing.** Against the
      > out-of-sample gross edge (§6 Wave 2 exit):
      >
      > | Cell | impact 1.0 → measured | hurdle | OOS gross | net |
      > |------|----------------------:|-------:|----------:|----:|
      > | PTRO | 13.1 → 5.2  | 71.0 | −6.6 | **−77.6** |
      > | TPIA | 20.8 → 9.8  | 77.7 | −0.7 | **−78.4** |
      > | BRPT | 12.4 → 5.1  | 79.3 | +0.7 | **−78.6** |
      > | ENRG | 20.9 → 7.7  | 95.8 | −0.8 | **−96.6** |
      > | RATU | 34.8 → 10.4 | 72.1 | +17.6 | **−54.5** |
      >
      > Every cell still misses by **55–97 bps**. The kill in §6 is robust to the most
      > favourable impact assumption the data supports — which is the right way to test
      > a kill, and the reason this re-run was worth doing even though the family is dead.
      >
      > **One coefficient is unusable and the harness caught it itself.** RAJA fit at
      > 0.0603, outside the sane [0.2, 2.0] band, and the calibration's own §8
      > direction-check flagged it. Root cause: an unadjusted **1:5.26 corporate action
      > on 2026-07-16** that is present in the raw capture and back-adjusted away in
      > chartbit, inflating RAJA's close-to-close vol to 2340 bps against a true ~380.
      > See [plan 12 §5e](./12-data-gap-backlog-and-demand-driven-capture.md) — the two
      > substrates are **not** interchangeable across days, only intraday.
- [x] **W2.3 — Re-run the plan 10 grid + intraday walk-forward** on the rebuilt
      substrate, under W2.1 semantics.

      > ✔️ Done 2026-09-05 — live, 115-name funding universe, `--use-calibrated`
      > (measured impact ~0.38, **not** the assumed 1.0 — deliberately the most
      > favourable pricing the data supports) and `--intraday-store-dir data/intraday`.
      > **Verdict: INCONCLUSIVE**, empty survivor table, exactly as W2.1 predicted.
      >
      > **1,392 cells. The cost gate does almost all the work:** 836 tested, of which
      > **819 are cost-gated outright** — 98 % of everything tested cannot clear
      > min-edge + impact + financing. Plus 345 `skipped_horizon` (hold > 2 days,
      > capital-blocked by ADR-0011), 194 `insufficient_n`, 11 `decayed`,
      > 4 `long_short_infeasible`, 2 `data_blocked`.
      >
      > **Only 17 cells pass the cost gate, and 13 of them are blocked on ONE missing
      > piece — `impact.py` does not model the daily shape.** These are FDR-accepted at
      > α=0.10, not marginal.
      >
      > ⚠️ **Superseded 2026-09-05 by the ADR-0014 financing correction** — the counts
      > and net-bps figures in this block were computed on the trade-date-anchored
      > settlement clock, which overcharged financing by 10 bps on every hold ≥ 1 cell.
      > Re-run on the corrected clock, same universe and same 1,390 coverage rows:
      > **815 cost-gated, 21 clear the cost gate, and 17 are FDR-accepted and blocked
      > solely on `impact_not_priced`.** Every net-bps figure below is 10 bps low.
      > The table is kept as the W2.3 record; the current numbers are in §7.
      >
      > | Cell | hold | net bps (as run) | p | blocked by |
      > |------|-----:|--------:|--:|------------|
      > | BNBR daily_momentum | 2 | 159.7 | 0.001 | impact_not_priced |
      > | WIFI daily_momentum | 2 |  51.9 | 0.006 | impact_not_priced |
      > | VKTR volume_breakout | 2 | 39.6 | 0.001 | impact_not_priced |
      > | PTRO daily_momentum | 2 |  39.0 | 0.010 | impact_not_priced |
      > | BREN daily_momentum | 2 |  28.1 | 0.037 | impact_not_priced |
      > | BUVA volume_breakout | 2 | 54.2 | 0.017 | impact_not_priced |
      > | TINS mean_reversion | 2 |  16.4 | 0.011 | impact_not_priced |
      > | …plus BNBR h1, RATU h1, SSIA, INCO, PTRO volume_breakout | | | | |
      >
      > **This is "blocked", not "killed", and the distinction matters.** The intraday
      > ORB/momentum family is dead on evidence (§6 exit criterion). The *daily* band has
      > never been judged at all, because impact was never modelled for it — the old
      > report passed those cells with an assumed `0.0`, which is the conflation W2.1
      > removed. **The single highest-value next action is therefore plan 13's daily
      > impact model**; without it 13 statistically-surviving cells are unjudgeable in
      > either direction.
      >
      > Treat every one of them as unproven, and BNBR especially: its friction hurdle is
      > **~170 bps** (a sub-Rp-100 name where one tick is a large fraction of price), so
      > 159.7 bps net implies ~330 bps gross. That is exactly the profile where impact at
      > 30M IDR notional is most likely to be the whole story. None of the 13 has an
      > out-of-sample split either — the daily shape uses a decay metric, not a
      > walk-forward — so the selection-bias lesson that killed ORB (§6) is **untested
      > here**, over a 115-name × several-family × several-hold grid.
      >
      > **✅ RESOLVED 2026-09-05 (evening) — see §7.** Plan 13's daily impact model
      > shipped and ran live; all of these cells are now judged and none remains
      > `impact_not_priced`. The daily shape also got the missing out-of-sample split
      > (`strategy_fit/daily_walk_forward.py`). Outcome: **3 survivors** (PTRO
      > `daily_momentum` h2, PTRO `volume_breakout` h2, TINS `mean_reversion` h2). The
      > BNBR warning immediately above was correct in full — pricing impact moved its
      > hurdle from ~170 bps to **459.2 bps** and its net to **−129.5 bps**. The new OOS
      > split separately killed BREN h2, RATU h1 and WIFI h2 on negative out-of-sample
      > edge, confirming the selection-bias lesson was indeed untested here and did
      > apply.
      >
      > Also settled: **cross-sectional and pairs are structurally infeasible** and now
      > say so instead of being scored. The best cointegrated pair (BUMI/DEWA) is
      > gross −100.0 bps at hold 1 anyway. All six pooled event cells are cost-gated
      > (−53 to −88 bps against hurdles of 103–113). The two intraday cells that clear
      > cost gate + FDR (DSSA `intraday_mean_reversion`, UNTR `orb`) are correctly
      > reported as **"invalid split: neither IS nor OOS is usable"** — W2.1's new
      > diagnostic, which previously would have vanished silently.
- [x] **W2.4 — Re-run plan 17 §7.8 replay parity.** 21 round-trips over
      5 days becomes ~200 over 53. This is the number that decides.

      > ✔️ Done 2026-09-05 — **319 round-trips over 53 sessions** (better than the ~200
      > predicted), taker fills, all 7 names:
      >
      > | Symbol | RTs | gross bps | net bps | win % |
      > |--------|----:|----------:|--------:|------:|
      > | ENRG | 46 | −86.2 | −126.0 | 34.8 |
      > | RAJA | 49 | −78.4 | −118.2 | 26.5 |
      > | BREN | 44 | −71.3 | −111.1 | 29.5 |
      > | TPIA | 46 | −60.2 | −100.1 | 32.6 |
      > | **ALL** | **319** | **−59.5** | **−99.4** | **32.6** |
      > | BRPT | 48 | −55.6 | −95.5 | 29.2 |
      > | RATU | 41 | −39.6 | −79.5 | 31.7 |
      > | PTRO | 45 | −21.7 | −61.7 | 44.4 |
      >
      > **Every symbol is negative gross**, i.e. before fees — the entry-to-exit price
      > move itself loses, and the ~40 bps fee is then added on top. Win rate 26–44 %,
      > every one below 50 %. The 5-day run's −112.3 bps taker was not a small-sample
      > artefact; on 15x the data it is −99.4.
      >
      > **Mid fills — the unachievable best case — do not rescue it either.** Re-run with
      > `-fill-mode mid`, same 319 round-trips: **−20.2 bps gross / −60.2 bps net**
      > (5-day run was −76.2). The ~39 bps gap between taker gross (−59.5) and mid gross
      > (−20.2) is the round-trip spread. Only 2 of 7 names turn gross-positive at mid
      > (PTRO +12.7, RATU +2.1) and **both are still net-negative** once the ~40 bps fee
      > lands — PTRO, the best cell in the best case, is −27.3 bps. A retail taker cannot
      > systematically fill at mid, so this is a bound the strategy could never actually
      > reach, and it loses anyway.
      >
      > | Fill mode | RTs | gross | net | 5-day equivalent |
      > |-----------|----:|------:|----:|-----------------:|
      > | taker | 319 | −59.5 | **−99.4** | −112.3 |
      > | mid   | 319 | −20.2 | **−60.2** | −76.2 |
      >
      > ⚠️ **But this is NOT a replay of the strategy the Python harness tested, and
      > §7.8 should stop being called "the arbiter" until that is fixed.** The two
      > implementations sharing the name "ORB" encode **different rules**:
      >
      > | | entry trigger | threshold |
      > |---|---|---|
      > | Go `strategy/strategies/orb/orb.go` | any liveprice **tick** | fixed **0.5 %** above the day's **first tick** (`ORBBreakoutPct = 0.005`, `st.openPx = price`) |
      > | Python `families_intraday.py` | 1-minute bar **close** | **high of the first 30 minutes** (`ORB_WINDOW_MINUTES = 30`) |
      >
      > One is "buy on a 0.5 % move off the open"; the other is "buy on a bar close above
      > the opening-range high". That is why the replay finds ~45 trades/symbol where the
      > bar-based family finds ~25. **Plan 17's stated purpose — run the *same*
      > `strategy.go` interface live and in replay — is not met at the strategy-definition
      > level**, which is a more serious gap than any number in the table above.
      >
      > What W2.4 therefore does and does not establish: it does **not** independently
      > validate the Python ORB result. It **does** show that a second, differently
      > parameterised breakout rule also loses heavily out-of-sample on the same tape.
      > Combined with the same-window cross-substrate test in §6 (which *does* hold the
      > rule fixed and varies only the substrate), the family is dead under **both**
      > definitions — which is stronger evidence than either alone, but arrived at by
      > accident rather than design. **Reconciling the two ORB definitions is open.**

> **Wave 2 exit criterion.** The daily harness and the tick replay must agree on
> the same names over the same window. If they still disagree, **stop and fix the
> disagreement** — do not proceed to Wave 3 on the strength of whichever result is
> more convenient. If they agree that there is no edge, that is a successful
> outcome of this wave and the correct place to kill the strategy family, exactly
> as plan 06 was killed.

> ### ✅ Wave 2 exit criterion MET, 2026-09-05 — they agree, and they agree there is no edge.
>
> **There was never a substrate disagreement.** §6 called the CSV-harness-PASS vs
> tick-replay-LOSS contradiction "the whole problem". It was an artefact of comparing
> different *windows*. Restricted to the same 53 sessions (2026-06-22 → 2026-09-04),
> ORB long-only, gross bps — tick-tagged vs chartbit:
>
> | Symbol | tick n | tick bps | chartbit n | chartbit bps |
> |--------|-------:|---------:|-----------:|-------------:|
> | PTRO   | 26 |  −7.6 | 26 |  −6.6 |
> | BRPT   | 28 | −18.4 | 28 |  +0.7 |
> | TPIA   | 24 |  +7.5 | 24 |  −0.7 |
> | ENRG   | 20 |  −4.3 | 20 |  −0.8 |
> | RAJA   | 25 | −13.9 | 23 |  −5.8 |
> | BREN   | 25 | −97.9 | 26 | −98.2 |
> | RATU   | 23 | +23.1 | 23 | +17.6 |
>
> Trade counts match **exactly on 5 of 7** (RAJA +2, BREN −1) — the two independent
> substrates select the same breakout *days*. Signs agree on 5 of 7. **5 of 7 are
> negative gross on both**, before the 40 bps round-trip and before impact. The residual
> per-symbol gaps (BRPT's 19 bps is the worst) are the ~16 % upstream conflation measured
> in [plan 12 §5d](./12-data-gap-backlog-and-demand-driven-capture.md) shifting which
> 1-minute bar first closes above the opening range.
>
> **The edge is entirely inside the window the names were selected on.** Splitting the
> chartbit history at the capture start date:
>
> | Symbol | early (< 06-22) | n | late (≥ 06-22) | n |
> |--------|----------------:|--:|---------------:|--:|
> | ENRG   |  **+94.2** | 25 |  −0.8 | 20 |
> | TPIA   |  **+54.0** | 20 |  −0.7 | 24 |
> | BRPT   |  **+38.0** | 27 |  +0.7 | 28 |
> | PTRO   |      −6.6  | 27 |  −6.6 | 26 |
> | RATU   |    +144.1  |  5 | +17.6 | 23 |
> | RAJA   |     +74.7  |  5 |  −5.8 | 23 |
> | BREN   |     +59.3  |  5 | −98.2 | 26 |
>
> Plan 10's live run was **2026-06-20**, against a chartbit store spanning 2026-03-25 →
> 06-19. Forward capture of exactly those seven survivors began **2026-06-22**. So
> "early" *is* the selection window and "late" *is* genuine out-of-sample. Of the four
> names with a real early sample, three (ENRG, TPIA, BRPT) show a large edge collapsing
> to ≈0 and the fourth (PTRO) was negative in both — it never had one. RAJA/BREN/RATU's
> early n=5 is meaningless; their chartbit history only reaches 2026-06-08.
> **This is textbook selection bias: the edge exists only in the window used to choose
> the names, and is absent out-of-sample.** It is the same failure the capture layer was
> re-introducing (W0.2), arriving through the research layer instead.
>
> **Three independent defects each inflated the original PASS**, and they compound:
> 1. **Unrealizable short trades** — the report (mtime 2026-06-27 22:37) predates
>    `c62eba8` (2026-06-29), which removed ORB's short leg because IDX retail cannot
>    short. Roughly half the trades behind it could not be executed.
> 2. **Non-blocking gates** — 9 of 11 cells carried `OOS Eff-N = 0` and the other 2 an
>    unpriced impact of 0.0, and neither could change the verdict (W2.1).
> 3. **In-sample evaluation** — the above.
>
> **ORB is also untestable on 53 sessions regardless of any of this.** It fires on
> ~45–50 % of days, so 53 sessions yields 20–28 trades per name — every one below
> `MIN_EFF_N = 30`. The tick substrate alone cannot test this family. That is a
> data-volume fact, not a verdict.
>
> **Conclusion: kill the intraday ORB / momentum family, exactly as plan 06 was killed.**
> Do not proceed to Wave 3 on it and do not re-tune thresholds to keep cells alive. The
> substrate is now trustworthy (Wave 1) and the verdict semantics are now trustworthy
> (W2.1); what they jointly report is *no edge*. The honest next uses of this substrate
> are a signal family that is not price-breakout-shaped, or a genuinely prospective test
> on names selected **before** the evaluation window.

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

*All of Wave 0 and Wave 1 are closed, and Wave 2 has returned its verdict
(§6 "Wave 2 exit criterion MET", 2026-09-05). **The intraday ORB / momentum family
is dead** — the edge existed only in the window the names were selected on, and the
two substrates agree on that out-of-sample. Wave 3 is explicitly NOT unblocked; the
gate it sits behind was not cleared, it was answered in the negative.*

**✅ The only dated item is closed.**

1. ~~**Verify the widened capture on Tuesday 2026-09-08**~~ — **done 2026-09-12**,
   four days late. All five `w02-widened` sessions (2026-09-07 → 09-11) are clean:
   47/47 symbols per manifest, 9 hourly segments, full 01:31→09:25 UTC span,
   `truncated: no`, 0 RECONNECT, 0 errors, 1.4–1.8M records/day.

   **The collector is not saturating at 47 symbols on a t4g.nano.** Per-symbol
   frame loss for the original 7, against the 2026-09-04 baseline, *improved*:
   aggregate 16.1% → 13.8%, with the two worst names at 7 symbols improving most
   (PTRO 19.3 → 12.4, TPIA 20.6 → 15.0). ENRG's +4.0pp mean is a single day
   (30.5% on 09-07 vs 11.4–13.3% on the other four). No box resize; the prepared
   `terraform.tfvars` rollback is not needed. Full table in
   [plan 12 §5f](./12-data-gap-backlog-and-demand-driven-capture.md).

   This is also the **direct** confirmation of §5d's inference: that section argued
   from within one session that loss tracks each symbol's own update rate rather
   than process load. §5f varied the load 6.7× and the original seven did not
   degrade. Shared contention cannot produce that.

   The check was cheap because `sbcap seqcheck` now exists
   (`collector/cmd/sbcap/seqcheck.go`) — plan 12 §5d had left it **Open** and the
   analysis was an uncommitted ad-hoc pass, which is precisely why this deadline
   slipped. It reproduces the published §5d table exactly, and in doing so found a
   168-row arithmetic slip in that table's own ALL row.

   No fork: `scan_segments()` reads 55 `plan10-survivors` + 5 `w02-widened`
   sessions and `symbol_coverage()` unions them — PTRO and RATU resolve to **60**
   trading days across the id change.

**✅ DONE 2026-09-05 (evening) — plan 13's daily impact model is built and has run
live. The 17 blocked cells are resolved; `impact_not_priced` no longer appears on any
cell in the grid.** `strategy_fit/impact.py` now prices the `single_name_time_series`
shape from daily bars, and `strategy_fit/daily_walk_forward.py` (new) gives the daily
shape its first genuine out-of-sample split.

**Verdict: PASS on 3 cells** (115-name universe, `--use-calibrated`, 1,385 cells: 826
cost-gated, 345 skipped_horizon, ~190 insufficient_n, 11 decayed, 8 through the cost
gate — of which 3 pass, 3 are killed by the new OOS split, 2 are FDR-rejected):

| Cell | Net | Impact | OOS edge (n) | Persistent |
|---|---|---|---|---|
| PTRO `daily_momentum` h2 | +43.5 | 5.5 | +209.5 (53) | YES |
| PTRO `volume_breakout` h2 | +11.7 | 5.5 | +106.7 (67) | yes, UNDERPOWERED |
| TINS `mean_reversion` h2 | +4.6 | 21.9 | +183.1 (48) | yes, UNDERPOWERED |

**The headline is BNBR, and it vindicates the whole exercise.** BNBR `daily_momentum`
h2 was the largest cell in the W2.3 report at 169.7 bps. Pricing impact moved its
hurdle from ~170 bps to **459.2 bps** — a ~290 bps charge, because it is a sub-Rp-100
name and 30M IDR is an enormous share of its turnover. Net **−129.5 bps**: dead. Same
for VKTR (hurdle 184.2), BUVA (299.5), SSIA (115.4), INCO (97.0). **For the biggest
apparent edges the unpriced impact was not a detail, it was the entire result.**

Both guard-rails demanded above were built, and both fired:

- **The OOS split killed three cells that the cost gate had passed** — BREN
  `daily_momentum` h2 (OOS −87.9 on n=49), RATU `daily_momentum` h1 (−53.9 on n=60),
  WIFI `daily_momentum` h2 (−21.9 on n=59). Real in-sample-only edges, caught.
- **The penny names were where the impact lived**, exactly as predicted.

> **A gate bug shipped and was caught only by the live run — worth remembering.** The
> first live run reported PASS on *six* cells, including the three above with negative
> OOS edge. `_is_pass_eligible` called `_clears_oos_power_gate`, which only asks
> whether the OOS sample is big *enough* — it never asked what the sample *said*. The
> split was built to catch in-sample-only edges and its verdict was not consumed.
> Fixed: `_is_pass_eligible` now also requires `oos_persistent is not False` (`is not
> False`, not `is True` — `None` means no split applies to that shape). Five regression
> tests in `test_verdict_gates.py::TestOosPersistenceIsBlocking`.

> **A data-volume assumption that was wrong.** The pre-work estimate — repeated
> independently by a subagent — was that hold=2 daily cells would be untestable
> out-of-sample (~21 vs ~22–24 observations against `MIN_EFF_N=30`), both derived from
> "~1 yr of chartbit daily bars". **The 90-day cap is on the *intraday* endpoint.** The
> daily endpoint returns 631 rows (2024-01-02 → 2026-09-04), so OOS is ~250 rows and
> ~60 observations at h2 — which is why h2 cells dominate the survivor list. Two
> independent estimates agreeing confirmed the arithmetic, not the shared bad premise;
> a cheap live probe settled it in seconds. **Probe the data before reasoning about its
> size.**

**These 3 are provisional leads, not a green light.** Before anything downstream
treats them as deployable:

- **The impact coefficient is borrowed.** `impact_coeffs.json` was calibrated (plan 15)
  from tick fills on *intraday* executions for 7 large-caps. Applying it to daily cells
  is an assumption, recorded in `impact.DAILY_COEFF_IS_BORROWED_NOTE`.
- **TINS clears by 4.6 bps against a 21.9 bps impact charge** — entirely inside that
  coefficient's error bar. Treat as non-deployable until TINS has measured ticks (it
  joined the capture watchlist 2026-09-05; first session Mon 2026-09-07).
- **Two of the three are the same name.** That is concentration, not a portfolio.
- All three are h2, i.e. maximum financing exposure under the ADR-0011 clock.
- 2 of 3 are UNDERPOWERED: OOS n clears the hard floor but misses the power-derived
  required-N.


### ⛔ BREADTH TEST 2026-09-12 — the daily families have no edge. Close the band.

*The test the whole project has been missing: the same signal across the whole
universe instead of the one name a backtest grid selected. 47 names, deep history
from each name's own clean start (zero-volume < 5%/yr, floored at 2013),
`open_{t+1}` entry, filter-then-de-overlap, impact on **trailing-2y** ADV.
**85,245 trades.***

| family | names | trades | mean edge vs null | median | t-stat | +ve | p(sign) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `daily_momentum` | 47 | 24,787 | **+0.1** | −4.6 | 0.01 | 18/47 | 0.96 |
| `volume_breakout` | 47 | 30,088 | **+6.5** | +3.3 | 1.82 | 29/47 | 0.07 |
| `mean_reversion` | 47 | 30,370 | **−9.8** | −7.2 | −2.01 | 17/47 | 0.98 |

*Edge = the signal's own contribution over an always-long null, bps/trade.*

**`daily_momentum` has no edge — it is zero to three significant figures.**
Mean +0.1 bps, t = 0.01, and *fewer* than half the names positive. PTRO's +45.1 bps
was one name out of 47. The spread of per-name outcomes is symmetric noise: BNBR
+170.0 against MBMA −177.5 at comparable sample sizes.

**`mean_reversion` is significantly negative** (t = −2.01): buying 5-day
underperformers has been actively anti-predictive on IDX at a 2-day horizon.

**`volume_breakout` is the only one with a pulse — and it is irrelevant.**
+6.5 bps at p = 0.07. **The cheapest hurdle in the entire 47-name universe is
77.8 bps** (UNTR, a Rp 26,300 stock where one tick is 9.5 bps); the universe mean
is 125.1. A 6.5 bps signal is not 5% of the way to tradeable.

**Only 2 of 47 names cleared their hurdle on gross, and 0 of 47 on signal.** The
two (RATU, CUAN) are short-history names whose entire sample sits inside a recent
run-up — the PTRO pattern again, not an exception to it.

> **The hurdle is the finding, not the signals.** Mean 125.1 bps per 2-day
> round-trip, and it is mostly structural: one tick of spread (9.5 bps on UNTR,
> 105.3 on BNBR) + 40 bps fees + impact + 20 bps financing. No daily price-pattern
> signal measured here produces double-digit bps. **The gap is an order of
> magnitude, not a tuning problem.** This is the same wall plan 06 hit at the
> tick scale and W2.3 hit at 819-of-836 cost-gated cells; it is now measured at
> the daily scale on 85,245 trades.

**Consequence: close the daily price-pattern band**, as plan 06 closed
microstructure and Wave 2 closed intraday ORB. Do not re-tune thresholds, holds or
lookbacks on these three families — the shortfall is ~10× and the sample is no
longer thin enough to hide behind.

**What is NOT closed.** This tested three *price-pattern* families at hold=2.
Untouched: event-driven families with a named mechanism (index inclusion, forced
flow, corporate actions) where the edge is structural rather than statistical; any
horizon beyond 2 days (capital-blocked by ADR-0011, not evidence-blocked); and
names outside this 47. The 10-name random control slice in `w02-widened` remains
the only substrate no backtest has touched — it is the right place to test
anything genuinely prospective.

**Method note for every future run.** The always-long null belongs in
`strategy_fit` as a blocking gate. Nothing in the harness currently asks *"does
this beat holding the thing?"*, which is how a 30× move in one name was booked as
alpha and carried three cells to PASS. It is one function, and it would have
caught this in June.

---

### 🔴 VERDICT 2026-09-12 — the daily survivors do not survive a proper test

*Run on the deep history described below (PTRO 2013→2026, ~3,300 bars), with a
realistic `open_{t+1}` entry (D1), the corrected filter-then-de-overlap order
(D2), a 60/40 chronological split, and the repo's own cost stack.*

**PTRO `daily_momentum` h2 — the best cell this project has ever produced:**

| | value |
|---|---:|
| OOS gross edge (C2 entry, corrected order, n=306) | **+119.4 bps** |
| `min_edge_to_clear` (one-tick spread 41.9 + 40 bps fee) | 81.9 bps |
| modelled impact — **corrected, see below** | 3.9–4.5 bps |
| financing (ADR-0014, cheapest entry weekday, hold 2) | 20.0 bps |
| **best-case hurdle** | **~110.2 bps** |
| **net** | **+9.2 bps** |

> **Corrected 2026-09-12.** This table first read impact 17.6 bps / hurdle
> 119.5 / net **−0.1**. That impact was computed over the 2021→now OOS window,
> and `compute_adv_notional_idr_from_daily_bars` takes the **median** turnover
> across whatever window it is given. PTRO's liquidity changed by two orders of
> magnitude over that span, so the median is not a description of the stock you
> would trade today:
>
> | ADV window | median ADV | impact |
> |---|---:|---:|
> | 2013 → now | 2.1 B IDR | 33.6 bps |
> | 2021-03 → now (used originally) | 10.8 B IDR | 17.6 bps |
> | trailing 2y | 269.2 B IDR | 4.5 bps |
> | trailing 1y | **371.7 B IDR** | **3.9 bps** |
>
> A 176× spread. Pricing a trade you would place *today* requires trailing ADV,
> so the cell is **marginally positive (+9.2 bps), not breakeven**. This is a
> general trap for any deep-history re-run: `impact.py`'s median-ADV logic is
> correct for its stated near-term use and silently wrong over a decade for a
> name whose liquidity profile moved. **Any grid re-run on deep history must
> source ADV from a trailing window, not the full span.**

**The correction does not change the verdict, and the reason is below: +9.2 bps
of the cell's return is signal, and the rest is drift.**

**And most of that gross is not edge — it is the stock going up.** The OOS window
is 2021-03-08 → 2026-09-11, over which PTRO went **182 → 5,400, a 29.7×**. Against
an always-long null (enter every de-overlapped 2-day slot regardless of signal,
same window, same de-overlap):

| | bps/trade | n |
|---|---:|---:|
| strategy (signal > 0) | +119.4 | 306 |
| always-long null | +74.3 | 660 |
| **edge over the null** | **+45.1** | |
| p(random same-size subset ≥ strategy) | **0.060** | |

**62% of the apparent edge is beta to a 30× move.** The signal's own contribution
is +45.1 bps against the corrected ~110.2 bps hurdle — **−65.1 bps net** — and it
is not distinguishable from randomly selecting the same number of 2-day windows at
the 5% level. **This is the load-bearing number and the ADV correction does not
touch its sign.** To bank the +9.2 bps you must forecast that PTRO keeps drifting
up as it did while going 182 → 5,400; the signal alone never pays for the
friction. The 2026-YTD row is the live test of that: strategy +32.0 bps against a
~110 bps hurdle — **−78 bps** — in the year with one of the sample's better signal
edges (+71.6 over null).

**TINS `mean_reversion` h2 is unambiguously dead:** OOS +32.6 bps gross against the
same class of hurdle; edge over the null **+7.5 bps at p = 0.336**. Independently,
it is hit by both D1 (−13.9) and D2 (−36.1), and its impact charge still rests on a
borrowed coefficient (5 sessions of ticks vs plan 15's `MIN_IMPACT_OBS`).

**The in-sample half is the tell, and it points the same way.** For every cell the
OOS half *beat* the IS half — PTRO momentum 31.7 → 120.4 bps under production
semantics. That is backwards for an overfit strategy and is the signature of a
chronological split whose second half contains a bull run. The 2013–2021 half,
which is the regime-neutral one, earns **24–51 bps gross against a ~120 bps
hurdle**: deeply negative.

> **This is the same failure that killed ORB, in a new costume.** ORB died because
> its edge lived only in the window its names were selected in. This cell's edge
> lives only in the window its name went up 30×. In both cases the strategy was
> being credited for something other than the signal.

**Caveat on precision, not on direction.** `min_edge_to_clear_bps` here is
reconstructed from the tick ladder against the OOS median close (478 → tick 2 →
41.9 bps) rather than read from the plan-09 annotation, so the hurdle carries a
few bps of uncertainty. The direction does not depend on it: no plausible floor
turns a +45.1 bps null-adjusted edge into a pass, and the p = 0.060 is
cost-independent.

**Consequence for [plan 17](./17-execution-and-replay-parity-engine.md) §2 and
Wave 4:** condition 1 (confirmed edge) is **not met**. There is nothing to paper
trade. Building the forward paper-trade loop now would be building execution for
an edge that does not exist — the precise mistake Wave 2 was created to prevent.

**What this does NOT say.** The daily *band* is not dead — it has only now been
tested with enough data to judge, and only on three cells. The deep history that
made this verdict possible also makes the whole §4 catalog re-testable at ~5×
the sample it has ever had, and the 47-name capture (including the 10-name random
control slice, untouched by any backtest) is accruing the one substrate that can
answer a genuinely prospective question. **Re-run the grid on the deep history
before concluding anything broader.**

---

### 🔍 The daily band was never data-limited — 2.5 years used, ~14 usable years available (2026-09-12)

**`research/data/daily/` now exists** (`screener/daily_store.py`, one parquet per
symbol, 47 names, 17 MB) — the durable store whose absence §7 recorded as "the
daily families have no on-disk store". Populating it live surfaced something the
whole daily band has been evaluated without:

| Symbol | rows | first bar | clean from |
|--------|-----:|-----------|------------|
| TINS | 6,228 | 2001-03-14 | 2001 |
| PTRO | 5,758 | 2003-01-01 | **2013** |
| RAJA | 4,944 | 2006-04-20 | — |
| WIFI | 1,369 | 2020-12-30 | 2021 |
| BREN |   694 | 2023-10-09 | 2023 |
| RATU |   395 | 2025-01-08 | 2025 |

**Every daily result to date rests on ~636 rows (2024-01-02 → present).** That was
never the endpoint's limit — it is `screen_universe.DEFAULT_LOOKBACK_DAYS`, a
client-side default. chartbit serves from listing date, or a 2003-01-01 floor,
whichever is later.

PTRO's usable history is **2013 → 2026, ~3,300 trading days**, not 636. Its 939
zero-volume days are entirely pre-2013 (46% of 2003, 87% of 2011, then 0.0% every
year from 2013 on) — it was a thin small-cap before its recent run. TINS is clean
from 2001. BREN/RATU/WIFI are genuinely young and their short histories are real
listing constraints, not a fetch artefact.

**Why this matters more than any defect above.** Plan 13's out-of-sample split —
the guard-rail that killed BREN, RATU and WIFI, and the one that made the three
survivors credible — ran on **n = 48–67 observations**, with two of three cells
reported UNDERPOWERED against the W2.1 power-derived floor. On ~3,300 rows, a
60/40 split yields on the order of **300+ de-overlapped long-only observations
out-of-sample** for PTRO. The question "does this edge persist out-of-sample" goes
from underpowered to properly testable.

**This is the second instance of the same error, and §7 already recorded the
first:** the pre-plan-13 estimate that h2 daily cells would be untestable OOS came
from assuming a 90-day cap that applies only to the *intraday* endpoint — "two
independent estimates agreeing confirmed the arithmetic, not the shared bad
premise; a cheap live probe settled it in seconds. **Probe the data before
reasoning about its size.**" The same lesson, missed the same way: this session's
own first probe passed `start='2024-01-01'` and concluded "636 rows" without ever
asking what the endpoint would serve unbounded.

**Caveats before anyone re-runs the grid on 14 years.** PTRO ran from a median
close of 45 (2015) to 5,100 (2026) — a ~100× move. Any long-only momentum family
will look extraordinary on that path, and walk-forward persistence, not
full-sample edge, is the only meaningful read. Impact and ADV from 2013 cannot be
used to price a 2026 trade; the plan-15 coefficients are calibrated on 2026 ticks
and must stay scoped to recent data. And the deeper history is back-adjusted, so
it is internally consistent but is not the price anyone could have transacted —
the plan 12 §5e corporate-action warning applies across the full span.
`daily_store` writes a `divergence_log.jsonl` on every re-pull for exactly this.

---

### ⚠️ Three harness defects found 2026-09-12 — the 3 survivors are NOT yet re-judged

*All three sit in code paths production exercises and the test suite does not.
None of them is a reason to doubt the ORB kill (that family is dead on two
substrates). All three bear on the three daily survivors above, which are the
only live candidates this project has.*

**D1 — The daily families enter at a price that cannot be transacted.**
`families_single_name.py` computes every signal from `close_t` and measures
`forward_return = close_{t+hold} / close_t − 1`. Entry is therefore at the same
closing price the signal is derived from — observable only after the session has
closed. PTRO `daily_momentum`'s signal is literally `close_t/close_{t-1} − 1`, so
the overnight gap is exactly where its payoff would be consumed. Measured on
chartbit daily bars (636 rows, full-sample gross, long-only, hold=2), moving entry
to `open_{t+1}` costs **PTRO momentum −19.8 bps, TINS −13.9 bps**, while PTRO
`volume_breakout` *gains* +17.9. Controls behave correctly: BREN, already killed by
the OOS split, degrades worst at −74.0. **Not a false-positive mechanism — a
realism gap.**

**D2 — De-overlap runs before the long-only filter.**
`run_single_name_family:369` calls `deoverlap_daily` on the raw signal stream, and
`long_only_mask` is applied afterward at `:281` and `:393`. The stride therefore
burns slots on short/flat rows that are never traded. **Cannot inflate
significance** — kept rows remain a subset of an arithmetic progression, so no
false overlap and no lookahead. But it destabilises the *edge estimate*: filtering
first then de-overlapping moves gross by **−116 to +28 bps** across five cells
(PTRO momentum +28.1, PTRO volbreak +26.7, TINS −36.1, BREN −116.4, WIFI −63.5),
while `eff_n` barely moves (−3 to +8). An initial synthetic fixture suggested an
`eff_n` collapse; real data does not show one. `hold=1` is a de-overlap no-op, so
**only h2 cells are affected — which is all three survivors.**

**D3 — `MIN_EFF_N` gates the wrong population in the event family.** *(the only
one that can manufacture a false positive)*
`families_event.py:270` sets `eff_n = len(pooled)` on the raw **mixed-sign** pool
and gates `MIN_EFF_N` on it at `:272`; `eff_n_valid` is computed at `:291` and only
checked `== 0` at `:293`. The tested path then returns `"eff_n": eff_n_valid` — so
the function gates one population and reports another. A cell can clear
`MIN_EFF_N=30` on 29 short events + 1 long and ship as `status="tested"` with
`eff_n=1`, carrying a bootstrap p-value computed on one observation.
`run_strategy_fit.py:_run_event_cell` passes it through unchecked.
**No current result is affected** — all six pooled event cells are cost-gated at
**−53 to −88 bps against hurdles of 103–113** (W2.3, the 2026-09-05 run), so they
died on economics, not on N. Latent, not live.

> *Corrected 2026-09-12.* This blast-radius check was first made against
> `research/out/strategy_portfolio.md`, which reads −74.5 to −95.0 — **a
> 2026-06-29 artifact, 2.5 months stale**. See the `research/out/` warning below.
> The verdict is unchanged either way; the figures were not.

### ⚠️ `research/out/` is stale and actively misleading

Every artifact in `research/out/` predates the plan-13 run:
`strategy_portfolio.md` **2026-06-29**, `strategy_triage.md` 2026-06-27,
`impact_sweep.md` 2026-06-20. The 2026-09-05 grid — the one that produced the
three daily survivors, priced daily impact, and added the daily OOS split — **was
never written to disk.** Its results exist only as prose in this roadmap and in
[plan 13](./13-capital-allocation-and-capacity.md).

`strategy_portfolio.md` therefore still shows `impact=0.0` on daily cells (the
exact conflation W2.1 removed), pre-correction PTRO net-edge figures, and
event-cell numbers that differ from the current run. It is the most authoritative
*looking* file in the repo and it is wrong. It cost one incorrect figure in this
very section before being caught.

**Any re-run must persist its output**, and a stale-artifact check belongs in
`calendar/check_deadlines.py` alongside the data-freshness section — the same
class of failure as the chartbit store going dead for ten weeks without anyone
noticing.

**Test coverage is absent for all three.** `test_families_single_name.py:63`
hardcodes `horizon_trading_days=1` and `test_daily_walk_forward.py`'s helper
defaults to 1, while `run_strategy_fit.py:134` runs `SINGLE_NAME_HOLDS = [1, 2]`.
`test_families_event.py`'s fixtures split events ~50/50 so both halves clear
`MIN_EFF_N` independently. `families_intraday.py` was checked and is **clean** —
`run_orb_family` appends only long-only-taken trades as it iterates, so its gate is
already on the right population.

**Consequence: the +43.5 / +11.7 / +4.6 bps figures in the table above are not
currently trustworthy in either direction, and no downstream work should treat
them as inputs until the cells are re-judged under a realistic entry (D1) and the
corrected filter order (D2).** Rough direction from full-sample gross: PTRO
`daily_momentum` survives both and may come out stronger; **TINS is hit by both
against a +4.6 bps margin and is probably dead.** TINS is separately blocked —
it joined tick capture 2026-09-07 and has only 5 sessions, far short of plan 15's
`MIN_IMPACT_OBS`, so its 21.9 bps impact charge still rests on a borrowed
coefficient.

**Deferred, recorded so it is not re-derived:** `harness.effective_n_event_pooled`
is imported at `families_event.py:39` and never called; `run_event_family`
reimplements the same per-symbol-then-pool logic inline. The helper cannot be a
drop-in — it returns only a count and discards the rows that `run_event_family`
needs for `hit_rate`/`gross_edge_bps`/`p_value`. The fix that closes D1-for-events
and D3 together is to add `deoverlap_and_pool_events(events_df, hold) -> DataFrame`,
make `effective_n_event_pooled` a one-line wrapper over it, filter long-only
per-symbol *before* de-overlap, and gate `MIN_EFF_N` on `len()` of what the helper
returns — which is then definitionally the population the tested branch reports.
Changes the helper's contract, so `test_harness.py`'s three tests move with it.

---

**Then, what is worth testing next?** Not more machinery. The factory now works — it is
what produced both the kill and the block. Constraints that now bound any new
hypothesis:

- **Tick volume is unusable; chartbit daily volume is a different question.** The
  ~16 % conflation measured in §5d is in the *captured websocket stream* — it does not
  apply to chartbit's daily aggregates, so the `volume_breakout` cells above are **not**
  tainted by it. What it does rule out is anything keyed on tick-level volume,
  participation or signed flow built from `data/tick_bars*` — OFI-style signals
  especially.
- **Long-only.** Every family is now constrained (`strategy_fit/long_only.py`), and
  cross-sectional and pairs are marked **structurally infeasible** — a decile spread
  and a stat-arb pair both require a short leg no IDX retail account has. That
  removes two of the five shapes in the §4 catalog outright, which
  `catalog_meta.py` still lists as `testable_now` and should be corrected.
- **Selected before the window.** The failure that killed ORB was evaluating names on
  the period they were chosen on. The W0.2 widening (47 names, including a
  **10-name random control slice** sampled before any result is known) exists exactly
  so the next test can avoid it. That control slice is the most valuable thing capture
  is now accruing — it is the only part of the universe no backtest has touched.

**The standing `SB_BEARER_TOKEN` blocker is cleared** — the live grid above ran on a
valid token, so the daily families are no longer excluded by the `impact_priced` gate
and are now formally tested under the corrected long-only code. A token is still
required for any future re-run (the daily families have no on-disk store), and tokens
expire in roughly a day.

> **What Wave 1 changed about Wave 2.** Two constraints are now measured rather
> than assumed. Volume capture degrades only in the busiest bars — quintiles 1–3
> are ~100 % for every symbol, TPIA's busiest quintile is 92.9 %, and its top 3 %
> of bars carry 86 % of the shortfall — so volume- and flow-keyed families cannot
> be judged on tick volume, while price-keyed families can. **The cause is now
> established (2026-09-05): it is upstream conflation, not our collector.**
> Measured against the datafeed's own per-symbol `sequence_number` over all of
> 2026-09-04, 16.1 % of 263,398 sequence slots are unaccounted for, ranging
> RATU −1.1 % → TPIA 20.6 %. The discriminating test is that all seven symbols
> share one websocket and one process, so a starved collector would have to drop
> indiscriminately — but in the 30 busiest *market-wide* minutes RATU still loses
> only 2.5 % while TPIA loses 21.2 %. Loss tracks each symbol's own update rate,
> not the load on the box. That means it is **not fixable by us** and is a
> permanent property of this substrate; see
> [plan 12 §5d](./12-data-gap-backlog-and-demand-driven-capture.md).

---

Back to [Plans index](./README.md)
