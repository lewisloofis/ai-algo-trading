# Stockbit API Documentation (for Algo Trading)

This folder documents the full loop needed to run a strategy against the Stockbit
backend programmatically: authenticate, stream real-time market data, place and
manage orders, **track what happens to those orders**, read **account state** to
size them, and respect the **IDX pre-trade rules** that decide whether an order is
even accepted.

These docs were reverse-engineered from the three official Stockbit client
codebases in this repo:

- `sb-web/` — Next.js / TypeScript web client
- `sb-ios/` — Swift iOS client
- `sb-android/` — Kotlin Android client

The iOS and Android clients use the latest (`v6`) auth flow and the binary
Protobuf market-data feed, so they are the primary reference. The web client
confirms the same wire protocol with readable TypeScript and is cited where it
clarifies a payload.

## Contents

| Doc | What it covers |
|-----|----------------|
| [01-authentication.md](./01-authentication.md) | Logging into the Stockbit account, the separate trading (securities) account login with PIN, token storage, and **how to keep the session alive (token refresh)**. |
| [02-websocket-streaming.md](./02-websocket-streaming.md) | Connecting to the market-data WebSocket, the Protobuf subscribe protocol for **live price, running trade, and orderbook**, message schemas, heartbeat, and reconnection so the stream does not break mid-session. |
| [03-order-placement.md](./03-order-placement.md) | Placing and managing **trading orders** on the Carina backend: regular buy/sell, **smart orders (stop loss, take profit, trailing stop, bracket/OCO, volume trigger)**, amend, cancel, order retrieval, and the full enum reference. |
| [04-order-lifecycle.md](./04-order-lifecycle.md) | The other half of doc 03: **what happens after you submit** — the portfolio WebSocket order-update channel, the order **status state machine**, partial-fill / matching events, rejection reasons, and the REST polling fallback. This is the feedback loop an algo needs to track its own orders. |
| [05-account-state.md](./05-account-state.md) | Reading **account state to size orders**: available cash / buying power / trading limit, current positions & portfolio, and the **T+2 settlement** distinction (settled-vs-unsettled cash and lots) plus order-count limits. |
| [06-pre-trade-rules.md](./06-pre-trade-rules.md) | IDX microstructure rules an order must satisfy **before** it is sent or it auto-rejects: **tick size, ARA/ARB price bands**, lot size, market hours / session phases, holidays, instrument suspension/notations, and board types. |

A runnable **Go prototype** that ties all three docs together (auth → stream →
orderbook-imbalance strategy → live orders, dry-run by default) lives in
[`../collector/`](../collector/).

## Two independent sessions

Stockbit has **two distinct authenticated sessions** — understand this before
reading further:

1. **Main / social account session** — your Stockbit login (username + password).
   Grants access to social data, watchlists, charts, and — importantly — the
   **market-data WebSocket** (price, running trade, orderbook).
2. **Trading / securities account session** — a *separate* session unlocked with
   your **trading PIN** on top of the main session. Required only for
   order placement, portfolio, and balance. Streamed over a different
   "portfolio" WebSocket.

For pure market-data streaming you only need session #1. You need session #2
only when you want to place orders or stream your own portfolio/orders.

## Hosts (production)

| Service | Host | Purpose |
|---------|------|---------|
| Exodus (auth/user) | `https://exodus.stockbit.com` | Login, token refresh, WebSocket key |
| Carina (trading)   | `https://carina.stockbit.com` | Trading auth, orders, portfolio (REST) |
| Market-data WS     | `wss://wss-trading.stockbit.com/ws` | Price / running trade / orderbook stream |
| Portfolio WS       | `wss://ws-gen.stockbit.com/v1` | Your portfolio & order updates |
| Social WS          | `wss://wssocial.stockbit.com` | Chat / notifications |

> ⚠️ **Disclaimer.** These are private, undocumented endpoints used by Stockbit's
> own apps. They can change without notice, and automated/programmatic access may
> be subject to Stockbit's Terms of Service. Use your own account, respect rate
> limits, and confirm you are permitted to automate before running this in
> production.
