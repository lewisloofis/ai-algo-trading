# Storage Format & Replay Specification

> **Status:** ✅ Approved · **Owner:** @lewis · **Updated:** 2026-06-17
> **Related:** [01-market-data-capture.md](./01-market-data-capture.md) · [Plans index](./README.md) · [ADR-0001: plans as tickets](../adr/0001-treat-plans-as-tickets.md)

This document defines the exact on-disk and S3 format for captured market-data
segments, the JSON manifest schema, the replayer design with pseudocode, and the
optional Parquet analytics schema. It is the normative reference for any tool
that reads or writes segment files.

> ⚠️ **Disclaimer.** See [01-market-data-capture.md §1](./01-market-data-capture.md)
> for the full disclaimer on undocumented Stockbit endpoints and ToS.

---

## 1. Design rationale

### Why store raw binary frames instead of decoded records?

Every binary frame received from `ReadMessage` (see `collector/websocket.go`)
is a complete, self-contained `WebsocketWrapMessageChannel` Protobuf message.
Storing the raw bytes has three advantages:

1. **Zero CPU on the hot path.** No allocation, no field-number traversal, no
   string parsing. The collector is just a copy of bytes from a socket to a file.
2. **Perfect replay fidelity.** The replay path feeds the exact same bytes
   through `decodeWrap` (in `collector/datafeed.go`) as the live path would.
   Bugs introduced by an intermediate decode step are impossible.
3. **Schema resilience.** If the proto contract changes and a new field is
   added, the raw frames already contain it — no re-capture needed. Old frames
   are still decodable by the old `decodeWrap`; new frames decode with a new
   version.

### Why zstd?

The orderbook `body` string format (`#O|SYMBOL|BID|price;queue;volume;|...|ts`)
is highly repetitive: the same symbol name appears in every message, price
levels repeat across consecutive updates, and the structure is identical for
every frame. Protobuf's varint encoding also produces repetitive byte patterns
across similar messages. zstd's LZ77 back-reference finder is exceptionally
effective on this kind of data, typically achieving 5–10x compression ratios.
zstd also decompresses faster than gzip at equivalent ratios, which matters for
replay throughput.

---

## 2. Binary record format

A **segment file** (`.binpb`, uncompressed; or `.binpb.zst`, compressed) is a
flat sequence of **records**. There are two record types: data records and
control records. They share the same framing header but are distinguished by
a record-type byte.

### 2.1 Data record (type = 0x01)

Carries one raw Protobuf frame from the WebSocket.

```
Offset  Size     Field
------  -------  -----
0       1 byte   Record type = 0x01
1       8 bytes  recv_ns: frame arrival time, Unix nanoseconds, big-endian uint64
9       4 bytes  frame_len: length of the raw Protobuf frame, big-endian uint32
13      N bytes  frame: the exact raw bytes returned by wsConn.ReadMessage
```

Total overhead: 13 bytes per frame. At typical full-microstructure update rates
for ~30 symbols (hundreds of frames per second during active market hours), the
overhead is negligible.

| Byte offset | Type | Description |
|-------------|------|-------------|
| `[0]` | `uint8` | Record type: `0x01` = data frame |
| `[1..8]` | `uint64` big-endian | `recv_ns`: arrival time, Unix nanoseconds |
| `[9..12]` | `uint32` big-endian | `frame_len`: byte count of following frame |
| `[13..13+frame_len-1]` | `[]byte` | Raw `WebsocketWrapMessageChannel` protobuf bytes |

### 2.2 Control record (type = 0x02)

Carries a connection lifecycle event. Control records have no frame payload;
instead they carry a UTF-8 JSON object describing the event.

```
Offset  Size     Field
------  -------  -----
0       1 byte   Record type = 0x02
1       8 bytes  event_ns: event time, Unix nanoseconds, big-endian uint64
9       4 bytes  payload_len: length of JSON payload, big-endian uint32
13      M bytes  payload: UTF-8 JSON object
```

