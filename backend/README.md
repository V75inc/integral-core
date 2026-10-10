# Integral backend

The backend provides the domain-neutral graph substrate, access model, schema and App lifecycle, resident perimeter, and public API. It uses jvspatial with FastAPI; routes, validation schemas, services, Nodes, and Edges follow the repository's object-spatial conventions.

## Local launcher

An installed wheel with the built UI supports `integral up`, `status`, `logs`, `stop`, `backup`, `restore` and `upgrade`. The launcher runs PostgreSQL in an isolated Python 3.12 helper environment and serves the workspace with real auth on loopback. See [local installation](../docs/ops/LOCAL_INSTALLATION.md). Electron and commercial distribution remain in Integral Business.

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

## jvspatial security compatibility

Metadata pins jvspatial 0.1.1 and pydantic-ai-harness 0.36.0. Runtime dependencies resolve from PyPI. Production OAuth requires its encryption key, and production auth rate limits stay active. Repair duplicate model credentials before creating their unique index and test actual PostgreSQL duplicates.

`make verify` from the repository root is the broad local gate. PR smoke CI is narrower and runs without a developer .env. Database contracts and packaged-wheel checks provide separate evidence.
