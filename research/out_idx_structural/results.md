# IDX structural search — results (m_tests=24)

## Regime
```
{
 "asof": "2026-10-14",
 "ew_level": 4111.112087701404,
 "ew_ma200": 4625.454167594126,
 "ew_drawdown_from_52w_high": -0.25264943441529997,
 "ew_below_ma200": true,
 "ew_deep_dd": true,
 "bear_now": true,
 "bear_since": "2026-05-18",
 "share_days_bear": 0.30661619486504277,
 "n_days_bear": 1863,
 "n_days_nonbear": 4213,
 "xcheck_adv_weighted_top30": {
  "level": 3034.4162782792546,
  "ma200": 3800.871049375834,
  "drawdown": -0.4314997934659045,
  "below_ma200": true,
  "deep_dd": true
 },
 "ew_ret_1m": -0.08334762875881985,
 "ew_ret_3m": -0.004479018375715738,
 "ew_ret_6m": -0.15471781394244255,
 "ew_ret_12m": -0.10834143921651374
}
```

## Universe
```
{
 "n_symbols": 241,
 "panel_days": 6275,
 "first": "2000-11-01",
 "last": "2026-10-14",
 "n_ended_before_2026_10": 39
}
```

## Results (bps; net_x = net excess over always-long name null; abs = absolute net)

