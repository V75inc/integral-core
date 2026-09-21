# Operational Models overview

**Operational Model** is Integral's declarative schema and operational layer: EntryTypes, Tags, Views, optional Skills, Agents, and Settings compose into **Apps** (coherent domains) or **Tracks** (table-like collections). `OperationalModel` remains the current compatibility name in APIs and storage.

## Mental model

- **App** ≈ schema / database (groups tracks, cross-track relations, skills, agents)
- **Track** ≈ table (typed entries)
- **Entry** ≈ record (shape from EntryType under the track's profile)

Depth uses two reference patterns only: **lookup** (`relation` → entry, `REFERENCES` edge) and **expansion** (`relation` → track, anchor pattern, `ANCHORS` edge). See [operational-models/README.md](../operational-models/README.md) (Modeling Tenets).

## Roles

| Role | Placement |
|------|-----------|
| Model Listing | Discoverable catalog record for a reusable model or App Package |
| App Model | Attached model for an App; may `DEFINES_TRACK_PROFILE` → track templates |
| Track Model | Owns that track's EntryType / Tag / View subgraph |

## Capabilities (current)

| Area | Doc |
|------|-----|
| Manifest v2, App install, lifecycle | [backend/app-bundles-v1.md](../backend/app-bundles-v1.md) |
| App skills → resident jvagent overlay | [backend/workspace-agent-profile.md](../backend/workspace-agent-profile.md) |
| Authoring + library merge | [backend/operational-model-authoring-and-library.md](../backend/operational-model-authoring-and-library.md) |
| Draft / publish | [operational-models/DRAFT_PUBLISH.md](../operational-models/DRAFT_PUBLISH.md) |
| View palette | [operational-models/VIEW_PALETTE.md](../operational-models/VIEW_PALETTE.md) |
| Agent MCP contract | [operational-models/AGENT_CONTRACT.md](../operational-models/AGENT_CONTRACT.md) |
| Composition patterns | [operational-models/COMPOSITION_PATTERNS.md](../operational-models/COMPOSITION_PATTERNS.md) |

## APIs (summary)

- `GET/PATCH /apps/{id}/operational-model`, `GET/PATCH /tracks/{id}/operational-model`
- `POST …/merge-library`, detach, revert, derive to library
- `POST /operational-models/{id}/{draft,publish,diff,discard-draft}`

OpenAPI: `http://localhost:4000/docs` when the backend is running.

## Code anchors

| Concern | Module |
|---------|--------|
| Compile / validate | `backend/app/services/operational_model_compile.py` |
| Materialize graph | `backend/app/services/operational_model_graph.py` |
| Merge / install | `backend/app/services/operational_model_merge.py`, `app_lifecycle.py` |
| Agent patches | `backend/app/services/agent_profile_patches.py` |

## Learning path

1. This page → [operational-models/README.md](../operational-models/README.md)
2. [VIEW_PALETTE.md](../operational-models/VIEW_PALETTE.md) + [AGENT_CONTRACT.md](../operational-models/AGENT_CONTRACT.md)
3. [app-bundles-v1.md](../backend/app-bundles-v1.md) for App-scoped manifests
