# View Palette Convention

Content profiles do **not** hot-load arbitrary view source at runtime. They
compose Tracks, Apps, and Workspaces from a **prebuilt, configurable view
palette** — stable capability keys with declarative `view.config`, rendered by
platform clients that ship with the Integral release.

This document is the convention reference for authors, agents, and catalog
publishers. For modeling tenets (track-as-table, anchors, lookups), see
[README.md](README.md). For library drop-in and merge flows, see
[../../backend/docs/content_profile_authoring_and_library.md](../backend/content-profile-authoring-and-library.md).

---

## Mental model

| Concept | What it is | Where it lives |
|---------|------------|----------------|
| **View contract** | Platform-neutral capability key (`kanban`, `feed`, …) plus metadata (`platforms`, `palette_group`, `configurable`, `hot_loadable`) | Canonical: `backend/app/views/contracts/*.json` |
| **View type registry** | Backend validation + config schema + substrate introspection | `backend/app/views/content_profile_view_types.py` |
| **View implementation** | Actual UI renderer (web today; mobile later) | Web: `frontend/src/views/manifests/*.manifest.ts` + widget components under `frontend/src/components/views/` |
| **Profile view spec** | How a Track/App **uses** a palette key | Manifest `views[]` / attached `View` nodes |

**Rule:** Profiles reference **keys**, not file paths. Adding a view to a profile manifest never imports React code — it only declares `view_type` + `config`.

**Rule:** `hot_loadable` is `false` for all palette builtins today. Runtime catalog apply merges structure; it does not download new widget bundles into a running client.

---

## Palette inventory (current)

### Core widgets (`palette_group: core`)

| `view_type` | Default always on | Typical use |
|-------------|-------------------|-------------|
| `feed` | yes | Chronological stream; injected if a tier has no feed view |
| `kanban` | no | Column board; `group_by` / `kanban_columns` |
| `table` | no | Sortable grid; `columns` |
| `calendar` | no | Month/week/day; `calendar_mapping` |
| `gallery` | no | Image/card grid; first image attachment (URL or file) → optional `imageField` → placeholder |
| `wiki` (Pages) | no | Hierarchical pages (`page` entry type); `parent_field` (relation → entry), markdown reader |

### Composable meta-widgets (`palette_group: composable`)

Declarative grouping/sorting/filtering — no new React component per agent request.
See [META_WIDGETS.md](META_WIDGETS.md).

| `view_type` | Role |
|-------------|------|
| `composable_list` | Generic list |
| `composable_grid` | Card grid |
| `composable_board` | Kanban-style board via config |
| `composable_timeline` | Vertical chronology |

### Manifest-scoped composites

Profiles may declare `view_types[]` composites (e.g. `roadmap` built on
`composable_board`). These are **profile-local names** layered on palette keys,
not new palette entries. Resolved at compile time via
`register_composite_view_type` in the patch DSL.

---

## How profiles declare views

### Track scope (`scope: track`)

```yaml
content_profile_schema_version: 2
scope: track
track:
  entry_types: [...]
  views:
    - key: pipeline
      name: Pipeline
      view_type: kanban
      config:
        group_by: stage
      entry_type_keys: [deal]   # optional slice
  defaults:
    default_view: pipeline      # optional; feed remains available
  taxonomy:
    tag_groups: []
```

### App scope (`scope: app`)

Each track template under `app.tracks[]` carries its own `views[]` and
`defaults.default_view` using the same `view_type` keys.

### Runtime materialization

1. Manifest is compiled/validated against the backend view-type registry.
2. Merge-library or attach APIs create `View` nodes with `type` + `config`.
3. Frontend `ViewRenderer` resolves `view.type` → registered widget.
4. Unknown types → `MissingWidget` (profile applied, renderer not shipped).

---

## Developer workflow: add a palette view

### 1. Backend contract (canonical)

Create `backend/app/views/contracts/<type>.json`:

```json
{
  "type": "my_view",
  "label": "My view",
  "default_always_on": false,
  "platforms": ["web"],
  "palette_group": "core",
  "configurable": true,
  "hot_loadable": false
}
```

