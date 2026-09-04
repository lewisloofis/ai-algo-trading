# Strategy Triage & Hypothesis Prioritization — Deciding Which Family Is Worth Researching

> **Status:** ✔️ Done · **Owner:** @lewis · **Updated:** 2026-06-26

> ✅ **Result: Complete.** `catalog_meta.py` (10 families, full plan-10 §4 taxonomy), `triage_scorer.py` (pure scoring — imports ROUND_TRIP_BPS/financing_bps/impact_bps, no redeclarations), `triage.py` CLI → `out/strategy_triage.md` + `out/strategy_triage_gaps.json`. 6 tests passing. Ranked output: daily_momentum on ITMG (score 2.66) and event_driven_gap at universe (2.58) are top priorities; obi_ofi_tick cost_gated; 3 data_blocked/capital_blocked families emit plan-12 requirement records.
> **Related:** [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (pillar 1 alpha factory) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) (Workflow B, the validation battery this plan feeds) · [09-universe-screener.md](./09-universe-screener.md) (source of per-name ADV/vol/spread/tick annotations) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) (consumer — this plan sets its run order) · [12-data-gap-backlog-and-demand-driven-capture.md](./12-data-gap-backlog-and-demand-driven-capture.md) (routes `data_blocked` families) · [15-measured-fill-and-impact-calibration.md](./15-measured-fill-and-impact-calibration.md) · [17-execution-and-replay-parity-engine.md](./17-execution-and-replay-parity-engine.md) · [reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md) · [ADR-0011: capital model](../adr/0011-capital-model-300m-leverage-sleeve.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [Plans index](./README.md)

> **What this is.** A cheap, deterministic, transparent prioritization layer —
> the alpha factory's *brain* — that decides which strategy family is worth
> researching next. Plan 08's Workflow B orchestration is the factory's *muscle*:
> it runs the full validation battery once a family has been admitted to the grid.
> This plan does the ranked, quantified triage that decides what enters that grid
> in the first place and in what order, before any expensive validation compute is
> spent. It makes the plan-10 §4 static taxonomy RANKED and QUANTIFIED. It is
> independent of the plan-08 Workflow substrate, which remains correctly deferred
> until candidate volume justifies the orchestration overhead.

## 1. Why now

The strategy catalog in plan 10 §4 is a static, hand-authored taxonomy. Every
family is listed, status-tagged (`testable_now` / `data_blocked` / `cost_gated`
/ `deferred`), and briefly annotated — but the table is flat. It answers "can we
test this?" but not "should we test this next?" and not "how much do we lose by
testing family B before family A?"

Two consequences follow from that gap:

- **Test budget is spent arbitrarily.** The plan-10 grid and plan-08 Workflow B
  admit families roughly in catalog order, not in descending expected return on
  research effort. A family whose edge-to-friction ratio is structurally poor is
  validated before a family that is likely to survive, wasting the expensive
  compute (and the researcher's attention).
- **`data_blocked` families are silently deferred.** Today a blocked family
  acquires a status tag and nothing more. It never becomes a structured demand on
  the collector (plan 12), so it never auto-promotes when the data need is
  satisfied.

The piece that answers "which family next?" — a cheap triage and prioritization
layer — is missing, needed now, and independent of the plan-08 Workflow substrate.
It is cheap enough to build and run immediately, and its output is the input the
plan-10 grid needs to run in a sensible order.

### The brain / muscle framing

Plan 08 is the *muscle*: it orchestrates the validation battery across many
candidates in parallel, applies adversarial refutation, and runs the barrier
multiple-testing correction. That machinery is correctly deferred — it pays off
only when candidate volume justifies the fan-out overhead.

This plan is the *brain*: it ranks the candidates before they reach the muscle.
A factory that has a muscle but no brain runs every hypothesis with equal
priority, including the ones structurally unable to survive. A factory with only
a brain and no muscle never validates anything. Both are required; they are built
in this order because the brain is cheap and its output gates the muscle.

## 2. Locked decisions

**1. Cheap, deterministic, transparent Python — not a Workflow and not an LLM
on the scoring path.** The scoring function is a formula operating on inputs
already computed by plan 09 (per-name ADV/vol/spread/tick annotations) and a
catalog-metadata table (family → shape, horizon, characteristic-move prior,
natural observation unit). No LLM judgment is involved in the ranking.
Consistent with plan 08 §2's discipline test: the per-family scoring step is
mechanically wide (all families, all names) but every step is deterministic
arithmetic — "if you can hold it in your head, just do it." A Workflow wrapper
adds no value here.

An LLM may optionally *propose* novel candidate families upstream (hypothesis
generation), but generation and scoring are kept as strictly separate steps so
the ranking is reproducible and auditable. The generation step does not touch
the scoring path.

**2. Prioritizer, not validator.** The triage layer runs BEFORE the expensive
plan-10/plan-08 validation battery and decides where to spend test budget. It
makes no statistical edge claims — no p-values, no survivorship, no backtest
results. Those are plan 10's job. The output of this plan is a ranked list of
candidates with a score and a status tag; the verdict on whether a candidate
actually has edge belongs entirely to plan 10 and plan 08 Workflow B.

This boundary is intentional and must be preserved. A triage layer that runs
quick backtests to rank candidates has already run a backtest — it has become
plan 10 with a smaller grid. The triage score is a prior, not a posterior.

**3. Score components — all derivable from cheap inputs already available.**
The headline score for a `(name, family)` pair (or for a family across the
universe) has three components, all computable without running a backtest:

- **Expected gross edge per round-trip.** The family's characteristic move size
  from coarse priors — for example, a daily-momentum family on IDX names targets
  moves of 100–300 bps per swing; an event-driven gap family targets the gap
  magnitude distribution observed in daily OHLCV. These are not backtested
  estimates; they are the family's *structural* move premise. The catalog-metadata
  table carries a `gross_edge_prior_bps` per family, derived from published
  empirical ranges for the strategy class and cross-checked against the plan-09
  OHLCV distributions.

- **Friction floor for that name and horizon.** The cost hurdle the strategy
  must clear on this name: 40 bps base round-trip (from `research/backtest/
  backtest.py` FEE constants, per `reference/07-fees-and-costs.md`), plus
  financing for the family's characteristic hold horizon (10 bps per calendar day
  outstanding under T+2 settlement, per ADR-0011), capped at the ≤2 trading-day
  ceiling the leverage facility imposes, plus modelled market impact at the name's
  ADV from plan 09's square-root law. Names with lower friction rank higher, all
  else equal.

- **Feasible effective-N.** Does the family have enough independent observations
  on currently-available history to clear MIN_EFF_N=30 on its shape's natural
  unit (per plan 08 §4a and plan 10 §2)? The natural unit differs by shape:
  per-symbol rows for `single_name_time_series`, cross-sectional periods for
  `cross_sectional`, pooled non-overlapping events for `event_driven`, and
  residual-spread observations for `pairs`. If not → tag `data_blocked` and emit
  a structured requirement record routed to plan 12.

The headline score is an edge-to-friction ratio behind a feasibility gate:

```
feasible   ⟺  effective_N ≥ 30  (on the family's natural observation unit)
score      =  gross_edge_prior_bps / friction_floor_bps    (if feasible, else 0)
```

Higher scores rank first. A family with a 200 bps gross-edge prior and a 50 bps
friction floor scores 4.0; the same family on a high-friction name with a 150 bps
floor scores 1.3. The ratio is dimensionless and directly comparable across
families and names.

**4. Strategy-agnostic, full-catalog coverage.** The triage layer covers all
four shapes from plan 08 §4a and plan 10 §4: `single_name_time_series`,
`cross_sectional`, `event_driven`, and `pairs`. It tags every family in the
catalog with one of the four status tags from plan 08 §7.3:

| Status | Meaning |
|---|---|
| `testable_now` | sufficient data, feasible effective-N, score > 0 |
| `data_blocked` | not enough history or coverage for MIN_EFF_N on this shape; must emit a plan-12 requirement record |
| `cost_gated` | gross-edge prior is structurally at or below the friction floor on all feasible names |
| `deferred` | deliberate YAGNI deferral; unblock condition stated |

A completeness check verifies that every family in the plan-10 §4 taxonomy
appears in the triage output with an explicit status. No family is silently
skipped. This mirrors the completeness-critic pattern from plan 08 §6.7: a final
pass that flags any family without a status as a gap in the triage output.

**5. `data_blocked` emits a structured requirement record — not a bare tag.**
Per plan 08 §6.8 and plan 12 §2, a family may only be tagged `data_blocked` if
the triage output simultaneously emits a requirement record with the full plan-12
schema: `gap_id`, `kind`, `target`, `need` (quantified), `have`, `gated_alpha`,
`provenance`, and `status`. A bare tag without a record cannot be actioned by
plan 12's backlog store and cannot trigger auto-promotion when the data need is
met. The triage CLI is responsible for populating these fields from the catalog
metadata and the data inventory.

**6. Output feeds plan 10's run order and seeds plan 08 Workflow B's queue.**
The ranked output is the input contract for plan 10: it tests highest-score-first
within the `testable_now` families. When plan 08 Workflow B is eventually built,
the same ranked list seeds its hypothesis queue. The triage layer does not replace
either plan — it tells them where to start.

## 3. Design

### 3.1 Module layout

A small module under `research/strategy_fit/`, consistent with the location of
the plan-10 harness it feeds:

```
research/strategy_fit/
    triage.py          ← CLI entry-point; reads inputs, calls scorer, emits outputs
    triage_scorer.py   ← pure scoring functions; no I/O; unit-testable in isolation
    catalog_meta.py    ← catalog-metadata table (family → shape, horizon,
                          gross_edge_prior_bps, natural_obs_unit, char_hold_days)
```

The CLI is invoked as `python -m strategy_fit.triage` and emits
`research/out/strategy_triage.md`.

### 3.2 Inputs

| Input | Source | Notes |
|---|---|---|
| Per-name universe annotations | `research/out/universe_screen.md` (plan 09) | ADV, realised vol bps, min_spread_bps, is_edge_capped, is_thin |
| Catalog-metadata table | `research/strategy_fit/catalog_meta.py` | One row per family: shape, horizon, gross_edge_prior_bps, natural_obs_unit, char_hold_days |
| Data inventory | `research/out/data_inventory.md` (plan 12 §8.4) | "what history do we currently have?"; queried to compute feasible effective-N per family |
| FEE constants | `research/backtest/backtest.py` | 40 bps round-trip; single source of truth; do not redeclare |
| Financing rate | ADR-0011 | 10 bps per calendar day outstanding; ≤2 trading-day ceiling under the leverage facility |

The triage module imports FEE constants from `research/backtest/backtest.py`,
consistent with the import discipline established in plans 09 and 10. No 40 bps
value is declared inside `triage.py` or `triage_scorer.py`.

### 3.3 The scoring function

`triage_scorer.py` exports a pure function with no I/O:

```python
def score_family(
    family: FamilyMeta,          # from catalog_meta.py
    name_annotations: NameAnnotations | None,  # from plan-09 universe; None → universe-level
    data_inventory: DataInventory,
    fee_bps: float,              # imported from backtest.py
    financing_rate_bps_per_day: float,  # 10.0, from ADR-0011
    max_hold_days: int,          # ≤2 trading days, from ADR-0011
) -> TriageResult:
    ...
```

`TriageResult` carries: `family_id`, `name` (or `universe` for shape-level
families), `status`, `gross_edge_prior_bps`, `friction_floor_bps`,
`feasible_effective_n`, `score`, `data_blocked_record` (populated iff
`status == "data_blocked"`), and `unblock_note`.

The function is deterministic and side-effect-free. All I/O lives in `triage.py`.

### 3.4 Friction floor computation

The friction floor for a `(name, family)` pair is the sum of three terms:

```
friction_floor_bps =
    fee_bps                                             # 40 bps base round-trip
    + financing_rate_bps_per_day
        × min(char_hold_days, max_hold_days)            # 10 bps/day × capped days
    + impact_bps(half_spread, vol, position, adv)       # plan-09 square-root law
```

`impact_bps` is imported from `research/screener/metrics.py` (plan 09 §7.1),
the single source of truth for the square-root impact model. For cross-sectional
and event-driven families where `name_annotations` is None (the score is
computed at the universe level), the impact term is approximated from the
universe median ADV and vol from the plan-09 output.

### 3.5 Feasible effective-N gate

For each family, the triage layer queries the data inventory to estimate the
number of independent observations available on the family's natural observation
unit:

| Shape | Natural unit | Query to data inventory |
|---|---|---|
| `single_name_time_series` | per-symbol (t, return) rows, de-overlapped at hold horizon | trading days available per name / char_hold_days |
| `cross_sectional` | one cross-sectional period = one day across all universe names | trading days with universe-wide daily bars |
| `event_driven` | pooled non-overlapping events across the universe | estimated event frequency × trading days, de-overlapped |
| `pairs` | non-overlapping residual-spread observations at the pair level | trading days / char_hold_days, per candidate pair |

If the estimated independent observation count is below MIN_EFF_N=30, the family
is tagged `data_blocked` and a requirement record is populated with:
- `kind`: `length` (most common — right data, not enough history)
- `need`: a quantified unblock condition, e.g. "≥250 trading days of daily bars
  per name for single_name_time_series momentum at a 5-day hold horizon"
- `have`: what the inventory currently shows
- `gated_alpha`: the family's `gross_edge_prior_bps` (ranks the plan-12 backlog
  by research value)