| Byte offset | Type | Description |
|-------------|------|-------------|
| `[0]` | `uint8` | Record type: `0x02` = control event |
| `[1..8]` | `uint64` big-endian | `event_ns`: event time, Unix nanoseconds |
| `[9..12]` | `uint32` big-endian | `payload_len`: byte count of following JSON |
| `[13..13+payload_len-1]` | `[]byte` | UTF-8 JSON: `{"event": "...", ...}` |

#### Control event JSON payloads

```json
{ "event": "CONNECT",       "ws_url": "wss://wss-trading.stockbit.com/ws" }
{ "event": "SUBSCRIBE",     "watchlist": ["BBCA","BMRI","TLKM"] }
{ "event": "REAUTH",        "reason": "proactive" }
{ "event": "REAUTH",        "reason": "401" }
{ "event": "RECONNECT",     "attempt": 1, "backoff_ms": 750 }
{ "event": "SWITCH_RESET",  "symbol": "BBCA" }
```

### 2.3 Reading a segment — algorithm

```
open file (decompress if .zst)
loop:
    read 1 byte -> record_type
    if EOF: break
    read 8 bytes -> timestamp_ns (big-endian uint64)
    read 4 bytes -> payload_len (big-endian uint32)
    read payload_len bytes -> payload
    if record_type == 0x01:
        emit DataRecord{recv_ns: timestamp_ns, frame: payload}
    elif record_type == 0x02:
        emit ControlRecord{event_ns: timestamp_ns, json: payload}
    else:
        error("unknown record type")
```

On truncated final record (last few bytes incomplete due to crash): catch
short-read errors at any field after `record_type` and treat as end-of-file.

---

## 3. Segment naming

Segments are named by the UTC start time of the capture session that created them:

```
seg-<HHMMSS>.binpb.zst          # compressed (written to S3)
seg-<HHMMSS>.manifest.json      # sidecar manifest (written alongside)
```

The `HHMMSS` component is the UTC time when the segment was opened (i.e. when
the collector process started or when a UTC midnight roll occurred).

Example: segment opened at 01:45:02 UTC on 2026-01-15 for watchlist `default`:

```
seg-014502.binpb.zst
seg-014502.manifest.json
```

---

## 4. S3 layout

```
s3://<bucket>/raw/dt=<YYYY-MM-DD>/<watchlist-id>/seg-<HHMMSS>.binpb.zst
s3://<bucket>/raw/dt=<YYYY-MM-DD>/<watchlist-id>/seg-<HHMMSS>.manifest.json
```

The `dt=` prefix follows Hive partition naming, which allows tools like AWS
Glue and DuckDB to auto-discover partitions.

`<watchlist-id>` is a short stable identifier for the symbol list used (e.g.
`default`, `bluechips-30`). Changing the watchlist requires a new id so
segments are not mixed.

Full example:

```
s3://sb-marketdata-123456789012/
  raw/
    dt=2026-01-15/
      default/
        seg-014502.binpb.zst          # today's segment
        seg-014502.manifest.json
    dt=2026-01-14/
      default/
        seg-014500.binpb.zst
        seg-014500.manifest.json
```

### S3 lifecycle policy

Applied to prefix `raw/`:

| Rule | Age condition | Transition |
|------|--------------|------------|
| To Standard-IA | > 7 days | `STANDARD_IA` |
| To Glacier Deep Archive | > 30 days | `DEEP_ARCHIVE` |

```json
{
  "Rules": [{
    "ID": "raw-lifecycle",
    "Filter": { "Prefix": "raw/" },
    "Status": "Enabled",
    "Transitions": [
      { "Days": 7,  "StorageClass": "STANDARD_IA" },
      { "Days": 30, "StorageClass": "DEEP_ARCHIVE" }
    ]
  }]
}
```

---

## 5. JSON manifest schema

Each segment has a sidecar manifest written at the same time as the compressed
segment, before the S3 upload. The manifest lets a reader understand what is in
the segment without decoding every frame.

