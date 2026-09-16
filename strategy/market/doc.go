// Package market is the endpoint-backed implementation of risk.MarketGate:
// the pre-trade questions from docs/reference/06-pre-trade-rules.md that
// strategy/risk deliberately cannot answer for itself because answering
// them needs a network call.
//
// Until this package existed, NewRiskSink's only MarketGate was
// risk.DenyAllGate — every order denied, which is safe but means plan 18's
// B2 box "wire reference 06 rules as gates, not advisories" stayed open and
// ADR-0016 gate G2/G3 stayed unsatisfied. This package closes that gate: it
// is a B4 prerequisite, not a convenience.
//
// # Where each field comes from
//
// One row per risk.MarketState field. The "verified" column records how the
// contract was established, because two rows of reference 06 §10 turned out
// to be wrong when checked against the actual protos and clients (see
// "Corrections to reference 06" below).
//
//	Field         Source                                            Host     Token
//	NotHoliday    collector/calendar (embedded IDX holiday file)    none     none
//	MarketOpen    /company-price-feed/market-time          +        exodus   main
//	              /company-price-feed/market-time/session
//	Tradable      /stock/tradable?stock_codes=SYM                   carina   trading
//	NotSuspended  /stock/tradable (SEE THE CAVEAT BELOW)            carina   trading
//	WithinBand    /nego-engine/v1/order-book/{SYM}                  carina   trading
//
// Two distinct sessions are in play, which is why Client takes a TokenSource
// with two methods rather than one bearer string: the market-time endpoints
// live on Exodus and take the main account token, while tradability and the
// ARA/ARB band live on Carina and take the PIN-gated trading token
// (docs/reference/README.md, "Two independent sessions").
//
// # The NotSuspended caveat — one source answering two questions
//
// NotSuspended is NOT independently sourced. It is derived from the same
// /stock/tradable response that answers Tradable, and this is a real
// limitation rather than an oversight:
//
//   - Reference 06 §7.1 says to read is_tradable from the TRM private
//     StockService.Info. That service is unreachable from a client:
//     sb-protos/proto/securities/trm/core/stock/private/v2/stock.proto
//     declares no google.api.http annotation on any of its four RPCs, so
//     the gRPC gateway exposes no REST route for it. The route this package
//     uses instead — GET /stock/tradable?stock_codes= — is the one
//     Stockbit's own web client calls (sb-web/src/lib/api/trade.ts:103-107).
//
//   - Reference 06 §7.2 says to track CompanyTradingAction.suspend_state
//     from the ITCH stream. That message is not on any channel a client can
//     subscribe to: WebsocketChannel
//     (sb-protos/proto/securities/transactional/datafeed/v1/websocket.proto)
//     offers liveprice, runningTrade(+Batch), orderBook, bestBidOffer,
//     iepiev, intraday, watchlist, isHotlist, marketMover and orderQueue —
//     and no trading-action or company-status channel. The inbound oneof
//     has no such variant either (docs/reference/02-websocket-streaming.md
//     §5 lists all 15).
//
// So the broker's own tradability flag is used as the suspension proxy. The
// direction of the residual risk matters: an IDX suspension that the broker
// flag has not yet picked up produces an order the exchange auto-rejects,
// not a fill at a bad price. A rejected order costs an order-count slot and
// a log line. The opposite error — refusing to trade a tradable name — is
// the direction this package is deliberately biased toward everywhere else.
//
// Every answer records which source produced it, so a caller never has to
// read this comment to find out: Gate.Detail returns a FieldResult per
// field carrying its Source and Reason, and the suspension proxy's source
// is the self-describing SuspensionProxyNote.
//
// # Fail closed, and never on stale data
//
// risk.TriState has three values and only Allow passes. Every fetch failure,
// unparseable payload, unrecognised enum value, and expired cache entry
// leaves its field Unknown, which rejects. There is exactly one rule for
// staleness: a cache entry past its TTL is refetched, and if the refetch
// fails the field goes Unknown. A stale value is never served as an answer.
// TestGate_StaleCacheIsNeverServed pins that.
//
// # Corrections to reference 06
//
// Recorded here and in the reference doc itself, following the B1 precedent
// (plan 18 §10) of correcting the contract docs against what the API
// actually exposes:
//
//   - §7.1's StockService.Info is not client-reachable (no HTTP annotation);
//     GET /stock/tradable is.
//   - §7.2's CompanyTradingAction is not on any subscribable WS channel.
//
// # What this package does not do
//
// No orders. Like strategy/account this is structurally read-only: get is
// the only request path and http.MethodGet the only method constant, with
// readonly_test.go failing the build if that stops being true.
package market
