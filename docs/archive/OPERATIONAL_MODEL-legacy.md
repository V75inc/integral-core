# OperationalModel Extension System

## Vision: Integral as a platform

**OperationalModel** is the cornerstone of Integral's evolution from a productivity tool into a **modular, agent-authorable application platform**. Rather than building fixed features, Integral provides a runtime where **OperationalModels define entire application experiences** that conform to any business process.

### Core Insight

A **Space** is not just a collection of Tracks—it's an **application** when powered by a OperationalModel. The CRM example demonstrates this:

```
CRM OperationalModel (scope: space)
├── Contacts Track (type: contacts)
│   ├── EntryTypes: Contact, Interaction
│   ├── Views: Table (default)
│   └── Purpose: "Manage relationships and communications"
├── Opportunities Track (type: opportunities)
│   ├── EntryTypes: Opportunity (with relation to Contact)
│   ├── Views: Kanban pipeline (default), Deals Table
│   └── Purpose: "Track deals through sales stages"
├── Projects Track (type: projects)
│   ├── EntryTypes: Project, Task, Milestone (with relation to Contact)
│   ├── Views: Kanban board (default), Calendar
│   └── Purpose: "Deliver work for clients"
└── Cross-Track Relations
    ├── Projects → Contacts (reference)
    └── Opportunities → Contacts (reference)
```

This isn't three separate tracks—it's a **cohesive CRM application** where:
- Data flows between tracks via **relation fields**
- Each track has purpose-built entry types and views
- The **space-level manifest** defines how tracks interconnect
- Users experience it as **one workspace** with multiple "areas"

## OperationalModel as Extension Mechanism

### Implementation reference

| Area | Primary code |
|------|----------------|
| Manifest compile/validate | `backend/app/services/operational_model_runtime.py` (`compile_canonical_manifest`, `VALID_FIELD_TYPES`, `VALID_VIEW_TYPES`) |
| Merge, dependencies, migrations bookkeeping | `backend/app/services/operational_model_merge.py` |
| Library CRUD, validate endpoint | `backend/app/api/operational_models.py` |
| Space profile, preview/apply, track templates | `backend/app/api/spaces.py` |
| Track profile, merge-library | `backend/app/api/tracks.py` |
| Registry + attach helpers | `backend/app/services/app_graph.py` |

### Current State (Implemented)

