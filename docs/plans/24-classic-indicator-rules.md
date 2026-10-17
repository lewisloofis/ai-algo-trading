# Classic Indicator Rules: Pre-Registration

> **Status:** 🔒 Frozen 2026-10-17 (§9), development run next · **Owner:** @lewis · **Created:** 2026-10-17
> **Related:** [22-foreign-selling-rebound.md](./22-foreign-selling-rebound.md) (template, gates) · [10-strategy-fit-and-portfolio.md](./10-strategy-fit-and-portfolio.md) (momentum / mean-reversion / breakout families, all fixed-hold, no edge) · [../research/alpha-search-2026-10.md](../research/alpha-search-2026-10.md) (12-1 momentum weak lead, fails in bear) · [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md)

> **In plain words.** Retail traders use moving averages, RSI, MACD and
> Bollinger bands. Our earlier tests covered the same ideas (momentum, mean
> reversion) but always sold after a fixed number of days. Textbook indicator
> systems sell when the indicator says so. This plan tests five well-known
> rules with their **standard textbook settings**, chosen before any result is
> seen, so the test cannot be tuned until something looks good. Nothing here
> places an order.

---

## 1. Hypothesis (frozen at §9 sign-off)

**H.** At least one classic indicator rule, with its textbook settings and its
own signal-based exit, earns more per trade than simply holding the same
stock over the same number of days, by more than the round-trip cost.

All indicators use the split-adjusted daily close (`research/data/daily`),
computed causally (row *t* uses bars ≤ *t* only). Entry and exit signals are
read at the close of day *t*; the trade happens at `open_{t+1}`.

| id | rule | settings | entry signal on *t* | exit signal on *t* | time stop |
|---|---|---|---|---|---|
| R1 | Golden cross | SMA 50 / SMA 200 | SMA50 crosses above SMA200 (≤ on *t*−1, > on *t*) | SMA50 crosses below SMA200 | none |
| R2 | MACD crossover | EMA 12 / 26, signal EMA 9 | MACD line crosses above the signal line | MACD line crosses below the signal line | none |
| R3 | RSI oversold | RSI 14 (Wilder smoothing) | RSI crosses below 30 | RSI ≥ 50 | 20 sessions |
| R4 | Bollinger lower band | SMA 20 ± 2 population std | close < lower band | close ≥ middle band (SMA 20) | 20 sessions |
| R5 | Pullback in uptrend (Connors RSI-2) | RSI 2 (Wilder), SMA 200, SMA 5 | RSI2 < 10 **and** close > SMA200 | close > SMA5 | 20 sessions |

* R1 and R2 are trend-following, R3 and R4 are mean reversion, R5 is the
  combined "buy a dip only inside an uptrend" rule.
* A time stop counts sessions held; when it is reached the position exits at
  the next open like any other exit.
* Indicators need warm-up: no signal until the indicator has its full window
  (200 sessions for SMA200, 26 + 9 for MACD, and so on).

**Control (reported, not gated).** C-rand: for each rule, the same number of
trades with the same hold lengths, placed on random eligible entry days in the
same name and year (seed fixed, 200 draws). If a rule is not above the bulk of
its random twins, its timing adds nothing.

## 2. Data and universe

* Daily OHLCV, value and dividends from `research/data/daily/*.parquet`
  (local, no API). Prices split-adjusted; dividends added back net of 10%
  withholding by the existing harness (`fwd`-style total return).
* **Universe:** the harness point-in-time eligibility
  (`families_idx_structural.eligibility`: 60-session median value traded ≥
  Rp 2B, price ≥ Rp 100, ≤ 3 zero-volume days, ≥ 252 sessions of history),
  checked on the entry-signal day. Exits are never blocked by eligibility.
* **Survivorship:** the local set is mostly survivors (plan 22 §2.2, alpha
  search §2). This flatters every long rule and the null alike; it is
  recorded, not fixed.
* IHSG from `research/data/daily/IHSG.parquet` (G6, G8).
* Bars with |1-day return| > the harness `BAD_BAR_ABS_RETURN` inside a trade
  void that trade, as the harness does.

## 3. Trade mechanics and costs

* **Entry:** `open_{t+1}` after an entry signal on *t* (D1 lesson). Skip the
  trade if `open_{t+1}` is at the upper auto-rejection limit (harness
  `locked_up_entry`).
* **Exit:** `open_{u+1}` after the first exit signal or time stop on day
  *u* > *t*. If the name stops trading, exit at the next traded open.
* **One position per name per rule.** Entry signals while a position is open
  are ignored.
* **Window boundary:** trades are assigned to a window by entry date. A
  development trade still open at the development end is closed at the open
  of the last development session, so no holdout price is ever read in
  development.
