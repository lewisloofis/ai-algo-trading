# ADR 0004 — Hourly segment roll cadence

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** storage, durability, operations

> Backfilled to record a decision already implemented in
> `collector/cmd/collector/main.go` (`rollInterval`, `nextRollBoundary`,
> `rollLoop`).

## Context

The collector holds an open, uncompressed `.binpb` segment for the duration of
a capture run. While that segment is open, an unclean crash (OOM kill, hard
reboot, power loss) leaves the data in an uncompressed file that `recover.go`
can salvage on next boot — but only up to the last flushed byte. Any frames
still in the 256 KiB write buffer are lost.

The original design rolled the segment only at process shutdown (SIGTERM). That
is fine for a graceful stop, but it means a crash during a full trading session
(~7.5 h WIB, 00:30–09:00 UTC) puts the entire day's capture at risk. We need
to bound the blast radius.

Three roll strategies were considered:

1. **End-of-day / midnight-only** — roll once per calendar day.
2. **Size-based** — roll when the file exceeds a threshold (e.g. 50 MiB).
3. **Fixed wall-clock interval** — roll at every *n*-hour UTC boundary.

## Decision

Roll the active segment on a **fixed 1-hour UTC boundary** (`rollInterval =
time.Hour`).

`nextRollBoundary` computes the target time as:

```go
now.UTC().Truncate(interval).Add(interval)
```

This always aligns to the top of the next UTC hour. `rollLoop` sleeps until
that boundary, fires `sf.roll`, then recomputes for the following hour — no
drift accumulates. The 00:00 UTC boundary (which falls during the IDX
pre-opening period, ~07:00 WIB) also advances the `dt=<date>` partition,
subsuming the earlier midnight-only roll.

The seal/compress/upload mechanics triggered by a roll are defined by ADR-0002;
the daily operating window within which rolls fire is defined by ADR-0003.

## Consequences

**Positive**

- Worst-case data loss on an unclean crash is bounded to ~1 hour of capture
  rather than the full session.
- Seal times are predictable and UTC-aligned, making segment filenames and
  `dt=` partitions easy to reason about and query.
- The 00:00 UTC boundary naturally advances the date partition with no separate
  midnight-roll logic; one mechanism handles both intra-day durability and daily
  partitioning.
- ~9 sealed segments per weekday session give fine-grained replay granularity
  and keep individual object sizes manageable in S3.

**Negative / costs**

- Each roll triggers a compress + S3 upload in a background goroutine; with
  zstd and the observed data volumes this is negligible, but ~9 uploads per day
  replaces the previous 1.
- A crash within the first minutes of a new segment still loses up to one full
  hour — the bound is tighter than end-of-day but not zero.
- If `rollInterval` is ever changed (e.g. to 30 min), the `~9 segments/day`
  figure and the downstream partition assumptions should be re-evaluated.

## Related

- [ADR-0002 — segment storage & compression lifecycle](./0002-segment-storage-and-compression.md) —
  defines the seal/compress/upload path that executes at each roll boundary.
- [ADR-0003 — weekday market-hours instance scheduling](./0003-market-hours-instance-scheduling.md) —
  defines the daily operating window within which rolls fire.
