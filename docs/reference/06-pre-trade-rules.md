# IDX Pre-Trade Rules — What Gets an Order Auto-Rejected

An order can be syntactically valid (correct host, auth, and JSON shape as in
[03-order-placement.md](./03-order-placement.md)) and still be rejected outright
by the IDX matching engine or by Carina before the order even reaches the
exchange. This document covers the microstructure rules a bot must verify
**before** calling `POST /order/v2/buy` or any sibling endpoint. Violating any
one of them results in a `4xx` rejection or an exchange auto-reject; neither
surfaces as a trade.

> The same private-API disclaimer from doc 03 applies here. Nothing here is a
> public IDX specification — it is reverse-engineered from the three official
> clients and the Protobuf contracts in `sb-protos`. Each claim cites a source.

---

## Table of Contents

1. [Pre-trade checklist](#1-pre-trade-checklist)
2. [Market hours and session phases](#2-market-hours-and-session-phases)
3. [Holidays](#3-holidays)
4. [ARA / ARB — daily price bands](#4-ara--arb--daily-price-bands)
5. [Tick size (fraksi harga)](#5-tick-size-fraksi-harga)
6. [Lot size](#6-lot-size)
7. [Instrument status — suspension and special notations](#7-instrument-status--suspension-and-special-notations)
8. [Board type](#8-board-type)
9. [What is NOT represented here](#9-what-is-not-represented-here)
10. [Data-source summary](#10-data-source-summary)

---

## 1. Pre-trade checklist

Run these checks in order before submitting any order:

```text
[ ] 1. Today is not a market holiday
        GET /holidays/active → dates[] must not contain today (DD-MM-YYYY)

[ ] 2. Market is open (correct session for order type)
        GET /company-price-feed/market-time → data.market.status == "STATUS_OPEN"
        OR
        GET /company-price-feed/market-time/session?datetime=<now>
          → data.detail.regular.state_name in {SESSION_1 .. SESSION_5}
          (for regular board; nego orders also allowed during NEGOTIATION)

[ ] 3. Instrument is tradable and not suspended
        REST: StockService.Info → data.is_tradable == true
        ITCH stream: Company.tradable != 0 AND SuspendState == UNSUSPENDED
          (CompanyTradingAction.suspend_state in the live feed)

[ ] 4. Price is within ARA/ARB bands
        GET /nego-engine/v1/order-book/{asset_code}
          → data.auto_reject_price.below <= price <= data.auto_reject_price.above

[ ] 5. Price is on a valid tick
        ITCH: look up the stock's price_tick_size_table_id from OrderBookDirectory,
              then match price against PriceTickSizeTable rows for that table id.
        Quick approximation: see roundTick() in collector/broker.go:154-168

[ ] 6. Quantity is in whole lots
        quantity_shares % lot_size == 0
        lot_size from ITCH OrderBookDirectory.shares_per_lot (typically 100)
        or Company.round_lot_size from the datafeed consumer stream

[ ] 7. Board type matches instrument type
        Regular stocks → board_type "RG"
        Rights issues   → board_type "TN"
        (see §8 for the full enum)
```

---

## 2. Market hours and session phases

### 2.1 Simple open/close status (REST)

```
GET /company-price-feed/market-time
```

Response shape (TypeScript interface at
`sb-web/src/typings/market.ts:84-88`):

```json
{
  "data": {
    "market":         { "status": "STATUS_OPEN" },
    "iepiev_regular": { "status": "STATUS_OPEN" },
    "iepiev_fca":     { "status": "STATUS_CLOSE" }
  }
}
```

`Status` is an enum defined in
`sb-protos/proto/financial/company_price_feed/entity/v1/indicative_price_volume.proto:37-41`:

```proto
enum Status {
  STATUS_UNSPECIFIED = 0;
  STATUS_OPEN        = 1;
  STATUS_CLOSE       = 2;
}
```

`data.market.status == STATUS_OPEN` is the minimum check for regular orders.
The web client also maps the raw string values `"open"` / `"close"` to these
enum variants (`sb-web/src/typings/market.ts:72-78`), so normalise before
comparing.

### 2.2 Session detail (REST)

```
GET /company-price-feed/market-time/session?datetime=YYYY-MM-DD%20HH:ii:ss
```

Both endpoints are declared in
`sb-protos/proto/financial/company_price_feed/public/market_time/v1/market_time.proto:22-28`.

The response returns two `SessionDetail` sub-objects — `fca` (Full Call
Auction) and `regular` — each typed by
`sb-protos/proto/financial/company_price_feed/entity/v1/market_time.proto:25-37`:

```proto
message SessionDetail {
  int32     session           = 1;
  StateName state_name        = 2;
  bool      is_last_session   = 3;
  bool      is_end_of_day     = 4;
  string    state_start_time  = 5;  // e.g. "09:00:00"
  string    state_end_time    = 6;  // e.g. "11:59:59"
  TimeLeft  time_left         = 7;
  string    suspend_info      = 8;
}
```

`StateName` enum
(`sb-protos/proto/financial/company_price_feed/entity/v1/market_time.proto:9-23`):

```proto
enum StateName {
  STATE_NAME_UNSPECIFIED   = 0;
  STATE_NAME_PRE_OPENING   = 1;
  STATE_NAME_SESSION_1     = 2;
  STATE_NAME_SESSION_2     = 3;
  STATE_NAME_SESSION_3     = 4;
  STATE_NAME_SESSION_4     = 5;
  STATE_NAME_SESSION_5     = 6;
  STATE_NAME_BREAK         = 7;
  STATE_NAME_PRE_CLOSING   = 8;
  STATE_NAME_POST_CLOSING  = 9;
  STATE_NAME_NEGOTIATION   = 10;
  STATE_NAME_MARKET_CLOSED = 11;
  STATE_NAME_BEFORE_MARKET = 12;
}
```

Regular limit orders are accepted in `SESSION_1` through `SESSION_5`.
`PRE_OPENING`, `BREAK`, `PRE_CLOSING`, `POST_CLOSING`, and `MARKET_CLOSED`
do not accept new regular orders (or the exchange silently queues/rejects
them). Negotiated orders use the `NEGOTIATION` window.

### 2.3 Market cycle allocation (within trading info endpoint)

The TRM service also exposes a richer `MarketCycle` message with named
allocations, retrieved as part of the trading-info endpoint (feature flag
`FEATURE_MARKET_CYCLE`):

```proto
// sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:93-118
message MarketCycle {
  enum AllocationName {
    ALLOCATION_NAME_PRE_OPENING          = 1;
    ALLOCATION_NAME_PRE_OPENING_BREAK    = 2;
    ALLOCATION_NAME_FIRST_TRADING_SESSION  = 3;
    ALLOCATION_NAME_MARKET_BREAK         = 4;
    ALLOCATION_NAME_SECOND_TRADING_SESSION = 5;
    ALLOCATION_NAME_PRE_CLOSING          = 6;
    ALLOCATION_NAME_CLOSING              = 7;
    ALLOCATION_NAME_POST_CLOSING_1       = 8;
    ALLOCATION_NAME_POST_CLOSING_2       = 9;
    ALLOCATION_NAME_MARKET_CLOSE         = 10;  // also used on holidays
  }
  message Allocation {
    AllocationName          name     = 1;
    google.protobuf.Timestamp start_at = 3;
    google.protobuf.Timestamp end_at   = 4;
  }
  Allocation market_cycle_allocation = 1;
  // ...
}
```

`ALLOCATION_NAME_MARKET_CLOSE` is set both outside trading hours and on public
holidays — a convenient single flag if this endpoint is already being polled.

---

## 3. Holidays

```
GET /holidays/active
```

Declared in
`sb-protos/proto/securities/trm/core/holiday/backoffice/v1/holiday.proto:20-22`.
This is a backoffice endpoint (`x-nerv-gateway: SecuritiesBackofficeCockpit`);
verify gateway access before relying on it in production.

Response:

```json
{ "data": { "dates": ["17-08-2025", "01-01-2026"] } }
```

Dates are `DD-MM-YYYY` strings
(`sb-protos/proto/securities/trm/core/holiday/backoffice/v1/holiday.proto:53`).
Cache the list at start-of-day and compare today's date before trading.

---

## 4. ARA / ARB — daily price bands

**This is the most actionable pre-trade check.** IDX defines an
_Auto Rejection Above_ (ARA) and _Auto Rejection Below_ (ARB) price for each
instrument every morning. Any order priced strictly above ARA or strictly below
ARB is auto-rejected by the matching engine regardless of whether the order is
syntactically valid.

### Fetch

```
GET /nego-engine/v1/order-book/{asset_code}
```

Declared in
`sb-protos/proto/securities/transactional/negoengine/order_book/public/v1/order_book.proto:26-39`.
Host is Carina (`x-nerv-gateway: Carina`), so the trading session token is
required.

Response envelope:

```json
{
  "data": {
    "order_book": { "asset_code": "BBCA", "bid": {...}, "ask": {...} },
    "auto_reject_price": {
      "above": 10450,
      "below":  8550,
      "max_requestable_above": 10450,
      "max_requestable_below":  8550
    }
  }
}
```

### Schema

`AutoRejectPrice` is defined in
`sb-protos/proto/securities/transactional/negoengine/order_book/entity/v1/order_book.proto:72-100`:

| Field | Type | Meaning |
|---|---|---|
| `above` | `uint64` | ARA — hard ceiling; orders **above** this are rejected |
| `below` | `uint64` | ARB — hard floor; orders **below** this are rejected |
| `max_requestable_above` | `uint64` | ARA cap that clients are permitted to request |
| `max_requestable_below` | `uint64` | ARB floor that clients are permitted to request |

All values are integer IDR with no decimal point (e.g. `10450` = IDR 10,450).

### Guard

```
auto_reject_price.below <= order_price <= auto_reject_price.above
```

Orders at exactly the boundary are valid. ARA/ARB resets each morning; refetch
at session start and again if the instrument is halted and then unhalted.

---

## 5. Tick size (fraksi harga)

IDX divides the price range into bands, each with its own minimum price
increment. An order price that falls between valid ticks is rejected by Carina
with a `4xx` before reaching the exchange.

### How the rule is delivered

Tick-size data is not available as a single REST call. It is streamed via the
**ITCH feed** as two message types:

**`PriceTickSizeTable`**
(`sb-protos/proto/securities/transactional/datafeed/itch/entity/v1/entity.proto:31-40`):

```proto
message PriceTickSizeTable {
  int64 tick_size_table_id = 3;  // which table this row belongs to
  int64 tick_size          = 4;  // minimum price increment (IDR)
  int64 price_start        = 5;  // lower bound of this band (IDR)
}
```

Multiple rows share the same `tick_size_table_id`. Each row means: "for prices
≥ `price_start` (and below the next row's `price_start`), the tick is
`tick_size`."

**`OrderBookDirectory`**
(`sb-protos/proto/securities/transactional/datafeed/itch/entity/v1/entity.proto:75-104`):

```proto
message OrderBookDirectory {
  int64 price_tick_size_table_id = 16;  // which PriceTickSizeTable applies
  // ...
}
```

The client builds a lookup: `stock_code → price_tick_size_table_id → []PriceTickSizeTable`.
To validate a price, find the table for the stock, locate the row where
`price_start <= price < next_row.price_start`, and confirm
`price % tick_size == 0`.

### Practical approximation

The prototype implements a simplified version that is safe for placing limit
orders near the market price
(`collector/broker.go:151-168`):

```go
// roundTick snaps a price to a coarse IDX-like tick so demo orders are on-tick.
// (Real tick rules are price-banded; this is a safe approximation for a limit
// order placed near the mid — Carina rejects off-tick prices with 4xx anyway.)
func roundTick(p float64) float64 {
    var tick float64
    switch {
    case p < 200:   tick = 1
    case p < 500:   tick = 2
    case p < 2000:  tick = 5
    case p < 5000:  tick = 10
    default:        tick = 25
    }
    return float64(int(p/tick)) * tick
}
```

This captures the most common IDX bands. A production bot that operates across
the full price range should maintain the full ITCH table instead of using this
approximation, as the actual bands and their boundaries can differ.

---

## 6. Lot size

IDX requires orders in whole lots. Sending a non-lot quantity is rejected.

### Sources

**ITCH stream** — most precise, per instrument:

```proto
// sb-protos/proto/securities/transactional/datafeed/itch/entity/v1/entity.proto:88
message OrderBookDirectory {
  int64 shares_per_lot = 12;
  // ...
}
```

**Datafeed consumer stream** — also per instrument:

```proto
// sb-protos/proto/securities/transactional/datafeed/consumer/entity/v1/entity.proto:119
message Company {
  int64 round_lot_size = 31;
  // ...
}
```

The standard IDX lot size for equities is **100 shares**. Doc 03 encodes this
directly: `shares = lots * 100`
(`03-order-placement.md` §0, Units & validation). Rights issues and some other
instruments may carry a different `round_lot_size` in the feed; use the
per-instrument value rather than the constant when operating on non-standard
instruments.

### Guard

```
shares % round_lot_size == 0   (and shares > 0)
```

---

## 7. Instrument status — suspension and special notations

A stock can be tradable under normal conditions but temporarily halted or
flagged with exchange notations that restrict trading. Check all three signals.

### 7.1 `is_tradable` flag (REST)

The TRM private stock service exposes a lightweight tradability check:

```proto
// sb-protos/proto/securities/trm/core/stock/private/v2/stock.proto:87-99
message InfoResponse {
  message Data {
    string              code              = 1;
    string              name              = 2;
    StockType           type              = 3;
    repeated string     special_notations = 4;
    bool                is_tradable       = 5;
    bool                is_sharia         = 6;
  }
}
```

`is_tradable == false` means the instrument is not currently tradable — do not
submit. `special_notations` carries IDX notation codes (e.g. `"X"` for
suspension pending corporate action, `"S"` for shares collateral) that may
impose restrictions.

There is also a batch-tradability endpoint in the same service:
`TradableRequest` / `TradableResponse` at
`sb-protos/proto/securities/trm/core/stock/private/v2/stock.proto:41-56`, which
accepts `repeated string stock_codes` and returns `is_tradable` per code — use
this when warming up a watchlist.

### 7.2 `SuspendState` and `CompanyTradingAction` (ITCH stream)

The live trading state of each instrument is pushed via the ITCH/datafeed
consumer stream in `CompanyTradingAction`
(`sb-protos/proto/securities/transactional/datafeed/consumer/entity/v1/entity.proto:227-239`):

```proto
message CompanyTradingAction {
  string       trading_state  = 4;
  string       reason         = 5;
  string       stock_code     = 8;
  BoardType    board_type     = 9;
  SuspendState suspend_state  = 10;
}
```

`SuspendState` enum
(`sb-protos/proto/securities/transactional/datafeed/consumer/entity/v1/entity.proto:32-36`):

```proto
enum SuspendState {
  SUSPEND_STATE_UNSPECIFIED  = 0;
  SUSPEND_STATE_UNSUSPENDED  = 1;
  SUSPEND_STATE_SUSPENDED    = 2;
}
```

A `SUSPEND_STATE_SUSPENDED` message means the stock has been halted by IDX.
Orders submitted against a halted book are auto-rejected. Track the latest
`CompanyTradingAction` per `(stock_code, board_type)` and block order
submission when `suspend_state == SUSPENDED`.

The `Company` message in the same file also carries an `int32 tradable` field
at line 105, which the datafeed consumer populates from the ITCH snapshot.

### 7.3 Listing board and special notations (datafeed stream)

`Company.listing_board` and `Company.special_notation_codes` provide longer-term
instrument classification
(`sb-protos/proto/securities/transactional/datafeed/consumer/entity/v1/entity.proto:111-128`):

```proto
enum ListingBoardType {
  LISTING_BOARD_TYPE_UNSPECIFIED           = 0;
  LISTING_BOARD_TYPE_MAIN_BOARD            = 1;
  LISTING_BOARD_TYPE_DEVELOPMENT_BOARD     = 2;
  LISTING_BOARD_TYPE_ACCELERATION_BOARD    = 3;
  LISTING_BOARD_TYPE_SPECIAL_MONITORING_BOARD = 4;
  LISTING_BOARD_TYPE_NEW_ECONOMY_BOARD     = 5;
}
```

Stocks on `SPECIAL_MONITORING_BOARD` are subject to IDX-imposed trading
restrictions (intraday call-auction only, among others). A bot targeting only
regular continuous trading should filter these out.

---

## 8. Board type

Every Carina order carries a `board_type` field that selects the trading venue.
The enum is defined in two places that agree:

`sb-protos/proto/securities/trm/core/stock/enum/v1/enum.proto:9-14`:

```proto
enum BoardType {
  BOARD_TYPE_UNSPECIFIED = 0;
  BOARD_TYPE_RG          = 1;   // Regular (continuous auction)
  BOARD_TYPE_TN          = 2;   // Tunai / Cash (T+0 settlement, also Negotiated)
  BOARD_TYPE_NG          = 3;   // New/Emerging Growth board
}
```

The web client maps instrument type to board type in
`sb-web/src/constants/trading.ts:1-4`:

```typescript
export const BOARD_TYPE = {
  saham: 'RG',   // ordinary shares → Regular board
  right: 'TN',   // rights issues   → Cash/Tunai board
};
```

Most algorithmic strategies target `RG`. Sending a regular-share order with
`board_type: "TN"` routes to a different settlement cycle and a separate order
book; the order may be accepted but will never match against the regular book.

Doc 03 uses `board_type` in all order payloads and the smart-order
`market_board` field (`MARKET_BOARD_REGULER` / `MARKET_BOARD_TUNAI`)
mirrors the same distinction.

---

## 9. What is NOT represented here

The following rules exist at IDX but are enforced server-side only — they do
not appear in the client contracts examined here, so a bot cannot pre-validate
them locally:

- **Odd-lot rules.** IDX has a separate odd-lot board for quantities less than
  one lot. The regular Carina order endpoint does not expose odd-lot routing;
  any non-lot quantity is simply rejected.
- **Iceberg / hidden order types.** No iceberg or reserve-quantity order type
  appears in the Carina API or proto contracts. Assume all orders are fully
  visible depth.
- **Foreign ownership limits.** `OrderBookDirectory.foreign_limit` is present
  in the ITCH feed but the clients do not enforce it client-side; the exchange
  or broker rejects once the aggregate limit is reached.
- **Intraday order count limits.** `OrderCountLimit` in
  `sb-protos/proto/securities/trm/core/info/public/v1/entity.proto:149-158`
  tracks soft and hard per-account order count limits enforced by Carina, not
  IDX. Carina returns an error when the hard limit is hit; poll
  `user_order_count` from the trading-info endpoint if you need to budget.

---

## 10. Data-source summary

| Rule | How to fetch | Protocol |
|---|---|---|
| Holiday check | `GET /holidays/active` | REST (backoffice gateway) |
| Market open/closed | `GET /company-price-feed/market-time` | REST |
| Session phase | `GET /company-price-feed/market-time/session?datetime=` | REST |
| Market cycle allocation | Trading-info endpoint, feature `FEATURE_MARKET_CYCLE` | REST |
| ARA / ARB price bands | `GET /nego-engine/v1/order-book/{code}` | REST (Carina) |
| Instrument tradability | `StockService.Info` or `StockService.Tradable` (TRM private) | REST |
| Suspension state | `CompanyTradingAction` messages | ITCH / datafeed stream |
| Tick size | `PriceTickSizeTable` + `OrderBookDirectory.price_tick_size_table_id` | ITCH stream |
| Lot size | `OrderBookDirectory.shares_per_lot` or `Company.round_lot_size` | ITCH / datafeed stream |
| Board type | Hardcoded per instrument type (`RG` / `TN`) | Client constant |

Prev: [Order Placement ←](./03-order-placement.md) ·
Index: [README](./README.md)
