# BNBR `daily_momentum` h=2 — Forward-Test Pre-Registration

> **Status:** 📝 Draft · pre-registration, **not frozen** · ⛔ blocked on precondition P1 (§2), which **currently fails** · **Owner:** @lewis · **Updated:** 2026-10-14
> **Related:** [18-alpha-and-infrastructure-split.md](./18-alpha-and-infrastructure-split.md) (§3.1 seam, §4.4a caveats, §6 Track A charter, §5 B6) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [00-roadmap-research-to-live.md](./00-roadmap-research-to-live.md) (defect D1) · [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md) · [ADR-0017](../adr/0017-sleeve-is-a-funded-sub-account.md) · [first-live-order runbook](../runbooks/first-live-order.md)

> **What this is.** The rules of one live test, written before any order. Plan 18
> §6 says a candidate counts only on a *pre-registered* pass on the measured
> hurdle **and** the always-long null gate. This is that registration for plan-10
> cell **BNBR · `daily_momentum` · hold 2 · `single_name_time_series`**. It also
> defines the spec the daily target emitter must match (§3.1 seam). It places no
> orders and changes no code.
>
> **What it is not.** Evidence of an edge. C4 revived BNBR by correcting a cost
> assumption; it did not produce a strategy (plan 18 §4.4a). At 1 lot this test
> is mostly a *cost and execution measurement* with a tripwire for a bad edge
> (§5). Read the power statement before reading the thresholds.

---

## 1. The finding that gates everything — D1 is still live

**Backtest entry convention.** `research/strategy_fit/families_single_name.py:97`
sets `signal = close_t / close_{t-1} − 1` and `:98` sets
`forward_return = close_{t+hold} / close_t − 1`. The signal is computed from
`close_t` and the trade is *entered at that same `close_t`*, a price observable
only after the session ends. `run_single_name_family` (`:349`, `:379`) passes
that column straight through; nothing shifts entry. Roadmap defect **D1**
(`00-roadmap…md` §"Three harness defects") is therefore **not fixed** for this
path; no later commit touches it (`git log` on the file ends at `0cb9a3c`).

**What live can do.** The signal exists after day `s`'s close. The earliest
order is day `s+1`, placed by `strategy-run --strategy rebalance` at
`--rebalance-at`, priced at the last observed `liveprice` snapped to the tick
(`strategy/strategies/rebalance/rebalance.go:361`), once per (window, symbol)
(`:378`). So live entry is `close_{s+1}`-or-earlier, never `close_s`.

**Re-score, read-only, on local daily bars** (`research/data/daily/BNBR.parquet`,
636 rows 2024-01-02 → 2026-09-11, long-only `signal > 0`, hold 2; script was a
scratch file, nothing written). Cost hurdle at the latest close (95 IDR) is
`1e4/95 ≈ 105 bps` one-tick spread + 40 bps fees ≈ **145 bps**; the harness's
183.5 bps also carries a 20 bps impact charge priced at a 3M-IDR position, which
does not apply to 1 lot.

| Convention | n | gross bps | always-long null | edge over null | sd/trade |
|---|---:|---:|---:|---:|---:|
| Backtest as scored (enter `close_s`, stride-2 de-overlap) | 97 | 329.7 | 60.4 | **269.3** | 1,068 |
| **A** — enter `close_{s+1}`, exit `close_{s+3}`, stride-2 | 97 | 254.4 | 63.9 | 190.5 | 1,111 |
| **B** — enter `open_{s+1}`, exit `open_{s+3}`, stride-2 | 97 | 237.0 | 67.9 | 169.1 | 1,123 |
| D1 note's proxy — `open_{s+1}` → `close_{s+2}` | 97 | 79.1 | −4.6 | 83.8 | 860 |
| **Live rule** (§3), enter `close_{s+1}`, sequential, blocked while held | 100 | **70.7** | 62.1 | **8.6** | 947 |
| Same sequential rule, backtest entry `close_s` | 129 | 234.5 | — | — | 1,029 |

