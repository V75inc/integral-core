# Agentive Layer — Local Directives

> **Direction ([ADR-003](../../../docs/backend/adr/003-singular-resident-harness.md); spec [RESIDENT_HARNESS.md](../../../docs/product/RESIDENT_HARNESS.md)).** This layer hosts **one singular resident harness**, faceted by principal (personal / org-facing / system) — not a fleet of agents. It is the **primary surface** (UI is a projection). **A2A is retired**: no agent-to-agent discovery, delegation, or `a2a.delegate`; do NOT build `agent_actions/delegate.py`, `mcp_adapter.py:_wrap_for_a2a`, or the I-A2A-01..05 machinery. External agents reach the substrate through the **MCP surface** only, under the same policy/staging/audit path as the resident. `AgentConfig.scope` / `.capabilities` / `.policy_scope` persist as inert facet metadata pending the facet-refactor plan.

The agentive layer (`backend/app/agentive/`) is always on in Integral. It MUST follow the same jvspatial object-spatial conventions as `backend/app/api/` and `backend/app/services/`, including the **pragmatism clause**: convention is the default, measured-efficiency deviations are permitted with inline justification (see root `CLAUDE.md` § jvspatial Object-Spatial Contract → Pragmatism Clause).

**Drift cleared (Plan 06-05, Wave 4 directive-plan remediation).** Every file under `backend/app/agentive/api/` now uses `@endpoint` + `JVSpatialAPIException` + schemas-in-`schemas/agentive/`. The single carve-out is `agent_events.py`'s WebSocket route (jvspatial framework limitation — `@endpoint` does not support WebSocket; documented inline per `# deviation:` annotation). The pre-commit `jvspatial-drift-guard` hook is fully enforcing — any new raw-FastAPI pattern in this directory fails the hook.

## Required Patterns for New Code in `agentive/`

- HTTP routes: `@endpoint("/path", methods=[...], auth=True, tags=["Agentive"])` from `jvspatial.api` — never `@router.<method>` / `APIRouter`.
- Request/response shapes: `backend/app/schemas/agentive/*.py` (new subdir mirroring api/ structure) — never inline in handlers.
- Errors: `JVSpatialAPIException` subclasses from `app/api/errors.py` — never `HTTPException`.
- Auth identity: `user_id = resolve_principal_id(request)`.
- Workspace scope (when workspace-scoped): `workspace_id = await resolve_workspace_id_from_request(request, user_id)`.
- Edge create: `await source.connect(target, edge=EdgeClass, **metadata)`.
- Single-hop traversal: `await node.nodes(edge=[E], node=["T"])`.
- Response serialize: `await export_node(n)`.

### Multi-hop computation → Walker

For computations that traverse more than one hop (workspace skill-overlay resolution, change-event propagation through subscribers, scratch-memory promotion provenance chains), write a **Walker** in `backend/app/agentive/walkers/` (new subdir).

Do not re-implement what should be a graph walk as a Python state machine driving successive `.nodes()` calls. See root `CLAUDE.md` (Walker default) and `docs/INVARIANTS.md` for the dispatch pattern.

### Associative-edge state

Agent capability grants, policy scopes, uplink registrations carry relationship metadata. Declare it as Edge subclass fields, not as `context` dicts. See `backend/app/models/edges.py` for canonical examples (`COLLABORATES_ON.role`, `REFERENCES.field_key`, `ANCHORS.field_key`).

## Known Drift: NONE — migration complete in Plan 06-05

The previously-tracked drift (`APIRouter`, `@router.<method>`,
`HTTPException`, inline `BaseModel`, inline `UnknownCapabilitiesError`)
was drained to zero in Plan 06-05. `.ci/jvspatial_drift_allowlist.txt`
is header-only; `.ci/jvspatial_drift_check.sh` exits 0 against the entire
backend; `backend/tests/test_jvspatial_convention_compliance.py`'s 5
gates pass GREEN. Substrate invariants `I-CONV-01..03` (see
`docs/INVARIANTS.md`) encode the contract for future phases.

**DO NOT reintroduce drift.** New endpoints / services in this directory
MUST use canonical jvspatial patterns from day one. Any `# deviation:`
escape-hatch use requires a measurement; speculative deviation is
rejected. The lone documented exception is the WebSocket carve-out in
`agent_events.py` (jvspatial `@endpoint` does not support WebSocket dispatch).

## Remaining work (out of Plan 06-05 scope)

- **Facet collapse (ADR-003) — scaffolding landed (Wave 3), full collapse still open.**
  `AgentConfig.facet` is the additive preferred discriminator (optional; fall
  back to ``scope`` when ``None``). `HasOrgAgent` / `HasSystemAgent` are
  marked deprecated in docstrings but still wired for boot. Remaining work
  for a dedicated substrate-touching plan: backfill ``facet = scope``, make
  readers prefer ``facet``, collapse org/system edges onto one harness edge
  family, then drop dual fields. Do not build new facet-specific machinery
  on the deprecated edges until that plan executes. See
  [ADR-003](../../../docs/backend/adr/003-singular-resident-harness.md)
  migration-path note.
- **No walkers exist** in `agentive/`. Most agentive computation is multi-hop and would benefit (workspace skill-overlay resolution, change-event delivery to subscribers, scratch-memory promotion provenance). This is queued for the resident memory/proactivity phase (pulled forward per [ADR-003](../../../docs/backend/adr/003-singular-resident-harness.md)), not the 06-05 convention-conformance migration.

## Workspace Agent Profile (resident jvagent)

The embedded resident agent uses a **two-tier** skill model:

1. **Base (global)** — filesystem `integral_*` skills + full `tool_manifest.yaml` surface.
2. **Workspace overlay** — public declarative skills from installed Apps in the active workspace.

Implementation:

- [`workspace_agent_profile.py`](workspace_agent_profile.py) — compose, cache, turn ContextVar
- [`skill_bundle_provider.py`](skill_bundle_provider.py) — jvagent host provider registration
- [`services/skill_registry.py`](services/skill_registry.py) — graph persistence + private gate

Full reference: [`docs/backend/workspace-agent-profile.md`](../../docs/backend/workspace-agent-profile.md).

**When touching install/uninstall/settings paths** that affect App skills, call `invalidate_workspace_profile(workspace_id)` from `workspace_agent_profile.py`.

## Why the Drift Existed (historical)

The agentive layer was prototyped quickly against raw FastAPI patterns when bootstrapping the 06-01 milestone. Tactical velocity-over-consistency trade made under time pressure. Not an approved exception — debt was unwound in Plan 06-05 (Wave 4 directive-plan remediation).
