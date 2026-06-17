# Market-Data Capture & Replay — Plans

This directory contains the design and implementation plans for the cheapest
correct way to **record Stockbit market-data to disk, archive it on AWS, and
replay it for backtesting** — without re-architecting the prototype.

## Goal in one paragraph

Stream live price, running-trade, and orderbook data from the Stockbit
market-data WebSocket for a configurable watchlist of ~10–50 IDX symbols.
Append every raw Protobuf frame to a local binary log as it arrives (zero decode
on the hot path). Roll and zstd-compress log segments daily. Ship compressed
segments to an S3 bucket in `ap-southeast-3` (Jakarta). Run only during IDX
market hours via EventBridge scheduling so compute cost is effectively zero for
most of the day. For backtesting, a replayer reads the same raw bytes through
`collector/datafeed.go`'s existing `decodeWrap` function, feeding a strategy the
same event stream it would see live — no code changes required between live and
replay mode.

## Plans

| Doc | What it covers |
|-----|----------------|
| [01-market-data-capture.md](./01-market-data-capture.md) | End-to-end capture architecture: locked decisions, component design, AWS deployment (compute options + cost table), failure modes, and phased implementation steps. |
| [02-storage-format-and-replay.md](./02-storage-format-and-replay.md) | Precise on-disk and S3 specification: binary record framing (byte-level layout), segment naming, JSON manifest schema, zstd rationale, S3 layout + lifecycle policy, replayer design with pseudocode, and optional Parquet analytics schema. |

## Cheapest setup at a glance

- **Compute:** EC2 `t4g.nano` (Graviton/ARM) in `ap-southeast-3`, started and
  stopped by EventBridge around IDX session hours — roughly 150 active
  hours/month → under $1/month in compute.
- **Storage:** S3 Standard for the current week's compressed segments, automatic
  transition to Glacier Deep Archive after 30 days. Full-microstructure data for
  ~30 symbols compressed with zstd runs to tens–low-hundreds of MB/day; monthly
  S3 cost is cents.
- **Code reuse:** the capture loop is the existing `collector/websocket.go` WS
  client plus a thin append layer; the replay path reuses `datafeed.go`'s
  `decodeWrap` directly — no new decode logic.

---

Back to [docs/README.md](../README.md)
