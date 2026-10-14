# Alpha / Infrastructure Split — Two Tracks, One Seam

> **Status:** ✅ Approved · 🔬 Track C complete · 🔨 Track B in progress (B1–B3 complete and live-verified; B4 first live fills 2026-10-13) · **Owner:** @lewis · **Updated:** 2026-10-13
> **Related:** [ADR-0016: alpha and infrastructure are separate tracks](../adr/0016-alpha-and-infrastructure-are-separate-tracks.md) · [ADR-0015: leverage is optional](../adr/0015-leverage-optional-horizon-ceiling-lifted.md) · [ADR-0013: one Strategy interface](../adr/0013-one-strategy-interface-and-collector-public-packages.md) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) · [13-capital-allocation-and-capacity.md](./13-capital-allocation-and-capacity.md) · [15-measured-fill-and-impact-calibration.md](./15-measured-fill-and-impact-calibration.md) · [16-strategy-triage-and-prioritization.md](./16-strategy-triage-and-prioritization.md) · [17-execution-and-replay-parity-engine.md](./17-execution-and-replay-parity-engine.md) · [Plans index](./README.md)

> **What this is.** The re-cut of roadmap Waves 3–5 into two independently
> schedulable tracks — **Track A (alpha research)** and **Track B (trading
> infrastructure)** — joined by a single contract, `target_portfolio.json`,
> preceded by **Track C (§4)**, the offline cost calibration that gates how both
> are interpreted.
> It implements [ADR-0016](../adr/0016-alpha-and-infrastructure-are-separate-tracks.md)
> and supersedes the strict wave ordering in [plan 00 §6](./00-roadmap-research-to-live.md)
> from Wave 3 onward. §7 reconciles every prior plan against the split; nothing
> is left implicitly re-scoped.

---

## 1. Why now — the loop had no exit

The research loop did not stall for lack of effort. It stalled because its exit
condition was unreachable.

Every verdict this project has produced compares a measured gross edge against a
**modelled** hurdle: `min_edge_to_clear + modelled_impact + financing`. The
impact term comes from `SQRT_IMPACT_COEFF`. In
`research/strategy_fit/impact_coeffs.json`, `high_liq` is genuinely measured
(0.377, `eff_N=40,351`, 7 captured names) — but `mid_liq` and `low_liq` are
`null` and fall through to `global_default: 1.0`, a hand-picked constant. So for
most of the universe the ruler that killed every candidate has never been
checked against a real fill.

Checking the *fill* half of that ruler — queue position, fill rate, adverse
selection, rejects — needs realized fills. Realized fills need live orders. Live
orders were gated, by [plan 17 §2](./17-execution-and-replay-parity-engine.md),
behind a confirmed edge. **That is a cycle**, and it is why each wave re-entered
Wave 2.

> **Correction, 2026-09-13, after this plan's first draft.** The cycle is real
> but narrower than this section originally claimed. The *impact* half of the
> ruler needs no live order at all: impact is the price response to aggressive
> flow, and the captured tape records other people's aggressive flow in full. The
> pipeline to fit it already exists and has run twice. It left `mid_liq` and
> `low_liq` empty because of two defects, not because the measurement is
> impossible. **§4 (Track C)** is that work, and it now runs ahead of both tracks.

The scale of what the loop actually covered is the other half of the diagnosis.
The plan-10 coverage table holds **1,389 cells**:

| Shape | Cells | Share |
|---|---:|---:|
| `single_name_time_series` | 1,034 | 74.4% |
| `intraday_same_day` | 345 | 24.8% |
| `event_driven` | 6 | 0.4% |
| `pairs` | 2 | 0.1% |
| `cross_sectional` | 2 | 0.1% |

**99.3% of all work went into one shape family inside the ≤2-day band** — the
band where the 2026-09-12 breadth test (85,245 trades) measured a mean
round-trip hurdle of **125.1 bps** and concluded *"the gap is an order of
magnitude, not a tuning problem."* The catalog was never broadly searched. It
was deeply drilled in one place, and that place is structurally unwinnable at a
retail cost stack.

Infrastructure, meanwhile, is required by **every** alpha outcome. Nothing about
position reconciliation, pre-trade risk, or an order state machine depends on
which signal wins. Sequencing it behind an unresolved research question bought
nothing and cost the calibration.

## 2. The split

```
TRACK A — alpha research                 TRACK B — trading infrastructure
research/ · Python                       strategy/ · Go · ops
────────────────────────────             ──────────────────────────────
universe → signal → sizing               target diff → orders → fills
                    │                                   │
                    ▼                                   ▼
            target_portfolio.json ──────────>  positions · PnL · post-trade store
                    ▲                                   │
                    │                                   ▼
            impact_coeffs.json  <───────────── measured realized cost
```

**Track A** owns: what to hold and how much. It never models an order.
**Track B** owns: getting there safely and measuring what it cost. It never
models a signal.

The two feedback edges are the point of the whole design: Track A pushes target
weights down; Track B pushes measured costs back up. Today the upward edge does
not exist, which is why Track A cannot finish.

**Neither track blocks the other.** Track B is developed against a *null
portfolio* (§3.3) and needs no alpha to reach its exit criterion.

## 3. The seam — `target_portfolio.json`

### 3.1 Contract

```json
{
  "schema_version": 1,
  "as_of": "2026-09-13T09:00:00+07:00",
  "strategy_id": "calibration | momo_12_1_monthly | ...",
  "capital_model": "owned_cash | leveraged_sleeve",
  "base_notional_idr": 50000000,
  "targets": [
    {"symbol": "BBCA", "weight": 0.25},
    {"symbol": "TLKM", "weight": 0.25}
  ],
  "constraints": {
    "max_participation_rate": 0.05,
    "complete_by": "2026-09-13T15:00:00+07:00",
    "board": "RG"
  }
}
```

- `weight` is a fraction of `base_notional_idr`. Weights sum to ≤ 1.0. Long-only
  for now; a negative weight is a schema error until short support is built
  (§11).
- `capital_model` records which [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md)
  model produced the targets, because results under the two are not comparable.
- `strategy_id` is the post-trade store's partition key. `calibration` is
  **reserved** (ADR-0016 §6) and is never reported as strategy performance.

### 3.2 Why this seam and not the signal

A signal seam would force Track B to own sizing, netting and constraint logic
that belongs with the research that produced it, and it makes a null input
awkward to express. Target portfolio is the standard portfolio-construction /
execution boundary: both sides stay independently testable, and the file is a
plain artifact that can be hand-written, diffed, and replayed.

### 3.3 The null portfolio

Track B's development input is a deliberately zero-alpha target portfolio —
equal-weight across 5 liquid names, rebalanced on a fixed schedule. Its expected
alpha is zero by construction, so **every basis point of deviation from arrival
price is attributable to execution**, which is exactly the quantity being
measured.

This is why the null portfolio, not the best available strategy, is the primary
calibration instrument: trading a real candidate conflates "was there alpha" with
"was the execution good", and we already cannot answer the first question.

### 3.4 Where the sleeve trades — account `008488L`

**The bot trades one brokerage sub-account, and it is not the core book.**
Measured live on 2026-09-14, `GET /portfolio/v2/summary` →
`summary_per_portfolios[]` returns two:

| account | name | state |
|---|---|---|
| `0001958` | LEWIS | the owner's pre-existing core book — 8 positions, ~1.3B IDR invested |
| `008488L` | Trading | **empty** — 0 invested, 0 equity, 0 cash, 0 positions |

Everything Track B does happens in `008488L`.

**Why this is load-bearing and not an operational detail.** The two accounts
share one login, so without the split the bot and the core book are the same
book as far as every endpoint is concerned, and three things break:

1. **B1 could never reach MATCH.** Reconciliation compares broker holdings
   against positions derived from our own fills. The core book has no fills to
   derive from — it predates the store — so it reports as eight
   `held_at_broker_only` CRITICAL divergences, permanently. The gate cannot pass,
   which makes it useless as a gate.
2. **B2's caps would be meaningless.** "Max gross exposure" and "max position per
   name" computed over a shared account measure the core book, not the sleeve.
3. **B4's blast radius would include the core book.** Live routing's first
   mistake should not be able to touch BBKP or HMSP.

**What it replaces.** The alternative was a recorded opening-balance baseline —
a `position_baseline` table asserting "assume the broker was right on day one",
plus an operator re-baselining step after every manual trade in the core book.
That is an *assertion*; a separate account is a *boundary*. The sleeve starts
empty, so store-derived equals broker balance from the first fill with nothing
to assume. The baseline design is dropped.

**Token mechanism (verified).** One trading token covers both accounts. The
handshake is two steps — `GET exodus/sekuritas/auth/token` with the main token
yields a `login_token`, then `POST carina/auth/v2/login` with
`{login_token, pin}` yields the trading access/refresh pair. The resulting JWT is
stamped `acn=0001958` and still addresses `008488L` by query parameter, so there
is no second login and no account selection in the handshake.

**Scoping is inconsistent, and two endpoints ignore the parameter silently:**

| endpoint | parameter | honoured? |
|---|---|---|
| `/portfolio/v2/list` | `acc_no` | ✅ 8 results vs 0 |
| `/balance/withdrawable` | `balance_scope` + `account_number` | ✅ 29.7M vs 0 |
| `/balance/cash/info` | `acc_no` | ❌ **ignored** — always the core book's limits |
| `/order/v2/list` | `acc_no` | ❌ **ignored** — always every account's orders |

