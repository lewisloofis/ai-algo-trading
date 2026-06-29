# Universe Screener — Objective, Layered, Signal-Agnostic

> **Status:** ✔️ Done · **Tier A live-verified** (Tier B + opportunity scan deferred) · **Owner:** @lewis · **Updated:** 2026-06-19
> **Related:** [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (§7.1) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [docs/reference/06-pre-trade-rules.md](../reference/06-pre-trade-rules.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [ADR-0009: universe = funding intersection](../adr/0009-universe-is-funding-intersection-strategy-fit-downstream.md)

> **What this is.** The concrete build of the roadmap §7.1 "Universe screening"
> item — a three-layer funnel that ranks the IDX funding-eligible universe from
> daily data alone, completely independent of any signal hypothesis. This is the
> upstream fix for the selection bias every verdict so far inherits from the
> current 5–6-name convenience sample. It is also the realisation of plan 08's
> "Workflow A": once scoped to Tier A daily-bar metrics, the per-symbol step is
> deterministic arithmetic — see §6 for why that degrades correctly to plain
> Python, not a Claude Workflow.

## 1. The three-layer funnel

The funnel keeps three concerns strictly separated to preserve objectivity:

| Layer | Question | Source | Nature |
|---|---|---|---|
| 1. Eligibility gate | Can I fund it *every way*? | emitten subsectors (day-trade ∩ trading-limit) | Binary filter |
| 2. Tradeability annotation | How liquid? How tick-capped? | Daily bars + tick ladder | Continuous rank + **annotations** (not drops) |
| 3. Opportunity scan | Is *now* a good moment? (e.g. Bollinger) | Daily / intraday bars | Signal — runs *inside* the universe, never defines it |

> **Revision (2026-06-19).** Two original decisions below are superseded: (a) the
> funding gate is now the **intersection** (day-trade ∩ trading-limit, "all
> funding options unlocked"), not the union; (b) edge-cap and thinness are now
> **annotations** carried on every name, not drops — they encoded a single
> intraday strategy's friction budget, which belongs to the `(name, strategy)`
> pair, not the universe. Strategy fit is decided downstream by backtest in
> [plan 10](./10-strategy-fit-and-portfolio.md). The struck-through decisions in
> §2 are kept for provenance.

**Layer 3 is explicitly not part of this plan.** Opportunity signals run as a
separate scan inside the fixed universe, after it is established. Baking a
signal into the universe definition injects look-ahead and selection bias into
every downstream backtest — the exact trap the convenience-sample universe
already set.

## 2. Locked decisions

**Signal-agnostic universe.** The universe is defined as:
`funding-eligible ∩ not-structurally-tick-capped ∩ sufficiently-liquid`. No
opportunity signal (Bollinger proximity, OBI level, momentum) touches that
definition. Signals run inside the universe; they do not shape it.

**Tier A first (forward-looking).** The screener ranks the broad
funding-eligible IDX universe from daily data to decide *what to capture next*.
Today only BUMI is captured, so a "rank existing captures" pass would be a
universe of one — useless. Tier B (order-book microstructure refinement via
`sbcap features`) is deferred until Tier-A-selected names are actually in the
capture pipeline.

**Funding gate = day-trade ∩ trading-limit (intersection).** _(Revised — was
"union".)_ A name qualifies only when **all** funding options are unlocked: it
must appear in **both** the day-trade and trading-limit emitten subsectors. This
keeps the universe strategy-agnostic — every member supports every holding style
(intraday leverage *and* overnight margin), so the universe never quietly favours
one strategy. ~~Either catalog type qualifies a name (union).~~

**Edge-cap and thinness are annotations, not drops.** _(Revised — was a
three-state drop.)_ The screener computes, per name, `min_edge_to_clear_bps`
(one-tick spread + 40 bps round-trip fee), `is_edge_capped`, and `is_thin`, and
**keeps every funded name** in the ranked output carrying those flags. ~~A name
whose one tick exceeds the 40 bps gate was dropped as EDGE-CAPPED.~~ That cap
only rejects a name *for an intraday liquidity-taking strategy*; it does not make
the name untradeable for a wider-edge horizon. Which strategy a name supports is
decided by backtest in [plan 10](./10-strategy-fit-and-portfolio.md), where the
per-name friction floor is the pass threshold.

## 3. The objectivity principle — cutoffs from the cost gate, not taste

Every screening threshold is derived from the 40 bps round-trip cost gate
(buy 0.15 % + sell 0.25 %, as in `research/backtest/backtest.py`) and the
intended position size — not from convenient round numbers.

**Tick floor (computable from price alone):**

```
min_spread_bps = tick_size(price) / price · 10_000
```

If the minimum possible spread already consumes more than the target edge, the
name is structurally edge-capped and should be flagged or dropped before any
study runs. BUMI at 168 IDR with a 1-IDR tick gives `1/168 · 10_000 ≈ 59 bps`
minimum spread — already above the 40 bps round-trip fee gate. This is
computable at screening time; no order-book data needed.

**Liquidity floor (square-root market-impact law):**

```
cost_bps ≈ half_spread_bps + Y · σ_daily_bps · √(Q / ADV)    (Y ≈ 1)
```

Inverted against an impact budget tied to predicted edge, this gives the
minimum ADV and depth required. Placeholders until edge and size are fixed:
position ≤ 1 % of ADV; position ≤ approximately 1× sweepable depth at L1.

## 4. Data sources

Route contracts verified against `sb-protos/` `google.api.http` annotations;
base host and live HTTP-transcoding need confirmation with one manual read-only
call before integration tests run.

**Funding universe (primary gate):**
`GET /oms/catalog?catalog_type=CATALOG_TYPE_DBX` and `…=CATALOG_TYPE_MBX`
(proto: `securities/trm/reports_oms/public/v1/list.proto`, rpc `Catalog`).
Returns `CatalogData{ stock{code, name}, catalog[] }`. DBX ≈ day-trade,
MBX ≈ trading-limit — confirm from the returned `catalog[]` labels. The union
is the hard eligibility gate.

Note: `GET /balance/cash/info?stock_code=X` → `day_trade_buying_power`,
`trade_limit` is a trade-time endpoint, not the screening gate.

**Daily OHLCV:**
`GET /company-price-feed/historical/summary/{symbol}?start_date&end_date&period`

**Tick ladder:**
No REST endpoint exists. Port the documented `roundTick` step function from
`docs/reference/06-pre-trade-rules.md` §5 directly:
`< 200 → 1, < 500 → 2, < 2 000 → 5, < 5 000 → 10, else 25`.

**Order-book depth / spread (Tier B only, deferred):**
`bin/sbcap features` via `research/lib/loaders.py`.

## 5. Reuse — no reinvention

The screener builds on the existing `research/` layer:

- `research/lib/loaders.py` — `load_csv` / `clean` for any segment-based inputs.
- `research/lib/session.py` — `filter_continuous_session` for time filtering.
- `research/lib/stats.py` — bootstrap / decile utilities if needed for
  summarising distributions across names.
- `research/backtest/backtest.py` — the FEE constants (`BUY_FEE`, `SELL_FEE`)
  are the single source of truth for the 40 bps cost gate. The screener imports
  them; it does not redeclare them.

Output convention: markdown verdict in `research/out/universe_screen.md`,
matching the `*.md` output pattern established in plans 04–06.

## 6. Relationship to plan 08

Plan 08 named this "Workflow A" and sketched it as a Claude dynamic Workflow —
a `parallel` per-symbol `agent()` fan-out followed by a barrier rank. That
framing assumed order-book segment processing where each per-symbol step needed
judgment about data quality.

Scoped to Tier A daily-bar metrics, the per-symbol step is **pure deterministic
arithmetic**: `tick_size(price)`, `median_daily_turnover`, `realised_vol_bps`,
`impact_bps`. No LLM judgment is involved. Applying plan 08's own discipline
test from §2 — *"breadth × independence × scale; if you can hold it in your
head → just do it"* — it correctly degrades to a **plain Python loop + thin
orchestrator**, not a `agent()` fan-out. The audited Python does the work
(plan 08 §6.1); a Workflow would only wrap it later if going live against the
full ~900-name universe made the authenticated per-symbol I/O worth fanning out
for concurrency — and even then that is concurrency, not judgment.

The genuine dynamic-Workflow payoff stays with plan 08's Workflow B (the
signal-validation battery), where each candidate hypothesis needs real
statistical reasoning and adversarial verification across three attack angles.
**The screener's job is to hand Workflow B an unbiased universe.**

## 7. Implementation

Steps 7.1 and 7.2 are complete and live-verified (2026-06-19). 7.3 and 7.4
remain deferred (see §2 and §9).

- [x] **7.1** `research/screener/metrics.py` — pure functions, no I/O:
  `tick_size(price)`, `min_spread_bps(price)`, `median_daily_turnover(ohlcv)`,
  `realised_vol_bps(ohlcv)`, `impact_bps(half_spread, vol, position, adv)`,
  `max_position_for_budget(half_spread, vol, adv, budget_bps)`.
  Unit-tested against hand-worked numbers: BUMI at 168 → `min_spread_bps ≈ 59`.
- [x] **7.2** `research/screener/screen_universe.py` — Tier A orchestrator:
  fan-out per symbol → barrier collect → rank. Pluggable data source (fixture
  default; thin live HTTP given a bearer token). **Funding gate = day-trade ∩
  trading-limit intersection** (emitten subsectors, live-verified); ranked by
  liquidity; edge-cap and thinness carried as **annotations**
  (`min_edge_to_clear_bps`, `is_edge_capped`, `is_thin`), not drops (see §2
  revision). Writes `research/out/universe_screen.md` and the input to
  [plan 10](./10-strategy-fit-and-portfolio.md).
- [x] **7.2b** Capture-watchlist emit — `select_capture_watchlist` takes a
  top-N (default 30) **broad/diverse slice** across friction bands (round-robin
  tight `<20 bps` / moderate `20–40` / wide `≥40`, liquidity-first within each)
  and writes `research/out/watchlist.txt` in the collector's exact
  `CAPTURE_WATCHLIST_FILE` format (bare symbol per line, metadata on `#` lines,
  plus a ready-to-paste `watchlist = "…"` tfvars line). **The screener does not
  write SSM directly:** the operator pastes the line into `terraform.tfvars` and
  runs `tofu apply`, keeping the capture set as auditable IaC per
  [ADR-0006](../adr/0006-watchlist-from-ssm.md) (effective on the collector's
  next boot). This closes the screen → capture loop. See
  [ADR-0010](../adr/0010-capture-watchlist-is-topn-diverse-slice-via-tfvars.md)
  for the slice policy and the tfvars-not-SSM delivery rationale.
- [ ] **7.3** Tier B refine (deferred) — order-book metrics (time-weighted L1
  depth, realised relative spread bps, quote update rate) via `sbcap features`;
  re-rank Tier-A candidates once their data is captured.
- [ ] **7.4** Opportunity scan (separate, not part of the universe) — flag
  setups (e.g. Bollinger proximity) inside the fixed universe output from 7.2.

## 8. Verification

- [x] **Unit tests** for every function in `metrics.py` against hand-worked
  values; BUMI 168 → ~59 bps is the canonical reference case. _(16 tests pass:
  8 in `test_metrics.py` + 8 in `test_screen_universe.py`.)_
- [x] **Integration test** — live read-only funding + OHLCV calls with a valid
  bearer token. _(Done 2026-06-19: the funding gate is the **intersection** of
  the two emitten subsectors, not the old `/oms/catalog` guess; OHLCV via the
  chartbit endpoint. BUMI is annotated `edge-capped` at 59.5 bps and **kept** in
  the universe — the revised, non-dropping behaviour. Endpoints recorded in the
  `sb-screener-live-endpoints` note.)_
- [x] **End-to-end** — `universe_screen.md` is produced; every funded name is
  ranked with friction annotations; the universe differs from the 5–6-name
  convenience sample (the selection-bias fix is visible).
- [x] **Cost-gate consistency** — FEE constants imported from
  `research/backtest/backtest.py`; no duplicated 40 bps value anywhere in
  `screener/`. `min_edge_to_clear_bps` = one-tick spread + the imported
  round-trip fee.

## 9. Out of scope

- Baking opportunity signals (Bollinger, OBI level, momentum) into the
  universe definition — this would recreate the selection bias the screener
  exists to eliminate.
- Tier B order-book refinement until Tier-A names are captured (no data yet).
- Reimplementing the bootstrap or decile utilities inside the screener; import
  from `research/lib/stats.py`.
- Running a full `agent()` fan-out Workflow for Tier A — see §6.

---

Prev: [08-alpha-factory-workflows.md ←](./08-alpha-factory-workflows.md) ·
Index: [README](./README.md)
