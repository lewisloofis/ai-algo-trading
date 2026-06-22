# ADR 0012 — Market calendar is one curated source of truth, read by thin per-language clients

- **Status:** Proposed
- **Date:** 2026-06-20
- **Deciders:** Lewis
- **Tags:** calendar, holidays, trading-days, financing, collector, cross-language

> Introduces an IDX trading-day/holiday calendar as shared infrastructure, used
> by both the Go collector (health/scheduling) and the Python research stack
> (trading-day math, financing/settlement). Corrects a latent undercount in
> [plan 10](../plans/10-strategy-fit-and-portfolio.md)'s cost gate that follows
> from [ADR-0011](./0011-capital-model-300m-leverage-sleeve.md)'s financing model,
> and complements [ADR-0003](./0003-market-hours-instance-scheduling.md)
> (weekday scheduling, which is holiday-blind today). Pinned by
> [plan 14](../plans/14-market-calendar-and-time-system.md).

## Context

Two subsystems independently need to answer "is *this* date a trading day, and
how many trading/calendar days lie between A and B?" — and neither can today:

1. **The Go collector.** [ADR-0003](./0003-market-hours-instance-scheduling.md)
   boots the EC2 box on a weekday EventBridge schedule. EventBridge cron cannot
   know public holidays, so the box wakes on IDX holidays, connects, sees no
   data, and any health logic reads that as an outage rather than "market closed."
   The collector has **zero holiday awareness** (confirmed: no holiday/weekday
   gating in `collector/`).

2. **The Python research stack.** `research/lib/session.py` handles only
   *intraday time-of-day* boundaries (open/close, the lunch gap) — it has no
   concept of a trading **day** or a holiday. Yet plan 10's cost gate, per
   ADR-0011, charges `financing_bps = 10 × calendar_days_outstanding(entry_dow,
   hold)`, where calendar days outstanding is driven by **T+2 settlement**. The
   current implementation approximates this with a fixed `SETTLEMENT_TRADING_DAYS
   = 1` and weekend-only arithmetic. That is **holiday-blind**: a Thursday entry
   whose settlement leg crosses a Monday public holiday spans 5 calendar days,
   not 4, so financing (and therefore the hurdle) is **understated** exactly when
   it matters most. This is a correctness gap in the gate, not a convenience item.

A third, smaller need surfaced operationally: dated reminders such as "re-pull
the chartbit intraday store before its rolling-90-day window leaves a gap" (see
[plan 10](../plans/10-strategy-fit-and-portfolio.md) intraday-accrual note). That
is *operational* data about our own tasks, distinct from *market* data about the
world, but it shares the same "what's the trading-day math?" dependency.

The cross-cutting constraint that forces the shape of the decision: **the two
code consumers are in different languages.** A Python market-calendar library
(`pandas_market_calendars`, `python-holidays`) cannot serve the Go collector, and
duplicating a holiday list in Go and Python source guarantees they drift.

## Decision

1. **One committed data file is the single source of truth.** IDX holidays live
   in a language-neutral, version-controlled file (`calendar/idx_holidays.json`),
   listing each non-trading date with a description and a `kind`
   (`public_holiday` | `collective_leave` | `exchange_closure`). Both languages
   read this same file; neither hard-codes dates.

2. **The data is curated from the official IDX schedule**, published at
   `idx.co.id` (*Tentang BEI → Jadwal Libur Bursa*). IDX publishes the next
   year's schedule as an image, so the file is **hand-maintained** once per year
   from that authoritative source and reviewed in git. No third-party holiday
   library is taken as a dependency or as ground truth — at most one may be used
   offline to *draft* a year, but the committed file, reconciled against the IDX
   image, is authoritative. Each entry carries a `source` note and the file
   carries the IDX publication it was transcribed from.

3. **Thin per-language readers, no logic duplication.** A Python reader
   (`research/lib/market_calendar.py`) and a Go reader
   (`collector/internal/calendar`) each load the JSON and expose the same small
   vocabulary: `is_trading_day`, `next/prev_trading_day`, `add_trading_days`,
   `trading_days_between`, and `settlement_calendar_days(entry_date, t_plus)`.
   Behaviour is defined once in plan 14's spec and verified by parallel test
   suites in each language reading the same fixture.

