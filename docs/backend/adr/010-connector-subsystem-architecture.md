# ADR 010 — Connector subsystem boundaries

**Status:** Current architecture boundary.

## Decision

Keep native synchronization, external MCP mounts, credential resolution, package metadata, and lifecycle as distinct responsibilities behind the connector abstraction. Preserve explicit source identity and policy.

## Consequences

Native record synchronization deduplicates before writing and handles conflicts explicitly. MCP tools remain external effects with their own trust perimeter. Registry presence does not qualify every service. See [connectors](../connectors.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
