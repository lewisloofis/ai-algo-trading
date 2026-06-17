# Architecture Decision Records

This directory records significant architecture and process decisions for the
algo-trading project, using a lightweight [ADR](https://adr.github.io/) format.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](./0001-treat-plans-as-tickets.md) | Treat planning docs as tickets | Accepted |

## Conventions

- **Filename:** `NNNN-kebab-title.md`, zero-padded sequential (`0001`, `0002`, …).
- **Sections:** Status, Date, Context, Decision, Consequences.
- **Status values:** `Proposed` → `Accepted`; later `Deprecated` or `Superseded`.
- **Never rewrite history:** to reverse a decision, add a new ADR and mark the old
  one `Superseded by [NNNN]` with a link, rather than editing or deleting it.
