# Universe Screener — Objective, Layered, Signal-Agnostic

> **Status:** 🔨 In Progress · **Owner:** @lewis · **Updated:** 2026-06-19
> **Related:** [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (§7.1) · [08-alpha-factory-workflows.md](./08-alpha-factory-workflows.md) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [docs/reference/06-pre-trade-rules.md](../reference/06-pre-trade-rules.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md)

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
| 1. Eligibility gate | Can I fund it? | Stockbit Catalog API (DBX ∪ MBX) | Binary filter |
| 2. Tradeability rank | Liquid enough to exit? Edge tick-capped? | Daily bars + tick ladder | Continuous rank |
| 3. Opportunity scan | Is *now* a good moment? (e.g. Bollinger) | Daily / intraday bars | Signal — runs *inside* the universe, never defines it |

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

**Funding gate = trading-limit OR day-trade (union).** Either catalog type
qualifies a name for consideration. The gate is binary: present in DBX or MBX
→ eligible; absent → dropped.

**Liquidity floor deferred until edge + size are fixed.** The rank is computed
now (ADV, vol, depth proxy from tick ladder); the numeric pass/fail floor that
converts a rank into a hard TRADEABLE / TOO-THIN tag is derived later once a
target edge and position size are settled. Until then, the three-state output
tags names as TRADEABLE (passes current soft floors), EDGE-CAPPED
(structurally tick-floor-capped), or TOO-THIN (placeholder floor exceeded).

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

Steps 1 and 2 are currently in progress.

- [ ] **7.1** `research/screener/metrics.py` — pure functions, no I/O:
  `tick_size(price)`, `min_spread_bps(price)`, `median_daily_turnover(ohlcv)`,
  `realised_vol_bps(ohlcv)`, `impact_bps(half_spread, vol, position, adv)`,
  `max_position_for_budget(half_spread, vol, adv, budget_bps)`.
  Unit-tested against hand-worked numbers: BUMI at 168 → `min_spread_bps ≈ 59`.
- [ ] **7.2** `research/screener/screen_universe.py` — Tier A orchestrator:
  fan-out per symbol → barrier collect → rank. Pluggable data source (fixture
  default; thin live HTTP given a bearer token). Catalog DBX ∪ MBX gate,
  tick-floor-led ranking, three-state tags (TRADEABLE / EDGE-CAPPED / TOO-THIN),
  writes `research/out/universe_screen.md`. The ranked list becomes the next
  capture watchlist — feeds the collector via the SSM watchlist (ADR-0006).
- [ ] **7.3** Tier B refine (deferred) — order-book metrics (time-weighted L1
  depth, realised relative spread bps, quote update rate) via `sbcap features`;
  re-rank Tier-A candidates once their data is captured.
- [ ] **7.4** Opportunity scan (separate, not part of the universe) — flag
  setups (e.g. Bollinger proximity) inside the fixed universe output from 7.2.

## 8. Verification

- **Unit tests** for every function in `metrics.py` against hand-worked values;
  BUMI 168 → ~59 bps is the canonical reference case.
- **Integration test** — one live read-only Catalog + historical OHLCV call
  with a valid bearer token: confirm the funding gate returns DBX/MBX labels,
  ADV is plausible, BUMI is tagged EDGE-CAPPED.
- **End-to-end** — `universe_screen.md` is produced; ranked names include drop
  reasons; the top names differ from the 5–6-name convenience sample (the
  selection-bias fix is visible).
- **Cost-gate consistency** — FEE constants imported from
  `research/backtest/backtest.py`; no duplicated 40 bps value anywhere in
  `screener/`.

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
