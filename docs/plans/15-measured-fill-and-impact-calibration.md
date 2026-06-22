# Measured Fill & Impact Calibration — Replacing the Assumed Slippage Curve with a Measured One

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-22
> **Related:** [ADR-0011: capital model](../adr/0011-capital-model-300m-leverage-sleeve.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [13-capital-allocation-and-capacity.md](./13-capital-allocation-and-capacity.md) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) · [reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md) · [Plans index](./README.md)

> **What this is.** Plan 10's intraday ORB survivors (PTRO/TPIA/BRPT/ENRG/RAJA
> ORB + PTRO intraday\_momentum) carry an impact charge derived from an *assumed*
> square-root Almgren model with hand-picked constants
> (`research/strategy_fit/impact.py`: `SQRT_IMPACT_COEFF=1.0`,
> `POSITION_NOTIONAL_IDR=30M`). The impact sweep
> (`research/out/impact_sweep.md`) confirmed the ORB core is insensitive to
> coeff {0.5, 1.0} × size {15M, 30M, 60M} — but that is a *sensitivity* check
> on an unanchored model, not a measurement. The single biggest remaining risk
> to the provisional PASS is that the modelled impact is simply wrong. This plan
> derives the per-name impact coefficient from forward-captured order-book and
> trade-tape data rather than from a literature constant, and feeds the
> calibrated coefficient back into plan 10 (survivor re-run) and plan 13 (C*
> capacity caps). It is the capture-based precursor to the live realized-fill
> loop described in roadmap §2 — that live loop is plan 17.

## 1. Why now

Three facts make this the right next measurement step:

- **Plan 06 proved fills are exactly where IDX edges die.** Tick-scale OBI1
  died not because the signal was wrong but because the 40 bps round-trip fee
  ([reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md)) plus
  the crossed spread consumed every gross basis point — and the gross leg was
  already negative before fees landed. When a cost item is the kill-shot, it
  must be measured, not assumed.

- **Plan 10's ORB survivors carry an unanchored impact estimate.** The
  `SQRT_IMPACT_COEFF=1.0` constant in `research/strategy_fit/impact.py`
  is the conservative end of the Almgren et al. (2005) empirical range
  (0.5–1.0), picked for pre-live sizing research. On IDX names at 30M IDR
  notional, the computed impact term is the margin between a provisional PASS
  and a borderline result. If the true coefficient for PTRO is 0.3, the
  survivors look better than modelled; if it is 1.5, they fail the gate
  they appear to clear. Neither direction can be resolved by another sensitivity
  sweep — only a measurement resolves it.

- **The collector watchlist was repointed.** As of 2026-06-20, the capture
  pipeline (`watchlist_id=plan10-survivors`) is forward-capturing order-book
  and trade tape for the 7 survivor names (PTRO, TPIA, BRPT, ENRG, RAJA plus
  the PTRO intraday\_momentum cell). The raw material for a measured cost
  decomposition is accruing now. Waiting to act is waiting to lose runway.

## 2. Locked decisions

**2.1 Measure, don't assume.** Derive time-weighted relative spread (bps),
L1/L5 displayed depth, and a fitted realized temporary-impact curve vs
participation rate from the captured book and trade tape. Do not carry a
literature constant into a live deployment decision.

**2.2 Calibrate the existing model — do not swap model families.** The
square-root Almgren form in `impact.py` scales analytically in `SQRT_IMPACT_COEFF`:

```
impact_bps = 2 · coeff · daily_vol_bps · sqrt(participation_rate)
```

Plans 10 and 13 both consume this interface. Recovering an empirical `coeff`
by fitting the observed impact curve against participation — without changing
the functional form — drops straight into `impact.py` without touching either
consumer. A proposed ADR should record this interface-stability commitment if the
calibration findings cause material re-evaluation.

