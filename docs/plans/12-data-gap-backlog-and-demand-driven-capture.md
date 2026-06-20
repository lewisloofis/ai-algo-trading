# Data-Gap Backlog & Demand-Driven Capture — Research→Collector Return Edge

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-06-19
> **Related:** [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (pillar 1.2 "more data across regimes", pillar 4.1 unattended capture) · [01-market-data-capture.md](./01-market-data-capture.md) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) (§6.7 coverage table, §7.3 taxonomy/status-tags, §7.6 completeness critic, §6.8 emit contract) · [09-universe-screener.md](./09-universe-screener.md) (§7.2b capture-watchlist plumbing, ADR-0010) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [ADR-0010: capture watchlist is top-N diverse slice via tfvars](../adr/0010-capture-watchlist-is-topn-diverse-slice-via-tfvars.md)

> **What this is.** The missing return edge from the research engine back to the
> collector — the twin of the screener→collector edge plan 09 closes. When a
> strategy hypothesis dies during validation *for lack of data* (not lack of
> edge), that blocked verdict must become a demand the collector acts on. Today
> it is simply lost. This plan turns `data_blocked` verdicts into first-class
> requirement records, accumulates them in a backlog ranked by the alpha gated
> behind them, feeds them into the collector's capture config, and —
> critically — **auto-promotes blocked hypotheses back to `testable_now`** once
> the data need is satisfied. The loop closes without a human having to remember
> to re-run a study after months of accrual.

## 1. Why this is a separate plan from plan 08

Plan 08 is the research engine. It defines Workflow B (the validation battery),
the strategy taxonomy, the coverage table, and the completeness-critic pass.
Its single responsibility is *running studies and producing honest verdicts*.

This plan is data-ops. It takes a `data_blocked` verdict emitted by plan 08 and
services the underlying gap: building a backlog store, planning the collector
config, checking when data needs are satisfied, and re-queuing hypotheses. It
touches plan 01 (the collector) and plan 09 (the watchlist plumbing), and it has
its own verification surface entirely separate from Workflow B.

The relationship mirrors the plan 10 / plan 11 split exactly: plan 10 *defines*
the decay metric and uses it to gate the portfolio; plan 11 *operates* the same
metric against live PnL. Here, plan 08 *declares* the gap (the emit contract);
plan 12 *services* it (the loop). The shared surface between the two plans is the
**requirement-record schema**, just as the decay metric is the shared surface
between plans 10 and 11. Folding this into plan 08 would conflate research
with data-ops and make plan 08 responsible for collector configuration — a clear
violation of its single responsibility.

## 2. Locked decisions

**A `data_blocked` verdict MUST emit a requirement record — no bare tags.**
A cell in the coverage table (plan 08 §6.7 / §7.3) may only be marked
`data_blocked` if it simultaneously emits a structured requirement record naming
the precise unblock condition. A bare status tag without a record is invalid: it
provides no actionable information to the collector, cannot be deduped, cannot
rank the backlog, and cannot auto-promote when satisfied. The schema lives here;
the servicing loop lives here; plan 08 owns only the emit contract (§6.8).

**The gap taxonomy is the resolution authority.**
Each gap belongs to exactly one of four kinds, because the collector action
differs per kind:

| kind | what is missing | collector action | resolves by |
|---|---|---|---|
| `length` | right data, not enough history (de-overlap starved for effective-N) | already capturing — verify accrual is healthy; wait | time |
| `coverage` | symbol not captured at all | add to capture watchlist | next capture session |
| `granularity` | capturing the name, wrong resolution or missing field (have trades, need full L2 book; a field not subscribed) | change collector subscription / config | next capture session |
| `regime` | enough data, but all from one market regime | flag for opportunistic capture; cannot be forced | when the regime occurs |

A bare tag cannot encode this distinction. Each kind implies a different
collector action; conflating them produces the wrong response.

**Demand-driven only — every gap has provenance, no speculative capture.**
Every requirement record carries a `provenance[]` list of the specific
hypothesis/cell IDs that block on it. The collector's capture universe can grow
only in response to demonstrated research demand. Speculative pre-capture of
names not yet tied to a hypothesis is out of scope and wastes capacity.

