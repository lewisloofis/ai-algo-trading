# collector — Data Layer (Pillar 4)

Go module `collector`. Pure read-only data capture and inspection. This module
connects to the Stockbit market-data WebSocket, writes raw binary segments to disk,
ships them to S3, and provides offline inspection and depth-chart tooling. It does
not place orders and has no dependency on any order or trading API.

## Contents

| Path | What it is |
|------|------------|
| `cmd/collector/` | Capture binary — connects to the Stockbit WebSocket, writes raw Protobuf frames to rolling binary segment files, compresses and ships to S3. |
| `cmd/sbcap/` | Segment inspector/exporter — stat, dump, export, and order-book reconstruction from `.binpb` / `.binpb.zst` files. |
| `internal/feed/` | WebSocket feed client, SSM-backed config (watchlist, token rotation). |
| `internal/replay/` | Segment replayer — reads raw segment bytes and feeds a strategy the same event stream it would see live. |
| `internal/segment/` | Segment file format: append, roll, flush, and manifest logic. |
| `internal/s3upload/` | S3 upload with retry and progress tracking. |
| `internal/sbproto/` | Hand-rolled Protobuf codec for the Stockbit market-data wire format. |
| `internal/zstdio/` | zstd read/write wrappers. |
| `viz/` | Python depth-ladder and price-band charts (`plot_depth.py`, `plot_price_band.py`). |

## Prerequisites

- **Go 1.21+**
- **Python + venv** (optional — only needed for `viz/` charts; venv at `viz/.venv/`)

## Build

From the repo root (Go workspace):

```bash
make build
```

Or from this directory:

```bash
go build ./...
```

Both produce `bin/collector` and `bin/sbcap` at the repo root.

## Collect market data

The collector is pure data capture. It connects to the Stockbit market-data WebSocket
and appends raw Protobuf frames to local binary segments; it does not place any orders.

### Authentication

Supply credentials via environment variables:

**Option A — username/password login:**
```bash
export SB_USERNAME='your_stockbit_username'
export SB_PASSWORD='your_stockbit_password'
```

**Option B — pre-supplied tokens (skips password login):**
```bash
export SB_ACCESS_TOKEN='...'
export SB_REFRESH_TOKEN='...'
```

> **Device verification:** The first login from a new machine or network may trigger
> a device-verification challenge. Either log in to the Stockbit app interactively
> from this host first, or use Option B to supply tokens directly.

### Additional env vars

| Variable | Default | Meaning |
|----------|---------|---------|
| `CAPTURE_DIR` | `./segments` | Root directory for segment output. |
| `CAPTURE_WATCHLIST_ID` | `default` | Watchlist ID — becomes the middle path segment in every segment key. |
| `SSM_PARAM_PREFIX` | _(unset)_ | When set (e.g. `/sb-collector`), reads watchlist and watchlist_id from AWS SSM Parameter Store at boot instead of env vars. See [ADR-0006](../docs/adr/0006-watchlist-from-ssm.md). |

### Run

```bash
make collect
```

Or directly:

```bash
CAPTURE_DIR=$(pwd)/segments ./bin/collector
```

### Output layout

```
segments/
  dt=2026-06-17/
    default/
      seg-044221.binpb.zst     # closed segment (zstd-compressed)
      seg-044221.manifest.json # sidecar: frame count, channels, sha256
      seg-<HHMMSS>.binpb       # open/active segment (raw, no .zst yet)
```

Open segments are plain `.binpb`; they are compressed to `.binpb.zst` with a
sibling `.manifest.json` when closed (at midnight or on clean shutdown). Segments
roll hourly by default — see [ADR-0004](../docs/adr/0004-hourly-segment-roll-cadence.md).

Channels captured per symbol: `orderbook`, `liveprice`, `running_trade_batch`,
`iepiev` (IEP/IEV auction data). See [ADR-0007](../docs/adr/0007-iepiev-capture-and-preopening-trades.md).

## Inspect segments with sbcap

```
sbcap — market-data segment inspector

Usage:
  sbcap stat   <seg>
  sbcap dump   <seg> [--symbol S] [--channel trades|orderbook|liveprice] [--from HH:MM] [--to HH:MM]
  sbcap export <seg> --format ndjson|csv
  sbcap book   <seg> --symbol S --at HH:MM:SS [--json]
```

> **Flags-before-path:** Go's flag parser stops at the first positional argument.
> Always put flags **before** the segment path, not after.

### Examples

Via `make` (convenient for passing args):

```bash
# Summarise a segment
make sbcap ARGS="stat segments/dt=2026-06-17/default/seg-044221.binpb.zst"

# Dump orderbook messages for BUMI between 11:50 and 12:00
make sbcap ARGS="dump --symbol BUMI --channel orderbook --from 11:50 --to 12:00 segments/dt=2026-06-17/default/seg-044221.binpb.zst"

# Reconstruct the order book for BUMI at a specific time (JSON output)
make sbcap ARGS="book --symbol BUMI --at 11:53:00 --json segments/dt=2026-06-17/default/seg-044221.binpb.zst"
```

Or run the binary directly:

```bash
./bin/sbcap stat segments/dt=2026-06-17/default/seg-044221.binpb.zst

./bin/sbcap book --symbol BUMI --at 11:53:00 --json \
  segments/dt=2026-06-17/default/seg-044221.binpb.zst
```

## Visualize

Render a depth-ladder chart from a segment (requires the Python venv):

```bash
# First run: set up the venv
cd viz
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Render a depth chart
.venv/bin/python plot_depth.py \
  ../segments/dt=2026-06-17/default/seg-044221.binpb.zst \
  --symbol BUMI --at 11:53:00 --out depth.png
```

## Security

- Tokens are cached to `~/.sb-tokens.json` (mode `0600`). Never commit this file.
- The refresh token rotates on each refresh; the on-disk cache is updated automatically.
- This module uses private, undocumented Stockbit endpoints. Use your own account
  and be respectful of rate limits.

## More

- **Strategy prototype** (orderbook-imbalance signal, dry-run and live trading): [`../strategy/README.md`](../strategy/README.md)
- **Research studies** (microstructure analysis, backtest harness): [`../research/`](../research/)
- **Protocol documentation** (auth, WebSocket, order placement, lifecycle): [`../docs/`](../docs/)
- **Roadmap**: [`../docs/plans/00-roadmap-research-to-live.md`](../docs/plans/00-roadmap-research-to-live.md)
