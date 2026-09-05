# Data-Gap Backlog & Demand-Driven Capture — Research→Collector Return Edge

> **Status:** 📝 Draft · **Owner:** @lewis · **Updated:** 2026-09-05
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

## 5b. Measured capture fidelity baseline (2026-09-04)

*Produced by roadmap W1.1. The inventory above answers "do we have this name on
this date". This section answers the question underneath it: **is what we
captured actually correct?** Until now nothing had checked.*

**Method.** `sbcap bars` rebuilds 1-minute OHLCV from the captured protobuf
tick stream. `research/data/intraday/*.parquet` holds the same bars from a
completely independent substrate — the chartbit HTTP endpoint. Comparing them
is a true cross-substrate audit, not a self-consistency check. Window: the five
`plan10-survivors` sessions 2026-06-22 → 06-26, continuous session only
(09:00–15:59 WIB), side-tagged trades.

| Symbol | Bars | OHLC exact | Volume exact | Volume ratio | Median trades/bar |
|---|---|---|---|---|---|
| PTRO | 1540 | 1529 (99.3 %) | 1490 (96.8 %) | 0.99196 | 25 |
| BRPT | 1540 | 1516 (98.4 %) | 1434 (93.1 %) | 0.99094 | 65 |
| ENRG | 1532 | 1519 (99.2 %) | 1493 (97.5 %) | 1.00046 | 23 |
| TPIA | 1540 | 1485 (96.4 %) | 1227 (79.7 %) | 0.95315 | 146 |

**Finding 1 — prices are trustworthy, volumes are not.** OHLC reconstructs to
96–99 % exact. Volume does not, and the error is not noise: TPIA is short
**4.7 %** of continuous-session volume while ENRG is dead-on.

**Finding 2 — the loss is load-dependent, and that makes it a bias, not an
error bar.** Volume capture rate by bar-activity quintile:

| Quintile | BRPT | ENRG | PTRO | TPIA |
|---|---|---|---|---|
| Q1 quiet | 0.9993 | 0.9998 | 1.0013 | 0.9991 |
| Q2 | 1.0006 | 0.9991 | 0.9996 | 1.0011 |
| Q3 | 1.0003 | 1.0005 | 0.9992 | 1.0004 |
| Q4 | 1.0001 | 1.0072 | 1.0036 | 0.9842 |
| **Q5 busy** | **0.9846** | **0.9981** | **0.9863** | **0.9287** |

Quintiles 1–3 are essentially perfect for every name. Degradation appears only
in the busiest quintile and scales with trade rate — TPIA's Q5 median is 588
trades/minute and loses 7.1 %. For TPIA the top 3 % of bars carry **86 %** of
the entire shortfall.

**Why this matters more than the headline percentage.** A uniform 4 % loss would
be a tolerable haircut. This is not uniform: the tape is truncated *precisely in
the minutes of highest activity* — the minutes most signals fire in. Any family
keyed on volume spikes, participation rate, or signed flow is being measured on
data that is thinnest exactly where it is being asked the most. Price-keyed
families (ORB, intraday momentum) are unaffected.

**Ruled out.** `replay.go` fans each `running_trade` batch into one event per
trade and `sbcap bars` reads every one, so the loss is upstream of the bar
builder, not in it. `trade_number` gap analysis cannot diagnose this: the field
is **market-wide**, not per-symbol — four symbols on 2026-06-24 shared one number
space of 24,877 → 1,983,458 with *zero* collisions, so a single symbol's
"missing" numbers are simply other tickers' trades. A naive read of that field
suggests 94 % loss and means nothing.

**Not yet established** — whether the collector's websocket read loop drops
frames under burst, or the upstream feed itself samples `running_trade` when
busy. These call for different responses (fix and re-capture vs. accept the
limit and never trust tick volume), so W2 must not assume either.

