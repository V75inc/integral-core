# Author an App package

Begin with `examples/reference-hello-app/` and the [developer quickstart](../developer/quickstart.md). Work in an external package directory whose name matches its slug.

Describe the purpose in package metadata and discovery tags. Declare Tracks, EntryTypes, fields, and views in authoring YAML version 3. Separate related operational entities into records and relations, and place differently governed information behind separate resources.

Add queries and operations with explicit shapes and policy. Bind tools and hooks through declarations; implement tools against the scoped facade. Add standard SKILL.md files whose descriptions match the manifest. Use declarative composites or the declared iframe extension surface for package-owned presentation.

Validate compile, file containment, dependencies, signatures where required, installation, access denial, result readback, pause, and removal. A package is useful when its whole contract works, not merely when the YAML parser accepts it.

See [bundle architecture](app-bundles-v1.md), [model lifecycle](../operational-models/DRAFT_PUBLISH.md), and [qualification](../ops/QUALIFICATION.md).

## Track descriptions

Every newly provisioned `app.tracks[]` item and newly declared
`app.track_templates[]` item needs a meaningful `description`: explain what a
record represents, why the Track exists, and how its users work with it.
Package/App descriptions do not substitute for Track descriptions. Empty text,
placeholders, and a repeated name are rejected before new creation. Descriptions
are stored as Track `purpose`, including per-record anchored detail Tracks.

For legacy packages, existing Tracks and template declarations remain readable
and may receive metadata updates. Add descriptions to their manifest before
provisioning new Tracks or creating new anchored detail records. A normal
package update fills blank descriptions and refreshes manifest-owned wording;
it preserves descriptions edited by users. No automatic destructive migration
or generic description generation occurs. Keep the package update separate from
any data/content change and verify the descriptions after applying it.
