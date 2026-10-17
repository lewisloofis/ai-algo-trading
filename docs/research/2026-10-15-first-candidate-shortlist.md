# First-candidate shortlist — what to forward-test first (2026-10-15)

> **Status:** research note, not a plan · **Owner:** @lewis · **Data:** local daily bars through **2026-10-14** (the 10-15 bar is an in-progress placeholder and is dropped) · **Reproduce:** [§9](#9-reproduce)
> **Related:** [plan 10](../plans/10-strategy-fit-and-portfolio.md) (2026-10-14 re-scores) · [plan 18 §4.4a](../plans/18-alpha-and-infrastructure-split.md) · [plan 20](../plans/20-bnbr-forward-test-preregistration.md) · [ADR-0015](../adr/0015-leverage-optional-horizon-ceiling-lifted.md) · [ADR-0017](../adr/0017-sleeve-is-a-funded-sub-account.md)

## 1. Recommendation

**Nothing qualifies for a live 1-2 lot test.** No cell and no family clears the blocking bar: the best edge-over-null t-stat among the 13 name-level cells is 1.44, no family x hold test survives Holm correction (best adjusted p = 0.74), and no cell holds up in an independent 2015-2023 window once its five best trades are removed.

**Paper-only, zero cost:** forward-test **WIFI `daily_momentum` h5** first, with **PTRO `daily_momentum` h5** alongside it (both emit through the existing daily emitter path; see §7). They are the only two cells that pass two of the four quantitative gates (edge over null beats cost under every impact fit, and still positive after dropping the top 5 trades). They fail the other two (t >= 2, independent-window OOS). Treat paper as a plumbing check and a futility test, not as evidence of an edge (§6).

Money numbers for the paper pair, if it ever went live at 1 lot:

| | lot notional | round-trip cost (fee + 1 tick) | trades / month | expected cost / month | stress worst month | backtest worst month (today's lot) |
|---|---:|---:|---:|---:|---:|---:|
| WIFI h5 | Rp 137,000 | Rp 1,048 | 2.8 | **Rp 2,886** | Rp 137,206 (lot lost) | Rp -33,753 |
| PTRO h5 | Rp 412,000 | Rp 2,648 | 2.8 | Rp 7,291 | Rp 412,618 (lot lost) | Rp -110,148 |

Both would have a signal inside the next ~2 weeks (P(at least one entry in any 10-session window) = 0.97 / 0.99, last 12 months). As of the 10-14 close the emitter chain says both are **already in a position** (WIFI and PTRO: exit in 4 sessions), so a fresh start would wait for the next flat-then-signal day.

## 2. What "bar" was applied

All blocking, per the brief. `G1` live timing and `G6` executable-now hold by construction for every row (next-session `close_{s+1}` entry, `entry_convention` default; daily `rebalance` target).

| gate | definition used here |
|---|---|
| G2 | edge over the always-long null (same name, same window, same hold, every priced day) has Welch t >= 2 (null effective N = days / hold) |
| G3 | edge over null **minus cost** > 0 under the current coeffs, the 2026-10-14 candidate fit, **and** the 1-lot cost |
| G4 | mean net (gross - cost) still > 0 after dropping the 5 best trades |
| G5 | the **same cell on 2015-2023** (selection-independent; the grid was selected on 2024+): edge > 0, net > 0 at 1-lot cost, and still > 0 after dropping its 5 best trades. Chosen over the 60/40 split because n~50 OOS verdicts are noise (memory: `sb-plan10-strategy-fit-status`) |
| G7 | family-level support: the family x hold, pooled over 199 names, has a Holm-adjusted p < 0.10 for edge over null |

**Cost = tick_bps(entry price) + 40 + impact.** 40 bps is 0.15% buy + 0.25% sell (sell includes the 0.1% sales tax): verified in `research/backtest/backtest.py` `FEE` and `strategy/engine/sink.go` `ScheduleFeeIDR`, no minimum fee observed (runbook §15: 4 IDR on 2,800). Spread = one tick crossed round trip (the grid's convention), price-matched at each trade's entry price rather than at today's price. Impact = `2 * coeff(ADV bucket) * vol60 * sqrt(Q / ADV60)`, point-in-time at the signal day (vectorised form of `TrailingImpactModel`, verified equal to it to 1e-9 on BNBR/PTRO/DEWA/RAJA and in a unit test). Three coefficient sets:

* **current** `impact_coeffs.json` (per-name where confirmed, else bucket 0.345 / 0.301 / 0.293)
* **cand** = the 2026-10-14 "latest block" candidate (`spread-power` file, `no_code_change_alternative`): buckets 0.396 / 0.418 / 0.355. The most conservative of the three; the pooled v2 candidate (0.353 / 0.358 / 0.345) is also computed in the JSON.
* **1-lot**: cand coefficients at Q = 100 shares instead of the harness's Rp 3M.

## 3. Ranked name-level shortlist

Ranked by gates passed (of G2-G5 and G7; G7 is false for all), ties broken by lowest expected cost per month. These are the 10 cells the 2026-10-14 re-score flagged plus its 3 killed cells as controls; I did **not** scan for new winners (§8). bps per round trip.

| # | cell | n (eff N) | trades/mo | gross | null | edge over null | t | gates | verdict |
|---|---|---:|---:|---:|---:|---:|---:|:---:|---|
| 1 | WIFI daily_momentum h5 | 92 | 2.8 | 363.2 | 254.0 | 109.2 | 0.55 | G3, G4 | not credible; 70% of gross is null drift |
| 2 | PTRO daily_momentum h5 | 92 | 2.8 | 375.2 | 245.2 | 130.0 | 0.73 | G3, G4 | not credible; the known 29.7x-runner beta name (null 245) |
| 3 | DEWA daily_momentum h5 | 91 | 2.7 | 319.0 | 185.2 | 133.7 | 0.86 | G4 | fails G3: edge-net -3.1 / -3.8 / -1.0 |
| 4 | RAJA daily_momentum h5 | 89 | 2.7 | 293.5 | 128.8 | 164.8 | 0.87 | G3 | fails G4: -54.4 without top 5 |
| 5 | DSSA volume_breakout h2 | 149 | 4.5 | 152.0 | 47.5 | 104.5 | 1.44 | G3 | fails G4 (-10.3); 2015-23 "edge" is a penny-stock lottery (§4) |
| 6 | TAPG daily_momentum h5 | 95 | 2.8 | 210.0 | 119.8 | 90.2 | 1.12 | G4 | fails G3 (-4.9 / -5.5 / -3.0) |
| 7 | BRPT volume_breakout h2 | 176 | 5.3 | 121.5 | 30.2 | 91.3 | 1.36 | G3 | fails G4 (-43.5); pre-24 edge +5.6, t 0.2 |
| 8 | TINS mean_reversion h5 | 81 | 2.4 | 277.4 | 197.0 | 80.4 | 0.57 | G4 | fails G3; pre-24 edge -71 |
| 9 | BIPI daily_momentum h5 | 85 | 2.5 | 240.9 | 89.1 | 151.8 | 0.70 | none | edge-net ~0; drop-5 -224 |
| 10 | RATU daily_momentum h1 | 116 | 3.5 | 31.5 | 31.0 | 0.5 | 0.01 | none | no edge over null |
| c1 | BNBR daily_momentum h2 | 103 | 3.1 | 57.0 | 48.9 | 8.1 | 0.08 | none | dead (matches plan 20 P1) |
| c2 | HRTA volume_breakout h5 | 94 | 2.8 | 164.8 | 173.5 | -8.7 | -0.07 | none | dead, loses to null |
| c3 | INCO mean_reversion h2 | 143 | 4.3 | 36.5 | 12.8 | 23.7 | 0.46 | none | dead |

Cost, net and robustness (same order; "cur/cand/1lot" = cost or edge-net under the three cost bases; net = gross - cost, which **includes drift** and therefore overstates what the signal earns):

| # | cost cur / cand / 1-lot | edge-net cur / cand / 1-lot | net cur / cand | drop-top-5 net (cand) | 2015-23 n / edge / t | 2015-23 net 1-lot / drop-5 edge-net | harness 60/40 OOS edge / n |
|---|---|---|---|---:|---|---|---|
| 1 WIFI | 91.0 / 91.6 / 89.4 | +18.2 / +17.6 / +19.8 | 272.2 / 271.6 | +11.1 | 93 / +10.0 / 0.10 | -187.8 / -204.3 | 30.3 / 35 |
| 2 PTRO | 80.9 / 81.3 / 79.4 | +49.1 / +48.7 / +50.6 | 294.3 / 294.0 | +86.5 | 289 / +41.2 / 0.67 | -30.2 / -163.8 | 220.9 / 37 |
| 3 DEWA | 136.8 / 137.5 / 134.8 | -3.1 / -3.8 / -1.0 | 182.2 / 181.5 | +8.3 | 71 / -58.0 / -0.50 | -245.6 / -414.0 | 323.0 / 37 |
| 4 RAJA | 110.0 / 110.7 / 107.9 | +54.8 / +54.1 / +56.9 | 183.5 / 182.8 | -54.4 | 257 / +1.1 / 0.01 | -164.7 / -312.5 | 175.3 / 36 |
| 5 DSSA | 88.3 / 90.3 / 80.8 | +16.1 / +14.2 / +23.7 | 63.6 / 61.7 | -10.3 | 49 / +424.0 / 2.54 | +276.4 / -38.7 | 64.3 / 59 |
| 6 TAPG | 95.1 / 95.7 / 93.2 | -4.9 / -5.5 / -3.0 | 114.9 / 114.3 | +41.5 | 88 / +32.6 / 0.30 | -74.1 / -227.5 | 182.0 / 36 |
| 7 BRPT | 83.6 / 84.4 / 83.2 | +7.7 / +6.9 / +8.1 | 37.9 / 37.1 | -43.5 | 538 / +5.6 / 0.20 | -88.4 / -163.2 | 31.2 / 64 |
| 8 TINS | 88.5 / 88.3 / 86.0 | -8.1 / -7.9 / -5.6 | 188.9 / 189.2 | +13.4 | 281 / -71.1 / -1.34 | -154.5 / -207.3 | 448.8 / 30 |
| 9 BIPI | 154.0 / 154.8 / 151.3 | -2.2 / -2.9 / +0.6 | 86.9 / 86.1 | -223.7 | 157 / -42.4 / -0.45 | -173.9 / -326.2 | 401.0 / 33 |
| 10 RATU | 76.8 / 77.1 / 75.1 | -76.3 / -76.6 / -74.6 | -45.3 / -45.5 | -122.4 | none (listed 2024+) | - | 4.5 / 45 |
| c1 BNBR | 286.9 / 297.7 / 271.0 | -278.8 / -289.6 / -262.9 | -230.0 / -240.7 | -351.7 | 63 / -16.8 / -0.23 | -262.5 / -385.3 | 72.1 / 49 |
| c2 HRTA | 94.7 / 95.9 / 92.3 | -103.4 / -104.6 / -101.1 | 70.0 / 68.9 | -42.2 | 216 / +5.4 / 0.10 | -89.8 / -169.5 | 125.9 / 36 |
| c3 INCO | 69.9 / 70.7 / 69.5 | -46.2 / -47.0 / -45.8 | -33.5 / -34.3 | -86.7 | 472 / +16.5 / 0.69 | -45.6 / -74.4 | -56.9 / 54 |

### Tradability and run cost of a 1-month, 1-lot test (Rp)

Fee RT = lot x 0.40%; spread = 1 tick x 100 shares; impact at 1 lot is under 1 bp and rounds to zero. Trades/month is the sequential-chain entry rate over 2024-01..2026-10. "State" is the chain state at the 10-14 close (emitter rule: long-only, sequential, entry `close_{s+1}`). Worst trade / worst month = the backtest's worst 1-lot trade and calendar month, re-based to today's lot notional; stress cap = the whole lot plus buy fee (a suspended or limit-down name; the ARB band was not verified offline, so the cap, not a band, is the bound).

| # | cell | last close | lot notional | tick | fee RT | spread | cost/trip | trades/mo | **cost/month** | worst trade | worst month (bt) | stress cap | state at 10-14 close | P(entry <= 10 sessions) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 1 | WIFI h5 | 1,370 | 137,000 | 5 | 548 | 500 | 1,048 | 2.8 | **2,886** | -24,062 | -33,753 | 137,206 | in position, exit in 4 sessions | 0.97 |
| 2 | PTRO h5 | 4,120 | 412,000 | 10 | 1,648 | 1,000 | 2,648 | 2.8 | 7,291 | -108,255 | -110,148 | 412,618 | in position, exit in 4 | 0.99 |
| 3 | DEWA h5 | 330 | 33,000 | 2 | 132 | 200 | 332 | 2.7 | 904 | -7,765 | -11,614 | 33,050 | in position, exit in 2 | 1.00 |
| 4 | RAJA h5 | 595 | 59,500 | 5 | 238 | 500 | 738 | 2.7 | 1,966 | -14,323 | -30,172 | 59,589 | in position, exit in 4 | 1.00 |
| 5 | DSSA h2 | 895 | 89,500 | 5 | 358 | 500 | 858 | 4.5 | 3,826 | -25,439 | -33,396 | 89,634 | **signal: enter next session** | 0.98 |
| 6 | TAPG h5 | 2,080 | 208,000 | 10 | 832 | 1,000 | 1,832 | 2.8 | 5,209 | -25,936 | -30,600 | 208,312 | in position, exit in 4 | 1.00 |
| 7 | BRPT h2 | 1,430 | 143,000 | 5 | 572 | 500 | 1,072 | 5.3 | 5,647 | -27,586 | -55,515 | 143,214 | **signal: enter next session** | 1.00 |
| 8 | TINS h5 | 4,310 | 431,000 | 10 | 1,724 | 1,000 | 2,724 | 2.4 | 6,604 | -63,496 | -64,107 | 431,646 | flat | 0.91 |
| 9 | BIPI h5 | 115 | 11,500 | 1 | 46 | 100 | 146 | 2.5 | 371 | -2,594 | -4,135 | 11,517 | in position, exit in 2 | 0.97 |
| 10 | RATU h1 | 3,620 | 362,000 | 10 | 1,448 | 1,000 | 2,448 | 3.5 | 8,499 | -56,408 | -134,490 | 362,543 | flat | 1.00 |

Two lots double every rupiah figure here. The expected cost of a month (Rp 0.4-8.5k) is trivial next to the 14.9M account; the money at risk is the lot, and the question is whether there is any edge to pay for it. There is not enough evidence of one.

## 4. Why each pattern fails

* **Momentum h5 on WIFI / PTRO / DEWA / RAJA / TAPG / BIPI is trend beta.** The null (buy any day, hold 5) earns 89-254 bps in these names, 37-70% of the strategy's gross. The edge over null is 90-165 bps at t 0.55-1.12. The same family pooled over all 199 names has **+10.7 bps edge, t = 0.36, 57% of names positive** (§5): the six names are the right tail of a zero-mean distribution. Expected number of the 2,985 single-name cells with t >= 1.44 by chance alone: ~223. The best of our 13 has t = 1.44.
* **DSSA's 2015-2023 "OOS pass" is a lottery.** 49 trades, edge +424 bps, t 2.54, but the trades are Rp 42-274 penny entries in a name that ran to the thousands; the five best trades carry it (edge-net -38.7 without them), and entries at Rp 42-82 in a thin name are not realistically fillable at size. It is excluded by G5's drop-5 clause.
* **volume_breakout (BRPT, DSSA)** is a family with +1.9 bps edge at h2, t = 0.15. Two of 199 names looking good is what the family-level result predicts.
* **Cost dominates short holds.** Mean cost is 96-98 bps across the universe (fee 40 + tick spread 40-150 depending on the price). A hold-2 gross of +10 bps cannot pay it. The fee is fixed; only a higher-priced, finer-tick name lowers it (PTRO ~80 bps, BRPT/DSSA ~83-90 bps).
* **Impact fit is irrelevant to the verdict.** At the harness's Rp 3M notional the cand fit changes cost by -0.2 to +2.0 bps for every survivor and control except BNBR (+10.8); at 1 lot impact is under 1 bp. The newer fit being 15-40% higher moves nothing; tick + fee (80-150 bps) is the whole cost.

## 5. Every family in the catalog (pooled across 199 names, long-only, next-close)

30 pre-specified hypotheses (6 families x 5 holds), 2024-01-01..2026-10-14, cost at 1 lot (mean 96-105 bps), Holm-corrected, with the independent 2015-2023 window as replication. **No family clears G2, G3 or G7 in either window.**

| family | hold | trades | gross | null | edge | cluster t | Holm p | cost 1-lot | edge-net 1-lot | 2015-23 edge / t | 2015-23 edge-net | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| daily_momentum | 1 | 36,928 | 4.6 | 5.7 | -1.1 | -0.14 | 1.00 | 95.8 | -96.9 | -2.0 / -0.72 | -105.1 | no edge |
| daily_momentum | 2 | 28,720 | 9.7 | 11.2 | -1.5 | -0.11 | 1.00 | 96.1 | -97.6 | -1.6 / -0.30 | -105.4 | no edge |
| daily_momentum | 5 | 17,002 | 40.1 | 29.4 | 10.7 | 0.36 | 1.00 | 96.8 | -86.0 | -0.2 / -0.02 | -105.3 | no edge |
| daily_momentum | 20 | 5,566 | 149.0 | 134.7 | 14.2 | 0.15 | 1.00 | 97.5 | -83.3 | -10.2 / -0.23 | -117.3 | no edge |
| daily_momentum | 60 | 1,928 | 527.2 | 516.5 | 10.6 | 0.04 | 1.00 | 98.1 | -87.5 | +46.7 / 0.37 | -61.6 | no edge (null is +517 bps: bull-market drift) |
| mean_reversion | 1 | 37,592 | 5.0 | 4.9 | 0.1 | 0.01 | 1.00 | 98.2 | -98.1 | +0.4 / 0.13 | -107.6 | no edge |
| mean_reversion | 2 | 27,614 | -4.1 | 9.8 | -13.9 | -1.02 | 1.00 | 98.2 | -112.2 | -0.9 / -0.15 | -109.0 | negative |
| mean_reversion | 5 | 16,232 | 9.9 | 26.2 | -16.3 | -0.52 | 1.00 | 98.0 | -114.3 | -4.5 / -0.36 | -112.5 | negative |
| mean_reversion | 20 | 5,465 | 106.5 | 130.4 | -23.9 | -0.25 | 1.00 | 98.1 | -121.9 | -7.7 / -0.16 | -116.4 | negative |
| mean_reversion | 60 | 1,923 | 478.3 | 505.5 | -27.1 | -0.10 | 1.00 | 98.5 | -125.6 | -15.1 / -0.11 | -124.5 | negative |
| volume_breakout | 1 | 41,542 | 5.5 | 5.4 | 0.0 | 0.01 | 1.00 | 96.6 | -96.5 | -0.5 / -0.16 | -104.4 | no edge |
| volume_breakout | 2 | 31,056 | 12.6 | 10.7 | 1.9 | 0.15 | 1.00 | 96.7 | -94.8 | -1.6 / -0.30 | -106.1 | no edge |
| volume_breakout | 5 | 17,678 | 25.7 | 28.3 | -2.6 | -0.08 | 1.00 | 97.1 | -99.7 | -3.0 / -0.23 | -108.6 | no edge |
| volume_breakout | 20 | 5,591 | 127.6 | 136.4 | -8.8 | -0.09 | 1.00 | 97.8 | -106.6 | -1.0 / -0.02 | -108.6 | no edge |
| volume_breakout | 60 | 1,927 | 510.8 | 518.2 | -7.4 | -0.02 | 1.00 | 98.6 | -105.9 | -22.9 / -0.19 | -131.9 | no edge |
| gap_event | 1 | 4,269 | 21.3 | 13.2 | 8.2 | 0.31 | 1.00 | 105.0 | -96.8 | **+15.1 / 2.67** | -122.8 | edge replicates in sign; ~8% of cost |
| gap_event | 2 | 3,948 | 42.8 | 25.4 | 17.4 | 0.54 | 1.00 | 104.0 | -86.6 | +15.0 / 1.78 | -120.1 | same |
| gap_event | 5 | 3,222 | 83.4 | 62.0 | 21.3 | 0.37 | 1.00 | 103.1 | -81.7 | +39.2 / 2.03 | -91.8 | same |
| gap_event | 20 | 1,971 | 286.1 | 220.8 | 65.2 | 0.42 | 1.00 | 101.3 | -36.1 | +43.6 / 0.74 | -80.1 | same, cost still above edge |
| gap_event | 60 | 1,032 | 846.6 | 696.8 | 149.8 | 0.51 | 1.00 | 100.9 | **+48.9** | +122.9 / 0.87 | +4.5 | only positive edge-net; t 0.5, drop-5 -161.6 |
| volume_spike_event | 1 | 5,641 | 8.7 | 6.9 | 1.8 | 0.17 | 1.00 | 97.8 | -95.9 | +0.5 / 0.09 | -105.6 | no edge |
| volume_spike_event | 2 | 5,236 | 32.1 | 13.4 | 18.8 | 1.23 | 1.00 | 97.7 | -78.9 | +9.3 / 1.27 | -97.1 | edge < cost |
| volume_spike_event | 5 | 4,466 | 63.3 | 34.3 | 29.0 | 0.86 | 1.00 | 98.0 | -68.9 | +12.6 / 0.73 | -93.8 | edge < cost |
| volume_spike_event | 20 | 2,956 | 181.1 | 151.8 | 29.3 | 0.28 | 1.00 | 98.3 | -68.9 | +50.5 / 0.96 | -56.8 | edge < cost |
| volume_spike_event | 60 | 1,414 | 483.5 | 540.5 | -57.0 | -0.23 | 1.00 | 98.6 | -155.5 | +69.4 / 0.57 | -39.2 | no edge |
| foreign_flow_spike_event | 1 | 7,093 | 14.9 | 6.4 | 8.5 | 1.12 | 1.00 | 96.3 | -87.8 | +3.1 / 0.78 | -98.0 | edge < cost |
| foreign_flow_spike_event | 2 | 6,446 | 38.0 | 12.7 | 25.3 | 1.93 | 0.78 | 96.2 | -70.9 | +1.1 / 0.18 | -100.0 | not replicated |
| foreign_flow_spike_event | 5 | 5,297 | 91.4 | 32.8 | 58.6 | 1.97 | 0.74 | 96.2 | -37.6 | -4.6 / -0.31 | -106.3 | best 2024+ pulse; not replicated |
| foreign_flow_spike_event | 20 | 3,230 | 176.9 | 144.8 | 32.1 | 0.31 | 1.00 | 96.7 | -64.6 | -2.3 / -0.05 | -105.9 | no edge |
| foreign_flow_spike_event | 60 | 1,489 | 592.8 | 520.0 | 72.8 | 0.30 | 1.00 | 97.1 | -24.3 | +8.9 / 0.07 | -97.0 | no edge |

Notes: (a) "cluster t" clusters per-trade excess over the name's own null by entry month, so cross-name correlation is accounted for; (b) the 2015-23 rows keep only trades with volume > 0 on both the entry and exit bars; (c) gap_event is the only family whose edge over null is positive in **both** windows at all five holds (10 of 10), but at h1-h20 it is 8-65 bps against a 100-138 bps cost, and at h60 it is t 0.5-0.9 and not robust to its top 5 trades. It is the best lead for a future pre-registered test, not a candidate now; (d) foreign_flow h5 is the only 2024+ pulse (edge +58.6, t 1.97, Holm 0.74) and it does not replicate in 2015-23.

### Families not scored here

* **Cross-sectional (long-only top quintile):** already scored in plan 10 (2026-10-14): no edge over the equal-weight null at h1-h5 (t -1.3 .. -0.2), h20 edge over null -72 bps, h60 insufficient N. Not re-run.
* **Pairs / cointegration:** `long_short_infeasible` (no short); best pair gross -0.3 bps at h2.
* **Intraday multi-leg (ORB, intraday momentum, intraday mean reversion, OBI/OFI tick):** **"later"**, not picked. Blocked until per-leg order ids exist (brief, bar 5); not re-scored here. The plan-10 record has the ORB OOS failure.
* **Longer-horizon tilts (12-1 monthly momentum, multi-year carry):** not implemented in the harness; the h20 / h60 cells above are the catalogue's version and show nothing. A genuine 12-1 test needs its own pre-registration.

## 6. What paper can and cannot prove

Single-name daily cells cannot be validated forward in any useful time. Per-trade sd is 1,324 bps (PTRO) and 1,451 bps (WIFI). To detect an edge of 130 / 109 bps at 80% power (one-sided 5%) takes about **640 / 1,100 completed trades**; at 2.8 trades/month that is 19 / 33 years. So:

* **Paper's job** is (1) plumbing: the emitter output equals an independent recompute for 10 consecutive days including one full entry -> exit cycle (plan 20 §7), and (2) a futility screen.
* **Result that would justify going live (still not "proven"):** after >= 15 completed paper round trips per cell, *all* of: mean net P&L (schedule fee + 1 tick) > 0; mean minus the always-long null of the same window > 0; mean net still > 0 after dropping the 2 best trips; and the plumbing check passed. Missing any one -> stop. Passing all of them is necessary, not sufficient: at n = 15 the standard error is ~340-375 bps, so it filters out gross failures, not noise. A live 1-lot run after that is a **cost/execution measurement** (as plan 20 frames BNBR), with a hard loss halt at the lot's value; it should not be read as an efficacy test.
* **Honest expectation:** the most likely paper outcome is "inconclusive", and the base rate says a cell chosen as the right tail of 2,985 reverts toward zero edge.

## 7. Executable now

* The live bot runs only the `rebalance` strategy (one order per symbol per day, daily `target_portfolio.json`). Both paper cells are `daily_momentum`, whose signal needs only `close`, so they fit the existing emitter (`strategy_fit/target_emitter.py`) by adding an `EmitterSpec` to `SPECS` (today only `bnbr_momo_h2` is registered: `symbol`, `family='daily_momentum'`, `hold=5`, `signal_fn=daily_momentum_signal`). **I did not make that change.** The emitter's one-lot sizing (`base_notional = ceil(1.3 * close * 100)`) works for them.
* volume_breakout cells (DSSA, BRPT) would need the emitter to carry `open`/`volume` bars (`normalise_bars` keeps only date and close): a code change, and they are not the paper pick.
* Not verified (offline): that the names are currently tradeable (no suspension / UMA / notation). Check the emitten classification `tradeable` flag before arming.
* Cash-only, no leverage, no financing: `capital_model = owned_cash`, no horizon ceiling (ADR-0015), so h5 holds are allowed. T+2 settlement does not bind at 1-2 lots.

## 8. Multiple testing and what was not done

* **Looked at:** 13 name-level cells (the 10 the 2026-10-14 re-score flagged + 3 controls), each from a universe grid of **2,985 single-name cells** (199 names x 3 families x 5 holds; the re-score covered 115 names); and **30 pooled hypotheses** (6 families x 5 holds) on 2024+, plus the same 30 on 2015-23 as replication only. No new grid was mined for winners.
* **Correction:** the pooled tests use Holm over 30 (smallest adjusted p 0.74). Name-level t's are reported with the expected count of the 2,985 cells that reach the same t by chance (WIFI 873, PTRO 697, DSSA 223; see JSON `expected_cells_ge_t_by_chance`). No name-level cell survives any correction.
* **Survivorship:** the 199 names are currently listed; always-long nulls are therefore inflated (h60 null +517 bps) and edge-over-null is the number that matters.
* **Selection:** the 2015-23 window is independent of the selection (the grid was run on 2024+), but the cells were not chosen blind to everything: the 2026-10-14 re-score is the selection step.
* **Data hygiene (not fixed, out of scope):** the local parquet carries a 2026-10-15 in-progress bar for all 201 names with volume 0 and close equal to the prior close. `LocalParquetDataSource` does not drop it, so a grid run today would count a flat session. The emitter is unaffected (it uses bars strictly before the as-of day). Three results/d1 cells (BIPI, DSSA, TAPG) differ from the comparison.md numbers by 6-30 bps gross; the harness's own `run_single_name_family` on the current bars reproduces this note's figures exactly (BIPI 240.9, TAPG 210.0, DSSA 152.0, PTRO 375.2), so the bars were refreshed after that run (not verified).

## 9. Reproduce

```bash
cd research
python3 -m pytest -q strategy_fit/test_shortlist_2026_10.py        # 5 tests, synthetic data
python3 -m strategy_fit.shortlist_2026_10 --out-dir results/shortlist   # ~2.5 min; writes shortlist.json + shortlist.md
python3 -m strategy_fit.shortlist_2026_10 --out-dir results/shortlist --skip-pooled   # name-level rows only, ~15 s
```

Local data only (`research/data/daily/*.parquet`, no network, no credentials). `--as-of` pins the last complete bar (default: detected as 2026-10-14).
