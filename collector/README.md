# Stockbit Algo-Trading Prototype (Go)

A small, **dependency-free** Go program that demonstrates end-to-end algorithmic
trading on Stockbit:

```
login → keep session alive → stream live order book → orderbook-imbalance
strategy → place buy/sell orders (dry-run by default, live behind safety gates)
```

It ties together the three docs in [`../docs`](../docs): authentication & token
refresh, the Protobuf market-data WebSocket, and order placement.

> ⚠️ **This can place REAL orders with REAL money** when armed (see *Safety*).
> By default it runs in **dry-run** mode and sends nothing.

## Why this matters (the pitch)

It shows, on live IDX data, that a machine can (1) ingest the full order book in
real time, (2) turn microstructure (bid/offer volume imbalance) into a decision
in microseconds, and (3) act on it under hard risk limits — the loop a human
trader can't run at that speed or consistency. Swap the strategy file and the
same harness runs any signal.

## Design

Zero third-party dependencies — a hand-rolled minimal Protobuf codec and
WebSocket client (pure stdlib), so it builds offline on any Go ≥ 1.19. Field
numbers come straight from the contracts in [`../sb-protos`](../sb-protos).

| File | Responsibility |
|------|----------------|
| `config.go` | Env-var config + safety gates |
| `auth.go` | Exodus login / refresh, Carina PIN login, WebSocket key |
| `websocket.go` | Minimal RFC 6455 client (TLS handshake, masked frames) |
| `protobuf.go` | Minimal proto3 reader/writer |
| `datafeed.go` | Encode `WebsocketRequest`, decode `WebsocketWrapMessageChannel`, parse the order-book `body` string |
| `strategy.go` | Order-book volume-imbalance signal (pluggable) |
| `broker.go` | Carina order client: dry-run, live gating, caps, kill switch |
| `main.go` | Wires it together: connect, subscribe, heartbeat, reconnect, refresh |
| `prototype_test.go` | Unit tests for the parsing/strategy/codec (no network) |

## Build & test

```bash
cd collector
go build ./...
go test ./...
```

## Verifying trade capture (next market open)

IDX hours are approximately 09:00–16:00 WIB. Run these commands during a live
session to confirm `running_trade_batch` frames are actually being captured:

```bash
# 1. Build the collector and the inspector.
go build -o /tmp/collector ./cmd/collector
go build -o /tmp/sbcap     ./cmd/sbcap

# 2. Set credentials (one of the three options below):
#    Option A — username + password (triggers fresh login):
export SB_USERNAME='your_username'
export SB_PASSWORD='your_password'
#    Option B — pre-obtained access token (skips login):
export SB_ACCESS_TOKEN='<token>'
#    Option C — persisted token file (FileTokenStore, default path ~/.sb-tokens.json):
#    just ensure the file exists with valid tokens; no env var needed.

# 3. Set the capture target and let it run for a few minutes.
export CAPTURE_DIR="./segments-test"
export CAPTURE_WATCHLIST_ID="verify"
# watchlist.txt must list at least one symbol, e.g. BUMI or BBCA.
/tmp/collector        # Ctrl-C after ~2 minutes

# 4. Inspect the resulting segment.
/tmp/sbcap stat ./segments-test/dt=$(date -u +%Y-%m-%d)/verify/seg-*.binpb.zst
```

A successful capture shows `running_trade > 0` in the "channels" line.  If it
still shows zero, confirm the collector log says:
  `[feed] subscribed runningTradeBatch+orderBook+liveprice+iepiev for N symbol(s)`
and check that the market is open (pre-open auction 08:55–09:00 WIB may send
IEPIEV but no running trades; continuous trading starts at 09:00).

## Run (dry-run — safe default)

```bash
export SB_USERNAME='your_stockbit_username'
export SB_PASSWORD='your_stockbit_password'
export SB_SYMBOL='BBCA'
go run .
```

Expected output (abridged):

```
[auth] main session ready (user_id="123456")
────────────────────────────────────────────
 Stockbit algo prototype — orderbook imbalance
 symbol=BBCA depth=5 threshold=0.30 lots/order=1
 caps: maxOrders=5 maxLots=5 maxNotional=10000000 cooldown=30s
 mode: DRY-RUN (no real orders)
────────────────────────────────────────────
[feed] connected to wss://wss-trading.stockbit.com/ws
[feed] subscribed order_book + liveprice for BBCA
[book] BID top=9500 x 120000 (10 levels) | imbalance=+0.412 ref=9500 (depth=5, thr=0.30) | signal=BUY
[broker] DRY-RUN BUY 1 lot(s) BBCA @ 9500  (POST /order/v2/buy {...})
```

