# ADR 0007 — Capture IEP/IEV and exclude pre-opening trades from flow features

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** capture, study, microstructure

> Backfilled to record decisions already implemented in
> `collector/cmd/collector/main.go` (`activeChannels` / `activeChannelNames`),
> `collector/cmd/sbcap/trades.go` (pre-opening skip guard), and
> `collector/cmd/sbcap/features.go` (`EventIEPIEV` state carry-forward).

## Context

The IDX session opens with a **pre-opening auction** (roughly 08:31–08:59 WIB)
during which the exchange runs a call-auction uncrossing to discover the opening
price. Two distinct data signals come out of this phase:

1. **IEP/IEV frames** — the exchange broadcasts an indicative equilibrium price
   and volume (IEP/IEV) as the auction evolves. In the replay library these are
   decoded as `EventIEPIEV` ("indicative equilibrium price/volume",
   `collector/internal/replay/replay.go`). The IEP is the market's best estimate
   of where the stock will open; it is a valuable pre-opening signal.

2. **Running-trade batch frames from the pre-opening auction** — when the
   uncrossing executes, the feed emits `running_trade_batch` frames whose entries
   have `side == ""` and `action == 0`. These carry no aggressor-side tag: the
   auction trades are matched by the exchange at a single price with no buyer- or
   seller-initiated distinction.

The collector already subscribed to `orderBook`, `liveprice`, and
`runningTradeBatch`. The question was whether to add `iepiev` as a fourth
subscribed channel, and — independently — how to treat pre-opening trade entries
in the signed-flow / OFI feature pipeline.

**Capture gap:** before this decision the `iepiev` channel was absent from
`activeChannels`, so pre-opening auction price discovery was not recorded at all.

**Flow pollution risk:** if pre-opening batch trades were counted as normal
aggressive flow, they would inject phantom signed volume into OFI metrics at the
open. A `side == ""` entry is structurally indistinguishable from a continuous-
session trade in the raw batch frame; the skip must be applied explicitly in
downstream tooling.

## Decision

**1. Subscribe to `iepiev` as a first-class channel.**

`activeChannels` in `collector/cmd/collector/main.go` sets `IEPIEV: true`
alongside `OrderBook`, `RunningTradeBatch`, and `LivePrice`. The channel name
`"iepiev"` is included in `activeChannelNames` and is therefore recorded in every
segment manifest (see [ADR-0002](./0002-segment-storage-and-compression.md)).
Capture is verified: a decoded pre-opening segment (08:31–08:59 WIB, 2026-06-18)
contained 857 `EventIEPIEV` events.

**2. Skip pre-opening batch trade entries in trade/OFI tooling.**

`collector/cmd/sbcap/trades.go` skips any trade entry whose `side` field is empty:

```go
if tr.Side == "" {
    return // skip pre-opening batch trades
}
```

The guard comment reads: "Pre-opening auction batch trades (side="") are SKIPPED —
they carry no aggressor-side tag and are not useful for signed-flow analysis."
This prevents phantom signed volume from appearing in the trade CSV and in any OFI
or trade-flow metric derived from it.

**3. Carry IEP forward as state in feature rows.**

`collector/cmd/sbcap/features.go` maintains `iep float64` / `hasIEP bool` in its
rolling state struct. On every `EventIEPIEV` event the latest IEP is stored:

```go
case replay.EventIEPIEV:
    if evt.Decoded != nil {
        st.iep = evt.Decoded.IEP
        st.hasIEP = true
    }
```

The `iep` column is then emitted on every subsequent order-book feature row,
carried forward until the next IEP/IEV update. This makes the IEP available as a
pre-opening context feature without changing the row-emission trigger (which
remains order-book updates).

**Rejected alternative:** treat pre-opening batch trade entries as normal
continuous-session trades. This was rejected because the entries have no aggressor
side: summing them into signed volume or OFI would inject a large, directionless
volume spike at the open and corrupt the feature series for the entire session.

## Consequences

**Positive**

- The pre-opening IEP signal is captured and available for study; it is a natural
  predictor candidate for the opening price and intraday momentum.
- OFI and signed-volume features are free of pre-opening artifacts; the skip is
  applied at the lowest level (`trades.go`) so all downstream consumers inherit
  the guarantee.
- IEP is carried forward into order-book feature rows with no change to the
  emission trigger, keeping the feature schema backward-compatible.
- Capture is empirically confirmed (857 events in one pre-open segment), not just
  asserted from code inspection.

**Negative / costs**

- `sbcap stat` does not count `iepiev` events in its fixed-format "channels"
  summary line (`collector/cmd/sbcap/stat.go`, line 208). The `statResult` struct
  has no `iepiev` field and `EventIEPIEV` falls through to the `default:` branch
  silently. This is a display bug in `stat`, not a capture problem — the events
  are present in the segment and decoded correctly by all other subcommands.
  Follow-up: add an `iepiev int` counter to `statResult` and include it in the
  `channels` print line.
- Pre-opening book state (bid/offer) accumulates in the feature roller during the
  auction but no IEP-gated feature rows are emitted until the first post-open
  order-book update. Studies that want a pure pre-opening snapshot must filter by
  time window (`--from` / `--to`).

## Related

- [ADR-0002 — segment storage & compression lifecycle](./0002-segment-storage-and-compression.md) —
  the `channels` manifest field records `"iepiev"` alongside the other three
  subscribed channels, making every sealed segment self-describing.
- [Plan 04 — microstructure study](../plans/04-microstructure-study.md) — the
  OBI/micro-price go/no-go gate that consumes the feature rows produced by
  `sbcap features`.
- [Plan 05 — OFI/trade-flow study](../plans/05-ofi-trade-flow-study.md) — the
  signed-volume and order-flow-imbalance analysis that depends on the pre-opening
  skip being in place.
