# Extension Contract v1

**Status:** F0 baseline
**Date:** 2026-09-15
**Companion:** [FOUNDATION_EXTENSION_SAAS.md](../product/FOUNDATION_EXTENSION_SAAS.md), [app-bundles-v1.md](../backend/app-bundles-v1.md)

This is the **product API for App authors**. It is intentionally smaller than
Core's internal API. Breaking changes require a deprecation window (see
[extension-contract-governance.md](extension-contract-governance.md)).

## Vocabulary

| Term | Meaning |
| --- | --- |
| Package artifact | Immutable, versioned, fingerprinted distributable (`bundle_fingerprint`) |
| Installed App instance | Workspace-local materialization; records `installed_package_slug`, `installed_package_version`, `installed_artifact_fingerprint` |
| Content Profile | Declarative schema/content component — not synonymous with a package artifact |
| Plugin | Executable extension (tool handler, view plugin) — not synonymous with an App |

## Surfaces

| Concern | Contract | Rule |
| --- | --- | --- |
| Data | Content Profile / App manifest (`docs/backend/app-bundles-v1.md`) | Rooted through Workspace → App → Track → Entry |
| Package class | `package.class`: `core_package` \| `community_app` \| `verified_app` \| `commercial_app` \| `private_org_app` | Core-seed defaults use `core_package` |
| Operations | `app.tools[]`, `ToolContext`, optional `app.operations[]` | Tools reach Core only through `ToolContext`; no `app.services` / `app.models` imports |
| Hooks | Frozen catalog I-HOOK-01 | New hook points require a Decision Record |
| Track aliases | `app.track_aliases[]` | Cross-app title/template_id aliases — never hardcoded in Core |
| Skills | I-SKILL-01..04 | Overlay namespaced `{app_slug}__{skill_key}` |
| Views | Generic view palette + declarative composition | **F2 Phase One:** App packages may ship ``view_types[]`` composites + ``views[]`` that resolve to Core palette widgets without editing Core `frontend/src/views/manifests/` (see `examples/reference-hello-app`). Domain widgets (`payroll_register`, `example_desk_*`) register via commercial `frontend/src/views/productManifests/` — not Core auto-discover. Signed/dynamic App FE modules remain deferred. |
| Lifecycle | `installing` → `awaiting_settings` → `active` ↔ `paused` → `uninstalled` / `failed` | Pause unregisters hooks/tools; uninstall deregisters + retains data policy stub |
| Package roots | `INTEGRAL_PACKAGE_PATHS`, `INTEGRAL_CORE_ONLY` | Core tree: `backend/app/profiles/` seeds only. Commercial Apps: `packages/apps/`. See [INTEGRAL_CORE_EXTRACT.md](../product/INTEGRAL_CORE_EXTRACT.md). |
| Bundle post-seed | `<bundle>/seeds/post_install.py` with `async def run(app, actor_id)` | Domain seed side-effects (e.g. CRM wiki handbook) live in the package, not Core |

## ToolContext facade (semver boundary)

Supported methods for trusted bundle tools (see `backend/app/services/hooks/registry.py`):

- Entry / track reads and scoped finds
- `create_entry` / updates through policy gates
- `get_app_settings(app_key)`
- `get_employee_compensation(employee_id)` — generic REFERENCES/`base_salary` walk
- Workspace-scoped helpers documented on the class

Any required private import of Core internals is a **missing contract**, not an exception.

## Lifecycle recoverability

- Install saga checkpoints: `InstallAttempt` Object (I-GRAPH-02)
- Upgrade: snapshot attached manifest before merge; restore on failure
- Pause: unregister hooks/tools; data retained
- Uninstall: deregister hooks/tools; scheduled jobs cancelled (when present)

## Reference App

`examples/reference-hello-app/` is the external contract proof. Install via:

```bash
INTEGRAL_PACKAGE_PATHS=examples make verify-contract
```

Core must not require this package to boot (`INTEGRAL_CORE_ONLY=1`).

## Core release artifact

Product image (default `docker build -f backend/Dockerfile .`): full
`backend/app/profiles/` tree for dogfood.

Core image: `docker build --target core -f backend/Dockerfile .` — only
`personal-context` + `agent-scratch` remain under `app/profiles/`, with
`INTEGRAL_CORE_ONLY=1`. Domain Apps are not on disk in that artifact.

## Related invariants

- I-SUBSTRATE-01 — no domain tokens in substrate
- I-HOOK-01 / I-HOOK-02 — frozen hooks; operational layer survives merge
- I-EXT-01 / I-EXT-02 — Core/App separation (see `docs/INVARIANTS.md`)
- I-BUNDLE-01..05 — library package shape