| family | variant | n | clusters | gross abs | cost | net_x name | net_x mkt | abs net | t | p_bonf | IS | OOS | ex-top5 | 10x | bear n / net_x / abs | nonbear n / net_x / abs | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| factor | low_vol_h21 | 251 | 251 | 157.7 | 27.6 | -21.9 | -21.9 | 130.1 | -0.92 | 1.000 | 0.2 | -55.2 | -45.0 | -25.8 | 72 / -35.5 / 240.7 | 179 / -16.4 / 85.7 | **FAIL** |
| factor | low_vol_h63 | 83 | 83 | 444.1 | 48.9 | -108.7 | -108.7 | 395.2 | -1.01 | 1.000 | -68.9 | -169.1 | -215.6 | -116.0 | 22 / -407.7 / 737.6 | 61 / -0.9 / 271.7 | **FAIL** |
| factor | mom_12_1_h21 | 251 | 251 | 224.5 | 25.7 | 47.2 | 47.2 | 198.8 | 1.46 | 1.000 | 42.4 | 54.5 | 13.0 | 41.9 | 72 / -118.2 / 156.6 | 179 / 113.8 / 215.7 | **FAIL** |
| factor | mom_12_1_h63 | 83 | 83 | 767.6 | 45.2 | 220.7 | 220.7 | 722.4 | 1.59 | 1.000 | 211.1 | 235.3 | 19.7 | 210.9 | 22 / -384.0 / 753.4 | 61 / 438.8 / 711.2 | **FAIL** |
| factor | high52w_h21 | 251 | 251 | 204.1 | 41.8 | 10.3 | 10.3 | 162.3 | 0.35 | 1.000 | -7.0 | 36.5 | -15.3 | 3.6 | 72 / -103.2 / 173.9 | 179 / 56.0 / 157.6 | **FAIL** |
| factor | high52w_h63 | 83 | 83 | 729.4 | 56.4 | 171.1 | 171.1 | 673.0 | 1.45 | 1.000 | 84.4 | 302.5 | 35.3 | 161.5 | 22 / -356.4 / 782.2 | 61 / 361.4 / 633.6 | **FAIL** |
| factor | foreign_accum_h21 | 251 | 251 | 143.0 | 36.8 | -45.7 | -45.7 | 106.3 | -2.17 | 0.751 | -38.7 | -56.4 | -67.7 | -52.1 | 72 / -48.6 / 227.5 | 179 / -44.6 / 57.5 | **FAIL** |
| factor | foreign_accum_h63 | 83 | 83 | 665.7 | 70.3 | 91.5 | 91.5 | 595.4 | 0.88 | 1.000 | 190.8 | -58.9 | -66.9 | 79.4 | 22 / -70.3 / 1075.0 | 61 / 149.8 / 422.5 | **FAIL** |
| factor | rev_1m_h21 | 251 | 251 | 126.0 | 78.5 | -104.5 | -104.5 | 47.4 | -3.86 | 0.003 | -121.4 | -79.1 | -132.6 | -118.8 | 72 / 16.5 / 292.6 | 179 / -153.2 / -51.2 | **FAIL** |
| factor | rev_1m_h63 | 83 | 83 | 434.2 | 86.0 | -155.8 | -155.8 | 348.2 | -1.78 | 1.000 | -259.0 | 0.6 | -269.4 | -171.6 | 22 / 70.5 / 1215.8 | 61 / -237.4 / 35.2 | **FAIL** |
| factor | div_yield_h21 | 251 | 251 | 182.4 | 15.8 | 14.6 | 14.6 | 166.6 | 0.62 | 1.000 | 19.0 | 8.0 | -10.8 | 11.9 | 72 / 81.4 / 357.6 | 179 / -12.2 / 89.8 | **FAIL** |
| factor | div_yield_h63 | 83 | 83 | 505.6 | 25.6 | -24.0 | -24.0 | 479.9 | -0.31 | 1.000 | 5.9 | -69.4 | -110.8 | -28.6 | 22 / 5.8 / 1151.1 | 61 / -34.8 / 237.9 | **FAIL** |
| factor | overnight_persist_h21 | 251 | 251 | 147.8 | 37.3 | -41.5 | -41.5 | 110.5 | -1.48 | 1.000 | -38.8 | -45.6 | -69.1 | -48.9 | 72 / 7.7 / 283.8 | 179 / -61.3 / 40.8 | **FAIL** |
| factor | overnight_persist_h63 | 83 | 83 | 516.1 | 71.8 | -59.6 | -59.6 | 444.3 | -0.66 | 1.000 | -111.5 | 19.0 | -168.4 | -73.6 | 22 / 132.0 / 1277.4 | 61 / -128.8 / 143.8 | **FAIL** |
| limit_event | ARA_1d_next_close | 383 | 306 | 12.1 | 113.1 | -132.1 | -98.5 | -101.0 | -3.36 | 0.021 | -160.0 | -89.9 | -172.7 | -171.0 | 167 / -124.6 / -108.6 | 216 / -136.4 / -96.6 | **FAIL** |
| limit_event | ARA_1d_next_open | 383 | 306 | 41.4 | 113.1 | -105.6 | -61.0 | -71.7 | -1.66 | 1.000 | -146.1 | -44.4 | -169.1 | -144.5 | 167 / -48.9 / -31.4 | 216 / -138.3 / -95.0 | **FAIL** |
| limit_event | ARA_5d_next_close | 330 | 272 | 305.6 | 112.4 | 34.7 | 121.2 | 193.2 | 0.35 | 1.000 | 82.9 | -37.3 | -83.4 | -1.9 | 136 / 58.3 / 150.3 | 194 / 22.2 / 215.8 | **FAIL** |
| limit_event | ARA_5d_next_open | 330 | 272 | 313.3 | 112.4 | 39.3 | 148.8 | 200.9 | 0.32 | 1.000 | 91.0 | -37.9 | -121.4 | 2.7 | 136 / 40.6 / 135.0 | 194 / 38.7 / 235.7 | **FAIL** |
| limit_event | ARB_1d_next_close | 273 | 143 | -189.5 | 111.9 | -307.0 | -306.3 | -301.4 | -3.65 | 0.009 | -258.5 | -380.3 | -402.1 | -340.1 | 137 / -208.6 / -211.5 | 136 / -391.4 / -378.5 | **FAIL** |
| limit_event | ARB_1d_next_open | 273 | 143 | -16.3 | 111.9 | -136.3 | -186.3 | -128.2 | -1.21 | 1.000 | -50.8 | -265.4 | -249.1 | -169.4 | 137 / 23.4 / 23.4 | 136 / -273.3 / -258.1 | **FAIL** |
| limit_event | ARB_5d_next_close | 218 | 117 | -298.6 | 109.9 | -480.6 | -408.3 | -408.5 | -2.81 | 0.138 | -417.8 | -574.1 | -681.9 | -513.0 | 115 / -51.5 / -27.8 | 103 / -823.9 / -713.1 | **FAIL** |
| limit_event | ARB_5d_next_open | 218 | 117 | -5.4 | 109.9 | -187.2 | -185.7 | -115.2 | -0.93 | 1.000 | 50.0 | -540.4 | -399.5 | -219.5 | 115 / 349.3 / 373.8 | 103 / -616.3 / -506.5 | **FAIL** |
| dividend_event | div_runup | 842 | 607 | 109.7 | 91.3 | -17.0 | -20.8 | 18.4 | -0.76 | 1.000 | 7.2 | -53.2 | -36.9 | -32.4 | 321 / 18.2 / 51.4 | 521 / -35.6 / 0.9 | **FAIL** |
| dividend_event | div_runup_allyield | 1626 | 1042 | 42.3 | 90.5 | -87.0 | -70.8 | -48.2 | -5.02 | 0.000 | -84.0 | -91.5 | -100.0 | -101.8 | 572 / -40.4 / -8.4 | 1054 / -109.9 / -67.7 | **FAIL** |
| dividend_event | div_cum_capture | 847 | 607 | 80.0 | 90.8 | -24.8 | -27.1 | -10.8 | -1.35 | 1.000 | -16.8 | -36.7 | -50.0 | -39.9 | 335 / 35.8 / 47.3 | 512 / -58.7 / -43.4 | **FAIL** |
| dividend_event | div_cum_capture_allyield | 1631 | 1046 | 66.3 | 90.4 | -39.7 | -39.1 | -24.1 | -3.17 | 0.037 | -21.3 | -67.3 | -53.3 | -54.4 | 600 / 35.2 / 46.3 | 1031 / -78.1 / -60.3 | **FAIL** |
| dividend_event | div_post_ex | 852 | 612 | -36.2 | 91.5 | -162.5 | -148.0 | -127.8 | -6.09 | 0.000 | -164.7 | -159.2 | -193.6 | -177.6 | 338 / -145.7 / -112.8 | 514 / -171.8 / -136.1 | **FAIL** |
| dividend_event | div_post_ex_allyield | 1630 | 1043 | -4.2 | 90.7 | -134.1 | -124.7 | -94.9 | -6.90 | 0.000 | -124.9 | -147.9 | -153.2 | -148.8 | 602 / -78.0 / -47.9 | 1028 / -162.7 / -118.9 | **FAIL** |
| calendar | turn_of_month | 296 | 296 | 48.7 | 86.8 | -59.9 | -59.9 | -38.2 | -2.89 | 0.100 | -55.2 | -67.0 | -75.4 | -68.8 | 97 / -93.5 / -71.7 | 199 / -43.6 / -21.8 | **FAIL** |
| calendar | pre_lebaran | 26 | 26 | 182.3 | 88.7 | 21.0 | 21.0 | 93.5 | 0.18 | 1.000 | 131.9 | -156.4 | -176.2 | 11.3 | 9 / 94.1 / 166.6 | 17 / -17.7 / 54.8 | **FAIL** |

