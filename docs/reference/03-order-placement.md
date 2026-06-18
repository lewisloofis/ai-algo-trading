# Stockbit API — Order Placement (Buy, Sell & Smart Orders)

This document describes how to place and manage **trading orders** through the
Stockbit (Carina) trading backend: regular limit buy/sell, and the "smart
orders" — **stop loss, take profit, trailing stop, bracket (OCO), and volume
trigger** — plus amend, cancel, and order retrieval.

> ⚠️ **This places REAL orders against a REAL securities account with REAL
> money.** Everything here requires the **trading (securities) session** from
> [01-authentication.md](./01-authentication.md#4-trading-securities-account-login),
> not just the main account session. Test with small sizes, respect IDX price
> ticks and lot rules, and confirm you are permitted to automate before running
> in production. These are private, undocumented endpoints.

All facts below are reverse-engineered from and cross-verified across the three
official clients (`sb-web`, `sb-ios`, `sb-android`) and the Protobuf contracts in
`sb-protos`. Each section cites its source.

---

## 0. Host, auth, and the `ui_ref` convention

### Host

| Environment | Host |
|-------------|------|
| Production  | `https://carina.stockbit.com` |
| Pre-prod    | `https://carina.pre.stockbit.io` |
| Lab / dev   | `https://trading-core.lab.stockbit.com` |

Source: `sb-ios/Networking/Networking/Endpoints/BaseURL.swift:82-83`.

### Auth header

Send the **trading access token** (from the PIN login, *not* the main session
token):

```
Authorization: Bearer <trading_access_token>
Content-Type: application/json
```

> The web client sends `Bearer <token>`; the iOS client sets the raw token with
> no `Bearer ` prefix. If one form returns `401`, try the other.
> Source: `sb-web/src/config/requestTrading/index.ts:30-39`,
> `sb-ios/Networking/.../Base/SBSekuritasBaseAsyncAPIService.swift:36-37`,
> `sb-android/.../di/AuthInterceptorStockbitSecurities.kt`.

Keep this session alive with the trading-token refresh
(`POST https://carina.stockbit.com/auth/refresh`) exactly as described in the
auth doc — the same proactive + reactive strategy.

### `ui_ref` (client reference / idempotency)

**Every order-mutating request must include a `ui_ref`** — a client-generated
reference string. It de-duplicates accidental resubmits and lets the backend
attribute orders to a client. The Android client generates it as:

```
ui_ref = "A" + <epoch_millis> + <random_alphanumeric>
```

Generate a fresh `ui_ref` per distinct order action and reuse the same one only
when retrying that exact action. Source:
`sb-android/.../datasource/RemoteCarinaDataSource.kt` (`generateUiRef()`),
`sb-web/src/features/securities/utils` (`generateUiRef`).

### Units & validation

- **Shares vs lots:** the API takes **shares**. On IDX, `1 lot = 100 shares`, so
  send `shares = lots * 100`.
- **Price:** integer IDR, must follow the IDX price-tick (fraction) table. An
  off-tick price is rejected with `4xx`.
- **`board_type`:** `RG` = Regular, `TN` = Cash/Tunai (Negotiated uses the
  nego-engine, see §7). Source:
  `sb-web/src/global/Securities/constants/transactionForm.ts`.

---

## 1. Regular limit BUY / SELL

### Buy

```
POST /order/v2/buy
Authorization: Bearer <trading_access_token>
Content-Type: application/json
```

```json
{
  "ui_ref": "A1718524800123xK9",
  "symbol": "BBCA",
  "price": 9500,
  "shares": "100",
  "board_type": "RG",
  "is_gtc": false,
  "platform_order_type": "PLATFORM_ORDER_TYPE_LIMIT_DAY"
}
```

### Sell

```
POST /order/v2/sell
```

Same body shape as buy.

| Field | Type | Notes |
|-------|------|-------|
| `ui_ref` | string | Required. See §0. |
| `symbol` | string | Stock code, upper-case (e.g. `BBCA`). |
| `price` | number | Integer IDR, on-tick. |
| `shares` | string\|number | `lots * 100`. |
| `board_type` | string | `RG` or `TN`. |
| `is_gtc` | bool | `true` = Good-Till-Cancelled, `false` = Good-For-Day. |
| `platform_order_type` | string | See enum in §8. `..._LIMIT_DAY` for a normal day limit, `..._MARKET_FAK` for market. |
| `split_order` | object | *Optional.* `{ method, qty, min_lot, max_lot }` to slice a large order. |

**Response** (wrapped in `{ "data": ... }`):

```json
{ "data": { "order_id": "123456789", "order_limit_info": { "today_count": 3 } } }
```

Source: `sb-web/src/lib/api/trade.ts:185-275`,
`sb-ios/.../BuyPreview/Model/BuyPreviewModel.swift:21-50`,
`sb-android/.../api/securities/CarinaApi.kt:116-156`,
`sb-android/.../param/securities/PostOrderBuyV2DataParam.kt`.

### Preview before submitting (optional)

The nego flow has an explicit preview; for regular orders the clients compute
buying-power/fees locally from `GET /formula/v2`. There is no required preview
call for a plain limit order.

---

## 2. Smart orders

All smart orders live under `/smart-order/...` on the same Carina host and use
the same trading token. They share an `asset` object:

```json
"asset": {
  "code": "BBCA",
  "type": "ASSET_TYPE_STOCK_REGULAR",
  "market_board": "MARKET_BOARD_REGULER"
}
```

(Asset-type and market-board enums in §8.)

### 2.1 Stop Loss / Take Profit / Limit-If-Touched (the "stop order")

One endpoint serves all three; the `product_code` selects which.

```
POST /smart-order/stop-order/v1/order
```

```json
{
  "product_code": "ORDER_PRODUCT_CODE_STOP_LOSS",
  "asset": { "code": "BBCA", "type": "ASSET_TYPE_STOCK_REGULAR", "market_board": "MARKET_BOARD_REGULER" },
  "shares": "100",
  "trigger_type": "ORDER_TRIGGER_TYPE_LTE",
  "trigger_price": "9000",
  "order_side": "ORDER_SIDE_SELL",
  "order_price": "8950",
  "order_type": "ORDER_TYPE_LIMIT",
  "order_expiry_type": "ORDER_EXPIRY_TYPE_GTC",
  "ui_ref": "A1718524800999pQ2"
}
```

| Field | Values | Meaning |
|-------|--------|---------|
| `product_code` | `ORDER_PRODUCT_CODE_STOP_LOSS` / `_TAKE_PROFIT` / `_LIMIT_IF_TOUCHED` | Which smart order. |
| `trigger_type` | `ORDER_TRIGGER_TYPE_LTE` / `_GTE` | When to fire: `LTE` = when last price **≤** `trigger_price`; `GTE` = when last price **≥** `trigger_price`. |
| `trigger_price` | string | The price that arms/fires the order. |
| `order_side` | `ORDER_SIDE_BUY` / `_SELL` | Side of the order placed when triggered. |
| `order_price` | string (optional) | Limit price once triggered. Omit/empty for a market exit. |
| `order_type` | `ORDER_TYPE_LIMIT` / `_MARKET` | Order placed when triggered. |
| `order_expiry_type` | `ORDER_EXPIRY_TYPE_GFD` / `_GTC` / `_FAK` | Validity. |

**Worked semantics (long position on BBCA):**
- **Stop loss:** `product_code=STOP_LOSS`, `order_side=SELL`, `trigger_type=LTE`,
  `trigger_price` below current price → sells when the price drops to the stop.
- **Take profit:** `product_code=TAKE_PROFIT`, `order_side=SELL`,
  `trigger_type=GTE`, `trigger_price` above current price → sells into strength.

**Amend:** `PATCH /smart-order/stop-order/v1/order/{order_id}` with any subset of
`new_trigger_price`, `new_order_price`, `new_shares`, `new_trigger_type`,
`new_order_type`, `new_order_expiry_type`.

**Cancel:** `DELETE /smart-order/stop-order/v1/order/{order_id}`.

Source: `sb-web/src/lib/api/smartOrder.ts:35-75`,
`sb-web/src/global/Securities/types/api/smartOrder.ts:76-147`,
`sb-ios/.../SmartOrder/Entity/SmartOrderEndPoint.swift:31-38`,
`sb-android/.../api/SmartOrderApi.kt:37-58`.

### 2.2 Trailing Stop

A stop whose trigger follows the price by a percentage. **Sell-side** in the
clients.

```
POST /smart-order/trailing-stop/v1/order
```

```json
{
  "asset": { "code": "BBCA", "type": "ASSET_TYPE_STOCK_REGULAR", "market_board": "MARKET_BOARD_REGULER" },
  "shares": 100,
  "price_algorithm": {
    "type": "PRICE_ALGORITHM_TYPE_TRAILING_STOP_PERCENTAGE",
    "value": 5.0
  }
}
```

`value` is the trailing distance in percent (e.g. `5.0` = trail 5% below the peak).

- **Amend:** `PATCH /smart-order/trailing-stop/v1/order/{order_id}` with
  `new_shares` and/or `new_price_algorithm`.
- **Cancel:** `DELETE /smart-order/trailing-stop/v1/order/{order_id}`.

The order detail later reports the live `stop_price`. Source:
`sb-android/.../api/securities/CarinaApi.kt:119-148` +
`PostSellTrailingStopDataParam.kt`,
`sb-web/@types/securities/trading/order.d.ts:156-176`,
`sb-ios/.../Networking/Router/TradingV2APIRouter.swift:79-81`.

### 2.3 Bracket order (OCO: entry + stop loss + take profit)

A buy entry with two attached exits; when one exit fills, the other is cancelled.

```
POST /smart-order/bracket-order/v1/order
```

```json
{
  "asset": { "code": "BBCA", "type": "ASSET_TYPE_STOCK_REGULAR", "market_board": "MARKET_BOARD_REGULER" },
  "buy_order_price": "9500",
  "shares": "100",
  "stop_loss_trigger":   { "status": "ORDER_TRIGGER_STATUS_ACTIVE", "trigger_price": "9000" },
  "take_profit_trigger": { "status": "ORDER_TRIGGER_STATUS_ACTIVE", "trigger_price": "10000" },
  "ui_ref": "A1718524801777zT5"
}
```

Each leg's `status` is `..._ACTIVE` or `..._INACTIVE` (disable a leg to run a
single-sided bracket).

