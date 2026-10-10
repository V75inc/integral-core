# ADR 006 — Bounded unstaged observations

**Status:** Accepted narrow boundary.

## Decision

Permit only create/update row writes to manifest-declared unstaged Track keys in an active trusted/audited App, in the acting owner's own personal workspace. All clauses must pass; error or ambiguity stages the write.

## Consequences

The exemption is declared by the App rather than a hardcoded Core slug. Fact changes and other Tracks retain review. Preserve attached-manifest declarations through merges and apply the cap on exempt keys. See I-PC-01 in [invariants](../../INVARIANTS.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