## Regime filter: bear_to_cash
```
{
 "n": 17,
 "n_clusters": 17,
 "gross_abs_bps": 0.0,
 "gross_excess_name_bps": -1063.8403458406149,
 "cost_bps": 88.66296536549333,
 "net_excess_name_bps": -1152.503311206108,
 "net_excess_mkt_bps": -1152.503311206108,
 "abs_net_bps": -88.66296536549333,
 "t_net_name": -4.6425090364167465,
 "p_raw": 0.0002710040580759497,
 "p_bonf": 0.0065040973938227924,
 "is_net_excess_bps": -1222.597897914171,
 "oos_net_excess_bps": -1052.3681873374467,
 "oos_n_clusters": 7,
 "ex_top5_net_excess_bps": -1664.0700070296455,
 "net_excess_name_10x_bps": -1161.2234123714645,
 "median_net_excess_name_bps": -1264.2284220152194,
 "regime": {},
 "bear_abs_net_bps": null,
 "verdict": "FAIL",
 "failed_gates": [
  "net_excess_vs_name_null>0",
  "net_excess_vs_market>0",
  "oos_net_excess>0",
  "oos_clusters>=30",
  "excess_ex_top5>0",
  "capacity_10x>0",
  "clusters>=30"
 ],
 "family": "regime_filter",
 "name": "bear_to_cash",
 "counted_in_m": true,
 "hold": null,
 "filter": {
  "cagr": 0.08315153195991054,
  "max_dd": -0.34307419609872025,
  "total": 5.86112918793397
 },
 "buy_hold": {
  "cagr": 0.16664414253003534,
  "max_dd": -0.6831305872330208,
  "total": 40.11112087701411
 },
 "n_flips": 35,
 "open_episode": {
  "open_episode_since": "2026-05-18",
  "open_episode_basket_return": -0.051245565317230596
 },
 "episodes": 17
}
```

