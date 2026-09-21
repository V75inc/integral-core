# Operational Model Packages and Migrations

For step-by-step authoring, PATCH APIs, and how to add packages to the **App Catalog**, see [operational_model_authoring_and_library.md](operational-model-authoring-and-library.md). For the full reference on App-scoped operational sections (skills, agents, settings, seeds, permissions), see [app_bundles_v1.md](app-bundles-v1.md).

OperationalModel manifests support package metadata and forward-only migration descriptors. Manifest schema version is **v2**; v1 has been removed (integral is pre-production, no compatibility shim retained).

Integral baseline is intentionally constrained to `Post` + `Feed`. Package libraries are the intended path for domain expansion. The two flavors:

- **App suites** (`scope: app`, `library_package: true`) — install a coherent operational domain into a Workspace (e.g. CRM, Projects, Content Factory)
- **Track packs** (`scope: track`, `library_package: true`) — customize a single Track's schema (e.g. Content Calendar)

## Package Metadata

Use the top-level `package` object for distribution-level metadata:

- `name` — package identifier (kebab-case)
- `version` — semver; surfaces in the App Catalog
- `publisher` — author or organization
- `description` — one-paragraph user-facing description
- `tags` — discovery keywords surfaced in catalog search
- `license` — defaults to MIT for community contributions; consulting-deliverable Apps may use proprietary
- `homepage` — optional URL
- `source` — optional URL to source/repo
- `capabilities` — optional list of capability flags
- `trust_tier` — `community` | `trusted` (only `trusted` Apps may include `kind: custom` skills; see [app_bundles_v1.md §11.1](app-bundles-v1.md#111-security-boundary))

## Migration Metadata

Use `migrations[]` entries for schema/package evolution between published versions of the **same** package:

- `from_version` (required)
- `to_version` (required)
- `description` (optional)
- `transforms` (optional metadata for execution tooling)

Validation is enforced by `compile_canonical_manifest()` and is strict for profile-managed tracks/apps.

## Upgrading v1 manifests to v2

v1 is no longer supported. Any v1 manifest discovered in the codebase (seeded packages, fixtures, tests) is rewritten in place as part of the App-bundles rollout. The recipe:

1. Change `operational_model_schema_version: 1` → `2`
2. Rename root key `space:` → `app:` (where `scope: space`)
3. Change `scope: space` → `scope: app`
4. Optionally add new operational sections (`skills`, `agents`, `settings_schema`, `seeds`, `permissions`) — all five are optional and absent in a minimally migrated package
5. Validate via `compile_canonical_manifest()` or PATCH against a test OperationalModel; the compiler returns 400 on invalid v2 manifests

Nothing else in the manifest body changes — `entry_types`, `taxonomy`, `views`, `relations` are unchanged between versions.

## Prescribed tracks (app apply only)

App-scoped packages may list multiple track types under `app.tracks[]`. The platform can optionally **materialize matching `Track` rows** when the package is **installed onto a Workspace** (creating an App), controlled by `app.defaults.provision_prescribed_tracks` and per-track `provision_on_create` (see [operational_model_authoring_and_library.md](operational-model-authoring-and-library.md#optional-track-initialization-app-level-apply-only)).

The same package applied **to a Track** does not create additional Tracks.

## AI Apply Guardrails

Recommended apply workflow:

1. Compile YAML/JSON to canonical manifest (v2)
2. Validate migration chain metadata
3. Dry-run diff target manifest against current profile
4. Apply and persist
5. Roll back to prior manifest snapshot if runtime validation fails
