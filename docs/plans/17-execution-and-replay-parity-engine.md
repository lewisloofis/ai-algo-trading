# Execution & Replay-Parity Engine + Post-Trade Store — The Event-Driven Runtime Scaffold

> **Status:** 🔨 In Progress · build-now tasks done · §2 gate superseded by ADR-0016 · **Owner:** @lewis · **Updated:** 2026-09-13
> **Related:** [ADR-0016: alpha and infrastructure are separate tracks](../adr/0016-alpha-and-infrastructure-are-separate-tracks.md) · [18-alpha-and-infrastructure-split.md](./18-alpha-and-infrastructure-split.md) · [ADR-0011: capital model](../adr/0011-capital-model-300m-leverage-sleeve.md) · [ADR-0008: top-level layout mirrors pillars](../adr/0008-top-level-layout-mirrors-pillars.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md) · [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) · [06-fill-simulator-and-backtest.md](./06-fill-simulator-and-backtest.md) · [07-repo-restructure.md](./07-repo-restructure.md) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) · [13-capital-allocation-and-capacity.md](./13-capital-allocation-and-capacity.md) · [14-market-calendar-and-time-system.md](./14-market-calendar-and-time-system.md) · [15-measured-fill-and-impact-calibration.md](./15-measured-fill-and-impact-calibration.md) · [16-strategy-triage-and-prioritization.md](./16-strategy-triage-and-prioritization.md) · [reference/03-order-placement.md](../reference/03-order-placement.md) · [reference/04-order-lifecycle.md](../reference/04-order-lifecycle.md) · [reference/06-pre-trade-rules.md](../reference/06-pre-trade-rules.md) · [reference/07-fees-and-costs.md](../reference/07-fees-and-costs.md) · [Plans index](./README.md)

> **What this is.** The Go event-driven engine that wires the plan-02 replayer
> into a `strategy.Strategy` interface, producing (a) a proper event-driven
> backtester usable immediately against captured book data, and (b) the
> append-only post-trade store that plan 11 is hard-blocked on. The live order
> router is designed here but built only after the confirmed-edge + paper-trade
> gate clears (§2). This plan serves roadmap pillar 2 "execution system" §2 and
> pillar 4.2 "live trading runtime."

---


> ## ⚠️ 2026-09-05 — THE PARITY ENGINE DOES NOT HAVE PARITY
>
> This plan's premise (roadmap §2) is "the event-driven **Go** engine that runs the
> *same* `strategy.go` interface live and in replay". At the strategy-definition level
> that is **not currently true**, and §7.8 has been treated as an arbiter of the Python
> harness's result while testing a different rule:
>
> | | entry trigger | threshold |
> |---|---|---|
> | Go `strategy/strategies/orb/orb.go` | any liveprice **tick** | fixed **0.5 %** above the day's **first tick** (`ORBBreakoutPct = 0.005`; `st.openPx` is set from the first tick, not an opening range) |
> | Python `research/strategy_fit/families_intraday.py` | 1-minute bar **close** | **high of the first 30 minutes** (`ORB_WINDOW_MINUTES = 30`) |
>
> "Buy on a 0.5 % move off the open" and "buy on a bar close above the opening-range
> high" are different strategies. The replay finds ~45 trades/symbol where the bar-based
> family finds ~25 over the same 53 sessions — that gap is the rule, not the substrate.
>
> This inflated the roadmap's central "the harness and the tape disagree" mystery, which
> turned out to be **three** differences at once: different windows (in-sample vs
> out-of-sample), different trade universes (the harness scored unrealizable shorts until
> `c62eba8`), and different rules (this).
>
> **Both rules lose.** The W2.4 re-run over 53 sessions: 319 round-trips, **−59.5 bps
> gross / −99.4 bps net**, every symbol negative gross, win rate 26–44 %. The Python rule
> loses too (roadmap §6). So the conclusion is not in doubt — but it was reached by
> accident, and until the definitions are reconciled this engine cannot be used to
> confirm or refute a research result. **Open, and a prerequisite for §7.11–7.16.**

## 1. Why now / scope discipline

