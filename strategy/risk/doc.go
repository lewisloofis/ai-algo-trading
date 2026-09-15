// Package risk is the pre-trade risk layer for plan 18 §5, Track B step B2
// (docs/plans/18-alpha-and-infrastructure-split.md), and implements ADR-0016
// gates G2/G3 (docs/adr/0016-alpha-and-infrastructure-are-separate-tracks.md).
//
// RiskSink decorates an engine.OrderSink (strategy/engine/sink.go): it wraps
// an inner sink and runs every pre-trade gate in Submit before delegating.
// A rejected intent never reaches the inner sink. engine.Run is untouched —
// RiskSink is just another OrderSink from the engine's point of view, which
// is what makes this package testable with no engine, no network, and no
// orders.
//
// This package never places an order or makes a network call itself — see
// structural_test.go, which fails the build if one is ever added. Two
// consequences follow directly from that constraint:
//
//   - Sleeve equity (ADR-0017) is injected as a SleeveEquityIDR, sourced by
//     the caller from the scoped GET /balance/withdrawable endpoint, never
//     fetched here. GET /balance/cash/info reports the core book's ~377M
//     trade limit regardless of account number — roughly 25x the real
//     15M-IDR sleeve — so a cap computed from it would be wrong by 25x.
//     SleeveEquityIDR exists as a distinct type specifically so that number
//     cannot be passed in without an explicit, reviewable conversion.
//   - The endpoint-backed pre-trade rules from reference 06 (holiday,
//     session phase, is_tradable, suspension, ARA/ARB bands) are answered by
//     an injected MarketGate, not fetched here. Every field of the state a
//     MarketGate returns is a TriState, not a bool: Unknown is a common,
//     legitimate state (not yet warmed up, endpoint not wired, stale cache)
//     and it must reject, exactly like Deny — a bare bool's zero value would
//     silently read "haven't checked" as "confirmed safe".
//
// The daily loss limit needs realized+unrealized PnL for the session, which
// lives in strategy/account and the post-trade store; it is injected behind
// a small PnLProvider interface for the same reason — this package stays
// pure and synchronously testable.
//
// The kill switch is trippable two ways: in-process (RiskSink.TripKillSwitch,
// and automatically on a confirmed daily-loss breach) and externally, via the
// presence of a file at a configured path, checked on every Submit. Once
// tripped, either way, it stays tripped for the life of the process — no
// auto-recovery.
//
// Configuration (Limits, SleeveEquityIDR, the MarketGate/PnLProvider/Clock
// injected at construction) is set once by NewRiskSink and never mutated.
// The mutable counters — the day's cumulative notional, each symbol's
// tracked position, and the kill switch's tripped-ness — live in state.go
// and killswitch.go respectively, each guarded by its own mutex; see their
// doc comments for the concurrency contract. engine.Run drives Submit from a
// single goroutine, but the external kill-switch file and any future ops
// surface are not on that goroutine, so this package assumes concurrent
// callers throughout.
package risk
