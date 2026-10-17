# Research Verdicts Feed the Dashboard

> **Status:** 🔨 In progress · steps 1–2 built 2026-10-17, step 3 not started · **Owner:** @lewis · **Created:** 2026-10-17
> **Related:** [19-observability-and-dashboard.md](./19-observability-and-dashboard.md) (the dashboard) · [16-strategy-triage-and-prioritization.md](./16-strategy-triage-and-prioritization.md) (the family catalog) · [../../research/results/README.md](../../research/results/README.md)

> **In plain words.** Research happens in Claude Code sessions: a study runs, we
> read the numbers, and we write the verdict into the plan's results log. The
> dashboard never sees any of that. Its Strategies page only reacts to bot run
> stores and price files, and the outcome of each study exists only as prose.
> This plan makes every study verdict a small machine-readable file next to the
> study's output, has the dashboard read those files, and has the live
> dashboard rebuild when one appears.

---

## 1. Problem

1. **No structured outcome.** A family's result (FAIL, NO_GO, INCONCLUSIVE…)
   lives in `docs/plans/README.md` and in each plan's §10 log. The dashboard
   cannot parse prose, so the outcomes were first hand-copied into
   `strategy_fit/catalog_meta.py`, which goes stale on the next study.
2. **The live dashboard does not watch research.** `dashboard serve` rebuilds
   when `runs/*.db`, the daily parquet files or a heartbeat's outcome change.
   A new `research/results/` file, a plan edit or a commit does not trigger
   anything, so research finished an hour ago shows up only on the 5-minute
   periodic rebuild, and only if a hand-maintained file was updated.

## 2. Design

### 2.1 Verdict files (step 1)

A verdict is a **decision**, recorded after reading a study's numbers. It is
not recomputed by the study runner (plan 21's verdict needed a human read of
a holdout, and that cannot be derived from one JSON).

* **Where:** `research/results/<study>/verdict.json`, next to that study's
  output. One study may decide several families (the shortlist decides seven).
  Committed, like the rest of `research/results/`.
* **Shape:**

  ```json
  {"schema": 1,
   "verdicts": [
     {"family_id": "indicator_rules", "outcome": "FAIL", "decided": "2026-10-17",
      "source": "plan 24 §10.1", "doc": "docs/plans/24-classic-indicator-rules.md",
      "summary": "all 5 textbook rules FAIL development (Holm p ≥ 0.32)"}]}
  ```

  `outcome` ∈ `PASS | FAIL | NO_GO | INCONCLUSIVE`. `family_id` must exist in
  `catalog_meta.ALL_FAMILIES`. `doc` is repo-relative and must exist.
* **Write:** `strategy_fit/study_verdicts.py record …` (CLI) validates and
  upserts one verdict into a study's file, so a session closing a study runs
  one command next to the §10 log entry.
* **Read:** `load_verdicts(results_dir)` reads every `*/verdict.json`. When two
  studies decide the same family, the latest `decided` date wins; a tie is
  reported as a problem instead of being silently resolved. An unreadable or
  invalid file is reported by path and reason; the other files still load.
* **Catalog:** `catalog_meta.py` keeps family definitions only. A family with
  no verdict is `untested`.

### 2.2 Consumers (step 1)

* **Triage report** (`strategy_fit/triage.py`): the "Test outcome" column and
  Section 3 come from the verdict files.
* **Dashboard Strategies page**: the "Strategy families we consider" headline
  and the first evidence line ("Test (date, source): summary") come from the
  verdict files. Problems with a verdict file are shown on the page as
  "Not checked: …", never as an empty result.

### 2.3 Watch research (step 2)

`dashboard serve`'s fingerprint also covers:

* every `research/results/*/verdict.json` (content hash, so a re-save with
  no change does not rebuild),
* `docs/plans/*.md` (mtime),
* the current git commit (`.git/HEAD` and the ref it points to).

A verdict recorded in a Claude Code session then reaches the live page at
the next poll plus the settle gap (seconds), not the 5-minute periodic build.

### 2.4 Research activity feed (step 3, not started)

A "Research activity" section built from git commits and verdict files, and
optionally a Claude Code hook that appends to `runs/research_log.jsonl` when
a study starts or finishes, so in-progress research shows as "running" like
the bot's Online panel. It needs a hook in the owner's Claude Code settings,
so it waits for an explicit go.

## 3. Backfill

The outcomes already decided are written as verdict files with the dates
they were decided (from git history and the plans' §10 logs):

| study folder | families | decided | source |
|---|---|---|---|
| `obi_ofi/` (new) | obi_ofi_tick | 2026-06-19 | plan 06 (NO_GO); plans 04, 05 |
| `intraday/` (new) | intraday_momentum | 2026-09-05 | plan 10 (ORB / intraday killed) |
| `d1_xsec/` | cross_sectional | 2026-10-14 | plan 10 D1 cross-sectional re-score |
| `idx_structural/` | factor_books, limit_move_events, dividend_events, calendar_effects | 2026-10-15 | alpha-search 2026-10 |
| `shortlist/` | daily_momentum, mean_reversion, event_driven_gap, pairs_stat_arb, volume_breakout, volume_spike_event, foreign_flow_spike_event | 2026-10-15 | first-candidate shortlist |
| `index_rebalance/` | index_rebalance_deletion | 2026-10-16 | plan 21 §10.2 |
| `foreign_flow/` | foreign_selling_rebound | 2026-10-17 | plan 22 §10.2 |
| `indicator_rules/` | indicator_rules | 2026-10-17 | plan 24 §10.1 |

`multi_week_trend`, `multi_month_macro` and `multi_year_carry` stay
`untested`.

## 4. Done when

* [x] `study_verdicts` load/validate/record with tests (bad outcome, unknown
      family, missing doc, tie, unreadable file).
* [x] Backfill files committed; triage report and dashboard read them.
* [x] `catalog_meta` carries no outcome fields.
* [x] `dashboard serve` rebuilds on a verdict, plan or commit change (tests).
* [ ] Step 3 decided.

## 5. Rule for future studies

When a study's verdict is decided, in the same session and commit as the
plan's §10 entry:

```bash
cd research && python -m strategy_fit.study_verdicts record \
  --study <results folder> --family <family_id> --outcome FAIL \
  --decided YYYY-MM-DD --source "plan NN §10.x" \
  --doc docs/plans/NN-….md --summary "<one line with the deciding numbers>"
```

A new family is added to `catalog_meta.ALL_FAMILIES` (and its words to
`dashboard/strategy_words.py`) before its verdict is recorded.
