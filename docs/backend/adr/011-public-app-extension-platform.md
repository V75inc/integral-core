# ADR-011: Public app extension platform (operations, view host, SDK)

**Status:** Accepted (Wave 0)
**Date:** 2026-09-17
**Sprint:** [FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md](../../product/FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md)

## Context

Public developers must ship complete Apps — data, declarative views, custom views,
typed operations, skills, and schedules — without editing Core or importing
`app.models` / `app.services`. F0 already compiles `app.operations[]` and
registers bundle `tools[]` per workspace. Enforcement was advisory; HTTP and UI
had no namespaced operation surface. Asset Register is the proof domain.

## Decision

### 1. Operation routing and API namespace

- **Route shape:** `POST /api/extensions/{app_id}/operations/{operation_key}`
- **Discovery:** `GET /api/extensions/{app_id}/operations` (metadata only; no handler refs)
- **Dispatcher:** single Core module (`app.services.app_operations`) — Apps do not mount FastAPI routers
- **Handler resolution:** each operation declares `handler_ref` (`module:callable`) OR `tool` (workspace tool key). `handler_ref` is normalized at install like bundle tools (`install_hook._normalize_handler_ref`)
- **Context:** `OperationContext` extends `ToolContext` with `app_id`, `operation_key`, `idempotency_key`, `correlation_id`
- **Policy:** `policy_action` on the operation spec gates invoke (reuse `policy_evaluate`). Paused/uninstalled apps reject with `app_not_active`
- **Idempotency:** optional `Idempotency-Key` header; persisted `OperationIdempotencyRecord` (Object, I-GRAPH-02) scoped by workspace + app + operation + principal + key

### 2. Public SDK import namespace

- **Python package:** `integral_sdk` under `sdk/python/integral_sdk/` (published separately; dev path in monorepo)
- **App authors import:** `from integral_sdk import OperationContext, operation` — thin typing/helpers only; runtime context is injected by Core
- **Forbidden:** `app.*`, `jvspatial.db.*`, Core frontend paths
- **Facade evolution:** deprecate `ToolContext.get_employee_compensation` (domain leak). Commercial callers keep a documented shim until F1 consumer migration completes

### 3. View isolation (custom app views)

- **Host:** sandboxed `iframe` in Core-owned slot (`AppExtensionViewHost`)
- **Bridge:** typed `postMessage` protocol (`integral.extension.v1`); per-mount handshake token bound to workspace + app + package version
- **Assets:** package ships `views/{key}/index.html` + hashed bundle; Core serves via verified static route (`GET /api/extensions/{app_id}/views/{view_key}/…`)
- **Fallback:** generic record view when host fails or view key unknown
- **No** dynamic import of app JS into main SPA bundle

### 4. Installation trust

- Executable Python requires deployment operator trust (`trust_tier: trusted|audited`) — unchanged from DR-30-01
- Signature verification binds manifest digest to all loaded bytes (tools, seeds, view assets) before activation
- Workspace admin cannot elevate arbitrary code to trusted tier

### 5. Package version resolution

- One active executable version per slug per deployment (preview). Install of incompatible second version fails explicitly
- Workspace records `installed_package_slug`, `installed_package_version`, `installed_artifact_fingerprint` on App node (existing fields)

### 6. Lifecycle registration ownership

- `register_bundle_on_install` registers tools, hooks, track aliases — **extended** to register normalized operation handler refs in `_OPERATIONS[workspace_id][app_id]`
- Pause/uninstall clears operation registrations for that app instance
- `rehydrate_all_installed_bundles` replays on startup (existing pattern)

### 7. Atomic state transitions (custody)

- **Approved primitive:** jvspatial `Database.find_one_and_update` with conditional query (`lifecycle_state: available`) on Postgres — natively atomic (`jvspatial/db/postgres.py`)
- Custody checkout uses conditional update + idempotency record in same transaction where `supports_transactions` is true
- No raw SQL in App code; no process-local locks as correctness guarantee

## Consequences

- WP-02 implements dispatcher + SDK; WP-03 view host; WP-05/06 Asset Register package
- Extension contract v1 gains operation `handler_ref` / schemas; governance semver minor bump
- CI: contract tests for hello-app operation invoke; postgres spike + AC-05 integration in WP-08

## Alternatives considered

| Alternative | Rejected because |
| --- | --- |
| Apps mount FastAPI routers | Breaks single auth/policy/audit surface; namespace collisions |
| Main-bundle dynamic `import()` for views | Untrusted code in SPA context |
| Tool-only surface (no operations) | Cannot express policy/staging/idempotency per operation; HTTP/MCP/UI diverge |
| Optimistic read-modify-write without conditional update | AC-05 fails under Postgres concurrency |
