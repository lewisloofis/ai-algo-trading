# Thesis — Research Notes

Research notes that record the **academic thesis** behind a signal or strategy
*before* we build it: why it should work, the exact formulas we will implement,
the citations, and the open question of whether the result transfers to IDX /
Stockbit market structure.

These are not implementation plans (see [`../plans/`](../plans/)) or decisions
(see [`../adr/`](../adr/)) — they are the *evidence base* a study or strategy is
built on, so the code has a documented rationale and we know what would falsify it.

## Contents

| Doc | What it covers |
|-----|----------------|
| [01-microstructure-signals.md](./01-microstructure-signals.md) | Thesis, formulas, and citations for **order-book imbalance (OBI)** and **micro-price** — the two first signals to study on captured BUMI data, plus the open question of whether developed-market results transfer to a thin 1-tick IDX name. |
