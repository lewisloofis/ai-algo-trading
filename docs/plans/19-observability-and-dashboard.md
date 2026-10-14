# Observability & Dashboard — One Readable Surface Over Three Pipelines

> **Update 2026-10-14 — the presentation layer was rebuilt.** The single three-panel page described below proved unreadable to its actual reader (most rows said "unknown"; no picture of trading activity or backtest results). `research/dashboard/` now builds a multi-page plain-language site (`make dashboard` / `python -m dashboard build`): Overview, Trades, Strategies (+ per-case detail pages), Research progress, Glossary. Panels A/B/C, `catalog_map.py` and the single-file `build.py` are gone. What still holds from this plan: the dashboard computes no metric of its own (D2, `test_no_new_metrics.py`), the post-trade store reader, the strategy-id join table that fails the build on an unknown id, calibration traffic excluded, and live never summed with paper. Per-trade backtest records come from the new `strategy_fit/trade_export.py`, which calls the harness's own functions.

> **Status:** ✔️ Done · all six stages landed · three boxes carry caveats (§6 Stages 4–6) · **Owner:** @lewis · **Updated:** 2026-09-16
> **Related:** [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [ADR-0016: alpha and infrastructure are separate tracks](../adr/0016-alpha-and-infrastructure-are-separate-tracks.md) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) · [16-strategy-triage-and-prioritization.md](./16-strategy-triage-and-prioritization.md) · [17-execution-and-replay-parity-engine.md](./17-execution-and-replay-parity-engine.md) · [18-alpha-and-infrastructure-split.md](./18-alpha-and-infrastructure-split.md) · [Plans index](./README.md)

> **What this is.** The read surface over everything the other plans already
> compute: **what the alpha factory is mining** (Track A), **whether the machine
> is running** (pillar 4 ops), and **what each strategy earned** (Track B). Three
> panels, one generated static HTML file, plus a glossary generated from the
> docstrings of the code that produces each number. This plan **renders state; it
> does not compute metrics** (§2 D2). It serves plan 18 B7 (ops) and delivers the
> reporting half of plan 11 §7.6.

---

## 1. Why now — the numbers exist, the readability does not

Every quantity an operator would want is already produced and already durable.
None of it is legible:

| Artifact | Size / shape | Why it is unreadable |
|---|---|---|
| `research/out_live/strategy_portfolio.md` | **271 KB** of markdown | The coverage table over 2,093 cells is the most decision-relevant object in the repo and cannot be scanned, diffed, or filtered |
| Post-trade SQLite (`decision` / `order_intent` / `fill` / `attribution`) | **938 MB** over the 53-session W2.4 replay | Queryable only by hand-written SQL; the roadmap §6 W2.4 attribution was extracted exactly that way, once |
| `scripts/collector_healthcheck.py` | stdout + exit 0/1/2 | Correct and thorough, but it is a check you must remember to run, not a state you can see |
| `universe/dt=*/eligibility.json` in S3 | one object/trading day | Accruing since 2026-09-13; nothing reads it back |
| `engine.Counters` | one `fmt.Printf` at process exit | Discarded the moment the run ends |

The gap is not measurement. It is that three pipelines with genuinely different
cadences — a research grid that runs occasionally, a collector that runs daily,
and a strategy runtime that will run continuously — have no common surface, so
the only way to know the state of the system is to remember which file to open.

**A second, sharper reason.** Several results in this repo were found late and by
accident: BNBR flipping −113.1 → +119.5 bps, the `×100` lot-size defect that
filed every name as `high_liq`, the volatility-estimator flip that inflated
PTRO's coefficient 3.9×, and the ORB definition mismatch in plan 17. Each was a
**change in a value that nothing was watching**. A rendered diff between runs is
not a convenience here; it is the cheapest available defect detector.

## 2. Locked decisions

