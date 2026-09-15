# ContentProfile overview

**ContentProfile** is Integral's declarative schema and operational layer: EntryTypes, Tags, Views, optional Skills, Agents, and Settings compose into **Apps** (coherent domains) or **Tracks** (table-like collections).

## Mental model

- **App** ≈ schema / database (groups tracks, cross-track relations, skills, agents)
- **Track** ≈ table (typed entries)
- **Entry** ≈ record (shape from EntryType under the track's profile)

Depth uses two reference patterns only: **lookup** (`relation` → entry, `REFERENCES` edge) and **expansion** (`relation` → track, anchor pattern, `ANCHORS` edge). See [content-profiles/README.md](../content-profiles/README.md) (Modeling Tenets).

## Roles

| Role | Placement |
|------|-----------|
| Library package | Under ContentProfiles registry; versioned manifests; merge into attached instances |
| App-attached | Default profile for an App; may `DEFINES_TRACK_PROFILE` → track templates |
| Track-attached | Owns that track's EntryType / Tag / View subgraph |

## Capabilities (current)

| Area | Doc |
|------|-----|
| Manifest v2, App install, lifecycle | [backend/app-bundles-v1.md](../backend/app-bundles-v1.md) |
| App skills → resident jvagent overlay | [backend/workspace-agent-profile.md](../backend/workspace-agent-profile.md) |
| Authoring + library merge | [backend/content-profile-authoring-and-library.md](../backend/content-profile-authoring-and-library.md) |
| Draft / publish | [content-profiles/DRAFT_PUBLISH.md](../content-profiles/DRAFT_PUBLISH.md) |
| View palette | [content-profiles/VIEW_PALETTE.md](../content-profiles/VIEW_PALETTE.md) |
| Agent MCP contract | [content-profiles/AGENT_CONTRACT.md](../content-profiles/AGENT_CONTRACT.md) |
| Composition patterns | [content-profiles/COMPOSITION_PATTERNS.md](../content-profiles/COMPOSITION_PATTERNS.md) |

## APIs (summary)

- `GET/PATCH /apps/{id}/content-profile`, `GET/PATCH /tracks/{id}/content-profile`
- `POST …/merge-library`, detach, revert, derive to library
- `POST /content-profiles/{id}/{draft,publish,diff,discard-draft}`

OpenAPI: `http://localhost:4000/docs` when the backend is running.

## Code anchors

| Concern | Module |
|---------|--------|
| Compile / validate | `backend/app/services/content_profile_compile.py` |
| Materialize graph | `backend/app/services/content_profile_graph.py` |
| Merge / install | `backend/app/services/content_profile_merge.py`, `app_lifecycle.py` |
| Agent patches | `backend/app/services/agent_profile_patches.py` |

## Learning path

1. This page → [content-profiles/README.md](../content-profiles/README.md)
2. [VIEW_PALETTE.md](../content-profiles/VIEW_PALETTE.md) + [AGENT_CONTRACT.md](../content-profiles/AGENT_CONTRACT.md)
3. [app-bundles-v1.md](../backend/app-bundles-v1.md) for App-scoped manifests
