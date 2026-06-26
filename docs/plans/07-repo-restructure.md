# Repo Restructure — Top-Level Layout Mirrors Trading-System Pillars

> **Status:** ✅ Complete · **Owner:** @lewis · **Updated:** 2026-06-19
> **Related:** [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) · [ADR-0008](../adr/0008-top-level-layout-mirrors-pillars.md) · [Plans index](./README.md)

> ✅ **Result: Executed.** The top-level layout now mirrors the five pillars from the
> roadmap. `collector/` is the data layer (Pillar 4), `research/` is the alpha factory
> (Pillar 1), and `strategy/` is the execution seed (Pillar 2). The collector Go module
> is renamed from `sb-algo-prototype` to `collector`, and a repo-root `go.work` ties
> the two Go modules together.

## 1. Problem

Everything lived under `collector/` before this restructure:

- The data-capture binary and its shared Go packages — which belong to Pillar 4
  (infrastructure and operations) — were mixed with the trading-bot prototype, which
  belongs to Pillar 2 (execution engine).
- The Python research scripts (microstructure studies, fill simulator, backtest) —
  which belong to Pillar 1 (alpha factory) — also lived under `collector/study/`.
- The Go module was named `sb-algo-prototype`, a vestige of the earliest sketch.
  Every internal import path (`sb-algo-prototype/internal/...`) advertised the wrong
  identity and made it harder to add `strategy/` as a peer module without confusion.
- With a single flat layout and a single Go module, there was no clean boundary between
  what the data layer owns, what research owns, and what execution will own.

## 2. Pillar → folder mapping

| Top-level folder | Roadmap pillar | What it contains |
|-----------------|----------------|-----------------|
| `collector/` | **Pillar 4** — Infra & ops (data layer) | Go module `collector`. Capture binary (`cmd/collector`), segment inspector/exporter (`cmd/sbcap`), shared packages (`internal/{feed,replay,segment,s3upload,sbproto,zstdio}`), and Python depth charts (`viz/`). Read-only — captures orderbook, liveprice, running trades. Places no orders. |
| `research/` | **Pillar 1** — Alpha factory | Python: microstructure studies (`study/`), fill simulator and backtester (`backtest/`), shared utilities (`lib/`), `requirements.txt`, `pyproject.toml`. |
| `strategy/` | **Pillar 2** — Execution (seed) | Go module `strategy`. The former zero-dependency trading-bot prototype: auth, WebSocket client, hand-rolled Protobuf codec, order-book imbalance signal, Carina order client. Dry-run by default; can place real orders when armed. |

Pillars 3 (portfolio & risk) and 5 (capital & governance) have no code yet and no
top-level folder; they will when they do.

## 3. What moved where

| Before | After |
|--------|-------|
| `collector/study/*.py` | `research/study/` |
| `collector/study/backtest.py`, `test_backtest.py` | `research/backtest/` |
| `collector/{auth,websocket,protobuf,datafeed,strategy,broker,main,config}.go` | `strategy/` |
| `collector/README.md` (prototype docs) | `strategy/README.md` |
| `collector/internal/{feed,replay,...}` | `collector/internal/{feed,replay,...}` (unchanged path, renamed module) |

`collector/viz/` stays under `collector/` — it renders depth charts from segment
files, so it belongs with the data layer, not with alpha-factory research.

## 4. Module rename and Go workspace

The collector Go module was renamed from `sb-algo-prototype` to `collector`.
All internal import paths updated accordingly (`collector/internal/feed`, etc.).

A `go.work` file at the repo root ties the two modules together so that tooling
operates across both without `replace` directives:

```
go 1.21

use ./collector
use ./strategy
```

Each module retains its own `go.mod` and dependency graph. The Makefile is
unchanged — it builds `./cmd/collector` and `./cmd/sbcap` via relative paths
under the `collector/` module, which remain correct after the rename.

## 5. Path remapping for earlier plans

Plans 01–06 were written before this restructure and reference the old paths.
Their bodies are left intact (see [ADR-0001](../adr/0001-treat-plans-as-tickets.md):
never rewrite history). The table below maps old references to their new locations:

| Old path (plans 01–06) | New location |
|------------------------|-------------|
| `collector/study/*.py` | `research/study/` |
| `collector/study/backtest.py` | `research/backtest/backtest.py` |
| `collector/study/test_backtest.py` | `research/backtest/test_backtest.py` |
| `collector/study/audit_join.py` | `research/study/audit_join.py` |
| `collector/{auth,websocket,protobuf,datafeed,strategy,broker,main,config}.go` | `strategy/` |
| `module sb-algo-prototype` | `module collector` |
| `collector/README.md` (prototype docs) | `strategy/README.md` |

The capture binary, segment inspector, and all shared Go packages remain in
`collector/` at the same relative paths within that module.

## 6. Deferred follow-up

The shared Go packages in `collector/internal/` use the `internal/` visibility
restriction by convention. Promoting any of them to a top-level `pkg/` (making
them importable by `strategy/` or a future risk module) is deliberately deferred
until Pillar 2 is being actively built — premature promotion before the consumer
exists would just be churn. Until then, `strategy/` carries its own hand-rolled
codec and WS client, which is intentional (it is zero-dependency by design).

## 7. Checklist

- [x] `research/` directory created with `study/`, `backtest/`, `lib/`
- [x] `strategy/` directory created with its own `go.mod` (`module strategy`)
- [x] `collector/go.mod` module name changed from `sb-algo-prototype` to `collector`
- [x] All internal import paths in `collector/` updated (`collector/internal/...`)
- [x] `go.work` created at repo root (`use ./collector`, `use ./strategy`)
- [x] `collector/README.md` created (data layer only)
- [x] Root `README.md` Layout table updated
- [x] `strategy/README.md` carries the moved prototype docs
- [x] ADR-0008 written

---

Back to [Plans index](./README.md)
