# Closed-Family Re-check

> **Status:** 📝 Draft 2026-10-17 · not built; build when the first daily family nears 30 post-verdict observations (≈ Dec 2026–Jan 2027) · **Owner:** @lewis · **Created:** 2026-10-17
> **Related:** [25-research-verdicts-feed-the-dashboard.md](./25-research-verdicts-feed-the-dashboard.md) (verdict files, research log, §2.5 manual-trigger decision) · [22-foreign-selling-rebound.md](./22-foreign-selling-rebound.md) · [24-classic-indicator-rules.md](./24-classic-indicator-rules.md) · [21-index-rebalance-event-study.md](./21-index-rebalance-event-study.md) · [11-decay-monitoring-and-live-revalidation.md](./11-decay-monitoring-and-live-revalidation.md) (live strategies; this plan is for closed ones)

> **In plain words.** Every strategy family we tested failed or was
> inconclusive. Markets change, so a dead idea can come back. This plan re-runs
> each closed family, **with the exact rules it failed under**, on prices that
> arrived **after** its verdict, which it has never seen. If a family starts
> clearing its costs on that new data, we get told. Being told is not a pass:
> it only earns the family a fresh pre-registered test. Run when the owner
> asks (no schedule). Nothing here places an order.

---

## 1. What is re-checked

Every family whose verdict file (plan 25) says `FAIL`, `NO_GO` or
`INCONCLUSIVE`, using:

* **Frozen rules.** The spec the verdict was decided under: same signal,
  parameters, holds, entry convention (`next_close` unless the study fixed
  another), cost stack and always-long null. No re-tuning, no new holds. The
  frozen spec is recorded per family in `research/recheck/specs.py`
  (new), pointing at the existing family code and the source plan section.
* **Post-verdict data only.** Signals dated strictly after the verdict's
  `decided` date. Earlier data has already been judged.
* **Current costs.** The cost stack as it stands at the re-check
  (`impact_coeffs.json`, fees, financing). A family must clear today's cost,
  not the one it failed under. Each run records which coefficient file it used.

### 1.1 Families that are excluded or special

| family | treatment | why |
|---|---|---|
| foreign_selling_rebound (22), indicator_rules (24) | **excluded (owner, 2026-10-17)** | their unspent holdouts run from 2023-01-01 to the day the holdout is run, so post-verdict data *is* holdout data. Re-checking would peek at the holdout. The owner either spends the holdout (one shot, plan 22/24 rules), or declares it forfeited and the family joins the re-check. |
| index_rebalance_deletion (21) | re-check MSCI_ST on new deletion events only | MSCI_ST holdout is spent; the other cells' 2022–2025 holdout stays unseen and is not touched. Events are a few per year, so this will report "waiting" for years. |
| obi_ofi_tick (06) | report "waiting" with a reason | needs tick capture of the original names; re-check only on captured sessions after 2026-06-19, and only if the collector holds them |
| pairs_stat_arb | not re-checked | fails structurally (no shorting), not on data. Revisit only if shorting becomes possible. |
| limit_move_events, factor_books | re-check with their regime split reported | both leads are regime-dependent (ARB rebound: bear only; 12-1 momentum: non-bear). The regime flag is computed by the alpha-search code and reported, not used to filter after the fact. |
| untested families | not re-checked | nothing to re-check; they need a first study |

## 2. When a result counts

* **Milestone looks only.** A family is evaluated only when its
  post-verdict effective N first reaches **30, 60 and 120** independent
  observations (the family's natural unit and de-overlap rule, as in plan 10).
  Between milestones the re-check prints "waiting, N of 30" and decides
  nothing. This caps each family at 3 looks.
* **Alert bar (frozen here).** At a milestone, a family alerts only if all hold:
  1. edge over the always-long null, after current cost, > 0;
  2. one-sided p of that edge < **0.10 / (3 × number of families re-checked)**
     (Bonferroni over every family and every look; with 15 families that is
     p < 0.0022, z ≈ 2.85);
  3. still > 0 after dropping its 5 best trades.
* **What an alert does.** It writes an `INCONCLUSIVE`-or-better verdict only
  through a **new pre-registered plan** (template: plan 22). The re-check
  itself records its findings in the research log, not as a verdict that
  changes a family's outcome, so a lucky month cannot flip the dashboard to
  PASS.

## 3. How a re-check is run (manual)

1. Owner asks: "run the closed-family re-check".
2. Claude logs `research_log start --study recheck_<YYYY-MM> --plan "plan 26"`.
3. `make refresh-prices` (daily bars current).
4. `cd research && python -m recheck.run --as-of <date>` prints, per family:
   verdict date, post-verdict effective N vs the next milestone, and at a
   milestone the edge, cost, p and drop-5 result, plus ALERT / no alert.
   Output is written to `research/results/recheck/<YYYY-MM-DD>.{md,json}`
   (committed).
5. Claude logs `research_log finish` with outcome `INCONCLUSIVE` (nothing
   crossed or nothing at a milestone) or notes the alerting family, and
   records a milestone result for each family that reached one.
6. On an alert: draft the new pre-registered plan for that family. No money
   moves before it passes.

## 4. Build tasks (when triggered)

* [ ] `research/recheck/specs.py`: one frozen spec per re-checkable family,
      each citing its source plan section and family code; test that every
      closed family in the verdict files is either specced or listed in §1.1
      with its reason.
* [ ] `research/recheck/run.py`: post-verdict window, de-overlap, milestone
      logic, alert bar, output files. Poison test: no signal dated on or
      before the verdict date is ever read.
* [ ] Milestone ledger in `research/results/recheck/milestones.json` so a
      milestone is evaluated once (a second run at the same N refuses).
* [ ] Dashboard: the families table shows "re-check: N of 30" per family.

## 5. Open questions for the owner

None open. Both answered 2026-10-17 (§6).

## 6. Decision log

| date | decision | by |
|---|---|---|
| 2026-10-17 | Manual trigger, no schedule (plan 25 §2.5). Draft written; build deferred until post-verdict data accumulates. | @lewis (owner; instructed in session, entered by Claude) |
| 2026-10-17 | Plans 22 and 24 stay **out** of the re-check; their holdouts stay unspent. Milestones stay **30 / 60 / 120** (3 looks per family). | @lewis (owner; instructed in session, entered by Claude) |