The roadmap (plan 00 §6) defers execution machinery until a confirmed edge
clears the 40 bps gate. The ORB edge identified in plan 10 is **still
provisional**: intraday persistence is pending plan 14's deadline mechanism, and
measured fills are pending plan 15. Nothing here contradicts that discipline.
What this plan builds instead is the subset that earns its keep *right now*,
independent of which edge ultimately survives:

**Part A — the event-driven backtester.** The Python fixed-horizon harness
(plan 06) proved the OBI edge dead. The ORB survivors need a more honest
evaluation: event-by-event replay against captured book, respecting order flow,
book state, and the gap/reconnect lifecycle that the static CSV harness ignores.
That evaluation is useful now — it either confirms the ORB survivors or kills
them before more capital is committed to the live transition. The engine to do
it is the same engine the live system will eventually run.

**Part B — the post-trade store.** Plan 11 carries a hard block: "cannot run
before the post-trade store exists." That store is a pillar-4.2 prerequisite,
not a live-trading requirement. It can be built, exercised, and validated in
replay/paper mode before any real order is placed. Building it now unblocks
plan 11's schema design and lets the monitoring infrastructure be tested against
simulated fill data.

**What this plan explicitly does not build:** live order routing to Carina, live
WS event sourcing, pre-trade risk enforcement in code, or any part of the go-live
ladder. Those are deferred to the STRICT GATE in §3. This is the same
"design-now / run-after-live" posture plan 13 takes for capital allocation.

---

## 2. The confirmed-edge gate — a hard prerequisite, not a soft note

> ## 🗑️ SUPERSEDED 2026-09-13 — conditions 1 and 3 are void
>
> [ADR-0016](../adr/0016-alpha-and-infrastructure-are-separate-tracks.md) supersedes
> **condition 1 (edge confirmation)** and **condition 3 (calibrated kill criteria)** as
> preconditions for the live router's *existence*. They formed a cycle: a strategy verdict
> needs a trustworthy cost model → that needs realized fills → those need live orders →
> which this section gated behind a confirmed edge. The cost model's impact term is
> `global_default: 1.0` — a hand-picked constant — for every name outside the 47-name
> capture, so the gate forbade the measurement its own condition 1 depended on.
>
> **Condition 2 (paper forward test) is retained**, re-scoped: it gates an increase in
> *size*, not whether the router exists.
>
> Live routing is now gated on **bounded size and enforced controls** instead:
> G1 position reconciliation · G2 risk layer in code (daily loss limit, gross exposure cap,
> kill switch) · G3 de-minimis per-order **and** per-day notional caps · G4 the existing
> `ALGO_LIVE` + verbatim `ALGO_CONFIRM` double-arm. See
> [plan 18 §4](./18-alpha-and-infrastructure-split.md) for the build order.
>
> **§6.1–6.6 are promoted from design sketch to tickable work** as plan 18's B3–B6, and
> the §7 "Deferred tasks" block is unblocked to the same extent. The body below is
> preserved unedited as the record of the original decision.


No live order routing is built or enabled by this plan. The following
conditions must ALL be satisfied before the live Carina sink (§6) is
implemented:

1. **Edge confirmation (plan 14 + plan 15).** The ORB edge holds intraday
   persistence under the plan-14 holiday-correct calendar, *and* measured fills
   from plan 15 do not erode the simulated net edge below the 40 bps cost gate
   (reference/07-fees-and-costs.md).
2. **Paper-trade forward test (roadmap pillar 5, §6 step 6).** The engine runs
   in paper mode (replay event source + paper sink) for a forward test period
   whose PnL, fill quality, and decay metrics meet the pre-agreed go-live
   thresholds.
3. **Plan 11 kill criteria calibrated.** The decay monitor is live, kill-criteria
   thresholds are set from the paper-trade period, and the global off-switch is
   wired.

Until all three are satisfied, the live Carina sink is a design sketch only.
Any code that would route a real order to `https://carina.stockbit.com` must be
behind an explicit `--enable-live-routing` flag that is absent from the binary
unless this gate has been passed and the flag added deliberately.

---

## 3. Locked decisions