**Discovery capture (screener watchlist) and demand capture (research backlog)
are distinct collector inputs — never merged.**
Plan 09 §7.2b emits a discovery watchlist: a broad/diverse top-N slice of
funding-eligible names selected before any hypothesis is tested. This plan
emits demand records: precise gap requirements tied to real blocked hypotheses.
Both feed the collector but answer different questions. Merging them would
corrupt both: the discovery slice would acquire hypothesis-driven bias; the
demand records would be drowned in names with no associated study. The collector
resolves both inputs, but they flow through separate channels.

**The data inventory is the single shared source of truth for "what do we have?"**
Three steps in this loop all need to answer that question: the research-time
block decision (plan 08 §6.7), the collector planning step, and the satisfaction
check. Making it one real queryable component — names × date-ranges ×
granularity × subscribed fields — keeps all three honest. If each step maintains
its own informal sense of coverage, they diverge and the gap/satisfied
classification becomes inconsistent.

**Auto-promotion on satisfaction is mechanical, not manual.**
When a gap's `need` is verified as met by the data inventory, the status
transitions `open → satisfied` and every hypothesis in `provenance[]` is
re-queued as `testable_now` in the research backlog automatically. This is the
whole point of the loop: without it, a researcher must remember — weeks or months
later — to re-run every study that was blocked, and the blocked studies are
silently lost to attrition.

## 3. Requirement-record schema

The record is the shared surface between plan 08 (emitter) and plan 12
(servicer). Any change to this schema is a breaking change for both.

| Field | Type | Description |
|---|---|---|
| `gap_id` | string | Stable hash of `(kind \|\| target \|\| need_spec)` — dedup key across all records |
| `kind` | enum | `length` \| `coverage` \| `granularity` \| `regime` |
| `target` | string or list | Symbol(s) or universe slice the gap applies to |
| `need` | string | Quantified unblock condition, precise enough for the collector to act without translation (e.g. "≥250 trading days of daily bars for TLKM"; "L2 depth subscription for BBCA, BMRI, ASII"; "≥30 pooled non-overlapping gap-up events across the LQ45 universe") |
| `have` | string | What the data inventory currently shows — the delta that makes the gap visible |
| `gated_alpha` | string | Expected edge or strategy family blocked behind this gap — ranks the backlog by research value |
| `provenance` | list | Hypothesis / cell IDs from the plan 08 coverage table that block on this gap; multiple hypotheses may share one gap record |
| `status` | enum | `open` \| `collecting` \| `satisfied` |

## 4. Core design — the closed loop

```
[plan 08 Workflow B / completeness-critic (§7.6)]
  ─ cell marked data_blocked ─► emit requirement record
                                        │
                                        ▼
                            [data_backlog store]
                          research/out/data_backlog.md
                          (deduped by gap_id, ranked by
                           gated_alpha; N hypotheses on
                           one gap → one task, all
                           provenance back-refs kept)
                                        │
                                        ▼
                        [collector planning step]        ◄──── [data inventory]
                    reads backlog; merges length /                names × date-ranges
                    coverage / granularity gaps into              × granularity × fields
                    collector capture config — adds               (single source of truth
                    names to watchlist via plan 09                for "what do we have?")
                    §7.2b tfvars plumbing; adjusts
                    subscription fields / retention
                                        │
                                        ▼
                           [collector runs]  (plan 01)
                           data accrues into capture store
                                        │
                                        ▼
                        [satisfaction check]             ◄──── [data inventory]
                    re-tests each open gap against
                    current inventory: is need met?
                                        │
                          ┌─────────────┴──────────────┐
                          │ yes                         │ no
                          ▼                             ▼
               mark gap satisfied              no action; gap stays open;
               auto-promote ALL                regime gaps flagged for
               provenance hypotheses           opportunistic accrual
               → testable_now in
               research backlog
```

The loop runs continuously (or on demand after each capture session). Each
iteration either confirms gaps are still open, marks newly-satisfied gaps and
re-queues their hypotheses, or surfaces new gaps from fresh Workflow B runs.

## 5. The data inventory component

The inventory catalogs what the collector has actually captured, queryable by
any component in the loop:

- **What it stores:** per name — date range of captured sessions, granularity
  (tick/trade/L1/L2), subscribed field set, segment count, and last-verified
  timestamp.