- **Amend parent:** `PATCH /smart-order/bracket-order/v1/order/{order_id}`
  (`buy_price`, `shares`, `stop_loss_trigger`, `take_profit_trigger`).
- **Cancel parent:** `DELETE /smart-order/bracket-order/v1/order/{order_id}`.

Source: `sb-android/.../api/SmartOrderApi.kt:21-35` +
`PostBracketOrderOrderDataParam.kt`,
`sb-ios/.../BracketOrder/BracketOrderAPIModel.swift:13-127`.

### 2.4 Volume trigger order

Fires when traded/queued volume at an evaluation level crosses a threshold.

```
POST /smart-order/volume-trigger-order/v1/order
```

```json
{
  "asset": { "code": "BBCA", "type": "ASSET_TYPE_STOCK_REGULAR", "market_board": "MARKET_BOARD_REGULER" },
  "shares": "100",
  "trigger_volume": "1000000",
  "order_price": "9500",
  "order_side": "ORDER_SIDE_BUY",
  "order_type": "ORDER_TYPE_LIMIT",
  "order_expiry_type": "ORDER_EXPIRY_TYPE_GFD",
  "ui_ref": "A1718524802555wR8"
}
```

- **Amend:** `PATCH /smart-order/volume-trigger-order/v1/order/{order_id}`
  (`new_*`).