**2.3 Forward-capture only — no backfill.** The IDX order book cannot be
reconstructed retroactively from the segment store (only top-5 snapshots, no
full depth-rebuild from deltas). Calibration runs solely on data captured going
forward from `watchlist_id=plan10-survivors`. This is a deadline-style
dependency: capture must accrue before calibration is meaningful; coordinate with
plan 14's deadline mechanism and the EventBridge schedule that starts the
collector.

**2.4 Gate the calibration by effective-N.** A calibration on three days of
book data is as thin as a 1-day hit-rate study — the same error that produced
the plan-04 INCONCLUSIVE. Per-name temporary-impact observations are sparse by
construction (they require a measurable price response to an identifiable flow
event). The calibration harness must report `eff_N` (number of independent
impact observations, de-overlapped on the same discipline as plans 04/05/06);
a per-name coefficient is only published once it clears a floor (proposed
`MIN_IMPACT_OBS=20` independent observations per name). Names below the floor
fall back to the bucket or global default coeff until their window fills.

**2.5 Two-way feedback.** The calibrated coefficients feed back in two
directions: (a) re-run the plan-10 grid with the measured coeff and record the
survivor delta in the plan-10 status file; (b) plan 13's per-name C* capacity
caps use the same calibrated curve, as C* is derived from the same square-root
impact law (plan 13 §5).

## 3. What is being measured

Three cost components, computed per survivor name from the captured CSVs:

**3.1 Time-weighted relative spread (bps)**

```
spread_bps(t) = (ask_px_0(t) − bid_px_0(t)) / mid(t) × 10_000
```

Time-weighted over the continuous session (08:59–11:30, 13:30–15:49 WIB per
`research/lib/session.py`), excluding auction windows. This is the observable
half-round-trip cost paid by a taker on each leg, independent of size. It also
cross-checks the plan-09 universe annotations — if the screener's
`min_edge_to_clear_bps` estimate diverges from the forward-captured spread,
the annotation is the one to update.

**3.2 Displayed depth at L1 and L5**

```
depth_L1(t) = bid_vol_0(t) + ask_vol_0(t)   (in lots, 100 shares each)
depth_L5(t) = sum of bid/ask vols at levels 0–4
```

Time-weighted medians. Depth at L1 governs when a 30M IDR order (≈ the plan-10
slice) exhausts the top of book and begins walking the book; depth at L5 bounds
the no-walk zone. If median depth at L1 covers 30M IDR notional comfortably,
the participation rate is low and impact is small — the assumed coefficient is
conservative in the right direction and the survivor verdict is safe. If L1 is
thin, the model may be an underestimate.

**3.3 Realized temporary impact vs participation — the calibration target**

For each identifiable order-flow event in the trade tape where a directional
aggressor sequence drives a measurable price response, measure:

```
observed_impact_bps = (mid_after_T − mid_before) / mid_before × 10_000
participation_rate  = event_notional_idr / adv_notional_idr
```

Fit the square-root model to these observations:

```
observed_impact_bps ≈ 2 · coeff · daily_vol_bps · sqrt(participation_rate)
                                                            ^
                                               OLS on sqrt(participation_rate)
                                               → coeff is the slope / (2·vol_bps)
```

This is a single-parameter OLS regression (intercept forced through zero per
the Almgren model). The fit is per name where `eff_N ≥ MIN_IMPACT_OBS`, and
a single liquidity-bucket coefficient where individual names are thin.

The `sbcap features` CSV (`bid_px_0..ask_vol_4`, `recv_ns`, `iso_wib`) and
`sbcap trades` CSV (`price`, `vol`, `side`, `trade_num`) are the inputs — the
same Go subcommands that have been the data contract since plans 04/05. No new
Go work is required.

## 4. Calibration harness design

A Python calibration harness under `research/strategy_fit/fill_calibration.py`,
consuming the survivor-name CSVs as accrued from S3 capture.

**Ingestion.** Load features and trades CSVs from a configurable path prefix
(e.g. `data/survivors/PTRO/`), concatenate across available capture dates,
filter to continuous session, apply the same `filter_continuous_session` /
`_crosses_lunch` guards from `multisym_obi.py`. Validate that at least
`MIN_CALIBRATION_DAYS` (proposed: 5 trading days) of data are present before
running; emit a clear "insufficient data — N days captured, need M" message and
exit cleanly otherwise.

