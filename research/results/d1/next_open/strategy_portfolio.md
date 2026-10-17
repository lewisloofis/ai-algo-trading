# Strategy Portfolio — Plan 10 Strategy Fit

> **LIVE DATA** — Generated from live chartbit daily history via LiveDataSource. Real market results.

**Generated:** 2026-10-14T13:09:35Z  
**Capital model:** `owned_cash` — ADR-0015 owned capital, no financing charge (0 bps/cal-day), no horizon ceiling. Not comparable with `leveraged_sleeve` results.

## Verdict: PASS

**7 cell(s)** survive the full cost gate (min_edge + modelled_impact + financing at best-feasible entry day), the Benjamini-Hochberg FDR correction at α=0.10, have their market impact genuinely priced (impact_priced=True), clear the OOS not-zero floor (MIN_OOS_EFF_N — W2.1; below-floor cells cannot reach PASS), and — where an always-long null is computable for their shape — beat that null by more than min_edge_hurdle_bps (the signal, not asset drift, pays for its own friction; see Methodology Notes). Whether a cell that clears that floor is also UNDERPOWERED relative to the power-derived required-N is reported per cell ("OOS Persistent" column) for human review — see Methodology Notes.

## Surviving Strategies

| Symbol | Strategy | Hold (days) | Shape | Hit Rate | Net Edge (bps) | Eff-N | Best Entry Day | Friction Hurdle (bps) | Impact (bps) | Null Edge (bps) | Edge over Null (bps) | OOS Edge (bps) | OOS Eff-N | OOS Persistent | Decay Metric (Sharpe) |
|--------|----------|-------------|-------|----------|---------------|-------|----------------|----------------------|-------------|-----------------|------------------------|----------------|-----------|----------------|----------------------|
| ADMR | mean_reversion | 5 | single_name_time_series | 0.561 | 85.7 | 82 | Mon | 73.1 | 1.8 | 42.2 | 116.6 | 349.3 | 33 | YES | -0.02 |
| ADRO | mean_reversion | 5 | single_name_time_series | 0.549 | 32.5 | 82 | Mon | 80.6 | 0.8 | 22.8 | 90.3 | 230.7 | 34 | YES | 0.03 |
| HRTA | daily_momentum | 5 | single_name_time_series | 0.585 | 179.1 | 82 | Mon | 92.4 | 3.1 | 178.4 | 93.1 | 367.1 | 32 | YES | 0.14 |
| INET | mean_reversion | 2 | single_name_time_series | 0.512 | 151.5 | 123 | Mon | 131.0 | 5.5 | 88.2 | 194.3 | 172.0 | 54 | YES ⚠ (UNDERPOWERED) | 0.11 |
| JPFA | mean_reversion | 5 | single_name_time_series | 0.595 | 112.7 | 79 | Mon | 86.0 | 1.5 | 75.6 | 123.1 | 313.1 | 30 | YES | 0.05 |
| PTRO | daily_momentum | 5 | single_name_time_series | 0.571 | 265.2 | 91 | Mon | 65.7 | 1.5 | 249.6 | 81.4 | 133.8 | 36 | YES ⚠ (UNDERPOWERED) | 0.33 |
| TINS | mean_reversion | 5 | single_name_time_series | 0.537 | 213.5 | 80 | Mon | 66.0 | 2.8 | 201.0 | 78.5 | 460.3 | 30 | YES | 0.19 |

## Coverage Table (All Cells)

Every tested, skipped, or blocked cell is listed here. No cell is silently omitted (plan-10 §7.8).

