# ADR 0009 — Universe is the funding intersection; strategy fit is decided per (name, strategy) downstream

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** universe, screener, strategy, cost-gate, selection-bias

> Records a decision taken while completing [plan 09](../plans/09-universe-screener.md)
> and spinning out [plan 10](../plans/10-strategy-fit-and-portfolio.md). It revises
> two locked decisions in plan 09 §2; that plan keeps the struck-through originals
> for provenance.

## Context

Plan 09 originally defined the tradeable universe as
`funding-eligible (DBX ∪ MBX union) ∩ liquid ∩ not-edge-capped`, where the
edge-cap layer **dropped** any name whose one-tick spread already exceeded the
40 bps round-trip fee gate (e.g. BUMI at 168 IDR → ~59.5 bps → dropped).

Two problems surfaced once the screener ran live and the goal was restated:

1. **The edge-cap drop smuggled a strategy assumption into the universe.** A
   one-tick spread of 59.5 bps only makes a name untradeable *for a thin-edge
   intraday liquidity-taking strategy* (the class plan 06 already returned a
   NO-GO on). It does **not** make the name untradeable for a strategy whose net
   per-trade edge reliably clears that friction — a daily swing or event-driven
   strategy holding for a 200–500 bps move trades BUMI fine, just less often.
   Baking the cap into the universe definition re-introduces exactly the
   selection bias the screener exists to eliminate (plan 09 §1), only via cost
   structure instead of via a convenience sample.

2. **The funding gate was a union.** "In day-trade *or* trading-limit" admits
   names that support only one holding style, so the universe quietly favours
   whichever strategies that style permits.

The owner's restated intent (2026-06-19): be **data-centric**, pursue a
**combination of the most profitable strategies across horizons** (intraday
through daily-hold), and constrain the universe only to names where **all
funding options are unlocked**.

## Decision

1. **Universe membership = the funding intersection.** A name is in the universe
   iff it appears in **both** the day-trade and the trading-limit emitten
   subsectors (`day-trade ∩ trading-limit`). This is the single hard gate. It
   guarantees every member supports every holding style, so the universe carries
   no implicit strategy preference.

2. **Edge-cap and thinness are annotations, not drops.** The screener keeps every
   funded name in its ranked output and attaches per-name structural facts:
   `min_edge_to_clear_bps` (one-tick spread + the imported 40 bps round-trip
   fee), `is_edge_capped`, and `is_thin`. No name leaves the universe for a
   strategy-specific reason.

3. **Friction belongs to the `(name, strategy)` pair, not to the universe.** A
   name's `min_edge_to_clear_bps` is the *minimum net per-trade edge a strategy
   must beat on that name* — a pass threshold, not a membership test. Which
   strategy (and horizon) fits each name is decided by backtest downstream in
   [plan 10](../plans/10-strategy-fit-and-portfolio.md).

The 40 bps gate stays the single source of truth, imported from
`research/backtest/backtest.py`; `min_edge_to_clear_bps` is derived from it, not
a second constant.

## Rejected alternatives

**Keep the funding union.** Simpler and a larger candidate set. Rejected: a name
with only one funding option cannot support the full strategy combination the
owner wants, and the union lets holding-style availability bias the universe.
The intersection is the smaller but genuinely strategy-agnostic set.

**Keep edge-cap as a universe drop (original plan 09).** Rejected: it is correct
only under the intraday-taking assumption and discards names a wider-edge
strategy could trade. It also contradicts the screener's own signal-agnostic
charter. Demoting it to an annotation preserves the information (the flag and the
exact bps floor are still reported) without making the strategy choice on the
universe's behalf.

**One screened universe per strategy family.** Run a separate screen per strategy
with its own friction budget. Rejected (KISS/DRY): it duplicates the funding gate
and the metric computation N times. A single annotated universe plus a downstream
per-`(name, strategy)` backtest carries the same information with one screen.

## Consequences

**Positive**

- The universe is genuinely strategy-agnostic; it enforces only the hard funding
  constraint and hands an unbiased, annotated set to the strategy-fit layer.
- Cheap, high-friction names (BUMI ~100 bps floor, DEWA ~229 bps) are retained
  with an explicit, computable cost floor instead of silently discarded.
- The plan-06 NO-GO is correctly recontextualised: "intraday OBI/OFI does not
  clear 40 bps **on these cheap names**", not "these names are untradeable".
- Selection bias is reduced on both axes at once — names are no longer chosen by
  convenience (the original motivation) *nor* filtered by a single strategy's
  economics.

**Negative / costs**

- The intersection is a smaller universe than the union; some names drop out at
  the funding gate (e.g. the fixture's INDY, which is day-trade-only).
- `min_edge_to_clear_bps` models only spread + fees, not market impact or adverse
  selection, so it is a **necessary-not-sufficient** floor; the real pass test is
  the downstream net-of-cost backtest (plan 10), which adds modelled impact.
- The universe no longer yields a "tradeable list" by itself — a tradeable set
  now requires running plan 10's strategy-fit backtests. This is intentional but
  is added work before the universe produces an actionable watchlist.
- The `TOO_THIN_TURNOVER_IDR` floor remains a placeholder pending a position-size
  model (plan 09 §3); until then `is_thin` is advisory.

## Related

- [Plan 09 — universe screener](../plans/09-universe-screener.md) — the screener
  this revises (see its §2 revision note).
- [Plan 10 — strategy fit & portfolio](../plans/10-strategy-fit-and-portfolio.md)
  — where `(name, strategy)` fit is decided from data.
- [Plan 06 — fill simulator & backtest](../plans/06-fill-simulator-and-backtest.md)
  — the NO-GO this decision recontextualises.
- [reference 07 — fees and costs](../reference/07-fees-and-costs.md) — the 40 bps
  round-trip cost gate.
- `sb-screener-live-endpoints` (session memory) — the verified emitten + chartbit
  endpoints the live funding gate uses.
