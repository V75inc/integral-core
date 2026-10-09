# Author an App package

Begin with `examples/reference-hello-app/` and the [developer quickstart](../developer/quickstart.md). Work in an external package directory whose name matches its slug.

Describe the purpose in package metadata and discovery tags. Declare Tracks, EntryTypes, fields, and views in authoring YAML version 3. Separate related operational entities into records and relations, and place differently governed information behind separate resources.

Add queries and operations with explicit shapes and policy. Bind tools and hooks through declarations; implement tools against the scoped facade. Add standard SKILL.md files whose descriptions match the manifest. Use declarative composites or the declared iframe extension surface for package-owned presentation.

Validate compile, file containment, dependencies, signatures where required, installation, access denial, result readback, pause, and removal. A package is useful when its whole contract works, not merely when the YAML parser accepts it.

See [bundle architecture](app-bundles-v1.md), [model lifecycle](../operational-models/DRAFT_PUBLISH.md), and [qualification](../ops/QUALIFICATION.md).
