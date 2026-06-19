# Stockbit API — Account State (Cash, Positions & Limits)

Before sizing an order, you need to answer two questions: **what can I afford to
buy?** and **what do I currently hold (and how much of it can I actually sell)?**
This document covers the REST endpoints and Protobuf contracts that answer those
questions — available cash / buying power, current positions including T+2
settlement detail, margin thresholds, and daily order-count limits.

> ⚠️ All endpoints below live on `https://carina.stockbit.com` (same host as
> order placement) and require the **trading (securities) session token** — the
> one obtained from the PIN login, not the main account token. See
> [01-authentication.md](./01-authentication.md#4-trading-securities-account-login).
> These are private, undocumented endpoints reverse-engineered from the three
> official clients.

All facts below are cross-verified across `sb-web`, `sb-ios`, `sb-android`, and
`sb-protos`. Each claim cites its source file and line.

---

## 0. Summary of endpoints

| Purpose | Method & path | Key response fields |
|---------|---------------|---------------------|
| Simple cash balance | `GET /balance/cash` | `available_cash_on_hand` |
| Full buying-power snapshot | `GET /balance/cash/info` | `trade_balance`, `day_trade_buying_power`, `margin.*` |
| Withdrawable + settlement schedule | `GET /balance/withdrawable` | `balance.*`, `settlement_schedules[]` |
| Portfolio list | `GET /portfolio/v2/list` | `results[].qty.{available,balance,unsettled}` |
| Portfolio detail (one stock) | `GET /portfolio/v2/detail` | full `PortfolioDetail` |
| Portfolio summary | `GET /portfolio/v2/summary` | P&L, debt ratio |
| Trading limits (margin, order caps) | `GET /trading/info` | `margin_trading.*`, `order_count_limit.*`, `split_order.*` |

---

## 1. Cash / buying power

### 1.1 Simple cash-on-hand

```
GET /balance/cash
Authorization: Bearer <trading_access_token>
```

No request parameters. Response:

```json
{ "data": { "available_cash_on_hand": 5000000.0 } }
```

`available_cash_on_hand` is the amount immediately available for placing a new
buy order — settled cash minus reserved amounts. This is the cheapest call to
make for a quick sanity check before sizing.

Source: proto service definition
`sb-protos/proto/securities/trm/core/balance/public/v2/balance.proto:20-40`,
web client call `sb-web/src/lib/api/trade.ts:121-126`,
endpoint constant `sb-web/src/global/Securities/constants/apiEndpoints/balance.ts:3`,
TypeScript interface `sb-web/src/global/Securities/types/api/balance.ts:15-17`.

### 1.2 Full buying-power detail

```
GET /balance/cash/info?stock_code=BBCA
GET /balance/cash/info?stock_code=BBCA&order_id=123456789   # for an amend
Authorization: Bearer <trading_access_token>
```

Parameters (both sent as query strings by the web client):

| Parameter | Required | Notes |
|-----------|----------|-------|
| `acc_no` | Required (proto field 1) | Account number. The web client derives it from session context and does not send it explicitly as a query param; the server appears to resolve it from the auth token. |
| `stock_code` | Required | Upper-case ticker (e.g. `BBCA`). The server uses this to compute stock-specific buying power limits. |
| `order_id` | Optional | Pass the existing order ID when checking headroom for an amend. |

Response `data` is a `CashInfo` object:

| Field | Type | Meaning |
|-------|------|---------|
| `trade_limit` | double | Total funded limit (credit + cash) for the regular account. |
| `trade_balance` | double | Remaining regular buying power (after open-buy reservations). **Use this to size new regular buy orders.** |
| `day_trade_buying_power` | double | Gross intraday buying power (leverage applied). |
| `day_trade_leverage` | double | Leverage multiplier in effect for the sub-account. |
| `day_trade_trading_balance` | double | Remaining day-trade buying power after open reservations. |
| `margin` | object | Present only for margin accounts; see §1.3. |

Source: proto `sb-protos/proto/securities/trm/core/balance/public/v2/balance.proto:43-88`,
web client call `sb-web/src/global/Securities/api/balance.ts:6-15`,
endpoint constant `sb-web/src/global/Securities/constants/apiEndpoints/balance.ts:2`,
TypeScript interface `sb-web/src/global/Securities/types/api/balance.ts:7-13`.

### 1.3 Margin buying power (inside `cash/info`)

The `margin` sub-object within the `CashInfo` response:

| Field | Type | Meaning |
|-------|------|---------|
| `buying_power` | double | Gross margin buying power. |
| `trading_balance` | double | Remaining margin buying power after open-buy reservations. Slider input for the order form. Cannot go negative. |
| `total_debt` | double | Realised + unrealised debt (negative TB). |
| `total_collateral` | double | Portfolio market loan value (PMLV) + potential open-buy collateral. |
| `status` | enum | `MARGIN_ACCOUNT_STATUS_UNREGISTERED` / `UNVERIFIED` / `ACTIVE`. Only `ACTIVE` accounts have real values in the other fields. |

Source: `sb-protos/proto/securities/trm/core/balance/public/v2/balance.proto:59-68`.

### 1.4 Withdrawable balance and settlement schedule

```
GET /balance/withdrawable
GET /balance/withdrawable?balance_scope=BALANCE_SCOPE_SELECTED_ACCOUNT&account_number=<acc_no>
Authorization: Bearer <trading_access_token>
```

Omitting query parameters returns the aggregate across all related sub-accounts.
Passing `balance_scope=BALANCE_SCOPE_SELECTED_ACCOUNT` with an `account_number`
scopes the response to one account (the server rejects if the user is not
eligible for single-account queries).

Response `data` contains:

**`balance` object** (maps to `Balance` message):

| Field | Meaning |
|-------|---------|
| `withdrawable` | Cash that can be withdrawn right now (T+0 settled, floored at zero). |
| `transaction` | Trading balance (a.k.a. funded trade limit). |
| `pending` | Cash reserved / pending (e.g. open buy orders). |
| `leverage_liability` | Leverage debt owed, typically negative. |
| `net_withdrawable` | `withdrawable` minus liabilities; can be negative. |
| `transferable` | T+0-settled balance that can be transferred between sub-accounts. |

**`settlement_schedules[]`** — an array of up to three `WithdrawableSchedule`
entries, one per settlement tier:

| Field | Example | Meaning |
|-------|---------|---------|
| `title` | `"T+0"` / `"T+1"` / `"T+2"` | Settlement tier label. |
| `formatted_date` | `"16/06/26"` | Calendar date of settlement. |
| `balance` | `1000000.0` | Cash settling on this date. |

The T+1 and T+2 balances are proceeds from recent trades that have not yet
settled. They are **not** available for withdrawal and are **not** included in
`trade_balance` until the settlement date passes. See §5 for the bot implications.

Source: proto service `sb-protos/proto/securities/trm/core/balance/public/v2/balance.proto:90-106`,
`Balance` message `sb-protos/proto/securities/trm/core/balance/entity/v2/entity.proto:17-41`,
`WithdrawableSchedule` message `sb-protos/proto/securities/trm/core/balance/entity/v2/entity.proto:52-56`,
web client call `sb-web/src/lib/api/securities.ts:192-194`,
TypeScript response type `sb-web/src/global/Securities/types/api/multiPorto.ts:242-252`.

---

## 2. Portfolio positions

### 2.1 Portfolio list

```
GET /portfolio/v2/list
GET /portfolio/v2/list?acc_no=<account_number>
GET /portfolio/v2/list?stock_schedule=STOCK_SCHEDULE_SETTLED    # T+2 settled only
GET /portfolio/v2/list?margin_collateral_eligible=true          # margin-eligible stocks only
Authorization: Bearer <trading_access_token>
```

Returns a `PortfolioList` containing:
- `summary` — aggregate P&L, debt ratio, trading balance (see §2.3).
- `results[]` — regular account positions as `PortfolioItem` (see §2.2).
- `day_trade[]` — day-trade virtual-account positions, same shape.

Source: proto service `sb-protos/proto/securities/trm/core/portfolio/public/v2/portfolio.proto:21-23`,
web client calls `sb-web/src/lib/api/trade.ts:42-51`.

### 2.2 PortfolioItem fields

Each position in `results[]` is a `PortfolioItem`:

```
sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:508-524
```

| Field | Type | Meaning |
|-------|------|---------|
| `symbol` | string | Ticker, e.g. `BBCA`. |
| `qty` | PortfolioQty | Share/lot counts broken down by settlement status (see §2.2.1). |
| `price` | PortfolioPrice | Latest price and average cost (see §2.2.2). |
| `asset` | PortfolioAsset | Unrealised P&L and invested amount (see §2.2.3). |
| `haircut` | Haircut | `trading_limit` and `margin_trading` haircut rates for this stock. |
| `flags` | PortfolioFlags | `can_sell`, `allow_order`, `allow_market_order`, etc. |
| `is_sharia` | bool | Whether this holding is classified as sharia. |

#### 2.2.1 `PortfolioQty` — the settlement breakdown

This is the most important structure for a bot. All sub-fields use the same
`Qty { lot, share }` shape.

```
sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:64-76
```

| Field | Meaning |
|-------|---------|
| `stock_on_hand` | Raw share count held by the custodian. |
| `available` | **T+2 settled lots and shares — what you can sell right now.** |
| `balance` | Total position including unsettled shares (available + unsettled). This is the value shown in most portfolio UIs. |
| `unsettled` | `balance − available`: shares from recent buys that are not yet T+2 settled and **cannot be sold**. |
| `unsettled_until_at` | Date until which the unsettled portion remains locked. Always set to the next trading day; individual settlement dates per-trade are not exposed at this level (see proto comment at line 73-75). |

The proto comment on `available` explicitly notes: _"this will be T+2
(previously TX)"_ — confirming that only fully settled shares are counted here.

**Critical for sell orders: use `available.lot * 100` as your maximum sell
shares, not `balance.lot`.** Attempting to sell unsettled shares results in a
server rejection.

Source: `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:64-76`.

#### 2.2.2 `PortfolioPrice`

```
sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:54-62
```

| Field | Meaning |
|-------|---------|
| `latest` | Most recent market price for this stock. |
| `average.price` | Volume-weighted average cost per share (excluding fees). |
| `average.fee` | Average cost per share including brokerage fees. |

Use `average.price` as your book cost for P&L calculations; use `average.fee`
for break-even calculations that include transaction costs.

#### 2.2.3 `PortfolioAsset`

```
sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:78-86
```

| Field | Meaning |
|-------|---------|
| `unrealised.market_value` | Current market value of the position. |
| `unrealised.profit_loss` | Unrealised P&L in IDR. |
| `unrealised.gain` | Unrealised P&L as a percentage. |
| `amount_invested` | Total cost basis (shares × average cost). |

### 2.3 Portfolio detail (single stock)

```
GET /portfolio/v2/detail?stock_code=BBCA
Authorization: Bearer <trading_access_token>
```

Returns `data.result` (regular account) and `data.day_trade` (day-trade
sub-account), both `PortfolioDetail` messages. `PortfolioDetail` has the same
`qty`, `price`, and `asset` fields as `PortfolioItem` (§2.2), plus
`can_create_smart_order` and bond-specific coupon detail when applicable.

Source: proto `sb-protos/proto/securities/trm/core/portfolio/public/v2/portfolio.proto:207-219`,
entity `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:526-553`,
web client `sb-web/src/lib/api/trade.ts:110-118`.

### 2.4 Portfolio summary

```
GET /portfolio/v2/summary
Authorization: Bearer <trading_access_token>
```

Returns `data.aggregated_portfolio_summary` plus per-sub-account summaries
(`summary_per_portfolios[]`). The `PortfolioSummary` message contains:

| Field path | Meaning |
|------------|---------|
| `trading.balance` | Consolidated trading balance across accounts. |
| `amount.invested` | Total invested across the portfolio. |
| `profit_loss.unrealised` | Total unrealised P&L. |
| `profit_loss.realised` | Realised P&L for the period. |
| `debt.ratio` | Debt-to-equity ratio as a percentage (e.g. `93.55`). |
| `debt.total` | Total outstanding margin debt. |
| `debt.market_value` | Total portfolio market value. |
| `debt.buffer.value` / `.percentage` | Distance to the next risk threshold. |

Source: proto `sb-protos/proto/securities/trm/core/portfolio/public/v2/portfolio.proto:27-29, 82-98`,
entity `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:19-51`,
web client `sb-web/src/lib/api/multi-porto.ts:52`.

### 2.5 Haircut values

The `Haircut` sub-object on each `PortfolioItem` exposes:

| Field | Meaning |
|-------|---------|
| `trading_limit` | Haircut rate applied to this stock when computing the regular trading limit. |
| `margin_trading` | Haircut rate applied when computing the margin collateral value. |

Source: `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:498-501`.

---

## 3. Trading limits from `/trading/info`

```
GET /trading/info?features=FEATURE_MARGIN_TRADING&features=FEATURE_ORDER_COUNT_LIMIT&features=FEATURE_SPLIT_ORDER
Authorization: Bearer <trading_access_token>
```

The `features` query parameter is a **repeated** enum that gates which
sub-objects are populated in the response. The web client serialises the array
as repeated params (e.g. `features=FEATURE_MARGIN_TRADING&features=FEATURE_ORDER_COUNT_LIMIT`).

Source: proto service `sb-protos/proto/securities/trm/core/info/public/v1/info.proto:19-43`,
feature enum `sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:160-181`,
web client `sb-web/src/global/Securities/api/trading.ts:6-13`,
endpoint constant `sb-web/src/global/Securities/constants/apiEndpoint.ts:53`.

### 3.1 Margin thresholds (`FEATURE_MARGIN_TRADING`)

The `data.margin_trading` object:

```
sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:72-87
```

| Field | Meaning |
|-------|---------|
| `at_risk_ratio_threshold` | Debt ratio (%) that puts the account "at risk" — a warning stage below margin call. |
| `margin_call_ratio_threshold` | Debt ratio (%) at which a margin call is issued. Depends on user's limit tier. |
| `force_sell_ratio_threshold` | Debt ratio (%) that triggers forced liquidation by the risk engine. |
| `margin_call_consecutive_days` | Number of consecutive margin-call days allowed before forced sell. |
| `active_margin_call_counter` | Current consecutive margin-call count from the risk engine. |

A bot running a margin account should track `PortfolioSummary.debt.ratio`
against `force_sell_ratio_threshold` and reduce positions before the server acts.

### 3.2 Daily order-count limits (`FEATURE_ORDER_COUNT_LIMIT`)

The `data.order_count_limit` object:

```
sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:149-158
```

| Field | Meaning |
|-------|---------|
| `soft_limit.phase_1` | First warning threshold (e.g. 2000 orders). UI shows a warning banner. |
| `soft_limit.phase_2` | Second warning threshold (e.g. 2500 orders). |
| `hard_limit` | Absolute daily cap (e.g. 3000 orders). Further submissions are rejected. |
| `user_order_count` | Orders submitted today on the regular account. |
| `user_daytrade_order_count` | Orders submitted today on the day-trade virtual account. Empty for sub-accounts (multi-porto). |

Each buy, sell, and amend response also returns `order_limit_info.today_count`
(see §3.4), so you can track the counter without polling `/trading/info`
repeatedly.

Source: `sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:149-158`.

### 3.3 Split-order limits (`FEATURE_SPLIT_ORDER`)

The `data.split_order` object:

```
sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:33-40
```

| Field | Meaning |
|-------|---------|
| `max_order` | Maximum number of child orders a split can produce. |
| `max_lot_per_order` | Maximum lots per child order in a split. |
| `trading_open` / `trading_close` | Window during which split orders are accepted. |

### 3.4 Per-order count feedback

Every `POST /order/v2/buy`, `POST /order/v2/sell`, and `POST /order/v2/amend`
response includes:

```json
{ "data": { "order_id": "123456789", "order_limit_info": { "today_count": 42 } } }
```

`order_limit_info.today_count` is the running total of orders placed today
(buy + sell + amend). Compare this to `hard_limit` from `/trading/info` to
know how much headroom remains.

Source: `sb-protos/proto/securities/trm/core/order/entity/v1/entity.proto:21-27`.

### 3.5 No per-stock maximum order size

There is **no `max_order_size` or equivalent field** exposed in the public API
for individual stocks. The server risk engine enforces position and order-size
limits internally and rejects oversized orders with a `4xx` error. A bot must
handle this rejection gracefully — see
[03-order-placement.md](./03-order-placement.md) for the order-level error
handling pattern.

---

## 4. T+2 settlement — what it means in practice

IDX equities settle on a **T+2 basis**: a trade executed on day T settles (and
the proceeds become freely available) on the end of day T+2.

The Stockbit backend enforces this in both directions:

**For cash (sell proceeds):**
- Cash from a sale on day T appears in `settlement_schedules` under `T+2`.
- It is **not** included in `trade_balance` or `withdrawable` until settlement.
- The `pending` field in the `Balance` message captures amounts in transit.

**For shares (bought shares):**
- Shares bought on day T appear in `PortfolioQty.balance` immediately (so the
  UI can show the total holding), but are captured in `unsettled` until day T+2.
- Only `available.lot / available.share` reflects T+2-settled, sellable shares.
- `unsettled_until_at` gives the date until which the unsettled shares are locked.

```
PortfolioQty.balance = PortfolioQty.available + PortfolioQty.unsettled
```

The proto comment on `balance` confirms: _"previously balance_lot, balance_share.
this still will be TX (since we need to show it on porto)"_ — meaning the display
balance intentionally includes unsettled shares, while `available` is strictly
settled.

Source: `sb-protos/proto/securities/trm/core/portfolio/entity/v2/portfolio.proto:64-76`.

---

## 5. For an algo bot

### What to fetch on startup and before each order cycle

1. **`GET /balance/cash`** — quick gate check. If `available_cash_on_hand` is
   below your minimum order value, skip the buy cycle entirely.
2. **`GET /balance/cash/info?stock_code=<symbol>`** — before sizing a buy, read
   `trade_balance` (regular) or `margin.trading_balance` (margin). Size the buy
   so that `price × shares ≤ trade_balance`.
3. **`GET /portfolio/v2/list`** or **`GET /portfolio/v2/detail?stock_code=<sym>`**
   — before sizing a sell, read `qty.available.lot`. Your maximum sell quantity
   in lots is `available.lot`; in shares it is `available.share` (or equivalently
   `available.lot * 100` for regular board stocks).
4. **`GET /trading/info?features=FEATURE_ORDER_COUNT_LIMIT`** — fetch once per
   session start, then maintain a local counter updated from `order_limit_info.today_count`
   on each order response. Stop submitting new orders when `today_count` approaches
   `hard_limit`.

### Refresh cadence

| Data | Suggested cadence |
|------|------------------|
| `available_cash_on_hand` | After every fill / order response; or poll every 10–30 s during active trading. |
| `trade_balance` / `buying_power` | Before each new buy order. |
| Portfolio positions | After every fill; at session start. |
| Margin thresholds | Once at session start; re-read if `debt.ratio` is elevated. |
| Order count limit | Maintained from order responses; full poll at session start. |

### The T+2 traps

- **Never sell `balance.lot`** — it includes unsettled shares that the server
  will reject. Always sell from `available.lot`.
- **Never assume sell proceeds are immediately available for a same-day rebuy.**
  They appear in `settlement_schedules[T+2].balance`, not in `trade_balance`,
  until two trading days later.
- **`unsettled_until_at` is always the next day** (per the proto comment at line
  73–75 of the entity file). For finer-grained settlement scheduling you would
  need to track individual trade timestamps externally.

### Sizing a buy (regular account)

```
max_buy_shares = floor(trade_balance / (price * (1 + broker_fee_rate)))
max_buy_lots   = floor(max_buy_shares / 100)
send_shares    = max_buy_lots * 100        # API takes shares, not lots
```

Where `trade_balance` comes from `GET /balance/cash/info` and `broker_fee_rate`
is typically 0.0015–0.0028 (0.15%–0.28%) depending on account tier. The
`/formula/v2` endpoint returns the exact fee schedule if needed.

### Margin account guardrails

If `margin_trading.active_margin_call_counter > 0` or
`portfolio_summary.debt.ratio` is within 5% of `force_sell_ratio_threshold`,
treat the account as in a restricted state: submit only sell or close orders,
not new buys. The server will force-sell automatically once the threshold is
crossed, which will happen at an unfavourable price.

---

## Quick reference

| Endpoint | Auth | Key output |
|----------|------|------------|
| `GET /balance/cash` | Trading token | `available_cash_on_hand` |
| `GET /balance/cash/info?stock_code=X` | Trading token | `trade_balance`, `day_trade_buying_power`, `margin.*` |
| `GET /balance/withdrawable` | Trading token | `balance.*`, `settlement_schedules[]` (T+0/T+1/T+2) |
| `GET /portfolio/v2/list` | Trading token | Per-stock `qty.{available,balance,unsettled}`, `summary` |
| `GET /portfolio/v2/detail?stock_code=X` | Trading token | Full single-stock position detail |
| `GET /portfolio/v2/summary` | Trading token | Aggregate P&L, `debt.ratio` |
| `GET /trading/info` | Trading token | `margin_trading.*`, `order_count_limit.*`, `split_order.*` |

Prev: [Order placement ←](./03-order-placement.md) ·
Index: [README](../README.md)
