# IDX alpha search — structural hypotheses, 2026-10

> **Status:** research note, not a plan · **Data:** local daily bars `research/data/daily/*.parquet` (241 names, 2000-11 .. 2026-10-14; the 10-15 placeholder bar is dropped) · **Code:** `research/strategy_fit/families_idx_structural.py`, runner `run_idx_structural.py`, tests `test_families_idx_structural.py` (101) · **Raw output:** `research/results/idx_structural/results.{md,json}` · **No API, token or `.env*` file was touched.**

## 1. Bottom line

- **Nothing passes.** 24 pre-registered variants (Bonferroni m = 24) through the standard gates: D1 entry, full cost stack, always-long null, 60/40 OOS, ex-top-5, 10x capacity, plus a new absolute-return gate. Zero PASS.
- **Two weak leads, both "promising-needs-data", neither tradeable on this evidence:**
  1. **ARB-rebound, bear regime only** (long the day after an ARB-class close, buy at the next open, 1–5 day hold). Positive in 12 (5d) and 11 (1d) of 15 bear episodes, episode-level t = 2.3–3.0. Blocked by survivorship (names that crashed and died are not in the universe), only ~15 independent episodes, heavy tails (one episode loses 24%), and a Bonferroni bar it does not meet.
  2. **12-1 momentum / 52-week-high, monthly-to-quarterly rebalance.** Net +47 to +221 bps per period over the equal-weight universe after cost, positive in IS, OOS and all three sub-periods, but t = 1.5–1.6 at the pre-registered phase (1.97–2.64 at other phases), fails Bonferroni, and **loses money in the bear regime**, so it is not the bear play.
- **Regime now: bear.** The equal-weight liquid-universe index is 25% below its 52-week high and below its 200-day MA, in a bear state since 2026-05-18 (102 days). A volume-weighted top-30 cross-check is 43% below its high.
- **Every simple bear-regime play failed** (defensive low-vol, high dividend, bear-to-cash). The bear flag fires after most of the damage and the universe rebounds on average (and is survivor-flattered), so "go to cash" gave up about half the return.
- **The universe cannot settle long-horizon or crash-rebound questions.** Plan 18 A-3 already said so, and the numbers agree: the equal-weight always-long null returns about +150 bps per month (~20%/yr) here versus ~10%/yr for the real index.

## 2. What was already tested (inventory)

- Single-name daily: `daily_momentum` (1-day), `mean_reversion` (5-day), `volume_breakout`. Event: `gap_event`, `volume_spike_event`, `foreign_flow_spike_event`. Cross-sectional: 1-day-return rank IC. Pairs: cointegration. Intraday: ORB, intraday momentum, intraday mean-reversion. All hold <= 5 days, mostly 2024+ data. D1 entry killed every survivor (`results/d1/`, `results/d1_xsec/`, `results/costwin/`; `docs/research/2026-10-15-first-candidate-shortlist.md`: nothing qualifies).
- **Not previously touched:** long-horizon (21–63 day) low-turnover factor books on the full 2000+ history, limit-class (ARA/ARB) events, dividend ex-date events, calendar windows, overnight/intraday decomposition, regime conditioning.

**Data available locally:** daily OHLCV + value + foreign buy/sell + ex-date dividend per share (price is split-adjusted, **not** dividend-adjusted; dividends added back net of 10% withholding) for 241 names, 132 of them listed before 2010; 1-minute and tick-bar captures for ~7–10 names only (~53 sessions); no index series, no constituent history, no announcement dates, no delisted names before ~2021 (39 names end before 2026-10: 25 on 2021-07-02, ~10 in 2025, the rest scattered).

## 3. Ranked hypotheses

Prior = my subjective probability that a net-of-cost, regime-robust edge exists. T = tested here.