- **Cancel:** `DELETE /smart-order/volume-trigger-order/v1/order/{order_id}`.

Source: `sb-android/.../api/SmartOrderApi.kt:60-74`,
`sb-ios/.../TriggerVolume/TriggerVolumeModel.swift:32-86`.

> **Legacy smart-order API (deprecated).** Older clients used a single
> `POST/PUT/DELETE /smart-order` with `type` ∈ `AB` (auto-buy), `SL`, `TP`,
> `LIT_SELL`. Prefer the `/smart-order/.../v1/order` endpoints above. Source:
> `sb-web/src/lib/api/smartOrder.ts:26-69` (marked `@deprecated`).

---

## 3. Amend a working order

**Single:**

```
POST /order/v2/amend
```
```json
{ "order_id": "123456789", "ui_ref": "A...", "symbol": "BBCA", "price": 9550, "shares": 100, "board_type": "RG" }
```

**Bulk:**

```
POST /order/v2/amend/bulk
```
```json
{ "ui_ref": "A...", "amend_request": [ { "order_id": "123", "price": 9550, "shares": "100" } ] }
```

Source: `sb-web/src/lib/api/trade.ts:293-330`,
`sb-android/.../api/securities/CarinaApi.kt:139-142`.

---

## 4. Cancel / withdraw