### 3.1 One strategy interface, live and replay identical

```go
// strategy/engine/strategy.go
type Strategy interface {
    // OnEvent is called for every market event in arrival order.
    // The implementation must not block; it may call engine.SubmitOrder.
    OnEvent(ctx context.Context, e MarketEvent, engine Engine)

    // OnFill is called when an order fill record is available
    // (simulated or real).
    OnFill(ctx context.Context, f FillRecord)
}
```

The engine differs only in its **event source** (the plan-02 replayer reading
captured raw frames via `decodeWrap`, or a live WS feed) and its **order sink**
(paper or Carina). The strategy implementation never calls either directly — it
calls `engine.SubmitOrder`, which dispatches to whichever sink is wired at
startup. This is the live/replay parity thesis from plan 02 §1: "the whole
point of plan 02."

[ADR-0013](../adr/0013-one-strategy-interface-and-collector-public-packages.md)
records this one-interface decision and the collector package-promotion decision
as formal architectural constraints. Both govern the shape of every future
strategy implementation and every attribution query. (The
post-trade-store-as-source-of-truth decision in §3.3 is documented in this plan;
it can be folded into a follow-up ADR if it proves load-bearing for plan 11.)

### 3.2 Pluggable order sink

```go
type OrderSink interface {
    Submit(ctx context.Context, intent OrderIntent) (OrderID, error)
    Cancel(ctx context.Context, id OrderID) error
}
```

Two implementations:

| Sink | Mode | Status |
|------|------|--------|
| `PaperSink` | Simulates fills using the plan-15 calibrated fill/impact model; no network calls | **Build now** |
| `CarinaSink` | Routes to `https://carina.stockbit.com` via the reference/03 Carina order API | **DEFERRED — §2 gate** |

The strategy code never knows which sink is active. Swapping live for paper is
a startup flag, not a code change.

### 3.3 Post-trade store as the single source of truth

The post-trade store is the append-only log of every decision the engine makes
and every fill it records. It is:

- **Append-only** — records are never modified; attribution queries read the
  log chronologically.
- **Replay-reproducible** — given the same segment files and the same strategy
  binary, the post-trade store produced by a replay run must be identical to
  the one produced by the original run. This is the property that lets
  attribution audits be re-run.
- **Exercised before any live trade** — in paper/replay mode the store logs
  simulated decisions and fills. Plan 11's decay runner consumes these records
  in the same format it will consume live fills; the schema and tooling are
  validated before go-live.

The store is the data substrate plan 11 is hard-blocked on. Building it now
unblocks that plan's schema design and test harness.

### 3.4 Reuse, do not rebuild

The replayer (`collector/internal/replay`) and `decodeWrap` (`collector/internal/feed`)
are plan-02 deliverables. The `strategy/` Go module seed is the plan-07 repo
restructure. The Carina order-client sketch is in `strategy/` already (from the
zero-dep prototype). The engine is those pieces wired into a strategy loop, not
new transport code. No new WS transport, no new protobuf codec, no new S3
client.

---

## 4. Architecture overview

```
                    ┌────────────────────────────────────┐
                    │          Engine (strategy/)         │
                    │                                     │
  ┌───────────────┐ │  ┌──────────┐     ┌─────────────┐  │  ┌──────────────┐
  │  Event Source │─┼─▶│  Clock   │────▶│  Strategy   │  │  │ Order Sink   │
  │               │ │  │          │     │  (user impl) │──┼─▶│              │
  │  Replay:      │ │  │  replay  │     │              │  │  │  PaperSink   │
  │   Replayer    │ │  │  or live │     │  OnEvent()   │  │  │  (now)       │
  │  (plan 02)    │ │  │  wall    │     │  OnFill()    │  │  │              │
  │               │ │  └──────────┘     └─────────────┘  │  │  CarinaSink  │
  │  Live (defer):│ │                                     │  │  (deferred)  │
  │   WS feed     │ │  ┌──────────────────────────────┐  │  └──────────────┘
  └───────────────┘ │  │      Post-Trade Store        │  │
                    │  │   (append-only log, SQLite)  │◀─┘
                    │  │                              │
                    │  │  Decision / Intent / Fill /  │
                    │  │  Attribution records         │
                    │  └──────────────────────────────┘
                    └────────────────────────────────────┘
```