| # | Hypothesis | IDX mechanism | Prior | Status |
|---|---|---|---|---|
| 1 | ARB-rebound, long-only, bear regime | Forced/panic selling at the auction limit, retail capitulation, 15% asymmetric ARB since 2023; bounce when the sell queue clears | 35% | T — lead |
| 2 | 12-1 momentum / 52w-high, monthly–quarterly | Slow information diffusion, foreign/institutional herding, low turnover amortises 40 bps fee | 25% | T — weak lead |
| 3 | LQ45 / IDX80 / MSCI inclusion-exclusion flow | Passive and benchmark-tracking forced buyers on known dates | 40% | **needs data** |
| 4 | Dividend run-up (yield >= 2%) | Retail dividend demand into cum-date | 20% | T — fail on cost |
| 5 | Foreign net-flow accumulation (60d) | Foreign institutions are informed and slow | 20% | T — fail |
| 6 | Low-vol / defensive | Lottery demand overprices volatile small caps; defensive in bear | 15% | T — fail |
| 7 | Bear-to-cash regime filter | Avoid drawdown with a long-only account (shorting is restricted on IDX, no short leg counted anywhere) | 20% | T — fail |
| 8 | Turn-of-month, pre-Lebaran | Salary/window-dressing flows, pre-holiday optimism | 10% | T — fail |
| 9 | Overnight vs intraday; overnight-return persistence | Pre-open auction pricing, retail intraday noise | 10% | T — descriptive + fail |
| 10 | IPO early drift / lock-up expiry | Underwriter support, 8–12 month lock-up supply | 30% | **needs data** |
| 11 | Post-earnings drift | Slow retail/institutional reaction, thin analyst coverage | 25% | **needs data** |
| 12 | UMA / FCA-board / suspension-resumption transitions | Forced regime changes with known rules | 25% | **needs data** |

Also tested as extras (counted in m): ARA continuation, 1-month reversal, trailing dividend-yield factor.

## 4. Method

Pre-registered before any run; nothing re-tuned after seeing results except the one disclosed regime fix below.