| Capability | Status | Notes |
|------------|--------|-------|
| Attached profiles (Space/Track) | ✅ | `HAS_OPERATIONAL_MODEL` edge, one per entity; `attached_operational_model_id` on Space/Track |
| Library packages | ✅ | `OperationalModels` registry, `CATALOGS` edge; `library_package` flag filters list |
| Canonical manifest v1 | ✅ | `scope: track` or `scope: space`, validated in `compile_canonical_manifest` |
| Merge into attached Operational Model | ✅ | `merge_library_manifest_into_operational_model` in `operational_model_merge.py` |
| Space-scoped multi-track | ✅ | `space.tracks[]` with `provision_on_create`; `provision_prescribed_tracks` default |
| Cross-track relations | ✅ | `space.relations[]` graph check; `relation` fields with `allow_cross_track` + `target_track_types` |
| Profile dependencies (merge) | ✅ | `package.dependencies[]` validated at compile; merge resolves chain via `_resolve_library_dependency_chain` (missing dep / circular → `BadRequestError`) |
| `package.capabilities[]` (manifest) | ✅ | Normalized and validated against same widget type set as views (`VALID_VIEW_TYPES`) |
| Workspace-scoped library packages | ✅ | `POST/PUT/DELETE /api/operational-models`, `GET /api/workspaces/{id}/operational-models` (`operational_models.py`) |
| Validate manifest (agents) | ✅ | `POST /api/operational-models/validate` returns `canonical_manifest` or `issues[]` |
| Space library preview / apply | ✅ | `POST /api/spaces/{id}/operational-model/preview` and `.../apply` (preview summary + merge + `provision_prescribed_tracks`) |
| Applied migration audit | ✅ | `operational_model_migrations.run_publish_migrations` executes declarative ops on publish; `_append_applied_migrations` records the audit |
| Track templates | ✅ | `DEFINES_TRACK_PROFILE` children; `.../operational-model/track-templates` and apply-to-track APIs on spaces |
| View palette contracts (backend) | ✅ | `backend/app/views/contracts/*.json` + `view_contract_catalog.py`; synced to `frontend/src/views/contracts.json` |
| View registry (frontend) | ✅ | `frontend/src/views/registry.tsx`: `registerWidget()`, `ViewRenderer`, `MissingWidget`; manifests auto-loaded from `frontend/src/views/manifests/` |
| Field-type registry (backend + frontend) | ✅ | `operational_model_field_types` (backend) + `frontend/src/components/entries/fieldTypes/registry.ts` (frontend); both support manifest-declared composites |
| View-type registry (backend) | ✅ | `app.views.operational_model_view_types`: palette builtins + composable meta-widgets (`composable_list/grid/board/timeline`) |
| Composable meta-widgets | ✅ | `frontend/src/components/views/composable/`: declarative grouping/sorting/filtering, no new code per agent request |
| Draft / publish lifecycle | ✅ | `OperationalModel.status`, `published_at`, `draft_of_id`, `version_number`; `POST /operational-models/{id}/draft|publish|diff|discard-draft` (`operational_model_atomic_swap`) |
| Migration runner (declarative ops) | ✅ | `rename_field`, `default_fill`, `delete_field`, `prune_enum_option`, `coerce_type`, `move_field` (manual-review). Strict + permissive failure policies |
| Code plugin discovery (signed) | ✅ | `operational_model_plugins`: entry-points + directory scan; Ed25519 verify against `INTEGRAL_PLUGIN_PUBKEY`; permissive in dev |
| Substrate introspection (agent + UI) | ✅ | `GET /operational-model-substrate` returns field types, view types, plugins, registry versions |
| Agent contract (introspection-first) | ✅ | New MCP tools: `integral_describe_substrate`, `integral_describe_model`, `integral_get_model_draft`, `integral_propose_model_revision`, `integral_diff_model_draft`, `integral_publish_model_draft`, `integral_discard_model_draft` |
| Patch DSL interpreter | ✅ | `agent_profile_patches.apply_operations`: 13 declarative ops over a draft manifest, validated via `compile_canonical_manifest` |
| Staged entry index | Partial | `_cp_index` on entries from profile `index: true` hints (`operational_model_runtime.build_entry_index_document`) |
| YAML authoring | ✅ | `manifest_yaml` path in `compile_canonical_manifest` (requires PyYAML) |

### Target State (Platform Vision)

| Capability | Gap | Implementation Notes |
|------------|-----|---------------------|
| **Agent-authorable profiles** | ✅ | Introspection-first contract via the new MCP tool surface (Pillar 3); patch DSL + draft/publish lifecycle |
| **Runtime capability registration** | ✅ | Backend field/view-type registries; frontend mirror; signed-plugin discovery (Pillar 1) |
| **Profile dependencies** | Partial | Merge order + lookup by dependency `id` (package `id`/`name`, or graph node id) is implemented; semver ranges and a package registry UX are not |
| **Profile versioning & migrations** | ✅ | Draft/publish + atomic swap + declarative migration runner (Pillar 2) |
| **Org-private library publishing** | Partial | **Implemented** for org-scoped packages; publish permission uses org owner or `IS_MEMBER_OF` with `can_create_spaces` or `can_create_tracks` (no separate `can_publish` flag yet) |
| **Profile discovery & search** | 🔲 | `_cp_index` extraction exists; full-text/category/rating not wired as product search |
| **Profile sandbox/testing** | ✅ | Draft CP + dry-run apply via `POST /operational-models/{id}/diff` (entry-impact preview) and atomic swap on publish |
| **Profile analytics** | 🔲 | No dedicated adoption/retention metrics |
| **AI authoring contract** | ✅ | Deterministic schema + validate endpoint + introspection tools + patch DSL + staging primitive integration |

## Architecture for Agent Authoring

### Agent Authoring Flow