* **Costs:** harness `cost_components` at the entry price: one tick + 40 bps
  fee/tax + point-in-time sqrt impact, at 1 lot and at Rp 3M notional. One
  round trip per trade. Own-cash sleeve: no financing.

## 4. Test rules (frozen at §9 sign-off)

### 4.1 Cells (G7 Holm denominator: 5)

R1 … R5, one cell each. C-rand and the portfolio view (§4.4) are reported only.

### 4.2 Windows

* **Development:** entries 2015-01-01 → 2022-12-31 (indicator warm-up uses
  earlier bars).
* **Holdout:** entries 2023-01-01 → the last full session before the holdout
  run, **run once**, behind `research/results/indicator_rules/HOLDOUT_AUTHORIZED`.

### 4.3 Gates (blocking unless marked)

Per-trade quantities, in bps: `gross` = total return entry→exit;
`null` = hold length × the same name's mean daily total return over all
eligible sessions of the entry's calendar year (what being in that stock on
average days that year earned); `edge = gross − null`; `net = gross − cost`.

| gate | rule |
|---|---|
| G1 | live-feasible entry and exit at the next open (§3) |
| G2 | mean edge > 0 with **t ≥ 2, clustered by entry calendar month** (variable holds overlap across names, so month clusters are the honest unit) |
| G3 | mean (edge − cost) > 0 under current coeffs, the 2026-10-14 candidate coeffs, and 1 lot |
| G4 | mean net > 0 after dropping the 5 best entry dates |
| G5 | holdout: edge > 0 and G3 on the holdout alone. Nothing tuned between windows |
| G6 | mean (net − IHSG return over the same entry→exit window) > 0 |
| G7 | Holm across the 5 cells |
| G9 | with entries dated 2020-02-01 → 2020-12-31 removed, development still has edge > 0, net > 0 and net − IHSG > 0 |
| G8 | *reported, not blocking:* split by IHSG above / below its 200-session average on the entry-signal day |

**Minimum sample:** ≥ 100 trades and ≥ 24 distinct entry months in
development; ≥ 30 trades and ≥ 10 months in holdout. Below that the cell is
`insufficient_n`, not a fail.

### 4.4 Reported alongside (not gated)

* Trades, mean and median hold, share of time in market, win rate.
* Portfolio view per rule: equal-weight across open positions, daily, net of
  cost, against the equal-weight eligible-universe buy-and-hold and IHSG:
  CAGR, max drawdown, worst year. This answers "does the trend rule at least
  avoid crashes", which per-trade edge does not.
* C-rand percentile of each rule's mean gross.

## 5. Decision rule

* **A cell passes G1–G4, G6, G7 and G9 in development, then G5 in holdout:**
  write a forward-test pre-registration on the plan 20 model (paper first,
  then 1 lot, rupiah worst case stated before the first order).
* **Development passes, holdout fails:** close the family; no re-test with
  new settings.
* **Only the portfolio view looks good** (smaller drawdown, no per-trade
  edge): recorded as a risk-control finding, not a trading signal. It may
  inform position sizing later; it is not a strategy.
* **Nothing passes in development:** close; holdout stays unspent.

## 6. Phases

| phase | work | effort |
|---|---|---|
| 1 | `research/strategy_fit/families_indicator_rules.py` (indicators, variable-hold trade builder, gates reusing plan 22 helpers) + tests on synthetic series with hand-checked signals; development run → `research/results/indicator_rules/dev.{json,md}`, `dev_trades.csv` | 1 day (agent) |
| 2 | Holdout run, once, only if §5 allows and the owner creates the authorization file | ½ day |

## 7. Risks and known biases

* **Settings are fixed on purpose.** The common way these rules "work" is a
  search over periods and thresholds. Any later "what about SMA 20/100" is a
  new plan with its own Holm count.
* **Survivorship flatters long rules** (§2). A pass would still need the
  forward test.
* **Bull drift.** 2015–2022 includes the 2020 crash and recovery; G9 and the
  per-trade null (same name, same year) stop drift from counting as edge.
* **Overlap.** Trend rules hold for months; trades in many names overlap the
  same market move. Month clustering in G2 accounts for that.

## 8. Open questions

None. Settings are the textbook defaults; the owner asked for a pre-registered
test with standard settings (session 2026-10-17).

## 9. Sign-off

Frozen when the owner fills this in. Gates and cells are not edited after this
line. Any later change is a new dated row with the reason.

| date | change | by |
|---|---|---|
| 2026-10-17 | Freeze §1–§5 as written. Signed before any indicator signal or return was computed on these rules. | @lewis (owner; instructed in session, entered by Claude) |

## 10. Results log (append-only)

*Empty until the development run.*

---

Back to [plans index](./README.md)