4. **Weekend rule is code; holidays are data.** Saturdays/Sundays are a constant
   weekmask in each reader; only the irregular public-holiday/collective-leave
   dates come from the file. This keeps the file small and auditable (it never
   lists weekends).

5. **Operational deadlines are a separate file, not market data.** Dated
   reminders live in `calendar/deadlines.yaml` (or `.md`) — distinct from
   `idx_holidays.json` — and are consumed by a small checker, never by the
   collector or the cost gate. Conflating "the market is closed" with "we owe
   ourselves a task" is explicitly rejected (see below).

6. **Holiday-blind scheduling stays; the box self-skips.** ADR-0003's weekday
   EventBridge schedule is unchanged (EventBridge still cannot read the file).
   Instead the collector consults the calendar **at boot** and shuts back down on
   a holiday, rather than moving holiday logic into infrastructure.

## Rejected alternatives

**A Python calendar library as the source of truth.** Simplest for the research
side. Rejected: it cannot serve the Go collector, and the IDX coverage in
general-purpose holiday libraries is incomplete/imperfect (collective-leave days,
*cuti bersama*, and ad-hoc exchange closures are frequently missing or wrong).
The authoritative artifact must be language-neutral and IDX-sourced.

**Duplicate holiday lists in Go and Python source.** No shared file, no I/O.
Rejected: guaranteed drift — the next yearly update would have to touch two code
files in lock-step, and a missed one silently corrupts either the cost gate or
the collector's health view.

**Push holiday logic into EventBridge / Terraform.** Have the scheduler skip
holidays so the box never boots on a closed day. Rejected: EventBridge cron has
no holiday concept; encoding dates in IaC splits the source of truth and couples
a yearly data refresh to a `tofu apply`. The boot-time self-skip keeps the box's
~minutes of idle cost (negligible per the plan-01 cost envelope) in exchange for
one source of truth.

**Fold deadlines into the holiday file.** One "calendar" file for everything.
Rejected: market holidays are externally-defined facts on a yearly cadence;
operational deadlines are internal, frequently-changing, and consumed by entirely
different code. Merging them couples unrelated change cadences and invites a
consumer (the cost gate) to accidentally read our TODOs.

## Consequences

**Positive**

- The plan-10 cost gate becomes holiday-correct: settlement-spanning financing
  counts real calendar days across holidays, closing the understated-hurdle bug.
- The collector stops mistaking IDX holidays for outages, and avoids booting into
  a dead session.
- Trading-day math (MIN_EFF_N windows, walk-forward roll, the chartbit re-pull
  deadline) shares one definition instead of ad-hoc weekend arithmetic.
- A single yearly edit (one JSON file) keeps every consumer correct in both
  languages; the diff is the audit record.

**Negative / costs**

- A **yearly manual step**: transcribe the next year's IDX schedule from the
  official image into the JSON and review it. This is accepted as the price of an
  authoritative, dependency-free source (and is a calendar deadline itself).
- Until reconciled against the official IDX image, any draft year (the seeded
  2026 list in plan 14) is *provisional* and must be flagged as such.
- Two new small readers to maintain (Go + Python), kept in sync by a shared
  fixture and parallel tests rather than shared code — an accepted cross-language
  cost.

## Related

- [Plan 14 — market calendar & time system](../plans/14-market-calendar-and-time-system.md)
  — the implementation this pins (file schema, reader API, financing wiring).
- [ADR-0011 — capital model](./0011-capital-model-300m-leverage-sleeve.md) — the
  financing/settlement model whose calendar-day count this makes holiday-correct.
- [ADR-0003 — weekday market-hours scheduling](./0003-market-hours-instance-scheduling.md)
  — the holiday-blind scheduler this complements with a boot-time self-skip.
- [Plan 10 — strategy fit & portfolio](../plans/10-strategy-fit-and-portfolio.md)
  — the cost-gate consumer, and the source of the chartbit re-pull deadline.
- [ADR-0008 — top-level layout](./0008-top-level-layout-mirrors-pillars.md) — why
  shared cross-pillar data sits at the repo top level (`calendar/`).