The 269.3 reproduces the C4 figure on local data. Three readings:

1. Lagging entry one day removes 75–95 bps of gross (stride-matched); the stride
   figures survive A/B only by ~45 bps / ~25 bps over the 145 bps hurdle.
2. The **sequential live rule** collapses to 70.7 bps gross, below the hurdle
   and 8.6 bps over the null. Standard error is ~95 bps, so 70.7 vs 254 is about
   two sigma: the backtest's point estimate is fragile, not necessarily wrong.
3. The harness charged a constant hurdle at today's 95 IDR. BNBR traded 32–222
   IDR in the window; at 32 IDR one tick is 312 bps. Mean tick+fee over the
   sequential entries is **272 bps**, not 183.5. Cost is price-dependent; §3 caps
   entry price for that reason.

---

## 2. Blocking preconditions (all required before the first *paper* day)

| # | Precondition | Status 2026-10-14 |
|---|---|---|
| **P1** | **Re-score under the live entry convention before any live order.** Run on refreshed bars (through the latest session) with the §3 rule: convention A, sequential, per-entry price-matched hurdle `1e4/P_entry + 40`. Must satisfy **both** (i) stride-matched A and (ii) sequential A: mean gross − always-long null > mean hurdle, and mean gross > mean hurdle. No re-tuning of lookback, hold or threshold. | ❌ **Fails** on (ii): 8.6 vs ~145–272 bps. (i) passes by ~45 bps at the latest-price hurdle only. Needs the user's decision (§10 Q1). |
| P2 | Daily bars refreshed. Local BNBR bars end 2026-09-11, ~1 month stale. | ❌ open |
| P3 | Emitter built to §3 and §4.2, deterministic from (bars, holiday file, its own state file). | ❌ open (separate agent) |
| P4 | Paper phase passed (§7). | ❌ open |
| P5 | B1 real-fill gates complete, including the GOTO sell-to-zero (runbook §15, 2026-10-15). | ❌ open |
| P6 | Fee schedule and session end confirmed against one live order preview / `/market-time/session` (§10 Q3, Q4). | ❌ open |

P1's thresholds are fixed here so the result cannot be argued after the fact. A
P1 fail followed by a decision to proceed anyway must be recorded in §12 with
the numbers; the test then runs as a cost-measurement exercise and its
efficacy verdict is reported as such.

---

## 3. Signal, entry, exit (frozen at registration)

**3.1 Signal.** `signal_s = close_s / close_{s-1} − 1`; **long when `signal_s > 0`
strictly** (`families_single_name.py:97`, `long_only.py:63-71`). Lookback 1
(`DEFAULT_LOOKBACK_MOMENTUM`, `:63`). No other filter, no threshold. Hold 2
trading days (`SINGLE_NAME_HOLDS`, `run_strategy_fit.py:156`).

**3.2 Data.** Chartbit daily OHLCV for BNBR through the repo's data layer
(`ds.daily_history`, `run_strategy_fit.py:804`), the source the C4 numbers used.
Observation time: the emitter runs **08:30 WIB on entry day `e`** and reads day
`s`'s close. If the `s` bar is absent or its `date` ≠ `s`, no entry on `e` and
no catch-up (§8, invalidation list). Data anomaly guard: `|signal_s| > 0.35`
(above the ARA limit, so a corporate-action artifact) → no entry.

**3.3 Calendar.** Trading days from `calendar/idx_holidays.json` (weekmask
`1111100`, holidays listed). `s` is a trading day; `e` = next trading day after
`s`; exit day `x` = second trading day after `e` (`x = e + 2` trading days).
Same calendar file as `collector/calendar`; the emitter must not roll its own.

**3.4 Entry.** On day `e`, target = 1 lot (§4). `strategy-run --strategy
rebalance --rebalance-at <T>`; proposed `T = 15:30` WIB, the nearest in-session
time to the close-to-close convention A (a GOTO order filled at 15:38 on
2026-10-14, runbook §15), **to be confirmed against the session endpoint (Q4)**.
Limit price = last observed price, snapped down to tick
(`rebalance.go:361`); for BNBR (< 200 IDR, tick 1) that is the last trade price
itself. One attempt, no reprice, no cancel/replace, no market order. Unfilled at
the day's end = **missed entry**: counted, not retried, not a round trip.

