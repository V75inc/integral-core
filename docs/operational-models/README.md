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

## Lifecycle-based read-only records

A trusted or audited App can declare `readonly_when` under `app.protected_state` for a named Entry type. The condition compares a saved custom field to a string. It can follow one single-valued relation to a declared type in the same installed App.

```yaml
app:
  protected_state:
    review:
      readonly_when: {field: phase, equals: closed}
    review_summary:
      readonly_when: {relation: review, related_entry_type: review, field: phase, equals: closed}
```

The typed Entry writer checks the rule on updates, retyping and individual deletion. The operation that moves an editable record into its terminal state can finish. The rule applies to standard fields such as title and body as well as custom fields. A missing state or unavailable related record keeps the Entry read-only. Existing permissions still govern access, including read access to the related record. The Entry detail and write responses expose `app_record_readonly` when the restriction applies. The UI refreshes that status when it reads the Entry.

Standard attachment writes, including App tool attachment saves, honor the same Entry policy. Existing attachments remain available under their normal read permissions. Action bar file downloads remain available and skip the optional save to a read-only Entry. Apps still define when an export is available and whether it writes other records.

The restriction is part of the installed App definition and adds no operation grant. Qualify an upgrade against existing records before using a new rule in an installed App. Protected App restore requirements also apply to this declaration. [App portability](../platform/resource-portability.md)

## Reading path

- [App settings UI contract](../platform/app-settings-ui.md): one standard dialog
  for all App-specific settings controls.

- [Composition patterns](COMPOSITION_PATTERNS.md)
- [Field presentation](FIELD_LAYOUT.md)
- [Package discovery and indexing](../backend/operational-model-search-index.md)
- [View palette](VIEW_PALETTE.md), [composites](COMPOSITES.md), and [regions](REGION_SYSTEM.md)
- [Draft and publish](DRAFT_PUBLISH.md), then [migrations](MIGRATIONS.md)
- [Agent authoring contract](AGENT_CONTRACT.md)
- [UI components](UI_COMPONENTS_GUIDE.md), [interface complements](UI_COMPLEMENTS.md), [packs](UI_PACKS.md), and [plugins](PLUGINS.md)

The [App quickstart](../developer/quickstart.md) provides the runnable package example. [Invariants](../INVARIANTS.md) preserve the substrate constraints.
