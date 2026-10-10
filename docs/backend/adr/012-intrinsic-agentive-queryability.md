# ADR 012 — Queryable application information

**Status:** Current query contract.

## Decision

Prefer scoped get/query/invoke and explicit information shapes over leaking raw graph entities to packages. Queries distinguish declared-capability and Core-open modes, with resource authorization in either path.

## Consequences

Field namespaces, relation targets, revisions, rows, and totals must remain explicit. A query mode or output row cannot manufacture drill-through authority. See [extension contract](../../platform/extension-contract-v1.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
