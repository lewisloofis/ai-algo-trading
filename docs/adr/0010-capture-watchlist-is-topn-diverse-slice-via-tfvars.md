# ADR 0010 — Capture watchlist is a top-N broad/diverse slice of the screened universe, delivered via tfvars

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** Lewis
- **Tags:** screener, collector, watchlist, capture, infra

> Records the decision that wires [plan 09](../plans/09-universe-screener.md)'s
> screened universe into the collector's capture watchlist (plan 09 §7.2b).
> Builds on [ADR-0006](./0006-watchlist-from-ssm.md) (watchlist from SSM) and
> [ADR-0009](./0009-universe-is-funding-intersection-strategy-fit-downstream.md)
> (the universe is strategy-agnostic).

## Context

The screener (plan 09) produces the funding-intersection universe — dozens to
~100+ names, ranked by liquidity and annotated with structural friction
(`min_edge_to_clear_bps`, `is_edge_capped`, `is_thin`). ADR-0009 made that
universe deliberately strategy-agnostic.

The collector (plan 01) captures **intraday order-book microstructure over a
WebSocket for only ~10–50 symbols** — the repo's stated capacity envelope, bounded
by the `t4g.nano` and bandwidth/cost. It reads its symbol list from
`CAPTURE_WATCHLIST_FILE` / the SSM `/sb-collector/watchlist` parameter
(comma-separated), which is populated by `tofu apply` from `var.watchlist`
(ADR-0006).

Two facts collide: the screened universe is far larger than capture capacity, and
the screener previously emitted **only** a markdown report — no machine-readable
list. So "the ranked list becomes the next capture watchlist" (plan 09 §7.2) was
true on paper but unwired: a human read the table and retyped symbols. Closing the
screen → capture loop requires deciding *what* to emit, *how many*, *which* names,
and *how* they reach the collector.

## Decision

1. **The screener emits `research/out/watchlist.txt`** in the collector's exact
   `CAPTURE_WATCHLIST_FILE` format — one bare symbol per line; `#`-prefixed and
   blank lines ignored; metadata (rank/band/friction/turnover and a paste-ready
   `watchlist = "…"` tfvars line) lives only on `#` lines. Controlled by
   `--watchlist-out` / `--no-watchlist`. No collector change is needed — it
   already parses this format.

2. **The watchlist is a top-N slice** (default `N=30`, `--top`), because capture
   capacity (~10–50) is far smaller than the universe. `--top 0` emits all.

3. **The slice policy is "broad/diverse across friction bands"** — a round-robin
   across `tight` (`min_spread < 20 bps`) / `moderate` (`20–40`) / `wide`
   (`≥ 40`, i.e. edge-capped) bands, liquidity-first within each band, until N is
   reached or the bands exhaust. This guarantees the capture set spans the friction
   spectrum so [plan 10](../plans/10-strategy-fit-and-portfolio.md) gets both
   intraday-suited (tight) and swing/event-suited (wide) names to study.

4. **The capture watchlist may be mildly strategy-aware; the universe is not.**
   Banding by friction is a capture-selection heuristic, scoped to this one
   selection step. It must never leak back into the universe *definition* (that
   stays strategy-agnostic per ADR-0009).

5. **Delivery stays via tfvars + `tofu apply` (ADR-0006).** The screener does
   **not** write SSM. The operator pastes the generated `watchlist = "…"` line into
   `terraform.tfvars` and applies; it takes effect on the collector's next boot.

## Rejected alternatives

**Screener writes SSM directly.** Removes the manual paste step. Rejected: it
breaks ADR-0006's model where Terraform is authoritative and the capture set is
auditable IaC. An out-of-band script mutating production `/sb-collector/watchlist`
has no review/rollback story and can silently diverge from `tfvars`. The
paste-into-tfvars step *is* the audit point, and it is cheap.

**Top-N purely by liquidity.** Simplest slice. Rejected: it over-weights whichever
band dominates the liquid tail and can spend scarce capture slots on names a given
strategy cannot exploit — e.g. BUMI ranks #1 by turnover yet is edge-capped
(intraday-implausible). A pure-liquidity slice could fill all 30 slots with names
that share one friction profile, starving plan 10 of contrast.

**Low-friction-first (capture only intraday-plausible names).** Tempting given the
plan-06 NO-GO was about intraday friction. Rejected as the default: plan 10
explicitly intends to test swing/event strategies, which need data on
higher-friction names. (A friction-filtered slice remains achievable later by
pre-filtering before `--top` if a study calls for it.)

**Capture the entire universe.** Rejected — impossible: it exceeds the collector's
~10–50 symbol WebSocket capacity and the cost/bandwidth envelope.

## Consequences

**Positive**

- The screen → capture loop is closed with a machine-readable artifact; the next
  capture set is data-chosen, not retyped from memory.
- The capture set is diverse by construction, giving the strategy-fit work
  (plan 10) names across the friction spectrum.
- Delivery remains auditable IaC (ADR-0006); no production SSM is mutated by a
  script; no collector code changed.

**Negative / costs**

- `N=30` and the band thresholds (`20` / `40` bps) are heuristics pinned now and
  expected to move once a target position size and the plan-10 results exist.
- The broad/diverse policy deliberately spends some capture slots on high-friction
  names that intraday strategies cannot use — accepted, to feed swing/event study.
- The manual paste-to-tfvars step remains (by design, not a gap).
- This selection is the **one** place a mild strategy-awareness enters an otherwise
  strategy-agnostic pipeline; it is fenced to the capture-slice step and must be
  kept out of the universe definition (ADR-0009).

## Related

- [Plan 09 §7.2b — universe screener](../plans/09-universe-screener.md) — the emit
  this records.
- [ADR-0006 — watchlist from SSM](./0006-watchlist-from-ssm.md) — the delivery path
  this reuses.
- [ADR-0009 — universe is the funding intersection](./0009-universe-is-funding-intersection-strategy-fit-downstream.md)
  — the strategy-agnostic universe this slices from.
- [Plan 10 — strategy fit & portfolio](../plans/10-strategy-fit-and-portfolio.md)
  — the consumer that motivates the diverse slice.
- [ADR-0002 — segment storage](./0002-segment-storage-and-compression.md) — the
  `watchlist_id` that labels captured segments.
