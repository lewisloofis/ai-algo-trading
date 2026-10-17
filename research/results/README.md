# research/results/

One folder per study run, committed (reports and small CSV/JSON; large
`strategy_portfolio.json` sidecars and run logs are gitignored).

| folder | what | plan |
|---|---|---|
| `obi_ofi/` | plan 06 tick-level OBI/OFI result (verdict only) | 04, 05, 06 |
| `intraday/` | plan 10 intraday / ORB result (verdict only) | 10 |
| `d1/` | daily re-score after the D1 entry-timing fix (`next_close/`, `next_open/`, `lookahead_legacy/`) | 10, 20 |
| `d1_xsec/` | cross-sectional re-score after D1 | 10 |
| `costwin/` | cost-window comparison (legacy vs point-in-time impact inputs) | 10 |
| `idx_structural/` | IDX structural event families | alpha-search 2026-10 |
| `shortlist/` | 2026-10-15 first-candidate shortlist (read by the dashboard) | 10 |
| `index_events/` | plan 21 coverage tables and event-time plots | 21 |
| `index_rebalance/` | plan 21 H2 development and MSCI_ST holdout | 21 |
| `indicator_rules/` | plan 24 classic indicator rules, development run (holdout unspent) | 24 |
| `fixture/` | local fixture run (gitignored) | — |

A study that decides a strategy family also holds a `verdict.json` (schema in `strategy_fit/study_verdicts.py`; written with `python -m strategy_fit.study_verdicts record`, see plan 25). The triage report and the dashboard read it.

Live runner output stays in `research/out_live/`; older loose script output is in `research/out/` (gitignored).