> First login from a new machine may hit a device-verification challenge. Log in
> once interactively in the Stockbit app from this host/network first, or supply
> tokens directly via `SB_ACCESS_TOKEN` / `SB_REFRESH_TOKEN` (skips password login).

## Run (LIVE — real orders, real money)

Three things must all be true, by design:

```bash
export SB_USERNAME=... SB_PASSWORD=...
export SB_TRADING_PIN='123456'                 # trading (securities) session
export ALGO_LIVE=true
export ALGO_CONFIRM='I_UNDERSTAND_LIVE_TRADING' # exact phrase required
go run .
```

If `ALGO_LIVE=true` but `ALGO_CONFIRM` is not the exact phrase, it stays in
dry-run and tells you so. Start tiny: `ALGO_LOTS_PER_ORDER=1`, low
`ALGO_MAX_ORDERS`, and a small `ALGO_MAX_NOTIONAL_IDR`.

## Safety model

- **Dry-run by default** — no order leaves the process unless explicitly armed.
- **Two-key arming** — `ALGO_LIVE=true` *and* the exact `ALGO_CONFIRM` phrase.
- **Per-order caps** — `ALGO_MAX_LOTS`, `ALGO_MAX_NOTIONAL_IDR`. A breach trips
  the kill switch.
- **Session cap** — `ALGO_MAX_ORDERS` total, then the kill switch trips.
- **Cooldown** — `ALGO_COOLDOWN_SEC` between orders.
- **No stacking / no shorting** — won't add to an existing long; only sells what
  this bot bought.
- **Kill switch** — any order error or cap breach stops all further trading.

This is a **demo**, not production trading software: it doesn't reconcile real
fills/positions from the account, handle partial fills, or manage exits with
smart orders (the API for those is in [`../docs/03-order-placement.md`](../docs/03-order-placement.md)).

## Configuration reference

| Env var | Default | Meaning |
|---------|---------|---------|
| `SB_USERNAME` / `SB_PASSWORD` | — | Main account login |
| `SB_TRADING_PIN` | — | Trading PIN (required for live) |
| `SB_ACCESS_TOKEN` / `SB_REFRESH_TOKEN` | — | Use preset main tokens (skip login) |
| `SB_TRADING_ACCESS_TOKEN` / `SB_TRADING_REFRESH_TOKEN` | — | Use preset trading tokens (skip PIN) |
| `SB_SYMBOL` | `BBCA` | Symbol to trade |
| `SB_EXODUS_HOST` | `https://exodus.stockbit.com` | Auth host |
| `SB_CARINA_HOST` | `https://carina.stockbit.com` | Trading host |
| `SB_WS_URL` | `wss://wss-trading.stockbit.com/ws` | Market-data WebSocket |
| `SB_WS_SUBPROTOCOL` | `web` | WS sub-protocol (`web` / `ios`) |
| `ALGO_IMBALANCE_THRESHOLD` | `0.30` | \|imbalance\| needed to fire a signal |
| `ALGO_DEPTH_LEVELS` | `5` | Order-book levels summed for imbalance |
| `ALGO_LOTS_PER_ORDER` | `1` | Lots per order (1 lot = 100 shares) |
| `ALGO_COOLDOWN_SEC` | `30` | Min seconds between orders |
| `ALGO_MAX_ORDERS` | `5` | Session order limit (then kill switch) |
| `ALGO_MAX_LOTS` | `5` | Per-order lot cap |
| `ALGO_MAX_NOTIONAL_IDR` | `10000000` | Per-order notional cap |
| `ALGO_LIVE` | `false` | Enable live trading path |
| `ALGO_CONFIRM` | — | Must equal `I_UNDERSTAND_LIVE_TRADING` to send live |

## Using the generated Protobuf stubs instead (optional)

On Go ≥ 1.23 you can drop the hand-rolled codec and import the real stubs:

```go
// go.mod
require go.stockbit.io/protos v0.0.0
replace go.stockbit.io/protos => ../sb-protos
```

Then use `datafeed.WebsocketRequest`, `WebsocketChannel`, and
`WebsocketWrapMessageChannel` from
`go.stockbit.io/protos/gen/go/securities/transactional/datafeed/v1`, and marshal
with `google.golang.org/protobuf/proto`. The wire format is identical, so the
rest of the program is unchanged.