- `provenance`: the family/cell ID from this triage run

A `coverage` gap (symbol not captured at all) is detected when the inventory
shows no history for the names required by a family. A `granularity` gap
(wrong resolution or missing field) is detected when the required data type —
e.g. full L2 book for an order-book family — is absent for an otherwise-captured
name. The plan-12 gap taxonomy (§2) governs which kind is assigned; the triage
layer classifies but does not service the gap.

### 3.6 Cost-gated detection

A family is tagged `cost_gated` when `gross_edge_prior_bps ≤ friction_floor_bps`
on every feasible name in the universe. The tick-scale OBI/OFI family on cheap
high-friction names is the canonical case: the one-tick spread alone exceeds the
family's characteristic move, so the score is negative before fees. This tag
encodes a structural prior — not a backtest result — and is revisited if the
gross-edge prior is revised or if the family's applicable name set changes (e.g.
a very-low-friction new name enters the universe).

### 3.7 Optional upstream hypothesis generation

The triage layer is designed so that novel candidate families — not yet in the
plan-10 §4 taxonomy — can be proposed upstream by an LLM and passed into the
catalog-metadata table before scoring. The generation step produces rows in the
same `FamilyMeta` schema; the scorer is unaware of their provenance. This keeps
the scoring path deterministic and the generation step optional and separable.
The generation step is not built as part of this plan; it is noted here so the
module interface does not foreclose it.