```json
{
  "schema_version": 1,
  "collector_version": "0.1.0",
  "proto_contract": "sb-protos @ <git-sha>",
  "watchlist_id": "default",
  "symbols": ["BBCA", "BMRI", "TLKM", "ASII", "BBRI"],
  "channels": ["runningTradeBatch", "orderBook", "liveprice"],
  "segment_start_utc": "2026-01-15T01:45:02.000000000Z",
  "segment_end_utc":   "2026-01-16T01:45:00.000000000Z",
  "total_data_records": 847291,
  "total_control_records": 12,
  "frame_counts": {
    "liveprice":          123450,
    "orderbook":          587234,
    "runningTradeBatch":  134907,
    "ping":               1825,
    "other":              1875
  },
  "gaps": [
    {
      "gap_start_utc": "2026-01-15T04:12:33.000000000Z",
      "gap_end_utc":   "2026-01-15T04:12:35.750000000Z",
      "reason": "reconnect"
    }
  ],
  "switch_resets": [
    {
      "utc": "2026-01-15T02:01:14.000000000Z",
      "symbol": "BBCA"
    }
  ],
  "compressed_size_bytes": 47382019,
  "uncompressed_size_bytes": 391847203
}
```

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Format version; increment when the record framing changes |
| `collector_version` | string | Semver of the collector binary |
| `proto_contract` | string | Git SHA of `sb-protos` at build time |
| `watchlist_id` | string | Identifier for the symbol set |
| `symbols` | string[] | Exact list of symbols subscribed |
| `channels` | string[] | Channels subscribed (`runningTradeBatch`, `orderBook`, `liveprice`) |
| `segment_start_utc` | RFC3339Nano | Time of first record in segment |
| `segment_end_utc` | RFC3339Nano | Time of last record in segment |
| `total_data_records` | int | Count of type-0x01 records |
| `total_control_records` | int | Count of type-0x02 records |
| `frame_counts` | object | Per-channel decoded frame counts (best-effort from partial decode) |
| `gaps` | array | Reconnect gaps — start/end UTC + reason |
| `switch_resets` | array | Observed `switch_reset` events — UTC + symbol |
| `compressed_size_bytes` | int | Size of the `.binpb.zst` file |
| `uncompressed_size_bytes` | int | Estimated uncompressed size |

---

## 6. Replayer design

The replayer reads segment files from disk (or directly from S3 via a streaming
download) and emits a `MarketEvent` stream to a strategy — the same types that
the live collector would deliver.

### 6.1 MarketEvent types

```go
type EventKind string

const (
    EventLivePrice    EventKind = "liveprice"
    EventOrderbook    EventKind = "orderbook"
    EventRunningTrade EventKind = "running_trade"
    EventControl      EventKind = "control"
)

type MarketEvent struct {
    RecvNs    int64       // arrival timestamp (ns since Unix epoch)
    Kind      EventKind
    Symbol    string      // empty for control events
    // exactly one of the following is non-nil:
    LivePrice    *decoded  // from datafeed.decodeWrap, kind=="liveprice"
    Orderbook    *decoded  // from datafeed.decodeWrap, kind=="orderbook"
    RunningTrade *decoded  // from datafeed.decodeWrap, kind=="running_trade"
    Control      *ControlEvent
}

type ControlEvent struct {
    Kind       string // "CONNECT", "SUBSCRIBE", "REAUTH", "RECONNECT", "SWITCH_RESET"
    Symbol     string // set for SWITCH_RESET
    Watchlist  []string // set for SUBSCRIBE
    Reason     string // set for REAUTH, RECONNECT
}
```

> `decoded` is the existing struct from `collector/datafeed.go`. The replayer
> calls `decodeWrap(frame)` and surfaces the result — no new decode logic.

### 6.2 Pacing modes

The replayer supports two pacing modes selected at startup:

**Mode A: as-fast-as-possible (default for backtesting)**

Emit events as quickly as the CPU can decode them. `RecvNs` is set to the
recorded arrival timestamp so the strategy can reconstruct timing without
actual wall-clock delay. Suitable for batch backtesting where throughput matters.

**Mode B: event-time paced (for realism / visual inspection)**

Sleep between events to honour the original inter-arrival gaps recorded in
`recv_ns`. This simulates the live feed rate.

```go
// event-time pacing
var prevNs int64
for each event:
    if prevNs > 0:
        gapNs := event.RecvNs - prevNs
        if gapNs > 0 && gapNs < 5_000_000_000:  // cap at 5s (don't replay long silences)
            time.Sleep(time.Duration(gapNs))
    prevNs = event.RecvNs
    emit(event)
```