The clock is the replay timestamp from `MarketEvent.RecvNs` in replay mode, or
wall-clock in live mode. Strategies access the clock only through the `Engine`
interface and never call `time.Now()` directly — this is what makes replay
deterministic.

---

## 5. Build-now: the replay/paper engine

### 5.1 Engine event loop (`strategy/engine/`)

```
strategy/
  engine/
    engine.go      — Engine interface + Run() loop
    strategy.go    — Strategy interface (§3.1)
    clock.go       — Clock interface (replay ts vs wall-clock)
    sink.go        — OrderSink interface + PaperSink
    store.go       — PostTradeStore (append-only, SQLite)
    event.go       — MarketEvent wrapper (re-export of collector types)
  cmd/
    strategy-run/  — CLI: --segments, --sink paper|live, --strategy <name>
```

`Run()` accepts an event source (a channel of `MarketEvent`), instantiates the
registered strategy, and dispatches events in arrival order. It does not spawn
goroutines inside the strategy — the strategy's `OnEvent` runs synchronously
on the event loop goroutine; if a strategy needs async work it manages that
internally.

The loop:

```go
func Run(ctx context.Context, src <-chan MarketEvent, strat Strategy, sink OrderSink, store *PostTradeStore) error {
    for {
        select {
        case <-ctx.Done():
            return ctx.Err()
        case evt, ok := <-src:
            if !ok {
                return nil // source exhausted (replay end)
            }
            store.LogDecision(evt) // record what the engine saw
            strat.OnEvent(ctx, evt, engineHandle{sink, store})
        }
    }
}
```

### 5.2 Replay event source

The replay event source wraps the plan-02 `Replay()` function, converting its
`handler func(MarketEvent)` callback into a `chan MarketEvent` that the engine
loop reads:

```go
func ReplaySource(ctx context.Context, segments []string, mode PacingMode) <-chan MarketEvent {
    ch := make(chan MarketEvent, 256)
    go func() {
        defer close(ch)
        _ = replay.Replay(segments, mode, func(e replay.MarketEvent) {
            select {
            case ch <- MarketEvent(e):
            case <-ctx.Done():
            }
        })
    }()
    return ch
}
```

Pacing modes follow plan-02 §6.2: `ModeFast` for batch backtesting, `ModePaced`
for forward-test realism. The engine does not care which mode is active.

### 5.3 Paper order sink (plan-15 fill model)

`PaperSink` simulates fills using the calibrated fill and impact model from
plan 15. Until plan 15 delivers its calibrated parameters, the sink uses the
same taker fill model as the plan-06 backtester (fill at far touch, full size
up to top-of-book, no queue simulation). Once plan 15's parameters land, they
are loaded at startup from a config file — the sink implementation does not
change.

```go
type PaperSink struct {
    model     FillModel        // swap for calibrated plan-15 model when ready
    store     *PostTradeStore  // logs every simulated fill
    clock     Clock
}

func (s *PaperSink) Submit(ctx context.Context, intent OrderIntent) (OrderID, error) {
    fill := s.model.Simulate(intent, s.clock.Now())
    s.store.LogFill(fill)
    return fill.OrderID, nil
}
```

### 5.4 Post-trade store schema

SQLite, append-only. Three record kinds, all with a monotonic `seq` and a
`recv_ns` timestamp sourced from the engine clock:

**Decision record** — written on every `OnEvent` call before the strategy runs:

| Column | Type | Description |
|--------|------|-------------|
| `seq` | INTEGER | monotonic sequence, replay-reproducible |
| `recv_ns` | INTEGER | event clock (replay ts or wall ns) |
| `strategy_id` | TEXT | registered strategy name |
| `symbol` | TEXT | event symbol (empty for control) |
| `event_kind` | TEXT | `liveprice / orderbook / running_trade / control` |
| `signal_value` | REAL | strategy's computed signal (nullable; strategy fills in) |

**Order intent record** — written when `engine.SubmitOrder` is called:

