# Stockbit API — Order Lifecycle & State Feedback

You have POSTed an order (see [03-order-placement.md](./03-order-placement.md)). This document describes **how you find out what happened to it**: the real-time update channel, the status state machine, how fills accumulate, rejection reasons, and how to fall back to polling when the socket is unavailable.

> **Prerequisite:** the **trading (securities) session** from
> [01-authentication.md](./01-authentication.md#4-trading-securities-account-login).
> Order state flows over the **portfolio WebSocket** (`wss://ws-gen.stockbit.com/v1`),
> which is separate from the market-data socket documented in
> [02-websocket-streaming.md](./02-websocket-streaming.md).

All facts below are reverse-engineered from and cross-verified across the three
official clients (`sb-web`, `sb-ios`, `sb-android`) and the Protobuf contracts in
`sb-protos`. Each section cites its source.

---

## 1. Two WebSocket channels — which one carries order state

The market-data socket (`wss://wss-trading.stockbit.com/ws`) streams **price,
running trade, and orderbook** (doc 02). It carries no information about *your*
orders.

Order state — open, partial fill, full fill, cancel, rejection — arrives on a
**separate socket**:

| Property | Market-data socket | Portfolio / orders socket |
|----------|-------------------|--------------------------|
| URL (prod) | `wss://wss-trading.stockbit.com/ws` | `wss://ws-gen.stockbit.com/v1` |
| Auth | `WebsocketRequest{ key, userId, accessToken }` (binary) | Cookie header: `gen-websocket=<trading_access_token>` |
| Session required | Main account (session #1) | **Trading account (session #2)** |
| Protobuf package | `securities.transactional.datafeed.v1` | `platform.websocket.wsevent.v1` |
| What it delivers | Price, trade ticks, orderbook | Portfolio, order list, order detail |
| Heartbeat | 10 s | **15 s** |

Sources:
`sb-ios/Networking/.../Endpoints/WebSocketURL.swift:114-115` (host `ws-gen.stockbit.com`),
`sb-ios/Networking/.../Portfolio/PortfolioSocketConstants.swift:19` (`cookieKeyword = "gen-websocket"`),
`sb-ios/Networking/.../Portfolio/PortfolioSocketManager.swift:414-416` (cookie set as `gen-websocket=<token>`),
`sb-ios/Networking/.../Portfolio/PortfolioSocketPingWorker.swift:22` (ping interval `15` seconds),
`sb-web/src/global/Securities/websocket/utils.ts:41` (`generateWSCookie`),
`sb-web/src/global/Securities/websocket/constants.ts:3` (`HEARTBEAT_INTERVAL = 15000`).

---

## 2. Connecting and subscribing (portfolio socket)

### 2.1 Connect with the trading token as a cookie

```
wss://ws-gen.stockbit.com/v1
Cookie: gen-websocket=<trading_access_token>
```

The trading access token must **not** include the `Bearer ` prefix — strip it
before setting the cookie value. Source:
`sb-ios/Networking/.../Portfolio/PortfolioSocketManager.swift:94`
(`replacingOccurrences(of: "Bearer ", with: "")`).

### 2.2 Subscribe to order/portfolio updates

After the connection opens, send a binary-encoded `WebsocketRequest`
(`platform.websocket.wsevent.v1.WebsocketRequest`) to subscribe to your
portfolio and orders:

```
WebsocketRequest {
  requests: [
    Request {
      value: <account_number encoded as UTF-8 bytes>
      subscribe: CHANNEL_SECURITIES_LIVE_PORTO   // = 1
    }
  ]
}
```

`subscribe` is a `Channel` enum; field `1` = `CHANNEL_SECURITIES_LIVE_PORTO`.

Sources:
`sb-protos/proto/platform/websocket/wsevent/v1/websocket.proto:25-28` (Channel enum),
`sb-web/src/global/Securities/websocket/utils.ts:25-31` (`createSubscribePayload`),
`sb-ios/Networking/.../Portfolio/PortfolioSocketManager.swift:169-174` (`subscribeLivePortfolio`),
`sb-web/src/global/Securities/websocket/store/securities.store.types.ts:22-23` (`SECURITIES_LIVE_PORTO = 1`).

### 2.3 Heartbeat — every 15 seconds

Send a `WebsocketRequest` containing a ping command:

```
WebsocketRequest {
  requests: [
    Request {
      value: <PingRequest{ message: "PING" } serialized>
      command: COMMAND_PING   // = 1
    }
  ]
}
```

Source: `sb-ios/Networking/.../Portfolio/PortfolioSocketPingWorker.swift:21-23`
(`pingInterval = .seconds(15)`, `pingMessage = "PING"`),
`sb-protos/proto/platform/websocket/wsevent/v1/ping.proto`.

### 2.4 Incoming frame structure

Every binary frame decodes as `platform.websocket.wsevent.v1.WebsocketResponse`:

```
WebsocketResponse {
  timestamp: google.protobuf.Timestamp
  oneof response {
    ping       = 2   // PingResponse — heartbeat reply
    auth       = 3   // AuthResponse — re-authentication acknowledgement
    securities = 4   // securities.trm.core.portfolio.wsevent.v1.WebsocketResponse
  }
}
```

All order and portfolio data arrives in the `securities` field, which is itself
a `oneof`:

```
securities.trm.core.portfolio.wsevent.v1.WebsocketResponse {
  action: Action { ACTION_UPSERT = 1, ACTION_DELETE = 2 }
  oneof message {
    portfolio_summary       = 2
    portfolio_detail        = 3
    portfolio_list          = 4
    order_detail            = 5   // ← single order update
    order_list              = 6   // ← full order list snapshot
    portfolio_detail_day_trade = 7
    nego_engine_order_book  = 8
  }
  event: Event { EVENT_NEW_CONNECTION = 1, EVENT_NEW_TRANSACTION_UPDATE = 2 }
}
```

Sources:
`sb-protos/proto/platform/websocket/wsevent/v1/websocket.proto:41-49`,
`sb-protos/proto/securities/trm/core/portfolio/wsevent/v1/securities.proto:13-42`.

**For order feedback, switch on `order_detail` (field 5) and `order_list` (field 6).**
The web client maps these to `SecuritiesMessageCase.ORDER_DETAIL` and
`SecuritiesMessageCase.ORDER_LIST` and calls `Transform.orderDetail()` on each
update. Source:
`sb-web/src/global/Securities/websocket/constants.ts:14-15`,
`sb-web/src/global/Securities/websocket/usePortfolio/usePortfolioReceiver.ts:159-229`.

---

## 3. Order status state machine

### 3.1 Core status enum (event layer)

The event contract (`securities.trm.core.transaction.event.v1.StatusUpdate.Transaction.Status`)
is the source of truth for what the server emits in a status update event:

```text
STATUS_OPEN             = 0   // order acknowledged by exchange
STATUS_PARTIALLY_FILLED = 1   // one or more partial executions, remainder open
STATUS_FILLED           = 2   // fully executed
STATUS_CANCELLED        = 4   // cancelled by client request or by IDX at end-of-day
STATUS_REPLACED         = 5   // an amend was accepted; order_id is now the new order
STATUS_REJECTED         = 8   // order was not accepted
STATUS_MANUAL_APPROVAL  = 19  // pending back-office review
STATUS_BASKET_AMEND     = 101 // amend queued in basket
STATUS_BASKET           = 102 // new order queued in basket
STATUS_SENDING          = 103 // being forwarded to exchange
```

Source: `sb-protos/proto/securities/trm/core/transaction/event/v1/status_update.proto:107-124`.

The entity-layer enum (`securities.trm.core.order.entity.v1.Status`, used in
`OrderStatusUpdate` and `OrderForceSell` events) has the same semantics under
slightly different constant names:

```text
STATUS_UNSPECIFIED  = 0
STATUS_BASKET       = 1
STATUS_BASKET_AMEND = 2
STATUS_SENDING      = 3
STATUS_OPEN         = 4
STATUS_PARTIALLY_FILLED = 5
STATUS_FILLED       = 6
STATUS_CANCELLED    = 7
STATUS_REPLACED     = 8
STATUS_REJECTED     = 9
```

Source: `sb-protos/proto/securities/trm/core/order/entity/v1/entity.proto:8-19`.

The risk-management layer adds a coarser summary (`OrderStatus`) used internally
to track lifecycle from the risk engine's perspective:

```text
ORDER_STATUS_INIT     = 1   // order just created
ORDER_STATUS_PARTIAL  = 2   // partially executed
ORDER_STATUS_SUCCESS  = 3   // fully executed
ORDER_STATUS_REJECTED = 4
ORDER_STATUS_DELETED  = 5   // cancelled or expired
```

Source: `sb-protos/proto/securities/risk_management/order/entity/v1/order_update.proto:12-19`.

### 3.2 Client-facing status labels (web)

The web client presents a string-based `OrderStatus` enum to the UI. These are
the values you will see in `status_text` from REST calls and in the
`status` field of transformed WS messages:

```text
Open           = "OPEN"
Partial        = "PARTIAL"
Match          = "MATCH"          // fully filled
Amended        = "AMENDED"
Withdrawn      = "WITHDRAWN"      // cancelled
Rejected       = "REJECTED"
Ready          = "READY"          // smart order waiting for trigger
Executed       = "EXECUTED"       // smart order triggered & placed
Active         = "ACTIVE"
Pending        = "PENDING"
RequestAmend   = "REQUEST AMEND"
RequestCancel  = "REQUEST CANCEL"
Succeeded      = "SUCCEEDED"
```

Source: `sb-web/src/global/Securities/types/transaction.ts:47-61`.

### 3.3 State-machine diagram (regular limit order)

```
                      POST /order/v2/buy
                            │
                    ┌───────▼────────┐
                    │ BASKET/SENDING │  (queued in basket, being forwarded to IDX)
                    └───────┬────────┘
                            │ IDX acknowledges
                    ┌───────▼────────┐
                    │     OPEN       │  order visible in book
                    └──┬──────────┬──┘
         partial match │          │ cancel/amend
          ┌────────────▼──┐  ┌────▼────────────────┐
          │ PARTIALLY_    │  │  CANCELLED /         │
          │  FILLED       │  │  REPLACED            │
          └────────────┬──┘  └─────────────────────-┘
       remaining fills │
                ┌──────▼──────┐
                │   FILLED    │  all lots executed
                └─────────────┘

   At any stage from BASKET onward:
                ┌─────────────┐
                │  REJECTED   │  IDX or broker rejected the order
                └─────────────┘
```

**Important IDX behaviour:** IDX sometimes emits a `ACTION_PARTIAL` matching
event *after* the full-match event. Do not assume `ACTION_FULL_MATCH` is the
final event — watch for `STATUS_FILLED`/`lot_done == lot_total` to confirm
completion. Source:
`sb-protos/proto/securities/trm/core/transaction/event/v1/status_update.proto:193`
(comment: "Partial Event sometimes can be notified after full match, this is IDX behaviour").

---

## 4. Matching / fill events

Fill details arrive as `StatusUpdate.Transaction.Matching` inside the event
layer. The key fields are:

```
Matching {
  action:              Action { ACTION_PARTIAL = 1, ACTION_FULL_MATCH = 2 }
  order:               BaseOrder   // the full order snapshot at time of match
  match_shares:        int64       // shares matched in THIS execution only
  match_remain_shares: int64       // shares still open after this execution
  match_price:         int64       // execution price (integer IDR)
  idx_trade_id:        string      // IDX trade ID; unique per execution leg
  total_match_shares:  int64       // cumulative matched shares across all legs
}
```

Source: `sb-protos/proto/securities/trm/core/transaction/event/v1/status_update.proto:190-207`.

### 4.1 Tracking fill progress from the portfolio socket

The `order_detail` / `order_list` messages carry the accumulated fill state in
the `Qty` sub-message:

```
Order.Qty {
  lot_done:  double   // lots matched so far (previously order_done)
  lot_open:  double   // lots still open in the book (previously order_open)
  lot_total: double   // total lots in the original order (previously order_total)
}
```

Invariant: `lot_done + lot_open == lot_total` while the order is live.

Source: `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:250-254`.

Matched amounts accumulate in `Order.Amount`:

```
Order.Amount {
  invested:       double   // amount reserved when order was placed
  matched:        double   // amount matched in most recent execution
  matched_total:  double   // cumulative matched amount across all legs
  open:           double   // amount still reserved for open quantity
}
```

Source: `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:173-178`.

The running average execution price is in `Order.Price.Average`:

```
Order.Price.Average {
  price: double   // volume-weighted average execution price (previously price_average)
  fee:   double   // fee component of the average (previously price_average_fee)
}
```

Source: `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:183-187`.

The web client's `OrderStateItem` interface mirrors these under camelCase field
names (`order_done`, `order_open`, `order_total`, `amount_matched`,
`amount_matched_total`, `amount_open`, `price_average`, `price_average_fee`).
Source: `sb-web/src/global/Securities/types/transaction.ts:218-228`.

---

## 5. Rejection reasons

There are two places rejection detail can arrive.

### 5.1 Order-event layer — `OrderReject`

```
OrderReject {
  order_id: string   // the order being rejected
  acc_no:   string   // account number
  message:  string   // human-readable rejection reason from IDX or broker
}
```

Source: `sb-protos/proto/securities/trm/core/order/event/v1/update.proto:34-38`.

### 5.2 Transaction event layer — `Rejection`

Inside `StatusUpdate.Transaction`, the `rejection` sub-message carries both
the rejection action type and the raw IDX message:

```
Rejection {
  action:         Action {
    ACTION_CANCEL_REJECT        = 1  // cancel request was rejected
    ACTION_AMEND_REJECT         = 2  // amend request was rejected
    ACTION_NEW_REJECT           = 3  // new order rejected at submission
    ACTION_DECLINE_COUNTER_PARTY = 4 // nego-board: counterparty declined
  }
  order:          BaseOrder
  reject_message: string   // IDX or broker rejection text
  prev_order:     PrevOrder  // only for ACTION_AMEND_REJECT
}
```

Source: `sb-protos/proto/securities/trm/core/transaction/event/v1/status_update.proto:174-189`.

### 5.3 Error types at the API layer

The following typed errors can be returned synchronously when POSTing an order
(i.e. HTTP 4xx with a typed Protobuf body), before the order even reaches IDX:

| Error type | Trigger |
|-----------|---------|
| `ErrorInvalidParameter` | Invalid request field (e.g. `ui_ref` > 40 chars, off-tick price) |
| `ErrorDuplicateOrder` | Same `ui_ref` submitted again — idempotency guard |
| `ErrorRejectSharia` | Order violates the account's sharia filter |
| `ErrorUserSuspended` | Account suspended (several variants below) |
| `ErrorUserSuspendedUnspecified` | Account suspended, reason unspecified |
| `ErrorUserSuspendedBackOffice` | Suspended by back-office |
| `ErrorUserSuspendedForcedSell` | Buy blocked due to forced-sell state |
| `ErrorAutomaticOrderAndGTCDisabled` | Panic button / emergency halt active |
| `ErrorInvalidMarketOrderStockCode` | Market order attempted on a rights-issue stock |
| `ErrorSystemError` | Internal server error |

Source: `sb-protos/proto/securities/trm/core/order/private/v2/error.proto:1-59`.

---

## 6. Polling fallback

If the WebSocket is unavailable or you need to reconcile state after a reconnect,
these REST endpoints return current order state (same trading token, same
`carina.stockbit.com` host as doc 03):

| Action | Endpoint | Key params |
|--------|----------|-----------|
| Order list | `GET /order/v2/list` | `filter_criteria.stock_code` (optional), `status`, `action`, `board_type` |
| Order detail | `GET /order/v2/detail` | `order_id=<id>` |
| Order summary | `GET /order/v2/summary` | `stock_code` |
| Trade history | `GET /history` | date range |

The detail response body is `OrderDetail` (same `Qty`, `Price.Average`, `Amount`
fields as the WS order messages). The `status_text` field carries one of the
string labels from §3.2.

Sources: `sb-web/src/lib/api/trade.ts:66-76` (`getOrderList`),
`sb-web/src/global/Securities/constants/apiEndpoint.ts:34-36` (`orderDetail: '/order/v2/detail'`),
`sb-android/.../api/securities/CarinaApi.kt:95-179`,
`sb-ios/.../Networking/Router/TradingV2APIRouter.swift:172-333`.

These endpoints are also listed in §5 of doc 03. Use them at startup or on
reconnect to fetch a full snapshot before the WS stream begins delivering deltas.

---

## 7. For an algo bot — practical implementation guide

### 7.1 Connection setup

1. Obtain a trading access token (doc 01, §4).
2. Open `wss://ws-gen.stockbit.com/v1` with the HTTP upgrade request carrying:
   ```
   Cookie: gen-websocket=<trading_access_token_without_bearer_prefix>
   ```
3. On `open`, send a subscribe `WebsocketRequest` with
   `subscribe = CHANNEL_SECURITIES_LIVE_PORTO` and `value` = your account number
   as UTF-8 bytes (source:
   `sb-web/src/global/Securities/websocket/utils.ts:25-31`).
4. Start a heartbeat timer: send `COMMAND_PING` every **15 seconds**.

### 7.2 Receiving order updates

Decode every binary frame as `platform.websocket.wsevent.v1.WebsocketResponse`.
Switch on `response`:

```python
msg = WebsocketResponse.FromString(raw)
which = msg.WhichOneof("response")

if which == "ping":
    pass  # heartbeat acknowledged

elif which == "securities":
    sec = msg.securities
    inner = sec.WhichOneof("message")

    if inner == "order_detail":
        handle_order_update(sec.action, sec.order_detail)

    elif inner == "order_list":
        for order in sec.order_list.orders:
            handle_order_update(sec.action, order)
```

### 7.3 Tracking fills

In `handle_order_update(action, order)`:

```python
def handle_order_update(action, order):
    if action == ACTION_DELETE:
        order_book.remove(order.order_id)
        return

    prev = order_book.get(order.order_id)
    new_lots_filled = order.qty.lot_done - (prev.qty.lot_done if prev else 0)

    if new_lots_filled > 0:
        log(f"{order.symbol}: +{new_lots_filled} lots @ avg {order.price.average.price}")

    order_book[order.order_id] = order

    # Terminal states — stop tracking
    if order.status_text in ("MATCH", "WITHDRAWN", "REJECTED"):
        finalize(order)
```

Key fill fields to track per order:
- `order.qty.lot_done` — cumulative lots executed so far
- `order.qty.lot_open` — lots remaining in the book
- `order.qty.lot_total` — original order size
- `order.price.average.price` — VWAP of all executions
- `order.amount.matched_total` — cumulative matched value in IDR

### 7.4 Handling rejections

```python
if order.status_text == "REJECTED":
    reason = order.message      # human-readable IDX rejection text
    log_rejection(order.order_id, reason)
    # Inspect for idempotency errors (ErrorDuplicateOrder) at the REST layer
    # before retrying; check reason text for "SUSPEND" to avoid retrying a
    # suspended account.
```

### 7.5 Reconnect and reconcile

On disconnect:

1. Reconnect with backoff (the iOS client uses 0.75 s base, 3 s cap — the same
   parameters as the market-data socket, source:
   `sb-ios/Networking/.../Portfolio/PortfolioSocketManager.swift:98`).
2. Re-send the cookie header (the token may have been refreshed during the outage
   — use the latest value).
3. Re-subscribe (`CHANNEL_SECURITIES_LIVE_PORTO`).
4. Immediately call `GET /order/v2/list` to reconcile any orders that changed
   state while disconnected — do not trust the in-memory book until you have
   reconciled.

Token expiry: the server may send an `auth` response event; re-send the subscribe
request with a refreshed token embedded in the cookie. Source:
`sb-protos/proto/platform/websocket/wsevent/v1/auth.proto`,
`sb-ios/Networking/.../Portfolio/PortfolioSocketManager.swift:391-398`
(`reauthenticate`).

### 7.6 Smart order status

Smart orders (stop loss, take profit, trailing stop, bracket, volume trigger)
have their own status field `smart_order.status` which uses
`SmartOrderStatusStr`:

```text
ORDER_STATUS_READY        // waiting for trigger condition
ORDER_STATUS_AMENDED
ORDER_STATUS_EXECUTED     // trigger fired, child order placed
ORDER_STATUS_WITHDRAWN
ORDER_STATUS_FORCE_DELETED
ORDER_STATUS_CORP_ACT_DELETED
ORDER_STATUS_EXPIRED
```

When a smart order fires, the resulting child limit order appears as a new
`order_detail` message. Track `smart_order_parent_id` to link the child back to
the originating smart order. Source:
`sb-web/src/global/Securities/types/transaction.ts:182-191`,
`sb-web/src/global/Securities/websocket/usePortfolio/usePortfolioReceiver.ts:216`
(child-order skip guard: `if (transformedData.smart_order_parent_id !== '') break`).

---

## 8. Quick reference

| Concern | Value |
|---------|-------|
| Orders/portfolio WS | `wss://ws-gen.stockbit.com/v1` (path `/v1`) |
| Auth | Cookie: `gen-websocket=<trading_token_no_bearer_prefix>` |
| Subscribe | `WebsocketRequest{ requests:[{ subscribe: CHANNEL_SECURITIES_LIVE_PORTO=1, value:<account_no_utf8> }] }` |
| Heartbeat | `COMMAND_PING` every **15 s** |
| Incoming frame | `WebsocketResponse{ securities: WebsocketResponse{ oneof message } }` |
| Order update | `securities.order_detail` → `Order{ qty, price.average, amount, status_text }` |
| Order snapshot | `securities.order_list` → `OrderList{ orders[] }` |
| Fill progress | `order.qty.lot_done` / `lot_open` / `lot_total` |
| Average price | `order.price.average.price` |
| Rejection reason | `order.message` (string from IDX/broker) |
| Polling fallback | `GET carina.stockbit.com/order/v2/list` · `/order/v2/detail?order_id=` |
| Reconnect | Backoff 0.75 s → cap 3 s; re-send cookie; re-subscribe; poll to reconcile |

Prev: [Order placement ←](./03-order-placement.md) · Index: [README](../README.md)