The 5-second cap avoids replaying the mid-day break or weekend gaps at real
time. Strategies can override the cap for realism testing.

### 6.3 Gap and discontinuity handling

The replayer emits a `ControlEvent` for every control record in the segment.
Strategies should register a control event handler and:

- On `RECONNECT`: treat the next orderbook frames per symbol as **full snapshots**
  (not incremental diffs). Reset any cached book state for all subscribed symbols.
- On `SWITCH_RESET` for symbol X: reset the cached book state for symbol X.
  Do not trade based on the pre-reset book.
- On `REAUTH`: no action required — the frame stream is continuous.

For orderbook `sequence_number` gap detection during replay:

```go
// track per symbol
lastSeq := map[string]int64{}

// on each orderbook event
if prev, ok := lastSeq[sym]; ok {
    if seq > prev+1 {
        emit gap event: {symbol: sym, missed: seq - prev - 1}
    }
}
lastSeq[sym] = seq
```

The `sequence_number` is field 3 of the consumer `Orderbook` message (varint).
It is extracted by `decodeOrderbookMsg` — add it to the `decoded` struct if not
already present (currently `collector/datafeed.go` only extracts `stock_code`
and `body`).

### 6.4 Replayer pseudocode

```go
func Replay(segments []string, mode PacingMode, handler func(MarketEvent)) error {
    var prevNs int64

    for _, segPath := range segments {
        r, err := openSegment(segPath) // transparently decompresses .zst
        if err != nil {
            return fmt.Errorf("open segment %s: %w", segPath, err)
        }
        defer r.Close()

        for {
            rec, err := readRecord(r)
            if err == io.EOF {
                break
            }
            if err != nil {
                log.Printf("warn: truncated record in %s: %v", segPath, err)
                break
            }

            var evt MarketEvent
            switch rec.Type {
            case 0x01: // data frame
                evt.RecvNs = rec.TimestampNs
                d, err := decodeWrap(rec.Payload)
                if err != nil {
                    log.Printf("warn: decode error: %v", err)
                    continue
                }
                evt.Kind = EventKind(d.kind)
                evt.Symbol = d.symbol
                // assign LivePrice / Orderbook / RunningTrade from d

            case 0x02: // control record
                evt.RecvNs = rec.TimestampNs
                evt.Kind = EventControl
                json.Unmarshal(rec.Payload, &evt.Control)
            }

            if mode == ModePaced && prevNs > 0 {
                gap := time.Duration(evt.RecvNs - prevNs)
                if gap > 0 && gap < 5*time.Second {
                    time.Sleep(gap)
                }
            }
            prevNs = evt.RecvNs
            handler(evt)
        }
    }
    return nil
}

func openSegment(path string) (io.ReadCloser, error) {
    f, err := os.Open(path)
    if err != nil {
        return nil, err
    }
    if strings.HasSuffix(path, ".zst") {
        // github.com/klauspost/compress/zstd or valyala/gozstd
        dec, err := zstd.NewReader(f)
        if err != nil {
            f.Close()
            return nil, err
        }
        return dec, nil
    }
    return f, nil
}
```

### 6.5 Selecting segments for a backtest

To backtest over a date range, list the relevant S3 objects (or local files)
sorted by `dt=` partition and segment start time, then pass them to `Replay`
in order:

```go
segments, _ := listSegments("raw/", "2026-01-10", "2026-01-14", "default")
// segments = ["raw/dt=2026-01-10/default/seg-014500.binpb.zst", ...]
Replay(segments, ModeFast, myStrategy.OnEvent)
```

---

## 7. Inspecting captured data

Raw `.binpb` and `.binpb.zst` files are not human-readable on their own — every
inspection path decodes frames through the same `decodeWrap` function in
`collector/datafeed.go` that the replayer uses. Inspection **is** the replay
path with different output formatting. Two regimes cover different needs.

### 7.1 Single-segment inspection — `sbcap` CLI

`sbcap` is a read-only CLI tool that wraps the replayer for interactive use.
It transparently decompresses `.binpb.zst` files and accepts an `s3://` path to
inspect a segment directly from the bucket (streaming download — no local copy
needed).

