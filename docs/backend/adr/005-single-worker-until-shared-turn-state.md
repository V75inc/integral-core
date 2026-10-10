# ADR 005 — Conservative worker posture

**Status:** Single-worker deployment remains the supported baseline.

## Decision

Use one worker until shared turn admission and cross-worker event delivery are qualified together. Process-local turn counters and websocket maps cannot establish cluster-wide concurrency or fan-out guarantees.

## Consequences

Durable native mechanisms introduce shared-state paths, but their existence does not qualify all ordinary and compatibility journeys. Increasing WORKERS requires the full intended configuration's evidence, not a partial shared registry. See [qualification](../../ops/QUALIFICATION.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