## 4. Output — `research/out/strategy_triage.md`

The CLI emits a structured markdown report. The report has three sections:

**Section 1 — Ranked candidates (`testable_now`):** families ordered by score
descending, grouped by shape. Each row shows:

| Family | Shape | Name / Universe | Score | Gross edge prior (bps) | Friction floor (bps) | Feasible N | Status |
|---|---|---|---|---|---|---|---|

**Section 2 — Blocked and deferred:** families tagged `data_blocked`,
`cost_gated`, or `deferred`, with the "what would unblock this" note and (for
`data_blocked`) the requirement-record fields.

**Section 3 — Completeness check:** a coverage row for every family in the
plan-10 §4 taxonomy. Any family missing from either Section 1 or Section 2 is
flagged as a gap in the triage output.

The `data_blocked` requirement records from Section 2 are also emitted as a
structured JSON sidecar (`research/out/strategy_triage_gaps.json`) for ingestion
by plan 12's backlog store without re-parsing the markdown.

### How this feeds plan 10

Plan 10's grid runner (§7.8) reads Section 1 of `strategy_triage.md` and runs
`testable_now` families in score order. The total plan-10 validation budget
(compute, researcher attention, and the plan-08 Workflow B fan-out capacity) is
spent in descending priority order. A family that is unlikely to survive the cost
gate — low score — is validated last, not first.

