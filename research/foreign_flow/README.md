# foreign_flow (plan 22, Phase 1)

Data tooling only: universe, fetch, data checks. No returns are computed here.
Spec: `docs/plans/22-foreign-selling-rebound.md` sections 2 and 6. Output goes to
`research/data/foreign_flow/` (git-ignored with the rest of `research/data/`).

Run from `research/`:

```bash
.venv/bin/python -m foreign_flow.universe        # reads local bars only; writes universe_monthly.csv
set -a && source ../.env.local && set +a         # token: SB_ACCESS_TOKEN, only needed for fetch
.venv/bin/python -m foreign_flow.fetch --dry-run # planned call count, no network
.venv/bin/python -m foreign_flow.fetch           # resumable; rerun after a stop
.venv/bin/python -m foreign_flow.checks          # writes checks.md (plan 2.3 checks 1-5)
```

Make equivalents from the repo root: `make foreign-universe`, `make foreign-fetch`
(`FOREIGN_ARGS="--dry-run"`), `make foreign-checks`.

Notes:
* Fetch options: `--symbols A,B --start-year 2016 --end-year 2026 --sleep 1.0`.
* One call per symbol-year; completed past years are in `_manifest.json` and skipped; the
  current year is always refetched. 401 stops the run (refresh tokens, rerun); 403 stops with a
  WAF/User-Agent message.
* Universe: top 40 by median value traded over the last 60 sessions <= month-end (value =
  bars' `value` column, else close x volume), applied to sessions after that month-end.
* Tests: `.venv/bin/python -m pytest -q foreign_flow` (no network).