## Regime filter: bear_to_cash_raw_flicker_v1
```
{
 "n": 50,
 "n_clusters": 50,
 "gross_abs_bps": 0.0,
 "gross_excess_name_bps": -128.290982924022,
 "cost_bps": 87.58608313694997,
 "net_excess_name_bps": -215.877066060972,
 "net_excess_mkt_bps": -215.877066060972,
 "abs_net_bps": -87.58608313694997,
 "t_net_name": -2.2066019927131513,
 "p_raw": 0.032058936346915014,
 "p_bonf": 0.7694144723259604,
 "is_net_excess_bps": -213.09549269212582,
 "oos_net_excess_bps": -220.04942611424121,
 "oos_n_clusters": 20,
 "ex_top5_net_excess_bps": -378.73863272881766,
 "net_excess_name_10x_bps": -224.4325377670613,
 "median_net_excess_name_bps": -296.779036867162,
 "regime": {},
 "bear_abs_net_bps": null,
 "verdict": "FAIL",
 "failed_gates": [
  "net_excess_vs_name_null>0",
  "net_excess_vs_market>0",
  "bonferroni_p<0.05",
  "oos_net_excess>0",
  "oos_clusters>=30",
  "excess_ex_top5>0",
  "capacity_10x>0"
 ],
 "family": "regime_filter",
 "name": "bear_to_cash_raw_flicker_v1",
 "counted_in_m": false,
 "hold": null,
 "filter": {
  "cagr": 0.12528782548987705,
  "max_dd": -0.45585336278683664,
  "total": 16.21959147860857
 },
 "buy_hold": {
  "cagr": 0.16664414253003534,
  "max_dd": -0.6831305872330208,
  "total": 40.11112087701411
 },
 "n_flips": 101,
 "open_episode": {
  "open_episode_since": "2026-05-18",
  "open_episode_basket_return": -0.051245565317230596
 },
 "episodes": 50
}
```

## Overnight vs intraday (EW eligible, bps/day)
```
{
 "all": {
  "n_days": 6023,
  "overnight_bps_per_day": 6.842933456710739,
  "intraday_bps_per_day": 3.568769257329195
 },
 "bear": {
  "n_days": 1863,
  "overnight_bps_per_day": -2.784858482216852,
  "intraday_bps_per_day": 9.240246106427351
 },
 "nonbear": {
  "n_days": 4160,
  "overnight_bps_per_day": 11.154610471667974,
  "intraday_bps_per_day": 1.0288746972643243
 },
 "2004-2012": {
  "n_days": 2188,
  "overnight_bps_per_day": -1.5076475122316246,
  "intraday_bps_per_day": 20.75468299416316
 },
 "2013-2019": {
  "n_days": 1699,
  "overnight_bps_per_day": 14.547093327109462,
  "intraday_bps_per_day": -11.319251664080168
 },
 "2020-2026": {
  "n_days": 1633,
  "overnight_bps_per_day": 21.559791748930582,
  "intraday_bps_per_day": -17.13310915766658
 }
}
```

## Bear episodes
```
[
 {
  "start": "2002-07-26",
  "end": "2003-08-06",
  "days": 249,
  "index_change": -0.0013560483263944656
 },
 {
  "start": "2004-05-17",
  "end": "2004-08-31",
  "days": 73,
  "index_change": 0.1256438533350137
 },
 {
  "start": "2005-08-24",
  "end": "2005-12-06",
  "days": 69,
  "index_change": 0.06440113800162206
 },
 {
  "start": "2007-08-16",
  "end": "2007-09-19",
  "days": 24,
  "index_change": 0.14924967444924842
 },
 {
  "start": "2008-01-22",
  "end": "2008-01-31",
  "days": 8,
  "index_change": 0.05072591807639415
 },
 {
  "start": "2008-03-14",
  "end": "2009-05-29",
  "days": 292,
  "index_change": 0.049749203741083825
 },
 {
  "start": "2010-05-21",
  "end": "2010-06-16",
  "days": 18,
  "index_change": 0.06368642378599332
 },
 {
  "start": "2011-09-22",
  "end": "2012-01-09",
  "days": 77,
  "index_change": 0.04983941476002962
 },
 {
  "start": "2012-06-04",
  "end": "2012-07-02",
  "days": 21,
  "index_change": 0.0765307728741067
 },
 {
  "start": "2013-06-24",
  "end": "2014-03-14",
  "days": 176,
  "index_change": 0.08261986353315742
 },
 {
  "start": "2015-06-09",
  "end": "2016-06-06",
  "days": 243,
  "index_change": -0.017072241585576964
 },
 {
  "start": "2018-06-28",
  "end": "2019-01-18",
  "days": 139,
  "index_change": 0.05926204237888277
 },
 {
  "start": "2019-05-16",
  "end": "2019-06-18",
  "days": 18,
  "index_change": 0.08345536585712598
 },
 {
  "start": "2020-01-31",
  "end": "2020-11-12",
  "days": 190,
  "index_change": 0.019120580617734584
 },
 {
  "start": "2023-03-16",
  "end": "2023-06-13",
  "days": 52,
  "index_change": 0.0718862255828292
 },
 {
  "start": "2025-02-10",
  "end": "2025-07-15",
  "days": 96,
  "index_change": 0.09391062272452055
 },
 {
  "start": "2026-03-13",
  "end": "2026-04-13",
  "days": 16,
  "index_change": 0.08228854544870301
 },
 {
  "start": "2026-05-18",
  "end": "2026-10-14",
  "days": 102,
  "index_change": -0.12221329424050664
 }
]
```

