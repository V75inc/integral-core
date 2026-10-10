# ADR 013 — Operational Model vocabulary

**Status:** Current public terminology.

## Decision

Use Operational Model for schema/composition and App for the purposeful installed environment. Distinguish authoring package, library model, attached model, App instance, and active ApplicationDefinition.

## Consequences

Runtime compatibility discriminators or historical aliases do not justify publishing obsolete Space APIs as current authoring guidance. Disk YAML v3 and runtime schema v2 are separate interfaces. See [models](../../operational-models/README.md).

This edition removes superseded implementation narratives while preserving the decision boundary. Historical discussion remains in Git; current release evidence belongs in the qualification record.
