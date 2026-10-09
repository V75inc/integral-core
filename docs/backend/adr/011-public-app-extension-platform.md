# ADR 011 — Public application capabilities

**Status:** Current extension boundary.

## Decision

Apps expose declared operations and information through public facade contracts. Reviewed presentation uses registered composites or the sandboxed iframe view host. Domain behavior stays outside Core.

## Consequences

The iframe surface is implemented; it must not be described as universally deferred. Arbitrary remote React plugins remain outside this contract. Inputs, current access, active definitions, receipts, and lifecycle withdrawal need tests. See [extension contract](../../platform/extension-contract-v1.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