```
1. User expresses need: "I need a CRM to manage my consulting business"
                    ↓
2. AI analyzes intent → identifies domain (CRM), scope (solo consultant)
                    ↓
3. AI generates canonical manifest v1:
   - scope: space
   - space.tracks[]: [contacts, opportunities, projects]
   - cross-track relations: Project → Contact, Opportunity → Contact
   - views: kanban for opportunities, table for contacts
                    ↓
4. AI validates manifest:
   - `POST /api/operational-models/validate` or `compile_canonical_manifest()` → compiled manifest or `BadRequestError` / `issues[]`
   - view specs: `view_type` must be in backend `VALID_VIEW_TYPES` (`feed`, `kanban`, `table`, `calendar`, `gallery`)
   - `package.capabilities[]`: each `type` must also be in `VALID_VIEW_TYPES` (widget/capability names align with view types)
   - relation check: in-track targets or `allow_cross_track` + `target_track_types`; space graph validated in `space.relations`
                    ↓
5. User previews impact (optional): `POST /api/spaces/{id}/operational-model/preview`
                    ↓
6. Apply: `POST /api/spaces/{id}/operational-model/apply` (or `merge-library`) → merge manifest, provision prescribed tracks, materialize subgraph
```

### Deterministic Manifest Contract

Agents must emit **canonical v1** shape with strict validation:

```yaml
operational_model_schema_version: 1
scope: space
package:
  name: "crm-consulting"
  publisher: "ai-generated"
  description: "CRM for solo consultants"
  capabilities: ["kanban", "table", "calendar"]  # widget types used

space:
  tracks:
    - key: contacts
      name: Contacts
      description: "Client and prospect database"
      provision_on_create: true  # default
      entry_types:
        - key: contact
          name: Contact
          icon: users
          fields:
            - key: email
              type: text
            - key: company
              type: text
            - key: status
              type: select
              enum: [prospect, active, archived]
      taxonomy:
        tag_groups:
          - key: industry
            name: Industry
            tags:
              - key: tech
                name: Technology
              - key: healthcare
                name: Healthcare
      views:
        - key: table
          name: All Contacts
          view_type: table
          is_default: true

    - key: opportunities
      name: Opportunities
      description: "Sales pipeline"
      entry_types:
        - key: opportunity
          name: Opportunity
          icon: dollar-sign
          fields:
            - key: value
              type: number
            - key: stage
              type: select
              enum: [lead, qualified, proposal, closed-won, closed-lost]
            - key: contact
              type: relation
              relation:
                target_entry_types: [contact]
                target_track_types: [contacts]
                allow_cross_track: true
                many: false
      views:
        - key: pipeline
          name: Sales Pipeline
          view_type: kanban
          kanban_columns:
            - key: lead
              label: Lead
            - key: qualified
              label: Qualified
            - key: proposal
              label: Proposal
            - key: won
              label: Closed Won
            - key: lost
              label: Closed Lost
          is_default: true

  relations:
    - source_track_type: opportunities
      target_track_type: contacts
      relation_field: contact
      description: "Each opportunity links to one contact"

  defaults:
    provision_prescribed_tracks: true  # auto-create tracks on apply
```

### Validation Hooks

Agents receive **compile-time feedback** from `compile_canonical_manifest` (and `/api/operational-models/validate`). Representative messages (exact text from `operational_model_runtime.py`):

| Validation | Example / pattern |
|------------|---------------------|
| Unknown manifest `view_type` | `Unsupported view type '<name>'` |
| Unknown `package.capabilities[].type` | `Unknown capability '<name>'. Available: calendar, feed, gallery, kanban, table` |
| Relation target (same track tier) | `Relation field '<key>' references unknown entry type '<t>' in <where>` (allowed when `allow_cross_track` is true and `target_track_types` is set) |
| Invalid `scope` | `manifest scope must be 'track' or 'space'` |
| `space.relations` vs `space.tracks` | `space.relations source_track_type '…' not found in space.tracks` (and analogous for `target_track_type`) |
| Circular `space.relations` | `Circular relation detected in space.relations` |
| Schema version | `manifest must set operational_model_schema_version to 1` |
| Profile dependency merge | `Missing dependency '<id>' for operational model '<cp id>'` / `Circular profile dependency detected` (`operational_model_merge.py`) |

**Frontend (runtime):** if a saved view’s `type` has no `registerWidget` entry, `MissingWidget` renders (“Widget not available … isn’t installed yet”). That is separate from manifest compile errors.

