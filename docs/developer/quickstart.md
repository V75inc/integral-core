# Public developer quickstart (developer preview)

Build and install an external App package against Integral Core without editing Core source.

## Prerequisites

- Python 3.11+ with `uv` or `pip`
- Node 20+ (for Core frontend only if running full UI)
- Postgres 16+ recommended for concurrency proofs (`docker compose up -d db`)

## 1. Run Core locally

```bash
cd backend
uv sync --frozen --extra dev --extra test
cp .env.example .env   # optional for local dev
.venv/bin/python -m app.main
```

## 2. Point Core at external packages

```bash
export INTEGRAL_PACKAGE_PATHS=/path/to/examples
export INTEGRAL_CORE_ONLY=0
```

Packages under that path (e.g. `reference-hello-app`, `asset-register`) appear in the content-profile library.

## 3. Validate a package manifest

```bash
cd backend
TESTING=1 .venv/bin/pytest tests/contract/test_asset_register_manifest.py -q
```

## 4. Install from the UI or API

Install `asset-register` or `reference-hello-app` into a workspace. Core registers tools, hooks, operations, and extension views at install time.

## 5. Invoke a typed operation

```http
POST /api/extensions/{app_id}/operations/echo
Idempotency-Key: my-key-1
Content-Type: application/json

{"input": {"message": "hello"}}
```

## 6. SDK typing surface

App handlers import types from `integral_sdk` (see `sdk/python/integral_sdk/`). Handlers receive `OperationContext` injected by Core — do not import `app.models` or `app.services`.

## 7. Custom views

Declare `app.extension_views[]` in `profile.yaml` and reference them from track views with `view_type: extension_view`. Core serves assets from the package directory via the sandboxed iframe host.

## Verification commands

```bash
make verify-contract    # extension contract tests
make verify-core-only   # Core boots without commercial packages
.ci/verify_artifact_baseline.sh
```

See [FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md](../product/FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md) for acceptance criteria and [ADR-011](../backend/adr/011-public-app-extension-platform.md) for architecture decisions.