| Symbol | Strategy | Hold | Shape | Status | Reason / Unblock |
|--------|----------|------|-------|--------|------------------|
| (cross_sectional) | cross_sectional_momentum | 1 | cross_sectional | long_short_infeasible | Cross-sectional IC computed (eff_n=657, mean_IC=-0.0714, ICIR=-0.426, p=1.000) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (cross_sectional) | cross_sectional_momentum | 2 | cross_sectional | long_short_infeasible | Cross-sectional IC computed (eff_n=656, mean_IC=-0.0613, ICIR=-0.360, p=1.000) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (cross_sectional) | cross_sectional_momentum | 5 | cross_sectional | long_short_infeasible | Cross-sectional IC computed (eff_n=653, mean_IC=-0.0315, ICIR=-0.186, p=1.000) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (cross_sectional) | cross_sectional_momentum | 20 | cross_sectional | long_short_infeasible | Cross-sectional IC computed (eff_n=638, mean_IC=-0.0210, ICIR=-0.142, p=1.000) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (cross_sectional) | cross_sectional_momentum | 60 | cross_sectional | long_short_infeasible | Cross-sectional IC computed (eff_n=598, mean_IC=-0.0181, ICIR=-0.126, p=1.000) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (pairs) | pairs_cointegration | 1 | pairs | long_short_infeasible | Best pair ('BUMI', 'DEWA') passed cointegration (eff_n=659, hit_rate=0.000, gross_edge=-100.0bps) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (pairs) | pairs_cointegration | 2 | pairs | long_short_infeasible | Best pair ('BUMI', 'DEWA') passed cointegration (eff_n=329, hit_rate=0.498, gross_edge=-0.3bps) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (pairs) | pairs_cointegration | 5 | pairs | long_short_infeasible | Best pair ('BUMI', 'DEWA') passed cointegration (eff_n=131, hit_rate=0.519, gross_edge=3.8bps) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (pairs) | pairs_cointegration | 20 | pairs | long_short_infeasible | Best pair ('BUMI', 'DEWA') passed cointegration (eff_n=32, hit_rate=0.656, gross_edge=31.2bps) but IDX retail accounts cannot short-sell (see families_intraday.py run_orb_family, commit c62eba8). This family is structurally long/short and cannot be made long-only without changing what the strategy tests. |
| (pairs) | pairs_cointegration | 60 | pairs | insufficient_n | Best pair ('BUMI', 'DEWA') has eff_n=10 < MIN_PAIR_OBS=30 |
| (pooled) | foreign_flow_spike_event | 1 | event_driven | tested | cost_gated: net_after_cost=-58.5 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | foreign_flow_spike_event | 2 | event_driven | tested | cost_gated: net_after_cost=-55.5 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | foreign_flow_spike_event | 5 | event_driven | tested | cost_gated: net_after_cost=-3.7 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | foreign_flow_spike_event | 20 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| (pooled) | foreign_flow_spike_event | 60 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| (pooled) | gap_event | 1 | event_driven | tested | cost_gated: net_after_cost=-89.2 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | gap_event | 2 | event_driven | tested | cost_gated: net_after_cost=-38.2 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | gap_event | 5 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| (pooled) | gap_event | 20 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| (pooled) | gap_event | 60 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| (pooled) | volume_spike_event | 1 | event_driven | tested | cost_gated: net_after_cost=-98.6 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | volume_spike_event | 2 | event_driven | tested | cost_gated: net_after_cost=-88.1 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | volume_spike_event | 5 | event_driven | tested | cost_gated: net_after_cost=-51.0 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| (pooled) | volume_spike_event | 20 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| (pooled) | volume_spike_event | 60 | event_driven | tested | impact_not_priced (excluded from PASS pending plan-13 impact model for this shape) |
| AADI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-68.6 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-69.2 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-108.6 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=17 < MIN_EFF_N=30 |
| AADI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| AADI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-52.7 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-37.3 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-27.5 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=17 < MIN_EFF_N=30 |
| AADI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| AADI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-65.2 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-48.5 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-14.1 bps ≤ 0 (gross − hurdle, where hurdle=61.6 bps) |
| AADI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=19 < MIN_EFF_N=30 |
| AADI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| AALI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-88.1 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-108.4 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-71.7 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| AALI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AALI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-70.2 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-37.3 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-27.5 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AALI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AALI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-57.6 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-64.7 bps ≤ 0 (gross − hurdle, where hurdle=71.9 bps) |
| AALI | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=58.5 bps <= hurdle=71.9 bps (gross=78.9, null=20.4 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| AALI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| AALI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ABMM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-61.5 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-95.6 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-59.9 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ABMM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ABMM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-88.3 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-108.3 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-114.7 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ABMM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ABMM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-92.2 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-79.2 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-58.5 bps ≤ 0 (gross − hurdle, where hurdle=78.3 bps) |
| ABMM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ABMM | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.061, mean_hr=0.182 |
| ACES | daily_momentum | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.067, mean_hr=0.405 |
| ACES | daily_momentum | 2 | single_name_time_series | decayed | decayed: mean_sharpe=-0.124, mean_hr=0.418 |
| ACES | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-136.0 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ACES | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ACES | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-106.9 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-103.5 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-124.0 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| ACES | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ACES | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-97.0 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-80.2 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-102.1 bps ≤ 0 (gross − hurdle, where hurdle=98.6 bps) |
| ACES | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ACES | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| ADMR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-97.3 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-87.9 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.204 |
| ADMR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ADMR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ADMR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-43.7 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-6.9 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ADMR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ADMR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-92.2 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-45.5 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-88.8 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| ADMR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ADMR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ADRO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-78.7 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-118.2 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-138.5 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ADRO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ADRO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-49.4 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-46.1 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ADRO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ADRO | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-67.6 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-69.3 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-40.9 bps ≤ 0 (gross − hurdle, where hurdle=80.6 bps) |
| ADRO | volume_breakout | 20 | single_name_time_series | tested | fdr_rejected: p=0.319 |
| ADRO | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AKRA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-86.7 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-83.9 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-39.9 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AKRA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AKRA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-61.6 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-93.6 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-62.2 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| AKRA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AKRA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-74.7 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-63.4 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-15.9 bps ≤ 0 (gross − hurdle, where hurdle=74.5 bps) |
| AKRA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AKRA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AMMN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-130.9 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-130.1 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-94.2 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AMMN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AMMN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-51.2 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-71.2 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-96.0 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AMMN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AMMN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-59.0 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-109.2 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.1 bps ≤ 0 (gross − hurdle, where hurdle=64.4 bps) |
| AMMN | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| AMMN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AMRT | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-111.4 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-120.7 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-107.6 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| AMRT | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AMRT | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-71.7 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-113.8 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-183.9 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AMRT | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| AMRT | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-58.3 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-8.4 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-127.6 bps ≤ 0 (gross − hurdle, where hurdle=81.0 bps) |
| AMRT | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AMRT | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| ANTM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-61.8 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-43.0 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.070 |
| ANTM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ANTM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ANTM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-52.0 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-54.1 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-37.5 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ANTM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ANTM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-74.0 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-49.9 bps ≤ 0 (gross − hurdle, where hurdle=73.8 bps) |
| ANTM | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.153 |
| ANTM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ANTM | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ARCI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-78.7 bps ≤ 0 (gross − hurdle, where hurdle=83.7 bps) |
| ARCI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-57.7 bps ≤ 0 (gross − hurdle, where hurdle=83.7 bps) |
| ARCI | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.064 |
| ARCI | daily_momentum | 20 | single_name_time_series | tested | fdr_rejected: p=0.077 |
| ARCI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ARCI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-47.3 bps ≤ 0 (gross − hurdle, where hurdle=83.7 bps) |
| ARCI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-20.9 bps ≤ 0 (gross − hurdle, where hurdle=83.7 bps) |
| ARCI | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.105 |
| ARCI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ARCI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ARCI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-37.1 bps ≤ 0 (gross − hurdle, where hurdle=83.7 bps) |
| ARCI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-43.3 bps ≤ 0 (gross − hurdle, where hurdle=83.7 bps) |
| ARCI | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.145 |
| ARCI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ARCI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ARTO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-97.6 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-170.8 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-140.9 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ARTO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ARTO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-131.8 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-99.6 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-215.2 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ARTO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ARTO | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-125.9 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-97.4 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-125.3 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| ARTO | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ARTO | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ASII | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-120.4 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-97.5 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-67.6 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ASII | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ASII | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-45.7 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-17.3 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-45.8 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ASII | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ASII | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-93.1 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-99.9 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-72.7 bps ≤ 0 (gross − hurdle, where hurdle=61.2 bps) |
| ASII | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ASII | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ASSA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-154.8 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-149.8 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-113.0 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ASSA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ASSA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-96.5 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-123.6 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-88.7 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ASSA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| ASSA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-137.3 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-185.4 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-138.2 bps ≤ 0 (gross − hurdle, where hurdle=126.1 bps) |
| ASSA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ASSA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| AUTO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-84.0 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-67.5 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-50.8 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| AUTO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AUTO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-39.6 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-85.7 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-57.5 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| AUTO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| AUTO | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-54.7 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-45.2 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-55.1 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| AUTO | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| AUTO | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AVIA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-94.4 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-98.2 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-74.1 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| AVIA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| AVIA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-108.5 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-103.0 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-139.3 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| AVIA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| AVIA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-108.5 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-100.6 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-116.1 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| AVIA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| AVIA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBCA | daily_momentum | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.043, mean_hr=0.384 |
| BBCA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-120.2 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-161.3 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BBCA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBCA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-56.9 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-58.8 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-34.0 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BBCA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBCA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-72.3 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.3 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-91.0 bps ≤ 0 (gross − hurdle, where hurdle=81.1 bps) |
| BBCA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BBCA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBNI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.8 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-106.0 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-89.7 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BBNI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBNI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-68.5 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-99.4 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-102.4 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BBNI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBNI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-65.8 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-72.1 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-101.9 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | volume_breakout | 20 | single_name_time_series | tested | cost_gated: net_after_cost=-228.6 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| BBNI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BBRI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-82.5 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-136.4 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-147.7 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | daily_momentum | 20 | single_name_time_series | tested | cost_gated: net_after_cost=-313.9 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBRI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-75.8 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-100.3 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-102.7 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BBRI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBRI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-65.2 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-89.1 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-115.5 bps ≤ 0 (gross − hurdle, where hurdle=72.7 bps) |
| BBRI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BBRI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBTN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-103.8 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-77.1 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-63.1 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| BBTN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBTN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.9 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-63.8 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-53.7 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BBTN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BBTN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-76.4 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-68.8 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-108.8 bps ≤ 0 (gross − hurdle, where hurdle=84.1 bps) |
| BBTN | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BBTN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BBYB | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-124.2 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-167.6 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-127.8 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BBYB | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBYB | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-132.1 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-136.8 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-161.4 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BBYB | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BBYB | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-152.0 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-128.3 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-153.1 bps ≤ 0 (gross − hurdle, where hurdle=124.4 bps) |
| BBYB | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BBYB | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BDMN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-36.9 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-75.0 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-20.1 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BDMN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BDMN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.5 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-34.0 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-68.4 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| BDMN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BDMN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-24.7 bps ≤ 0 (gross − hurdle, where hurdle=66.5 bps) |
| BDMN | volume_breakout | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=61.8 bps <= hurdle=66.5 bps (gross=83.7, null=21.9 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| BDMN | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.068 |
| BDMN | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BDMN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BFIN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-118.0 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-122.2 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-106.8 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| BFIN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BFIN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-109.1 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-105.3 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.6 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BFIN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BFIN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-86.7 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-95.4 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.5 bps ≤ 0 (gross − hurdle, where hurdle=92.2 bps) |
| BFIN | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BFIN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BIPI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-135.6 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-216.2 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-69.4 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BIPI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BIPI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-88.2 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-161.9 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-223.3 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| BIPI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BIPI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-127.7 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-82.5 bps ≤ 0 (gross − hurdle, where hurdle=112.2 bps) |
| BIPI | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.262 |
| BIPI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BIPI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BJTM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-133.8 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-135.3 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-141.6 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BJTM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BJTM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-116.1 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-158.8 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-151.8 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BJTM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BJTM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-129.3 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-123.1 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-112.8 bps ≤ 0 (gross − hurdle, where hurdle=138.4 bps) |
| BJTM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| BJTM | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BMRI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-70.4 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-96.0 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-83.3 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BMRI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BMRI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-63.4 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-60.1 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-93.7 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BMRI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BMRI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-87.9 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.0 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-56.7 bps ≤ 0 (gross − hurdle, where hurdle=65.5 bps) |
| BMRI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BMRI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BNBR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-121.6 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-171.1 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.095 |
| BNBR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| BNBR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BNBR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-333.5 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-319.6 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-412.5 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=23 < MIN_EFF_N=30 |
| BNBR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BNBR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-252.3 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-140.4 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-168.1 bps ≤ 0 (gross − hurdle, where hurdle=215.8 bps) |
| BNBR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| BNBR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BNGA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-60.6 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-66.0 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-71.8 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BNGA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BNGA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-76.2 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-82.1 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-82.5 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BNGA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BNGA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-75.5 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-73.7 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-50.0 bps ≤ 0 (gross − hurdle, where hurdle=70.5 bps) |
| BNGA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BNGA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BREN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-87.9 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-122.0 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-159.7 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BREN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BREN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-125.6 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-35.2 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-87.8 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BREN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BREN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-82.9 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-55.3 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-65.4 bps ≤ 0 (gross − hurdle, where hurdle=84.0 bps) |
| BREN | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BREN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BRIS | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-76.3 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-64.5 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-4.2 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BRIS | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BRIS | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-36.7 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-45.3 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-53.0 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BRIS | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BRIS | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-74.3 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-53.3 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.1 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| BRIS | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BRIS | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BRMS | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-122.5 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-88.0 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-15.6 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | daily_momentum | 20 | single_name_time_series | tested | fdr_rejected: p=0.205 |
| BRMS | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BRMS | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-117.8 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-53.6 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.052 |
| BRMS | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BRMS | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BRMS | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-124.1 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-53.3 bps ≤ 0 (gross − hurdle, where hurdle=131.3 bps) |
| BRMS | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=47.3 bps <= hurdle=131.3 bps (gross=197.2, null=150.0 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| BRMS | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BRMS | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| BRPT | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-36.6 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-34.3 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-15.6 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BRPT | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BRPT | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-66.2 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-109.9 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-30.9 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BRPT | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BRPT | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-38.4 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-83.2 bps ≤ 0 (gross − hurdle, where hurdle=75.8 bps) |
| BRPT | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.141 |
| BRPT | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BRPT | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BSDE | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-135.8 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-174.1 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-199.8 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BSDE | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BSDE | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-120.6 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-119.1 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-114.1 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BSDE | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BSDE | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-147.1 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-166.0 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-230.4 bps ≤ 0 (gross − hurdle, where hurdle=131.9 bps) |
| BSDE | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BSDE | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BTPS | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-93.9 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-103.3 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-95.9 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BTPS | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BTPS | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-89.1 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-88.9 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-75.7 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BTPS | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BTPS | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-109.3 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-109.5 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-81.1 bps ≤ 0 (gross − hurdle, where hurdle=91.0 bps) |
| BTPS | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BTPS | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BULL | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-118.4 bps ≤ 0 (gross − hurdle, where hurdle=103.4 bps) |
| BULL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-57.2 bps ≤ 0 (gross − hurdle, where hurdle=103.4 bps) |
| BULL | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.176 |
| BULL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BULL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BULL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-66.4 bps ≤ 0 (gross − hurdle, where hurdle=103.4 bps) |
| BULL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-27.7 bps ≤ 0 (gross − hurdle, where hurdle=103.4 bps) |
| BULL | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.068 |
| BULL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BULL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BULL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-58.4 bps ≤ 0 (gross − hurdle, where hurdle=103.4 bps) |
| BULL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-101.2 bps ≤ 0 (gross − hurdle, where hurdle=103.4 bps) |
| BULL | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.160 |
| BULL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| BULL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BUMI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-131.5 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-80.8 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.059 |
| BUMI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| BUMI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BUMI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-99.6 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-122.9 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.152 |
| BUMI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BUMI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BUMI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-70.9 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-25.0 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-46.4 bps ≤ 0 (gross − hurdle, where hurdle=103.3 bps) |
| BUMI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BUMI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BUVA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-232.6 bps ≤ 0 (gross − hurdle, where hurdle=150.0 bps) |
| BUVA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-65.4 bps ≤ 0 (gross − hurdle, where hurdle=150.0 bps) |
| BUVA | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.073 |
| BUVA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BUVA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BUVA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-105.4 bps ≤ 0 (gross − hurdle, where hurdle=150.0 bps) |
| BUVA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-12.8 bps ≤ 0 (gross − hurdle, where hurdle=150.0 bps) |
| BUVA | mean_reversion | 5 | single_name_time_series | tested | thin OOS: oos_eff_n=0 < MIN_OOS_EFF_N=10; insufficient evidence for persistence |
| BUVA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BUVA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BUVA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-26.3 bps ≤ 0 (gross − hurdle, where hurdle=150.0 bps) |
| BUVA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-31.8 bps ≤ 0 (gross − hurdle, where hurdle=150.0 bps) |
| BUVA | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=56.0 bps <= hurdle=150.0 bps (gross=371.8, null=315.8 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| BUVA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| BUVA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| BWPT | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-120.0 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-108.0 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-30.5 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| BWPT | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| BWPT | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-113.9 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-47.9 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | mean_reversion | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=106.0 bps <= hurdle=130.5 bps (gross=214.6, null=108.6 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| BWPT | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| BWPT | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| BWPT | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-131.6 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-137.1 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-11.5 bps ≤ 0 (gross − hurdle, where hurdle=130.5 bps) |
| BWPT | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| BWPT | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| CBDK | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-34.8 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-127.2 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.228 |
| CBDK | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=16 < MIN_EFF_N=30 |
| CBDK | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| CBDK | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-127.4 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-162.0 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-281.5 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=17 < MIN_EFF_N=30 |
| CBDK | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| CBDK | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-109.5 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-118.2 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-88.9 bps ≤ 0 (gross − hurdle, where hurdle=69.0 bps) |
| CBDK | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=17 < MIN_EFF_N=30 |
| CBDK | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| CMNT | daily_momentum | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.023, mean_hr=0.311 |
| CMNT | daily_momentum | 2 | single_name_time_series | decayed | decayed: mean_sharpe=-0.042, mean_hr=0.294 |
| CMNT | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-189.6 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| CMNT | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CMNT | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-84.4 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-83.9 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-120.8 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| CMNT | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| CMNT | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-123.7 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-137.2 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-176.3 bps ≤ 0 (gross − hurdle, where hurdle=107.6 bps) |
| CMNT | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| CMNT | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.058, mean_hr=0.227 |
| CPIN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-121.7 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-89.6 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-77.8 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| CPIN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CPIN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-69.3 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.9 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-124.6 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| CPIN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CPIN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-85.1 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-86.2 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-104.0 bps ≤ 0 (gross − hurdle, where hurdle=73.0 bps) |
| CPIN | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| CPIN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CTRA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-174.4 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-174.0 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-216.2 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| CTRA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CTRA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-92.8 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-153.6 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-136.3 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| CTRA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CTRA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-127.8 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-137.1 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-139.8 bps ≤ 0 (gross − hurdle, where hurdle=123.1 bps) |
| CTRA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| CTRA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CUAN | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-41.2 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | daily_momentum | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=116.1 bps <= hurdle=116.9 bps (gross=129.8, null=13.7 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| CUAN | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.114 |
| CUAN | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| CUAN | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CUAN | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-96.7 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-136.7 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-219.6 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| CUAN | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| CUAN | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-97.6 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-158.4 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-58.9 bps ≤ 0 (gross − hurdle, where hurdle=116.9 bps) |
| CUAN | volume_breakout | 20 | single_name_time_series | tested | fdr_rejected: p=0.230 |
| CUAN | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| DEWA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-121.8 bps ≤ 0 (gross − hurdle, where hurdle=102.4 bps) |
| DEWA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-95.2 bps ≤ 0 (gross − hurdle, where hurdle=102.4 bps) |
| DEWA | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.098 |
| DEWA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DEWA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| DEWA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-87.7 bps ≤ 0 (gross − hurdle, where hurdle=102.4 bps) |
| DEWA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-86.5 bps ≤ 0 (gross − hurdle, where hurdle=102.4 bps) |
| DEWA | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.118 |
| DEWA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| DEWA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| DEWA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-77.8 bps ≤ 0 (gross − hurdle, where hurdle=102.4 bps) |
| DEWA | volume_breakout | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=47.7 bps <= hurdle=102.4 bps (gross=122.3, null=74.6 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| DEWA | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=39.8 bps <= hurdle=102.4 bps (gross=234.0, null=194.2 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| DEWA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DEWA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| DOID | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-104.6 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-153.2 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.1 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DOID | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| DOID | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-141.5 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-165.7 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-141.7 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DOID | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| DOID | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-140.4 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-144.6 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-155.7 bps ≤ 0 (gross − hurdle, where hurdle=124.5 bps) |
| DOID | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DOID | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.175, mean_hr=0.091 |
| DSNG | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-80.4 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| DSNG | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-106.9 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| DSNG | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.075 |
| DSNG | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DSNG | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| DSNG | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-39.2 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| DSNG | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-21.6 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| DSNG | mean_reversion | 5 | single_name_time_series | tested | thin OOS: oos_eff_n=0 < MIN_OOS_EFF_N=10; insufficient evidence for persistence |
| DSNG | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DSNG | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| DSNG | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-59.4 bps ≤ 0 (gross − hurdle, where hurdle=73.1 bps) |
| DSNG | volume_breakout | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=27.9 bps <= hurdle=73.1 bps (gross=75.0, null=47.1 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| DSNG | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=46.4 bps <= hurdle=73.1 bps (gross=164.0, null=117.6 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| DSNG | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DSNG | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| DSSA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-42.7 bps ≤ 0 (gross − hurdle, where hurdle=87.1 bps) |
| DSSA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-6.4 bps ≤ 0 (gross − hurdle, where hurdle=87.1 bps) |
| DSSA | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.046 |
| DSSA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| DSSA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| DSSA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-62.9 bps ≤ 0 (gross − hurdle, where hurdle=87.1 bps) |
| DSSA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-43.7 bps ≤ 0 (gross − hurdle, where hurdle=87.1 bps) |
| DSSA | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.245 |
| DSSA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| DSSA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| DSSA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-57.4 bps ≤ 0 (gross − hurdle, where hurdle=87.1 bps) |
| DSSA | volume_breakout | 2 | single_name_time_series | tested | fdr_rejected: p=0.073 |
| DSSA | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.143 |
| DSSA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| DSSA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| ELIT | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-150.2 bps ≤ 0 (gross − hurdle, where hurdle=125.3 bps) |
| ELIT | daily_momentum | 2 | single_name_time_series | tested | fdr_rejected: p=0.050 |
| ELIT | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.073 |
| ELIT | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| ELIT | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ELIT | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-102.5 bps ≤ 0 (gross − hurdle, where hurdle=125.3 bps) |
| ELIT | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-102.3 bps ≤ 0 (gross − hurdle, where hurdle=125.3 bps) |
| ELIT | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-26.9 bps ≤ 0 (gross − hurdle, where hurdle=125.3 bps) |
| ELIT | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ELIT | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ELIT | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-96.4 bps ≤ 0 (gross − hurdle, where hurdle=125.3 bps) |
| ELIT | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-15.1 bps ≤ 0 (gross − hurdle, where hurdle=125.3 bps) |
| ELIT | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.071 |
| ELIT | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ELIT | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ELSA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-119.7 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-135.5 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-99.5 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ELSA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| ELSA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-84.5 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-49.5 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-36.6 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| ELSA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ELSA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-93.1 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-125.9 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-89.8 bps ≤ 0 (gross − hurdle, where hurdle=111.3 bps) |
| ELSA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ELSA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| EMTK | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-96.9 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-162.6 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-107.3 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| EMTK | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| EMTK | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-60.0 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-89.6 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-61.0 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| EMTK | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| EMTK | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-75.3 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-83.3 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-134.7 bps ≤ 0 (gross − hurdle, where hurdle=94.4 bps) |
| EMTK | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| EMTK | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ENRG | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-157.6 bps ≤ 0 (gross − hurdle, where hurdle=97.0 bps) |
| ENRG | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-54.2 bps ≤ 0 (gross − hurdle, where hurdle=97.0 bps) |
| ENRG | daily_momentum | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=11.3 bps <= hurdle=97.0 bps (gross=200.5, null=189.2 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| ENRG | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ENRG | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ENRG | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-106.5 bps ≤ 0 (gross − hurdle, where hurdle=97.0 bps) |
| ENRG | mean_reversion | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=25.4 bps <= hurdle=97.0 bps (gross=98.0, null=72.7 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| ENRG | mean_reversion | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=80.3 bps <= hurdle=97.0 bps (gross=270.4, null=190.1 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| ENRG | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| ENRG | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ENRG | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-82.6 bps ≤ 0 (gross − hurdle, where hurdle=97.0 bps) |
| ENRG | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-117.6 bps ≤ 0 (gross − hurdle, where hurdle=97.0 bps) |
| ENRG | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.050 |
| ENRG | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ENRG | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ERAA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-134.2 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-170.8 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-110.2 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| ERAA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ERAA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-110.7 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-68.2 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-56.0 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| ERAA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ERAA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-109.4 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-110.6 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-87.6 bps ≤ 0 (gross − hurdle, where hurdle=126.3 bps) |
| ERAA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ERAA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ESSA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-141.5 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-144.0 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-112.1 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| ESSA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ESSA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-64.2 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-128.5 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-22.4 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ESSA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ESSA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-61.3 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-119.9 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-68.1 bps ≤ 0 (gross − hurdle, where hurdle=118.6 bps) |
| ESSA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ESSA | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.024, mean_hr=0.091 |
| EXCL | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-112.6 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.7 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-45.4 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| EXCL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| EXCL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-49.6 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-26.7 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | mean_reversion | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=66.9 bps <= hurdle=79.5 bps (gross=100.3, null=33.4 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| EXCL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| EXCL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| EXCL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-43.7 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-49.5 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-64.5 bps ≤ 0 (gross − hurdle, where hurdle=79.5 bps) |
| EXCL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| EXCL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| GGRM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-14.4 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-44.7 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-14.4 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| GGRM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| GGRM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-57.2 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-59.2 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-115.9 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| GGRM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| GGRM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-46.6 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-64.2 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-10.7 bps ≤ 0 (gross − hurdle, where hurdle=55.5 bps) |
| GGRM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| GGRM | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.007, mean_hr=0.091 |
| GJTL | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-65.1 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-85.3 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-37.6 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| GJTL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| GJTL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-86.6 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-48.8 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-71.2 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| GJTL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| GJTL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-70.7 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-97.7 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-109.4 bps ≤ 0 (gross − hurdle, where hurdle=79.8 bps) |
| GJTL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| GJTL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HEAL | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-121.9 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-144.1 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-195.8 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| HEAL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HEAL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-130.1 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-143.0 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-171.9 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| HEAL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HEAL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-120.2 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-145.5 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-162.5 bps ≤ 0 (gross − hurdle, where hurdle=116.4 bps) |
| HEAL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| HEAL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HMSP | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-135.9 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-84.1 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-158.9 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| HMSP | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HMSP | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-91.6 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-98.5 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-119.7 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| HMSP | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HMSP | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-124.8 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-107.8 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-61.6 bps ≤ 0 (gross − hurdle, where hurdle=110.9 bps) |
| HMSP | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| HMSP | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HRTA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-66.9 bps ≤ 0 (gross − hurdle, where hurdle=92.4 bps) |
| HRTA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-48.1 bps ≤ 0 (gross − hurdle, where hurdle=92.4 bps) |
| HRTA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| HRTA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HRTA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-39.6 bps ≤ 0 (gross − hurdle, where hurdle=92.4 bps) |
| HRTA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-6.6 bps ≤ 0 (gross − hurdle, where hurdle=92.4 bps) |
| HRTA | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.118 |
| HRTA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| HRTA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HRTA | volume_breakout | 1 | single_name_time_series | tested | loses_to_null: edge_over_null=59.0 bps <= hurdle=92.4 bps (gross=94.5, null=35.4 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| HRTA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-27.6 bps ≤ 0 (gross − hurdle, where hurdle=92.4 bps) |
| HRTA | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=73.1 bps <= hurdle=92.4 bps (gross=249.9, null=176.8 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| HRTA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| HRTA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HRUM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-111.5 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-153.0 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-90.5 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| HRUM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HRUM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-76.0 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-80.4 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-146.2 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| HRUM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| HRUM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-91.1 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-116.0 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-171.7 bps ≤ 0 (gross − hurdle, where hurdle=103.2 bps) |
| HRUM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| HRUM | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ICBP | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-84.0 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-102.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-157.6 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ICBP | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ICBP | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-78.0 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-66.3 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-85.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ICBP | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| ICBP | volume_breakout | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.040, mean_hr=0.434 |
| ICBP | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-61.5 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-84.6 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| ICBP | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ICBP | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| IMJS | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-113.1 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-135.9 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-67.5 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| IMJS | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| IMJS | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-104.5 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-39.6 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-109.5 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| IMJS | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| IMJS | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-90.1 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-111.7 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-56.0 bps ≤ 0 (gross − hurdle, where hurdle=108.1 bps) |
| IMJS | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| IMJS | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INCO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-51.4 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-109.0 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-141.6 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| INCO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INCO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-55.4 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-3.3 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-17.0 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| INCO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INCO | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-77.8 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-108.9 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-129.7 bps ≤ 0 (gross − hurdle, where hurdle=65.6 bps) |
| INCO | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| INCO | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INDF | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-92.3 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-59.0 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-56.8 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| INDF | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INDF | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-72.9 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-56.1 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-40.5 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| INDF | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INDF | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-70.2 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-73.2 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-63.3 bps ≤ 0 (gross − hurdle, where hurdle=76.8 bps) |
| INDF | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| INDF | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| INET | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-130.5 bps ≤ 0 (gross − hurdle, where hurdle=131.0 bps) |
| INET | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-89.6 bps ≤ 0 (gross − hurdle, where hurdle=131.0 bps) |
| INET | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.120 |
| INET | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=23 < MIN_EFF_N=30 |
| INET | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INET | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-24.3 bps ≤ 0 (gross − hurdle, where hurdle=131.0 bps) |
| INET | mean_reversion | 5 | single_name_time_series | tested | thin OOS: oos_eff_n=0 < MIN_OOS_EFF_N=10; insufficient evidence for persistence |
| INET | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| INET | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| INET | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-126.5 bps ≤ 0 (gross − hurdle, where hurdle=131.0 bps) |
| INET | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-2.4 bps ≤ 0 (gross − hurdle, where hurdle=131.0 bps) |
| INET | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=52.2 bps <= hurdle=131.0 bps (gross=285.3, null=233.2 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| INET | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| INET | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INKP | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-82.0 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-101.3 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-85.4 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| INKP | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INKP | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-56.3 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-35.7 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-71.2 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| INKP | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| INKP | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-100.8 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-30.3 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-63.5 bps ≤ 0 (gross − hurdle, where hurdle=69.7 bps) |
| INKP | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| INKP | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| INTP | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-83.3 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-115.7 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-55.7 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| INTP | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INTP | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-75.0 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-87.8 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-100.0 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| INTP | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| INTP | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-97.1 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-105.5 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-142.1 bps ≤ 0 (gross − hurdle, where hurdle=90.3 bps) |
| INTP | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| INTP | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ISAT | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-105.4 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-82.3 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-120.1 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ISAT | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ISAT | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-72.0 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-35.4 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.141 |
| ISAT | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ISAT | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| ISAT | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-67.9 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-67.5 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-59.2 bps ≤ 0 (gross − hurdle, where hurdle=82.9 bps) |
| ISAT | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ISAT | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ITMG | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-55.4 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-35.9 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-80.1 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| ITMG | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ITMG | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-41.8 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-55.2 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-15.0 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| ITMG | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| ITMG | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-59.1 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-79.8 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-57.4 bps ≤ 0 (gross − hurdle, where hurdle=51.5 bps) |
| ITMG | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| ITMG | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| JPFA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-103.8 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-92.5 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-29.9 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| JPFA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| JPFA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-18.4 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-10.2 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| JPFA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| JPFA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-56.2 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-52.6 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-54.7 bps ≤ 0 (gross − hurdle, where hurdle=86.0 bps) |
| JPFA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| JPFA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| JSMR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-88.4 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-105.8 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-128.5 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| JSMR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| JSMR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-68.9 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-78.9 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-82.4 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| JSMR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| JSMR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-79.0 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-87.8 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-126.6 bps ≤ 0 (gross − hurdle, where hurdle=75.4 bps) |
| JSMR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| JSMR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| KIJA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-87.9 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-91.0 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-115.0 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| KIJA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| KIJA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-139.7 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-90.2 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-74.3 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| KIJA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| KIJA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-141.0 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-101.3 bps ≤ 0 (gross − hurdle, where hurdle=109.4 bps) |
| KIJA | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.114 |
| KIJA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| KIJA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| KLBF | daily_momentum | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.076, mean_hr=0.402 |
| KLBF | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-149.3 bps ≤ 0 (gross − hurdle, where hurdle=109.1 bps) |
| KLBF | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-226.8 bps ≤ 0 (gross − hurdle, where hurdle=109.1 bps) |
| KLBF | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| KLBF | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| KLBF | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-98.4 bps ≤ 0 (gross − hurdle, where hurdle=109.1 bps) |
| KLBF | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-141.8 bps ≤ 0 (gross − hurdle, where hurdle=109.1 bps) |
| KLBF | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-124.5 bps ≤ 0 (gross − hurdle, where hurdle=109.1 bps) |
| KLBF | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| KLBF | mean_reversion | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.060, mean_hr=0.182 |
| KLBF | volume_breakout | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.089, mean_hr=0.406 |
| KLBF | volume_breakout | 2 | single_name_time_series | decayed | decayed: mean_sharpe=-0.132, mean_hr=0.420 |
| KLBF | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-186.1 bps ≤ 0 (gross − hurdle, where hurdle=109.1 bps) |
| KLBF | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| KLBF | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| LSIP | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-66.3 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-91.0 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | daily_momentum | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=35.6 bps <= hurdle=77.5 bps (gross=86.9, null=51.4 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| LSIP | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| LSIP | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| LSIP | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-65.8 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-34.8 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.103 |
| LSIP | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| LSIP | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| LSIP | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-55.0 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-27.4 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-0.5 bps ≤ 0 (gross − hurdle, where hurdle=77.5 bps) |
| LSIP | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| LSIP | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MAPA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-165.8 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-131.1 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-125.0 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MAPA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MAPA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-83.5 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-99.7 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-85.3 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| MAPA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MAPA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-130.4 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-122.2 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-126.2 bps ≤ 0 (gross − hurdle, where hurdle=118.3 bps) |
| MAPA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MAPA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| MAPI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-118.9 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-120.4 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-179.3 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MAPI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MAPI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-53.3 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-62.5 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-17.7 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MAPI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MAPI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-107.7 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-112.3 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-103.2 bps ≤ 0 (gross − hurdle, where hurdle=79.2 bps) |
| MAPI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| MAPI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MARK | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-128.5 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-143.8 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-80.2 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MARK | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MARK | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.6 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-56.2 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-18.3 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| MARK | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MARK | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-83.4 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-70.2 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-26.4 bps ≤ 0 (gross − hurdle, where hurdle=87.3 bps) |
| MARK | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| MARK | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MBMA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-51.3 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-116.0 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-62.4 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| MBMA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MBMA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-34.8 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-16.8 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.097 |
| MBMA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| MBMA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MBMA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-108.3 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-105.0 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-33.2 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | volume_breakout | 20 | single_name_time_series | tested | cost_gated: net_after_cost=-13.0 bps ≤ 0 (gross − hurdle, where hurdle=86.3 bps) |
| MBMA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MDKA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-81.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-132.6 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-43.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| MDKA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MDKA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-53.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-30.9 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-131.8 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| MDKA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MDKA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-61.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-28.2 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-45.4 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | volume_breakout | 20 | single_name_time_series | tested | cost_gated: net_after_cost=-26.5 bps ≤ 0 (gross − hurdle, where hurdle=75.9 bps) |
| MDKA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MEDC | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-113.0 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-54.9 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-51.5 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| MEDC | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MEDC | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-48.0 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-50.0 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-11.7 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MEDC | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MEDC | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-83.1 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-52.5 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-25.8 bps ≤ 0 (gross − hurdle, where hurdle=74.2 bps) |
| MEDC | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| MEDC | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| MIKA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-111.4 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-113.9 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-99.2 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| MIKA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MIKA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-54.7 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-44.2 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.049 |
| MIKA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MIKA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MIKA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-78.4 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-60.8 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-132.1 bps ≤ 0 (gross − hurdle, where hurdle=68.4 bps) |
| MIKA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MIKA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MPMX | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-90.7 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-80.8 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-87.4 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| MPMX | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MPMX | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-101.2 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-103.0 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-89.4 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MPMX | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MPMX | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-71.8 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-94.6 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-68.7 bps ≤ 0 (gross − hurdle, where hurdle=92.0 bps) |
| MPMX | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MPMX | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MTDL | daily_momentum | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.022, mean_hr=0.317 |
| MTDL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-173.5 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-187.9 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| MTDL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MTDL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-84.7 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-88.3 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-76.6 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| MTDL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MTDL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-148.8 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-95.1 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-107.6 bps ≤ 0 (gross − hurdle, where hurdle=143.2 bps) |
| MTDL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=23 < MIN_EFF_N=30 |
| MTDL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MTEL | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-100.1 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-127.5 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-84.5 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MTEL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| MTEL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.2 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-48.4 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-96.2 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MTEL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MTEL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-56.5 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-97.2 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.4 bps ≤ 0 (gross − hurdle, where hurdle=83.8 bps) |
| MTEL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| MTEL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MYOR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-96.6 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-106.1 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-161.1 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| MYOR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| MYOR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-81.1 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-70.2 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-77.3 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| MYOR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| MYOR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-70.8 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-68.5 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-62.9 bps ≤ 0 (gross − hurdle, where hurdle=74.7 bps) |
| MYOR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| MYOR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| NCKL | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-93.8 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-132.4 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-61.3 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| NCKL | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| NCKL | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.6 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-17.4 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.096 |
| NCKL | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| NCKL | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| NCKL | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-119.8 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-47.2 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-99.6 bps ≤ 0 (gross − hurdle, where hurdle=95.3 bps) |
| NCKL | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| NCKL | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| NISP | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-74.6 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-92.0 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-74.6 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| NISP | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| NISP | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-59.1 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-58.6 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-58.9 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| NISP | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| NISP | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-67.7 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-105.4 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-75.7 bps ≤ 0 (gross − hurdle, where hurdle=81.2 bps) |
| NISP | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| NISP | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| PANI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-81.9 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-68.1 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.069 |
| PANI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PANI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PANI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-37.9 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-90.7 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.201 |
| PANI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PANI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PANI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-94.9 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-75.3 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-14.1 bps ≤ 0 (gross − hurdle, where hurdle=86.1 bps) |
| PANI | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PANI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PGEO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-99.6 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-91.4 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-127.8 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| PGEO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PGEO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-100.0 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-35.9 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-68.1 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| PGEO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| PGEO | volume_breakout | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.040, mean_hr=0.429 |
| PGEO | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-113.4 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-169.3 bps ≤ 0 (gross − hurdle, where hurdle=90.7 bps) |
| PGEO | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PGEO | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.021, mean_hr=0.182 |
| PNLF | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-138.8 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-129.0 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-117.5 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| PNLF | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PNLF | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-142.6 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-112.0 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-139.5 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| PNLF | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PNLF | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-118.5 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-122.3 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-154.9 bps ≤ 0 (gross − hurdle, where hurdle=132.4 bps) |
| PNLF | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PNLF | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| POWR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-107.8 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-89.2 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-75.0 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| POWR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| POWR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-82.7 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-72.8 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-55.7 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=23 < MIN_EFF_N=30 |
| POWR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| POWR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-82.8 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-80.3 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-73.1 bps ≤ 0 (gross − hurdle, where hurdle=92.9 bps) |
| POWR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| POWR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| PSAB | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-122.8 bps ≤ 0 (gross − hurdle, where hurdle=128.7 bps) |
| PSAB | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-58.5 bps ≤ 0 (gross − hurdle, where hurdle=128.7 bps) |
| PSAB | daily_momentum | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=118.6 bps <= hurdle=128.7 bps (gross=331.4, null=212.8 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| PSAB | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PSAB | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PSAB | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-59.6 bps ≤ 0 (gross − hurdle, where hurdle=128.7 bps) |
| PSAB | mean_reversion | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=44.8 bps <= hurdle=128.7 bps (gross=132.7, null=87.9 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| PSAB | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.061 |
| PSAB | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| PSAB | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PSAB | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-64.6 bps ≤ 0 (gross − hurdle, where hurdle=128.7 bps) |
| PSAB | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-99.6 bps ≤ 0 (gross − hurdle, where hurdle=128.7 bps) |
| PSAB | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.061 |
| PSAB | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PSAB | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PTBA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-79.6 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-103.9 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-66.6 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| PTBA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PTBA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-90.3 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-60.7 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-30.0 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=24 < MIN_EFF_N=30 |
| PTBA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PTBA | volume_breakout | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.015, mean_hr=0.396 |
| PTBA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.6 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-60.4 bps ≤ 0 (gross − hurdle, where hurdle=73.4 bps) |
| PTBA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| PTBA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PTRO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-12.2 bps ≤ 0 (gross − hurdle, where hurdle=65.7 bps) |
| PTRO | daily_momentum | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=58.8 bps <= hurdle=65.7 bps (gross=151.8, null=93.0 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| PTRO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| PTRO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PTRO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.1 bps ≤ 0 (gross − hurdle, where hurdle=65.7 bps) |
| PTRO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-112.2 bps ≤ 0 (gross − hurdle, where hurdle=65.7 bps) |
| PTRO | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.321 |
| PTRO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| PTRO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PTRO | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-5.5 bps ≤ 0 (gross − hurdle, where hurdle=65.7 bps) |
| PTRO | volume_breakout | 2 | single_name_time_series | tested | fdr_rejected: p=0.109 |
| PTRO | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=-14.8 bps <= hurdle=65.7 bps (gross=245.1, null=259.9 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| PTRO | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| PTRO | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PWON | daily_momentum | 1 | single_name_time_series | decayed | decayed: mean_sharpe=-0.044, mean_hr=0.385 |
| PWON | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-160.9 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-142.3 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| PWON | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| PWON | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-126.2 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-94.6 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-138.6 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| PWON | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| PWON | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-133.2 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-151.8 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-160.8 bps ≤ 0 (gross − hurdle, where hurdle=115.2 bps) |
| PWON | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| PWON | volume_breakout | 60 | single_name_time_series | decayed | decayed: mean_sharpe=-0.054, mean_hr=0.273 |
| RAJA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-129.1 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-61.3 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.177 |
| RAJA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| RAJA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| RAJA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-130.0 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-123.1 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-89.4 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| RAJA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| RAJA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-99.2 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-104.1 bps ≤ 0 (gross − hurdle, where hurdle=126.0 bps) |
| RAJA | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.123 |
| RAJA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| RAJA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| RATU | daily_momentum | 1 | single_name_time_series | tested | oos_split_note: OOS edge non-positive: oos_edge_bps=-6.3 |
| RATU | daily_momentum | 2 | single_name_time_series | tested | fdr_rejected: p=0.166 |
| RATU | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.204 |
| RATU | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=17 < MIN_EFF_N=30 |
| RATU | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| RATU | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-73.1 bps ≤ 0 (gross − hurdle, where hurdle=70.9 bps) |
| RATU | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-130.2 bps ≤ 0 (gross − hurdle, where hurdle=70.9 bps) |
| RATU | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-134.8 bps ≤ 0 (gross − hurdle, where hurdle=70.9 bps) |
| RATU | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=17 < MIN_EFF_N=30 |
| RATU | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| RATU | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-96.0 bps ≤ 0 (gross − hurdle, where hurdle=70.9 bps) |
| RATU | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-72.8 bps ≤ 0 (gross − hurdle, where hurdle=70.9 bps) |
| RATU | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.263 |
| RATU | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=18 < MIN_EFF_N=30 |
| RATU | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=6 < MIN_EFF_N=30 |
| SCMA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-134.1 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-145.4 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-58.2 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| SCMA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| SCMA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-91.3 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-14.2 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-0.5 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SCMA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| SCMA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-62.3 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-39.8 bps ≤ 0 (gross − hurdle, where hurdle=93.2 bps) |
| SCMA | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.150 |
| SCMA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SCMA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SIDO | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-118.0 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-102.9 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | daily_momentum | 5 | single_name_time_series | decayed | decayed: mean_sharpe=-0.199, mean_hr=0.286 |
| SIDO | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SIDO | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SIDO | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-107.1 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-123.3 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-158.3 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| SIDO | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=8 < MIN_EFF_N=30 |
| SIDO | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-108.3 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-83.9 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-157.7 bps ≤ 0 (gross − hurdle, where hurdle=99.1 bps) |
| SIDO | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SIDO | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| SMDR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-81.4 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.8 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-74.1 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| SMDR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMDR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-78.4 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-102.7 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-9.8 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SMDR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMDR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-100.7 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-113.2 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-73.7 bps ≤ 0 (gross − hurdle, where hurdle=95.2 bps) |
| SMDR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SMDR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMGR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-110.9 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-78.6 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-174.9 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| SMGR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMGR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-92.0 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-106.2 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-122.4 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| SMGR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMGR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-84.8 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-112.3 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-193.6 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SMGR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| SMGR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMRA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-130.6 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-177.9 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-209.4 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| SMRA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SMRA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-89.9 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-76.0 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-75.8 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| SMRA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| SMRA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-112.1 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-112.4 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-140.1 bps ≤ 0 (gross − hurdle, where hurdle=102.0 bps) |
| SMRA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| SMRA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SRTG | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-65.9 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-45.2 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-86.0 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SRTG | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SRTG | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-54.7 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-62.8 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-30.2 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| SRTG | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SRTG | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-105.1 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-118.7 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-75.6 bps ≤ 0 (gross − hurdle, where hurdle=70.2 bps) |
| SRTG | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| SRTG | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SSIA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-83.5 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SSIA | daily_momentum | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=55.8 bps <= hurdle=72.3 bps (gross=115.4, null=59.6 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| SSIA | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.114 |
| SSIA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| SSIA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SSIA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-101.7 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SSIA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-35.0 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SSIA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-33.7 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SSIA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| SSIA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| SSIA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-38.8 bps ≤ 0 (gross − hurdle, where hurdle=72.3 bps) |
| SSIA | volume_breakout | 2 | single_name_time_series | tested | fdr_rejected: p=0.057 |
| SSIA | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.050 |
| SSIA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| SSIA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| TAPG | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-57.1 bps ≤ 0 (gross − hurdle, where hurdle=86.2 bps) |
| TAPG | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-23.8 bps ≤ 0 (gross − hurdle, where hurdle=86.2 bps) |
| TAPG | daily_momentum | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=51.0 bps <= hurdle=86.2 bps (gross=185.5, null=134.4 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| TAPG | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| TAPG | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| TAPG | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-68.2 bps ≤ 0 (gross − hurdle, where hurdle=86.2 bps) |
| TAPG | mean_reversion | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=32.2 bps <= hurdle=86.2 bps (gross=87.0, null=54.8 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| TAPG | mean_reversion | 5 | single_name_time_series | tested | fdr_rejected: p=0.063 |
| TAPG | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=25 < MIN_EFF_N=30 |
| TAPG | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| TAPG | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-50.0 bps ≤ 0 (gross − hurdle, where hurdle=86.2 bps) |
| TAPG | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-39.1 bps ≤ 0 (gross − hurdle, where hurdle=86.2 bps) |
| TAPG | volume_breakout | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=0.9 bps <= hurdle=86.2 bps (gross=135.0, null=134.1 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| TAPG | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TAPG | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TINS | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-49.3 bps ≤ 0 (gross − hurdle, where hurdle=66.0 bps) |
| TINS | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-0.0 bps ≤ 0 (gross − hurdle, where hurdle=66.0 bps) |
| TINS | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-24.3 bps ≤ 0 (gross − hurdle, where hurdle=66.0 bps) |
| TINS | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TINS | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TINS | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-23.8 bps ≤ 0 (gross − hurdle, where hurdle=66.0 bps) |
| TINS | mean_reversion | 2 | single_name_time_series | tested | loses_to_null: edge_over_null=14.9 bps <= hurdle=66.0 bps (gross=92.8, null=77.8 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| TINS | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TINS | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TINS | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-31.8 bps ≤ 0 (gross − hurdle, where hurdle=66.0 bps) |
| TINS | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-12.1 bps ≤ 0 (gross − hurdle, where hurdle=66.0 bps) |
| TINS | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.055 |
| TINS | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TINS | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TKIM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-85.2 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-67.2 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-44.2 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TKIM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TKIM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-64.4 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-53.1 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-29.2 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| TKIM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| TKIM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-95.8 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-68.5 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-67.4 bps ≤ 0 (gross − hurdle, where hurdle=74.6 bps) |
| TKIM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| TKIM | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TLKM | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-99.7 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-110.6 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-88.1 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| TLKM | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TLKM | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-109.3 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-97.8 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-113.2 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TLKM | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TLKM | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-86.5 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-84.0 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-76.4 bps ≤ 0 (gross − hurdle, where hurdle=85.0 bps) |
| TLKM | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| TLKM | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TOBA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-108.1 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-79.7 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.176 |
| TOBA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TOBA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TOBA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-79.2 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-58.7 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-111.6 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TOBA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TOBA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-63.5 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-28.0 bps ≤ 0 (gross − hurdle, where hurdle=90.9 bps) |
| TOBA | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.105 |
| TOBA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TOBA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TOWR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-131.2 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-118.1 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-116.6 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TOWR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TOWR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-90.0 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-108.0 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-127.4 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TOWR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TOWR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-104.3 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-100.9 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-136.0 bps ≤ 0 (gross − hurdle, where hurdle=85.9 bps) |
| TOWR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| TOWR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| TPIA | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-54.7 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-7.1 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-29.6 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| TPIA | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TPIA | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-131.8 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-159.2 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-101.7 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TPIA | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| TPIA | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-52.2 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-64.2 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-42.5 bps ≤ 0 (gross − hurdle, where hurdle=74.3 bps) |
| TPIA | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| TPIA | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| UNTR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-52.0 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-49.3 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-9.5 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| UNTR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| UNTR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-42.8 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-65.0 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-14.8 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| UNTR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| UNTR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-38.2 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-17.1 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-35.0 bps ≤ 0 (gross − hurdle, where hurdle=50.8 bps) |
| UNTR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=29 < MIN_EFF_N=30 |
| UNTR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| UNVR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-89.4 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-73.7 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | daily_momentum | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-90.8 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=27 < MIN_EFF_N=30 |
| UNVR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| UNVR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-94.9 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-117.8 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-142.1 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| UNVR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=9 < MIN_EFF_N=30 |
| UNVR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-68.1 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-100.2 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | volume_breakout | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-104.5 bps ≤ 0 (gross − hurdle, where hurdle=72.2 bps) |
| UNVR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| UNVR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| VKTR | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-149.6 bps ≤ 0 (gross − hurdle, where hurdle=144.4 bps) |
| VKTR | daily_momentum | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-107.6 bps ≤ 0 (gross − hurdle, where hurdle=144.4 bps) |
| VKTR | daily_momentum | 5 | single_name_time_series | tested | fdr_rejected: p=0.138 |
| VKTR | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=26 < MIN_EFF_N=30 |
| VKTR | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| VKTR | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-76.0 bps ≤ 0 (gross − hurdle, where hurdle=144.4 bps) |
| VKTR | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-74.2 bps ≤ 0 (gross − hurdle, where hurdle=144.4 bps) |
| VKTR | mean_reversion | 5 | single_name_time_series | tested | thin OOS: oos_eff_n=0 < MIN_OOS_EFF_N=10; insufficient evidence for persistence |
| VKTR | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| VKTR | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| VKTR | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-19.0 bps ≤ 0 (gross − hurdle, where hurdle=144.4 bps) |
| VKTR | volume_breakout | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-17.3 bps ≤ 0 (gross − hurdle, where hurdle=144.4 bps) |
| VKTR | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.094 |
| VKTR | volume_breakout | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| VKTR | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| WIFI | daily_momentum | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-48.6 bps ≤ 0 (gross − hurdle, where hurdle=79.0 bps) |
| WIFI | daily_momentum | 2 | single_name_time_series | tested | fdr_rejected: p=0.062 |
| WIFI | daily_momentum | 5 | single_name_time_series | tested | loses_to_null: edge_over_null=-5.4 bps <= hurdle=79.0 bps (gross=252.3, null=257.7 — signal does not pay for its own friction; the reported edge is asset drift, not signal) |
| WIFI | daily_momentum | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| WIFI | daily_momentum | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| WIFI | mean_reversion | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-52.8 bps ≤ 0 (gross − hurdle, where hurdle=79.0 bps) |
| WIFI | mean_reversion | 2 | single_name_time_series | tested | cost_gated: net_after_cost=-102.6 bps ≤ 0 (gross − hurdle, where hurdle=79.0 bps) |
| WIFI | mean_reversion | 5 | single_name_time_series | tested | cost_gated: net_after_cost=-12.3 bps ≤ 0 (gross − hurdle, where hurdle=79.0 bps) |
| WIFI | mean_reversion | 20 | single_name_time_series | insufficient_n | eff_n=28 < MIN_EFF_N=30 |
| WIFI | mean_reversion | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| WIFI | volume_breakout | 1 | single_name_time_series | tested | cost_gated: net_after_cost=-40.4 bps ≤ 0 (gross − hurdle, where hurdle=79.0 bps) |
| WIFI | volume_breakout | 2 | single_name_time_series | tested | fdr_rejected: p=0.069 |
| WIFI | volume_breakout | 5 | single_name_time_series | tested | fdr_rejected: p=0.045 |
| WIFI | volume_breakout | 20 | single_name_time_series | tested | invalid split: neither IS (status='insufficient_n') nor OOS (oos_eff_n=0 < MIN_OOS_EFF_N=10) is usable at this split frequency — cannot validate persistence or form a decay comparison |
| WIFI | volume_breakout | 60 | single_name_time_series | insufficient_n | eff_n=10 < MIN_EFF_N=30 |
| (all) | multi_week_trend | 5+ | single_name_time_series | data_blocked | data_blocked AND capital_blocked. Unblocks at 2+ years history AND owned cash (no T+4 clock). |
| (all) | multi_month_momentum | 20+ | single_name_time_series | data_blocked | 20-day hold → ~12 OOS windows/year < MIN_EFF_N=30. Unblocks at 3+ years. |
| (all) | order_book_obi | <1min | order_book | deferred | Plan-10 §7.9 deferred. Requires re-test on low-friction names post-capture. |

## Methodology Notes

- **Financing model:** ADR-0011 rate (10 bps/calendar-day) on the ADR-0014 settlement clock: charged over `[settle(entry), settle(exit)]` with both legs at T+2, so the cost is 10 bps per trading day held except where the settlement window straddles a weekend (Wed entry on a 1-day hold: 30 bps). Holiday-aware when an entry date is supplied; the weekday sweep below assumes a holiday-free week.
- **Cost gate entry_dow:** Best-feasible (cheapest) weekday chosen by `best_feasible_hurdle_bps`. The allocator controls entry timing — deployability is tested at the cheapest day, not the worst case (which is Wednesday for a 1-day hold — see ADR-0014, not Thursday as ADR-0011 stated).
- **FDR correction:** Benjamini-Hochberg at α=0.10 across the full (name, strategy, horizon) grid.
- **Effective-N:** single_name per-symbol de-overlap; event_driven pooled across universe.
- **Walk-forward:** fit=120 rows, test=40 rows, roll by test_window. Decay = majority of windows below Sharpe=0 AND hit_rate=0.5.
- **Impact model (intraday + daily single-name, plan 13):** Square-root Almgren law (round-trip): 2 × SQRT_IMPACT_COEFF(1.0) × daily_vol_bps × sqrt(participation_rate), where participation = POSITION_NOTIONAL_IDR(3,000,000 IDR, `sleeve_n5` preset, N=5 concurrent names — ADR-0017; the void ADR-0011 30M/name figure this replaced assumed a 300M leverage facility the sleeve was never funded by, see strategy_fit/sleeve_sizing.py) / ADV. Charged at both entry and exit; one-tick spread already in min_edge_to_clear_bps (no double-count). Daily single-name cells are priced from daily bars (plan 13), using the SAME formula: the square-root law prices ONE execution and the 2x factor is the entry leg plus the exit leg, so a 2-day hold does not double impact — only how daily_vol_bps and ADV are ESTIMATED differs. Event / cross-sectional / pairs cells still never price impact (`impact_priced=False`). **Coefficients are BORROWED for daily cells:** impact_coeffs.json was calibrated (plan 15) from tick fills on INTRADAY executions for 7 large-caps; names outside that sample fall through to global_default=1.0, the conservative high-friction end. See impact.DAILY_COEFF_IS_BORROWED_NOTE. **W2.1: impact_priced=True is a hard prerequisite for PASS** — a cell whose impact was never priced is excluded from Surviving Strategies, not passed with an assumed 0.0 (see Cost-Gate Passers / Coverage Table for the `impact_not_priced` reason). See strategy_fit/impact.py.
- **OOS persistence and power (W2.1; extended to daily cells in plan 13):** Single 60/40 chronological IS/OOS split — on trading dates for intraday cells, on raw daily bars (split BEFORE the signal is computed, so no lookback or forward-return window can straddle the boundary) for daily single-name cells. is_persistent=True iff OOS edge > 0 AND OOS eff_n ≥ MIN_OOS_EFF_N=10 — MIN_OOS_EFF_N is a hard, not-zero floor, not a power calculation. **A False persistence verdict is now BLOCKING for PASS (plan 13).** Previously the floor was checked but the verdict itself was only annotated, so a cell with 50+ OOS observations and a measured NEGATIVE out-of-sample edge could still be reported as a survivor — the exact in-sample-only failure that killed the intraday ORB family. **Below this floor a cell is excluded from PASS** (INCONCLUSIVE at best), shown as `oos_underfloor` in Cost-Gate Passers / Coverage Table. Separately, `oos_required_eff_n` (lib.stats.required_effective_n: N ≥ (z·σ/edge)², z=2) asks whether oos_eff_n is enough to *trust* the specific observed edge given its per-trade dispersion — a cell can clear the hard floor and still be `oos_underpowered`, shown as YES ⚠ (UNDERPOWERED) in the survivor table. This is an expected, common, honest verdict given ~55 trading days of history, not evidence the pipeline is broken. See strategy_fit/intraday_walk_forward.py.
- **Always-long null gate:** for single_name_time_series, event_driven, and intraday_same_day cells, the family runner ALSO scores the same de-overlapped, long-only, hold-matched population with the signal deleted (`null_edge_bps` — buy every slot, direction ignored). `edge_over_null_bps = gross_edge_bps − null_edge_bps` is the edge attributable to the signal itself, net of whatever the underlying asset's drift alone contributed. **A cell whose signal does not clear `min_edge_hurdle_bps` on edge_over_null_bps is excluded from PASS**, shown as `loses_to_null` in Cost-Gate Passers / Coverage Table — even when its gross/net edge, cost gate, FDR, impact, and OOS persistence all pass. This gate exists because a strongly-trending name (e.g. PTRO, up ~30x in-sample) makes any long-biased rule look brilliant on gross edge alone: measured against its own always-long null, PTRO's signal contributed +45.1 bps against a 110.2 bps hurdle — it lost to buy-and-hold. cross_sectional and pairs cells have no long-only null (structurally long/short — long_short_infeasible) and are exempt; a cell IN scope with `edge_over_null_bps=None` is treated as a measurement failure and blocked, never silently passed. See strategy_fit/report.py:_clears_null_gate and NULL_GATE_REQUIRES_SIGNAL_CLEARS_HURDLE.
- **Cross-sectional family:** daily Spearman IC between trailing-return rank and forward return; eff_n = independent cross-sectional periods. Cost floor: median min_edge_to_clear_bps (market-neutral L/S; see families_cross_sectional.py).
- **Pairs family:** Engle-Granger cointegration on the top-15 most-liquid names; spread half-life estimated via AR(1); OOS block-bootstrap on non-overlapping spread observations. Cost floor: sum of both legs' min_edge (each leg is a separate order).
- **Intraday same-day families:** ORB (30-min opening range), intraday momentum (morning-to-afternoon), VWAP mean-reversion. hold=0 → financing=0, hurdle = min_edge + impact. One obs per trading day (no de-overlap needed).
- **Deferred families:** order_book — see plan-10 §7.9. (cross_sectional and pairs are now testable.)

