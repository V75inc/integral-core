# Operational Models

An Operational Model describes the record schema and composition attached to an App or Track. It gives information a useful shape without making Core domain-specific.

## Model deliberately

A Track holds a collection; an Entry represents one operational item. Use typed fields for validated values, relations for connected entities, member fields for accounts, and file fields for attachments. Keep independently governed entities out of nested JSON collections.

Use sibling Tracks when collections have independent lifecycles. Use anchors when a record needs a template-provisioned related collection inside its workspace. Different audiences require separate resource boundaries rather than hidden fields.

## Four complementary mechanisms

1. **Schema:** EntryTypes, field definitions, required values, and validation.
2. **Views and composition:** registered views, declarative composites, and reusable regions.
3. **Lifecycle:** attached models, drafts, publication, impact checks, and migrations.
4. **Application extension:** packages can add declared queries, operations, tools, skills, and presentation through Core's perimeter.

Current package YAML uses `integral_operational_model_version: 3`; compiled manifests use `operational_model_schema_version: 2`. An Operational Model is not the entire installed App authority: the immutable active ApplicationDefinition also governs its capability contract.

## Reading path

- [Composition patterns](COMPOSITION_PATTERNS.md)
- [View palette](VIEW_PALETTE.md), [composites](COMPOSITES.md), and [regions](REGION_SYSTEM.md)
- [Draft and publish](DRAFT_PUBLISH.md), then [migrations](MIGRATIONS.md)
- [Agent authoring contract](AGENT_CONTRACT.md)
- [UI components](UI_COMPONENTS_GUIDE.md), [packs](UI_PACKS.md), and [plugins](PLUGINS.md)

The [App quickstart](../developer/quickstart.md) provides the runnable package example. [Invariants](../INVARIANTS.md) preserve the substrate constraints.