| Column | Type | Description |
|--------|------|-------------|
| `seq` | INTEGER | monotonic |
| `recv_ns` | INTEGER | engine clock at submission |
| `strategy_id` | TEXT | |
| `symbol` | TEXT | |
| `side` | TEXT | `buy / sell` |
| `price_idr` | INTEGER | price in IDR (tick-snapped) |
| `lots` | INTEGER | lot quantity (1 lot = 100 shares) |
| `order_id` | TEXT | client-assigned idempotency id |
| `sink_kind` | TEXT | `paper / live` |

**Fill record** — written when `OnFill` is called:

| Column | Type | Description |
|--------|------|-------------|
| `seq` | INTEGER | monotonic |
| `recv_ns` | INTEGER | fill clock |
| `order_id` | TEXT | links back to intent |
| `fill_price_idr` | INTEGER | actual fill price |
| `fill_lots` | INTEGER | filled quantity |
| `fee_idr` | INTEGER | fee charged (reference/07 schedule) |
| `slippage_bps` | REAL | `(fill_price − intent_price) / intent_price × 10000` |
| `net_pnl_idr` | INTEGER | PnL attributed to this fill (computed at close) |
| `sink_kind` | TEXT | `paper / live` |

**Attribution record** — written at position close (round-trip complete):

| Column | Type | Description |
|--------|------|-------------|
| `seq` | INTEGER | monotonic |
| `symbol` | TEXT | |
| `strategy_id` | TEXT | |
| `entry_recv_ns` | INTEGER | engine clock at entry fill |
| `exit_recv_ns` | INTEGER | engine clock at exit fill |
| `gross_pnl_bps` | REAL | gross round-trip return in bps |
| `net_pnl_bps` | REAL | net after fees and slippage |
| `entry_order_id` | TEXT | |
| `exit_order_id` | TEXT | |
| `hold_calendar_days` | REAL | calendar days from entry to exit (feeds plan 14 financing clock) |

The `hold_calendar_days` column is computed using `settlement_calendar_days`
from the plan-14 calendar reader — the attribution record uses the same
holiday-correct financing clock as the cost gate.

### 5.5 ORB event-driven re-validation

The first real exercise of the engine is an event-driven re-validation of the
ORB survivors from plan 10. This replaces the static CSV harness with proper
event-by-event replay:

- Segments from the capture archive for the ORB survivor symbols are fed to
  `ReplaySource` in `ModeFast`.
- The ORB strategy implementation reads the opening auction price from
  `LivePrice.open` in the opening bar and sets its breakout threshold.
- The paper sink simulates fills using the pre-plan-15 taker model.
- The post-trade store accumulates decision, intent, fill, and attribution
  records.
- A summary script (Python, reusing `research/strategy_fit/`) computes the
  same rolling-Sharpe and hit-rate metrics as plan 10 over the store's
  attribution records, enabling a direct comparison.

The comparison answers: does the static-CSV result hold up when the strategy
runs event-by-event through the real book feed, gap events included? A material
divergence is a signal that the CSV harness masked a real-time execution issue;
agreement is the event-driven confirmation plan 10 lacked.

---

## 6. Deferred: design sketch for the live runtime

The items below are **specified here so the design is coherent** but are not
built until the §2 gate is passed. They are recorded as design constraints, not
implementation tickets.

### 6.1 Live WS event source

The live event source wraps the `strategy/` module's existing WS client (the
plan-07 seed, which already connects to `wss://wss-trading.stockbit.com/ws`
and calls `decodeWrap`). It converts the callback stream into the same
`chan MarketEvent` the replay source produces — the engine loop is unchanged.

Because the market-data socket and the portfolio/orders socket
(reference/04-order-lifecycle.md) are separate connections on separate URLs
and auth schemes, the live runtime requires two socket goroutines: one feeding
`MarketEvent` to the engine, one delivering fill confirmations from
`wss://ws-gen.stockbit.com/v1`. Both funnel into the same event loop via typed
channels.

### 6.2 Live Carina order router (`CarinaSink`)

Routes order intents to `https://carina.stockbit.com` via the reference/03
Carina order API. Key implementation constraints:

- **`ui_ref` idempotency** — every order submission generates a client-side
  `ui_ref` (reference/03 §0) stored in the post-trade store before the HTTP
  call. On reconnect or duplicate-fire, the `ui_ref` prevents double orders.
- **Trading token session** — the Carina sink requires the trading session token
  (reference/01 §4), not the main session token. Token rotation follows plan-03.
- **Hard exit on T+2** — plan 13's T+2 exit rule is enforced by the sink: an
  order intent whose computed hold would breach T+2 is rejected before it is
  submitted, not after.

### 6.3 Smart execution (deferred)

Limit-vs-market, passive-vs-aggressive, and order slicing are not specified
here. The plan-15 impact calibration will determine when crossing the spread
costs more than queue time saves — that output feeds the smart execution logic.
Until plan 15 delivers measured fill parameters the taker model (cross at far
touch) is the only safe default.

### 6.4 Pre-trade risk checks (deferred)

The rules in reference/06-pre-trade-rules.md (ARA/ARB bands, tick validation,
suspension check, session-phase gate, holiday check) must be enforced in code
by `CarinaSink` before any order is submitted. The pre-trade checklist in
reference/06 §1 is the normative spec; `CarinaSink` implements it in order. The
plan-14 calendar reader provides the holiday check (`is_trading_day`).

### 6.5 Order lifecycle (deferred)

Partial fills, cancels, rejects, and reconnect-mid-order handling follow the
state machine in reference/04-order-lifecycle.md. On reconnect, open orders
must be reconciled via the REST fallback (`GET /order/v2/list`) before the
engine resumes submitting new orders (reference/04 §5).

### 6.6 Realized-vs-expected fill loop (deferred)