- **Who queries it:**
  - Plan 08 §6.7 (research time) — "do we have ≥250 daily bars for TLKM?" to
    decide whether to tag `data_blocked` or `testable_now`.
  - The collector planning step — "which coverage/granularity gaps are still
    open given current inventory?" to avoid re-adding names already captured.
  - The satisfaction check — "has the `need` condition been met?" to trigger
    auto-promotion.
- **Implementation:** a lightweight queryable file
  (`research/out/data_inventory.md` or a JSON sidecar) built from the capture
  store's segment manifests. Not a database — the capture store is the
  authoritative record; the inventory is a derived summary updated after each
  capture session.

## 6. Roadmap consolidation — what this plan formalises

| Roadmap item | Location | Resolved here |
|---|---|---|
| "More data across regimes" — multi-day capture so verdicts rest on diverse sessions | Pillar 1.2 | Gap taxonomy `regime` kind flags regime-scarce gaps; `length` kind tracks accrual of additional sessions; satisfaction check re-queues blocked hypotheses when enough diverse sessions exist |
| "Verify it is actually accruing data" before counting on "just wait" | Pillar 4.1 | Satisfaction check re-tests inventory; `length` gaps surface accrual failures (inventory not growing) |
| Capture-watchlist plumbing — tfvars delivery via plan 09 §7.2b | ADR-0010 | Collector planning step reuses this plumbing for coverage/granularity gaps; demand watchlist entries flow through the same tfvars channel |
| Collector capture universe growing only on demonstrated research demand | Implicit in ADR-0010 demand rationale | Formalised here as the provenance requirement: every gap has a `provenance[]` of specific blocked hypotheses |

## 7. Method — end-to-end loop steps

1. **Emit.** Plan 08 completeness-critic (§7.6) marks a cell `data_blocked` in
   the coverage table and simultaneously emits a requirement record with all
   required fields. If a record cannot be fully populated (kind unknown, need
   unquantified), the cell may not be marked `data_blocked` — it must stay
   `deferred` with a note.
2. **Dedup and accumulate.** The backlog store computes `gap_id` from
   `(kind || target || need_spec)`. If a record with that `gap_id` already
   exists, merge the new hypothesis into `provenance[]` — one task in the
   backlog, all provenance back-refs preserved. New gaps are added with
   `status: open`.
3. **Plan collector config.** A collector planning step reads all `status: open`
   records. For `coverage` and `granularity` gaps it generates additions to the
   capture watchlist and/or subscription config, delivered via the plan 09 §7.2b
   tfvars channel. For `length` gaps it verifies the name is already being
   captured and flags if inventory is not growing. For `regime` gaps it adds a
   note for opportunistic capture but takes no hard config action.
4. **Accrue.** The collector runs (plan 01), data accrues into the capture store.
5. **Satisfaction check.** After each capture session (or on demand), the check
   re-tests every `status: open` gap against the current data inventory. If
   `need` is met: transition to `status: satisfied`, emit a satisfaction record,
   and auto-promote all `provenance[]` hypotheses to `testable_now` in the
   research backlog.
6. **Auto-promote.** Each auto-promoted hypothesis re-enters the plan 08 Workflow
   B queue as `testable_now`, with a note that it was previously `data_blocked`
   and is now unblocked by the satisfied gap. No manual intervention required.

## 8. Implementation

Output convention: markdown reports in `research/out/` — `data_backlog.md` for
the running gap list, `data_inventory.md` for the catalog summary — matching the
`*.md` output pattern established in plans 04–06.

- [ ] **8.1** (Buildable now) Requirement-record schema — define the JSON schema
  for the record fields in §3 above. Implement a validator that rejects
  incomplete records (missing `gap_id`, unquantified `need`, empty `provenance`).
  This is the shared surface between plan 08 and plan 12; fix it before either
  side builds against it.
- [ ] **8.2** (Buildable now) Gap taxonomy implementation — a small module
  (`research/lib/gap_taxonomy.py` or equivalent) that encodes the four `kind`
  values, their per-kind resolution logic, and the classification rules. Unit-test
  the classification against each kind's defining case.
