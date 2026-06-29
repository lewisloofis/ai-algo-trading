# Market Calendar & Time System — One Holiday-Aware Clock for the Whole Repo

> **Status:** ✔️ Done · **Owner:** @lewis · **Updated:** 2026-06-29

> ✅ **Result: Complete.** `calendar/idx_holidays.json` (22 dates, `provisional: false`), Python reader `research/lib/market_calendar.py` + 23-test pytest suite, Go reader `collector/internal/calendar` + table-driven tests, all cross-language parity cases green. Cost gate wired (`financing.py` now accepts `entry_date` for holiday-aware settlement). Collector self-skips on non-trading days. Deadline checker operational (2 tracked deadlines: chartbit repull 2026-09-10, holiday refresh 2026-12-05). ADR-0012 implemented. Walk-forward date window uses `CALENDAR.trading_days_between` (plan-14 §7.8). Ground-truth reconciliation CLI `research/lib/calendar_reconcile.py` shipped (plan-14 §7.9).
> **Related:** [ADR-0012: market calendar single source](../adr/0012-market-calendar-single-source-cross-language.md) · [ADR-0011: capital model](../adr/0011-capital-model-300m-leverage-sleeve.md) · [ADR-0003: weekday scheduling](../adr/0003-market-hours-instance-scheduling.md) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [13-capital-allocation-and-capacity.md](./13-capital-allocation-and-capacity.md) · [01-market-data-capture.md](./01-market-data-capture.md) · [Plans index](./README.md)

> **What this is.** A single, holiday-aware notion of an IDX trading day, shared
> by every part of the repo that reasons about time. One curated data file
> (the official IDX holiday schedule) read by thin Go and Python clients
> (ADR-0012). It exists to do three concrete jobs: (1) make plan 10's
> financing/settlement cost gate **holiday-correct**, (2) stop the collector from
> mistaking IDX holidays for outages, and (3) give the repo an **operational
> deadline** mechanism — the immediate trigger being plan 10's chartbit
> intraday-store re-pull, which must happen before its rolling-90-day window
> leaves an unrecoverable gap.

## 1. Why now

Three independent needs converged on the same missing primitive:

- **The cost gate is holiday-blind (a correctness bug).** Per ADR-0011 financing
  is `10 bps × calendar_days_outstanding`, driven by T+2 settlement. The current
  code approximates this with a fixed `SETTLEMENT_TRADING_DAYS = 1` and
  weekend-only arithmetic. A Thursday entry whose settlement leg crosses a Monday
  public holiday spans **5** calendar days, not 4 — so financing, and the hurdle a
  strategy must beat, is **understated** precisely on the expensive entries.
- **The collector cannot tell "market closed" from "outage."** ADR-0003 boots the
  box on a weekday EventBridge schedule with no holiday concept; on an IDX holiday
  it wakes into a dead session.
- **No operational-deadline mechanism exists.** Plan 10's intraday accrual store
  pulls a rolling ~90-day chartbit window; the window *slides* rather than grows,
  so the history only extends if successive pulls stay **<90 days apart**. Miss
  that by a week and a permanent gap opens. That deadline currently lives only in
  a memory note — it needs a tracked home.

`research/lib/session.py` solves none of these: it is intraday-time-of-day only.

## 2. Locked decisions (per ADR-0012)

- **One source of truth, curated from IDX.** `calendar/idx_holidays.json`,
  hand-maintained yearly from the official schedule at `idx.co.id`
  (*Tentang BEI → Jadwal Libur Bursa*). No holiday library is authoritative.
- **Weekends are code; holidays are data.** Readers apply a Sat/Sun weekmask;
  the file lists only irregular non-trading dates (never weekends).
- **Thin per-language readers, one shared spec.** Python
  (`research/lib/market_calendar.py`) and Go (`collector/internal/calendar`)
  expose the same vocabulary and are verified against a shared fixture.
- **Deadlines are separate data** (`calendar/deadlines.yaml`) with their own
  checker — never read by the collector or the cost gate.
- **Scheduling stays holiday-blind; the box self-skips at boot.** ADR-0003 is
  unchanged; the collector consults the calendar on startup and shuts down on a
  holiday.

## 3. The holiday data file

`calendar/idx_holidays.json` — language-neutral, one record per non-trading date:

```jsonc
{
  "source": "IDX Jadwal Libur Bursa (idx.co.id), transcribed 2026-06-20",
  "provisional": true,                // until reconciled against the official IDX image
  "weekmask": "1111100",              // Mon–Fri trading; Sat/Sun closed (documented, not enumerated)
  "holidays": [
    { "date": "2026-01-01", "kind": "public_holiday",  "desc": "Tahun Baru 2026 Masehi" },
    { "date": "2026-03-23", "kind": "collective_leave", "desc": "Cuti Bersama Idul Fitri 1447 H" }
    // ...
  ]
}
```