## View Palette (Widget Capability System)

Profiles compose from a **prebuilt view palette** — stable `view_type` keys
with declarative `config`. They do **not** hot-load arbitrary React view
source at runtime. Full convention:
[docs/operational-models/VIEW_PALETTE.md](../operational-models/VIEW_PALETTE.md).

### Layers

| Layer | Location | Role |
|-------|----------|------|
| Contracts | `backend/app/views/contracts/*.json` | Canonical keys + `palette_group`, `configurable`, `hot_loadable` |
| Backend registry | `backend/app/views/operational_model_view_types.py` | Validation + substrate introspection |
| Web implementations | `frontend/src/views/manifests/*.manifest.ts` + components under `frontend/src/components/views/` | Renderers registered at boot |
| Profile spec | Manifest `views[]` / materialized `View` nodes | Declares which palette keys a Track/App uses |

Sync contracts → frontend artifact:

```bash
cd backend && venv/bin/python scripts/sync_view_contracts.py
# or: cd frontend && npm run sync:view-contracts
```

### Profile-declared views

```yaml
track:
  views:
    - key: pipeline
      name: Pipeline
      view_type: kanban
      config:
        group_by: stage
      entry_type_keys: [deal]
  defaults:
    default_view: pipeline
```

**Runtime behavior:**
1. `compile_canonical_manifest()` validates every `view_type` against the backend palette registry (`feed` is injected when missing).
2. Merge-library or attach APIs materialize `View` rows (`type` + `config`).
3. `ViewRenderer` resolves `view.type` via `frontend/src/views/registry.tsx`.
4. Unknown or unshipped keys → `MissingWidget` (profile applied; client lacks renderer).

Optional `package.capabilities[]` entries use the same type set as `views[]`.

### Extensibility (palette growth, not hot-load)

To add a palette view: backend contract → sync script → web manifest +
widget component → reference `view_type` in library bundles
(`backend/app/packages/<slug>/`). Signed code plugins remain the rare
escape hatch ([docs/operational-models/PLUGINS.md](../operational-models/PLUGINS.md)).

## Gaps to Address

### 1. Profile Dependency System

**Done today:** `package.dependencies[]` is part of the canonical manifest (`id` + `version` required per entry). On library merge, `_resolve_library_dependency_chain` merges dependency packages **before** the primary package (same target attached Operational Model), with cycle detection and “missing dependency” errors. Dependency `id` is resolved against another library `OperationalModel` by node id or by matching `package.id` / `package.name` on cataloged packages.

**Still open:** Treat `version` as a semver range resolver, UI to declare/browse dependencies, and clearer packaging conventions (stable `package.id` for every published library).

### 2. Workspace-Private Library Publishing

**Implemented:** Workspace-scoped library packages use the same global `OperationalModels` registry plus `workspace_id` and `library_package: true`.

| Method | Path | Role |
|--------|------|------|
| GET | `/api/operational-models` | List cataloged library packages |
| POST | `/api/operational-models/validate` | Validate/normalize manifest |
| POST | `/api/operational-models` | Publish (body includes `workspace_id`, manifest or `manifest_yaml`) |
| PUT | `/api/operational-models/{id}` | Update workspace-scoped package |
| DELETE | `/api/operational-models/{id}` | Remove package from catalog (node deleted) |
| GET | `/api/workspaces/{id}/operational-models` | List packages for one workspace |

**Permissions:** Workspace owner, or workspace member with `can_create_spaces` or `can_create_tracks` on `IS_MEMBER_OF` (`can_publish_operational_models_under_workspace` in `permissions.py`). There is no separate `can_publish_operational_models` edge yet.

### 3. Profile preview before apply

**Implemented (space):** `POST /api/spaces/{id}/operational-model/preview` returns a structured `preview` (per-track counts, relations, `would_create_tracks`, etc.) without mutating the Operational Model. `POST .../operational-model/apply` runs merge + `provision_prescribed_tracks_from_space_manifest` and echoes `preview` plus `applied` summary.

**Still open:** A dedicated **isolated** sandbox space or clone workflow for zero-risk experimentation; track-level equivalent of space preview if product needs it.

### 4. AI Authoring SDK