Once `CarinaSink` is live, every fill record carries a `slippage_bps` column
(§5.4). A daily aggregation feeds back into plan 15's ongoing impact model
calibration — the loop that reference/04 opened ("Realized vs. expected fill
measurement — close the loop between simulated fills and live slippage").

---

## 7. Implementation

### Build-now tasks

- [x] **7.1** `strategy/engine/strategy.go` — `Strategy` and `Engine` interfaces
  (§3.1, §5.1); `Clock` interface with replay-timestamp and wall-clock
  implementations; `MarketEvent` wrapper type re-exporting the collector's
  types. *(Done — `engine/strategy.go` (`Strategy`, `Engine`, `MarketEvent`) + `engine/clock.go` (`Clock`))*
- [x] **7.2** `strategy/engine/engine.go` — `Run()` event loop (§5.1); dispatch
  to `Strategy.OnEvent` synchronously; log decision record to the post-trade
  store before dispatch; handle context cancellation and source exhaustion. *(Done — `engine/engine.go`)*
- [x] **7.3** `strategy/engine/sink.go` — `OrderSink` interface + `PaperSink`
  implementation (§5.3) with taker fill model and fee schedule from
  reference/07 (`buy: 0.0015, sell: 0.0025`); tick-snapping via the
  `roundTick()` logic already in `strategy/`; post-trade store write on every
  simulated fill. *(Done — `engine/sink.go`; `feeRateBuy = 0.0015`, `feeRateSell = 0.0025` per reference/07)*
- [x] **7.4** `strategy/engine/store.go` — `PostTradeStore` backed by SQLite
  (§5.4); schema migrations on first open; `LogDecision`, `LogIntent`,
  `LogFill`, `LogAttribution` methods; `hold_calendar_days` computed via the
  plan-14 calendar reader (`settlement_calendar_days`). Append-only: no UPDATE
  or DELETE. Store path configurable at startup. *(Done with two deviations — `engine/store.go`, WAL-mode SQLite, append-only (no UPDATE/DELETE). Methods are named `InsertDecision` / `InsertIntent` / `InsertFill` / `InsertAttribution`, not `Log*`. **`hold_calendar_days` is a schema column but the plan-14 calendar reader is not yet wired** — no `collector/internal/calendar` import in `engine/`; the value is passed in by the caller. Follow-on)*
- [x] **7.5** `strategy/engine/replay_source.go` — `ReplaySource` function
  (§5.2) wrapping `collector/internal/replay.Replay`; both pacing modes; buffer
  size 256 events; context cancellation propagated. *(Done — `engine/replay_source.go`; buffer 256, `ModeFast` / `ModePaced` re-exported)*
- [x] **7.6** ORB strategy implementation `strategy/strategies/orb/orb.go` —
  `Strategy` interface implementation for the ORB signal survivors from plan 10;
  reads opening price from the first `LivePrice` event per symbol; sets a
  breakout threshold; submits order intents via the engine handle; calls
  `OnFill` to update internal position state. *(Done — `strategies/orb/orb.go`; long-only after commit `c62eba8` (short leg removed))*
- [x] **7.7** `strategy/cmd/strategy-run/main.go` — CLI: `--segments
  <glob-or-list>`, `--strategy orb`, `--sink paper`, `--store <path>`,
  `--pacing fast|paced`; wires replay source → engine → paper sink → store;
  emits a run summary on exit (trades attempted, fills, net PnL bps). *(Done — `cmd/strategy-run/main.go`; all five flags present)*
- [x] **7.8** ORB event-driven re-validation run (§5.5) — run `strategy-run`
  against the ORB survivor segments; compare attribution-record summary against
  plan-10's CSV-based output; record whether the results agree or diverge in
  plan-10's status document. *(Done — run executed and recorded in plan-10 §"Event-driven re-validation status". **Result: DIVERGES.** First pass (30-min timer exit + phantom post-close re-entry) discarded as invalid; corrected run (EOD exit at 08:49 UTC, no-re-entry guard, Jun 22–26, 7 symbols, 21 round-trips) gives mean net −112.3 bps taker / −76.2 bps mid, 3 of 7 symbols gross-positive. Plan-10 §8 red flag NOT closed. Caveats: N=3 round-trips per symbol, 5-day stub universe, live-data confirmation blocked on SB_BEARER_TOKEN. **Re-run on the full 53-session capture before drawing any conclusion**)*
- [x] **7.9** Unit tests — engine dispatch order (OnEvent before OnFill, no
  out-of-order dispatch); PaperSink fee math matches the reference/07 worked
  example; post-trade store round-trip (write then read gives identical records);
  replay-reproducibility (two runs over the same segments produce byte-identical
  stores). *(Done — `TestEventFiresBeforeFill`, `TestPaperSinkFeeMath`, `TestStoreRoundTrip`, `TestReplayReproducibility`, `TestReplayReproducibilityWithTrades`, `TestStoreWALMode`, `TestStoreAppendOnly` (commit `0ed479b`))*
- [x] **7.10** `strategy/engine/` Go tests — mock strategy and sink; verify
  that `Run()` exhausts the source and closes cleanly; verify context
  cancellation unblocks the loop within one event. *(Done — `TestRunExhaustsSourceCleanly`, `TestContextCancellation`)*

### Deferred tasks (design sketch only; not tickable until §2 gate passes)

- [ ] **7.11** (Deferred — §2 gate) `strategy/engine/live_source.go` — live WS
  event source wrapping the `strategy/` module WS client + portfolio socket
  (reference/04 §1); dual-goroutine (market-data + portfolio sockets).
- [ ] **7.12** (Deferred — §2 gate) `strategy/engine/carina_sink.go` —
  `CarinaSink` implementing `OrderSink` against the reference/03 API; `ui_ref`
  idempotency; T+2 guard; trading-token auth (plan 03).
- [ ] **7.13** (Deferred — §2 gate + reference/06) Pre-trade risk checks inside
  `CarinaSink.Submit()` — holiday (plan-14 calendar), session phase, ARA/ARB,
  tick validation, suspension — in the order listed in reference/06 §1.
- [ ] **7.14** (Deferred — §2 gate + reference/04) Order lifecycle in
  `CarinaSink` — partial fills, cancels, rejects, reconnect reconciliation via
  the REST fallback.
- [ ] **7.15** (Deferred — §2 gate + plan 15) Calibrated fill model in
  `PaperSink` — swap the taker placeholder for plan-15's calibrated impact
  parameters loaded from a config file.
- [ ] **7.16** (Deferred — §2 gate) Realized-vs-expected fill aggregation —
  daily slippage summary from attribution records fed back to plan 15.

---

## 8. Verification

**Engine correctness**

- **Event order invariant** — `OnEvent` is always called before `OnFill` for
  any given strategy; the fill arrives only after the intent that generated it;
  no event is delivered out of `RecvNs` order within a single segment.
- **Fee math round-trip** — a hand-constructed trade (entry at 8750, 100 lots,
  exit at 8800) produces fees matching the reference/07 §5 worked example to
  the rupiah in `PaperSink`.
- **Tick validation** — a price that falls between valid ticks is snapped to the
  nearest valid tick before submission; the intent record carries the snapped
  price.

**Post-trade store**

- **Round-trip fidelity** — a decision + intent + fill + attribution written by
  the engine is readable by a fresh store handle with no lossy transformation.
- **Replay reproducibility** — two `strategy-run` invocations over the same
  segments and strategy produce stores whose attribution records are
  field-for-field identical (same `seq`, same `recv_ns`, same PnL figures).
- **Plan-11 compatibility** — the attribution records are readable by plan 11's
  decay-metric runner using the same schema (the schema is specified here; plan
  11 imports it unchanged).
- **Append-only enforcement** — the SQLite store opens in WAL mode; any UPDATE
  or DELETE statement panics in tests; the store file grows monotonically across
  runs that resume from a checkpoint.

**ORB re-validation**

- **CSV vs. event-driven agreement** — the net PnL bps and hit-rate from the
  event-driven run agree with the plan-10 CSV-based result within the
  confidence interval of the static harness. A divergence of more than ±5 bps
  in mean net PnL or ±0.05 in hit-rate is treated as a red flag requiring
  investigation before the ORB edge is considered confirmed.
- **Gap handling** — the engine correctly resets book state on `RECONNECT` and
  `SWITCH_RESET` control events (plan-02 §6.3); no trade is opened in the
  window immediately after a gap.
- **No look-ahead** — the clock seen by the strategy during replay never exceeds
  the `recv_ns` of the current event; future events are not visible.

**Pre-live (deferred, checked before §2 gate clears)**

- **Pre-trade checklist fires** — a synthetic order intent on a holiday, outside
  session hours, or at a price outside ARA/ARB is rejected by `CarinaSink`
  before any HTTP call is made.
- **`ui_ref` idempotency** — submitting the same order intent twice (same
  `ui_ref`) results in exactly one HTTP call; the second is suppressed.
- **T+2 guard** — an intent whose intended hold crosses T+2 is rejected at
  submission time with a structured error logged to the post-trade store.

---

## 9. Out of scope

- **Live order routing** — explicitly deferred behind the §2 gate; no Carina
  HTTP calls are made by anything built in this plan.
- **Multi-strategy portfolio execution** — the engine runs one registered
  strategy per process. Portfolio-level orchestration (multiple strategies
  sharing capital, cross-strategy risk limits) is a future concern; the
  `OrderSink` interface is intentionally the right seam for it.
- **Smart execution logic** — limit/market decision, passive/cross choice,
  slicing — deferred until plan 15 delivers calibrated fill parameters.
- **Position sizing** — the ORB strategy implementation (§7.6) uses a fixed
  lot size for the re-validation run. Volatility-scaled sizing (pillar 3) is
  plan 13's domain and is not wired here.
- **Monitoring and alerting** — roadmap pillar 4.2 "monitoring within seconds";
  deferred until live trading is live.
- **Reconciliation** — our view of positions vs. the broker's, daily; deferred.
- **Parquet export of the post-trade store** — the SQLite store is the
  authoritative record; Parquet is a possible future analytics layer, not
  specified here.
- **The `go.work` workspace or module structure** — plan 07 owns the layout;
  this plan adds packages inside the existing `strategy/` module without
  altering module boundaries.

---

Prev: [16-strategy-triage-and-prioritization.md ←](./16-strategy-triage-and-prioritization.md) ·
Index: [README](./README.md)
