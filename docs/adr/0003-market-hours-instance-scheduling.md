# ADR 0003 — Weekday market-hours instance scheduling

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** infra, cost, operations

> Backfilled to record a decision already implemented in `infra/scheduler.tf`
> (variables `market_open_schedule`, `market_close_schedule`, `schedule_timezone`).

## Context

The collector only has useful work to do while IDX is trading (pre-opening ~08:45,
post-trading close ~16:15 WIB), Monday–Friday. Running the `t4g.nano` collector
24/7 burns ~3× the compute hours for zero captured data outside the session and
nothing at all on weekends.

We need the instance **up and connected before pre-opening** and **stopped shortly
after close** — but the stop must be graceful, because the final segment is only
sealed, compressed, and uploaded on `SIGTERM` (see
[ADR-0002](./0002-segment-storage-and-compression.md)). A hard power-off would lose
the last open segment.

Candidate mechanisms:

- **cron on the box** — rejected: the instance must already be running for its own
  cron to fire, so it cannot *start* a stopped instance, which defeats the cost goal.
- **EventBridge Rule + Lambda** — works, but adds a Lambda to own and monitor.
- **EventBridge Scheduler universal target** — can call `ec2:StartInstances` /
  `ec2:StopInstances` directly, supports a timezone field, and needs no compute of
  its own.

## Decision

Drive the instance lifecycle with **two EventBridge Scheduler schedules** (universal
targets, IAM-scoped to start/stop this instance), expressed in **Asia/Jakarta** so
the cron reads as local market time:

| Schedule | Local (WIB) | Cron | Rationale |
|----------|-------------|------|-----------|
| Start | **08:30**, Mon–Fri | `cron(30 8 ? * MON-FRI *)` | 15 min before pre-opening (08:45) for boot + auth + WS-connect margin |
| Stop | **16:25**, Mon–Fri | `cron(25 16 ? * MON-FRI *)` | ~10 min after post-trading close (16:15) so the final segment flushes on `SIGTERM` |

Weekends: neither schedule fires, so the instance stays stopped. The stop issues a
graceful shutdown, which triggers the collector's final flush/compress/upload path.

## Consequences

**Positive**

- Compute billed only for the ~8 h weekday session — roughly a 3× reduction vs 24/7,
  and nothing on weekends.
- No self-hosted scheduler or Lambda to maintain; the schedules are declarative
  Terraform and the timezone field absorbs WIB/UTC conversion and DST (none for WIB).
- Start margin guarantees the feed is connected before pre-opening; stop margin
  guarantees the last segment is sealed and shipped.

**Negative / costs**

- The window is pinned to current IDX hours; a market-hours change requires editing
  the cron variables (the assumption is recorded here so the dependency is explicit).
- A start failure (e.g. capacity, auth) silently loses that day's capture unless
  separately alarmed — scheduling does not imply health monitoring.
- Margins are fixed minutes, not health-gated; an unusually slow boot/auth could in
  principle clip the open or a slow final upload could be cut by the stop.

## Related

- [ADR-0002 — segment storage & compression lifecycle](./0002-segment-storage-and-compression.md) — the graceful-stop margin exists to let its final seal complete.
- `infra/scheduler.tf`, `infra/variables.tf` (implementation).
