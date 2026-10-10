# ADR 007 — Preserve review outcomes

**Status:** Accepted decision-recording boundary.

## Decision

Record relevant terminal review outcomes through the declared personal-context mechanism while the staged proposal still has its scope and human-visible diff. Decision records belong in their dedicated App Track rather than a mixed belief collection.

## Consequences

A record of review is separate from effect completion. Expiry and rejection need honest outcomes, and a reserved edited-then-approved value must not imply an editing path was exercised. Core must not acquire App-specific identity branches. See I-PC-01 in [invariants](../../INVARIANTS.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
