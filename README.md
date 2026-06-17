# Algo Trading Exploration

This repo is a demonstration on how to use Stockbit Trading APIs for algo trading purposes.

It focuses on **read-only market-data capture and inspection** — the collector connects to the Stockbit WebSocket, writes raw binary segments to disk, and the inspector lets you query those segments offline. No orders are placed by any tool documented here. The strategy prototype (which can place orders in dry-run or live mode) lives in `collector/README.md`.

---

## Layout

| Path | What it is |
|------|------------|
| `collector/` | Go module (`module sb-algo-prototype`, Go 1.19). The only `go.mod` in the repo — all `go` commands must run from here. |
| `collector/cmd/collector/` | Market-data collector binary — connects to Stockbit WebSocket, writes raw binary segments. |
| `collector/cmd/sbcap/` | Segment inspector binary — stat, dump, export, and order-book reconstruction. |
| `collector/` (root package) | Orderbook-imbalance strategy prototype (`go run .`). See `collector/README.md`. |
| `collector/viz/` | Python matplotlib charts (`plot_depth.py`, `plot_price_band.py`). |
| `bin/` | Compiled binaries (gitignored). |
| `segments/` | Collector output (gitignored). Layout: `segments/dt=<YYYY-MM-DD>/<watchlistID>/seg-<HHMMSS>.binpb[.zst]`. |
| `docs/` | Protocol and design documentation. |

---

## Prerequisites

- **Go 1.19+** (the collector module requires it).
- **Python + venv** (optional — only needed for the `viz/` charts). The venv is at `collector/viz/.venv/`.

---

## Build

```bash
make build
```

This compiles both binaries into `bin/` at the repo root:

```
bin/collector
bin/sbcap
```

---

## Collect market data

The collector is pure data capture — it connects to the Stockbit market-data WebSocket and writes raw binary segments. It does not place any orders.

### Authentication

Supply credentials via environment variables. Two options:

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

> **Device verification:** The first login from a new machine or network may trigger a device-verification challenge. Either log in to the Stockbit app interactively first from this host, or use Option B to supply tokens directly and skip the password flow.

### Additional env vars

| Variable | Default | Meaning |
|----------|---------|---------|
| `CAPTURE_DIR` | `./segments` | Root directory for segment output (relative to cwd). |
| `CAPTURE_WATCHLIST_ID` | `default` | Watchlist ID to subscribe to. |

### Run

```bash
make collect
```

This sets `CAPTURE_DIR` to the repo-root `segments/` directory and starts the collector. You can also run it directly:

```bash
CAPTURE_DIR=$(pwd)/segments ./bin/collector
```

### Output layout

```
segments/
  dt=2026-06-17/
    default/
      seg-044221.binpb.zst    # closed segment (zstd-compressed)
      seg-044221.manifest.json
      seg-<HHMMSS>.binpb      # open/active segment (raw)
```

Open segments are plain `.binpb`; they are compressed to `.binpb.zst` (with a sibling `.manifest.json`) when closed.

---

## Inspect segments with sbcap

```
sbcap — market-data segment inspector

Usage:
  sbcap stat   <seg>
  sbcap dump   <seg> [--symbol S] [--channel trades|orderbook|liveprice] [--from HH:MM] [--to HH:MM]
  sbcap export <seg> --format ndjson|csv
  sbcap book   <seg> --symbol S --at HH:MM:SS [--json]

Segment files: .binpb (raw) or .binpb.zst (zstd-compressed)
```

> **Flags-before-path:** Go's flag parser stops at the first positional argument. Always put flags **before** the segment path, not after.

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

Or run the binary directly (flags must still come first):

```bash
./bin/sbcap stat segments/dt=2026-06-17/default/seg-044221.binpb.zst

./bin/sbcap book --symbol BUMI --at 11:53:00 --json \
  segments/dt=2026-06-17/default/seg-044221.binpb.zst
```

---

## Visualize

Render a depth ladder chart from a segment (requires the Python venv):

```bash
collector/viz/.venv/bin/python collector/viz/plot_depth.py \
  segments/dt=2026-06-17/default/seg-044221.binpb.zst \
  --symbol BUMI --at 11:53:00 --out depth.png
```

---

## Security

- Tokens are cached to `~/.sb-tokens.json` (mode `0600`). Never commit this file.
- The refresh token rotates on each refresh — the on-disk cache is kept up to date automatically.
- This tool uses private, undocumented Stockbit endpoints. Use your own account and be respectful of rate limits.

---

## More

- **Strategy prototype** (orderbook-imbalance, dry-run and live trading): [`collector/README.md`](collector/README.md)
- **Protocol documentation** (auth, WebSocket streaming, order placement, lifecycle): [`docs/`](docs/)