- [ ] **8.3** (Buildable now) Backlog store — `research/out/data_backlog.md` (or
  JSON sidecar) that accumulates requirement records, deduplicates by `gap_id`,
  merges `provenance[]` on collision, and ranks by `gated_alpha`. Implement
  the dedup/merge logic and verify the round-trip: emit → store → read back →
  identical record with all provenance refs.
- [ ] **8.4** (Buildable now) Data inventory component — a queryable summary of
  what the capture store holds (`research/out/data_inventory.md`), rebuilt from
  segment manifests after each session. Must answer "does name X have ≥N trading
  days of daily bars?", "is L2 subscription active for name Y?", "what date range
  is captured for name Z?".
- [ ] **8.5** (Blocked — needs plan 08 §7.3–7.6 Workflow B) Emit contract wired
  into plan 08 completeness-critic — the §7.6 agent pass must populate and emit
  requirement records for every `data_blocked` cell it generates. Cannot be
  meaningfully tested until Workflow B exists.
- [ ] **8.6** (Blocked — needs multi-day unattended capture, pillar 4.1) Collector
  planning step — reads the open backlog, generates watchlist additions and
  subscription-config changes for `coverage` / `granularity` gaps via the plan 09
  §7.2b tfvars channel, and verifies `length` gap names are actively accruing.
  Blocked because there is nothing to plan against until multi-day capture is
  running reliably.
- [ ] **8.7** (Blocked — needs pillar 4.1 + 8.4 inventory) Satisfaction check —
  re-tests each open gap against the current inventory after each capture session.
  Marks satisfied gaps, emits a satisfaction record to `data_backlog.md`, and
  triggers auto-promotion.
- [ ] **8.8** (Blocked — needs 8.7) Auto-promotion — writes `testable_now` back to
  the plan 08 research backlog for every hypothesis in a newly-satisfied gap's
  `provenance[]`. The mechanism for re-queuing into Workflow B must be agreed with
  plan 08 §7.3 before implementation.

## 9. Verification

- **Round-trip integrity** — a `data_blocked` verdict emitted by plan 08 §7.6
  produces a valid requirement record (all fields present, `gap_id` stable,
  `provenance` non-empty); the record persists to `data_backlog.md` unchanged.
- **Dedup correctness** — N distinct hypotheses blocking on the same gap
  (identical `kind`, `target`, `need_spec`) produce exactly one backlog task
  whose `provenance[]` contains all N hypothesis IDs; no duplicates, no
  dropped refs.
- **Kind classification** — a `length` gap does not trigger a watchlist addition;
  a `coverage` gap does; a `regime` gap produces an opportunistic note and no
  hard config action. Verify each kind takes its correct path.
- **Discovery vs demand separation** — the collector planning step's watchlist
  additions are distinct from the plan 09 §7.2b screener watchlist; no name
  added by the demand backlog appears in the screener's top-N diverse slice unless
  it was independently selected there.
- **Satisfaction check fires correctly** — a gap whose `need` is met by current
  inventory transitions to `satisfied` and all `provenance[]` hypotheses are
  re-queued as `testable_now`; a gap whose `need` is not yet met stays `open`.
- **No false auto-promotion** — a `length` gap for a name not yet captured
  correctly stays `open`; a `length` gap for a name that is captured but has not
  yet accrued sufficient history correctly stays `open` until the inventory shows
  the needed count.

## 10. Out of scope

- Building the servicing loop (steps 8.6–8.8) before multi-day unattended
  capture is running — the block is hard; a satisfaction check with no accruing
  data is a no-op.
- Merging the discovery watchlist (plan 09 §7.2b) with the demand backlog — they
  are distinct inputs for distinct reasons; see §2.
- Re-implementing the collector (plan 01) — plan 12 only emits config it
  consumes; all collector changes go through plan 01 and plan 09's tfvars
  channel.
- Speculative gap records (gaps without a `provenance[]` of real blocked
  hypotheses) — every record must tie to at least one actual study that blocked.
- Building any part of the auto-promotion path (8.8) before the emit contract
  (8.5) and satisfaction check (8.7) are in place — the dependency chain is
  strict.

---

Prev: [11-decay-monitoring-and-live-revalidation.md ←](./11-decay-monitoring-and-live-revalidation.md) ·
Index: [README](./README.md)