**3.5 Exit.** On day `x` the emitter writes weight 0; the strategy sells to zero
(`ab21439`) at the same `T` and the same pricing rule. An unfilled exit is
retried each following trading day (new window, new order id) until flat;
`x_realized` is the actual fill date and the return is measured to the actual
fill, never to the nominal day.

**3.6 Overlap and re-entry.** One position at a time. A signal on day `s` is
acted on only if the position is **confirmed flat at the close of `s`**. Signals
that arrive while held, or while an exit is outstanding, are logged
`ignored_held` and dropped. No extension, no pyramiding, no averaging. Earliest
re-entry: `x_realized`'s next trading day.

**3.7 Entry price cap.** No entry if `close_s ≥ 200` IDR (tick regime changes and
the budget in §6 assumes it) — logged `ignored_price_cap`.

---

## 4. Sizing and the seam

**4.1** 1 lot = 100 shares, owned cash, `capital_model: "owned_cash"`, account
`008488L`, financing 0 (ADR-0015, ADR-0017). BNBR only; HRTA deferred. No
calibration-only trades. The 5–20M impact phase is deferred until a strategy
earns it.

**4.2 Weight → lots.** `rebalance.go:375` sizes `floor(weight × base_notional /
(p × 100))`, `p` the snapped price on day `e`. To get exactly one lot robustly:

```json
{
  "schema_version": 1,
  "as_of": "<e>T08:30:00+07:00",
  "strategy_id": "bnbr_momo_h2",
  "capital_model": "owned_cash",
  "base_notional_idr": <round(150 × close_s)>,
  "targets": [{"symbol": "BNBR", "weight": 1.0}],
  "constraints": {"max_participation_rate": 0.05,
                  "complete_by": "<e>T<T+15min>:00+07:00", "board": "RG"}
}
```

`base_notional_idr = 1.5 × 100 × close_s` yields 1 lot for any `p` in
(0.75, 1.5] × `close_s`; ARA/ARB keep day-`e` price inside that. The emitter
asserts the expected lot count is exactly 1 and writes weight 0 with reason
`sizing_breach` otherwise. **Flat and exit days use `weight: 0.0`**, never an
empty `targets` list (`target.go:270` rejects it). `max_participation_rate` is
read by nothing in the rebalance path (`rebalance.go` package doc) and is set
only to satisfy validation.

---

## 5. Sample size, duration, power

- **Signal frequency** (local bars, 2024-01 → 2026-09, ~2.7 years): `P(signal>0)
  = 0.29`. The sequential live rule produced **100 entries ≈ 3.1 per month**.
- **Planned n = 30 completed round trips** (both legs filled). At 3.1 entries per
  month and an assumed 80% entry-fill rate that is ~12 months of calendar time
  (~10 at 100% fill). Interim look at n = 15 is **operational only** (§8); no
  efficacy peek, so no alpha is spent.
- **Per-trade sd ≈ 950–1,100 bps** (§1 table; median trade is flat in 29% of
  entries). Take 1,000.
- **Power, one-sided α = 0.05, t-test approximation:** SE at n = 30 is 183 bps.
  The minimum effect detectable at 80% power is **≈ 466 bps** net per round
  trip. At n = 60 (~24 months) it is ≈ 326; at n = 100, ≈ 249.
- **What that means.** The live-convention point estimates are +0 to +110 bps
  net. At n = 30 the chance of a PASS if the true net is +110 is **~14%**, if
  +250 about 37%. **A PASS requires an effect far above anything the backtest
  shows, so it is more likely luck or regime than edge, and the most probable
  outcome is INCONCLUSIVE.** The test can falsify (a gross edge that is zero or
  negative shows up) and it measures cost; it cannot confirm. n is not
  extended to chase significance (§9).

