# Integral backend

The backend provides the domain-neutral graph substrate, access model, schema and App lifecycle, resident perimeter, and public API. It uses jvspatial with FastAPI; routes, validation schemas, services, Nodes, and Edges follow the repository's object-spatial conventions.

## Local launcher

An installed wheel with the built UI supports `integral up`, `status`, `logs`, `stop`, `backup`, `restore` and `upgrade`. The launcher runs PostgreSQL in an isolated Python 3.12 helper environment and serves the workspace with real auth on loopback. `integral up --profile desktop_single_user` selects the persisted profile for a new managed installation; existing installations remain on their recorded profile and require a reviewed migration to change. See [local installation](../docs/ops/LOCAL_INSTALLATION.md). Electron and commercial distribution remain in Integral Business.

## Develop

```bash
uv sync --frozen --extra dev --extra test
.venv/bin/python -m app.main
```

Run from this directory with private development settings configured. Python 3.11 or later is required. The source API uses port 4000. The frozen lock is authoritative; do not substitute requirements.txt or unrestricted package-index resolution.

## Navigate

`api/` handles transport, `schemas/` holds typed input/output, `models/` declares graph participants and edges, `services/` owns canonical mutations and lifecycle, and `agentive/` supplies harness bindings, MCP, skills, staging, and work. Domain packages remain behind public facade contracts.

The agentive layer always loads. Default resident binding is Integral AI through Pydantic AI; Integral AI is the only resident harness. Core-only filtering removes domain packages from catalog admission, not the resident layer.

Use the [backend reference](../docs/backend/README.md), [architecture](../docs/product/ARCHITECTURE.md), [invariants](../docs/INVARIANTS.md), and [deployment guide](../docs/ops/DEPLOY.md).

## Search and indexing

PostgreSQL uses durable indexing jobs and local text-only ONNX/pgvector passages.
Canonical writes enqueue work; readiness includes the actual encoder, store and
current-source coverage. `EMBEDDING_STORE_DRIVER=null` selects graph-only reads.
See [declarative indexing and retrieval](../docs/backend/semantic-indexing.md)
for deployment settings, rebuilds, API scope and real-database qualification.
The legacy `backfill_embeddings.py` script fills the older single-vector
adapter and does not populate the passage index.

## jvspatial security compatibility

Metadata pins jvspatial 0.2.2 and pydantic-ai-harness 0.36.0. Runtime dependencies resolve from PyPI. Production OAuth requires its encryption key, and production auth rate limits stay active. Repair duplicate model credentials before creating their unique index and test actual PostgreSQL duplicates.

`make verify` from the repository root is the broad local gate. PR smoke CI is narrower and runs without a developer .env. Database contracts and packaged-wheel checks provide separate evidence.

Successful database operations, including slow calls, do not emit log records.
Metrics and slow-call counters remain available; failed operations emit
structured ERROR records from jvspatial.


### Managed desktop owner profile

`integral up --profile desktop_single_user` creates a new installation for one
OS user. Profile changes require a reviewed migration; use a fresh home for a
new desktop installation. The runtime passes the installation identity,
controller secret and exact web origin as controlled startup settings. They
must never come from request headers, package manifests or browser detection.

Core atomically creates the owner AuthUser, rooted User and private workspace
on PostgreSQL at startup and refuses incompatible stores. Electron main obtains
signed owner sessions through the possession-protected `/api/auth/local-session`
endpoint. Browser Origin/fetch headers are refused on that endpoint; ordinary
signup/password login and additional users are closed in this profile.
`/api/meta/deployment` publishes read-only feature capabilities. HTTP/WS Host and
Origin checks, canonical policy and grant services enforce the same restrictions
on collaboration/public links/ownership transfer. Server defaults are retained.

The private `integral-apps/` directory is discoverable independently of the
single-owner restriction. Commercial entitlements, approval requirements,
package trust and App data export retain their existing generic contracts.
See `docs/INVARIANTS.md` I-DESKTOP-01 and the desktop engineering evidence under
`tasks/phase-one/evidence/` in the repository root. Licensing clearance and native
installer release qualification are separate gates.

### Desktop runtime qualification

Prepare portable resident assets with `.ci/bundle_resident_harness.sh` from the
repository root before building the Core wheel, following `RELEASING.md`. The
desktop bundler requires `desktop_assets_available` and desktop contract version
1. Its ready probe waits for completed startup and registered audit storage;
root `/health` alone does not qualify a desktop runtime.