**D1 — Static generated HTML. No Prometheus, Grafana, or time-series database.**
One operator, one box, episodic runs, and the data already lives in SQLite and
JSON. A metrics stack would add a daemon, a scrape config, and a second place
for a number to be defined — and would still not answer "which cells died at the
cost gate", which is not a time series. Revisit only if the runtime becomes
multi-process or multi-host.

**D2 — The dashboard computes nothing.** Every number is imported from the code
that owns its definition: decay thresholds and rolling Sharpe from
`research/strategy_fit/walk_forward.py` (whose docstring already records that it
is "shared verbatim with plan-11"), power from `research/lib/stats.py`
(`required_effective_n`, `nonoverlap_hit_rate`), hurdles and null-gate verdicts
from `research/strategy_fit/report.py`. If a panel wants a number nobody
computes, that is a task in the owning plan, **not** a computation added here.
This is the same constraint plan 11 §5 puts on the decay runner, for the same
reason: a second implementation of a metric is a second answer.

**D3 — The glossary is generated, never hand-written.** Source: the field
docstrings of `harness.CellResult`, `engine.Counters`, and the post-trade store
schema comments. These definitions carry distinctions a prose glossary would
flatten and that have already cost real time:

- `net_edge_bps` is gross minus the **full** hurdle, computed by the runner and
  not the family, "to count the friction floor exactly once".
- `eff_n` is **de-overlapped**, not a raw trade count — the entire plan 04/05
  audit turns on this.
- `oos_required_eff_n` is power-derived and explicitly **not** the
  `MIN_OOS_EFF_N` hard floor; a cell can clear the floor and still fall short.
- `oos_underpowered` distinguishes `False` (assessed, adequately powered) from
  `None` (not applicable) — and `False` additionally covers "power could not be
  assessed at all", a distinct and rarer condition.
- `impact_priced=False` means impact was **never priced for this shape**, not
  that impact is zero.

A hand-maintained copy of those sentences would be wrong within two plans.

**D4 — Provenance labels are structural, not filters.** `sink_kind`
(`paper` | live) and `is_synthetic` are rendered as always-visible banners on
every panel that shows them. `emit_strategy_portfolio`'s `is_synthetic`
parameter **defaults to `True`**, so a report is synthetic unless told
otherwise; a dashboard that renders that as a dismissible toggle will eventually
show fixture numbers as results.

**D5 — The three panels ship independently, research first.** Panel A needs no
schema migration and no live fills, so it is deliverable against today's repo.
Panels B and C are gated on work this plan itself specifies (§5 Stage 3, Stage 5).

**D6 — One page, regenerated, not a service.** `make dashboard` writes a single
self-contained HTML file. No server, no JS build step, no runtime dependency
that can be down when the thing being observed is down.

## 3. Panel A — the alpha mining process

**Question answered:** where does a candidate strategy die, and how many die there?

The state machine already exists as a typed, frozen dataclass.
`harness.CellResult.status` is a closed enum —
`tested` | `skipped_horizon` | `data_blocked` | `insufficient_n` | `cost_gated` |
`decayed` — carried alongside `passes_cost_gate`, `fdr_accepted`,
`oos_persistent`, `oos_underpowered` and `skip_reason`. The mining funnel is
therefore not a new model; it is a `GROUP BY` over state the grid already emits:

```
catalog cells            (catalog_meta.ALL_FAMILIES × universe × holds)
  ├─ data_blocked        multi_week_trend / multi_month_macro / multi_year_carry
  ├─ skipped_horizon     hold outside the tested band
  ├─ insufficient_n      below MIN_EFF_N after de-overlap
  └─ tested
       ├─ cost_gated     net_edge_bps ≤ min_edge_hurdle_bps   ← where nearly everything dies
       ├─ decayed        mean rolling Sharpe < DECAY_THRESHOLD_SHARPE
       ├─ FDR rejected   fdr_accepted = False
       ├─ loses_to_null  edge_over_null_bps ≤ hurdle
       └─ PASS
```

Views:

