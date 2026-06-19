# ADR 0002 — Segment storage format & compression lifecycle

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** storage, capture, durability

> Backfilled to record a decision already implemented in
> `collector/cmd/collector/segmentfile.go` and specified in
> [plan 02 — storage format and replay](../plans/02-storage-format-and-replay.md).

## Context

The collector streams raw market-data frames (order book, live price, running-trade
batches, IEP/IEV) to disk for later replay and study. We need a capture format that:

- appends cheaply on the hot path without blocking the websocket reader;
- survives an unclean crash with replayable data, not a corrupt tail;
- is cheap to store and ship to S3 (the feed is bursty but mostly small deltas);
- carries enough self-description (symbols, channels, time span, record counts) to
  be interpreted standalone, without consulting the collector that wrote it.

zstd compresses this data extremely well in practice (observed 6.6× on a quiet
half-hour segment, 13.9× on a full trading day), but a finalized zstd frame cannot
be appended to record-by-record, so compressing inline on every frame is not viable.

## Decision

Write segments **raw while open, compress once at close.**

1. **On-disk format.** The open segment is an uncompressed `.binpb` of
   length-delimited protobuf records (data records + control records) written
   through `segment.Writer` over a 256 KiB buffered writer.
2. **Partition layout.** `<captureDir>/dt=<YYYY-MM-DD>/<watchlistID>/seg-<HHMMSS>.binpb`,
   where the time encodes the UTC open time. The `dt=` partition is replay- and
   query-friendly (Hive-style).
3. **Seal on roll/shutdown.** `closeAndCompress` flushes, closes, compresses the
   `.binpb` to `.binpb.zst` (zstd), **deletes the uncompressed `.binpb`** once
   compression succeeds, writes a `*.manifest.json` sidecar, then uploads the `.zst`
   + manifest to S3. Compression and upload run in a background goroutine so the
   capture hot path is never blocked.
4. **Manifest sidecar.** Each sealed segment is described by a JSON manifest
   (schema version, collector version, watchlist id, symbols, channels, start/end
   UTC, data/control record counts, compressed & uncompressed sizes).
5. **Upload failure is non-fatal.** If the S3 upload fails, the local `.zst` and
   manifest are left in place and logged rather than discarded (plan 01 §9.6); they
   can be re-shipped or recovered later.
6. **Crash recovery.** Because the open segment is plain `.binpb`, a process that
   dies mid-segment leaves a replayable file; orphan `.binpb` files are detected and
   sealed on next boot (`recover.go`). The only file ever left uncompressed in steady
   state is the single currently-open segment.

## Consequences

**Positive**

- Hot path does a cheap buffered append; compression cost is amortised to roll time
  and kept off the capture goroutine.
- Crash exposure is bounded to the open segment, and that segment is still readable.
- zstd keeps storage and transfer cost low; the manifest makes every object
  self-describing for downstream `sbcap`/study tooling.

**Negative / costs**

- Two on-disk representations exist transiently (raw + `.zst`) around a roll, and a
  failed compress/upload leaves local files that need housekeeping.
- The "delete raw after compress" step assumes the compress succeeded atomically; a
  crash *between* compress and delete leaves both, handled as an orphan next boot.

## Related

- [Plan 02 — storage format and replay](../plans/02-storage-format-and-replay.md) (spec)
- [ADR-0004 — hourly segment roll cadence] *(pending)* governs **when** a seal fires.
