## Previously surviving cells (old PASS-eligible) -> new

| cell | old gross | old net | old edge/null | old eff_n | legacy-on-new-data gross | NEW gross | NEW net | NEW edge/null | NEW eff_n | NEW status |
|---|---|---|---|---|---|---|---|---|---|---|
| BNBR daily_momentum h2 | 324.0 | 140.5 | 267.8 | 84 | - | 44.7 | -171.1 | -10.9 | 82 | cost-fail |
| HRTA volume_breakout h5 | 341.3 | 254.8 | 156.3 | 63 | - | 249.9 | 157.5 | 73.1 | 92 | cost-pass, gated |
| INCO mean_reversion h2 | 87.9 | 25.4 | 70.4 | 156 | - | 62.3 | -3.3 | 50.1 | 139 | cost-fail |

## Every PASS-eligible cell in the NEW run

- ADMR mean_reversion h5: gross 158.8 net 85.7 eff_n 82 null 42.2 edge/null 116.6 oos_edge 349.3 oos_eff_n 33 p 0.0130 fdr True; mean net 0.3 excl top-5 (all 93.4, n=82); t(edge/null)=1.53
- ADRO mean_reversion h5: gross 113.1 net 32.5 eff_n 82 null 22.8 edge/null 90.3 oos_edge 230.7 oos_eff_n 34 p 0.0215 fdr True; mean net -30.4 excl top-5 (all 33.9, n=82); t(edge/null)=1.56
- HRTA daily_momentum h5: gross 271.5 net 179.1 eff_n 82 null 178.4 edge/null 93.1 oos_edge 367.1 oos_eff_n 32 p 0.0000 fdr True; mean net 75.4 excl top-5 (all 185.7, n=82); t(edge/null)=1.09
- INET mean_reversion h2: gross 282.5 net 151.5 eff_n 123 null 88.2 edge/null 194.3 oos_edge 172.0 oos_eff_n 54 p 0.0000 fdr True; mean net 51.1 excl top-5 (all 199.7, n=123); t(edge/null)=1.77
- JPFA mean_reversion h5: gross 198.7 net 112.7 eff_n 79 null 75.6 edge/null 123.1 oos_edge 313.1 oos_eff_n 30 p 0.0020 fdr True; mean net 47.5 excl top-5 (all 122.7, n=79); t(edge/null)=1.69
- PTRO daily_momentum h5: gross 330.9 net 265.2 eff_n 91 null 249.6 edge/null 81.4 oos_edge 133.8 oos_eff_n 36 p 0.0050 fdr True; mean net 91.5 excl top-5 (all 268.9, n=91); t(edge/null)=0.60
- TINS mean_reversion h5: gross 279.5 net 213.5 eff_n 80 null 201.0 edge/null 78.5 oos_edge 460.3 oos_eff_n 30 p 0.0020 fdr True; mean net 23.5 excl top-5 (all 217.0, n=80); t(edge/null)=0.67

## State tallies (single-name + event cells)

old:    {'SURVIVES': 3, 'cost-fail': 918, 'cost-pass, gated': 105, 'decayed': 31, 'insufficient_n': 683}
new:    {'SURVIVES': 7, 'cost-fail': 931, 'cost-pass, gated': 107, 'decayed': 22, 'insufficient_n': 673}