---

## 6. Hypotheses, metrics, thresholds (fixed now)

Realized net per round trip, from fills:

`net_i = (sell_fill × (1 − 0.0025) − buy_fill × (1 + 0.0015)) / (buy_fill × (1 + 0.0015)) × 1e4` bps.

Fees use the schedule (`engine.ScheduleFeeIDR`, `strategy/engine/sink.go:13`,
`backtest/backtest.py:41`); the broker's own fee field is unconfirmed, so the
schedule is reconciled against the account cash delta on the first round trip (Q3).
Spread, slippage and queue cost are inside the fill prices, so **`net_i` already
contains the whole hurdle**; "net vs hurdle" is `net_i > 0`.

**Always-long null.** After the window ends, from the same bars:
`null = mean over every trading day d in the test window of close_{d+3}/close_{d+1} − 1`
(convention A, frictionless). It is a single scalar, computed once.

| | Test | PASS threshold |
|---|---|---|
| **M1** realized net | H0: mean(`net_i`) ≤ 0 | one-sided bootstrap p < **0.05** |
| **M2** edge over null | H0: mean(`net_i`) ≤ `null` | one-sided bootstrap p < **0.05** |

- Bootstrap = percentile bootstrap of the mean over round trips, 10,000
  resamples, seed `20261014`, as `backtest.backtest.bootstrap_pvalue` is used in
  the harness. A Student-t p is also reported but is **not** decision-bearing
  (zero-inflated, heavy tails).
- **Both M1 and M2 must pass** (intersection-union; overall α ≤ 0.05, no
  multiplicity correction). One test of record at n = 30; no interim efficacy
  test.
- Verdicts at n = 30:
  - **PASS**: M1 and M2 both pass. It earns a *decision* on the 5–20M impact
    phase; it does not deploy anything.
  - **FAIL**: mean(`net_i`) ≤ 0 or mean(`net_i`) ≤ `null` (point estimate, either).
  - **INCONCLUSIVE**: otherwise. Treated as not-passed for promotion (plan 18 §6).
- Secondary, reported, not decision-bearing: hit rate, median, per-year split,
  realized vs modelled cost per trip (`1e4/P_entry + 40`), entry/exit fill
  rates, an intent-to-trade estimate that prices missed entries from bars.
- Adverse selection caveat: a passive limit at last price fills preferentially
  when the price trades *down* through it. Missed entries are therefore
  informative; they are counted and the ITT estimate is reported next to the
  filled-only one. If the two disagree in sign, the verdict is stated as
  fill-conditional.

---

## 7. Paper phase (before any live order)

`--sink paper --source live`, same emitter, same `--rebalance-at`, account
guard on. **Pass = 10 consecutive trading days** in which (a) the emitter's
`target_portfolio.json` matches an independent recomputation of §3 from the same
bars *exactly* (symbol, weight, base notional, lot count), (b) all required
files are present before 08:30, and (c) the window contains **at least one
complete entry → exit cycle** through the paper sink. Any mismatch, missing
file or manual edit **resets the count to 0**. Live starts only after P1–P6 and
a user go-ahead on that day's date.

---

## 8. Stop rules and invalidation

**Budget (rupiah).** At 95 IDR one lot is **9,500 IDR**. Entry is capped at
< 200 IDR, so exposure never exceeds **19,900 IDR** + 30 IDR buy fee (one lot,
one position, no financing).

| Rule | Trigger | Action |
|---|---|---|
| **Cumulative loss halt** | realized net P&L ≤ **−10,000 IDR** | stop; sell any open lot; verdict FAIL-by-halt, reported with n so far |
| Expected spend | ~138 IDR/trip at 95 IDR (38 fee + 100 tick), ~4,100 IDR over 30 trips; P&L noise sd ≈ 950 IDR/trip → ~5,200 IDR over 30 | context only |
| **Hard ceiling** | −10,000 realized, plus one position that cannot be exited (suspension/UMA) at ≤ 19,900 | **≈ 30,000 IDR total worst case** (~USD 2) |
| Consecutive losses | **12** consecutive net-negative trips (hit rate ≈ 33–40%, so 6–8 happens by chance) | pause and review execution for a bug; **not** a FAIL by itself |
| Kill switch | file `runs/KILL_bnbr_momo_h2` present, or operator `Ctrl-C` | emitter writes weight 0 and refuses entries; sell-to-zero; verify `008488L` reconciles MATCH |
| Entry fill rate | < 70% over the first 10 attempts | pause; the limit rule may not change inside this registration (§9) |