#### `sbcap stat <seg>` — health report

Provides a summary equivalent to the Phase-1 `verify` tool; they can share the
same implementation. Reports:

- Record counts by type: data records (type `0x01`) vs control records (type `0x02`)
- Frame counts by decoded channel: `running_trade` / `orderbook` / `liveprice` / `ping` / `error`
- Per-symbol trade counts (from `RunningTrade` frames)
- First and last `recv_ns` converted to wall-clock time, and the total span
- Orderbook `sequence_number` gaps per symbol (any jump > 1 in consecutive frames)
- Control event summary: `SWITCH_RESET` count, `RECONNECT` count, `REAUTH` count
- Truncation check: whether the final record's `frame_len` bytes are fully present

Example output:

```
seg-014502.binpb.zst  (391 MB uncompressed, 47 MB on disk)
span      : 2026-01-15 01:45:02 UTC → 2026-01-16 01:44:58 UTC  (23h 59m 56s)

records   : 847 291 data  /  12 control
channels  : orderbook 587 234  running_trade 134 907  liveprice 123 450  ping 1 825  error 0

trades/symbol:
  BBCA   34 120     BMRI   28 903     TLKM   21 455
  ASII   18 234     BBRI   32 195     …

seq gaps  : BBCA ×1 (offset 2h12m)    BMRI ×0    TLKM ×0    ASII ×0    BBRI ×0
controls  : RECONNECT ×1  SWITCH_RESET ×0  REAUTH ×3
truncated : no  (final record complete)
```

#### `sbcap dump <seg> --symbol BBCA --channel trades --from 10:00 --to 10:05`

Decodes and prints one event per line. Flags `--symbol`, `--channel`
(`trades|orderbook|liveprice`), `--from`, and `--to` (local WIB or UTC time)
are all optional and can be combined. Internally calls `decodeWrap` on every
data frame and applies filters before printing — no custom decode logic beyond
what the replayer already does.

Example output:

```
10:00:01.234  TRADE  BBCA  price=8750  vol=200    side=B  trade#=14201
10:00:01.891  TRADE  BBCA  price=8750  vol=500    side=B  trade#=14202
10:00:02.104  BOOK   BBCA  BID  8750×1200  8725×3400  8700×8900
10:00:02.109  BOOK   BBCA  OFFER 8775×900  8800×2200  8825×5100
10:00:03.442  PRICE  BBCA  last=8750  high=8800  low=8700  vol=4.2M
10:00:04.017  TRADE  BBCA  price=8775  vol=100    side=S  trade#=14203
```

#### `sbcap export <seg> --format ndjson|csv`

Emits all decoded events as NDJSON (one JSON object per line, pipe to `jq`) or
CSV (open in a spreadsheet). Useful for ad-hoc exploration without writing code.

```bash
sbcap export seg-014502.binpb.zst --format ndjson | jq 'select(.channel=="trades" and .symbol=="BBCA")'
sbcap export s3://sb-marketdata-xxx/raw/dt=2026-01-15/default/seg-014502.binpb.zst --format csv > jan15.csv
```

#### `sbcap book <seg> --symbol BBCA --at 10:30:00`

Reconstructs and prints the order book as it stood at a given point in time.

> ⚠️ **Side-assembly detail.** BID and OFFER arrive as **separate** `orderbook`
> messages (see [02-websocket-streaming.md §5](../02-websocket-streaming.md),
> line 288). The tool replays frames from the start of the segment up to time T,
> caching the latest BID message and the latest OFFER message per symbol. At T
> it assembles them into a full ladder — the same side-assembly that
> `parseOrderbookBody` in `collector/datafeed.go` already performs.

Example output:

```
Order book: BBCA  @10:30:00.000 WIB  (seq 48291/48290)

BID                         OFFER
Level  Price   Queue  Vol   Price   Queue  Vol
    0  8,750      12  3200   8,775      9   900
    1  8,725      31  8400   8,800     22  2200
    2  8,700      44 12000   8,825     35  5100
    3  8,675      18  5500   8,850     11  1800
    4  8,650       9  2100   8,875      6   600
```

### 7.2 Cross-day queries — Parquet + DuckDB/Polars

