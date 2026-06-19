# Alpha Factory as Orchestrated Workflows — Fan-Out the Research Loop

> **Status:** 🔨 In Progress · **Owner:** @lewis · **Updated:** 2026-06-19
> _(Workflow A / 7.2 — the universe screener — done & live-verified via [plan 09](./09-universe-screener.md); Workflow B 7.3–7.5 not started.)_
> **Related:** [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) · [04-microstructure-study.md](./04-microstructure-study.md) · [05-ofi-trade-flow-study.md](./05-ofi-trade-flow-study.md) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [09-universe-screener.md](./09-universe-screener.md) · [Plans index](./README.md)

> **What this is.** An idea, not yet a buildable ticket. The roadmap's
> [§1.2](./00-roadmap-research-to-live.md) names the bottleneck between "one
> rigorous research loop" and an **Alpha Factory**: a *pipeline* that takes a new
> hypothesis and returns the full validation battery in hours, with statistics
> that survive having tested dozens of ideas. That bottleneck is a fan-out +
> adversarial-verify problem — exactly the shape a Claude Code **dynamic
> workflow** orchestrates. This plan sketches three concrete workflows and the
> discipline for when orchestration earns its keep here and when it is theatre.

## 1. Why a workflow, and why now

We have one bespoke research loop (plans 04–06): hand-written per study, single
day, single signal family, killed at the 40 bps gate. The roadmap §1.2 lists what
turns that into a *factory*, and almost every item is intrinsically parallel:

- **Universe screening** — the same per-symbol metric computation across every
  captured name (immediate next action [§7.1](./00-roadmap-research-to-live.md)).
- **A signal pipeline** — add a hypothesis, get the whole validation battery
  without writing a bespoke script each time.
- **Multiple-testing discipline at scale** — when you test 50 signals, ~2–3 pass
  at p<0.05 by chance; the factory must *not* manufacture false alpha.
- **Lower-frequency signal families** — the next batch of hypotheses the cost gate
  permits, run through that same pipeline.

A dynamic workflow is a deterministic orchestrator (loops, fan-out, barriers) that
spawns subagents and collects structured results. The validation battery and cost
gate already exist as audited Python (`research/study/`, `research/backtest/`); the
workflow's job is to **crank the handle on many inputs in parallel and apply the
cross-hypothesis statistics one context couldn't hold by hand** — not to reinvent
the stats.

## 2. The discipline — when orchestration earns its keep

