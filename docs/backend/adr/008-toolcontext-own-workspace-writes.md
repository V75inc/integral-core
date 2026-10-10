# ADR 008 — The caller's own personal workspace

**Status:** Accepted scoped facade boundary.

## Decision

A bundle tool may reach the acting caller's own personal workspace only through the generic scoped facade contract. Creation uses normal validation, permission, event, and graph-attachment paths. The tool cannot mutate its context to choose another tenant.

## Consequences

A cross-workspace observation use case does not authorize arbitrary cross-workspace reads or writes. Validate source access and the exact personal target. Private Core service imports remain forbidden. See [extension contract](../../platform/extension-contract-v1.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