**Second-order observations.**
- Untagged trades (no aggressor side) are *not* only the pre-opening auction, as
  the collector docs implied. They occur in three places: pre-open (~08:58),
  scattered mid-session crossings, and the whole 16:00–16:15 post-trading
  session — 12 % of PTRO's daily volume on 2026-06-22. Chartbit excludes the
  mid-session ones but includes post-trading. `sbcap bars` therefore reports
  `untagged_vol` as its own column rather than silently choosing.

  **Correction — mid-session is not a footnote.** The first pass through this
  data treated mid-session untagged crossings as a minor third population
  behind the pre-open auction. Measured per-day, that is backwards: on
  2026-06-22, PTRO's mid-session untagged volume was **1.89M shares against
  only 108.8K pre-open** — 17× larger, and it sits *inside* the nominal
  09:00–15:59 continuous session, where nobody auditing "outside-session
  volume" would think to look for it. A continuous-session volume or OHLC diff
  against chartbit will disagree for this reason alone, with no capture defect
  involved (verified by re-running `sbcap bars --tagged-only`, which drops
  mid-session untagged trades along with the rest and reproduces chartbit's
  continuous-session bars exactly — see roadmap W1.1).

  Per-day breakdown, `plan10-survivors` window (shares):

  | Date | PTRO total vol | PTRO pre-open | PTRO mid-session untagged | PTRO post-trading | BRPT total vol | BRPT pre-open | BRPT mid-session untagged | BRPT post-trading |
  |---|---|---|---|---|---|---|---|---|
  | 2026-06-22 | 22,319,890 | 108,800 | **1,886,700** | 1,910,190 | 145,141,303 | 1,471,100 | 818,071 | 7,099,432 |
  | 2026-06-23 | 37,172,100 | 1,054,400 | 40,000 | 800,200 | 136,927,400 | 520,200 | 2,200,000 | 5,496,900 |
  | 2026-06-24 | 39,832,700 | 581,500 | 1,382,700 | 1,679,100 | 130,814,200 | 579,100 | 822,000 | 3,193,000 |
  | 2026-06-25 | 40,842,457 | 257,000 | 407,457 | 1,310,800 | 173,305,130 | 342,200 | 236 | 2,663,094 |
  | 2026-06-26 | 29,991,035 | 246,900 | 1,227,135 | 844,200 | 206,461,549 | 706,400 | 1,535,649 | 15,919,500 |

  Mid-session untagged volume is not a fixed small tax: it ranges from
  negligible (BRPT 06-25: 236 shares) to the largest of the three populations
  on a given day (PTRO 06-22, BRPT 06-23 and 06-26). It cannot be predicted
  from the pre-open or post-trading columns, so it must be measured per day,
  not assumed.

  **Build-time consequence (roadmap W1.1).** Because a mid-session untagged
  print can set a bar's `high` or `low`, it cannot be subtracted back out of
  OHLC after the fact the way `untagged_vol` can be subtracted from volume —
  the moment it is folded into a bar, the tagged-only price series is gone.
  `screener/tick_bars.py` therefore builds two independent stores rather than
  filtering one: `data/tick_bars/` (full tape — correct for volume,
  participation, turnover, impact calibration) and `data/tick_bars_tagged/`
  (`--tagged-only` — book-accessible prices only, the default input for any
  signal keyed on an OHLC level, e.g. ORB). A negotiated print inside an ORB
  opening-range window would otherwise manufacture a breakout the book never
  offered — the same failure class as the short leg removed in c62eba8.
- Bars are bucketed on collector **arrival** time; the feed carries no exchange
  trade timestamp. This puts a handful of boundary trades one bar either side of
  chartbit's placement — visible as ~105 bars with a small excess against 208
  short. Harmless at 1-minute width, material below it.
- The first three capture days are unusable for tick bars *for the survivor
  names* — 2026-06-17/18/19 ran the `default`, `lq45-mining` and `mining-sample`
  watchlists, none of which contain PTRO/BRPT/TPIA/ENRG. They do carry trade data
  for other names.
- **`watchlist_id` is not a reliable partition key, and the data already violates
  the invariant.** `infra/terraform.tfvars` states "changing the SET also changes
  `watchlist_id` so old/new segments don't mix in S3". It does not hold. The
  single directory `segments/dt=2026-06-17/default/` contains two different
  configurations under one id:

  | Segments | Channels | Symbols |
  |---|---|---|
  | `seg-044221`, `seg-062153`, `seg-062241` | `orderBook`, `liveprice` | BUMI |
  | `seg-112231`, `seg-112812` | `runningTradeBatch`, `orderBook`, `liveprice`, `iepiev` | BBCA, BMRI, TLKM, ASII, BBRI |

  Same day, same `watchlist_id`, different symbol set *and* different channel set.
  Any code that treats `watchlist_id` as implying a symbol set is wrong on data
  already on disk. The inventory must union `symbols[]` and `channels[]` across
  **every** manifest in a directory and gate per-symbol coverage on the required
  channel — reading one manifest per directory, or trusting the directory name,
  silently invents coverage that does not exist. (I made exactly that sampling
  error when first writing this section: I read the first manifest per directory
  and concluded `default` was BUMI-only with no trade channel. The union-based
  scan built for W1.2 is what caught it.)
