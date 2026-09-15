# Declarative Composite Types

Pillar 1 of the agent-authorable substrate. Composites let a content
profile manifest declare new field/view types that decorate an existing
primitive with extra config — no code, no plugin install, no platform
restart.

## Why

Most "new types" the agent will ever need (`currency`, `email`,
`pipeline_stage`, `roadmap`) are not genuinely new renderers; they are
specific configurations of an existing primitive. Composites capture that
specificity *in the manifest itself*, so they ship with the profile and
the agent can introduce them without permission to install code.

## Manifest shape

```yaml
content_profile_schema_version: 1
scope: track

field_types:
  - key: currency
    base: number
    config: { currency: USD, min: 0 }
    label: "Currency"
    description: "Numeric value with currency code metadata."
  - key: email
    base: text
    config: { pattern: '^[^@]+@[^@]+\.[^@]+$' }

view_types:
  - key: roadmap
    base: composable_board
    config: { group_by: stage, color_by: priority, swimlanes: quarter }

track:
  entry_types:
    - name: Invoice
      fields:
        - { key: amount, type: currency }   # uses the composite above
        - { key: contact_email, type: email }
  views:
    - { name: Roadmap, view_type: roadmap }
```

`base` MUST resolve to a primitive registered in
`content_profile_field_types` / `content_profile_view_types`. Composites
referencing other composites are valid as long as the chain terminates at
a primitive (cycle detection raises `BadRequestError`).

## Backend resolution

`compile_canonical_manifest` (in `app/services/content_profile_runtime.py`)
parses `field_types[]` / `view_types[]` into per-compile registrations
held in a `ContextVar`, then resolves field / view dispatch through
`field_type_registry.resolve()` / `view_type_registry.resolve()`. Each
materialized field carries a `composite: { base, config, label, description }`
metadata block so the entry validator can dispatch on the primitive base
without re-resolving the manifest on every entry write.

## Frontend resolution

The frontend mirrors the backend pattern:

- `frontend/src/components/entries/fieldTypes/registry.ts` — field-type
  registry. `SeamlessField` consults it first, then falls back to the
  built-in primitive cascade. Composite fields with no custom renderer
  resolve to the primitive base (e.g. a `currency` field renders via the
  `number` cascade with `field.composite.config` available).
- `frontend/src/views/registry.tsx` — widget registry (composite-aware):
  a saved view whose `composite.base` matches a registered palette widget
  renders via that widget with the composite's config merged into `view.config`.
  See [VIEW_PALETTE.md](VIEW_PALETTE.md).

## Tests

- `backend/tests/test_content_profile_registries.py` — composite
  registration, resolution, error paths.

## Related docs

- [UI_PACKS.md](UI_PACKS.md) — when a "new type" genuinely needs new code
  (not just config on an existing primitive), a UI pack is the recipe.