- **Funnel** — counts at each stage for one run. The headline number plan 18 §1
  had to compute by hand (1,379 of 1,389 cells in one structurally unwinnable
  corner) becomes a permanent readout.
- **Coverage grid** — family × horizon, cell-coloured by dominant status, with
  `catalog_status` (`testable_now` / `data_blocked`) from `catalog_meta.py` shown
  distinctly from run-time status. This is the completeness contract of plan 08
  §7.3 and plan 16, rendered: *no family is silently skipped* becomes visible
  rather than asserted.
- **Run diff** — cells whose status or `net_edge_bps` changed against the
  previous run, sorted by magnitude. This is the panel that would have caught
  BNBR and the three calibration defects.
- **Cell detail** — one cell's full `CellResult`, every field labelled from its
  own docstring (D3).

**The one gap.** `report.py` formats `CellResult` straight to markdown strings;
there is no JSON or CSV emit anywhere in `research/strategy_fit/`. Since
`CellResult` is a frozen dataclass, `dataclasses.asdict()` over the same `cells`
sequence `emit_strategy_portfolio` already receives is a sidecar of roughly
twenty lines — and it turns every run into a diffable artifact (§5 Stage 1).

## 4. Panel B — process health

**Question answered:** is the machine running, and is it recording anything real?

One row per stage, each with freshness, a volume counter, and last error:

| Stage | Source of truth | Exists? |
|---|---|---|
| Collector capture | `scripts/collector_healthcheck.py --json` | ✔️ — needs rendering, not writing |
| S3 segments | same | ✔️ |
| Universe eligibility snapshot | `universe/dt=*/eligibility.json` | ✔️ — nothing reads it back |
| Strategy runtime | **`status.json` heartbeat** | ❌ — §5 Stage 5 |
| Account reconciliation | `strategy/account/recon.go` | ✔️ output not persisted |

The runtime is the hole. `risk.mutableState` holds the day's cumulative
notional, per-symbol positions and kill-switch state **in memory only**, behind a
mutex whose doc comment already anticipates "any future ops surface". A
`status.json` written every few seconds — day notional used vs. cap, positions,
kill-switch tripped-ness, last event timestamp, `engine.Counters` so far — is
both this panel's live row and the thing plan 18 B7's alerting has to watch.
It is deliberately a file, not an endpoint: it survives the process dying, which
is exactly when it is most needed.

## 5. Panel C — per-strategy profitability

**Question answered:** what did each algo earn, net of everything, and is that
number believable?

Grouped from `attribution` by `strategy_id`: gross bps, net bps, the fee +
slippage gap between them, hold days, non-overlap hit rate, N, realised
drawdown. Plus four columns without which the panel misleads:

1. **`sink_kind`** — replay PnL and live PnL never share a row or a total.
2. **Always-long null** — the blocking gate `report.py` already applies to
   long-only cells (`edge_over_null_bps`, `loses_to_null`,
   `NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE`). PTRO's +45.1 bps signal against a
   110.2 bps hurdle is the standing example of why a gross-edge column alone is
   not a result.
3. **`required_effective_n` vs. actual N** — plan 11 §7.8's deployability
   screen. A cell that needs years to become falsifiable must say so on the face
   of the dashboard, not in a methodology note.
4. **`calibration` excluded** — reserved by ADR-0016 §6 and never reported as
   strategy performance. The dashboard enforces this, it does not trust the
   caller to filter.

**The blocking gap.** The store has **no `run_id`**. `seq` is a per-run primary
key and `--store` takes an arbitrary path, so rows from two runs cannot be told
apart or safely merged, and there is no day key on `attribution` other than
`entry_recv_ns`. Per-strategy PnL across runs is not currently expressible
(§5 Stage 3).

## 6. Build order

### Stage 1 — machine-readable grid output ✔️

`research/strategy_fit/portfolio_json.py`, written by `emit_strategy_portfolio`
alongside the markdown. Tests: `strategy_fit/test_portfolio_json.py`.