Both failures are silent 200s, which is the dangerous shape. `cash/info` would
hand B2 the core book's buying power as though it were the sleeve's, and
unscoped `order/v2/list` would attribute a core-book resting sell to the sleeve
whenever both accounts hold the same name. Order rows carry **no account field
at all**, so reservations cannot be attributed by account — they are attributed
by our own `order_id`s, which the post-trade store knows.

**Two open prerequisites before the sleeve can trade.**

- [x] **Funded 2026-09-14: 15,000,000 IDR.** `withdrawable` and `transaction` for
      `008488L` both read 15,000,000, and the core book's cash fell by exactly
      that amount (29,746,598.11 → 14,746,598.11), so the transfer is clean and
      the two balances are genuinely separate.
- [x] **Confirm whether the sleeve has a trading limit of its own.**
      **Confirmed 2026-10-14: it does not.** A 16,832,000 IDR BBRI buy in
      `008488L`, which held 14.9M of cash, was rejected with `[C-006]
      insufficient trading balance` ([runbook §14](../runbooks/first-live-order.md)).
      Earlier measurement:
      the limit does *not* follow the cash. `trade_limit` fell 392,013,889.11 →
      377,013,889.11 — exactly the 15,000,000 transferred out — and `cash/info`
      reports identical figures for both account numbers because it ignores
      `acc_no` entirely. So the sleeve is sized as **15M of owned cash**, not a
      300M facility. This is settled only by attempting an order above 15M in
      `008488L`, which is B4-gated; until then the conservative reading stands.
      Recorded as [ADR-0017](../adr/0017-sleeve-is-a-funded-sub-account.md),
      which also voids `POSITION_NOTIONAL_IDR = 30_000_000` — a per-name order
      notional twice the size of the entire sleeve, and a second over-charge in
      every impact figure on top of the coefficient error §4.4 corrected.

**Order attribution — measured 2026-10-13, mechanism unknown.** The first live
orders ([runbook §13](../runbooks/first-live-order.md)) landed in the sleeve:
- a resting BBCA order, auto-cancelled;
- a GOTO 1-lot fill, sold back in the app.

After the fill, `008488L` reconciled MATCH with GOTO held. The core book's
holdings were byte-identical before and after both orders. But **no order
request carries an account**: the order body has no account field, and
`strategy-run --account` is used only for reconciliation and store scoping. So
Carina chose the sleeve by itself, despite the JWT being stamped
`acn=0001958` as measured above. Treat this as observed, not guaranteed:
- every live session re-confirms it with a before/after snapshot of both
  accounts;
- identifying the routing mechanism is open.

- [ ] **Identify what routes an order to `008488L`** (app account selection
      at token mint? sub-account default? a changed token claim?). Until then,
      a token minted differently could route to the core book silently.
      **It did, 2026-10-14:** the §10 probe (16.6M BBCA buy, `--account
      008488L`) landed in the core book `0001958`. The operator had copied the
      token from a web session with the main account selected. The working
      hypothesis is that routing follows the token's minting account (`acn`
      claim). The §10 result is void. No live run until `strategy-run` refuses
      to arm when the token's `acn` ≠ `--account`
      ([runbook §14](../runbooks/first-live-order.md)).

> **This likely warrants an ADR amending [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md).**
> The capital model there reasons about a sleeve funded by a trading limit on the
> core book; a separate sub-account changes that from arithmetic we perform into
> a boundary the broker enforces. Not written yet.

## 4. Track C — measure the ruler first

**Track C gates both other tracks and is not parallel to them.** It is the
prerequisite neither A nor B can be trusted without, and unlike either it needs
no new capital, no live order, and no new capture. Everything it consumes is
already on disk.

### 4.1 What already exists

| Artifact | State |
|---|---|
| `research/strategy_fit/fill_calibration.py` | 25 KB, tested, working pipeline |
| `research/strategy_fit/impact.py` | plumbed into every hurdle via `get_impact_coeff` |
| `research/strategy_fit/impact_coeffs.json` | generated 2026-09-05; `high_liq` = 0.377 at `eff_N=40,351` |
| `research/out/fill_calibration_report.md` | generated 2026-06-27, `eff_N=6,602` |
| `segments/dt=2026-09-07 … 2026-09-11` | **5 sessions × 47 names, ~367 MB, never processed** |

The buckets are empty because of the two defects below, not because the data is
missing.

### 4.2 Defect 1 — `_adv_from_trades` multiplies by lots

```python
df["_notional"] = df["vol"] * df["price"] * LOTS_PER_SHARE_UNIT   # ×100 — wrong
```

The trade tape's `vol` is already in **shares**, not lots. Verified on BUMI
2026-06-18 (47,976 prints): `vol × price` = **Rp 415.09 B**, the right order of
magnitude against the screener's Rp 668.96 B median; `× 100` gives Rp 41,509 B.

The fitted coefficient is **unaffected** — in `detect_impact_events` the same
factor appears in the numerator of `participation`, so it cancels. What breaks is
`liquidity_bucket`, whose thresholds are absolute: `HIGH_LIQ_ADV_IDR = 100e9`
becomes an effective Rp 1 B cutoff, so **every name files `high_liq`**. That is
why `mid_liq` and `low_liq` carry `eff_N = 0`. ANTM's `low_liq` is a different
path — empty trades, `_adv_from_trades` returns `0.0`.

### 4.3 Defect 2 — an uncaptured name never reaches a bucket

`get_impact_coeff` resolves `per_name` first and consults `buckets` only for a
symbol **already in `per_name`**. Any symbol absent from it — every name outside
the 7 calibrated ones — returns `global_default = 1.0` directly. So fixing
defect 1 alone buys nothing for the universe: the buckets would be correct and
still unreachable.

`infra/terraform.tfvars` recorded this correctly on 2026-09-05 as *conservative,
not unsafe*: 1.0 is the high-friction end of the Almgren range, so uncalibrated
names are over-charged. That is the safe direction for capital and the **wrong**
direction for a verdict — it kills candidates by assumption.

### 4.3a Defect 3 — the volatility estimator flips on panel length *(found during C3)*

This one was not known when §4 was written. It was found by C3's own
cross-check and it is the defect that matters most.

`_daily_vol_bps_from_features` switches estimator on sample length:

```python
if n_days >= 2:
    return compute_daily_vol_bps(df[["date", "close"]])   # close-to-close
return _intraday_vol_bps(features)                        # intraday realized proxy
```

The 2026-06-27 calibration ran at `days=1` per name, so it used the **intraday
proxy**. The Track C panel is 5 days, so it uses **close-to-close over 4
returns**. Since the fit is
`observed_impact_bps ≈ 2 · coeff · daily_vol_bps · sqrt(participation)`, the
coefficient is inversely proportional to the volatility estimate — so changing
estimator rescales every coefficient without any change in measured impact.

Measured on the same names, same week:

| Name | close-to-close (4 returns) | intraday proxy, per day | ratio | coeff moved |
|---|---:|---|---:|---:|
| PTRO | 79.39 bps | 335, 275, 241, 470, 359 | 4.2× | 0.393 → 1.524 (3.9×) |
| BRPT | 63.70 bps | 192, 159, 213, 237, 203 | 3.2× | 0.409 → 1.264 (3.1×) |
| BREN | 67.71 bps | 176, 91, 135, 198, 174 | 2.6× | 0.417 → 0.919 (2.2×) |
| ENRG | 379.29 bps | 324, 442, 307, 455, 465 | 1.2× | 0.370 → 0.579 (1.6×) |

The coefficient inflation tracks the volatility ratio almost exactly, and ENRG —
the one name whose two estimates agree — is the one name whose coefficient
barely moved. PTRO closed the week near where it opened, so close-to-close
collapsed to 79 bps while the name realized 241–470 bps of intraday range on
every one of the five days. That is a small-sample artifact, not a volatility
regime.

**Consequence: C3's output is not yet usable and C4 must not run on it.** All
three buckets are populated for the first time, but they fail their own
cross-check against the previous calibration of the same names, and the failure
is fully explained by the estimator switch.

**Resolution — approved and implemented 2026-09-13.** Not "which of the two
short-window estimators is better": both read five days of capture. The grid
prices its hurdle through `compute_impact_bps_from_daily_df`, which takes
volatility from `compute_daily_vol_bps_from_daily_bars`. Calibration now uses
**the same estimator on the same kind of input**, so `coeff` carries impact
rather than the ratio between two estimators.

`_daily_vol_bps_from_features` is replaced by `resolve_daily_vol_bps(features,
daily_bars, as_of)`, which returns the volatility **and its source**:

1. `compute_daily_vol_bps_from_daily_bars` over the trailing
   `CALIBRATION_VOL_WINDOW_DAYS = 60` bars at or before the last capture date
   (`as_of` caps the window, so a calibration dated T never sees T+1).
2. Only when a name has no usable daily history: the intraday realized proxy,
   medianed per day — and it logs a warning, because its coefficient is not on
   the same scale as the rest.

The close-to-close-over-the-capture-panel path is gone entirely.

