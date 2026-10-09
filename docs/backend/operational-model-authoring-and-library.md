# Model authoring and the App library

Author reusable packages on disk and expose them through configured package roots. Library models are catalog artifacts, not installed workspace instances. Their visibility and detail reads must follow package/resource authorization; a broad library query must not expose private package metadata.

Use authoring YAML version 3, package identity, tags, declared assets, and standard skill files. The loader compiles runtime schema version 2 and reports load/signature issues. `INTEGRAL_CORE_ONLY=1` admits only Core packages; it does not disable agentive boot.

The library can help people choose reusable structure. Type-hint routing uses authorized catalog metadata rather than a Core table of domain names. Ambiguous top choices require an explicit selection; caller-specified picker fields must not silently conflict.

Install Apps through the canonical lifecycle. Do not approximate installation by creating an App and merging only its schema: that omits definitions, settings, seeds, skills, tools, and registration work. A library update needs deliberate instance upgrade/merge handling.

Drafts and published models have distinct visibility and authority. Derived packages stamp provenance. Detach and revert affect the directly attached model under their contract, rather than silently cascading into every child model. Revert checks existing-record impact before destructive application.

Use the [quickstart](../developer/quickstart.md), [composition guide](../operational-models/COMPOSITION_PATTERNS.md), and [draft lifecycle](../operational-models/DRAFT_PUBLISH.md).