This is strictly an ordering decision. Plan 10 still validates every
`testable_now` family; the triage layer does not pre-approve or pre-reject any
family. The statistical verdict is plan 10's alone.

## 5. Relationship to plan 08

Plan 08 §2's discipline test: "if you can hold the whole task in your head and
the steps depend on each other → just do it." The triage scoring pass is wide
(all families × all names) but every step is deterministic arithmetic — no LLM
judgment, no fan-out needed. It is correctly a plain Python loop, not a Workflow.

Plan 08 §7.3 defines the taxonomy and the status vocabulary. This plan consumes
that taxonomy and makes it ranked. It does not extend or modify the taxonomy;
new family additions go through plan 10 §4 (which requires a data reason and an
ADR) and the triage metadata table is updated accordingly.

Plan 08 §6.8 defines the emit contract for `data_blocked` requirement records.
This plan is a second emitter of those records — alongside plan 08's
completeness-critic (§7.6) — and must conform to the same plan-12 schema (§3 of
plan 12). The schema is the shared surface; any change is a breaking change for
all emitters and the plan-12 servicer.

When plan 08 Workflow B is eventually built (§7.4 of plan 08), the ranked
`strategy_triage.md` Section 1 seeds its hypothesis queue. The triage layer does
not build or replace Workflow B.

