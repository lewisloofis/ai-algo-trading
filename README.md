# Algo Trading Exploration

This repo is a demonstration on how to use Stockbit Trading APIs for algo trading purposes.

It focuses on **read-only market-data capture and inspection** — the collector connects to the Stockbit WebSocket, writes raw binary segments to disk, and the inspector lets you query those segments offline. No orders are placed by any tool documented here. The strategy prototype (which can place orders in dry-run or live mode) lives in `strategy/README.md`.

---

## Layout

The top-level directories map to the trading-system pillars in
[`docs/plans/00-roadmap-research-to-live.md`](docs/plans/00-roadmap-research-to-live.md).

| Path | Pillar | What it is |
|------|--------|------------|
| `collector/` | **Pillar 4** — Infra & ops | Go module `collector` (Go 1.21). Data-layer only: capture binary (`cmd/collector`), segment inspector (`cmd/sbcap`), shared packages (`internal/`), and Python depth charts (`viz/`). See `collector/README.md`. |
| `research/` | **Pillar 1** — Alpha factory | Python research environment: microstructure studies (`study/`), fill simulator and backtester (`backtest/`), shared utilities (`lib/`). |
| `strategy/` | **Pillar 2** — Execution (seed) | Go module `strategy` (Go 1.21). The zero-dependency orderbook-imbalance trading-bot prototype. Can place real orders when armed. See `strategy/README.md`. |
| `go.work` | — | Go workspace tying `./collector` and `./strategy`. Run `go` commands from the repo root or from within either module directory. |
| `bin/` | — | Compiled binaries (gitignored). |
| `segments/` | — | Collector output (gitignored). Layout: `segments/dt=<YYYY-MM-DD>/<watchlistID>/seg-<HHMMSS>.binpb[.zst]`. |
| `docs/` | — | Protocol, design documentation, plans, and ADRs. |

---

## Prerequisites

- **Go 1.21+** (required by both `collector/` and `strategy/`).
- **Python + venv** (optional — two independent venvs):
  - `collector/viz/.venv/` — depth and price-band charts (matplotlib).
  - `research/.venv/` — microstructure studies and backtest harness (pandas, scipy, etc.).

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

Render a depth ladder chart from a segment (requires the `collector/viz/.venv` Python venv):

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

- **Strategy prototype** (orderbook-imbalance signal, dry-run and live trading): [`strategy/README.md`](strategy/README.md)
- **Research studies** (microstructure analysis, fill simulator, backtest harness): [`research/`](research/)
- **Protocol documentation** (auth, WebSocket streaming, order placement, lifecycle): [`docs/`](docs/)
- **Roadmap** (five pillars, 40 bps cost gate, sequencing): [`docs/plans/00-roadmap-research-to-live.md`](docs/plans/00-roadmap-research-to-live.md)
