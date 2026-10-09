# ADR 002 — Contributed skill trust

**Status:** Partially implemented; wider marketplace and external skill delivery remain design work.

## Decision

Untrusted contributions cannot introduce custom executable skills. Trust-tier checks and an optional declared-tool narrowing allowlist add safeguards to the existing per-call policy perimeter. A standard skill body is instruction content, not a grant.

## Consequences

The broader vetting pipeline and delivery of App instructions to external agents are not established by these guards. Preserve the distinction between on-disk standard frontmatter and runtime capability metadata. See [skill format](../skill-format-standard.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