**Problem:** Agents need deterministic contracts for profile generation.

**Solution:** Python/TypeScript SDK:

```python
from integral import OperationalModelBuilder

profile = (
    OperationalModelBuilder()
    .scope("space")
    .package(name="crm-consulting", publisher="ai-agent")
    .add_track(
        key="contacts",
        name="Contacts",
        entry_types=[
            EntryType(name="Contact", fields=[
                TextField(key="email", label="Email"),
                SelectField(key="status", label="Status",
                           options=["prospect", "active", "archived"]),
            ])
        ],
        views=[
            View(name="All Contacts", type="table", is_default=True)
        ]
    )
    .build()
)

# Validate before emitting
errors = profile.validate()
if errors:
    raise ValidationError(errors)

# Emit YAML
yaml_output = profile.to_yaml()
```

### 5. Profile Analytics

**Problem:** Can't measure which profiles succeed.

**Solution:** Telemetry hooks:

```typescript
// In operationalModelManifest.ts or telemetry.ts
function trackProfileApply(profileId: string, spaceId: string) {
  telemetryEvent({
    name: 'operational_model_applied',
    payload: { profileId, spaceId, timestamp: Date.now() }
  });
}

function trackProfileRetention(profileId: string, days: number) {
  // Measure if space still has profile's entry types after N days
}
```

Backend aggregates:
- Applies per profile
- Retention at 7/30/90 days
- Entry creation rate post-apply
- View adoption (do users actually use the kanban?)

## Implementation Roadmap

### Phase 1: Foundation (Current)
- ✅ Canonical manifest v1
- ✅ Space-scoped multi-track profiles
- ✅ Cross-track relations
- ✅ Widget registry (baseline)

### Phase 2: Agent Contract (Next)
- [ ] AI authoring SDK (Python/TS)
- [ ] Validation error messages tuned for agents (`/operational-models/validate` exists)
- [ ] Deterministic YAML emission
- [ ] Richer `MissingWidget` / in-app remediation (baseline UI exists)

### Phase 3: Platform Features
- [x] Profile dependencies (merge-time resolution; semver/registry UX still thin)
- [x] Org-private publishing API (org-scoped packages + permission proxy)
- [x] Space library preview / apply (not an isolated sandbox)
- [ ] Profile analytics

### Phase 4: Ecosystem
- [ ] Community marketplace (public profiles)
- [ ] Rating/review system
- [ ] Profile forks and derivatives
- [ ] Revenue share for premium profiles

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Profile adoption rate** | >60% of new spaces use a library Operational Model | Backend audit on space create |
| **AI-authored profiles** | >30% of applied profiles | `publisher: "ai-*"` or provenance tracking |
| **Multi-track profiles** | >40% of space-scoped profiles provision 2+ tracks | `space.tracks[].length` on apply |
| **Widget adoption** | >50% of tracks use 2+ view types | `View` count per track |
| **Org-private packages** | >20% of orgs publish 1+ internal package | Org-scoped `OperationalModel` count |

## Conclusion

OperationalModel is Integral's **extension mechanism**—the bridge between a fixed tool and a living platform. By making profiles:
- **Modular** (library packages, dependencies)
- **Pluggable** (widget registry, capability declarations)
- **Agent-authorable** (deterministic contracts, validation)

Integral becomes a platform where **any business process can be modeled** without writing code. The CRM example is just the beginning—future profiles could power:
- Recruitment pipelines (candidates → interviews → offers)
- Content studios (ideas → drafts → published)
- Research labs (hypotheses → experiments → papers)
- Event production (venues → vendors → timelines)

Each profile is an **app** that transforms Integral into the perfect tool for that domain.

---

## Agent-Authorable Substrate (v1.1 — implemented)

The Operational Model system is now an agent-authorable substrate organised
around four architectural pillars:

0. **View palette** — profiles reference stable `view_type` keys from
   `backend/app/views/contracts/`; clients ship matching renderers. See
   [docs/operational-models/VIEW_PALETTE.md](../operational-models/VIEW_PALETTE.md).
1. **Declarative type system (Pillar 1)** — primitive field/view types live
   in extensible registries (`operational_model_field_types.py`,
   `app.views.operational_model_view_types` and frontend mirrors). Manifests can declare
   *composites* over those primitives without shipping any code.