**Single:**
```
POST /order/v2/cancel
{ "order_id": "123456789", "ui_ref": "A..." }
```

**Bulk:**
```
POST /order/v2/bulk-cancel
{ "ui_ref": "A...", "order_id": ["123","124"] }
```

Smart orders are cancelled via their own `DELETE .../{order_id}` endpoints (§2).

Source: `sb-web/src/lib/api/trade.ts:345-355`,
`sb-web/src/global/Securities/api/order.ts:7-13`,
`sb-android/.../api/securities/CarinaApi.kt:124-137`.

---

## 5. Retrieve orders & status

| Action | Method & path | Params |
|--------|---------------|--------|
| Order list | `GET /order/v2/list` | `filter_criteria.stock_code`, `status`, `action`, `board_type` (all optional) |
| Order detail | `GET /order/v2/detail` | `order_id` |
| Order summary | `GET /order/v2/summary` | `stock_code` |
| Smart orders for a symbol | `GET /smart-order` | `stock_code` |
| Trade history | `GET /history` | date range params |

The order-detail payload includes `status_text`, `side`, `qty`, `price`,
`gtc`, `platform_order_type`, and (when present) `trailing_stop`,
`smart_order_bracket_order_child[]`, `volume_trigger_order`.

Source: `sb-web/src/lib/api/trade.ts:66-76`,
`sb-android/.../api/securities/CarinaApi.kt:95-179`,
`sb-ios/.../Networking/Router/TradingV2APIRouter.swift:172-333`.

---

## 6. Day-trade (margin/leverage) orders

Separate endpoints carrying a `multiplier` (leverage):

```
POST /order/day-trade/v1/buy      { ui_ref, symbol, price, shares, multiplier, platform_order_type }
POST /order/day-trade/v1/sell     { ui_ref, symbol, price, shares, platform_order_type }
POST /order/day-trade/v1/amend    { ui_ref, order_id, price, shares, multiplier }
POST /order/day-trade/v1/cancel   { ui_ref, order_id }
```

Source: `sb-web/src/lib/api/trade.ts:221-368`,
`sb-android/.../api/securities/CarinaApi.kt:150-161`.

---

## 7. Negotiated (OTC) orders — nego-engine

Counterparty/negotiated trades use a different service, fully specified in the
proto contract `securities.transactional.negoengine.order.public.v1.OrderService`
(`sb-protos/proto/securities/transactional/negoengine/order/public/v1/order_service.proto`):

| Action | Method & path |
|--------|---------------|
| Create | `POST /nego-engine/v1/order` (body `{ idempotency_key, asset_code, shares, order_side, order_price, order_visibility, counterparty_order_id?, ... }`) |
| Cancel | `DELETE /nego-engine/v1/order/{order_id}` |
| Confirm cancel | `PATCH /nego-engine/v1/order/{order_id}/confirm-cancel` |
| Abort cancel | `PATCH /nego-engine/v1/order/{order_id}/abort-cancel` |
| Confirm matching | `POST /nego-engine/v1/order/{order_id}/confirm-matching` |
| Preview | `POST /nego-engine/v1/order/preview` |

Note `idempotency_key` here must be **12–36 chars**; orders above ~IDR 1,000,000
also need a `verification_session_id` from the verification service.

---

## 8. Enum reference

