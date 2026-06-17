# ADR 0001 — Treat planning docs as tickets

- **Status:** Accepted
- **Date:** 2026-06-17
- **Deciders:** Lewis
- **Tags:** process, documentation

## Context

Planning documents under `docs/plans/` have grown into multi-phase specs that are
implemented over time — the market-data capture plan, for example, spans three
phases and is only partially built. This repo has no issue tracker wired into it,
but we still want lightweight, in-repo visibility into:

- whether a plan is just an idea, agreed, in flight, or finished — a **status**;
- how plans relate to each other and to decisions — **links**;
- which concrete steps remain — a **task checklist** we can tick off.

GitHub-flavoured Markdown already renders `- [ ]` task lists with progress bars,
and the plans already group work into phases. We want to formalise this so every
plan reads like a ticket, without adopting heavyweight tooling.

## Decision

Treat each plan in `docs/plans/` like a ticket.

1. **Status header.** Every plan begins, immediately after its H1, with a metadata
   block:

   ```markdown
   > **Status:** 🔨 In Progress · **Owner:** @lewis · **Updated:** 2026-06-17
   > **Related:** [sibling-plan.md](./sibling-plan.md) · [Plans index](./README.md) · [ADR-0001](../adr/0001-treat-plans-as-tickets.md)
   ```

2. **Status vocabulary** (one per plan; phase-level progress is shown by the
   checklists, not the status):

   | Status | Emoji | Meaning |
   |--------|-------|---------|
   | Draft | 📝 | Idea captured, not yet agreed |
   | Approved | ✅ | Agreed; spec is stable / ready to build |
   | In Progress | 🔨 | Being implemented |
   | Blocked | ⛔ | Waiting on an external dependency or decision |
   | Done | ✔️ | Fully implemented and verified |
   | Superseded | 🗑️ | Replaced by another plan (link it) |

3. **Task checklists.** Implementation steps inside each phase are GitHub task-list
   checkboxes (`- [ ]` / `- [x]`), not ordered numbers, so remaining work is
   visible and tickable in any Markdown viewer. The phase grouping
   (`### Phase N — …`) is retained — phases are the epics, checkboxes are the tasks.

4. **Index as a board.** `docs/plans/README.md` carries a **Status** column and a
   legend so the directory reads like a board overview.

5. **Decisions are ADRs.** Architecture and process decisions live in `docs/adr/`
   (this is the first), each with its own `Status` field.

## Consequences

**Positive**

- Plan state and remaining work are visible at a glance, in-repo, and reviewable in
  pull requests alongside the code that fulfils them.
- No external tooling or sync; checkboxes render natively on GitHub and in editors.
- Cross-links make the plan set navigable and tie work back to its governing decision.

**Negative / costs**

- Statuses, `Updated:` dates, and checkboxes are maintained by hand and can drift
  from reality. *Mitigation:* tick the boxes and bump the status in the **same**
  change that does the work.
- This is not a substitute for a real issue tracker — no assignees at scale,
  automation, or cross-repo queries. It is deliberately minimal.

## Initial application

Applied to `docs/plans/01-market-data-capture.md` (status header + phase checklists),
`docs/plans/02-storage-format-and-replay.md` (status header + links), and
`docs/plans/README.md` (status column + legend). New plans follow the same shape.