- So a day can sit on disk, look like coverage, and contain no trade data for the
  name you are asking about — either because the channel was never subscribed, or
  because the symbol was not in that segment's slice of the watchlist.

## 5c. Roadmap W0.2 — proposed watchlist widening (2026-09-05)

*Proposed by roadmap W0.2. §5b above already established that `watchlist_id`
is not a reliable partition key and that readers must union across it by
manifest, never by directory name — this section is the capture-side
companion: what to widen the watchlist TO, and how to do it without forking
the history that finding says must not fork.*

**The coupling, restated from `infra/terraform.tfvars`:** "Changing the SET
also changes `watchlist_id` so old/new segments don't mix in S3." §5b already
showed this comment overstates what the directory layout actually guarantees
(`default` mixed two configurations under one id on 2026-06-17) — but the
forward-looking half holds: a new symbol set gets a new `watchlist_id`, which
forks the S3 key prefix (`raw/dt=<date>/<watchlist_id>/...`) on the day it is
applied. Every research reader audited for this change already unions across
that fork by reading manifests rather than trusting the directory name
(`research/lib/data_inventory.py`, `research/screener/tick_bars.py`,
`scripts/collector_healthcheck.py`), with one exception fixed in this same
change: `research/data/extract_survivors.sh` hardcoded
`WATCHLIST="plan10-survivors"` and would have silently dropped any date
captured under a different id — see its header comment and
`research/data/test_extract_survivors.py`. So the widening below can proceed
without forking the 7 survivors' existing 53-session history.

**Basis — the only real ranked output on disk.** `research/out_live/` holds
`universe_screen.md` / `watchlist.txt`, generated by
`screen_universe.py --source live` (dated 2026-06-20 by file mtime) — its
114-name ranked universe cannot have come from `FixtureDataSource`, which
only reads the 5 symbol CSVs under `research/screener/fixtures/`, so this is
a real chartbit/emitten pull, not synthetic. (`research/out/` — the
`--source fixture` default output directory — holds exactly the 5-symbol
BUMI/ITMG/MDKA/ANTM/DEWA fixture run and was not used here. Note also:
`screen_universe.py`'s report writer appends "Fixture data is synthetic..."
to the Notes section of *every* run regardless of `--source` — a pre-existing
cosmetic bug in the report template, not a signal to trust; the 114-name
count is what actually rules out a fixture run.) No live network call was
made to produce this proposal, per this task's constraint — see caveats
below.

**Proposed set — 43 names, built in three layers:**

1. **Keep all 7 plan-10 survivors unconditionally** — PTRO, TPIA, BRPT, ENRG,
   RAJA, BREN, RATU — so their existing history extends rather than forks.
   Only 4 of the 7 (PTRO, BRPT, BREN, RAJA) already fall inside the screener
   slice in (2); TPIA, ENRG, RATU would otherwise be silently dropped by a
   widening that only reused the screener's own selection.
2. **Add the screener top-N** — reusing the existing 30-name
   `research/out_live/watchlist.txt` (`screen_universe.py`'s own
   "broad/diverse across friction bands" slice over the 114-name
   funding-eligible universe, per ADR-0010) rather than deriving a second,
   undocumented selection rule.
3. **Add a random control slice of 10 names**, sampled without replacement
   from the 81 names in the 114-name universe not already in (1) or (2).
   This slice is the point, not a footnote: the survivors are selected by a
   full backtest grid (plan 10) and the screener slice by a liquidity/friction
   rule (plan 09), so a forward result on either alone cannot distinguish real
   edge from the selection process itself — an unselected control gives a
   future study a null baseline to diff against.

   **Sampling procedure (reproducible):**
   - Population: the 114 symbols in `research/out_live/universe_screen.md`'s
     ranked table, minus the 33 names from (1)+(2) → 81 candidates, sorted
     alphabetically (so the result does not depend on on-disk row order).
   - `random.Random(20260620).sample(population, 10)` — seed = the
     `out_live` snapshot's as-of date (2026-06-20), so the sample is tied to
     a specific, re-derivable screener run rather than an arbitrary constant.
   - Result: AALI, BSDE, HRTA, HRUM, INDF, KIJA, LSIP, MPMX, SMGR, TOBA.