- [x] `emit_strategy_portfolio` writes `strategy_portfolio.json` alongside the
      `.md`, from the same `cells` sequence, via `dataclasses.asdict()`.
- [x] Include run provenance in the sidecar: `generated_at`, `is_synthetic`,
      impact-coefficient source (measured vs. `global_default`), holds tested,
      universe size, and the git SHA of the tree that produced it.
- [x] Test: round-trip a `CellResult` sequence through the sidecar and assert
      field-for-field equality, so a new `CellResult` field cannot be silently
      dropped from the dashboard.

### Stage 2 — Panel A + glossary ✔️

`research/dashboard/` — `panel_a.py`, `glossary.py`, `render.py`, `build.py`,
`__main__.py`, plus `catalog_map.py` (see §6.5). `make dashboard`.

- [x] `research/dashboard/` renders funnel, coverage grid, cell detail from the
      sidecar. Imports `walk_forward.py` thresholds; computes nothing (D2).
- [x] Glossary generated from `CellResult` field docstrings; every column header
      links to its entry.
- [x] Run diff against the previous sidecar in the same directory.
- [x] `make dashboard` target.

### Stage 3 — `run_id` migration ✔️

`strategy/engine/store.go`: `run_id` on all four event tables, `as_of_date` on
`attribution`, a `run` header table, and `migrate()` for stores that predate them.

- [x] Add `run_id` (and `as_of_date` on `attribution`) to all four tables.
- [x] Add a `run` table: `run_id`, start/end, source (`replay` | `live`),
      pacing, fill mode, strategy id, segment glob or symbol list, binary hash,
      and the final `engine.Counters`.
- [x] `strategy-run` generates `run_id` at start and writes the `run` row at exit
      — including on error, so a crashed run is visible as a crashed run.
- [x] Decide and document the store-path convention. **Decided: one store file
      per run** (`engine.StorePathConvention`). `seq` is the primary key and every
      run restarts it at 1, so appending a second run to one file collides — which
      is the append-only guarantee working, not a flaw to route around. Widening
      the key to `(run_id, seq)` would rewrite the primary key of the existing
      938 MB W2.4 store for no gain. `run_id` is still stamped on every row, so a
      file is self-describing and a reader handed several can UNION them and still
      group correctly. Verified in practice: a second run into an occupied file
      exits non-zero and its header is finalised as `outcome='error'`.

### Stage 4 — Panel C ✔️

`research/dashboard/panel_c.py` + `store.py`.

- [x] Per-`strategy_id` aggregation with the four columns of §5.
- [~] Decision funnel from `decision` → `order_intent` → `fill`. **Partial:**
      decisions → signals → intents → fills are counted per (run, strategy) and
      rendered, but the four *reasons* an intent does not fill — `MarketGate`
      denial, `RiskSink` rejection, `ErrNoBook`, broker rejection — collapse into
      a single `intents_unfilled` column, because **the store does not record
      why**. `engine.SubmitOrder` returns the sink's error to the strategy and
      writes nothing. Separating them needs a rejection column (or a
      `rejection` table) on the Go side first; `store.DecisionFunnel`'s docstring
      says so at the point of use. On the first real render the visible drop-off
      was already large: 98,010 signals → 22 intents.
- [ ] Wire to plan 11 §7.6 — the daily decay report and this panel are the same
      artifact with thresholds attached; build them together, not twice.
      **Not done.** Panel C imports plan 11's thresholds through the same modules
      the decay runner uses, so the two cannot disagree about a threshold, but no
      daily decay-report artifact is emitted. That is plan 11's deliverable and it
      has not been built; this plan did not create a second one.

### Stage 5 — `status.json` + Panel B ✔️

`strategy/engine/status.go` (`RunProgress`, `StatusWriter`),
`strategy/risk/snapshot.go` (`RiskSink.Snapshot`), `research/dashboard/panel_b.py`.

