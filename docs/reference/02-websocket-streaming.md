# Stockbit API — WebSocket Market-Data Streaming

This document describes how to stream **live price**, **running trade** (trade
ticks), and **orderbook** (bid/ask depth) from Stockbit over WebSocket, using the
access token from [01-authentication.md](./01-authentication.md), and how to keep
the stream alive indefinitely.

> **Prerequisite:** a valid **main account** access token (session #1). The
> market-data feed does *not* require the trading PIN session.

---

## 1. The feed at a glance

- **Transport:** WebSocket, **binary frames** (`binaryType = 'arraybuffer'`).
- **Encoding:** Protocol Buffers. Protobuf package:
  `securities.transactional.datafeed.v1` (plus entity packages for the data
  messages). The TS package is `@stockbitgroup/protos/...`. **The canonical
  contracts are now vendored in this repo** under
  [`sb-protos/proto/securities/transactional/datafeed/`](../sb-protos/proto/securities/transactional/datafeed)
  with generated Go stubs in `sb-protos/gen/go/...` — these are the source of
  truth this doc is verified against (see §5).
- **One socket, many channels:** a single connection multiplexes price, running
  trade, orderbook, intraday, IEP/IEV, watchlist, movers, etc. You subscribe by
  sending one request that lists symbols per channel.

Sources (canonical contracts):
`sb-protos/proto/securities/transactional/datafeed/v1/websocket.proto`,
`.../datafeed/consumer/entity/v1/entity.proto`,
`.../datafeed/v1/running_trade.proto`, `.../datafeed/v1/orderbook.proto`,
`.../datafeed/v1/ping.proto`. Client usage:
`sb-ios/Networking/.../WebSocket/Trading/TradingSocketManager.swift`,
`sb-web/src/features/chartbit/TradingView/natsSocket.ts`,
`sb-android` `RemoteModule.kt` (Scarlet).

---

## 2. Connect

### URL (production)

```
wss://wss-trading.stockbit.com/ws
```

| Client | Host | Sub-protocol |
|--------|------|--------------|
| iOS | `wss://wss-trading.stockbit.com/ws` | `ios` |
| Android | `wss://wss-jkt.trading.stockbit.com/ws` | (Scarlet/OkHttp) |
| Web | `process.env.NEXT_PUBLIC_DATAFEED_SOCKET` (+ `?type=chart`) | `web` |

(Development: `wss://wss-trading.lab.stockbit.com/ws`.)

The clients pass a **WebSocket sub-protocol** identifying the platform. Send one,
e.g. `web`:

```python
ws = websocket.WebSocketApp("wss://wss-trading.stockbit.com/ws",
                            subprotocols=["web"])
```

Source: `sb-ios/Networking/.../Endpoints/WebSocketURL.swift` (host/path/
`Sec-WebSocket-Protocol: ios`), `sb-web/.../WebSocketV2/index.ts:33-51`
(`protocols = ['web']`).

### Dynamic host migration (optional but real)

Both iOS and web look up a Flipt feature flag (`findataWebSocketURL` /
`JKT_TRADING_SOCKET`, namespace `financialData`) at startup that can **override
the host** (e.g. route you to the Jakarta `wss-jkt...` server). If your
connection is refused or immediately closed, fall back to the alternate host.
Source: `sb-ios/.../WebSocket/Trading/TradingSocketServer.swift:29-74`,
`sb-web/.../WebSocketV2/index.ts:33-51`.

---

## 3. Authenticate the socket

There is **no token in the URL**. Immediately after the socket opens, send a
Protobuf `WebsocketRequest` carrying the auth key + your access token.

### Step 1 — fetch a WebSocket key (REST)

```
GET https://exodus.stockbit.com/auth/websocket/key
Authorization: Bearer <main access token>
```

```json
{ "data": { "key": "<websocket_key>" } }
```

Source: `sb-web/src/lib/api/ws.ts:7-8`,
`sb-ios/.../WebSocket/WebSocketAPIWorker/WebSocketEndpoint.swift`.

### Step 2 — send the auth message (Protobuf, binary)

```
WebsocketRequest {
  key:         "<websocket_key>"          // from /auth/websocket/key
  userId:      "123456"                    // your numeric user id, as a STRING
  accessToken: "<access token WITHOUT 'Bearer '>"
}
```

Serialize to binary and send. The same three fields (`key`, `userId`,
`accessToken`) accompany the auth and are also set on subscribe requests.

```js
// sb-web/src/features/chartbit/TradingView/natsSocket.ts:81-99
const buffer = new WebsocketRequest({ key, userId, accessToken }).toBinary();
socket.send(buffer);
```

> `accessToken` is the **raw token, without the `Bearer ` prefix**
> (`getTokenWithoutBearer()` in web; `keychainSession.getAccessToken()` in iOS).

---

## 4. Subscribe to channels

Send a `WebsocketRequest` whose `channel` (a `WebsocketChannel`) lists the
**symbols per channel**. The server then streams updates for those symbols.

### `WebsocketChannel` fields

| Field | Type | Streams |
|-------|------|---------|
| `liveprice` | `string[]` | **Live price** (last price, OHLC, volume, foreign buy/sell) |
| `runningTrade` | `string[]` | **Running trade** — individual trade ticks |
| `runningTradeBatch` | `string[]` | Running trade, batched (lower overhead for multi-symbol) |
| `orderBook` | `string[]` | **Orderbook** — bid/ask depth ladder |
| `bestBidOffer` | `string[]` | Best bid / best offer only (top of book) |
| `iepiev` | `string[]` | Indicative equilibrium price/volume (pre-open auction) |
| `intraday` | `string[]` | Intraday OHLC candles |
| `watchlist` | `string[]` | Aggregated watchlist price updates |
| `isHotlist` | `bool` | Top-20 hotlist |
| `marketMover` | `MarketMoverWebsocketRequest[]` | Market movers |
| `orderQueue` | `OrderQueueWebsocketRequest[]` | Order queue depth analysis |

**Symbol format:** plain uppercase ticker, no exchange suffix — e.g. `"BBCA"`,
`"BMRI"`, `"TLKM"`. Source: `websocket.pb.swift` `WebsocketChannel`,
`TradingSocketManager.swift:172-187`.

### Subscribe message

```
WebsocketRequest {
  userId:      "123456"
  accessToken: "<access token>"
  channel: WebsocketChannel {
    liveprice:    ["BBCA", "BMRI"]
    runningTrade: ["BBCA", "BMRI"]
    orderBook:    ["BBCA", "BMRI"]
  }
}
```

### How subscription state works (important)

The `channel` you send **replaces** the current subscription set — it is *not*
incremental. The clients keep a single `requestChannel` object, mutate the
relevant array, and resend the whole thing:

- **Add/replace** symbols → resend with the full desired list per channel.
- **Unsubscribe** a channel → resend with that array set to `[]` (empty).

```swift
// sb-ios TradingSocketManager.swift — subscribe everything for one symbol
requestChannel.runningTradeBatch = [symbol]
requestChannel.orderBook         = [symbol]
requestChannel.liveprice         = [symbol]
requestChannel.intraday          = [symbol]
requestChannel.bestBidOffer      = [symbol]
sendSocketRequest(requestChannel: requestChannel)   // resends full state
```

Keep your own authoritative copy of the desired subscription set so you can
resend it verbatim after a reconnect (see §6).

---

## 5. Incoming messages

Every binary frame from the server decodes as a single
`WebsocketWrapMessageChannel`, which is a **oneof** — exactly one of these is
set per frame. Switch on which field is present.

```
WebsocketWrapMessageChannel {            // field numbers from websocket.proto
  oneof message_channel {
    running_trade        = 1   // trade tick (datafeed.v1.RunningTrade)
    ping                 = 2   // heartbeat reply (see §6)
    top20                = 3
    watchlist            = 4
    orderbook_header     = 5   // structured depth header (see orderbook note)
    orderbook_body       = 6   // structured depth body   (see orderbook note)
    error                = 7   // { code, message } — code 401 => re-auth (§6)
    running_trade_batch  = 8
    liveprice            = 9   // consumer.entity.v1.LivePrice
    orderbook            = 10  // consumer.entity.v1.Orderbook  ← what order_book delivers
    iepiev               = 11
    intraday             = 12
    best_bid_offer       = 13  // top of book (BestBidOfferWS)
    market_mover_batch   = 14
    order_queue          = 15
  }
}
```

Source (canonical): `sb-protos/proto/securities/transactional/datafeed/v1/websocket.proto:43-61`.
Client dispatch: `TradingSocketManager.swift:709-742`,
`sb-web/.../natsSocket.ts:122-156`.

### `liveprice` → `LivePrice`

This is `consumer.entity.v1.LivePrice` (oneof field 9), the WS variant:

```
stock_code : string
lastprice  : double
volume     : double
high/low/open/close/prev/average : double
frequency  : double
frg_buy    : double      // foreign buy
frg_sell   : double      // foreign sell
value      : double
change     : Change { value: double, percentage: double }   // field 15
order_verb : string
quantity   : int64
is_index   : bool
sequence_number, order_book_id, order_number, match_number : int64
board      : BoardType   // RG / TN / NG ...
source     : Source
itch_incoming_time : string  // RFC3339Nano
```

Source (canonical): `sb-protos/proto/securities/transactional/datafeed/consumer/entity/v1/entity.proto:158-188`.

### `runningTrade` → `RunningTrade` (trade tick)

```
stock         : string
price         : double
volume        : double
action        : TradeType   // buy / sell / unspecified (aggressor side)
trade_number  : int32
market_board  : BoardType
time          : google.protobuf.Timestamp   // exchange trade time
websocket_time: google.protobuf.Timestamp   // server emit time
change        : Change { value, percentage }
is_global     : bool
```

(No `value` field — multiply `price * volume` yourself if you need turnover.)
`runningTradeBatch` → `RunningTradeBatch { batch: RunningTrade[] }` — same record,
delivered in arrays. Prefer the batch channel when subscribing to many symbols.
Source (canonical): `sb-protos/proto/securities/transactional/datafeed/v1/running_trade.proto:13-30`.

### `orderbook` → `Orderbook` (bid/ask depth)

The `order_book` channel delivers the **consumer `Orderbook`** message (oneof
field **10**), whose depth ladder is packed into a single **string** `body`:

```
stock_code     : string
body           : string     // pipe-delimited depth ladder — format below
sequence_number: int64      // monotonic; use to detect gaps / ordering
order_book_id  : int64      // only filled on the ITCH source
datetime       : string     // RFC3339Nano
source         : Source
switch_reset   : SwitchReset // feed-source switch marker (resync hint)
board          : BoardType
```

**The `body` string format** (verified against the parsers in all three clients:
iOS `WatchlistInteractor`, Android `OrderBookMapperV2`, web `utils/webSocket.js`):

```
#O|<SYMBOL>|<SIDE>|<price>;<queue>;<volume>;|<price>;<queue>;<volume>;|…|<unixTime>
```

- Outer delimiter `|`; per-level delimiter `;` (note the trailing `;` in each level).
- `#O` is the orderbook token; `<SYMBOL>` is at index 1; `<SIDE>` (`BID` or
  `OFFER`) at index 2.
- Levels run from index 3 up to the final element, which is a unix timestamp.
- Each level is `price ; queue_count ; volume` (volume in **shares**).
- **BID and OFFER arrive as two separate `orderbook` messages** — cache the most
  recent of each side to assemble a full book.

Example (one OFFER message, 3 levels shown):

```
#O|BANK|OFFER|418;2;5700;|420;7;60900;|422;9;177700;|1663553794
```

Use `sequence_number` as the ordering key — if it jumps, you missed a frame.

> The schema *also* defines a structured pair `OrderBookHeader` (field 5) +
> `OrderBookBody` (field 6, with repeated `Bid{price,lot}` / `Offer{price,lot}`)
> in `orderbook.proto`. The official clients do **not** use these for the
> `order_book` channel — they consume the string-`body` `Orderbook` above. The
> lighter `best_bid_offer` channel (`BestBidOfferWS{ best_bid, best_offer }`,
> structured) is the option if you only need top-of-book.

Source (canonical):
`sb-protos/proto/securities/transactional/datafeed/consumer/entity/v1/entity.proto:190-205`
(consumer `Orderbook`),
`sb-protos/proto/securities/transactional/datafeed/v1/orderbook.proto`
(structured `OrderBookHeader`/`OrderBookBody`/`Bid`/`Offer`).

### Getting the Protobuf stubs

The wire format is Protobuf, so you need the message definitions to encode
requests / decode frames. Options, best first:

1. **Use the in-repo contracts (`sb-protos/`).** This is the source of truth.
   - Generated **Go** stubs already exist: `sb-protos/gen/go/securities/transactional/datafeed/v1`
     (and `.../consumer/entity/v1`). Import via a `replace go.stockbit.io/protos => ./sb-protos`
     (requires Go ≥ 1.23 — see `sb-protos/go.mod`).
   - Generate for any other language straight from
     `sb-protos/proto/...` with `buf generate` (config in `sb-protos/buf.gen.yaml`)
     or plain `protoc` (e.g. `protoc --python_out`). Key files:
     `securities/transactional/datafeed/v1/websocket.proto`,
     `.../consumer/entity/v1/entity.proto`,
     `.../datafeed/v1/running_trade.proto`, `.../datafeed/v1/orderbook.proto`,
     `.../datafeed/v1/ping.proto`.
2. **Reuse the published npm stubs:** `@stockbitgroup/protos` (what the web client
   imports), e.g. `.../securities/transactional/datafeed/v1/websocket_pb`.
3. **No stubs at all:** these messages are simple enough to encode/decode with a
   minimal hand-rolled Protobuf codec — see `collector/protobuf.go` +
   `collector/datafeed.go` in this repo for a zero-dependency example.

---

## 6. Keeping the stream alive ⭐

A long-running stream breaks for three reasons. Handle all three.

### (a) Idle timeout → heartbeat ping every 10s

If you stop sending, the server drops the connection. Send a ping
`WebsocketRequest` on a **10-second** interval (the value used by both iOS and
web):

```
WebsocketRequest { ping: { message: "ping" } }   // serialize to binary, send
```

The server replies with a frame whose `messageChannel` is `ping`
(`PingResponse { message }`). You don't need to act on the reply other than to
note the connection is healthy.

```js
// sb-web natsSocket.ts:115-120
const HEARTBEAT_DURATION = 10000;
socket.send(new WebsocketRequest({ ping: { message: 'ping' } }).toBinary());
```

Source: `sb-ios/.../TradingSocketManager.swift:104-110, 451-462`
(`timerInterval = 10`, `PingRequest.message = "ping"`),
`sb-web/.../natsSocket.ts:30,115-120`.

### (b) Access token expiry → refresh + re-auth

The access token embedded in the auth/subscribe messages expires. When it does,
the server sends an `error` frame with **code `401`**. Handle it:

1. Refresh the access token (`POST /login/refresh` — see
   [auth doc §3](./01-authentication.md#3-keeping-the-session-alive--token-refresh-)).
2. Re-fetch the WebSocket key (`GET /auth/websocket/key`).
3. Resend the auth message with the new key + token.
4. Resend your subscription `channel`.

```js
// sb-web natsSocket.ts:106-156 — on error code 401
if (messageCase === 'error' && value.code === 401) handleUnauthorized();
// handleUnauthorized(): re-fetch key, re-send auth, re-send channel (max 3 retries)
```

**Better: refresh proactively** (before `access.expired_at`) so you never receive
a 401 in the first place — then just resend the auth+subscribe messages with the
new token without dropping the socket. Source: `natsSocket.ts:104-113`,
`handleAuth()/updateChannel()`.

### (c) Network drop → auto-reconnect with backoff

On `close`/`error`, reconnect with exponential backoff and **re-run the full
handshake** (auth → subscribe). Parameters from the clients:

- **Backoff (iOS):** capped exponential, base interval `0.75s`, cap `3s`
  (`SBSocketRetrier(maxCappedInterval: 3, interval: 0.75)`).
- **Do-not-retry:** close code **`4000`** = intentional shutdown; do not reconnect.
- **Only reconnect when appropriate:** iOS reconnects only if the app is active
  and the user is logged in; web does not retry when `navigator.onLine === false`.
- **Web retry ladder:** a few immediate reconnects, then fall back to slower
  polling-style retries (don't hammer the server).

```
on close(code):
    if code == 4000: return                 # graceful shutdown
    stop heartbeat timer
    sleep(min(cap, base * 2**attempt))       # base=0.75s, cap=3s
    reconnect(); on open -> auth(); resubscribe(saved_channel); start heartbeat
```

Source: `sb-ios/.../WebSocket/Monitoring/SBSocketRetrier.swift` +
`TradingSocketManager.didReceive` (`.peerClosed -> reconnectIfCould`),
`sb-web/.../natsSocket.ts:31` (`DO_NOT_RETRY_CODE = 4000`), close handler.

### Lifecycle summary

```
                ┌─────────────── every 10s ───────────────┐
                ▼                                          │
  connect → auth(key,userId,token) → subscribe(channel) → ping ──► healthy
     ▲                                   │                          │
     │                                   │ error 401                │ close (≠4000)
     │                                   ▼                          ▼
     │                      refresh token + re-key            backoff 0.75s→3s
     │                      re-auth + re-subscribe                  │
     └──────────────────────────────────────────────────────── reconnect
```

---

## 7. End-to-end pseudocode

```python
import time, threading, websocket
# from stockbit_protos import WebsocketRequest, WebsocketWrapMessageChannel  # generated stubs

DESIRED = {
    "liveprice":    ["BBCA", "BMRI"],
    "runningTrade": ["BBCA", "BMRI"],
    "orderBook":    ["BBCA", "BMRI"],
}

def ws_key(access):            # GET /auth/websocket/key
    return GET("https://exodus.stockbit.com/auth/websocket/key",
               headers={"Authorization": f"Bearer {access}"}).json()["data"]["key"]

def on_open(ws):
    access = get_valid_access_token(state)       # refreshes if near expiry (auth doc §3)
    key    = ws_key(access)
    ws.send(WebsocketRequest(key=key, userId=str(state.user_id),
                             accessToken=access).SerializeToString(), OPCODE_BINARY)
    ws.send(WebsocketRequest(userId=str(state.user_id), accessToken=access,
                             channel=DESIRED).SerializeToString(), OPCODE_BINARY)
    start_heartbeat(ws)                          # every 10s

def start_heartbeat(ws):
    def beat():
        while ws.connected:
            ws.send(WebsocketRequest(ping={"message": "ping"}).SerializeToString(), OPCODE_BINARY)
            time.sleep(10)
    threading.Thread(target=beat, daemon=True).start()

def on_message(ws, raw):
    msg = WebsocketWrapMessageChannel.FromString(raw)
    which = msg.WhichOneof("messageChannel")
    if which == "error" and msg.error.code == 401:
        reauthenticate(ws)                       # refresh + re-key + re-auth + re-subscribe
    elif which == "liveprice":     handle_price(msg.liveprice)
    elif which == "runningTrade":  handle_tick(msg.runningTrade)
    elif which == "orderbook":     handle_depth(msg.orderbook)
    # ... ping / others

def on_close(ws, code, _):
    if code == 4000: return                      # graceful, do not retry
    backoff_then_reconnect()                     # 0.75s -> cap 3s, then re-run on_open

ws = websocket.WebSocketApp("wss://wss-trading.stockbit.com/ws",
                            subprotocols=["web"],
                            on_open=on_open, on_message=on_message, on_close=on_close)
ws.run_forever()
```

---

## 8. Related streams (for completeness)

These are *separate* sockets, documented briefly since they're relevant to
trading bots but **not** needed for market data:

- **Portfolio / orders WS** — `wss://ws-gen.stockbit.com/v1`. Auth via
  **cookie**: `Cookie: gen-websocket=<trading access token>` (session #2).
  Protobuf package `platform.websocket.wsevent.v1`. Subscribe by sending a
  `WebsocketRequest` with `subscribe = CHANNEL_SECURITIES_LIVE_PORTO` (portfolio)
  or `CHANNEL_NEGO_ENGINE_ORDER_BOOK`; ping via `command = COMMAND_PING`. Streams
  `portfolioSummary`, `portfolioList`, `portfolioDetail`, `orderList`,
  `orderDetail`. Heartbeat ~15s. Source: `sb-ios/.../WebSocket/Portfolio/
  PortfolioSocketManager.swift`, `sb-web/src/global/Securities/websocket/*`,
  `sb-android/data/protobuf/.../wsevent/v1/*.proto`.
- **Social WS** — `wss://wssocial.stockbit.com?wskey=<key>`. Chat/notifications.
  Ping ~20s.

---

## Quick reference

| Concern | Value |
|---------|-------|
| Market-data WS | `wss://wss-trading.stockbit.com/ws` (sub-protocol `web`/`ios`) |
| WS key | `GET exodus.stockbit.com/auth/websocket/key` (`Bearer <access>`) |
| Auth msg | `WebsocketRequest{ key, userId, accessToken }` (binary) |
| Subscribe | `WebsocketRequest{ userId, accessToken, channel:{ liveprice, runningTrade, orderBook } }` |
| Price | `WebsocketWrapMessageChannel.liveprice` → `LivePrice` |
| Running trade | `.runningTrade` / `.runningTradeBatch` → `RunningTrade` |
| Orderbook | `.orderbook` → consumer `Orderbook`; `body` = `#O\|SYM\|BID/OFFER\|price;queue;volume;\|…\|ts` (BID & OFFER are separate messages) |
| Heartbeat | `WebsocketRequest{ ping:{ message:"ping" } }` every **10s** |
| Re-auth | on `error.code == 401` → refresh token, re-key, re-auth, re-subscribe |
| Reconnect | backoff `0.75s → 3s`; skip close code `4000` |

Back to [Authentication](./01-authentication.md) · [Index](../README.md)
