# Foreign-Selling Rebound: Pre-Registration

> **Status:** 🔒 Frozen 2026-10-17 (§9) · pre-registration · **Owner:** @lewis · **Created:** 2026-10-17
> **Related:** [21-index-rebalance-event-study.md](./21-index-rebalance-event-study.md) (template; its H2 failed because the edge leaned on 2020) · [20-bnbr-forward-test-preregistration.md](./20-bnbr-forward-test-preregistration.md) (D1 entry timing) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) · [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md)

> **In plain words.** When foreign funds dump a large Indonesian stock in one
> day, the price may fall further than the news justifies, and local buyers may
> pull it back up within a few days. If that happens reliably, we buy the
> morning after a heavy foreign-selling day and sell a few days later. This plan
> fixes the exact rules **before** we download the data, so the result cannot be
> bent to fit. Nothing here places an order.

> **Why now.** MSCI froze Indonesian additions in Feb-2026 and on 2026-10-15
> proposed downgrading Indonesia to Standalone (plan 21 §0). Foreign selling is
> heavy and will come in waves until the decision (by 2027-06-30). We can only
> buy, so a strategy that rides market direction loses in a bear market. A
> rebound after forced selling is one of the few setups that can work in this
> market.

> **The lesson from plan 21.** Deletion rebounds looked strong on 2014–2021
> and then lost money on 2022–2025, because the early result leaned on the 2020
> crash recovery. This plan therefore makes "still works without 2020" a
> **blocking** gate (G9), not a footnote.

---

## 1. Hypothesis (frozen at §9 sign-off)

**H.** Unusually heavy net foreign selling in a liquid stock on day *t* is
followed by a price rebound over the next 1–5 sessions, relative to the same
stock's normal return, large enough to beat the round-trip cost (~100–138 bps).

Two signal definitions are tested. Both use only data available after the
close of day *t*.

| id | name | trigger on day *t* | what we buy |
|---|---|---|---|
| S1 | single-name capitulation | the stock's `F_t` is at or below its own 5th percentile over the previous 250 sessions **and** its close-to-close return on *t* is ≤ −2% | that stock |
| S2 | market-wide foreign dump | the universe's summed net foreign value on *t* is at or below its own 5th percentile over the previous 250 sessions | the 5 universe names with the lowest `F_t` that day (ties: larger value traded first) |

`F_t` = net foreign value on *t* ÷ median daily value traded over *t−20 … t−1*.
Using a ratio makes BBCA and a smaller LQ45 name comparable. A stock needs
≥ 120 sessions of prior foreign data before it can trigger.

**Control (reported, not gated).** C1: the mirror image, heavy net foreign
*buying* (95th percentile). If C1 shows the same "rebound", the signal is just
picking volatile days, not foreign selling.

## 2. Data

### 2.1 Foreign flow (verified 2026-10-17)

* Endpoint: `GET exodus.stockbit.com/order-trade/foreign-domestic/historical?symbols=X&market_type=MARKET_TYPE_REGULER&from=YYYY-MM-DD&to=YYYY-MM-DD`
  (`sb-protobuf`: `financial/order_trade/public/foreign_domestic/v1`, `GetHistorical`).
* Returns `data.historical_net[]`, one row per session: `net_foreign`,
  `foreign_buy`, `foreign_sell`, `net_lot`, `net_frequency`, `average_price`,
  `percentage_foreign_value`, plus `historical_price[]` (OHLC).
* **Depth (BMRI probe):** full rows from 2017-01-03 to today; 2013 and 2015
  return 0 rows; 2016 untested. **Ranges longer than ~1 year are refused**, so
  the fetcher asks for one calendar year per call.
* Market type: **regular board only**. Negotiated-market block trades are
  excluded on purpose; they are crosses, not pressure on the order book.

### 2.2 Universe (fixed before any download)

A name is in the universe on day *t* if it is among the **top 40 by median
value traded over the previous 60 sessions**, computed point-in-time from
`research/data/daily` and re-ranked at each month-end. The download list is the
union of all names that were ever in the top 40 at any month-end from 2016-12
to 2026-09.

* **Survivorship:** `research/data/daily` holds 243 symbols, mostly
  survivors. Large caps rarely disappear, so the bias is small, but Phase 1
  reports every top-40-era name that has no local bars and fetches it.
* IHSG comes from `research/data/daily/IHSG.parquet` (G6).

### 2.3 Data checks (Phase 1, before any return is computed)