Once segments are decoded to the Parquet tier described in §9, inspection
becomes plain SQL. DuckDB can read Parquet **directly from S3** using Hive
partition auto-discovery — no download required.

```sql
-- Large-trade count by day for BBCA (trades ≥ 1 billion IDR value)
-- Reads only the dt/symbol partitions needed; no full-table scan.
SELECT
    dt,
    COUNT(*) AS large_trade_count,
    SUM(value) / 1e9 AS total_value_bio_idr
FROM read_parquet(
    's3://sb-marketdata-xxx/parquet/trades/dt=*/symbol=BBCA/*.parquet',
    hive_partitioning = true
)
WHERE value >= 1e9
GROUP BY dt
ORDER BY dt;
```

[Polars](https://pola.rs) is the DataFrame alternative for Python-based research
workflows: `pl.scan_parquet("s3://…/parquet/trades/dt=*/symbol=BBCA/*.parquet")`.

> ⚠️ **Source of truth.** The raw log (`.binpb.zst`) is the authoritative
> record — it is what the backtester replays and the only store with perfect
> fidelity. Parquet is a derived, queryable view. If a Parquet query looks
> wrong, re-decode from raw via the decoder job (§9.4) and re-check with
> `sbcap dump`.

### 7.3 Which tool to use

| Goal | Tool |
|------|------|
| Confirm a day captured cleanly | `sbcap stat <seg>` |
| Read raw trades/quotes by symbol & time | `sbcap dump` / `sbcap export` → `jq` |
| See the book as it stood at a moment in time | `sbcap book --at <time>` |
| Query or aggregate across many days | Parquet + DuckDB / Polars |

---

## 8. Storage size estimate

Assumptions:
- 30 symbols, full microstructure (orderBook BID + OFFER, runningTradeBatch,
  liveprice), active IDX trading day (~5.5 hours)
- Orderbook: ~2–5 updates/second/symbol × 30 symbols = 60–150 frames/second
  average; each frame ~200–600 bytes raw
- Trades: ~0.5–2 trades/second/symbol at peak; each frame ~80–150 bytes raw
- Liveprice: ~0.5–1 update/second/symbol; each frame ~100–200 bytes raw

Rough uncompressed: ~50–200 MB/day (lower on quiet days, higher during opening
auction and peak hours). With zstd at 6x ratio: **~8–35 MB/day compressed**.

| Period | Uncompressed estimate | Compressed (zstd ~6x) | Storage class | Cost/month |
|--------|-----------------------|----------------------|---------------|------------|
| Current week (5 days) | ~0.25–1 GB | ~40–170 MB | Standard | ~$0.01 |
| Last 30 days | ~1.5–6 GB | ~0.25–1 GB | Standard-IA | ~$0.01–0.02 |
| 1 year archive | ~18–72 GB | ~3–12 GB | Glacier Deep Archive | ~$0.003–0.012 |

Even at the high end of these estimates, annual S3 cost is under $1.

---

## 9. Optional — Parquet analytics tier

> This section documents a **Phase 3 / optional** offline conversion job. The
> raw segment files are the source of truth; Parquet is a derived convenience
> layer for vectorized backtesting with DuckDB or Polars. It adds no requirement
> to the collector or replayer.

A decoder job reads raw segments from S3, calls `decodeWrap` on every data
frame, and writes Parquet files partitioned by `symbol` and `dt`. Three tables
correspond to the three channel types.

### 9.1 Trades table (`trades`)

Derived from `runningTradeBatch` frames (`RunningTrade` / `RunningTradeBatch`
from `collector/datafeed.go`). Field names follow
[02-websocket-streaming.md §5](../02-websocket-streaming.md).

| Column | Type | Source |
|--------|------|--------|
| `dt` | date | partition key (from segment filename) |
| `symbol` | string | `RunningTrade.stock` |
| `recv_ns` | int64 | data record `recv_ns` |
| `exchange_time` | timestamp (ns) | `RunningTrade.time` (`google.protobuf.Timestamp`) |
| `ws_time` | timestamp (ns) | `RunningTrade.websocket_time` |
| `price` | double | `RunningTrade.price` |
| `volume` | double | `RunningTrade.volume` (shares) |
| `value` | double | computed: `price * volume` |
| `action` | string | `RunningTrade.action` (`TradeType`: buy/sell/unspecified) |
| `trade_number` | int32 | `RunningTrade.trade_number` |
| `market_board` | string | `RunningTrade.market_board` (`BoardType`: RG/TN/NG) |
| `change_value` | double | `RunningTrade.change.value` |
| `change_pct` | double | `RunningTrade.change.percentage` |
| `is_global` | bool | `RunningTrade.is_global` |

### 9.2 Orderbook table (`orderbook`)

Derived from `orderBook` frames (consumer `Orderbook` message). The pipe-
delimited `body` string is parsed (via `parseOrderbookBody` from
`collector/datafeed.go`) and exploded to one row per price level.

| Column | Type | Source |
|--------|------|--------|
| `dt` | date | partition key |
| `symbol` | string | `Orderbook.stock_code` |
| `recv_ns` | int64 | data record `recv_ns` |
| `exchange_time` | timestamp (ns) | `Orderbook.datetime` (RFC3339Nano parsed) |
| `sequence_number` | int64 | `Orderbook.sequence_number` (gap detection key) |
| `side` | string | `BID` or `OFFER` (from `body` index 2) |
| `level` | int | depth level index (0 = best) |
| `price` | double | level price from `body` |
| `queue` | int | queue count from `body` |
| `volume` | double | volume in shares from `body` |
| `board` | string | `Orderbook.board` (`BoardType`) |
| `switch_reset` | int | `Orderbook.switch_reset` enum value (non-zero = reset) |
| `source` | string | `Orderbook.source` enum string |

### 9.3 Liveprice table (`liveprice`)

Derived from `liveprice` frames (`LivePrice` from `consumer/entity/v1/entity.proto`).
Currently `collector/datafeed.go` extracts only `stock_code` and `lastprice` from
`LivePrice`. The full field set is documented in
[02-websocket-streaming.md §5](../02-websocket-streaming.md).

| Column | Type | Source |
|--------|------|--------|
| `dt` | date | partition key |
| `symbol` | string | `LivePrice.stock_code` |
| `recv_ns` | int64 | data record `recv_ns` |
| `itch_time` | string | `LivePrice.itch_incoming_time` (RFC3339Nano) |
| `lastprice` | double | `LivePrice.lastprice` |
| `open` | double | `LivePrice.open` |
| `high` | double | `LivePrice.high` |
| `low` | double | `LivePrice.low` |
| `close` | double | `LivePrice.close` |
| `prev` | double | `LivePrice.prev` |
| `average` | double | `LivePrice.average` |
| `volume` | double | `LivePrice.volume` |
| `value` | double | `LivePrice.value` |
| `frequency` | double | `LivePrice.frequency` |
| `frg_buy` | double | `LivePrice.frg_buy` (foreign buy) |
| `frg_sell` | double | `LivePrice.frg_sell` (foreign sell) |
| `change_value` | double | `LivePrice.change.value` |
| `change_pct` | double | `LivePrice.change.percentage` |
| `board` | string | `LivePrice.board` (`BoardType`) |
| `sequence_number` | int64 | `LivePrice.sequence_number` |

### 9.4 Parquet S3 layout

```
s3://<bucket>/
  parquet/
    trades/
      dt=2026-01-15/
        symbol=BBCA/
          part-000.parquet
        symbol=BMRI/
          part-000.parquet
    orderbook/
      dt=2026-01-15/
        symbol=BBCA/
          bid.parquet
          offer.parquet
    liveprice/
      dt=2026-01-15/
        symbol=BBCA/
          part-000.parquet
```

This layout allows DuckDB to query efficiently:

```sql
-- last-price time series for BBCA on 2026-01-15
SELECT recv_ns / 1e9 AS t, lastprice
FROM read_parquet('s3://sb-marketdata-xxx/parquet/liveprice/dt=2026-01-15/symbol=BBCA/*.parquet')
ORDER BY recv_ns;
```

---

Back to [01-market-data-capture.md](./01-market-data-capture.md) · [Plans Index](./README.md) · [docs/README.md](../README.md)