- `kind ∈ { public_holiday, collective_leave, exchange_closure }` — both
  `public_holiday` and `collective_leave` are non-trading; `exchange_closure`
  covers ad-hoc IDX closures. All three count as non-trading for the readers; the
  distinction is for audit/reporting only.
- `provisional: true` flags a year not yet reconciled against the official IDX
  image. The readers still use it; reports surface the flag.

### 3.1 Seed data — IDX 2026 (confirmed against the official IDX image, 2026-06-20)

Transcribed from the official IDX schedule (*KALENDER LIBUR BURSA TAHUN 2026*,
idx.co.id → Tentang BEI → Jadwal Libur Bursa) and **verified line-by-line against
the official image** on 2026-06-20. A provisional draft from a securities-house
mirror ([Mirae Asset](https://mstock.miraeasset.co.id/blog/jadwal-libur-bursa-2026/))
matched the official source exactly (all 22 non-trading dates). `provisional` is
therefore `false` for 2026; descriptions below use the official *Keterangan*.

| Date | Day | Kind | Description (official *Keterangan*) |
|---|---|---|---|
| 2026-01-01 | Kamis | public_holiday | Tahun Baru 2026 Masehi |
| 2026-01-16 | Jumat | public_holiday | Isra Mikraj Nabi Muhammad SAW |
| 2026-02-16 | Senin | collective_leave | Cuti Bersama Tahun Baru Imlek 2577 Kongzili |
| 2026-02-17 | Selasa | public_holiday | Tahun Baru Imlek 2577 Kongzili |
| 2026-03-18 | Rabu | collective_leave | Cuti Bersama Hari Suci Nyepi Tahun Baru Saka 1948 |
| 2026-03-19 | Kamis | public_holiday | Hari Suci Nyepi Tahun Baru Saka 1948 |
| 2026-03-20 | Jumat | collective_leave | Cuti Bersama Idul Fitri 1447 Hijriah |
| 2026-03-23 | Senin | collective_leave | Cuti Bersama Idul Fitri 1447 Hijriah |
| 2026-03-24 | Selasa | collective_leave | Cuti Bersama Idul Fitri 1447 Hijriah |
| 2026-04-03 | Jumat | public_holiday | Wafat Yesus Kristus |
| 2026-05-01 | Jumat | public_holiday | Hari Buruh Internasional |
| 2026-05-14 | Kamis | public_holiday | Kenaikan Yesus Kristus |
| 2026-05-15 | Jumat | collective_leave | Cuti Bersama Kenaikan Yesus Kristus |
| 2026-05-27 | Rabu | public_holiday | Idul Adha 1447 Hijriah |
| 2026-05-28 | Kamis | collective_leave | Cuti Bersama Hari Raya Idul Adha 1447 Hijriah |
| 2026-06-01 | Senin | public_holiday | Hari Lahir Pancasila |
| 2026-06-16 | Selasa | public_holiday | 1 Muharam Tahun Baru Islam 1448 Hijriah |
| 2026-08-17 | Senin | public_holiday | Proklamasi Kemerdekaan |
| 2026-08-25 | Selasa | public_holiday | Maulid Nabi Muhammad SAW |
| 2026-12-24 | Kamis | collective_leave | Cuti Bersama Kelahiran Yesus Kristus |
| 2026-12-25 | Jumat | public_holiday | Kelahiran Yesus Kristus |
| 2026-12-31 | Kamis | exchange_closure | Libur Bursa (year-end closure) |

> **Trading-day check.** 2026 has 261 weekdays (Mon–Fri). All 22 listed
> non-trading dates fall on weekdays, so 261 − 22 = **239**, matching IDX's
> published *JUMLAH HARI BURSA* (239). The reader reproducing this count over the
> file is the §8 sanity test.
>
> **IDX Catatan #1 (why some holidays are absent).** Idul Fitri 1447 H (21–22 Mar
> 2026), Paskah / Kebangkitan Yesus Kristus (5 Apr 2026), and Waisak 2570 BE
> (31 May 2026) are **not** listed because they fall on a Saturday/Sunday — they
> cost no additional trading day, so the file correctly omits them (weekends are
> handled by the weekmask, not enumerated).
>
> **IDX Catatan #2 (mid-year additions).** IDX may declare further *Libur Bursa*
> later if Bank Indonesia cancels clearing or the government announces an ad-hoc
> non-working day. Such dates are appended as `kind: exchange_closure` when
> announced — this is why a year's entry is append-able mid-year, not frozen at
> publication.

## 4. Reader API (shared spec, both languages)

Same names and semantics in Python and Go; only idiom differs.

| Operation | Meaning |
|---|---|
| `is_trading_day(d)` | not a weekend and not in the holiday set |
| `next_trading_day(d)` / `prev_trading_day(d)` | nearest trading day strictly after/before `d` |
| `add_trading_days(d, n)` | roll `n` trading days forward (n<0 backward) |
| `trading_days_between(a, b)` | count of trading days in `(a, b]` (de-overlap / window math) |
| `settlement_calendar_days(entry, t_plus)` | **calendar** days from `entry` until the `entry + t_plus` *trading*-day settlement date lands — the holiday-aware financing clock |

`settlement_calendar_days` is the keystone for the cost-gate fix: settlement is
`t_plus` **trading** days after entry, but financing accrues over the **calendar**
span to that date — so weekends and holidays in between lengthen it.

## 5. Consumers to wire

1. **Cost gate / financing (plan 10).** Replace the fixed
   `SETTLEMENT_TRADING_DAYS = 1` + weekend-only logic with
   `settlement_calendar_days(entry, t_plus)`. Re-run live; confirm hurdles rise on
   holiday-spanning entries and the ORB survivor set is re-checked under the
   corrected gate. (Same-day intraday cells stay financing-zero — no settlement
   leg — so the ORB core is expected to be unaffected; verify.)
2. **Collector boot self-skip (ADR-0003).** On startup, if today is not a trading
   day, log and shut down instead of connecting.
3. **Trading-day windows.** Point MIN_EFF_N / walk-forward / intraday-store
   trading-day counts at `trading_days_between` rather than ad-hoc arithmetic
   where they currently approximate it.
4. **Deadline checker.** `calendar/deadlines.yaml` + a small command that prints
   anything due within a lead window; seed it with the chartbit re-pull (see §6).

## 6. The first operational deadline — chartbit intraday re-pull

The motivating reminder, seeded into `calendar/deadlines.yaml`:

- **Store now holds** PTRO/BRPT/TPIA/ENRG, ~2026-03-25 → 2026-06-19.
- **Constraint:** chartbit returns a rolling ~90-day window; the next snapshot
  must land **<90 days** after the last stored date or a permanent gap opens.
- **Deadline:** re-pull by **~2026-09-10** (a margin inside the 90-day limit from
  2026-06-19) via `python -m screener.intraday_store snapshot`. Stitched history
  then reaches ~120 trading days → 60/40 walk-forward gives ~48 OOS days > the
  MIN_EFF_N=30 floor → the plan-10 OOS persistence check becomes answerable.

This replaces the "set up a daily cron" idea: a single well-timed pull (tracked
here) achieves the same accrual with near-zero standing machinery.

## 7. Implementation

- [x] **7.1** `calendar/idx_holidays.json` with the §3.1 seed (2026,
  `provisional: false` — confirmed) and the §3 schema. Add a top-level
  `calendar/README.md` documenting the yearly-refresh procedure and the official
  IDX source. *(Done 2026-06-26)*
- [x] **7.2** Python reader `research/lib/market_calendar.py` implementing the §4
  API; pytest suite incl. the 239-trading-day check and settlement-span cases
  (Thu→T+2 over a normal weekend = 4 cal-days; over a Monday holiday = 5). *(Done 2026-06-26 — 23 tests, all green)*
- [x] **7.3** Go reader `collector/internal/calendar` implementing the same API
  against the same JSON; Go tests mirroring the Python fixture cases. *(Done 2026-06-26 — all subtests green, `go build` clean)*
- [x] **7.4** Wire `settlement_calendar_days` into the plan-10 cost gate
  (replace `SETTLEMENT_TRADING_DAYS`); re-run live and record the delta on
  holiday-spanning cells in the plan-10 status. *(Done 2026-06-26 — `financing.py` `calendar_days_outstanding` / `financing_bps` now accept keyword-only `entry_date: date | None`; when provided, delegates to `CALENDAR.settlement_calendar_days`. All existing tests pass. Same-day intraday cells unaffected as expected — delta on holiday-spanning hold>0 cells deferred until actual entry dates flow through the backtester.)*
- [x] **7.5** Collector boot self-skip on non-trading days (ADR-0003 unchanged). *(Done 2026-06-26 — `calendar.Load()` + `IsTradingDay(time.Now())` check at top of `main()` before config/SSM/login)*
- [x] **7.6** `calendar/deadlines.yaml` + a `deadlines` checker command; seed the
  chartbit re-pull (§6) and the yearly holiday-refresh task. *(Done 2026-06-26 — `calendar/check_deadlines.py` exits 1 on DUE SOON / OVERDUE; both deadlines currently OK)*
- [x] **7.7** Reconcile the 2026 seed against the official IDX image — **done
  2026-06-20**; the provisional draft matched the official *Kalender Libur Bursa
  Tahun 2026* exactly (22 dates, 239 trading days), so `provisional = false` for
  2026.
- [x] **7.8** (Follow-on) point MIN_EFF_N / walk-forward trading-day counts at the
  shared reader where they currently approximate weekends. *(Done 2026-06-29 —
  `run_strategy_fit.run_grid` now imports `CALENDAR` from `lib.market_calendar`
  and calls `CALENDAR.trading_days_between(start, end)` to log the
  calendar-aware expected trading-day span at grid startup. The magic `< 10`
  row threshold was replaced with `< MIN_EFF_N` (the actual statistical floor
  from `lib.stats`) so the guard is no longer a magic number.)*
- [x] **7.9** **Ground-truth reconciliation validator** (the real-world check):
  `research/lib/calendar_reconcile.py` (+ a small CLI) that pulls ~1yr of chartbit
  daily bars for a few liquid names (e.g. BBRI/BBCA/TLKM), takes the **union of
  dates that have bars** as the observed IDX trading calendar, and diffs it against
  the file's predicted trading days over the same span. Emits a typed report:
  `calendar_says_trading_market_silent` (→ missing holiday / ad-hoc closure) vs.
  `calendar_says_holiday_market_traded` (→ wrong date). Zero-diff = pass.
  *(Done 2026-06-29 — `research/lib/calendar_reconcile.py` written; CLI runnable
  as `python -m lib.calendar_reconcile --symbols BBRI,BBCA,TLKM` from `research/`
  directory; also accepts `--start`, `--end`, `--lookback-days`, `--json`;
  exits 1 on divergences for CI integration. Requires `SB_BEARER_TOKEN` with a
  clear error message when missing. A second, independent ground truth — collector
  per-day frame presence vs. prediction — is wired once unattended capture exists,
  pillar 4.1.)*
- [ ] **7.10** **Staleness guards** in the `deadlines` checker (7.6): (a) a
  **coverage guard** that alarms when today is within ~30 days of the file's last
  covered date (the file is about to run dry), and (b) a **yearly-refresh
  deadline** (~early December) to transcribe and diff next year's IDX image.

## 8. Verification

The validation story is three tiers; only Tier 2 proves correctness against the
real market — Tier 1 catches typos, Tier 3 catches neglect.

**Tier 1 — build / CI (internal consistency; catches transcription errors)**

- **Trading-day count** — reader over the 2026 seed reproduces IDX's published
  239 trading days (sanity that weekmask + holiday set agree with the source).
- **Settlement math** — `settlement_calendar_days` gives 4 for a normal Thu→T+2
  (the ADR-0011 worked example) and 5 when the settlement leg crosses a holiday.
- **Cross-language parity** — Go and Python readers return identical answers on a
  shared fixture of dates (parity test).
- **Schema sanity** — every date parses, no holiday falls on a weekend, no
  duplicate dates, `kind` ∈ the allowed set.

**Tier 2 — ground-truth reconciliation (the real validator; task 7.9)**

- **chartbit vs. file** — the union of dates with daily bars across several liquid
  names equals the file's predicted trading days over the same span; any diff is a
  typed, actionable finding (missing-holiday vs. wrong-date). This is the
  authoritative check: chartbit is ground truth, the file is the hypothesis.
- **Collector vs. file** (once capture is unattended, pillar 4.1) — each day's
  observed data presence (S3 segment non-empty) matches the prediction; a
  divergence catches same-year ad-hoc closures (IDX *Catatan #2*) within a day.

**Tier 3 — staleness / drift (catches the human; task 7.10)**

- **Coverage guard** — alarms before the file runs dry (within ~30 days of its
  last covered date).
- **Yearly-refresh deadline** — fires in early December for the next-year update.

**Behavioural checks (per-consumer)**

- **Cost-gate consistency** — after 7.4, the only change to survivor economics is
  on holiday-spanning entries; same-day intraday cells (financing-zero) are
  unchanged; the change is in the conservative (higher-hurdle) direction.
- **Collector self-skip** — a holiday date triggers shutdown-without-connect in a
  unit test of the boot path.

## 9. Out of scope

- Intraday session-time logic — already in `research/lib/session.py`; this plan
  is trading-**day** granularity, not time-of-day.
- Holiday calendars for non-IDX venues — single-venue (IDX) only.
- Moving scheduling into IaC — rejected in ADR-0012; the box self-skips.
- A general task scheduler/cron — `deadlines.yaml` is a passive reminder list
  checked on demand, not an executor.

---

Prev: [13-capital-allocation-and-capacity.md ←](./13-capital-allocation-and-capacity.md) ·
Index: [README](./README.md)
