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

> **Implemented by [`strategy/market`](../../strategy/market)** (plan 18 §5 B2),
> which answers §§2–4 and §7 as `risk.MarketGate` and denies on anything it
> cannot confirm. `strategy/cmd/market-probe` prints that answer live.
>
> **Two rows of §10 were wrong and are corrected below** (2026-09-15, found while
> building that gate — the same class of contract error B1 found in doc 05):
> §7.1's `StockService.Info` has no REST route, and §7.2's `CompanyTradingAction`
> is not on any channel a client can subscribe to. Neither is reachable; the
> replacement for the first is `GET /stock/tradable`, and the second has no
> replacement.

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
        REST: GET /stock/tradable?stock_codes=BBCA  (Carina, trading token)
              → data[].is_tradable == true            ← the only reachable check
        NOT reachable from a client, contrary to earlier revisions of §7:
              StockService.Info (private service, no HTTP route)
              CompanyTradingAction.suspend_state (not on any WS channel)

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

> **Measured live, 2026-09-16 09:00 WIB (SESSION_1), via `market-probe --parsed`:**
>
> | Symbol | `below` (ARB) | `above` (ARA) | `max_requestable_below` | `max_requestable_above` |
> |---|---|---|---|---|
> | BBCA | 5450 | 7675 | 3360 | 13225 |
> | PTRO | 4400 | 6200 | 2710 | 10675 |
> | HRTA | 1830 | 2680 | 1135 | 5225 |
> | BNBR | 79 | 124 | 50 | 280 |
>
> Two corrections to the schema section above, both from that run:
>
> 1. **The values arrive as bare JSON numbers, not strings.** The proto types
>    are `uint64`, and proto3's JSON mapping permits the string form, so a
>    parser should accept both — but numbers are what this gateway emits
>    today.
> 2. **`max_requestable_*` is WIDER than the hard band, not equal to it** —
>    contrary to the example body above, which shows the two matching. What
>    the wider pair means is not established here, so `strategy/market` gates
>    on `below`/`above` alone: those are documented as the hard auto-reject
>    bounds, and they are the tighter of the two.
>
> An unknown asset code answers **HTTP 500** (`SYSTEM_ERROR`), not 404 — so
> "no such symbol" is indistinguishable from a server fault and must be
> treated as unresolved rather than as an absent constraint.

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

**Use `GET /stock/tradable?stock_codes={CODE}`** on Carina, with the trading
token. It answers a row per requested code:

```json
{ "data": [ { "stock_code": "BBCA", "is_tradable": true,
              "is_sharia_tradable": true, "is_margin_tradable": true } ] }
```

Source: `sb-web/src/lib/api/trade.ts:103-107` (`checkStockTradable`) and
`sb-web/src/global/Securities/types/api/stockTradable.ts`. A response that
omits the code you asked about is *not* `is_tradable: false` — it is an
unanswered question, and `strategy/market` treats the two differently.

> **Correction (2026-09-15).** The rest of this section described the TRM
> **private** stock service, which a client cannot call: every RPC in
> `securities/trm/core/stock/private/v2/stock.proto` lacks a
> `google.api.http` annotation, so the gRPC gateway exposes no REST path for
> it. It is kept below because it documents the field semantics the public
> route returns, not because it is callable.

The TRM private stock service defines the same check:

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

> **Measured live, 2026-09-16:** `GET /stock/tradable` answers a row even for
> a code that does not exist (`ZZZZ` → `is_tradable: false`), so the flag
> conflates "suspended", "not permitted for this account" and "no such
> symbol". All three deny, so the conflation is safe for gating — but it
> means the flag cannot be used to decide whether a symbol is real.

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
Orders submitted against a halted book are auto-rejected.

> **Correction (2026-09-15): this message never reaches a client.**
> `CompanyTradingAction` is an ITCH/datafeed-internal message. The subscribe
> contract (`WebsocketChannel` in
> `securities/transactional/datafeed/v1/websocket.proto`) offers liveprice,
> runningTrade(+Batch), orderBook, bestBidOffer, iepiev, intraday, watchlist,
> isHotlist, marketMover and orderQueue — no trading-action or company-status
> channel — and the inbound oneof has no such variant either
> ([02-websocket-streaming.md §5](./02-websocket-streaming.md) lists all 15).
> "Track the latest `CompanyTradingAction`" is therefore not implementable.
>
> **Consequence for any pre-trade gate:** there is no independent suspension
> signal. `strategy/market` uses §7.1's broker tradability flag as the
> suspension proxy and labels it as one on the value itself
> (`market.SuspensionProxyNote`). The residual risk is one-directional: a
> suspension the broker flag has not caught yet produces an exchange
> auto-reject, not a bad fill.

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

> **How this repo gates it.** `engine.OrderIntent` carries a `BoardType`
> that the strategy must set, and `risk.checkStructural` admits `RG` only.
> The field exists because the strategy and the router make *different*
> claims: the router knows which board it sends to, the strategy knows which
> one it meant, and a mismatch is silent on both sides — a TN order on the
> regular book may be accepted and never match. The unset zero value is
> rejected rather than read as `RG`, so "nobody filled this in" cannot
> masquerade as a correct answer.

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

| Rule | How to fetch | Host / session | Reachable? |
|---|---|---|---|
| Holiday check | `GET /holidays/active` | backoffice gateway | ⚠️ gateway access unverified — `strategy/market` reads the curated `calendar/idx_holidays.json` (plan 14, ADR-0012) instead |
| Market open/closed | `GET /company-price-feed/market-time` | Exodus / main token | ✅ |
| Session phase | `GET /company-price-feed/market-time/session` | Exodus / main token | ✅ — the `?datetime=` parameter is optional and `sb-web` omits it; omitting it avoids an undocumented-timezone bug |
| Market cycle allocation | Trading-info endpoint, feature `FEATURE_MARKET_CYCLE` | Carina / trading token | not used |
| ARA / ARB price bands | `GET /nego-engine/v1/order-book/{code}` | Carina / trading token | ✅ — `uint64` fields arrive JSON-encoded as **strings** under the proto3 JSON mapping; parse both |
| Instrument tradability | `GET /stock/tradable?stock_codes=` | Carina / trading token | ✅ (corrected — `StockService.Info` is a private service with no HTTP route) |
| Suspension state | `CompanyTradingAction` messages | ITCH / datafeed-internal | ❌ **not on any client-subscribable channel** — see §7.2 |
| Tick size | `PriceTickSizeTable` + `OrderBookDirectory.price_tick_size_table_id` | ITCH stream | ❌ not on a client channel; `risk.isOnApproxTickIDR` uses the §5 banded approximation |
| Lot size | `OrderBookDirectory.shares_per_lot` or `Company.round_lot_size` | ITCH / datafeed stream | `engine.OrderIntent.Lots` is already a lot count, so the rule is typed rather than checked |
| Board type | Hardcoded per instrument type (`RG` / `TN`) | Client constant | ✅ gated — `engine.OrderIntent.BoardType`, admitted only as `RG` by `risk.checkStructural`; the unset zero value is rejected rather than defaulted |

Prev: [Order Placement ←](./03-order-placement.md) ·
Index: [README](../README.md)