1. `net_foreign = foreign_buy − foreign_sell` on every row (tolerance Rp 1M).
2. Every foreign-flow date is an IDX session in `research/data/daily`; report gaps.
3. `foreign_buy + foreign_sell` ≤ 2 × chartbit value traded on the same day
   (regular board can't exceed the total); report violations.
4. The endpoint's `historical_price` close equals the chartbit close on ≥ 99%
   of rows. This confirms the dates line up.
5. A coverage table: rows per symbol per year, and each symbol's first date.

If more than 5% of universe name-days are missing, the affected years are
`data_blocked` and are reported, never silently dropped.

## 3. Entry, exit and costs

* **Signal** after the close of *t* (foreign data is published after the close).
* **Entry: `open_{t+1}`** (`ENTRY_NEXT_OPEN`). Plan 20's D1 finding is why:
  any entry at `close_t` uses information that arrives too late.
  `close_{t+1}` is reported as a sensitivity.
* **Exit:** `close_{t+k}` with hold **k ∈ {1, 3, 5}** sessions. Own-cash
  sleeve, so no financing charge (ADR-0015); 1, 3 and 5 cover the plausible
  rebound window without inviting a search.
* **No overlap within a name:** a new trigger while a position is open is
  skipped (`sequential=True`).
* **Locked entries:** if `open_{t+1}` is at the upper auto-rejection limit,
  the trade is skipped (`skip_locked=True`). Lower-limit opens are tradable
  (we are buying) and are kept.
* **Costs:** the existing harness, `strategy_fit.families_idx_structural.build_event_trades`:
  one tick + 40 bps fee/tax + point-in-time sqrt impact (`impact_coeffs.json`).
  Reported at 1 lot and at the harness Rp 3M notional.

## 4. Test rules (frozen at §9 sign-off)

### 4.1 Cells (G7 Holm denominator: 6)

| signal | holds | cells |
|---|---|---|
| S1, S2 | k ∈ {1, 3, 5} | 6 |

C1 and the `close_{t+1}` entry are reported only and are not cells.

### 4.2 Windows

* **Development:** signals on 2017-01-01 → 2022-12-31 (the first 250
  sessions of each name are warm-up only).
* **Holdout:** 2023-01-01 → the last full session before the Phase 3 run,
  **run once**. It includes the 2025–2026 foreign outflow, which is the regime
  we want to trade.

### 4.3 Gates (all blocking unless marked)

| gate | rule |
|---|---|
| G1 | live-feasible entry `open_{t+1}` (§3) |
| G2 | mean edge over the always-long null (same name, same calendar window), **t ≥ 2, clustered by signal date**. S2 fires 5 names on one day, and S1 triggers bunch on market-wide sell days, so effective N = distinct signal dates |
| G3 | edge − cost > 0 under the current coeffs, the 2026-10-14 candidate coeffs, and 1 lot |
| G4 | mean net > 0 after dropping the 5 best signal dates |
| G5 | holdout: edge > 0 and G3 on the holdout alone. Nothing is tuned between the windows |
| G6 | net − IHSG over the same window > 0 |
| G7 | Holm across the 6 cells |
| **G9** | **2020 excluded:** with every signal dated 2020-02-01 → 2020-12-31 removed, the development result still has edge > 0, net > 0 and net − IHSG > 0. This is the plan 21 lesson |
| G8 | regime split, *reported, not blocking*: IHSG above vs. below its 200-session average on *t* |

**Minimum sample per cell:** ≥ 100 trades and ≥ 30 distinct signal dates in
development; ≥ 30 trades and ≥ 12 dates in holdout. Below that the cell is
`insufficient_n`, not a fail. Rough expectation: S1 at the 5th percentile with
a −2% filter gives about 3–6 triggers per name per year × ~40 names, so
several hundred development trades; S2 gives about 12 days a year × 5 names.

## 5. Decision rule

* **A cell passes G1–G7 and G9 in development, then G5 in holdout:** write
  plan 23, a forward-test pre-registration modelled on plan 20: paper first,
  then 1-lot live orders with a rupiah worst case stated before the first order
  (frugal live-test rule).
* **Development passes but holdout fails:** close the family and record the
  numbers here. Do not re-test with new thresholds.
* **Only G9 fails** (the edge is 2020): close; same reading as plan 21 §10.2.
* **C1 matches the passing cell** (heavy buying "rebounds" too): not a
  candidate. The signal is picking volatility, not foreign flow.
* **Nothing passes in development:** close; the holdout stays unspent.

## 6. Phases

| phase | work | output | effort |
|---|---|---|---|
| 0 | Probe 2016 depth; confirm the call rate the endpoint tolerates (start at 1 request/second) | §2.1 updated | ¼ day |
| 1 | `research/foreign_flow/` fetcher (one call per symbol-year, resumable, token from the environment and never logged, tested with a fake opener) → `research/data/foreign_flow/{SYMBOL}.parquet`. ~60 names × 10 years ≈ 600 calls; the owner runs it in their own terminal. Then the §2.3 checks | coverage table | 1 day |
| 2 | §9 sign-off. Build `families_foreign_flow.py` (signal masks only; costs, null and gates reused unchanged) and run development | `docs/research/<date>-foreign-selling-dev.md` | 1 day |
| 3 | Holdout run, once, behind an authorization file as in plan 21 | the same report, extended | ½ day |

## 7. Risks and known biases

* **The rebound may not be capturable.** Foreign selling can continue for weeks
  (an MSCI exit is a months-long programme). Then heavy selling predicts more
  selling, and H fails honestly. That is a useful result.
* **Same-day timing.** Part of the rebound may land at `open_{t+1}`, before we
  can buy. The `close_{t+1}` sensitivity and the open-gap leg show this.
* **Intraday foreign data is not tested here.** The `/summary` endpoint has a
  mid-day flag, so a live version could act before the close. No history of
  intraday flow exists, so this plan does not claim it.
* **One-off regime.** If the edge exists only in 2025–2026, the holdout will
  show it, but development will not; under §5 that is a fail. Recorded on
  purpose: a regime-only edge cannot be told apart from luck with this sample.
* **Endpoint definition changes.** If Stockbit changed how "foreign" is
  classified (for example custody-based vs. broker-based) during 2017–2026,
  the series has a break. §2.3 check 3 and the coverage table are the first
  warning.

## 8. Open questions

1. ~~Universe size?~~ Answered 2026-10-17: top 40 by value traded.
2. ~~S1 price filter at −2%?~~ Answered 2026-10-17: keep.
3. ~~Add a k = 10 hold?~~ Answered 2026-10-17: no.

## 9. Sign-off

Frozen when the owner fills this in. Gates and cells are not edited after this
line. Any later change is a new dated row with the reason.

| date | change | by |
|---|---|---|
| 2026-10-17 | Freeze §1–§5 as written. §8 answered with the proposed defaults: universe = top 40 by value traded, S1 keeps the −2% price filter, no k = 10 hold. Signed before any foreign-flow history beyond the BMRI depth probe was downloaded; no returns computed. | @lewis (owner; instructed in session, entered by Claude) |
| 2026-10-17 | **Data rule, added after the §10.1 checks and before any return was computed.** (1) A name-day that is missing, or that fails check 3 (buy + sell > 2 × chartbit value), is *no data*: it cannot trigger, it is left out of the name's trailing-250 percentile history, and it is never treated as zero flow. (2) S2 is evaluated only on sessions where ≥ 35 of the 40 universe members have valid foreign data; the market sum and its trailing-250 percentile use only those sessions. Nothing else changes. | @lewis (owner; instructed in session, entered by Claude) |

## 10. Results log (append-only)

### 10.1 Phase 1 data checks (2026-10-17)

Downloaded by the owner with `make foreign-fetch` (154 universe names, one call
per symbol-year, 2016–2026), checked with `make foreign-checks`
(`research/data/foreign_flow/checks.md`). No returns were computed.

* **Depth:** history reaches **2016-01-04** for names listed then (better than
  the 2017 probe suggested), so the 250-session warm-up fits before 2017.
* **Check 1** (net = buy − sell): 0 violations.
* **Check 4** (endpoint close = chartbit close): 100% match, so the dates line up.
* **Check 2:** one market-wide gap, **2017-08-30** (all 40 members missing).
  Otherwise 299 of 2,358 sessions miss 1–9 members. Missing universe name-days
  are 0.0–1.5% per year, so **no year is `data_blocked`**.
* **Check 3** (buy + sell > 2 × chartbit value): 263 name-days (0.08% of rows),
  mostly 2017–2018 (239), median ratio 2.7×. Either chartbit's value or the
  foreign figure is wrong on those days; the check cannot tell which.
* **Survivorship:** the universe is built from local bars, so a top-40 name that
  was delisted and is missing locally cannot be seen (§2.2). Not measurable here.
* A stray `A.parquet` (1 row, a Saturday) is not a universe name and is ignored.
