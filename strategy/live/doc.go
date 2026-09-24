// Package live is a Carina order-routing sink for engine.OrderSink (plan 18
// §5 B4, docs/plans/18-alpha-and-infrastructure-split.md). It is the ONLY
// package in this module that sends a non-GET request to Carina —
// strategy/account is structurally read-only (see its readonly_test.go),
// engine.PaperSink only ever simulates fills, and strategy/risk gates
// intents without ever placing one. That makes this package the sleeve's
// entire blast radius for "sent a real order by mistake": everything
// upstream of it can be exercised against a replay or a fake sink without
// ever touching a broker.
//
// # Scope: REST polling, not the portfolio WebSocket
//
// Order state comes from GET /order/v2/detail and GET /order/v2/list
// (docs/reference/04-order-lifecycle.md §6), not the portfolio WebSocket
// (wss://ws-gen.stockbit.com/v1 — a second protobuf protocol behind a
// second, cookie-based auth scheme, §1-§2 of the same doc). This is a
// deliberate scope cut, not an oversight: strategy/strategies/rebalance
// trades in one daily window at 15:00 WIB, so a few seconds of polling
// latency costs nothing there, and the WS path can be added later behind
// the same Fills() channel without changing a caller — Sink already looks
// like an async sink from engine's point of view (see engine.AsyncFill), so
// swapping the delivery mechanism underneath it is an internal change, not
// an interface break. Also out of scope: smart orders (stop loss, take
// profit, trailing stop, bracket, volume trigger — this package places only
// PLATFORM_ORDER_TYPE_LIMIT_DAY regular orders), order slicing and
// participation caps (B5), and day-trade/leverage endpoints (ADR-0017: the
// sleeve trades owned cash only, never on margin).
//
// # Client order id vs broker order id — the one thing that must not get
// swapped
//
// Sink.Submit takes an engine.OrderIntent whose OrderID is a strategy-minted,
// human-readable id (e.g. "rebalance-TINS-1" — see
// strategy/strategies/rebalance/rebalance.go's newOrderID) that is not
// unique across process restarts. Carina's POST /order/v2/buy|sell instead
// returns its own numeric broker order id, which every subsequent
// Detail/Cancel/Amend call must use. Sink.Submit nonetheless returns the
// CLIENT id, and every engine.AsyncFill it emits carries the client id in
// Fill.OrderID, never the broker id: a strategy correlates a fill back to
// its own bookkeeping through the id it minted (rebalance.go's s.orders map
// is keyed by that id, not by anything Carina assigned), and the post-trade
// store's intent row was written under the same client id. Getting this
// backwards would silently break OnFill for every strategy at once — the
// symptom would look exactly like "fills stopped arriving", not a crash.
// The client id/broker id/ui_ref triple and how Sink keeps them mapped to
// each other is documented on Sink itself (sink.go) and on NewUIRef
// (uiref.go).
//
// # The double-arm gate
//
// NewClient refuses to construct at all unless the Arming passed to it
// reports Armed() — see armed.go. There is no dry-run fallback the way the
// prototype's strategy/broker.go has (logging "DRY-RUN" and returning
// success whenever ALGO_LIVE is unset): a router that pretends to trade
// while silently not trading is a worse failure than refusing to construct,
// because a caller reading its own logs can no longer tell simulated flow
// from real flow. Dry-run is engine.PaperSink's job, not this package's.
//
// # An open question this package does not resolve
//
// None of Carina's order-mutation or order-query endpoints
// (docs/reference/03-order-placement.md, docs/reference/04-order-lifecycle.md
// §6) accept an account-scoping parameter — unlike GET /portfolio/v2/list
// and GET /balance/withdrawable, which honour acc_no / account_number
// (docs/plans/18-alpha-and-infrastructure-split.md §3.4). Client stores
// accNo (constructor-required, mirroring strategy/account.New's own
// convention) but has no confirmed way to attach it to a placed order:
// there is currently no documented mechanism by which this package can
// prove an order lands in the sleeve sub-account (008488L) rather than the
// core book (0001958) that shares the same trading token. Whoever wires
// this package for real trading must independently confirm which account
// actually absorbs an order — the same way plan 18 §3.4 confirmed
// portfolio/balance scoping, by placing one and checking which account's
// holdings moved — before this sink is trusted with anything beyond the
// smallest possible size. This is flagged here, in the open, rather than
// silently assumed away.
package live