- **Universe, point in time:** trailing-60d median value >= Rp 2 bn, price >= Rp 100, <= 3 zero-volume days in 60, >= 252 bars. Eligible names: 35 in 2006, 71 in 2012, 114 in 2022, 149 in 2026. Events use eligibility as of the day *before* the signal (the event's own volume spike cannot qualify it).
- **Entry (G1):** signal on close of day s, enter `close_{s+1}` (`next_close`, the live bot's convention); `next_open` shown as a sensitivity and labelled "needs an order at the open". Dividend and calendar schedules use only public calendar facts; the yield filter uses the decision-day close.
- **Cost (G2):** one-tick spread (IDX ladder) + 40 bps round trip + calibrated square-root impact on trailing 60-bar ADV/vol (`impact_coeffs.json`) at Rp 30m per name (300M / 10), re-priced at 10x for capacity. Factor books pay **only** for names that enter or leave (half a round trip per leg). Financing is shown only where a hold <= 2 days could be leveraged (cum-date capture: ~27 bps more); longer holds are owned-cash only (ADR-0015).
- **Always-long null (G3):** events vs (a) the same name's mean forward return in the same calendar year and (b) the equal-weight eligible universe the same day; both nets must be positive. Factor books vs the equal-weight eligible universe over the identical windows.
- **OOS (G4):** chronological 60/40 of clusters (no fitted parameters, so it is a stability check). Clusters = signal date (events) or rebalance period (books).
- **Multiple testing (G5):** Bonferroni over m = 24 (7 factors x 2 holds, 4 limit-event, 3 dividend, 2 calendar, 1 regime filter). Sensitivities (`next_open`, all-yield dividend, raw regime) not counted. Rebalance phase checked at 3 offsets.
- **Capacity (G6):** net excess at 10x notional must stay > 0. Median participation of the event trades is 0.08–0.2% of ADV at Rp 30m, so capacity is not the binding constraint here; cost is.
- **Absolute-return gate (new):** mean absolute net (gross − cost) must be > 0 overall and in the bear split (when >= 10 bear clusters). Beating always-long while losing money is not a pass.
- **Bad bars:** |1-day return| > 50% marks a suspected unadjusted split; every forward window touching one is dropped.
- **Regime (point in time):** equal-weight daily return of the names eligible on the *previous* close. Bear = index below its 200-day MA **and** drawdown from its 252-day high worse than 15%, with hysteresis (stays bear until drawdown recovers to −7.5%). **Disclosed change:** the first version (plain AND) flickered, 50 episodes many one day long, 101 flips; I added the hysteresis state machine, then re-ran everything. Raw-flag filter result kept as a sensitivity row. No IHSG series is stored, so this is a proxy.
- **Checked by hand:** three ARB `next_open` trades recomputed from raw parquet match the engine to 5 decimals; signal day closes at its low with the stated return; entry and exit bars are s+1 and s+6.

## 5. Results

bps; `net_x` = net excess over the always-long name null (factor books: over the equal-weight universe). `abs` = gross − cost. p_bonf = Bonferroni-adjusted (m = 24) two-sided p of `net_x`. Bear/non-bear columns show **trades (events) or periods (books) / net_x / abs**; the regime is the one on the signal date.

| variant | n (clusters) | gross abs | cost | **net_x** | t | p_bonf | IS / OOS | ex-top5 | 10x | bear: n / net_x / abs | non-bear: n / net_x / abs | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| low_vol h21 | 251 | 158 | 28 | −22 | −0.92 | 1.00 | 0 / −55 | −45 | −26 | 72 / −35 / 241 | 179 / −16 / 86 | FAIL |
| low_vol h63 | 83 | 444 | 49 | −109 | −1.01 | 1.00 | −69 / −169 | −216 | −116 | 22 / −408 / 738 | 61 / −1 / 272 | FAIL |
| **mom_12_1 h21** | 251 | 224 | 26 | **+47** | 1.46 | 1.00 | 42 / 55 | 13 | 42 | 72 / −118 / 157 | 179 / +114 / 216 | promising-needs-data (weak) |
| **mom_12_1 h63** | 83 | 768 | 45 | **+221** | 1.59 | 1.00 | 211 / 235 | 20 | 211 | 22 / −384 / 753 | 61 / +439 / 711 | promising-needs-data (weak) |
| high52w h21 | 251 | 204 | 42 | +10 | 0.35 | 1.00 | −7 / 36 | −15 | 4 | 72 / −103 / 174 | 179 / +56 / 158 | FAIL |
| high52w h63 | 83 | 729 | 56 | +171 | 1.45 | 1.00 | 84 / 303 | 35 | 162 | 22 / −356 / 782 | 61 / +361 / 634 | FAIL (same signal as momentum) |
| foreign_accum h21 | 251 | 143 | 37 | −46 | −2.17 | 0.75 | −39 / −56 | −68 | −52 | 72 / −49 / 228 | 179 / −45 / 57 | FAIL |
| foreign_accum h63 | 83 | 666 | 70 | +91 | 0.88 | 1.00 | 191 / −59 | −67 | 79 | 22 / −70 / 1075 | 61 / +150 / 422 | FAIL |
| rev_1m h21 | 251 | 126 | 79 | −105 | −3.86 | 0.003 | −121 / −79 | −133 | −119 | 72 / +16 / 293 | 179 / −153 / −51 | FAIL |
| rev_1m h63 | 83 | 434 | 86 | −156 | −1.78 | 1.00 | −259 / 1 | −269 | −172 | 22 / +71 / 1216 | 61 / −237 / 35 | FAIL |
| div_yield h21 | 251 | 182 | 16 | +15 | 0.62 | 1.00 | 19 / 8 | −11 | 12 | 72 / +81 / 358 | 179 / −12 / 90 | FAIL |
| div_yield h63 | 83 | 506 | 26 | −24 | −0.31 | 1.00 | 6 / −69 | −111 | −29 | 22 / +6 / 1151 | 61 / −35 / 238 | FAIL |
| overnight_persist h21 | 251 | 148 | 37 | −42 | −1.48 | 1.00 | −39 / −46 | −69 | −49 | 72 / +8 / 284 | 179 / −61 / 41 | FAIL |
| overnight_persist h63 | 83 | 516 | 72 | −60 | −0.66 | 1.00 | −112 / 19 | −168 | −74 | 22 / +132 / 1277 | 61 / −129 / 144 | FAIL |
| ARA 1d (next_close) | 383 (306) | 12 | 113 | −132 | −3.36 | 0.02 | −160 / −90 | −173 | −171 | 167 / −125 / −109 | 216 / −136 / −97 | FAIL |
| ARA 5d (next_close) | 330 (272) | 306 | 112 | +35 | 0.35 | 1.00 | 83 / −37 | −83 | −2 | 136 / +58 / 150 | 194 / +22 / 216 | FAIL |
| ARB 1d (next_close) | 273 (143) | −189 | 112 | −307 | −3.65 | 0.01 | −258 / −380 | −402 | −340 | 137 / −209 / −211 | 136 / −391 / −378 | FAIL |
| ARB 5d (next_close) | 218 (117) | −299 | 110 | −481 | −2.81 | 0.14 | −418 / −574 | −682 | −513 | 115 / −51 / −28 | 103 / −824 / −713 | FAIL |
| ARB 1d next_open (sens.) | 273 (143) | −16 | 112 | −136 | −1.21 | 1.00 | −51 / −265 | −249 | −169 | 137 / **+23 / +23** | 136 / −273 / −258 | bear-only lead |
| ARB 5d next_open (sens.) | 218 (117) | −5 | 110 | −187 | −0.93 | 1.00 | 50 / −540 | −399 | −220 | 115 / **+349 / +374** | 103 / −616 / −506 | bear-only lead |
| div runup (yield >= 2%) | 842 (607) | 110 | 91 | −17 | −0.76 | 1.00 | 7 / −53 | −37 | −32 | 321 / +18 / 51 | 521 / −36 / 1 | FAIL |
| div cum-date capture | 847 (607) | 80 | 91 | −25 | −1.35 | 1.00 | −17 / −37 | −50 | −40 | 335 / +36 / 47 | 512 / −59 / −43 | FAIL |
| div post-ex rebound | 852 (612) | −36 | 92 | −162 | −6.09 | 0.00 | −165 / −159 | −194 | −178 | 338 / −146 / −113 | 514 / −172 / −136 | FAIL (significantly negative) |
| turn-of-month (hold 3) | 296 | 49 | 87 | −60 | −2.89 | 0.10 | −55 / −67 | −75 | −69 | 97 / −93 / −72 | 199 / −44 / −22 | FAIL |
| pre-Lebaran (hold 10) | 26 | 182 | 89 | +21 | 0.18 | 1.00 | 132 / −156 | −176 | 11 | 9 / +94 / 167 | 17 / −18 / 55 | FAIL |
| bear-to-cash filter | 17 episodes | 0 | 89 | −1,153 | −4.64 | — | — / −1,052 | — | — | n/a | n/a | FAIL |
| bear-to-cash, raw flag (v1) | 50 episodes | 0 | 88 | −216 | −2.21 | — | −213 / −220 | −379 | −224 | n/a | n/a | FAIL |

`next_open` rows are sensitivities and carry no verdict of their own; they need a bid placed in the pre-open auction.

### Measured, by hypothesis

- **Momentum / 52w-high (books of ~16 names).** Phase check at the other rebalance offsets: mom h21 net_x +47 / +64 / +82 (t 1.46 / 1.97 / 2.64); mom h63 +221 / +294 / +162 (t 1.59 / 2.57 / 1.39); high52w h63 +171 / +154 / +67. Sub-periods for mom h21: 2006–12 +64 (t 1.03), 2013–19 +27 (0.65), 2020–26 +73 (1.11). Mean one-way turnover 0.26 (h21) and 0.46 (h63). The phases share the same underlying data, so the three t-stats are not independent. Concentration: ex-top-5 periods the edge falls to +13 / +20 bps, so a handful of periods (e.g. 2007, 2021) carry it.
- **Regime behaviour.** Momentum, 52w-high and low-vol net_x are negative in the bear regime (n = 22–72 periods) and positive outside it, so the "momentum crash" is visible. `rev_1m` and `div_yield` are slightly positive in bear (+16 to +81) and negative outside, none with t > 1.6.
- **ARB-rebound by bear episode (15 episodes).** `next_open`, 5d: episode-mean net_x +716 bps (t = 2.25), 12 of 15 episodes positive, abs +751; 1d: +541 (t = 2.97), 11 of 15 positive. Episodes of 2025-02 (n = 29), 2026-05-18 (n = 37, the **current** episode, +582 so far at 5d) and 2008-03 (n = 14) carry most trades; the 2020-01 episode is −2,437 bps. Under the `next_close` entry, which is what the live bot does, ARB is significantly negative (gross −189 / −299 bps), i.e. the rebound happens at the next open, not after it.
- **Dividend.** Run-up gross +110 bps against a 91 bps cost, so the effect exists but is smaller than cost (net −17, t −0.76); in the bear split +18. Post-ex is strongly negative (price keeps falling after the ex-date).
- **Calendar.** Turn-of-month gross +27 bps over the basket's own drift versus 87 bps cost. Pre-Lebaran: 26 windows, IS +132, OOS −156.
- **Overnight vs intraday (EW eligible, bps/day).** All: overnight +6.8, intraday +3.6. By era: 2004–12 −1.5 / +20.8; 2013–19 +14.5 / −11.3; 2020–26 +21.6 / −17.1. Bear: −2.8 / +9.2. The sign flip in 2013 looks like a change in what `open` means in the feed (opening-auction price vs first trade), so overnight-based signals are not trustworthy on this data. Capturing even +21 bps/day overnight needs a round trip per day (cost >= 45 bps), so it cannot work as a daily trade regardless.
- **Regime filter.** Selling the liquid-30 basket when the bear flag fires and re-buying when it clears forgoes 1,064 bps gross per episode on average (the index is **up** on average from flag-on to flag-off) and costs 89 bps. Whole-sample filter vs buy-and-hold of the EW index: CAGR 8.3% vs 16.7%, max drawdown −34% vs −68%. It is a drawdown-reduction tool, not alpha, and it fails the harness's relative gate. Current open episode: basket −5.1% since the exit bar.

### Regime as of the latest bar (2026-10-14), measured

| | EW eligible index | ADV-weighted top-30 (cross-check) |
|---|---|---|
| Drawdown from 52w high | −25.3% | −43.1% |
| Below 200-day MA | yes (4,111 vs 4,625) | yes |
| 1m / 3m / 6m / 12m | −8.3% / −0.4% / −15.5% / −10.8% | — |
| State | **bear since 2026-05-18** | bear |

Share of days in the bear state over the sample: 30.7% (1,863 days), 18 episodes (the 2008–09, 2015–16, 2020 and 2025 episodes are the long ones).

## 6. Skepticism: why these numbers are softer than they look

- **Survivorship (largest issue).** Plan 18 A-3 recorded that the funding endpoints return currently tradeable names; 39 of 241 here end before 2026-10 and none of the earlier delistings are present. The always-long null returns ~+150 bps/month (~20%/yr). That inflates absolute returns in every row (the absolute-return gate is therefore nearly vacuous for the books) and biases **bear-regime rebounds** and the ARB-rebound lead upward, because the crashed names that never came back are missing. I cannot size the bias from local data. It likely biases *momentum* excess downward (dead losers missing makes the null too high), but that is reasoning, not measurement.
- **Few independent bear episodes.** 18 episodes, ~15 with ARB events, and 2025–26 alone supply 126 of 218 ARB events. Date-level t-stats overstate the evidence; the episode-level numbers above are the honest ones. Any bear-split result here is also a second look at the same data, so I would apply an additional ~x2 on the Bonferroni count (48 looks): the best ARB episode t = 2.97 (p ~ 0.01 two-sided, df = 14) does not clear 0.05/48.
- **Heavy tails.** Per-trade 5-day ARB returns range down to −59% (one 2014 trade) and calendar-year means reach +35%; the means are driven by few names (SMIL, BRMS, ENRG, MSIN, FILM contribute the most signals).
- **Fillability is unmeasured.** The ARB-rebound lead assumes a fill at the opening auction price on the day after a limit-down close. Locked bars are skipped for ARA entries; nothing equivalent can be checked for ARB buys with daily data.
- **Proxies.** Limit-class events are defined by bar shape (>= +19% closing at the high; <= −14% closing at the low), not the true per-tier ARA/ARB price, which changed in 2015, 2020 and 2023. Dividend yield, ex-date and amount come from the feed's `dividend` column with no announcement date; the run-up variant assumes the ex-date was known at entry (typically true 1–3 weeks ahead, unverified).
- **Equal-weight null is generous.** Daily-rebalanced equal weight in illiquid, mean-reverting names earns a rebalancing premium and a bid-ask-bounce bias, so the null is probably high; a value-weighted null would be lower, but I did not measure by how much.
- **Cost is the dominant failure.** Event families trade low-priced names with 1–2% round-trip floors (mean cost 87–113 bps). A post-hoc "high-priced names only" cut would likely close the dividend run-up gap, but choosing it after seeing the result is tuning, so it is listed as a next step, not a finding.

## 7. Recommended next step

1. **Do not paper-trade or deploy any of these as-is.**
2. **Close the survivorship hole first** (data request 1). It decides both leads: ARB-rebound-in-bear and momentum are the only two candidates whose result moves materially with delisted names added, and a point-in-time universe is the prerequisite plan 18 A-3 already names. Until then, long-horizon and crash-rebound verdicts measure survivorship.
3. **If the user wants a bear-regime forward test regardless:** pre-register ARB-class `next_open` rebound, hold 1–5 days, price >= Rp 2,000 (one-tick spread <= 0.5%, so the round-trip floor is <= ~90 bps; this price cut is a new choice, not a tested one), bear-regime only, 1–2 lots, paper first, record the open auction fill. Median trade cost in the backtest was ~110 bps; the backtest gross in the bear split is +100 to +750 bps with huge variance, so the test is a futility test, not a confirmation. Live cadence is ~15–40 signals per bear month, and the current bear episode has already produced 37–49 ARB-class events.
4. **Pre-register a replication of momentum** on the survivorship-free universe with a single hold (63 days) and the phase-averaged (three tranches) book, using a value-weighted null.
5. Do not spend more time on: low-vol, foreign-flow accumulation, 1-month reversal, turn-of-month, pre-Lebaran, post-ex rebound, bear-to-cash (all negative net after cost, several significantly), or the overnight/intraday decomposition on this feed.

## 8. Data requests (nothing was fetched)

1. **Point-in-time listing universe including delisted/suspended names**, with delisting dates and last prices (and ideally tradeable flag history, plan 18 A-3).
2. **Index series and constituent history:** IHSG, LQ45, IDX80, IDX30, MSCI Indonesia, FTSE; semi-annual/quarterly rebalance announcement and effective dates (hypothesis 3, and a real regime series).
3. **Corporate-action calendar with announcement dates:** cum, ex, record, payment dates, declared amount; rights issues, stock splits, buybacks (hypotheses 4, plus verifying the ex-date assumption).
4. **IPO list:** offer price, listing date, lock-up end dates, allocation (hypothesis 10).
5. **Earnings release dates and EPS/consensus** (hypothesis 11).
6. **Exchange special-board history:** UMA, FCA, special notation, suspension and resumption dates (hypothesis 12).
7. **Exact daily ARA/ARB price bands per name per day** (replaces the bar-shape proxy), plus **opening-auction (pre-open) indicative price and first-minute book** for names with ARB closes, to verify fillability of the rebound entry.
8. **A longer intraday capture across more names** if intraday tests are wanted; the existing 7–10 names x ~53 sessions cannot support a regime split.

## 9. Files

- Added: `research/strategy_fit/families_idx_structural.py`, `research/strategy_fit/run_idx_structural.py`, `research/strategy_fit/test_families_idx_structural.py`, `research/results/idx_structural/{results.md,results.json}`, this note.
- Reproduce: `cd research && python3 -m strategy_fit.run_idx_structural --data-dir <main checkout>/research/data/daily --outdir results/idx_structural` (about 6–8 min, no network).
- Nothing existing was modified. The data directory is gitignored and lives only in the main checkout, so the runner takes it as an argument.
