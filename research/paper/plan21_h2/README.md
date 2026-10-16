# Plan 21 H2 paper forward log (PAPER ONLY, no orders)

Frozen rule: plan 21 §3, §4.3, §9a. Ledger: `ledger.csv` (tracked, append-only; units are bps).
Run everything from `research/`. Per MSCI review (example Nov-2026, announced 2026-11-11):

1. Morning after the announcement: `.venv/bin/python -m index_events.paper_log fetch --review Nov26` (refuses to overwrite; non-zero if MSCI has not published).
2. `.venv/bin/python -m index_events.paper_log log --review MSCI-2026-11` (append-only; refuses once entry is in the past). It prints p, entry and k5/k10/k20 sessions.
3. Any name printed as UNMAPPED: add it to `index_events/msci_ticker_map.py` (confidence not `low`) before the entry close, then run `update` (it records `resolved_ticker`; later than the entry close it becomes `excluded_resolved_late`).
4. After the close of p+1 (entry session): refresh bars with the printed line (`python -m screener.daily_store snapshot --symbols ...`, your own token in the environment; includes IHSG), then `update`.
5. After each exit session (k5, k10, k20): refresh bars the same way, `update`, and at k20 `scorecard` (`--json` available).

`update` never edits the planned columns, never overwrites a recorded price, and rewrites the file atomically. Eligibility/suspension/entry checks, cost (Rp 3M and 1 lot, current `impact_coeffs.json`) and dividend handling reuse the harness. Paper only: this module must not import order-placing code (enforced by `test_paper_log.py`).