## Lebaran closure check
```
[
 {
  "eid": "2001-12-16",
  "last_open": "2001-12-13",
  "reopen": "2001-12-19",
  "closure_cal_days": 6
 },
 {
  "eid": "2002-12-06",
  "last_open": "2002-12-04",
  "reopen": "2002-12-11",
  "closure_cal_days": 7
 },
 {
  "eid": "2003-11-25",
  "last_open": "2003-11-21",
  "reopen": "2003-12-01",
  "closure_cal_days": 10
 },
 {
  "eid": "2004-11-14",
  "last_open": "2004-11-12",
  "reopen": "2004-11-22",
  "closure_cal_days": 10
 },
 {
  "eid": "2005-11-03",
  "last_open": "2005-11-01",
  "reopen": "2005-11-09",
  "closure_cal_days": 8
 },
 {
  "eid": "2006-10-24",
  "last_open": "2006-10-20",
  "reopen": "2006-10-30",
  "closure_cal_days": 10
 },
 {
  "eid": "2007-10-13",
  "last_open": "2007-10-11",
  "reopen": "2007-10-17",
  "closure_cal_days": 6
 },
 {
  "eid": "2008-10-01",
  "last_open": "2008-09-29",
  "reopen": "2008-10-06",
  "closure_cal_days": 7
 },
 {
  "eid": "2009-09-20",
  "last_open": "2009-09-17",
  "reopen": "2009-09-24",
  "closure_cal_days": 7
 },
 {
  "eid": "2010-09-10",
  "last_open": "2010-09-07",
  "reopen": "2010-09-15",
  "closure_cal_days": 8
 },
 {
  "eid": "2011-08-31",
  "last_open": "2011-08-26",
  "reopen": "2011-09-05",
  "closure_cal_days": 10
 },
 {
  "eid": "2012-08-19",
  "last_open": "2012-08-16",
  "reopen": "2012-08-23",
  "closure_cal_days": 7
 },
 {
  "eid": "2013-08-08",
  "last_open": "2013-08-02",
  "reopen": "2013-08-12",
  "closure_cal_days": 10
 },
 {
  "eid": "2014-07-28",
  "last_open": "2014-07-25",
  "reopen": "2014-08-04",
  "closure_cal_days": 10
 },
 {
  "eid": "2015-07-17",
  "last_open": "2015-07-15",
  "reopen": "2015-07-22",
  "closure_cal_days": 7
 },
 {
  "eid": "2016-07-06",
  "last_open": "2016-07-01",
  "reopen": "2016-07-11",
  "closure_cal_days": 10
 },
 {
  "eid": "2017-06-25",
  "last_open": "2017-06-22",
  "reopen": "2017-07-03",
  "closure_cal_days": 11
 },
 {
  "eid": "2018-06-15",
  "last_open": "2018-06-08",
  "reopen": "2018-06-20",
  "closure_cal_days": 12
 },
 {
  "eid": "2019-06-05",
  "last_open": "2019-05-31",
  "reopen": "2019-06-10",
  "closure_cal_days": 10
 },
 {
  "eid": "2020-05-24",
  "last_open": "2020-05-20",
  "reopen": "2020-05-26",
  "closure_cal_days": 6
 },
 {
  "eid": "2021-05-13",
  "last_open": "2021-05-11",
  "reopen": "2021-05-17",
  "closure_cal_days": 6
 },
 {
  "eid": "2022-05-02",
  "last_open": "2022-04-28",
  "reopen": "2022-05-09",
  "closure_cal_days": 11
 },
 {
  "eid": "2023-04-22",
  "last_open": "2023-04-18",
  "reopen": "2023-04-26",
  "closure_cal_days": 8
 },
 {
  "eid": "2024-04-10",
  "last_open": "2024-04-05",
  "reopen": "2024-04-16",
  "closure_cal_days": 11
 },
 {
  "eid": "2025-03-31",
  "last_open": "2025-03-27",
  "reopen": "2025-04-08",
  "closure_cal_days": 12
 },
 {
  "eid": "2026-03-20",
  "last_open": "2026-03-17",
  "reopen": "2026-03-25",
  "closure_cal_days": 8
 }
]
```