## 6. Implementation

- [x] **6.1** `research/strategy_fit/catalog_meta.py` — the catalog-metadata
  table. One `FamilyMeta` dataclass per family (matching the plan-10 §4
  taxonomy): `family_id`, `shape`, `horizon_label`, `char_hold_days`,
  `natural_obs_unit`, `gross_edge_prior_bps`, and `deferred` flag with
  `deferred_note`. Seed from the plan-10 §4 table; every row must have a
  `gross_edge_prior_bps` drawn from published empirical ranges for the strategy
  class (stated source in a comment). No invented numbers. *(Done — `catalog_meta.py`, `FamilyMeta` with all seven fields + `ALL_FAMILIES`)*

- [x] **6.2** `research/strategy_fit/triage_scorer.py` — the pure scoring
  function (`score_family`). Imports FEE constants from
  `research/backtest/backtest.py` and `impact_bps` from
  `research/screener/metrics.py`. Unit-tested against hand-worked cases:
  (a) a low-friction `testable_now` family with score > 1.0; (b) a family on a
  high-friction name that is `cost_gated`; (c) a family where the data inventory
  has <30 independent observations → `data_blocked` with a populated record
  (all fields present and non-empty). *(Done — `triage_scorer.py::score_family`; `ROUND_TRIP_BPS` derived from `_FEE` (not redeclared) and `impact_bps` from `screener.metrics`. All three hand-worked cases covered by `test_triage_scorer.py`: `test_low_friction_testable_now`, `test_high_friction_cost_gated`, `test_data_blocked_low_n`)*

- [x] **6.3** `research/strategy_fit/triage.py` — CLI orchestrator. Reads
  `universe_screen.md` (plan 09), `data_inventory.md` (plan 12 §8.4), and
  `catalog_meta.py`; calls `score_family` per family/name; groups results by
  status; runs the completeness check against the full plan-10 §4 taxonomy;
  emits `research/out/strategy_triage.md` and the JSON sidecar
  `research/out/strategy_triage_gaps.json`. Invoked as
  `python -m strategy_fit.triage`. *(Done — `triage.py`, runnable as `python -m strategy_fit.triage`; emits `research/out/strategy_triage.md` + `strategy_triage_gaps.json`. Reads `universe_screen.md` and `catalog_meta.py`; the `data_inventory.md` input is deliberately not wired — that is 6.7, pending plan 12 §8.4)*

- [x] **6.4** Completeness check — after scoring all families, verify every
  family in `catalog_meta.py` appears in the output with an explicit status.
  Any family without a status is printed as a gap warning and the CLI exits
  non-zero. This is the triage-level analogue of plan 08's completeness-critic. *(Done — `triage.py::main` diffs `ALL_FAMILIES` against represented ids, prints `COMPLETENESS FAIL: missing {...}` to stderr and returns 1)*

- [x] **6.5** `data_blocked` requirement-record emission — for every family
  tagged `data_blocked`, populate the full plan-12 schema and write to the JSON
  sidecar. Validate the record against the plan-12 schema (§3 of plan 12) before
  writing: `gap_id` must be a stable hash of `(kind || target || need_spec)`,
  `need` must be a quantified string, `provenance` must be non-empty. An
  incomplete record is a hard error. *(Done — `_gap_id()` is `sha256(kind|target|need)[:16]`, stable; records written to the JSON sidecar)*

