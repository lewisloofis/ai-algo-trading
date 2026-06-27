# ADR 0013 — One Strategy interface for live and replay; collector packages promoted to public API

- **Status:** Accepted
- **Date:** 2026-06-27
- **Deciders:** Lewis
- **Tags:** execution, go-modules, replay-parity, architecture

> Records two coupled decisions made while building [plan 17](../plans/17-execution-and-replay-parity-engine.md):
> the single `Strategy`/`Engine` interface that backtest and live share, and the
> promotion of five `collector/internal/` packages to the public `collector/` API
> so the `strategy/` module can reuse them. Plan 17 §3.1 explicitly called for this ADR.

## Context

Plan 17 builds the event-driven runtime: the engine that wires the plan-02
replayer into a strategy loop, producing both (a) an event-driven backtester
usable now and (b) the append-only post-trade store plan 11 is blocked on. Two
architectural questions had to be settled before any of that code could compile.

**1. How do backtest and live execution share code?** The whole point of plan 02
(see its §1) is live/replay parity: the same strategy logic must run unchanged
whether events arrive from a captured segment file or a live WebSocket feed, and
whether orders go to a paper simulator or to Carina. Without a single seam, the
backtester and the live system drift into two implementations, and a backtest
stops being evidence about live behaviour.

**2. How does `strategy/` reuse the replayer and codec?** The engine must call
the plan-02 `replay.Replay` and `feed.DecodeWrap`, plus the `segment` record
types. Those live in `collector/internal/{feed,replay,segment}` (with transitive
deps on `sbproto` and `zstdio`). Go forbids importing another module's
`internal/` packages, and — contrary to a common assumption — the repo-root
`go.work` does **not** lift that rule: `internal/` visibility is scoped to the
defining module, not the workspace. So `strategy/` literally could not compile
against `collector/internal/…`.

[ADR-0008](./0008-top-level-layout-mirrors-pillars.md) anticipated this exact
moment, deferring the call: "a deliberate decision about promoting
`collector/internal/` packages to `pkg/` will be made … when Pillar 2 is actively
built." Plan 17 is that point. [Plan 07](../plans/07-repo-restructure.md) §Risks
recorded the same deferral.

## Decision

### 1. One `Strategy` interface, identical for live and replay

The engine exposes a single strategy contract. A `Strategy` reacts to events and
fills; it acts on the world only through an injected `Engine` handle, never by
calling a sink, a clock, or `time.Now()` directly.

```go
type Strategy interface {
    OnEvent(ctx context.Context, e MarketEvent, engine Engine)
    OnFill(ctx context.Context, f FillRecord)
}

type Engine interface {
    SubmitOrder(ctx context.Context, intent OrderIntent) (OrderID, error)
    Now() time.Time
}
```

The runtime varies only in two injected dependencies:

- **Event source** — the plan-02 replayer (`ReplaySource`) in backtest, a live WS
  feed later; both produce the same `chan MarketEvent`.
- **Order sink** — `PaperSink` now (simulated fills), `CarinaSink` after the
  plan-17 §2 confirmed-edge gate; both satisfy the `OrderSink` interface.

The strategy never knows which source or sink is wired. Swapping replay for live,
or paper for Carina, is a startup choice, not a code change. The clock the
strategy sees comes from `Engine.Now()` — in replay it is the current event's
`RecvNs`, which is what makes a replay run deterministic and reproducible.

### 2. Promote five collector packages from `internal/` to public

Move, preserving git history:

| Was | Now |
|-----|-----|
| `collector/internal/feed` | `collector/feed` |
| `collector/internal/replay` | `collector/replay` |
| `collector/internal/segment` | `collector/segment` |
| `collector/internal/sbproto` | `collector/sbproto` |
| `collector/internal/zstdio` | `collector/zstdio` |

`feed`/`replay`/`segment` are the engine's direct dependencies; `sbproto` and
`zstdio` are their transitive dependencies and must move with them. `calendar`
and `s3upload` stay under `collector/internal/` — they are not part of the
execution seam and nothing in `strategy/` needs them.

