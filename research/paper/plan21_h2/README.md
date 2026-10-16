# Plan 21 H2 paper forward log (PAPER ONLY, no orders)

> **STOPPED 2026-10-16** by the owner after the MSCI_ST holdout FAIL (plan 21 §10.2). The ledger was never used and the reminders are removed. The code is kept, tested and inert, in case a future candidate needs an event-driven paper log.

Frozen rule: plan 21 §3, §4.3, §9a. Ledger: `ledger.csv` (tracked, append-only; units are bps).
Run everything from `research/`. Per MSCI review (example Nov-2026, announced 2026-11-11):

1. Morning after the announcement: `.venv/bin/python -m index_events.paper_log fetch --review Nov26` (refuses to overwrite; non-zero if MSCI has not published).
2. `.venv/bin/python -m index_events.paper_log log --review MSCI-2026-11` (append-only; refuses once entry is in the past). It prints p, entry and k5/k10/k20 sessions.
3. Any name printed as UNMAPPED: add it to `index_events/msci_ticker_map.py` (confidence not `low`) before the entry close, then run `update` (it records `resolved_ticker`; later than the entry close it becomes `excluded_resolved_late`).
4. After the close of p+1 (entry session): refresh bars with the printed line (`python -m screener.daily_store snapshot --symbols ...`, your own token in the environment; includes IHSG), then `update`.
5. After each exit session (k5, k10, k20): refresh bars the same way, `update`, and at k20 `scorecard` (`--json` available).

**Optional live 1-lot execution check (your own manual orders; the paper rows stay the record):**
record each side once, right after the fill:
`.venv/bin/python -m index_events.paper_log record-fill --review MSCI-2026-11 --ticker CPIN --side buy --px 5050 --lots 1`
and at the k20 exit the same with `--side sell`. Fees use 0.15% buy / 0.25% sell. `scorecard` then shows live net (Rp and bps)
next to paper 1-lot on the same rows, plus entry/exit slippage vs the paper close. Dividends received are not added.

`update` never edits the planned columns, never overwrites a recorded price, and rewrites the file atomically. Eligibility/suspension/entry checks, cost (Rp 3M and 1 lot, current `impact_coeffs.json`) and dividend handling reuse the harness. Paper only: this module must not import order-placing code (enforced by `test_paper_log.py`).