- [x] `strategy-run` writes `status.json` on a ticker (`--status`,
      `--status-interval`): `run_id`, wall clock, last event ts and its WIB date,
      outcome, and counters so far. Written by atomic rename, so a poller never
      reads a half-written file. **Caveat on the risk half:** day notional vs.
      cap, positions and kill-switch state come from `risk.RiskSink.Snapshot`,
      which is built and tested — but `strategy-run` does not wire a `RiskSink`
      at all today (that is plan 18 B2/B4), so a real run currently emits
      `"risk": null`. The panel renders that as *not reported*, never as *no
      limits in force*.
- [x] Panel B renders the stage table; folds in `collector_healthcheck --json`.
      The key set is asserted against the health check's own `_result_to_dict`,
      so a rename there fails the test rather than blanking a row. **Not yet
      exercised against live output** — the AWS SSO token was expired, so the
      check could not reach S3 during this build.
- [~] Alert hooks for plan 18 B7. **Kill-switch trip, stale heartbeat, run
      error, run crashed and collector-check failure all fire** and are named
      constants B7 can import. **Reconciliation divergence cannot fire:**
      `ALERT_RECON_DIVERGENCE` is defined and the row is rendered as `unknown`
      saying why, but `strategy/account/recon.go` does not persist its output, so
      there is nothing to compare. Persisting it is the remaining Panel B gap.

### Stage 6 — publication ✔️

`make publish-dashboard` — private object, no public ACL, read via a presigned URL.

- [x] Write the HTML to S3 beside the market data, so the dashboard is readable
      without the laptop that generated it. **Target written, not yet run:**
      publishing is an outward-facing action and the SSO token was expired during
      this build. The object is private — no public-read ACL, no bucket-policy
      change, no website hosting — because the page carries run ids, position
      sizes and PnL; it is read with credentials or a presigned URL, exactly like
      the segments next to it.

## 6.5 What landed, and what it found on the first real run

**Two defects, both found by the panels within minutes of pointing them at a
real grid run.** That is the §1 argument — "a rendered diff is the cheapest
available defect detector" — paying for itself before the page had a reader.

**1. `CellResult.status` was documented as a six-value enum; the grid emits
eight.** `long_short_infeasible` (cross-sectional and pairs cells: IDX retail
cannot short, `long_only.STATUS_LONG_SHORT_INFEASIBLE`) and
`cointegration_not_found` (`families_pairs.py`, Engle-Granger p above
threshold) have been produced for some time and were never written down. The
funnel counted 10 of 115 cells as `UNCLASSIFIED` and said so on the page rather
than dropping them, which is exactly the conservation behaviour §8 asks for.
Fixed at the source — the `harness.py` docstring, which is also the glossary's
source — and both are now terminal stages in the funnel.

**2. The runner and the catalog use different names for the same families, and
nothing joined them.** `CellResult.family_name` is the runner's vocabulary
(`cross_sectional_momentum`, `pairs_cointegration`, `gap_event`);
`catalog_meta.FamilyMeta.family_id` is the catalog's (`cross_sectional`,
`pairs_stat_arb`, `event_driven_gap`). Compared by string, the coverage grid
reported **seven of ten catalog families as never tested** when most had been
tested under another name — a false gap on the very completeness contract
(plan 08 §7.3, plan 16) the panel exists to check. The real answer is four:
the three `data_blocked` families and `obi_ofi_tick`, whose `order_book` shape
is deferred (plan 10 §7.9).

The fix is `research/dashboard/catalog_map.py`, an artifact this plan did not
anticipate: the same explicit-table-with-a-loud-failure pattern §7 requires of
the runtime `strategy_id` join, applied to the research side. Three things stay
visible rather than being collapsed: a runner family with no catalog entry
(`volume_breakout`, `intraday_mean_reversion`, the two non-gap event families),
a name the table has never heard of, and a catalog family genuinely absent from
the run. `orb` is **deliberately** left unmapped — the runner's ORB family opens
on the 30-minute range and the catalog's `intraday_momentum` is the
morning-to-afternoon family, so mapping them would repeat, inside research, the
same conflation §7 refuses at the runtime boundary.
`test_catalog_map.py::test_every_runner_family_is_mapped` is driven off the
runner's own family constants, so a new family fails the test rather than
quietly mis-stating coverage.

