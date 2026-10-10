# ADR 009 — External MCP servers as connectors

**Status:** Accepted integration direction.

## Decision

Represent an external MCP server as an outbound connector with declared transport, authentication, tools, and lifecycle. Keep this separate from Integral's inbound MCP surface for external agents.

## Consequences

Mounting does not create another principal plane or agent-to-agent fabric. Qualify reauthorization, source changes, permission, revocation, and failure behavior. See [connectors](../connectors.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
