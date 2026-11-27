# ADR 010 — Connector subsystem boundaries

**Status:** Current architecture boundary.

## Decision

Keep native synchronization, external MCP mounts, credential resolution, package metadata, and lifecycle as distinct responsibilities behind the connector abstraction. Preserve explicit source identity and policy. One server-admitted, versioned registration drives discovery and management for a connector; installed instances, effective health, and capability-specific execution remain distinct. Core owns scope, permissions, and lifecycle policy. See the [Connector Standard v1 and migration queue](../connector-extension-standard-v1.md).

## Consequences

Native record synchronization deduplicates before writing and handles conflicts explicitly. MCP tools remain external effects with their own trust perimeter. Registry presence does not qualify every service. See [connectors](../connectors.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