The `strategy/` module declares the dependency explicitly:

```
require collector v0.0.0
replace collector => ../collector
```

The `replace` is **not** redundant with `go.work`. The workspace file makes
root-level builds (`go build ./...` from the repo root) resolve `collector`; the
`replace` directive is what makes module-mode builds work — `cd strategy && go
build`, or any CI step or tool that runs with `GOWORK=off`. Both linkages are
kept: `go.work` for the developer workspace, `replace` for the durable
module-level edge. `go.work` and `go.work.sum` are committed (they were
previously untracked).

### Deviation from ADR-0008

ADR-0008 said packages would be promoted to `collector/pkg/`. We promote to flat
`collector/<pkg>` instead. The `pkg/` convention adds a directory level without
buying anything here: `collector` is an application module, not a published
library, and the flat layout keeps import paths short (`collector/replay` rather
than `collector/pkg/replay`). This supersedes the `pkg/` wording in ADR-0008.

## Rejected alternatives

**Thin public shim that re-exports from `internal/`.** Keep the packages under
`internal/` and add a public `collector/replay` that re-exports only the types
the engine needs. Rejected: it leaves a permanent duplicate-surface layer on top
of the re-export the engine already performs (engine wraps `replay.MarketEvent`
into its own `MarketEvent`), and it pretends the packages are still private when
they are in fact a cross-module API. Promotion is the honest, one-time change.

**Single repo-root Go module.** Collapsing both modules removes the import
problem entirely but was already rejected in ADR-0008 (loss of the dependency
boundary, churn across every import path and Makefile target). Nothing here
changes that reasoning.

**Separate sink/source interfaces for backtest vs live.** Two parallel strategy
contracts — one for replay, one for live — would let each be optimised
independently. Rejected: it directly defeats the plan-02 parity thesis. Divergent
contracts mean a passing backtest is no longer evidence about live behaviour,
which is the entire reason the backtester exists.

## Consequences

**Positive**

- A strategy is written once and runs in backtest, paper, and (post-gate) live
  with no code change — the parity property plan 02 set out to guarantee.
- `strategy/` compiles against the real replayer and codec; no duplicated
  transport or codec logic is introduced for the engine (the prototype's
  hand-rolled WS/protobuf codec in `strategy/` remains, to be retired separately).
- The post-trade store is fed by the same `SubmitOrder` path live and replay, so
  attribution records are format-identical across modes — exactly what plan 11's
  decay runner needs.
- The `collector` ↔ `strategy` dependency is now explicit and auditable in
  `strategy/go.mod`, not implicit via a workspace-only side effect.

**Negative / costs**

- The promoted packages are now public API of the `collector` module: changing
  their signatures is a cross-module break, not a private refactor. This is the
  intended trade — the coupling is real and is now named.
- Promotion rewrote the import path in ~25 `collector` files (cmd/* and tests)
  plus the `strategy/engine` files. Done mechanically; `collector` and `strategy`
  both build, vet, and test green after the change.
- `collector/internal/` now holds only `calendar` and `s3upload`, a smaller
  boundary than before. That is correct — only those two remain genuinely private.

## Related

- [Plan 17 — execution & replay-parity engine](../plans/17-execution-and-replay-parity-engine.md) — §3.1 requested this ADR; §3.2–§3.4 specify the sink and reuse constraints.
- [ADR-0008 — top-level layout mirrors pillars](./0008-top-level-layout-mirrors-pillars.md) — deferred the promotion decision this ADR now makes; superseded on the `pkg/` path detail.
- [Plan 02 — storage format and replay](../plans/02-storage-format-and-replay.md) — the live/replay parity thesis the one-interface decision serves.
- [Plan 07 — repo restructure](../plans/07-repo-restructure.md) — recorded the same deferral of `internal/` promotion.
- [Plan 11 — decay monitoring](../plans/11-decay-monitoring-and-live-revalidation.md) — consumer of the post-trade store the shared interface feeds.
