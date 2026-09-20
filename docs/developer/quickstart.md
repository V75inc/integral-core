# Public developer quickstart (developer preview)

Build and install an external App package against Integral Core without editing Core source.

## Prerequisites

- Python 3.11+ with `uv` or `pip`
- Node 20+ (for Core frontend only if running full UI)
- Postgres 16+ recommended for concurrency proofs (`docker compose up -d db`)

## 1. Run Core locally

Follow the repo-root [README](../../README.md) (Docker is the default). From
source:

```bash
./scripts/bootstrap_env.sh .env .env.example
docker compose up -d db
cd backend
uv sync --frozen --extra dev --extra test
.venv/bin/python -m app.main
```

`JVSPATIAL_JWT_SECRET_KEY` in `.env` must be a real ≥32-character secret — the
example placeholders are rejected at boot.

### Install a built Core wheel

The current harness release candidate is referenced directly in Core's wheel
metadata, so a regular installer can resolve it without inheriting this
repository's `uv` index configuration:

```bash
cd backend
uv build --wheel --out-dir dist
uv venv ../.integral-core-venv
uv pip install --python ../.integral-core-venv/bin/python dist/integral_core-*.whl
```

The harness reference will revert to a normal PyPI constraint when its final
release is available. Use `make verify-clean-install` to reproduce this proof
in a temporary environment.

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

**API (authenticated):**

```http
POST /api/workspaces/{workspace_id}/apps/install?library_content_profile_id={cp_id}
```

Optional query params: `version`, `settings` (JSON), `include_seed_data` (default true). Returns `{status: "active", app_id, ...}` or `{status: "awaiting_settings", install_token, ...}` when the package declares a settings schema.

**Scaffold a new package:** copy `examples/reference-hello-app` to a directory on `INTEGRAL_PACKAGE_PATHS`, rename `package.slug` in `profile.yaml`, and edit `tools/*.py`. See [quickstart-trial-log.md](quickstart-trial-log.md) for an independent trial walkthrough.

## 5. Invoke a typed operation

```http
POST /api/extensions/{app_id}/operations/echo
Idempotency-Key: my-key-1
Content-Type: application/json

{"input": {"message": "hello"}}
```

## 6. SDK typing surface

App handlers import types from `integral_sdk` (see `sdk/python/integral_sdk/`).
Handlers receive `OperationContext` injected by Core — do not import
`app.models` or `app.services`. Operation handlers can use its typed
`create_entry(track_id=..., entry_type_key=..., ...)` method only for Tracks
owned by their installed App; Core enforces membership, edit permission, field
validation, graph wiring, hooks, and audit events. Treat a `None` result as a
normal domain failure and return an App-specific error to the caller.

## 7. Custom views

Declare `app.extension_views[]` in `profile.yaml` and reference them from track views with `view_type: extension_view`. Core serves assets from the package directory via the sandboxed iframe host.

## Verification commands

```bash
make verify-pr          # both PR CI jobs — run before push
make verify-contract    # extension contract tests
make verify-core-only   # Core boots without commercial packages
make verify-artifact   # build + isolated wheel import/resource boundary
make verify-clean-install # fresh dependency resolution + ASGI import
```

Independent developer trial evidence: [quickstart-trial-log.md](quickstart-trial-log.md).

See [FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md](../product/FOUNDATION_PUBLIC_DEVELOPER_SPRINT.md) for acceptance criteria and [ADR-011](../backend/adr/011-public-app-extension-platform.md) for architecture decisions.