The test is **breadth × independence × scale**. A workflow pays off only when the
work is *wide* (many items), *independent* (items don't depend on each other), and
*at scale* (enough items that the orchestration overhead is dwarfed).

- ✅ **Fits:** "compute screening metrics for 30 symbols," "run the validation
  battery on 20 candidate signals," "verify each surviving signal from 3 skeptical
  angles." Many independent sites, mechanical per-site, synthesised at the end.
- ❌ **Does not fit:** the [plan 07](./07-repo-restructure.md) restructure (a few
  coupled `git mv` + import fixups — sequential, one context), the deferred Go
  execution engine (sequential build work, pillar 2), any single-day one-off study
  small enough to write directly. Reaching for a workflow there is slower, not
  faster.

> One-line rule: if you can hold the whole task in your head and the steps depend
> on each other → just do it. If it's the same operation across many independent
> inputs, plus a synthesis that needs them all at once → workflow.

## 3. Workflow A — Universe screening → now plan 09 (deterministic Python)

The roadmap's cheapest, most upstream next step ([§7.1](./00-roadmap-research-to-live.md)):
choose names from *data*, not memory, so the next study doesn't inherit the
current 5–6-name convenience sample's selection bias. This was originally
sketched as a `parallel` per-symbol `agent()` fan-out, assuming each symbol
would need judgment about order-book segment data quality.

**Scoped to Tier A daily-bar metrics, this is correctly deterministic Python —
not a Claude Workflow.** The per-symbol step — `tick_size(price)`,
`median_daily_turnover`, `realised_vol_bps`, `impact_bps` — is pure arithmetic.
Applying the §2 discipline test: the work is mechanically wide, yes, but every
step is deterministic computation with no LLM judgment involved. Per the
one-line rule above, the right tool is a plain Python loop with a thin
orchestrator, not an `agent()` fan-out. A Workflow wrapper could make sense
later if going live against the full ~900-name IDX universe makes the
authenticated per-symbol HTTP I/O worth fanning out for concurrency — but
that is concurrency, not judgment.

**Why tick-to-price leads the ranking:** plan 06 showed BUMI's 1-tick book made
the spread alone ≈ the predicted move. `min_spread_bps = tick_size(price)/price·10_000`
is computable from price alone; names where it exceeds the 40 bps cost gate are
structurally edge-capped before any study runs. Screening must surface that
*before* a study runs, not after.

The full specification — three-layer funnel, funding gate contract, tick-floor
derivation, square-root impact law, data sources, reuse inventory, and
implementation steps — lives in **[plan 09](./09-universe-screener.md)**.
Plan 09 also explains in detail why the Tier A scoping causes this to degrade
from an `agent()` fan-out to plain Python (§6 of that plan).

## 4. Workflow B — Signal-validation battery (the prize)

This *is* the §1.2 "signal pipeline" + "multiple-testing discipline" made
executable. It takes N candidate hypotheses and returns a single, honestly-corrected
verdict batch.

**Shape:** `pipeline` per hypothesis (no barrier between stages — hypothesis A can
be at the cost gate while B is still validating) → then **one barrier** for the
cross-hypothesis multiple-testing correction, which genuinely needs all survivors
at once.

```js
// Stage 1: full validation battery — reuses the audited research/study harness
//   (decile curve, HAC regression, conditional hit-rate, block-bootstrap null,
//    chronological OOS, de-overlap audit, MIN_EFF_N=30 floor).
// Stage 2: the 40 bps cost/fill gate from research/backtest/backtest.py.
// Stage 3: adversarial refutation — the discipline that stops false alpha.
const graded = await pipeline(
  hypotheses,
  h  => agent(`Run the full validation battery on signal "${h.name}":
               ${h.spec}. Report decile curve, HAC t-stat, de-overlapped
               hit-rate, effective-N, block-bootstrap p.`,
              { label: `validate:${h.name}`, phase: 'Validate', schema: STATS_SCHEMA }),
  (stats, h) => stats.effectiveN < 30 || !stats.passesNull
      ? { ...h, stats, gate: { verdict: 'INCONCLUSIVE' } }            // skip the cost gate
      : agent(`Net signal "${h.name}" against the 40 bps round-trip cost stack
               (taker primary, passive sensitivity), long-only retail base case,
               fixed-horizon exit. Three-state verdict.`,
              { label: `gate:${h.name}`, phase: 'Cost gate', schema: GATE_SCHEMA })
          .then(gate => ({ ...h, stats, gate })),
  graded => graded.gate?.verdict !== 'PASS' ? graded
      : parallel(['look-ahead leakage', 'de-overlap / overlapping-window inflation',
                  'regime / cherry-picked-window dependence'].map(lens => () =>
          agent(`Try to REFUTE that "${graded.name}" is a real tradeable edge,
                 attacking via ${lens}. Default to refuted=true if uncertain.`,
                { label: `refute:${graded.name}:${lens}`, phase: 'Refute', schema: VERDICT_SCHEMA })))
          .then(votes => ({ ...graded,
                            survives: votes.filter(Boolean).filter(v => !v.refuted).length >= 2 }))
);

// BARRIER (correct here): multiple-testing correction needs every result at once.
const survivors = graded.filter(Boolean).filter(g => g.gate?.verdict === 'PASS' && g.survives);
const corrected = await agent(`Apply multiple-testing correction across ALL
  ${hypotheses.length} hypotheses tested (deflated Sharpe / FDR). A signal that
  passed in isolation may not survive the correction. Emit the final batch verdict.`,
  { phase: 'Correct', schema: FDR_SCHEMA /*, input: survivors + full count */ });
// → research/study/out/signal_battery_report.md
```

Two non-negotiable properties this encodes:

1. **Adversarial verify before believing.** For every signal that "passes," 2–3
   skeptics prompted to *refute* it (look-ahead, de-overlap inflation — the exact
   trap that downgraded [plan 04](./04-microstructure-study.md) and the ANTM case
   in [plan 05](./05-ofi-trade-flow-study.md) — and regime dependence). A plausible
   but wrong survivor is the expensive failure mode in quant; majority-refute kills
   it. This is the single highest-value use of orchestration in the project.
2. **Correction over the whole batch, not per signal.** The barrier exists *because*
   "did this survive given I tested 50?" can only be answered with all results in
   hand. This is the §1.2 "factory doesn't manufacture false alpha" requirement.

## 5. Workflow C — Multi-day / regime pooling (deferred)

Once unattended multi-day capture is running (pillar 4.1, blocked today), the same
battery generalises: **pipeline over days/regimes** → per-day stats respecting day
boundaries → **barrier** to pool with the OOS split done *by day* (train early days,
test later), keeping effective-N and bootstrap honest across the boundary. This is
the §1.2 "day-aware multi-day pooling" item. Deferred until there is more than one
session to pool — it is worthless on a single day.

## 6. Locked decisions / guardrails

1. **Workflows orchestrate; the audited Python does the stats.** No statistical
   logic is reimplemented inside an agent prompt — agents shell the existing
   `research/study/` and `research/backtest/` code and return structured results.
   One source of truth for the math, same as [plan 06](./06-fill-simulator-and-backtest.md) decision 6.
2. **Structured output everywhere.** Every `agent()` call that feeds a later stage
   uses a JSON `schema`, so the orchestrator branches on validated data, not parsed
   prose.
3. **The human reads the verdict, not the agents' chatter.** A workflow's value is
   the synthesised report (`research/.../out/*.md`); intermediate agent output is
   plumbing. Build only enough to produce a verdict a desk would act on.
4. **Cost is opt-in and bounded.** A workflow can spawn dozens of agents; it runs
   only when explicitly requested (the `ultracode` keyword or "use a workflow"),
   never inferred. Scale the finder/refuter pool to the question — a quick check is
   a few agents; "thoroughly screen the universe" is the larger fan-out.
5. **Determinism for resume.** Scripts pass dates/timestamps via `args` (no
   `Date.now()`/`Math.random()`), so a long battery run can resume from cache after
   an interruption.
6. **Build the screener first, then Workflow B.** Universe screening (low stakes,
   deterministic Python — see §3 and [plan 09](./09-universe-screener.md)) runs
   first; it produces an unbiased input universe. Workflow B (the signal battery,
   the prize) is the **first true Workflow** in this project — the screener
   validates nothing about Workflow mechanics, but its output is the prerequisite
   for a meaningful battery run.

## 7. Implementation steps

- [ ] **7.1** Confirm `research/lib/loaders.py` + `sbcap` expose everything the
      screening metrics need per symbol (turnover, L1 depth, spread, tick/price,
      vol, update rate); add any missing column.
- [x] **7.2** Build the universe screener (deterministic Python, not a Workflow) per
      [plan 09](./09-universe-screener.md): `metrics.py` pure functions → `screen_universe.py`
      Tier A orchestrator → `research/out/universe_screen.md`. **Done & live-verified
      (2026-06-19).** Strategy fit over the ranked universe is now its own
      [plan 10](./10-strategy-fit-and-portfolio.md) — the data-centric body that
      Workflow B (7.3–7.5) will orchestrate.
- [ ] **7.3** Define the candidate-hypothesis spec format (name + signal
      construction + horizon) the battery pipeline consumes, seeded with the
      lower-frequency families the 40 bps gate permits (§1.2 last bullet).
- [ ] **7.4** Author Workflow B (validation battery): `pipeline`
      validate → cost gate → adversarial refute, then the barrier FDR/deflated-Sharpe
      correction → `research/study/out/signal_battery_report.md`.
- [ ] **7.5** Wire the three-state verdict (PASS / NO-GO / INCONCLUSIVE) to mirror
      plans 04–06 exactly, so a workflow verdict is directly comparable to a
      hand-run one.
- [ ] **7.6** (Deferred, blocked on pillar 4.1) Workflow C: day-aware multi-day
      pooling once multi-day capture is accruing.

## 8. Out of scope (deliberately)

- **Any workflow for execution / risk / ops (pillars 2–3).** Sequential build work,
  deferred until a signal clears the gate; orchestration adds nothing there.
- **Workflows for structural refactors** — see §2; coupled, single-context work.
- **Replacing the audited Python battery with agent-side statistics** — the workflow
  cranks the handle, it does not become the handle (§6.1).
- **Running a workflow unprompted** — cost gate is explicit opt-in (§6.4).

---

Prev: [07-repo-restructure.md ←](./07-repo-restructure.md) ·
Index: [README](./README.md)
