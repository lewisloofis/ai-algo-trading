## Previously surviving cells (old PASS-eligible) -> new

| cell | old gross | old net | old edge/null | old eff_n | legacy-on-new-data gross | NEW gross | NEW net | NEW edge/null | NEW eff_n | NEW status |
|---|---|---|---|---|---|---|---|---|---|---|
| BNBR daily_momentum h2 | 324.0 | 140.5 | 267.8 | 84 | 295.8 | 57.0 | -158.8 | 8.1 | 74 | cost-fail |
| HRTA volume_breakout h5 | 341.3 | 254.8 | 156.3 | 63 | 328.3 | 164.8 | 72.4 | -8.7 | 90 | cost-pass, gated |
| INCO mean_reversion h2 | 87.9 | 25.4 | 70.4 | 156 | 77.5 | 36.5 | -29.1 | 23.7 | 142 | cost-fail |

## Every PASS-eligible cell in the NEW run

- BIPI daily_momentum h5: gross 270.6 net 158.5 eff_n 75 null 108.7 edge/null 161.9 oos_edge 444.0 oos_eff_n 31 p 0.0525 fdr True; mean net -166.2 excl top-5 (all 183.7, n=75); t(edge/null)=0.81
- BRPT volume_breakout h2: gross 121.5 net 45.7 eff_n 168 null 30.2 edge/null 91.3 oos_edge 31.2 oos_eff_n 64 p 0.0170 fdr True; mean net -32.6 excl top-5 (all 51.5, n=168); t(edge/null)=1.57
- DEWA daily_momentum h5: gross 319.0 net 216.6 eff_n 89 null 185.2 edge/null 133.7 oos_edge 323.0 oos_eff_n 37 p 0.0035 fdr True; mean net 46.4 excl top-5 (all 223.7, n=89); t(edge/null)=1.07
- DSSA volume_breakout h2: gross 157.8 net 70.7 eff_n 134 null 54.4 edge/null 103.3 oos_edge 73.8 oos_eff_n 58 p 0.0015 fdr True; mean net 2.8 excl top-5 (all 83.6, n=134); t(edge/null)=1.57
- PTRO daily_momentum h5: gross 375.2 net 309.5 eff_n 92 null 245.2 edge/null 130.0 oos_edge 220.9 oos_eff_n 37 p 0.0030 fdr True; mean net 101.9 excl top-5 (all 309.5, n=92); t(edge/null)=0.94
- RAJA daily_momentum h5: gross 293.5 net 167.5 eff_n 87 null 128.8 edge/null 164.8 oos_edge 175.3 oos_eff_n 36 p 0.0175 fdr True; mean net -68.8 excl top-5 (all 174.3, n=87); t(edge/null)=1.06
- RATU daily_momentum h1: gross 104.2 net 33.3 eff_n 113 null 31.0 edge/null 73.2 oos_edge 4.5 oos_eff_n 45 p 0.0440 fdr True; mean net -56.9 excl top-5 (all 43.4, n=113); t(edge/null)=1.05
- TAPG daily_momentum h5: gross 227.9 net 141.6 eff_n 89 null 134.2 edge/null 93.7 oos_edge 225.1 oos_eff_n 34 p 0.0000 fdr True; mean net 70.3 excl top-5 (all 146.8, n=89); t(edge/null)=1.47
- TINS mean_reversion h5: gross 277.4 net 211.4 eff_n 78 null 197.0 edge/null 80.4 oos_edge 448.8 oos_eff_n 30 p 0.0000 fdr True; mean net 39.9 excl top-5 (all 222.1, n=78); t(edge/null)=0.71
- WIFI daily_momentum h5: gross 363.2 net 284.2 eff_n 90 null 254.0 edge/null 109.2 oos_edge 30.3 oos_eff_n 35 p 0.0035 fdr True; mean net 26.2 excl top-5 (all 292.3, n=90); t(edge/null)=0.71

## State tallies (single-name + event cells)

old:    {'SURVIVES': 3, 'cost-fail': 918, 'cost-pass, gated': 105, 'decayed': 31, 'insufficient_n': 683}
legacy: {'SURVIVES': 4, 'cost-fail': 934, 'cost-pass, gated': 96, 'decayed': 23, 'insufficient_n': 683}
new:    {'SURVIVES': 10, 'cost-fail': 928, 'cost-pass, gated': 99, 'decayed': 29, 'insufficient_n': 674}
