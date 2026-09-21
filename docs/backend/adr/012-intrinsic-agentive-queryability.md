# ADR-012: Intrinsic agentive queryability

**Status:** Accepted
**Date:** 2026-09-17
**Sprint:** [HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md](../../product/HARNESS_RUNTIME_SUBSTRATE_GAP_PLAN.md)

## Context

Integral already has graph access control, typed App operations (ADR-011), retrieval,
Operational Model introspection, and a hand-curated tool manifest. Discovery and read
paths remain fragmented. Agents and App views cannot rely on one permission-filtered
catalogue plus one governed query/command seam.

## Decision

### 1. Intrinsic agentive queryability

A substrate or App capability is incomplete until an authorized agent can discover it,
understand schema/effects, query state, invoke operations, and cite grounding objects.
Core must not add a hand-written agent tool for every App concept.

### 2. Capability catalogue

Every active Core or App contribution compiles into a versioned `CapabilityDescriptor`
catalogue (generation per workspace). Surfaces (HTTP, resident, MCP, extension bridge)
advertise the same permission-filtered snapshot. Failed capability compilation must not
leave an App silently `active` with a partial surface — use `activation_failed` (not
retrieval/connector `degraded`).

`tool_manifest.yaml` becomes an adapter/generated view of Core descriptors over time;
App capabilities are never hand-edited into that file.

### 3. QuerySpec v1 (locked C)

1. **Declared App queries** — `kind: query` capabilities with typed parameters; App owns plan.
2. **Open bounded Core queries** — Core primitives only; strict depth/cardinality/projection/policy.
3. **No generic open querying of App domain records** — fail closed unless an explicit declared capability.

### 4. Command seam

App operations remain the sole mutation path for protected App state. Generic Entry/Profile
writes that touch protected fields are rejected or routed to the required operation.

### 5. Evidence envelope

Query and operation results share object refs, schema/version, policy/audit correlation,
warnings, and cursor/degraded markers.

### 6. Operational Models

Draft/publish stays specialized. The catalogue may advertise CP capabilities; this ADR
does not fold propose/diff/publish into generic Query/Command.

## Consequences

- WP-01 schemas + SDK; WP-03 query engine; WP-05/06 catalogue; WP-04 guards; WP-08/09 surfaces.
- Asset Register is the continuous proof (declared warranty/availability queries + custody ops).
- Multi-worker invalidation port is specified; production adapter may follow (ADR-004/005).

## Alternatives considered

| Alternative | Rejected because |
| --- | --- |
| Open App-record QuerySpec in sprint 1 | Too permissive; leaks plan/control from Apps |
| Keep hand-curated tools per App concept | Does not scale; inventory ≠ protocol |
| Replace Operational Model draft/publish | Out of scope; specialized authoring remains valuable |