2. **Versioned, sandboxed schema lifecycle (Pillar 2)** — every CP is
   draft/published. Draft mutations preview via structural diff +
   entry-impact; publish is an atomic swap of the manifest onto the
   identity-preserved CP node, with a migration runner executing any
   declared `migrations[].ops[]` against existing entries.
3. **Agent reasoning contract (Pillar 3)** — introspection-first MCP tools
   give the agent a small, declarative surface to author profiles via a
   patch DSL.
4. **Composable meta-widgets (Pillar 4)** — generic, declaratively-driven
   list / board / grid / timeline widgets close the runtime-UI gap so most
   "show me X view of Y data" requests resolve to a config.

### Manifest v1.1 additions

```yaml
operational_model_schema_version: 1

# Pillar 1 — declarative composites scoped to this manifest only.
field_types:
  - { key: currency, base: number,   config: { currency: USD, min: 0 } }
  - { key: email,    base: text,     config: { pattern: '^[^@]+@[^@]+\.[^@]+$' } }
view_types:
  - { key: roadmap, base: composable_board, config: { group_by: stage, color_by: priority } }

# Pillar 1 — signed code plugin requirements (rare; escape hatch).
plugins:
  - { id: timeline-widget, version: '>=1.2', signature: <base64> }

# (existing v1 keys remain — entry_types, views, taxonomy, space.tracks,
# space.relations, defaults, etc.)
```

### REST endpoints (Pillar 2)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/operational-models/{id}/draft`         | Fork a draft from a published CP |
| POST | `/api/operational-models/{id}/publish`       | Atomic swap + run migrations |
| POST | `/api/operational-models/{id}/diff`          | Structural diff + entry impact |
| POST | `/api/operational-models/{id}/discard-draft` | Delete an unpublished draft |
| GET  | `/api/operational-model-substrate`           | Field/view/plugin catalogue |

### MCP tools (Pillar 3)

| Tool | Stages? | Purpose |
|------|---------|---------|
| `integral_describe_substrate`        | no  | Catalogue of registered types + plugins |
| `integral_describe_model`          | no  | Published + draft state for a track/space CP |
| `integral_get_model_draft`         | no  | Auto-create or fetch draft |
| `integral_propose_model_revision`  | yes | Apply patch DSL to a draft |
| `integral_diff_model_draft`        | no  | Diff vs published parent + entry impact |
| `integral_publish_model_draft`     | yes | Atomic swap + migrations |
| `integral_discard_model_draft`     | yes | Discard unpublished draft |

The patch DSL supports: `add/remove/modify_entry_type`, `add/remove/modify_field`,
`add/remove_view`, `add/remove_tag`, `add_relation`,
`register_composite_field_type`, `register_composite_view_type`. See
[`backend/app/services/agent_profile_patches.py`](backend/app/services/agent_profile_patches.py).

### Composable meta-widgets (Pillar 4)

Front-end widgets that consume declarative `view.config`:

| Widget | Config keys |
|--------|-------------|
| `composable_list`     | group_by, sort, filter, projection, density |
| `composable_board`    | group_by, color_by, swimlanes, sort_within_column, filter |
| `composable_grid`     | group_by, color_by, projection, card_layout, sort |
| `composable_timeline` | date_field, end_date_field, color_by, filter |

Source: [`frontend/src/components/views/composable/`](frontend/src/components/views/composable/).
Palette convention: [docs/operational-models/VIEW_PALETTE.md](../operational-models/VIEW_PALETTE.md).

### Migration ops (Pillar 2)

Declared in `manifest.migrations[].ops[]`. Executed by
`operational_model_migrations.run_publish_migrations` on publish.

| Op | Args |
|----|------|
| `rename_field`       | entry_type, from, to |
| `default_fill`       | entry_type, field, value |
| `delete_field`       | entry_type, field |
| `prune_enum_option`  | entry_type, field, option, replacement? |
| `coerce_type`        | entry_type, field, to (text/number/boolean/json) |
| `move_field`         | from_entry_type, to_entry_type, field (manual review) |

Failure policy: `abort_on_failure=True` short-circuits publish; `False`
keeps going and surfaces per-op errors in the run record.