- [ ] **6.6** Integration test — run the CLI against the live
  `universe_screen.md` output from plan 09 and confirm: (a) every plan-10 §4
  family appears in the output; (b) Section 1 is ordered by score descending;
  (c) every `data_blocked` family has a populated record in the JSON sidecar;
  (d) FEE constants agree with `research/backtest/backtest.py` (no duplicated
  40 bps value). *(Open — only unit tests exist (`test_triage_scorer.py`); there is no
  test that runs the CLI end-to-end against the live `universe_screen.md`. Criterion (d)
  is already covered by `test_no_redeclared_fee`; (a)–(c) are not asserted anywhere.
  The CLI has been run manually — `research/out/strategy_triage.md` and
  `strategy_triage_gaps.json` exist — but that run is not pinned by a test.)*

- [ ] **6.7** (Follow-on, when plan 12 §8.4 data inventory is built) Wire the
  feasible-effective-N gate against the real data inventory. Until then, the
  gate uses the known data-length priors from plan 10 §4's `data_blocked` rows
  as a static approximation.

## 7. Verification

- **Score is a monotone prior.** For a fixed family, a lower-friction name
  always scores higher than a higher-friction name (all else equal). Verify by
  running the scorer against the plan-09 universe annotations sorted by
  `min_edge_to_clear_bps` ascending and confirming score is non-increasing.

- **Cost-gate consistency.** The friction floor uses the same FEE constants as
  `research/backtest/backtest.py` — import, never redeclare. A grep for `0.0015`
  or `0.0025` or `0.40` inside `triage_scorer.py` is a hard failure.

- **No validation claims.** The output `strategy_triage.md` must not contain
  p-values, Sharpe ratios, backtest returns, or any statistical result derived
  from running the data. A grep for these terms in the output is a linting check.

- **Full taxonomy coverage.** Every family in `catalog_meta.py` appears in the
  triage output with an explicit status. The completeness check (§6.4) enforces
  this at CLI runtime; a CI test runs the CLI against a fixture and asserts
  zero gap warnings.

- **`data_blocked` records are valid.** Every record in the JSON sidecar
  validates against the plan-12 schema: `gap_id` is a stable hash, `need` is
  non-empty and quantified, `provenance` is non-empty, `kind` is in
  `{length, coverage, granularity, regime}`. A schema validator runs as a unit
  test against every record produced by a fixture run.

- **Determinism.** Running the CLI twice on the same inputs produces byte-for-byte
  identical output. No random seeds, no `datetime.now()` calls, no file-path
  timestamps in the scored output. (The report header may carry a run date as
  metadata; the scored rows do not.)

- **Known priors reproduce.** The OBI/OFI family on BUMI (price 168, min-spread
  ~59.5 bps, plan-06 NO-GO) must be tagged `cost_gated` at its tick-scale
  characteristic move prior: the gross-edge prior for tick-scale intraday on a
  ~59 bps spread name is structurally below the friction floor. This is the
  canonical sanity case the scorer must reproduce.

## 8. Out of scope

- **The validation battery itself (plans 08 and 10).** This plan ranks candidates;
  it does not validate them. Statistical edge claims, p-values, backtest results,
  and walk-forward persistence tests belong entirely to plan 10 and plan 08
  Workflow B.

- **The plan-08 Workflow B orchestration substrate.** Plan 08's fan-out machinery
  — `pipeline` validate → cost gate → adversarial refutation → barrier
  multiple-testing correction — is correctly deferred until candidate volume
  justifies the overhead. This plan seeds Workflow B's queue when it is built; it
  does not build it.

- **Live execution.** The triage layer produces a research priority list, not
  trading signals or orders. Live execution belongs to pillars 2–3 of the roadmap.

- **Signal combination and blending.** How surviving signals from plan 10 combine
  into one portfolio view is plan 13 (capital allocation) and roadmap pillar 3.
  The triage layer operates at the family level, upstream of any individual signal.

- **Modifying the plan-10 §4 taxonomy.** New family additions require a data
  reason and an update to plan 10 §4. The triage `catalog_meta.py` mirrors the
  taxonomy; it does not extend it independently.

- **Speculative pre-capture.** The `data_blocked` records this plan emits follow
  the plan-12 demand-driven discipline: every gap must be tied to a real blocked
  hypothesis (the `provenance` field). This plan does not emit records for
  speculative future families.

---

Prev: [15-measured-fill-and-impact-calibration.md ←](./15-measured-fill-and-impact-calibration.md) ·
Index: [README](./README.md)
