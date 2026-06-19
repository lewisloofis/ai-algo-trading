# ADR 0008 — Top-level layout mirrors trading-system pillars

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** repo-structure, go-modules, workspace

> Backfilled to record decisions already executed in the 2026-06-19 restructure;
> see [plan 07](../plans/07-repo-restructure.md) for the full change log.

## Context

After completing the first full research loop (plans 04–06, NO-GO verdict), the
repository had a structural problem: all Go code and all Python code lived under a
single `collector/` directory, regardless of which pillar it served.

- **Pillar 4 (data layer):** the capture binary (`cmd/collector`), the segment
  inspector (`cmd/sbcap`), and the shared Go packages (`internal/`) — all correctly
  in `collector/`.
- **Pillar 1 (alpha factory):** the Python microstructure studies and fill simulator
  — also in `collector/study/`, with no directory-level distinction from the
  infrastructure code.
- **Pillar 2 (execution, seed):** the zero-dependency Go trading-bot prototype
  (`auth.go`, `broker.go`, `strategy.go`, …) — at the root of the `collector/`
  module, mixed with the capture infrastructure.

The Go module was named `sb-algo-prototype` — a name that described the earliest
sketch, not the current purpose of the module, and one that would collide
conceptually once a real `strategy/` module was added as a peer.

As the system grows toward pillars 2, 3, and 5, the lack of top-level separation
creates two concrete risks: (1) it is unclear where new code belongs, and (2) Go's
`internal/` visibility rules assume module boundaries that match the intended
coupling — which only works if each pillar is its own module or at least its own
top-level tree.

## Decision

Structure the top-level directories to match the pillar map in
[00-roadmap-research-to-live.md](../plans/00-roadmap-research-to-live.md):

| Directory | Pillar | Module |
|-----------|--------|--------|
| `collector/` | Pillar 4 — Infra & ops (data layer) | `module collector` |
| `research/` | Pillar 1 — Alpha factory | Python only (no `go.mod`) |
| `strategy/` | Pillar 2 — Execution (seed) | `module strategy` |

Rename the `collector/` Go module from `sb-algo-prototype` to `collector` and
update all internal import paths. Move the trading-bot prototype source files
(`auth.go`, `broker.go`, `config.go`, `datafeed.go`, `main.go`, `protobuf.go`,
`strategy.go`, `websocket.go`, `prototype_test.go`) and their README to
`strategy/`, giving `strategy/` its own `go.mod` (`module strategy`, Go 1.21).

Add a `go.work` file at the repo root that ties both modules:

```
go 1.21

use ./collector
use ./strategy
```

This allows `go build ./...` and `go test ./...` to operate across both modules
from the repo root without `replace` directives in either `go.mod`.

## Rejected alternatives

**Single repo-root module.** One `go.mod` at the root covering all Go code would
eliminate the workspace complexity. Rejected because:

- The `internal/` packages under `collector/internal/` are data-layer
  infrastructure; making them importable by the entire repo (which a single root
  module implies) removes the coupling boundary before it has been deliberately
  re-examined.
- Moving the module root to the repo root would require touching every import path
  in `collector/` plus every Makefile target and CI step that assumes `go` is
  invoked from inside `collector/`.
- As pillar 3 (portfolio & risk) and additional pillar 2 components are added, a
  single root module would accumulate unrelated dependencies in one `go.sum`,
  making dependency auditing harder.

**Monorepo with no Go workspace (`replace` directives).** Each module could
reference the other via `replace` in `go.mod`. Rejected because `go.work` is the
idiomatic multi-module workspace solution since Go 1.18 and does not pollute each
module's `go.mod` with local-path overrides that must be stripped before publishing.

## Consequences

**Positive**

- A new contributor can find code by asking "which pillar?" — an answer directly
  readable from the directory listing.
- The `internal/` boundary is preserved correctly: `strategy/` cannot accidentally
  import `collector/internal/…` without an explicit decision to promote those
  packages to `collector/pkg/`.
- The module name `collector` matches what the module actually does; future
  Go tooling output (error messages, build traces) will reference the right name.
- `go.work` lets both modules be developed together without `replace` hacks; the
  workspace file is gitignored from neither module's `go.mod`.

**Negative / costs**

- Two `go.mod` files and a `go.work` means tooling commands that don't understand
  workspaces (older `go` versions < 1.18) will fail. The declared minimum is
  Go 1.21 in both modules, so this is not a practical concern.
- Plans 01–06 reference old paths (`collector/study/*.py`, module name
  `sb-algo-prototype`). Per [ADR-0001](./0001-treat-plans-as-tickets.md) those
  ticket bodies are left intact; [plan 07](../plans/07-repo-restructure.md)
  provides a path-remapping table for future reference.
- `strategy/` currently carries a hand-rolled Protobuf codec and WebSocket client
  that duplicate logic present in `collector/internal/sbproto` and `internal/feed`.
  This duplication is acceptable (the prototype was zero-dependency by design) and
  will be resolved only when Pillar 2 is actively built — at which point a
  deliberate decision about promoting `collector/internal/` packages to `pkg/` will
  be made.

## Related

- [Plan 07 — repo restructure](../plans/07-repo-restructure.md) — full change log,
  path-remapping table, and checklist.
- [ADR-0001 — treat plans as tickets](./0001-treat-plans-as-tickets.md) — rationale
  for leaving earlier plan bodies intact when paths change.
- [00-roadmap-research-to-live.md](../plans/00-roadmap-research-to-live.md) — the
  five-pillar map that the directory structure now mirrors.