**Invalidates the test (stop, record, do not count the trips affected):**

- Any order or fill in account `0001958`, or the token `data.acn` guard failing
  (ADR-0017). Halt immediately.
- Missing/late bar for `s` on more than 2 entry days (data outage); single
  outages are `no_entry` days.
- Daily pre-trade `008488L` reconcile not MATCH.
- Entry fill rate under 50% across ≥ 15 attempts: the test then measures the
  limit rule, not the signal. Reported as INVALID for efficacy, still valid as
  fill-rate data.
- Any manual order in BNBR from the sleeve, or any edit to a frozen section after
  the first paper day.

---

## 9. What FAIL / INCONCLUSIVE means, and what happens next

- **FAIL or loss-halt:** record the finding as a dated addendum to plan 18
  §4.4a (n, `net_i` mean and CI, null, realized cost vs modelled). **BNBR
  `daily_momentum` h2 is closed under A-1** and is not re-tuned, re-lagged,
  re-held or re-priced (§6 A-1). The realized-cost data still feeds B6 even on a
  FAIL, booked under `bnbr_momo_h2` and kept out of the null-portfolio cost
  books.
- **INCONCLUSIVE:** same promotion outcome as FAIL. Extending n requires a **new**
  pre-registration document with a stated budget; n = 30 is not topped up
  mid-test.
- **PASS:** user decision on a capped 5–20M IDR phase; its own pre-registration.
- A change to any §3–§8 rule after the first paper day voids this registration and
  restarts the count under a new document.

---

## 10. Open questions for the user

1. **P1 currently fails** on the live sequential rule (gross 70.7 bps vs ~145 bps
   hurdle). Stop here, refresh data and re-run first, or proceed knowing it is a
   cost-measurement test only?
2. Is 12 months for n = 30 acceptable, given ≈ 14% power at the backtest's
   live-convention effect size? Alternative: accept the test as a futility/cost
   instrument and read only the FAIL side.
3. Real fee: reconcile the schedule (0.15% / 0.25%) with the first buy's cash
   delta? Runbook §15 saw a 4 IDR fee on 2,800 IDR, no minimum observed.
4. `T`: confirm IDX continuous-trading end from `/market-time/session` before
   fixing 15:30 (code comment says continuous trading ends before 15:00, but a
   fill happened at 15:38).
5. Are strategy-run orders day orders (expire at close)? The exit-retry rule in
   §3.5 assumes so.

---

## 11. Recording (per fill, `strategy_id = bnbr_momo_h2`)

Post-trade store (`strategy/engine/store.go`: `decision`, `order_intent`,
`fill`, `attribution`, `order_reject`, `run`) plus the emitter's sidecar run log
for fields the store lacks. Per round trip: `signal_s`, `close_s`,
`close_{s-1}`, signal date `s`, entry day `e`, nominal and realized exit day;
**decision price** = `close_s`; **arrival mid** = book mid at submit (captured
feed; null if unavailable, never substituted); limit price; fill price and lots;
fee (schedule and, once known, broker) ; slippage bps vs limit (`SlippageBps`)
and vs arrival mid; **queue wait** = submit → first fill; reject reasons (the
`order_reject` table); ignored-signal reasons (§3.6, §3.7); missed entries;
reconcile result and account number per day; `net_i`; per-trip modelled cost.
Null bars are saved alongside the verdict for reproducibility.

---

## 12. Changelog

- 2026-10-14 — drafted. P1 evaluated on local bars (through 2026-09-11): fails.
