## Cells whose verdict changed

| cell | old impact | new impact (mean) | new impact (median) | old net | new net | old verdict | new verdict |
|---|---|---|---|---|---|---|---|
| AADI volume_breakout h5 | 1.2 | 1.0 | 1.0 | 10.4 | -3.5 | cost-pass, gated | cost-fail |
| CBDK daily_momentum h5 | 2.7 | 2.4 | 2.3 | 5.4 | -37.2 | cost-pass, gated | cost-fail |
| DSSA volume_breakout h5 | 1.6 | 11.5 | 1.3 | 8.2 | -1.7 | cost-pass, gated | cost-fail |
| ENRG mean_reversion h1 | 3.6 | 4.1 | 4.3 | 0.3 | -0.3 | cost-pass, gated | cost-fail |
| RATU daily_momentum h1 | 3.3 | 3.3 | 2.7 | 33.3 | -39.4 | PASS | cost-fail |
| RATU daily_momentum h2 | 3.3 | 3.3 | 2.6 | 6.2 | -87.8 | cost-pass, gated | cost-fail |

## Top 15 cells by new net (tested)

| cell | old impact | new impact (mean) | new impact (median) | old net | new net | old verdict | new verdict |
|---|---|---|---|---|---|---|---|
| WIFI volume_breakout h20 | 2.5 | 2.1 | 2.2 | 1047.1 | 1047.5 | cost-pass, gated | cost-pass, gated |
| RAJA volume_breakout h20 | 2.0 | 2.5 | 2.0 | 485.9 | 485.4 | cost-pass, gated | cost-pass, gated |
| HRTA daily_momentum h20 | 3.1 | 3.1 | 3.1 | 448.3 | 448.3 | cost-pass, gated | cost-pass, gated |
| ARCI daily_momentum h20 | 3.0 | 3.7 | 3.4 | 386.9 | 386.3 | cost-pass, gated | cost-pass, gated |
| PTRO daily_momentum h5 | 1.5 | 2.1 | 1.5 | 309.5 | 308.8 | PASS | PASS |
| BRPT volume_breakout h20 | 0.8 | 0.8 | 0.7 | 298.2 | 298.2 | cost-pass, gated | cost-pass, gated |
| WIFI daily_momentum h5 | 2.5 | 2.1 | 2.1 | 284.2 | 284.6 | PASS | PASS |
| BUVA daily_momentum h5 | 13.9 | 24.4 | 14.9 | 258.0 | 247.5 | cost-pass, gated | cost-pass, gated |
| DEWA daily_momentum h5 | 1.8 | 2.2 | 2.3 | 216.6 | 216.1 | PASS | PASS |
| TINS mean_reversion h5 | 2.8 | 3.1 | 2.6 | 211.4 | 211.1 | PASS | PASS |
| BNBR daily_momentum h5 | 19.5 | 16.7 | 13.2 | 173.9 | 176.7 | cost-pass, gated | cost-pass, gated |
| INET mean_reversion h5 | 5.5 | 5.9 | 4.5 | 175.4 | 175.1 | cost-pass, gated | cost-pass, gated |
| RAJA daily_momentum h5 | 2.0 | 2.5 | 2.1 | 167.5 | 167.0 | PASS | PASS |
| BIPI daily_momentum h5 | 3.7 | 3.0 | 2.5 | 158.5 | 159.1 | PASS | PASS |
| WIFI volume_breakout h5 | 2.5 | 2.1 | 2.2 | 157.7 | 158.1 | cost-pass, gated | cost-pass, gated |

## Named cells

| cell | old impact | new impact (mean) | new impact (median) | old net | new net | old verdict | new verdict |
|---|---|---|---|---|---|---|---|
| BNBR daily_momentum h2 | 19.5 | 16.9 | 12.2 | -158.8 | -156.1 | cost-fail | cost-fail |
| HRTA volume_breakout h5 | 3.1 | 3.1 | 3.0 | 72.4 | 72.4 | cost-pass, gated | cost-pass, gated |
| INCO mean_reversion h2 | 1.3 | 1.2 | 1.1 | -29.1 | -29.0 | cost-fail | cost-fail |

## Whole grid: impact charged per single-name cell (bps)

| basis | min | p10 | p25 | median | p75 | p90 | max | mean | cells |
|---|---|---|---|---|---|---|---|---|---|
| old (full-history) | 0.2 | 0.9 | 1.5 | 1.9 | 2.8 | 4.1 | 19.5 | 2.7 | 1725 |
| new (trailing PIT, mean/trade) | 0.2 | 0.9 | 1.4 | 2.0 | 2.6 | 4.2 | 26.4 | 2.6 | 1725 |

new/old impact ratio over 1725 cells: 0.6 | 0.8 | 0.9 | 1.0 | 1.0 | 1.1 | 8.1 | 1.0 | 1725
trades excluded for < 20 bars of history (summed over cells): 85

## Tallies

old: {'PASS': 10, 'cost-fail': 921, 'cost-pass, gated': 91, 'decayed': 29, 'insufficient_n': 674}
new: {'PASS': 9, 'cost-fail': 927, 'cost-pass, gated': 86, 'decayed': 29, 'insufficient_n': 674}

## New passers (PASS in new, not in old)

none