**Why 60 days.** The window is not neutral — `coeff ∝ 1/daily_vol_bps`, so a
window that misreads volatility by 4× misreads every coefficient by 4×. Five
days gives four returns, which is what caused this defect. A multi-year median
averages across regime changes, the trap already recorded for ADV (PTRO's
modelled impact ranged 3.9 → 33.6 bps purely by window choice). 60 trading days
is stable enough to estimate and short enough to describe the regime the capture
actually sampled. **Changing it changes every coefficient**, so it is a named
constant with that warning in its docstring.

**Provenance is now recorded.** `impact_coeffs.json` carries `vol_estimator` and
`vol_window_days` at the top level and `vol_source` per name; the report states
them and warns that pre-2026-09-13 runs are on a different scale; `merge_coeffs`
logs a warning when provenance differs between the payloads being merged. The
point is that a coefficient is meaningless without the volatility estimator it
was fitted against, and that fact should not have to be remembered.

### 4.4 Build order

**C1 — Extract the captured sessions. ✅ Done 2026-09-13.**

- [x] `sbcap features` and `sbcap trades` over `segments/dt=2026-09-07 …
      2026-09-11`, 47 names → `research/out_live/ticks/{SYMBOL}/{date}_*.csv`
      (the per-symbol subdir layout `_discover_pairs` supports). 235/235 jobs,
      1.2 GB, zero empty outputs.
- [x] Confirm capture is still running. **Checked 2026-09-13: healthy.** Local
      segments stop at 2026-09-11 because **2026-09-12 was a Saturday** — 09-11
      was the last trading day and it captured cleanly through `seg-090000`.
      Both EventBridge schedules (`market-open` 08:30, `market-close` 16:25,
      MON-FRI) are `ENABLED` and the instance is correctly `stopped` for the
      weekend. Order book cannot be backfilled, so this check is worth repeating
      on any unexplained gap.

**C2 — Fix the defects (test-first).**

- [x] Dropped `* LOTS_PER_SHARE_UNIT` from **both** `_adv_from_trades` and
      `detect_impact_events` — they must move together or every coefficient
      shifts by sqrt(100). Constant removed; the reasoning is a comment at the
      old site so it is not reintroduced.
- [x] Audited `median_l1_lots`: book depth and tape volume come from the same
      feed in the same unit, so run detection was always unit-consistent and the
      detected *events* were never wrong. Only the name was misleading — renamed
      to `median_l1_shares` (with `acc_shares` / `threshold_shares`), since the
      wrong name is what produced the bug.
- [x] `get_impact_coeff(symbol, adv_notional_idr=...)` now resolves a bucket
      from the name's own ADV when it is absent from `per_name`; falls through to
      `global_default` only when that bucket is unconfirmed. `compute_impact_bps`
      passes the ADV it already holds, so both `_from_df` wrappers inherit the fix.
      Behaviour with no ADV supplied is unchanged.
- [x] 13 new tests, RED before GREEN. Full suite: **412 passed, 0 failed,
      12 skipped.**
- [ ] **Defect 3 (§4.3a) is not yet fixed** — it needs the methodology decision
      recorded there.

**C3 — Re-run the calibrator. ✅ Done 2026-09-13 (second run, corrected estimator).**

With §4.3a's fix the cross-check **passes**, and that is what makes the numbers
usable. The `high_liq` bucket now reads **0.3452** against the previous
calibration's **0.3768** — within 8%, from an independent capture window, a
different name membership and a different volatility estimator. Two independent
measurements agreeing at the bucket level is the evidence the first run lacked.

| bucket | coeff | eff_N | names | prior file |
|---|---:|---:|---:|---:|
| `high_liq` | 0.3452 | 5,703 | 18 | 0.3768 |
| `mid_liq` | **0.3007** | 6,843 | 20 | `null` |
| `low_liq` | **0.2926** | 1,799 | 9 | `null` |

All 47 names resolved volatility from `daily_bars`; none fell back to the
intraday proxy. Per-name agreement with the prior run is looser than the bucket
agreement — PTRO 0.86×, ENRG 1.23×, RATU 1.30×, but BREN 0.44× and TPIA 0.43× —
which is the expected shape: a five-day per-name fit carries far more noise than
a pooled bucket at `eff_N` in the thousands.

**The headline number: measured impact is roughly a third of what was assumed.**
Every name outside the calibrated set was charged at `global_default = 1.0`.
Measured, the buckets are 0.29–0.35. Impact scales linearly in the coefficient,
so the universe has been over-charged for impact by about 3×.

- [x] Written to `research/strategy_fit/impact_coeffs.json`; the prior file is
      archived as `impact_coeffs.SUPERSEDED-2026-09-13-pre-track-c.json`.
      **Replaced, not merged** — the old per-name entries were fitted against the
      old volatility estimator and are not on the same scale (§4.3a), and
      `merge_coeffs` would additionally have kept the old `high_liq` on `eff_N`.

**C3 (first run) — superseded.**

