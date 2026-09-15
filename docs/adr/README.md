# Architecture Decision Records

This directory records significant architecture and process decisions for the
algo-trading project, using a lightweight [ADR](https://adr.github.io/) format.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](./0001-treat-plans-as-tickets.md) | Treat planning docs as tickets | Accepted |
| [0002](./0002-segment-storage-and-compression.md) | Segment storage format & compression lifecycle | Accepted |
| [0003](./0003-market-hours-instance-scheduling.md) | Weekday market-hours instance scheduling | Accepted |
| [0004](./0004-hourly-segment-roll-cadence.md) | Hourly segment roll cadence | Accepted |
| [0005](./0005-s3-versioning-suspended.md) | Suspend S3 bucket versioning to cap storage cost | Accepted |
| [0006](./0006-watchlist-from-ssm.md) | Source watchlist and watchlist_id from SSM at boot | Accepted |
| [0007](./0007-iepiev-capture-and-preopening-trades.md) | Capture IEP/IEV and exclude pre-opening trades from flow features | Accepted |
| [0008](./0008-top-level-layout-mirrors-pillars.md) | Top-level layout mirrors trading-system pillars | Accepted |
| [0009](./0009-universe-is-funding-intersection-strategy-fit-downstream.md) | Universe is the funding intersection; strategy fit decided downstream | Accepted |
| [0010](./0010-capture-watchlist-is-topn-diverse-slice-via-tfvars.md) | Capture watchlist is a top-N broad/diverse slice, delivered via tfvars | Accepted |
| [0011](./0011-capital-model-300m-leverage-sleeve.md) | Capital model — 300M IDR T+2 leverage sleeve run as a periodic batch allocator | Superseded by [0015](./0015-leverage-optional-horizon-ceiling-lifted.md) |
| [0012](./0012-market-calendar-single-source-cross-language.md) | Market calendar is one curated source of truth, read by thin per-language clients | Proposed |
| [0013](./0013-one-strategy-interface-and-collector-public-packages.md) | One Strategy interface for live and replay; collector packages promoted to public API | Accepted |
| [0014](./0014-financing-accrues-between-settlement-dates.md) | Financing accrues between settlement dates, not from the trade date | Accepted |
| [0015](./0015-leverage-optional-horizon-ceiling-lifted.md) | Leverage is optional; the ~2-trading-day horizon ceiling is lifted for owned-cash strategies | Accepted · amended by [0017](./0017-sleeve-is-a-funded-sub-account.md) |
| [0016](./0016-alpha-and-infrastructure-are-separate-tracks.md) | Alpha discovery and trading infrastructure are separate tracks; live routing is gated on size, not on edge | Accepted |
| [0017](./0017-sleeve-is-a-funded-sub-account.md) | The sleeve is a separate funded broker sub-account; its capital is measured, not assumed | Accepted |

## When to write an ADR

Record a decision here when **any** of these holds:

- **One-way door** — reversing it later means a data migration, a storage-format
  break, or a re-architecture (e.g. on-disk layout, partition scheme, auth model).
- **A live alternative was rejected** — you genuinely weighed B or C and picked A
  (e.g. hourly roll vs end-of-day, EventBridge Scheduler vs cron-on-box).
- **The rationale is non-obvious** — a future reader will ask "why is it like this?"
  (e.g. write-raw-then-compress, the 08:30 WIB start margin).
- **It pins an assumption that may move** — IDX market hours, the active watchlist,
  or the acceptable crash-loss window.

Skip it for reversible implementation detail, naming, or anything self-evident from
the code. Capture the ADR in the **same change** that commits the choice: open it as
`Proposed`, flip to `Accepted` on merge.

## Conventions

- **Filename:** `NNNN-kebab-title.md`, zero-padded sequential (`0001`, `0002`, …).
- **Sections:** Status, Date, Context, Decision, Consequences.
- **Status values:** `Proposed` → `Accepted`; later `Deprecated` or `Superseded`.
- **Never rewrite history:** to reverse a decision, add a new ADR and mark the old
  one `Superseded by [NNNN]` with a link, rather than editing or deleting it.