**Final proposed `watchlist` (43, alphabetical):** AALI, ADRO, AMMN, ANTM,
ARCI, ASII, BBCA, BBNI, BBRI, BMRI, BREN, BRMS, BRPT, BSDE, BULL, BUMI, BUVA,
CUAN, DEWA, EMTK, ENRG, GGRM, HRTA, HRUM, INCO, INDF, INET, ITMG, KIJA, LSIP,
MBMA, MDKA, MEDC, MPMX, PTRO, RAJA, RATU, SMGR, TLKM, TOBA, TPIA, UNTR, WIFI.

**Proposed `watchlist_id`:** a new id (e.g. `plan10-survivors-w02`) — any
name change forks the prefix per the coupling above; this is a naming
suggestion for whoever implements the infra half, not a claim about what the
right id scheme should be long-term.

**Caveats:**
- The `out_live` snapshot is ~2.5 months old (2026-06-20) as of this writing
  (2026-09-05); prices, spreads, and turnover ranks may have moved.
  Refreshing it needs `screen_universe.py --source live` with a valid
  `SB_BEARER_TOKEN` — the one in this environment is expired, and re-running
  it live is explicitly out of scope for this task.
- Capture-side capacity for 43 names (bandwidth, memory, S3 write volume) is
  **not verified here** — the current live `infra/terraform.tfvars` watchlist
  is still the original 7 names; the 30-name screener slice in
  `out_live/watchlist.txt` was generated but was never observed to have been
  applied to the collector, so it is not evidence of measured headroom.
- Applying this proposal means editing `infra/terraform.tfvars` and running
  `tofu apply` (owned by another track — not done here), and extending
  `research/data/extract_survivors.sh`'s `SYMBOLS` array (or a future
  generalization of it) if fill-calibration coverage should follow the wider
  set.

## 5d. Measured frame loss — it is upstream, not the collector (2026-09-05)

*§5b established that tick VOLUME degrades in the busiest bars while tick PRICE
does not, and left the cause open: "whether that loss is the collector dropping
frames under burst or the feed sampling upstream is **not established**; the two
call for different responses." This section closes that question.*

**Method.** The datafeed assigns a per-symbol `sequence_number`. It is a single
space **shared between order-book and running-trade messages** — the collector
populates it only on book records, so the apparent "gaps" in the book sequence are
largely the trades. Calibrated on RATU, the least active name: observed book
sequences + trade count accounts for the full span to within **-1.1%**. Anything
left over after that accounting is a message the exchange numbered and we never
received. Measured over all of 2026-09-04, full session, 7 symbols, 263,398
sequence slots.

| Symbol | Book seqs seen | Trades | Seq span | Unexplained | Loss |
|--------|---------------:|-------:|---------:|------------:|-----:|
| RATU   |  9,890 |  5,414 | 15,136 |   -168 | **-1.1%** |
| BREN   | 11,211 |  5,584 | 18,345 |  1,550 |  8.5% |
| ENRG   | 16,958 | 10,109 | 30,840 |  3,773 | 12.2% |
| BRPT   | 25,461 | 13,604 | 45,793 |  6,728 | 14.7% |
| RAJA   | 12,927 |  5,135 | 22,182 |  4,120 | 18.6% |
| PTRO   | 26,640 | 13,857 | 50,179 |  9,682 | 19.3% |
| TPIA   | 43,833 | 20,423 | 80,923 | 16,667 | **20.6%** |
| **All**|        |        | 263,398| 42,520 | **16.1%** |

**The discriminating test.** Collector saturation and upstream conflation both
predict "busier symbol loses more", so the aggregate table cannot separate them.
What separates them is whether a *quiet* symbol loses frames during a
*market-wide* burst: all seven share one websocket and one process, so a starved
collector must drop indiscriminately. Taking the 30 busiest market-wide minutes:

    RATU 2.5%   BREN 7.3%   RAJA 11.3%   BRPT 14.8%   ENRG 16.9%   PTRO 21.1%   TPIA 21.2%

RATU stays near zero *in the same minutes* TPIA loses 21%. Loss tracks each
symbol's **own** update rate, not the load on the process. **The conflation is
upstream of us** — at the exchange, the feed server, or in delivery — and the
collector is not the bottleneck at 7 symbols.

**Consequences.**

- **It is not fixable by us**, so it is a permanent property of this substrate,
  not a defect to schedule. Every volume-, participation- or flow-keyed feature
  is measured on a stream that is ~16% conflated overall and ~21% on the most
  liquid names. §5b's guidance stands and is now explained, not merely observed.