**Three smaller things the build settled.**

- `engine.Counters` needed JSON tags. Without them `status.json` carried
  `OrdersAttempted` while every reader looked for `orders_attempted`. The tags
  are now load-bearing and their doc comment says so.
- `StatusWriter.Start`'s stop function must **block** until the ticker
  goroutine has returned, not merely signal it. Signal-and-return loses the
  race often enough that a 20 ms test caught it: a tick already inside
  `WriteOnce` lands after the terminal write and leaves a finished run
  reporting `running` forever.
- The `run` table is the one place this store issues an `UPDATE`, and only
  there. The four event tables stay strictly INSERT-only. Writing the header at
  start and closing it at exit is what makes a crashed run visible as a crashed
  run instead of as no run at all.

**What the first real render showed.** Two replay runs (2026-06-22 and
2026-06-23, ORB, paper taker fills) over 864k events: 11 round-trips, mean net
+96.2 bps, 45% win rate, 1,601.6 bps max drawdown — and a deployability screen
of **103 required round-trips against 11 realised**, so the result is not yet
falsifiable, which is the column's whole job. The decision funnel showed 98,010
signals collapsing to 22 intents, a drop-off that had no representation
anywhere before this page.

## 7. Traps the dashboard must encode

These are not documentation notes; each is a way the panel can silently lie, and
each needs a mechanical guard.

- **`orb` ≠ `intraday_momentum`.** The Go strategy fires on any liveprice tick
  0.5 % above the day's first tick; the Python family fires on a 1-minute bar
  close above the 30-minute opening-range high (plan 17's warning box). Joining
  post-trade `strategy_id` to strategy-fit `family_name` by string would report
  one strategy's realised cost against another's modelled edge. **The join must
  be an explicit map, and an unmapped id must fail loudly rather than render.**
- **Borrowed impact coefficients.** `report.py` already warns that
  `impact_coeffs.json` was calibrated from tick fills and is *borrowed* for daily
  cells. That warning must survive into any panel showing a daily hurdle.
- **Window-sensitive inputs.** Impact depends on both the ADV window and the
  volatility estimator — PTRO's impact ranged 3.9 → 33.6 bps by ADV window and
  its coefficient moved 3.9× by estimator choice. Any impact or hurdle figure
  renders with the window that produced it, or it is not a number.
- **Synthetic by default.** `is_synthetic=True` is the default (D4).
- **Replay ≠ live.** Until plan 18 B4 lands, every PnL figure on Panel C is
  replay. The panel says so in a banner, not a footnote.

## 8. Verification

- **No-new-metric test** — the dashboard package imports its thresholds and
  statistics; a test asserts it defines no Sharpe, hit-rate, hurdle or power
  function of its own (D2).
- **Glossary completeness** — every column rendered has a glossary entry sourced
  from a docstring; a column without one fails the build (D3).
- **Field-drift test** — Stage 1's round-trip test, extended: a `CellResult`
  field absent from the rendered cell detail fails.
- **Funnel conservation** — stage counts sum to the catalog total; a cell that
  reaches no terminal state is a bug in the grid, and the dashboard surfaces it
  rather than dropping it.
- **Join safety** — an unmapped `strategy_id` fails the render (§7).

## 9. Out of scope

- Real-time streaming charts, tick-level replay UI, or anything requiring a
  server process (D1, D6).
- Any new statistic, threshold, or verdict. Panel C's thresholds come from plan
  11; Panel A's from plan 10 / `walk_forward.py`.
- Alert *routing* (email/Telegram/SNS). Stage 5 defines the conditions; delivery
  is plan 18 B7's.
- Rendering `research/out/*.png` study output — those belong to plans 04–06 and
  are already self-contained.

---

Back to [Plans index](./README.md)
