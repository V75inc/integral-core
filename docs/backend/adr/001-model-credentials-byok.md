# ADR 001 — Server-side model credentials

**Status:** Accepted boundary.

## Decision

Resolve platform/BYOK credentials under host policy on the server. Stored keys are encrypted, provider-specific, and unavailable to browser payloads. Hybrid, strict BYOK, and platform-only policy remain distinct.

## Consequences

A user preference cannot override host credential authority. Rotation, missing keys, provider validation, and route changes need explicit tests. See [credentials](../model-credentials-byok.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