**Spread and depth summary.** Compute `spread_bps`, `depth_L1_lots`,
`depth_L5_lots` as time-weighted medians per name over the full captured window.
Emit a per-name table as the first section of the report.

**Impact event detection.** Identify directional aggressor sequences in the
trade tape using the `side` field: a run of B-side (or S-side) prints where
the accumulated volume exceeds a minimum threshold (proposed: 10% of median L1
depth, to exclude noise prints). Measure `mid_before` from the features CSV at
the first print of the run and `mid_after_T` at a fixed post-run window (e.g.
30 seconds after the last print in the run), discarding any runs that overlap
the lunch break or cross the session close.

**Per-name fit.** Regress `observed_impact_bps / (2 · daily_vol_bps)` on
`sqrt(participation_rate)` via OLS (intercept=0). Report `coeff`, `R²`,
`eff_N` (number of independent — non-overlapping — impact observations), and a
`confidence` flag: `provisional` if `eff_N < MIN_IMPACT_OBS`, `confirmed` once
it clears. Names flagged `provisional` are reported but fall back to the bucket
or global default in the plan-10 re-run.

**Bucket fallback.** Assign each name to a liquidity bucket by median ADV
(e.g. `high_liq: ADV > 100B IDR`, `mid_liq: 20B–100B`, `low_liq: < 20B`).
Pool confirmed-name observations within a bucket to produce a bucket-level
coeff as the fallback for provisional names in the same bucket.

**Config emission.** Write `research/strategy_fit/impact_coeffs.json`:

```jsonc
{
  "generated": "2026-06-22",
  "min_impact_obs": 20,
  "global_default": 1.0,
  "buckets": {
    "high_liq": { "coeff": null, "eff_N": 0, "confidence": "provisional" },
    "mid_liq":  { "coeff": null, "eff_N": 0, "confidence": "provisional" }
  },
  "per_name": {
    "PTRO": { "coeff": null, "eff_N": 0, "confidence": "provisional", "bucket": "mid_liq" }
    // ...
  }
}
```

`impact.py` reads this file at module load (falling back to `SQRT_IMPACT_COEFF=1.0`
if the file is absent, preserving all current behaviour). The plan-10 re-run
sets `use_calibrated=True` to pull from the file; plan 13's C* calculator does
the same.

## 5. Output artifacts

**`research/out/fill_calibration.md`** — the "measured vs modelled"
reconciliation report. One row per survivor name:

| Name | spread\_bps (meas.) | depth\_L1\_lots | modelled impact (bps) | measured impact (bps) | implied coeff | eff\_N | confidence |
|---|---|---|---|---|---|---|---|
| PTRO | — | — | — | — | — | — | provisional |
| … | | | | | | | |

Plus a summary section noting: where modelled > measured (conservative — survivor
verdict is safe or better), where modelled < measured (non-conservative — the
cell may be weaker than the provisional PASS implies), and a "survivor delta"
entry reserved for the plan-10 re-run result once the calibrated coefficients
are applied.

**`research/strategy_fit/impact_coeffs.json`** — the machine-readable config
consumed by `impact.py`, plan 10's grid runner, and plan 13's C* calculator.

**Plan-10 status annotation.** After the re-run, record the survivor-before /
survivor-after delta in `research/out/strategy_portfolio.md` (or a dedicated
`impact_calibration_delta.md`). The annotation must state per-name: was the cell
strengthened, weakened, or unchanged by the measured coeff?

## 6. Dependency chain and timeline

This plan has a hard forward-capture dependency. The calibration is
meaningless until enough capture days accrue for the survivor names. Key
milestones to track in `calendar/deadlines.yaml` (plan 14 §7.6):

1. **Capture confirmation** — verify `watchlist_id=plan10-survivors` is live and
   accruing; check S3 inventory for at least 1 complete session per survivor name.
   (Should be immediate — the watchlist was repointed 2026-06-20.)