Register config schema in `backend/app/views/content_profile_view_types.py`
(if the view needs validated config keys beyond generic passthrough).

### 2. Sync frontend contract artifact

```bash
cd backend && venv/bin/python scripts/sync_view_contracts.py
# or from frontend/: npm run sync:view-contracts
```

Produces `frontend/src/views/contracts.json` for client introspection (not
authoritative — backend contracts are).

### 3. Web implementation + manifest

1. Implement widget under `frontend/src/components/views/` (or composable).
2. Add `frontend/src/views/manifests/<type>.manifest.ts`:

```typescript
import type { WidgetRegistration } from '../types';
import { MyWidget } from '../../components/views/MyWidget';

const manifest: WidgetRegistration = {
  type: 'my_view',
  component: MyWidget,
  meta: { label: 'My view', icon: SomeIcon, description: '...' },
};

export default manifest;
```

Manifests are auto-discovered via `import.meta.glob` in
`frontend/src/views/manifests/auto.ts` — no central import list.

### 4. Use in profiles

Reference `view_type: my_view` in library bundles (`backend/app/profiles/<slug>/`)
or attached profiles. Re-scan or bootstrap syncs library catalog rows.

---

## Catalog and library apply (future-facing)

Today’s building blocks already support a **catalog → choose → apply → go**
flow without hot-loading view code:

1. **Catalog** — library `ContentProfile` packages under the `ContentProfiles`
   registry (`GET /api/content-profiles`, workspace-scoped lists).
2. **Choose** — user/agent picks a package; optional `type_hint` ranking.
3. **Apply** — `merge-library` on App/Track or workspace app install with
   `library_content_profile_id`; backend validates every `view_type` against
   the palette registry.
4. **Go** — materialized `View` rows render on clients that ship matching
   implementations; substrate API exposes `view_types[]` with
   `palette_group`, `configurable`, `hot_loadable`, `supported_platforms`.

**Capability gating (recommended for catalog UI):**

- Show `supported_platforms` per view type from substrate.
- Warn when a profile requires a `view_type` the current client has not
  registered (parity check: contract keys vs `listWidgets()` keys on web).

Signed **code plugins** ([PLUGINS.md](PLUGINS.md)) remain the escape hatch for
genuinely new renderers; they still register a stable `view_type` key and ship
with the app build — not arbitrary runtime hot-load.

---

## API surfaces

| Surface | Purpose |
|---------|---------|
| `GET /api/content-profile-substrate` | Field types, view types (incl. palette metadata), plugins |
| `POST /api/content-profiles/validate` | Compile manifest; rejects unknown `view_type` |
| `POST .../content-profile/merge-library` | Apply library package views onto attached profile |
| `GET /api/content-profiles` | List catalog library packages |

---

## Compatibility shims (do not use in new code)

| Legacy path | Canonical path |
|-------------|----------------|
| `app.services.content_profile_view_types` | `app.views.content_profile_view_types` |
| `app.services.view_contract_catalog` | `app.views.view_contract_catalog` |
| `frontend/src/components/views/registry.tsx` | `frontend/src/views/registry.tsx` |

`frontend/src/components/views/*` remains the home for widget **components**;
registration metadata lives under `frontend/src/views/`.

---

## Related docs

- [REGION_SYSTEM.md](REGION_SYSTEM.md) — the "apex-style" region widgets
  (`form_region`, `layout_container`, `chart_region`, …) + the
  `create_wizard` step-kind catalog bundled with them
- [META_WIDGETS.md](META_WIDGETS.md) — composable config keys
- [COMPOSITES.md](COMPOSITES.md) — profile-scoped composite view types
- [PLUGINS.md](PLUGINS.md) — signed code plugins (rare)
- [UI_PACKS.md](UI_PACKS.md) — the standard recipe for a plugin that adds
  view types: pack manifest, namespacing, placement `scope`
- [AGENT_CONTRACT.md](AGENT_CONTRACT.md) — introspection + patch DSL
- [../../CONTENT_PROFILE.md](../platform/content-profile.md) — platform overview