- [x] `cd research && python -m strategy_fit.fill_calibration --data-dir out_live/ticks`
      (`--data-dir` resolves against the CWD, not against the module — the old
      docstring's `../out_live` pointed outside the repo).
- [x] Produced the first non-null `mid_liq` and `low_liq` coefficients in the
      project's history, all three buckets `confirmed`, and per-name fits for all
      47 names:

      | bucket | coeff | eff_N | names |
      |---|---:|---:|---:|
      | `high_liq` | 0.7415 | 5,703 | 18 |
      | `mid_liq` | 0.5897 | 6,843 | 20 |
      | `low_liq` | 0.6227 | 1,799 | 9 |

- [x] Discarded. Its coefficients (`high_liq` 0.7415 / `mid_liq` 0.5897 /
      `low_liq` 0.6227) were inflated by defect 3 and are kept here only as the
      record of how the cross-check caught it.

**C4 — Re-judge the grid on measured cost. ✅ Done 2026-09-13, partially.**

- [x] Re-ran the plan-10 grid live, `--use-calibrated`, 115-name universe,
      1,044 cells. **Run as a controlled A/B**: the same command on the same day
      against the same data with the flag off is the control, so every difference
      is attributable to the coefficient and to nothing else. (The previous
      `strategy_portfolio.md` was 2026-09-05 data on a different universe screen
      and is *not* a valid comparison — it is archived as
      `strategy_portfolio.SUPERSEDED-2026-09-13-pre-track-c.md`.)

      | | assumed `1.0` | measured | 
      |---|---:|---:|
      | Surviving cells | **1** | **3** |
      | Cells no longer cost-gated | — | 13 |

- [x] **BNBR is the clean confirmation.** It was the largest blocked cell in the
      grid and the impact charge was the entire result:

      | | assumed `1.0` | measured |
      |---|---:|---:|
      | Friction hurdle | 442.8 bps | **210.2 bps** |
      | Impact | ~290 bps | **64.9 bps** |
      | Net edge | **−113.1** (cost_gated) | **+119.5** (PASS) |
      | Edge over always-long null | — | **+269.3 bps** |
      | OOS edge / eff-N | — | 420.3 bps / 52 |

      It also clears the always-long null gate that killed PTRO, and it is the
      one surviving cell **not** flagged UNDERPOWERED.

- [x] **The long-horizon band was re-judged. ✅ Done 2026-09-13.** The ≤2-day
      ceiling lived in **two** places, and lifting it in `capital_model.py` alone
      had made the 345 long-horizon cells *vanish* from the coverage table rather
      than turn into tested ones — `SINGLE_NAME_HOLDS` still enumerated `[1, 2]`,
      so they read as "not applicable" instead of "untested". Holds are now
      `[1, 2, 5, 20, 60]`, with cross-sectional and pairs following the same list
      rather than their own literals. The grid goes **1,044 → 2,093 cells**.

      | hold | tested | insufficient_n |
      |---:|---:|---:|
      | 5 | 344 | 1 |
      | 20 | 3 | 345 |
      | 60 | 3 | 338 |

      **hold=5 is a genuine new band and it produced a new survivor** — HRTA
      `volume_breakout` h5, net **+257.0 bps**, the largest net edge in the grid,
      though UNDERPOWERED at `eff_N=62`. **Holds 20 and 60 are almost entirely
      walled off by `MIN_EFF_N=30`**: after de-overlap, a 60-day hold over the
      default 2024-01-01 window yields ~12 independent trades per name. That is
      the known lookback trap, not a bug — and widening the window trades it for
      survivorship exposure, which is exactly what A-3 has only just started
      accruing against. **A-2.2 is therefore `data_blocked`, not untested**, and
      that is a different and more useful answer than the one this box asked for.

- [x] Two defects found while verifying the above, both in the direction that
      manufactures a result:

      - `report.py` hardcoded *"Capital model: ADR-0011 — 300M IDR T+2 leverage
        sleeve, 10 bps/cal-day financing, ≤2 trading-day horizon ceiling"* and
        printed it whatever model actually ran. **Every report since ADR-0015
        asserted a financing charge and a ceiling that were not applied**, in the
        direction that makes hurdles look higher than they were. Now derived from
        `ACTIVE_CAPITAL_MODEL` via `render_capital_model_line()`, which also
        states that the two models are not comparable.
      - An expired `SB_BEARER_TOKEN` makes both emitten endpoints return HTTP
        401; `screen_universe` degraded to an empty intersection rather than
        raising, so `run_grid` completed over **zero symbols** and wrote a
        confident "Verdict: NO-GO" over a good report. A credentials failure must
        not be able to author a research result: `run_grid` now raises
        `EmptyUniverseError`, naming the token as the usual cause.

### 4.4a What C4 actually establishes — and what it does not

**Establishes.** Impact was over-charged roughly 3× across the universe, and at
least one cell was killed outright by that assumption. A-1's premise — that the
closed bands were closed by measurement — was wrong for every name outside the
calibrated set, because the hurdle they were measured against was assumed.

**Does not establish that BNBR is tradeable.** Three caveats, all load-bearing:

1. **`eff_N = 28`.** BNBR's per-name coefficient rests on 28 impact observations,
   barely over `MIN_IMPACT_OBS = 20`. Its `mid_liq` bucket (`eff_N` 6,843,
   coeff 0.3007) is far better measured. Substituting the bucket raises BNBR's
   impact from 64.9 to ~89 bps and its net from +119.5 to ~+95 — it survives
   either way, which is the only reason this is a caveat and not a blocker.
2. **Five days of capture**, one regime, September 2026.
3. **BREN and INCO are UNDERPOWERED.** INCO in particular flips on a 5.6 bps
   margin: under the assumed coefficient it failed the null gate at
   `edge_over_null=70.4 ≤ hurdle=77.5`; measured, the hurdle falls to 64.8 and it
   passes. That is a cell decided by the third decimal of a cost model, and it
   should be treated as noise until the hurdle is confirmed by B6.

**The honest summary:** the ruler was wrong, the correction is real and
measured, and it revived a candidate worth pre-registering a forward test on.
It did not produce a strategy.

### 4.5 What C decides

| C4 outcome | Consequence |
|---|---|
| Measured cost materially **below** modelled | Some closed cells revive. A-1 was partly an artifact of the default. Track B gains a real candidate to trade instead of only a null portfolio |
| Measured cost **confirms** the model | The ≤ 2-day band is genuinely unwinnable rather than mis-measured. A-1 is settled on evidence and A-2 becomes the only live research question |

Both answers are worth having; neither needs a live order. **C is the cheapest
decision-relevant work available, so it runs first.**

**Outcome, 2026-09-13: the first row.** Measured cost came in at roughly a third
of modelled, the surviving-cell count went 1 → 3 on a controlled A/B, and BNBR
went from −113.1 bps (killed) to +119.5 bps (PASS, clears the null gate, not
underpowered). A-1 is therefore **not** settled for the ≤ 2-day daily band, and
Track B now has a real candidate to point at rather than only a null portfolio —
subject to the three caveats in §4.4a.

## 5. Track B — build order

Ordered by dependency and by blast radius. **B1 is read-only** and can be
validated against the live account immediately at zero risk; nothing below it is
safe without it.

### B1 — Position & PnL reconciliation *(read-only)*

> **Status: built and live-verified 2026-09-14; being re-scoped to the sleeve
> account (§3.4).** Package
> `strategy/account` (client / position / pnl / recon) + `strategy/engine/positions.go`
> + `strategy/cmd/account-recon`. `go build`, `go vet` clean; 58 tests, `account`
> at 86.0% coverage, race-clean, no network in tests.

- [x] Carina portfolio/position read client; map to an internal `Position` view.
      **Structurally read-only**: `get()` is the package's only request path and
      `readonly_test.go` fails the build if any other HTTP method token or a
      second `http.NewRequestWithContext` appears. Field shapes are grounded in
      the generated protobuf structs under `sb-protos/gen/go/...`, not in this
      repo's prose docs — `PortfolioQty.{available,balance,unsettled}` as
      `{lot,share}` at `portfolio/entity/v2/portfolio.pb.go:1008-1014`.
      **An auth failure cannot present as an empty portfolio**: 401/403 returns
      a typed `ErrUnauthorized`, and a 200 with no usable `data` returns
      `ErrEmptyResponse`. This is the §4.4 lesson applied — there, an expired
      token let `run_grid` write a confident NO-GO over zero symbols; here the
      same bug would report "you hold nothing" and reconcile it as a match.
- [x] Reconcile broker-reported holdings against the post-trade store's derived
      position; emit a typed divergence report rather than silently trusting either.
      Five kinds: `held_at_broker_only`, `held_in_store_only`, `quantity_mismatch`,
      `unsettled_only`, `cost_basis_mismatch`, plus `settlement_inconsistent` when
      the broker's own `available + unsettled != balance` — in that case neither
      leg is a trustworthy comparison basis and the report says so instead of
      picking one. `sink_kind` filters both sides of the fill→intent join:
      reconciling paper fills against a live account is noise, and the kind is
      printed in the report header so a paper run cannot be read as a live one.

      > **The defect worth remembering.** The first implementation classified the
      > benign T+2 lag by testing whether the delta from `available` *equalled*
      > `unsettled` — which is sign-blind. A store total 50 shares **short** of a
      > 150-share broker position produced the same |delta| as the benign case and
      > was reported `unsettled_only` / INFO / **verdict MATCH, exit 0**. The gate
      > would have waved through the exact condition it exists to catch, in the
      > dangerous direction (broker holdings we have no record of). The benign case
      > means *store total ≈ broker `balance`*, so that is now what it tests, with
      > `BalanceConsistent()` as a precondition. Four regression tests pin it.
- [x] Cost-basis and realized/unrealized PnL, fee-inclusive
      ([reference 07](../reference/07-fees-and-costs.md)). Buy 0.15% / sell 0.25%
      **all-in** (the exchange-fee breakdown is inside those rates, not additive),
      carried in a `FeeSchedule` parameter rather than baked in, as reference 07
      instructs. Derived cost basis uses moving-average accounting, matching the
      basis the broker's own `amount_invested` / `price.average` are computed on —
      FIFO would make the two sides incomparable on a partial sell.
- [x] **Scope every call to the sleeve account `008488L` (§3.4).** The account
      number is a required input, not a defaultable one: falling back to the
      token's own account silently reconciles the core book, which is the exact
      failure the split exists to prevent. `cash/info` must be typed so it cannot
      be mistaken for sleeve buying power, and reserved-share attribution must
      filter by our own `order_id`s, since order rows carry no account field.
- [ ] **Verify against the real account, end of a session, by hand.** Needs live
      credentials and a session's own fills; nothing below B1 is safe until this
      passes. Invocation: `SB_TRADING_ACCESS_TOKEN=… account-recon --store <path>
      --acc-no 008488L --sink-kind live`. §10 requires three consecutive clean
      sessions.

      Scoping to the sleeve is what makes this reachable at all: against the
      shared account it could never pass (§3.4), and against an empty sleeve a
      clean MATCH is the *expected* result from day one, before B4 places
      anything. That is a weaker check than it sounds — MATCH on an empty account
      proves only that we agree about nothing — so the three sessions that count
      are three with real fills in them.

> **Why first.** "What do I own and what did it cost" is the precondition for
> every order decision, every risk check, and every PnL claim. It is also the
> only Track B component that can be fully validated before a single order is
> sent.

### B2 — Risk layer *(no orders yet)*

- [x] Port and generalise `strategy/broker.go`'s `checkCaps` into a
      portfolio-level pre-trade check behind the `OrderSink` interface.
      *(`strategy/risk.RiskSink` decorates any `engine.OrderSink`. Caps are
      fractions of injected sleeve equity, never absolute IDR, and
      `SleeveEquityIDR` is a distinct type — so `cash/info`'s core-book
      figure, ~25× the sleeve, cannot be passed in silently (ADR-0017).)*
- [x] Enforce, in code: per-order notional cap, per-day notional cap, max gross
      exposure, max position per name, **daily loss limit**, kill switch.
      *(Exposure is accounted from order OUTCOMES, not intents: `Submit`
      reserves under the order id and commits only once the inner sink
      accepts; a rejected order releases its reservation, and `Cancel`
      releases the exposure while leaving the day's notional spent. Deriving
      it from intents is wrong in both directions — a broker-rejected sell
      frees headroom the real position still holds, and a placed-then-
      cancelled buy holds headroom forever. Remaining gap, bounded: a cancel
      racing a partial fill releases the whole reservation, because
      `OrderSink` carries no fill feedback. Exact positions need B4/B6.)*
- [x] Wire [reference 06](../reference/06-pre-trade-rules.md) rules as gates, not
      advisories. *(`strategy/market.Gate` is the endpoint-backed
      `risk.MarketGate`, 93.5% covered, race-clean, structurally read-only
      under the same build-failing test `strategy/account` uses.
      `strategy/cmd/market-probe` prints its verdict live and exits 1 only
      when a source could not be consulted — a deny is not a failure, a
      blind gate is.)*

      Sourcing, and the one place it is a proxy rather than a check:

      | Field | Source |
      |---|---|
      | `NotHoliday` | `collector/calendar`, evaluated in **WIB** — the box runs on UTC and the two disagree about the date between 00:00 and 07:00 |
      | `MarketOpen` | Exodus `market-time` **and** `market-time/session`; both must say open, either saying closed denies |
      | `Tradable` | Carina `GET /stock/tradable` |
      | `NotSuspended` | the same `/stock/tradable` row — **a proxy, not an independent check** |
      | `WithinBand` | Carina `GET /nego-engine/v1/order-book/{code}` |

      Two rows of reference 06 §10 turned out to be unreachable and are
      corrected there: §7.1's `StockService.Info` is a private service with
      no HTTP route (the real route is `GET /stock/tradable`, which the web
      client uses), and §7.2's `CompanyTradingAction.suspend_state` is on no
      client-subscribable WS channel at all. So there is **no independent
      suspension signal to be had**, and the broker's tradability flag
      carries `market.SuspensionProxyNote` on the value itself rather than
      only in a doc comment. The residual risk is one-directional: a
      suspension the flag has not caught yet costs an exchange auto-reject,
      not a bad fill.

      Every answer is cached with its own TTL (session 15s, tradability 60s,
      band 5m) and **a stale entry is never served** — past its TTL it is
      refetched, and a failed refetch reports Unknown, which rejects. That
      property, the WIB date, the both-sources-must-agree rule and the
      per-symbol cache isolation were each confirmed by mutating the code
      and watching the test fail (13 mutations, 13 caught).
- [x] **Board type (§8) is gated.** `engine.OrderIntent` now carries a
      `BoardType`, and `risk.checkStructural` admits `BoardRG` only. The
      zero value is **rejected, not defaulted to RG** — the same reasoning
      that makes `risk.TriState` a three-state type rather than a bool: a
      zero value that reads as a confident, correct answer is
      indistinguishable from a field nobody filled in. The two rejections
      are deliberately distinct messages, because they are fixed in
      different places: an unset board is a strategy that never said which
      venue it meant; a set-but-other board (TN/NG) is a strategy asking for
      a venue B4's `LiveSink` will not route to. `strategies/orb` declares
      `BoardRG` and a test pins it, so a strategy that stops declaring one
      fails in CI rather than at the first live order.

      Not done, and deliberately: the post-trade store does **not** persist
      the board type. `engine/store.go` creates its schema with
      `CREATE TABLE IF NOT EXISTS` and has no migration path, so adding a
      column would either be silently absent on an existing store file or
      break inserts against one. It needs a migration story of its own, and
      board type does not affect B1 reconciliation.
- [x] Kill switch must be trippable **externally** (file/flag), not only from
      inside the process. *(External file checked every `Submit`, plus an
      in-process trip on a confirmed daily-loss breach. Fails closed: any
      `stat` error other than "not exist" trips it — an unreadable kill
      switch is not a kill switch that failed to fire. No untrip.)*

> Must be complete and tested before B4 is armed. This is ADR-0016 gate G2/G3.
>
> **Status, 2026-09-15: the code is there; the live verification is not.**
> The caps, the kill switch and the structural checks are done and tested
> (`strategy/risk`, 97% covered), and the endpoint-backed gates now exist
> (`strategy/market`, 93.5%) with an end-to-end test proving each closed
> condition stops an order *before* it reaches the inner sink. Two things
> keep G2/G3 from being ticked outright:
>
> **Status, 2026-09-16 09:00 WIB: G2/G3 is satisfied.** `market-probe` was
> run live in SESSION_1 against a fresh token pair, and all five sources
> answered:
>
> - **BBCA @ 6550 → ALLOW, exit 0.** Every field allow, including
>   `state_name=STATE_NAME_SESSION_1 market.status=STATUS_OPEN` — which
>   settles the open question about the two-source corroboration rule, since
>   a `/market-time` that read CLOSE during a live session would have made
>   the gate deny everything.
> - **BBCA @ 8000 → DENY** on `within_band` alone (real band 5450..7675).
> - **ZZZZ → DENY, exit 1**: `is_tradable=false`, and the band unresolved
>   (HTTP 500), correctly reported as a blind gate rather than as an absent
>   constraint.
> - BNBR, HRTA and PTRO all parse, including BNBR's sub-200 tick band.
>
> Two things the run corrected, both now recorded in reference 06 §4: the
> ARA/ARB values arrive as **bare numbers**, not proto3-JSON strings (the
> permissive parser accepts both and stays), and `max_requestable_*` is
> **wider** than the hard band rather than equal to it, so the gate reads
> `below`/`above` alone.
>
> **Out-of-session case confirmed, 2026-09-16 12:49 WIB** (the lunch break,
> with a freshly minted token pair): `market_open=deny` on BBCA/BNBR/HRTA/
> PTRO, **exit 0**, and every other field `allow` — so the deny came from
> the session-phase check alone, not from a gate that had gone blind. See
> §10's checkbox for the full run.
>
> That run also settles a question the in-session run could not: the
> `/market-time` envelope reports `market.status=STATUS_OPEN` *during the
> lunch break*, while `/session` reports `state_name=STATE_NAME_BREAK`.
> `market.status` therefore answers "is the market open today", not "are
> orders accepted right now" — a gate reading it alone would send orders
> into the break. `market.Gate` reads `state_name`, which this run confirms
> is the load-bearing source. Recorded in reference 06 §4.

### B3 — `LiveSource`

- [x] Wrap the existing market-data websocket as `<-chan engine.MarketEvent`.
      `engine.Run` already takes a channel — this is an adapter, not a new engine.
      *(`engine.LiveSource`; `*feed.WsConn` satisfies the narrow `LiveConn`
      interface directly, asserted at compile time, so no second WebSocket
      client exists. `WSDialer` reuses `feed.Session`/`DialWS`/
      `BuildSubscribe`/`BuildPing` for the real handshake.)*
- [x] Reconnect/gap semantics identical to the replay path (ADR-0013 parity).
      *(Same 750ms–3s capped backoff and 60s read deadline as
      `cmd/collector`; `RECONNECT` emitted at the moment of disconnect,
      before the backoff wait, as `runWithReconnect` does; the same five-case
      kind mapping and running_trade batch expansion as `collector/replay`;
      `RecvNs` stamped at frame arrival before decode. The per-symbol
      switch_reset dedup map is per CONNECTION, matching where `runOnce`
      creates its own — carried across reconnects it would swallow a
      `SWITCH_RESET` the collector writes.)*
- [x] Holiday/session awareness via the plan-14 calendar reader
      (this absorbs roadmap **W4.2**). `LiveSource` refuses to start on a
      non-trading day with `ErrMarketClosed`, and `strategy-run` exits 0 on
      it so a scheduler does not read "closed today" as a crash. The two
      gaps recorded here on 2026-09-15 are both closed:

      - **The calendar is now re-checked before every reconnect**, not only
        at start, so a process that runs across midnight into a weekend or
        holiday stops instead of reconnecting into a closed market. It stops
        *after* emitting `RECONNECT` (the book really is invalid) and then
        closes the channel, ending the run the way the end of a replay does.
        A per-reconnect check is sufficient for a process that never
        restarts because `liveReadDeadline` (60s) breaks any connection
        within a minute of the feed going quiet, which routes every
        long-lived process through the reconnect path well before midnight.
      - **Both checks evaluate the date in Jakarta** (`calendar.Jakarta`,
        newly exported as the single definition), not in the host's zone.
        The collector box runs on UTC, where the two disagree for the seven
        hours after midnight WIB — 02:00 Saturday in Jakarta is still Friday
        19:00 UTC, and the unconverted check called that a trading day.
      - Intraday **session phase** is checked by B2's `MarketGate`
        (`strategy/market`), which now exists — it is a per-order question,
        not a per-connection one, so it belongs on the order path rather
        than here.

      *(Required `collector/internal/calendar` → `collector/calendar`, since
      Go's `internal/` is module-scoped and `go.work` does not lift it —
      ADR-0013's sixth promotion.)*

### B4 — `LiveSink` *(first real money)*

> **Status 2026-09-24:** `strategy/live` is code-complete and reviewed (three
> bugs fixed, each with a RED→GREEN test: fill bookkeeping committed before
> delivery, duplicate-resolution adopting a pre-existing — possibly core-book
> — order, and a failed timeout-cancel never retrying). It is **not yet wired
> into any `strategy/cmd` binary**, so no real order can be placed yet; the
> first-order procedure is `docs/runbooks/first-live-order.md`.

- [x] Carina order routing behind `engine.OrderSink`, replacing the single-symbol
      fire-and-forget `broker.go` path. *Wired 2026-09-24: `strategy-run --sink
      live --account … --prior-stores …`, always behind `risk.RiskSink`; refuses
      `--source replay`. Not yet exercised against production.*
- [ ] Idempotency keys; order state machine (new → acked → partial → filled /
      cancelled / rejected); cancel/replace; timeout handling. *State machine and
      timeouts done. Restart durability added 2026-09-24: client ids are
      deterministic per (symbol, window), `ui_ref`s per (client id, trading day),
      and `--prior-stores` (mandatory for live) seeds already-attempted windows
      from the day's earlier store files —
      `TestRestartDurability_CrashThenRestart_DoesNotDoubleSubmit` proves one POST
      across a crash and restart against a fake Carina. Open: whether Carina
      itself deduplicates on a repeated `ui_ref` is unverified, and a restart
      does not rebuild `RiskSink`'s in-memory exposure for an order placed but
      not yet filled.*
- [ ] **Restart-stable, per-leg order ids for intraday strategies** (found
      2026-10-13). The live sink dedups on `ui_ref = f(client order id, trading
      day)`. Each strategy's id scheme therefore decides what can trade and
      what a restart does:
      - `rebalance` uses `rebalance-<SYM>-<date>`: deliberately one order per
        symbol per day, so no same-day round trip. The first live test's GOTO
        sell had to go through the app.
      - `orb` uses `orb-<SYM>-<counter>`, and the counter restarts at 1 with the
        process. After a same-day crash and restart, a **new** trade reuses the
        morning's id and `ui_ref`, is treated as a duplicate, and is
        **silently skipped**. That fails safe (no double order) but drops the
        trade.

      Any multi-trade strategy needs ids that are unique per intended leg
      (entry ≠ exit ≠ re-entry) and stable across a restart for the same
      decision. Derive them from the decision (leg + signal time), or from a
      per-day sequence recovered from `--prior-stores`, never from an
      in-process counter. **Gate: before any intraday strategy runs `--sink
      live`.** Needs a restart test proving neither a duplicate order nor a
      dropped trade.
- [ ] Reconcile every terminal state back to B1. *`strategy-run` calls
      `Baseline` at startup and `ReconcileTerminal` at shutdown, logging any
      divergence; rejected intents now land in the store's `order_reject` table
      and on the dashboard. The realized-PnL
      feeding the daily loss limit keeps only the last partial fill's leg
      (`engine.recordRoundTrip`), so a multi-partial order understates it.*
- [x] Retain the existing double-arm: `ALGO_LIVE=true` **and** the verbatim
      `ALGO_CONFIRM` phrase (ADR-0016 gate G4). *`live.NewClient` refuses to
      construct unless armed; no dry-run fallback.*
- [ ] Absorbs plan 17 §6.2, §6.4, §6.5.

### B5 — Execution algo

- [ ] Do not market-order a basket. Arrival-price benchmark, time/volume slicing,
      participation cap from `constraints.max_participation_rate`, cancel-on-adverse.
- [ ] Absorbs plan 17 §6.3.

### B6 — Realized-cost measurement *(the point)*

Track C fills `mid_liq` and `low_liq` from the tape. B6 measures the half the
tape cannot see, and cross-checks C's answer against reality.

| Quantity | Source | Obtainable by |
|---|---|---|
| Price impact of aggressive flow | captured tape | **C** — offline, no orders |
| Fill rate, queue wait, adverse selection, rejects | your own resting orders | **B6 only** |

- [ ] Per fill, record: decision price, arrival mid, fill price, participation
      achieved, realized spread crossed, slippage vs arrival (bps), queue wait,
      reject/partial counts.
- [ ] **Exit criterion (§9):** reconcile realized-fill coefficients against Track
      C's tape-derived coefficients in **all three** buckets. Agreement within
      tolerance validates both methods and makes the hurdle trustworthy;
      divergence invalidates one of them and must be resolved before either is
      trusted.
- [ ] The pre-Track-C version of this criterion — "fill `mid_liq` and `low_liq`"
      — is **superseded**. C3 fills them, and a single unverified number is a
      weaker exit condition than a cross-check between two independent methods.
- [ ] Absorbs plan 17 §6.6 and completes [plan 15](./15-measured-fill-and-impact-calibration.md).

**Where the fills come from (decided 2026-10-13).** Fills from a strategy carry
the same B6 fields as calibration-only fills. The primary source is therefore the
**live forward test of a Track A candidate** (BNBR, HRTA) at 1–2 lots, not a
dedicated calibration programme.

- **Why.** The candidate needs a live forward test regardless of B6, so its fees
  and spread are paid either way. Merging the two removes the separate bill. A
  dedicated 1-lot programme was costed at roughly 0.5–1M IDR for ~100 round
  trips, and up to ~2M with low-liquidity names. About 0.40% of that is
  fee + tax and the rest is spread.
- **Trade-offs accepted:**
  - **Slower.** A daily single-name signal trades a few times a month, so ~100
    round trips takes months, not weeks.
  - **Narrow coverage.** One or two names cover one or two buckets. A
    calibration top-up fills only the buckets the candidates leave empty: a few
    resting-order round trips, budgeted at ~100–300k IDR under
    `strategy_id = "calibration"`.
  - **The edge stays unproven.** BNBR's case rests on `eff_N=28` and five days
    of one regime (§4.4a). Its forward test is a test, not income.
- **Books stay separate.** Strategy fills are booked under their own
  `strategy_id`, and cost metrics are computed from them per fill. A lucky
  strategy run must not read as cheap execution, nor an unlucky one as
  expensive execution.
- **Impact needs size.** At 1–2 lots the impact term is buried under spread and
  noise. These fills measure fill rate, queue wait, adverse selection and
  slippage, but they cannot check C3's impact coefficients. That cross-check
  needs a later, capped phase at a visible participation rate, roughly
  5–20M IDR per order, scheduled only after the small-size fills are in.
- [ ] **Prerequisite — the daily target emitter.** No job yet turns a Track A
      signal into `target_portfolio.json` (§3.1). Build it first: a pre-open job
      that writes the day's target for each forward-tested candidate. Then
      `strategy-run --strategy rebalance` trades it. It is the first producer on
      the seam.

### B7 — Ops

- [ ] Deploy `strategy-run` with the same self-check discipline the collector has
      (binary hash check, zero-record alert, freshness).
- [ ] Supervision, restart policy, alerting on kill-switch trip and on
      reconciliation divergence.

### B8 — Decay monitoring *(unblocks early)*

- [ ] [Plan 11](./11-decay-monitoring-and-live-revalidation.md) was hard-blocked
      on "live trading + post-trade store". B1 + B6 deliver real rows without an
      edge, so the decay loop and global off-switch can be built and exercised on
      calibration flow.

## 6. Track A — charter and freeze

Track A continues, but **not** by widening the same search. Three rules:

**A-1 — The closed bands stay closed, pending C4.** Daily price-pattern
(`daily_momentum`, `mean_reversion`, `volume_breakout`) at hold ≤ 2, tick
microstructure (plans 04/05/06), and intraday ORB are closed by measured
verdicts on large samples. Do not re-tune thresholds, holds or lookbacks in
them. Reopening one requires a new *mechanism*, not a new parameter.

One qualification, added with Track C. Those verdicts measured a *gross edge*
against a *modelled* hurdle, and for every name outside the 7 calibrated ones
that hurdle used `global_default = 1.0` (§4.3). C4 re-scores them against
measured cost. **A cell that C4 revives is reopened by evidence, not by tuning,
and does not violate this rule.** Whatever C4 leaves dead is closed for good.

**A-2 — Work the structurally untested regions, not the drilled one.** In
priority order:

1. **Event-driven with a named mechanism** — index inclusion/exclusion
   (LQ45 / IDX30 quarterly rebalance flow), rights issues, corporate actions.
   **6 cells have ever run.** [Plan 16](./16-strategy-triage-and-prioritization.md)
   already ranks `event_driven_gap` top at **3.73**. These edges are structural
   (a forced counterparty), measured in percent rather than basis points, and
   therefore the only class with a plausible margin over a 125 bps hurdle.
2. **The long-horizon band** opened by [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md)
   — blocked first by A-3.
3. Cross-sectional and pairs, which have 2 cells each.

**A-3 — Start recording point-in-time eligibility now.** This is Wave-0 class
and it is the one Track A item that is time-sensitive. ADR-0015 recorded that
the emitten funding endpoints return *currently tradeable* names by construction
(201/201 alive, zero delistings), `effective_date` is null, and the point-in-time
universe **cannot be reconstructed** — it can only be recorded going forward.
Every day without a snapshot is a day of universe history that is permanently
unavailable. Until this accrues, any long-horizon backtest on
`research/data/daily/` measures survivorship, not edge — as the +25%/yr first
pass demonstrated.

- [x] Daily snapshot of the day-trade ∩ trading-limit membership, appended to a
      durable store, with the run date as the key. **✅ Done 2026-09-14.**
      Snapshots land in S3 at `universe/dt=YYYY-MM-DD/eligibility.json`,
      date-partitioned like the market data and written on the collector's
      schedule rather than whenever a laptop is awake (`research/data/*` is
      gitignored, so a local file would be neither tracked nor backed up).
      **Both legs are stored, not just the intersection** — a name can leave the
      eligible set by falling off either list, and which one is not recoverable
      afterwards. Automated on the collector box as a boot-triggered
      `universe-snapshot.service`: EventBridge starts the instance at 08:30 on
      weekdays only, so one boot **is** one pre-open snapshot per trading day —
      no timer to drift and nothing to fire on a holiday. Deliberately decoupled
      from `collector.service` in both directions, because a failed snapshot must
      never stop capture (the book cannot be backfilled) and a failed capture
      must not stop the snapshot. `EmittenClient.fetch_subsector` **raises**
      where the screener's copy swallows HTTP errors: that degradation is right
      for a screening run and wrong for a recording one, where an expired token
      would write "nothing was tradeable today" down as fact, permanently.
      Recorded so far: 2026-09-13 and 2026-09-14, 118 day-trade / 198
      trading-limit / **115 eligible**.
- [ ] Emit the plan-12 requirement record for the historical gap this cannot fill.
      **Still open** — `research/out/data_backlog.md` does not exist yet, so the
      gap is documented in prose here and nowhere a machine can read it.

> Note the user-supplied correction preserved in ADR-0015's consequences: a
> near-suspended name typically *drops off* the eligibility lists before it dies,
> so the names missing from a historical backtest are those that **lost
> eligibility**, not those that were delisted — and Indonesian delistings are
> rare. The survivorship exposure here is eligibility churn, not death.

## 7. Reconciliation with previous plans

Every prior plan, and what this split does to it. Nothing is silently re-scoped.

| Plan | Was | Under this split |
|---|---|---|
| [00](./00-roadmap-research-to-live.md) roadmap | Waves 0–5 strictly serial; Waves 3–5 behind the edge gate | Waves 0–2 **closed, unchanged**. Waves 3–5 re-cut into Track A / Track B, run in parallel (§8). Pillar 2's checklist becomes B1–B7; pillar 3's becomes B2 + Track A sizing |
| [01](./01-market-data-capture.md), [02](./02-storage-format-and-replay.md), [03](./03-token-rotation.md) | Done | Unchanged. Track B depends on all three |
| [04](./04-microstructure-study.md), [05](./05-ofi-trade-flow-study.md), [06](./06-fill-simulator-and-backtest.md) | INCONCLUSIVE / NO-GO | Unchanged and **closed** (A-1). Not reopened |
| [07](./07-repo-restructure.md) | Done — layout mirrors pillars | Unchanged, and it is what makes the split cheap: `research/` is Track A, `strategy/` is Track B, already separated |
| [08](./08-alpha-factory-workflows.md) | In progress — Workflow B battery | Track A. **Deprioritised**: the battery industrialises a search whose coverage problem (§1) is a choice of region, not of throughput. More cells in the drilled corner is the failure mode, not the fix |
| [09](./09-universe-screener.md) | Done, Tier A | Track A. Tier B (order-book refinement) is unblocked by capture but is **not** on the critical path |
| [10](./10-strategy-fit-and-portfolio.md) | Done — 3 PASS, all dead on re-test | Track A, **frozen** under A-1/A-2. The harness stays; what it is pointed at changes. Its 1,389 cells are **re-scored by C4** on measured cost, and its hurdle becomes fully trustworthy after B6 reconciles C3 against realized fills |
| [11](./11-decay-monitoring-and-live-revalidation.md) | ⛔ Blocked on live trading | **Unblocked early** → B8. Calibration flow gives it real rows without an edge |
| [12](./12-data-gap-backlog-and-demand-driven-capture.md) | Draft | Track A. Its highest-priority record is now A-3's point-in-time universe gap |
| [13](./13-capital-allocation-and-capacity.md) | Duty-cycle batch allocator for the 300M leverage sleeve | **Premise superseded** by ADR-0015 (owned cash has no tenor clock, so the ~40–50% duty-cycle utilisation ceiling is void). Becomes Track A's sizing step feeding `target_portfolio.json` §3.1. Its **C\*** capacity caps move to Track C/B — they need measured impact (C3, confirmed by B6), not modelled |
| [14](./14-market-calendar-and-time-system.md) | Done | Unchanged. Roadmap W4.2 (wire the calendar reader into `strategy/engine/`) folds into **B3** |
| [15](./15-measured-fill-and-impact-calibration.md) | Capture-based impact calibration | **Necessary, not sufficient — and it stalled for a fixable reason.** It produced `high_liq` and left mid/low null; §4.2–4.3 show that was two defects, not a data limit. It becomes **Track C**, the offline half of cost calibration. What remains genuinely out of reach from captured book — queue position, fill rate, rejects — is **B6**, which also cross-checks C |
| [16](./16-strategy-triage-and-prioritization.md) | Done | Track A. Its top-ranked untested family (`event_driven_gap`, 3.73) is A-2 item 1 |
| [17](./17-execution-and-replay-parity-engine.md) | Build-now done; live routing behind the §2 three-condition gate | §2 conditions 1 and 3 **superseded by ADR-0016**; condition 2 retained but re-scoped to gate *size*, not the router's existence. §6.1–6.6 promoted from design sketch to **B3–B6**. Its open parity defect (the two ORB definitions) stays open but is no longer on the critical path, since Track B is validated against a null portfolio, not against ORB |

### ADRs

| ADR | Effect |
|---|---|
| [0011](../adr/0011-capital-model-300m-leverage-sleeve.md) | Already superseded by 0015; `LEVERAGED_SLEEVE` survives as a preset |
| [0013](../adr/0013-one-strategy-interface-and-collector-public-packages.md) | **Load-bearing and unchanged** — one Strategy interface is why B3/B4 are adapters rather than a second engine |
| [0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md) | Unchanged. Opens Track A's long-horizon band; its point-in-time universe caveat becomes A-3 |
| [0016](../adr/0016-alpha-and-infrastructure-are-separate-tracks.md) | This plan implements it |

## 8. Re-sequenced waves

```
  Waves 0–2 ........................ CLOSED (unchanged)
       │
       ▼
  TRACK C — measure the ruler   ← START HERE (no capital, no orders, data on disk)
  C1 extract 5 captured sessions
  C2 fix the two calibration defects
  C3 re-run calibrator → mid_liq / low_liq
  C4 re-judge 1,389 cells on measured cost
       │
       ├──────────────────────────────┬──────────────────────────────┐
       ▼                              ▼                              │
  TRACK B (infrastructure)      TRACK A (alpha)                      │
  B1 reconciliation  ← start    A-3 eligibility snapshots ← start    │
  B2 risk layer                 A-1 re-confirmed by C4               │
  B3 LiveSource                 A-2.1 event-driven mechanism         │
  B4 LiveSink  ← first money    A-2.2 long-horizon band              │
  B5 execution algo                                                  │
  B6 realized cost ──── reconciled against C3's coefficients ────────┘
  B7 ops                                    │
  B8 decay loop                             ▼
                                   Track A verdicts become conclusive
                                            │
                                            ▼
                              Wave 5 — live at size, on realized PnL
```

**C runs first, but does not hold up the two cheap starts.** A-3 and B1 begin
immediately alongside it: both are free, neither risks capital, and A-3 is losing
something every day it is deferred. What C gates is *interpretation* — A-1's
finality and the meaning of every hurdle downstream — not the calendar.

### Progress, 2026-09-14

| | State |
|---|---|
| **Track C** | ✅ **Complete.** C1–C4 done, including the long-horizon half of C4 (§4.4). The ruler is measured: buckets 0.29–0.35 against the assumed 1.0 |
| **Track A** | A-3 ✅ done and automated (the plan-12 record is the one piece left). A-1 partly reopened by C4 — BNBR and HRTA are live candidates, both caveated. A-2.1 (event-driven, the top-ranked untested family) is the open research question; A-2.2 is `data_blocked` on `MIN_EFF_N` |
| **Track B** | 🔨 **B1–B3 complete and live-verified (gate probed in SESSION_1, 2026-09-16).** Nothing existed before 2026-09-14: `strategy/` was the plan-17 scaffold — engine, replay source, paper sink, store, ORB — with no account client, no risk layer, no reconciliation. Every reference-06 rule is now gated, and `LiveSource` re-checks the calendar per reconnect. That run happened on 2026-09-16 and passed, so **ADR-0016 gate G2/G3 is satisfied and B4 is unblocked**. The seam it routes (`target_portfolio.json`) does not exist in code yet — nothing emits target weights and no portfolio-shaped Strategy exists — so that is the next build, ahead of `LiveSink` itself |

**Track B is now the critical path.** Track C measured the impact half of the
cost model and found it over-charged ~3×; the fill half — queue position, fill
rate, adverse selection, rejects — is reachable only through B6, and B6 is
reachable only through B1–B5. Until then every Track A verdict still rests on a
cost model that has been cross-checked against itself and never against a fill.

### Progress, 2026-10-13 — from first fill to an automated loop

B4 placed its first real orders today: one resting order was auto-cancelled, and
one GOTO lot filled with a clean reconciliation
([runbook §13](../runbooks/first-live-order.md)). The §10 sleeve trading-limit
probe is scheduled for 2026-10-14.

**A passing §10 shows the system trades safely. It shows nothing about
profit.** Profit needs two things that do not yet exist:

1. a **realized** cost model, which is B6;
2. an edge that clears that cost on a **pre-registered** test, which is Track A.

The order below assumes §10 passes. Each step is gated on the one before it,
not on a date.

| # | Step | Track | Gate to leave it |
|---|---|---|---|
| 1 | **Close B4.** Real-fill sessions 2–3 of B1. Verify that rebalance sells to zero when a symbol leaves the target. Identify the mechanism that routed orders to 008488L (§3.4: the order body carries no account field) | B | 3 clean real-fill sessions. Sell-to-zero observed live. Routing mechanism explained, or guarded by a both-account check that fails closed |
| 2 | **B5 execution algo.** Slicing, participation cap, cancel-on-adverse, arrival-price benchmark | B | A replay run and a de-minimis live run, each with per-child-order slippage vs. arrival recorded |
| 2a | **Daily target emitter.** A pre-open job writes `target_portfolio.json` from the BNBR / HRTA signals | A→B | Writes a correct target on N consecutive days, checked against the research signal. Runs with `--sink paper` before live |
| 3 | **B6, sourced from forward tests.** BNBR / HRTA live at 1–2 lots under their own `strategy_id`. Small `calibration` top-up only in buckets the candidates leave empty. Capped impact phase afterwards (B6 "Where the fills come from") | A+B | **Track B done (§9).** Realized coefficients reconcile with C3 in all three buckets |
| 4 | **B7 ops + B8 decay.** Unattended deploy, supervision, alerting, automated trading-token rotation (a 401 from an expired token hit 2026-10-13), and the plan-11 decay loop with a global off-switch | B | Runs a full session with nobody watching. Kill-switch and divergence alerts fire in a drill |
| A | **Candidates in parallel.** BNBR / HRTA forward tests (paper first, then de-minimis live, which is step 3's fill source). A-2.1 event-driven mechanisms. A-2.2 waits on accrued eligibility history (A-3) | A | A pre-registered pass on the **measured** hurdle and the always-long null gate. Re-judged after step 3 |

**"Automated alpha search" means automating the gauntlet, not the volume of
candidates.** §1 already showed the failure mode: more throughput in a single
drilled corner. The target loop, with every arrow a gate whose threshold is
written down before the test runs:

```
generate (plan 08, every family)
  → score on measured cost + always-long null gate (plan 10)
  → pre-registered OOS, multiple-testing penalty
  → paper shadow on the live feed
  → de-minimis live → staged capital
  → decay monitor (plan 11) retires it
```

The only human decision in the loop is **each step-up in capital**. Generation
and retirement run unattended.

**What decides whether this is worth building out:** step 3. If realized cost
confirms the measured hurdle and nothing clears it, §9(b) is the finding, and
the loop is not scaled up to search harder.

## 9. Definition of done

**Track C is done when** `impact_coeffs.json` carries non-null `mid_liq` and
`low_liq` coefficients at `eff_N ≥ MIN_IMPACT_OBS` derived from the captured
tape, **and** the plan-10 grid has been re-scored against them with the verdict
written down — including the case where the verdict is unchanged.

**Track B is done when `impact_coeffs.json` carries realized-fill coefficients in
all three buckets that reconcile with Track C's tape-derived coefficients, at
`eff_N ≥ MIN_IMPACT_OBS`.**

Not when it has features. This is stated as a number in a file precisely because
infrastructure-first has a runaway failure mode — gold-plating an execution stack
for a strategy that never arrives — and every prior wave lacked a falsifiable
exit condition. Rough order of magnitude: ~100 round trips in liquid names at
de-minimis size. The spend is a **research budget**, booked under
`strategy_id = "calibration"` (ADR-0016 §6), never as strategy PnL. *Since
2026-10-13 most of these round trips come from candidate forward tests booked
under their own `strategy_id`. Only the bucket top-up is `calibration` (B6,
"Where the fills come from").*

**Track A is done for this cycle when** either (a) a family in an A-2 region
clears the *measured* hurdle and the always-long null gate on a pre-registered
test, or (b) A-2's regions are exhausted and the honest finding is recorded —
that at a retail IDX cost stack the tradeable space is narrower than the catalog
assumed. **(b) is a legitimate outcome and must be written down as one**, not
treated as a reason to widen the search again.

## 10. Verification

- [ ] B1 reconciliation matches the live **sleeve** account (`008488L`, §3.4) by
      hand for 3 consecutive sessions **carrying real fills**. A MATCH on the
      empty sleeve does not count — it proves only that both sides agree there is
      nothing there.
- [x] B1's read path verified against the live account 2026-09-14: all four
      account endpoints plus the order list parse, and the per-position
      `amount_invested` sums to exactly the independently-fetched
      `/portfolio/v2/summary` aggregate. Four contract errors in
      `docs/reference/` were found and corrected in the process — the settlement
      identity among them (§3.4).
- [x] B2 limits provably reject: an over-cap order, an over-exposure basket, and a
      post-loss-limit order — each with a test. Done 2026-09-24 in
      `strategy/risk/limits_verification_test.go`; each confirmed non-vacuous by
      disabling its check in `risk/sink.go` and watching the test fail.
- [x] B2's reference-06 gate provably rejects each closed condition —
      holiday, lunch break, not-tradable, price outside ARA/ARB, and an
      unreachable endpoint — with the intent never reaching the inner sink
      (`strategy/market`, `TestRiskSink_WithRealGate_RejectsEveryClosedCondition`).
      Confirmed non-vacuous by mutating `Gate.State` to always allow and
      watching all five sub-cases fail.
- [x] Board type is gated and the zero value rejects
      (`TestSubmit_BoardTypeIsGated`, `TestBoardTypeRejectionsAreDistinguishable`),
      and `strategies/orb` is pinned to declaring `BoardRG`
      (`TestORBDeclaresTheRegularBoard`). Four mutations, four caught.
- [x] `LiveSource` stops rather than reconnecting when the day rolls over
      (`TestLiveSource_StopsWhenAReconnectFallsOnANonTradingDay`), still
      reconnects on a live trading day
      (`TestLiveSource_KeepsReconnectingWhileTheDayIsStillATradingDay`), and
      evaluates the date in Jakarta at both the start and the reconnect
      check. Four mutations, four caught — including deleting the check,
      forcing it always-true, and dropping the timezone conversion.
- [x] **`market-probe` run live, inside a trading session, against a real
      symbol.** Done 2026-09-16 09:00 WIB, SESSION_1: BBCA @ 6550 → ALLOW
      exit 0; BBCA @ 8000 → DENY on the band alone; ZZZZ → DENY exit 1 with
      the band unresolved; BNBR/HRTA/PTRO parse. All five wire contracts
      confirmed against production, and two were corrected by it (reference
      06 §4).
- [x] The same probe run **outside** a session — at the 11:30 break or after
      the 16:00 close — expecting a clean `market_open=deny` and **exit 0**.
      Done 2026-09-16 12:49 WIB, inside the lunch break: `market_open=deny`
      on BBCA/BNBR/HRTA/PTRO citing `state_name=STATE_NAME_BREAK`
      (12:00:00-13:29:59), **exit 0 on both runs**. What makes it a real
      verification rather than a blind gate denying by accident: every
      *other* source answered `allow` on the same run (`is_tradable=true`,
      ARB 5450 <= 6550 <= ARA 7675), so the deny is isolated to the
      session-phase check. A first attempt at 12:48 hit an expired trading
      token, returned three `unknown` fields and exited 1 — the correct
      blind-gate behaviour, and exactly why an expired-token run proves
      nothing either way; the token was re-minted and the probe re-run
      inside the same break.
- [ ] B3/B4 replay-vs-live parity: the same null portfolio through `ReplaySource`
      + `PaperSink` and through `LiveSource` + `LiveSink` produces the same
      *intents* (fills will differ; intents must not).
- [x] C2's two fixes each land with a failing test first (13 new tests, RED then
      GREEN; full suite 412 passed / 0 failed / 12 skipped).
- [x] **The units fix must not move any fitted coefficient.** Verified
      end-to-end, not only by unit test: the same legacy dataset
      (`research/out`) run through `HEAD`'s code and through the fixed code
      returns coefficients identical to the last digit — BUMI
      `0.531376689426782`, DEWA `0.5327797444468003`, INDY
      `0.43458019330279085`, ITMG `0.49495343152208954`, MDKA
      `0.46662285525232755` — while every ADV falls by exactly 100× and INDY and
      ITMG move `high_liq` → `mid_liq`. That is the whole defect, isolated.
- [x] C3 produces non-null `mid_liq` (0.3007, `eff_N` 6,843) and `low_liq`
      (0.2926, `eff_N` 1,799). The `high_liq` aggregate was not expected to hold
      at 0.377 — its membership legitimately changed (of the 7 previously
      calibrated names only ENRG, PTRO and TPIA are still `high_liq`; BRPT, RAJA,
      RATU and BREN are `mid_liq` on corrected ADV) — yet it landed at **0.3452**,
      within 8%. Two independent windows agreeing at the bucket level is the
      cross-check that the first C3 run failed.
- [x] **C4 run as a controlled A/B**, same day and data with `--use-calibrated`
      on and off, so every changed cell is attributable to the coefficient alone.
      Status flips: 0. Cells that stopped being cost-gated: 13. Surviving cells:
      1 → 3.
- [x] **The long-horizon band is verified.** The grid enumerates 2,093 cells
      across holds `[1, 2, 5, 20, 60]`; hold=5 tested 344 of 345 cells and
      produced HRTA `volume_breakout` h5 at +257.0 bps (UNDERPOWERED, `eff_N=62`).
      Holds 20 and 60 come back `insufficient_n` on 345/338 cells — a measured
      data limit under `MIN_EFF_N=30` after de-overlap, recorded as such rather
      than left blank (§4.4).
- [ ] C4's re-scored grid is diffed against the pre-C verdict; every changed cell
      is attributable to the coefficient change and to nothing else.
- [ ] B6 divergence check against Track C's coefficients in all three buckets.
- [ ] Kill switch trips from outside the process and is observed to stop orders.
      *Test-level evidence 2026-09-24:
      `TestKillSwitch_TrippedByExternalProcess_StopsOrders` has a separate OS
      process create the kill file and the next `RiskSink.Submit` is rejected
      without reaching the inner sink. Stays open until exercised against a
      running `strategy-run`.*

## 11. Out of scope

- **Short selling.** Long-only until a Track A strategy requires it; negative
  weights are a schema error (§3.1).
- **Intraday / high-frequency execution.** The closed bands (A-1) do not need it.
- **Multi-account or multi-broker routing.**
- **Track A's `MIN_EFF_N=30` question at a ~20-day hold** — flagged unresolved by
  ADR-0015 and still unresolved here.
- **Plan 17's ORB parity defect.** Stays open, off the critical path (§7).