- **It is mildly good news for W0.2 (§5c).** Adding 36 names should not degrade
  the existing 7, because the mechanism is per-symbol at the source rather than
  shared contention. §5c's "capture-side capacity for 43 names is not verified"
  caveat still stands on its own terms: this measurement shows we are not
  saturating **at 7 symbols on a t4g.nano (0.5 GB RAM, burstable CPU)**, and says
  nothing about 43. Size the box or stage the widening; do not read this as
  clearance.
- **It bounds order-book research.** OBI, depth and spread features see roughly
  four of every five book updates on the liquid names. Plan 10 §7.9's low-friction
  OBI re-test should treat that as a known ceiling on achievable resolution.
- **`sequence_number` is worth exposing.** `sbcap` has no gap analysis today; this
  was done with an ad-hoc pass over `sbcap export --format ndjson`. A
  `sbcap seqcheck` subcommand would make capture fidelity a routine check rather
  than an investigation. **Open.**

**Caveats.** One session. The shared-space model is calibrated on a single symbol
(RATU, -1.1% residual suggests it very slightly over-accounts, so absolute losses
may be ~1pp lower). The quiet-minute half of the discriminating test is thin —
several symbols produce too few records per minute to form a span. And this does
not distinguish exchange-side from feed-server-side conflation, only that both
are outside the collector.

## 5e. The two substrates disagree on corporate actions (2026-09-05)

**Captured tick data is RAW. Chartbit is BACK-ADJUSTED. Neither is wrong; mixing them
silently is.** Found while re-running the plan-15 impact calibration (roadmap W2.2) on
the full 53-session window, when RAJA's fitted coefficient came back at 0.0603 —
outside the sane [0.2, 2.0] band, and correctly self-flagged by the calibration's own
§8 direction-check as "review event detection or extend capture window before using".

RAJA closes across its corporate action on **2026-07-16**:

| Date | tick close | chartbit close |
|------|-----------:|---------------:|
| 2026-07-14 | 4590 | 920 |
| 2026-07-15 | 4630 | 920 |
| 2026-07-16 |  880 | 875 |
| 2026-07-17 |  890 | 885 |
| 2026-07-20 |  905 | 915 |

A **1:5.26 split**, carried raw in the capture and back-adjusted away in chartbit. It is
the only such event across all 7 names in the window (scanned for any close-to-close
move > 40 %; RAJA 2026-07-16 is the sole hit, and it appears in the tick store only).

**What it broke, and what it did not.**
- **Broke the impact fit.** The coefficient divides observed impact by `2·daily_vol_bps`,
  and `daily_vol_bps` is a close-to-close stdev. The spurious −81 % day inflated RAJA's
  vol to **2340 bps** against a true intraday dispersion of ~380 bps — a ~6x error — which
  deflated its coeff by ~5x. Compare PTRO 370 and TPIA 399 bps close-to-close.
- **Did NOT break the ORB comparison in [roadmap §6](./00-roadmap-research-to-live.md).**
  ORB is entry-to-close *within* one session, so a split between two days cannot corrupt
  it, and the 2026-07-16 session is internally consistent (all prices post-split).
- **Will break anything multi-day built on the tick store**: daily/swing families,
  close-to-close vol, overnight gaps, any return series spanning 2026-07-16 for RAJA.

**Consequences to carry forward.**
1. **Never compute a multi-day return from `data/tick_bars*` without adjusting**, and do
   not assume the two substrates are interchangeable for anything but intraday work.
   Roadmap §6's cross-substrate agreement was established on *intraday* returns
   specifically; it does not license mixing them across days.
2. **This gets worse as the watchlist widens.** §5c takes capture from 7 to 43 names, so
   the corporate-action rate rises ~6x. One unadjusted split in 7 names over 2.5 months
   implies roughly one every few weeks at 43 names.
3. **The needed piece is an adjustment factor table** keyed by (symbol, ex-date, ratio),
   applied when building bars from segments. `sbcap bars` has no notion of one today.
   Detection is cheap and worth wiring into the freshness check meanwhile: a
   close-to-close move beyond ~40 % on a liquid name is a corporate action until proven
   otherwise. **Open.**
4. **The calibration caught this on its own** — the [0.2, 2.0] direction-check did
   exactly its job. That guard is load-bearing; do not relax it.

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