```text
# Order side
ORDER_SIDE_BUY | ORDER_SIDE_SELL

# Validity / expiry
ORDER_EXPIRY_TYPE_GFD   (Good For Day)
ORDER_EXPIRY_TYPE_GTC   (Good Till Cancelled)
ORDER_EXPIRY_TYPE_FAK   (Fill And Kill / market)

# Triggered-order order type
ORDER_TYPE_LIMIT | ORDER_TYPE_MARKET

# Trigger direction
ORDER_TRIGGER_TYPE_LTE  (fire when price <= trigger_price)
ORDER_TRIGGER_TYPE_GTE  (fire when price >= trigger_price)

# Stop-order product
ORDER_PRODUCT_CODE_STOP_LOSS | ORDER_PRODUCT_CODE_TAKE_PROFIT | ORDER_PRODUCT_CODE_LIMIT_IF_TOUCHED

# Asset type
ASSET_TYPE_STOCK_REGULAR | ASSET_TYPE_STOCK_RIGHT | ASSET_TYPE_STOCK_WARRANT | ASSET_TYPE_FR_BOND | ASSET_TYPE_MUTUAL_FUND

# Market board
MARKET_BOARD_REGULER | MARKET_BOARD_TUNAI

# platform_order_type (regular orders)
PLATFORM_ORDER_TYPE_LIMIT_DAY | PLATFORM_ORDER_TYPE_MARKET_FAK | PLATFORM_ORDER_TYPE_FAST_ORDER
PLATFORM_ORDER_TYPE_STOP_LOSS | PLATFORM_ORDER_TYPE_TAKE_PROFIT | PLATFORM_ORDER_TYPE_LIT
PLATFORM_ORDER_TYPE_TRAILING_STOP | PLATFORM_ORDER_TYPE_BRACKET_ORDER

# Order status (regular)
ORDER_STATUS_OPEN | ORDER_STATUS_PARTIAL_MATCH | ORDER_STATUS_FULL_MATCH | ORDER_STATUS_WITHDRAWN | ORDER_STATUS_AMEND

# Order status (smart)
ORDER_STATUS_READY | ORDER_STATUS_AMENDED | ORDER_STATUS_EXECUTED | ORDER_STATUS_WITHDRAWN | ORDER_STATUS_EXPIRED
```

Source: `sb-web/src/global/Securities/types/api/smartOrder.ts:76-121`,
`sb-android/.../usecase/securities/model/type/*` and
`domain/model/securities/order/PlatformOrderType.kt`,
`sb-ios/.../Constant/PlatformOrderType.swift:10-70`.

---

## 9. End-to-end sequence (place a buy, attach a stop loss)

```text
# 1. Main session (see 01-authentication.md §1)
POST exodus.stockbit.com/login/v6/username           → main access token

# 2. Trading session (see 01-authentication.md §4)
GET  exodus.stockbit.com/sekuritas/auth/token         → login_token
POST carina.stockbit.com/auth/v2/login {login_token, pin} → trading access token

# 3. Buy 1 lot BBCA @ 9500, day limit
POST carina.stockbit.com/order/v2/buy
     { ui_ref, symbol:"BBCA", price:9500, shares:"100", board_type:"RG",
       is_gtc:false, platform_order_type:"PLATFORM_ORDER_TYPE_LIMIT_DAY" }
     → { data: { order_id } }

# 4. Protect it with a stop loss at 9000 (sell, GTC)
POST carina.stockbit.com/smart-order/stop-order/v1/order
     { product_code:"ORDER_PRODUCT_CODE_STOP_LOSS",
       asset:{code:"BBCA",type:"ASSET_TYPE_STOCK_REGULAR",market_board:"MARKET_BOARD_REGULER"},
       shares:"100", trigger_type:"ORDER_TRIGGER_TYPE_LTE", trigger_price:"9000",
       order_side:"ORDER_SIDE_SELL", order_type:"ORDER_TYPE_MARKET",
       order_expiry_type:"ORDER_EXPIRY_TYPE_GTC", ui_ref }

# 5. Watch fills
GET  carina.stockbit.com/order/v2/list?filter_criteria.stock_code=BBCA
```

---

## Quick reference

| Action | Method & path |
|--------|---------------|
| Buy | `POST /order/v2/buy` |
| Sell | `POST /order/v2/sell` |
| Amend | `POST /order/v2/amend` · `POST /order/v2/amend/bulk` |
| Cancel | `POST /order/v2/cancel` · `POST /order/v2/bulk-cancel` |
| Stop loss / take profit / LIT | `POST /smart-order/stop-order/v1/order` (+ `PATCH`/`DELETE` `/{id}`) |
| Trailing stop | `POST /smart-order/trailing-stop/v1/order` (+ `PATCH`/`DELETE` `/{id}`) |
| Bracket (OCO) | `POST /smart-order/bracket-order/v1/order` (+ `PATCH`/`DELETE` `/{id}`) |
| Volume trigger | `POST /smart-order/volume-trigger-order/v1/order` (+ `PATCH`/`DELETE` `/{id}`) |
| Order list / detail / summary | `GET /order/v2/list` · `/order/v2/detail` · `/order/v2/summary` |
| Day trade | `POST /order/day-trade/v1/{buy,sell,amend,cancel}` |
| Negotiated | `POST /nego-engine/v1/order` (see §7) |

Prev: [WebSocket streaming ←](./02-websocket-streaming.md) ·
Index: [README](./README.md)
</content>
</invoke>