2. **Minimum calibration window** — `MIN_CALIBRATION_DAYS=5` trading days of
   complete sessions per name. At one session per trading day this is ≈ one
   calendar week after capture confirmation.
3. **First provisional run** — run the harness, emit `fill_calibration.md` with
   `confidence=provisional` for all names, identify which names are
   impacting-as-modelled vs. not.
4. **Confirmed-coeff run** — once `eff_N ≥ MIN_IMPACT_OBS` clears for any name,
   re-run and promote its coeff to `confirmed`; emit the plan-10 re-run delta.
5. **Plan-10 and plan-13 update** — feed confirmed coeffs into the impact.py
   config; re-run the plan-10 grid and record the survivor delta; update plan 13's
   C* table.

Because this plan unlocks the plan-10 survivor verdict *and* plan 13's C* caps,
it is sequenced before plan 16 (strategy triage) and plan 17 (execution and
replay parity) draw on those outputs.

## 7. Implementation

- [ ] **7.1** `research/strategy_fit/impact_coeffs.json` — seed file with
  `confidence=provisional` and `coeff=null` for all 7 survivor names and the
  global default `1.0`. Update `research/strategy_fit/impact.py` to load the
  file at module init and expose `get_impact_coeff(symbol)` that resolves
  per-name → bucket → global default in that order, with a `use_calibrated`
  flag (default `False` to preserve all current behaviour).
- [ ] **7.2** `research/strategy_fit/fill_calibration.py` — ingestion and
  validation: load features + trades CSVs for the survivor names from a
  configurable data root, concatenate across days, apply session filters,
  check `MIN_CALIBRATION_DAYS` and emit a clean "insufficient data" exit if
  unmet. Add pytest unit tests for the session-filter and day-count logic.
- [ ] **7.3** Spread and depth summary: compute time-weighted `spread_bps`,
  `depth_L1_lots`, `depth_L5_lots` per name; unit-test against a synthetic
  features fixture with known values.
- [ ] **7.4** Impact event detection and de-overlap: identify directional aggressor
  runs in the trade tape (`side` field), compute `observed_impact_bps` and
  `participation_rate` per run, de-overlap runs (enforce non-overlapping windows
  as in plans 04/05/06), discard runs crossing the lunch break or session close.
  Unit-test: a synthetic tape with two overlapping runs collapses to one
  observation.
- [ ] **7.5** Per-name OLS fit: regress `observed_impact_bps / (2·daily_vol_bps)`
  on `sqrt(participation_rate)` with zero intercept; extract `coeff`, `R²`,
  `eff_N`; set `confidence` flag. Bucket-level pooled fallback for provisional
  names. Unit-test: a synthetic set of (participation, impact) pairs with a
  known true coeff=0.7 recovers the coeff to within rounding.
- [ ] **7.6** Write `impact_coeffs.json` with per-name and bucket-level results;
  emit `research/out/fill_calibration.md` with the measured vs modelled
  reconciliation table (§5).
- [ ] **7.7** `calendar/deadlines.yaml` entries (plan 14 §7.6) for: capture
  confirmation check (immediate), minimum calibration window, first provisional
  run, confirmed-coeff run. These are passive deadline records, not executors.
- [ ] **7.8** (Unblocked once `confirmed` coeffs exist) Re-run the plan-10 grid
  with `use_calibrated=True`; record the survivor-before / survivor-after delta
  in `research/out/fill_calibration.md` §"Survivor delta". Update plan 13's C*
  table with the calibrated impact model.
- [ ] **7.9** (Follow-on) Extend `impact.py` beyond intraday cells (currently
  `hold=0` only) to daily and event cells once plan 13 requests it. The
  calibrated `coeff` from 7.5 is already in the config; this step adds the
  shape dispatch.

## 8. Verification

The validation story has two tiers: internal self-consistency (catching
implementation errors), and external plausibility against priors (catching model
errors).

**Tier 1 — unit tests and self-consistency (build / CI)**

- **Session filter** — no rows outside continuous session hours appear in the
  impact dataset; lunch-straddling runs are dropped.
- **De-overlap discipline** — the de-overlapped `eff_N` is always ≤ the raw
  run count; a fixture with two overlapping runs yields `eff_N=1`.
- **Fee consistency** — the spread computed from features CSVs matches the
  plan-09 `min_edge_to_clear_bps` annotation to within a tolerance (structural
  alignment check). A large divergence is a data-quality alarm, not a silent
  pass.
- **OLS fixture** — a synthetic (participation, impact) dataset with a planted
  `coeff` recovers the planted value within 5% in the unit test.
- **Config roundtrip** — `get_impact_coeff(symbol)` resolves per-name coeff when
  `confirmed`, bucket coeff when `provisional` but bucket has data, global default
  (1.0) otherwise; unit-tested for each path.
- **Backward-compat guard** — with `use_calibrated=False`, `compute_impact_bps`
  returns the same value as before this plan existed (the existing test suite
  in `test_impact.py` still passes without modification).

**Tier 2 — plausibility against priors**

- **Direction check** — the measured coeff for every name falls in [0.2, 2.0];
  a fit outside this range flags a data-quality or methodology problem (wrong
  event detection), not a discovery.
- **Spread sanity** — time-weighted `spread_bps` for the survivor names is
  consistent with the plan-09 universe-screen annotations (same price levels,
  same tick ladder). A material divergence (>20%) triggers a screener annotation
  update, not a silent pass in the calibration report.
- **Survivor delta is bounded** — re-running the plan-10 grid with calibrated
  coeffs changes the survivor set by at most a few cells. A large swing (>50%
  of cells flipping) indicates the prior model was badly wrong and warrants a
  documented finding before proceeding to plan 16.
- **C* consistency** — plan 13's C* caps recomputed with the calibrated coeff
  move in the same direction as the impact change (lower coeff → higher C*,
  higher coeff → lower C*); any monotonicity violation is a bug in the C*
  calculator, not a finding.

**Behavioural checks (per-consumer)**

- **Plan 10 round-trip** — `strategy_portfolio.md` produced with
  `use_calibrated=True` reproduces the same survivor set as the hand-checked
  baseline when the calibrated coeff equals 1.0 (the prior default).
- **Plan 13 round-trip** — `cycle_allocation.md` C* caps tighten (or loosen)
  monotonically as `coeff` is stepped from 0.5 to 1.5 in a synthetic test.

## 9. Out of scope

- **Live order routing and real-time fill measurement.** The live realized-fill
  loop — comparing filled prices against pre-trade model predictions trade-by-trade
  and updating the model in near-real-time — is plan 17 (execution and replay
  parity engine). This plan is the capture-based, research-time precursor.
- **Non-survivor names.** Calibration covers the 7 `watchlist_id=plan10-survivors`
  names only. Extending to the full plan-09 universe requires extending the
  capture watchlist first (plan 12 / demand-driven capture).
- **Alternative model families beyond square-root Almgren.** Power-law,
  linear-in-participation, and regime-switching impact models are out of scope.
  The square-root form is locked (§2.2); replacing it would require an ADR and
  an interface change to both plan 10 and plan 13 consumers.
- **Permanent-impact estimation.** The Almgren model in `impact.py` is
  *temporary* impact (price reverts after execution). Permanent impact (adverse
  selection cost of information leakage) is not modelled here; it is partially
  captured in the plan-06 passive fill model's adverse-selection term and is
  left for a later calibration pass if the magnitude proves material.
- **Intraday bar store back-calibration.** The existing chartbit intraday history
  (PTRO/BRPT/TPIA/ENRG, ~2026-03-25 → 2026-06-19) does not carry the
  trade-tape `side` field or order-book depth, so it cannot substitute for
  forward-captured `sbcap` data in the impact event detection step.

---

Prev: [14-market-calendar-and-time-system.md ←](./14-market-calendar-and-time-system.md) ·
Index: [README](./README.md)
