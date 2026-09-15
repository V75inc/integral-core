# Authoring content profiles and updating the App catalog

This guide describes how to **author** ContentProfile manifests (YAML or JSON), **attach** them to a Track or App via the API, and **extend the cataloged library** of reusable App suites and Track packs.

ContentProfile is integral's technical artifact for declarative schema + operational behavior. It applies to two scopes:

- **`scope: track`** — a single Track's customization (entry types, taxonomy, views)
- **`scope: app`** — a full **App**: one or more Tracks, cross-track relations, App-scoped Skills, Agents, Settings, Seeds, and Permissions

The user-facing term is **App**; ContentProfile is what implementers see in code and manifests. This guide covers both scopes, but the App scope is the primary authoring target — most production-grade installs are Apps, not standalone Tracks.

For the canonical reference on App-scoped operational sections (Skills, Agents, Settings, Seeds, Permissions), see [app_bundles_v1.md](app-bundles-v1.md). This document covers the authoring workflow that ties manifest → install → runtime.

For package metadata and migrations, see [content_profile_packages.md](content-profile-packages.md). For search index staging (`_cp_index`), see [content_profile_search_index.md](content-profile-search-index.md).

For the **view palette convention** (contracts, manifests, catalog apply, no
runtime hot-load), see
[../../docs/content-profiles/VIEW_PALETTE.md](../../docs/content-profiles/VIEW_PALETTE.md).

---

## What a ContentProfile does

A ContentProfile holds a **canonical manifest** (`manifest` on the node) that defines, depending on scope:

- **Entry types** and typed **fields** (stored and validated against `Entry.custom_fields`; forms can be driven from `EntryType.form_schema`)
- **Views** (declarative `SavedView`-style config: filters, kanban columns, calendar mapping, etc.)
- **Taxonomy** (hierarchical tag groups seeded per track when merged)
- **Relations** between entries (including cross-track rules when allowed by an app-level profile)
- **Skills** (App scope only) — declarative or custom operational capabilities the agent can invoke
- **Agents** (App scope only) — long-lived AI workers spawned on install, bound to skills with scope and schedules
- **Settings schema** (App scope only) — JSON Schema describing what's user-configurable post-install
- **Seeds** (App scope only) — initial Entries planted on install
- **Permissions** (App scope only) — defaults for who can install and what role new members get

Platform baseline is intentionally minimal: every new Track starts with only:

- base **Post** entry type
- default **Feed** view

Everything else is expected to come from ContentProfile merges.

Manifests are **compiled and validated** when you PATCH a profile with `manifest` or `manifest_yaml` (see [content_profile_runtime.py](../app/services/content_profile_runtime.py)).

---

## When to use `scope: track` vs `scope: app`

| Choose | When |
|---|---|
| **`scope: track`** | Single-table customization. One Track needs its own entry types, taxonomy, and views. No cross-table relations. No operational behavior beyond what views provide. Example: a custom "Bookmarks" Track on a personal Workspace. |
| **`scope: app`** | Two or more Tracks that share a purpose, relate to each other, or need shared operational behavior (skills, agents, schedules, settings). The collection installs and uninstalls as one unit. Example: CRM (Contacts + Opportunities + Projects with cross-track relations), Content Factory (Source Material + Content Pipeline + Agent Runs + Performance with drafting agent). |

If the use case requires the user to install multiple separate Tracks and manually configure their connections, it's an App. If it's truly one Track with custom schema, it's a Track-scoped profile.

---

## Scopes: track vs app

| Scope | Attached to | Use case |
|-------|-------------|----------|
| `track` | Track's attached content profile | One Track: its own entry types, views, tags |
| `app` | App's attached content profile | Several **track types** under `app.tracks[]`, each with entry types, views, taxonomy; optional `app.relations` for cross-track rules; optional `app.skills`, `app.agents`, `app.settings_schema`, `app.seeds`, `app.permissions` for the operational layer |

Runtime resolution for a given **Track** prefers the Track's own manifest when `scope: track`. If the Track manifest is app-scoped or missing a usable tier, the system may resolve a tier from a **parent App** profile (matched by Track `template_id` or title slug vs `app.tracks[].key`).

---

## Authoring workflow

1. Write **YAML** (or JSON) matching the canonical shape below.
2. **Apply** it by updating the attached profile:
   - **Track:** `PATCH /tracks/{track_id}/content-profile` with `manifest_yaml` (string) or `manifest` (object), and `scope: track` if needed.
   - **App:** `PATCH /apps/{app_id}/content-profile` with the same, and `scope: app`.
3. The server runs `compile_canonical_manifest()` and persists the normalized manifest. Invalid manifests return **400** with a clear error.
4. To **materialize** entry types, tags, and views onto a Track from a **library** package, use **merge-library** (see below), or create types/tags/views via existing APIs.

**Dependencies:** YAML upload requires `PyYAML` on the backend (`pyproject.toml`).

---

## Canonical manifest shape (v2)

Manifest schema version is **2**. v1 has been removed; the compiler accepts v2 only.

Top-level keys:

- `content_profile_schema_version`: **2**
- `scope`: `"track"` | `"app"`
- `package` (optional): distribution metadata (`name`, `version`, `description`, `publisher`, `tags`, `license`, `homepage`)
- `migrations` (optional): list of `{ from_version, to_version, … }` for tooling

### Track scope

```yaml
content_profile_schema_version: 2
scope: track
package:
  name: my-pack
  description: Example track-level profile
track:
  entry_types:
    - key: post
      name: Post
      icon: document
      fields: []
    - key: task
      name: Task
      icon: check
      fields:
        - key: status
          name: Status
          type: select
          enum: [todo, in_progress, done]
          required: true
        - key: estimate_hours
          name: Estimate (hours)
          type: number
          index: true
  taxonomy:
    tag_groups:
      - key: priority
        name: Priority
        tags:
          - key: high
            name: High
            color: "#DC2626"
          - key: low
            name: Low
            color: "#6B7280"
            parent_key: null
  views:
    - key: feed
      name: Feed
      view_type: feed
      is_default: true
    - key: board
      name: Board
      view_type: kanban
      kanban_columns:
        - key: todo
          label: To Do
        - key: in_progress
          label: In Progress
        - key: done
          label: Done
  defaults: {}
```

### Field types (summary)

Supported `type` values include: `text`, `number`, `boolean`, `date`, `datetime`, `markdown`, `json`, `select`, `multi_select`, `relation`, `computed`.

### Base field overrides (body + attachments)

Each entry type can optionally define reusable base field presentation under `base_fields`, so specialized types can relabel base controls instead of adding redundant custom fields.

```yaml
- key: interaction
  name: Interaction
  fields:
    - key: interaction_date
      name: Interaction date
      type: date
  base_fields:
    body:
      label: Outcome
      placeholder: Summarize the result of this interaction
      help: Capture key outcomes, decisions, and follow-up notes.
    attachments:
      label: References
      help: Add files or links used during this interaction.
      allow_file_upload: true
      allow_url_reference: true
```

Defaults (when omitted) are:
- `base_fields.body.enabled: true`, label `Body`
- `base_fields.attachments.enabled: true`, label `Attachments`

Optional integer **`order`** on `base_fields.body` and `base_fields.attachments` merges those slots with custom `fields` when rendering the create-entry form: lower numbers appear earlier. Typical defaults in the UI are title at `0`, each custom field at its own `order` (or `200`, `210`, … if unset), body at `1000`, and attachments at `1100` unless overridden.

In the web composer, the default body label **Body** is not shown as a heading. Placeholder copy is generated for the signed-in user: **`Describe this entry, {firstName}`** (or **`Describe this entry`** with no name). If you set a **non-default** `body.label` (for example `Outcome`), the placeholder becomes **`Describe {label}, {firstName}`** (or **`Describe {label}`**). An explicit `body.placeholder` from the schema overrides the default-line copy when the label is still **Body**; for a custom label, it is appended after **` · `** when both are set.

**Relation** fields use a nested `relation` object, for example:

```yaml
- key: contact
  name: Contact
  type: relation
  relation:
    target_entry_types: [contact]
    allow_cross_track: false
    many: false
```

Use `allow_cross_track: true` and optional `target_track_types` when an **App** profile permits references across Tracks within the App.

**Cross-App relations.** A relation field can point at Entries in a *different* App within the same Workspace (e.g., a Payroll App's `employees` field pointing at the HR App's employee records). Set `target_app` to the target App template key and add `allow_cross_app: true`:

```yaml
- key: employees
  name: Employees
  type: relation
  relation:
    target_app: hr_app                       # App template key (matches package.name)
    target_track_types: [employees]          # Track key inside the target App
    target_entry_types: [employee]           # Entry type key
    allow_cross_app: true                    # required to traverse App boundary
    allow_cross_track: true
    many: true
    label_field: custom_fields.full_name     # how the reference renders in UI
    on_target_uninstall: block               # 'block' | 'null' | 'archive_self'
    resolution: workspace                    # 'workspace' | 'instance:<app_id>'
```

When using cross-App relations, the App must also declare an `app.requires_apps[]` entry for each target App so install and uninstall lifecycle protections work cleanly. See [app_bundles_v1.md §10.4](app-bundles-v1.md#104-cross-app-relations) and [§10.5](app-bundles-v1.md#105-app-dependencies-requires_apps) for the full semantics — including permission propagation at read time, handling of multiple App installations, and uninstall protection.

### App scope (multiple track types)

```yaml
content_profile_schema_version: 2
scope: app
app:
  tracks:
    - key: contacts
      name: Contacts
      description: Optional human-readable blurb stored on the Track as purpose when auto-provisioned.
      entry_types:
        - key: contact
          name: Contact
          fields:
            - key: email
              name: Email
              type: text
      taxonomy:
        tag_groups: []
      views:
        - key: feed
          name: Feed
          view_type: feed
          is_default: true
    - key: projects
      name: Projects
      entry_types:
        - key: project
          name: Project
          fields:
            - key: contact
              name: Contact
              type: relation
              relation:
                target_entry_types: [contact]
                target_track_types: [contacts]
                allow_cross_track: true
      taxonomy:
        tag_groups: []
      views: []
  relations: []
  defaults: {}
```

Match each real **Track** to an `app.tracks[].key` using the Track's `template_id` or a slug of its title (see resolver in `content_profile_runtime.py`).

### Worked example: cross-App relations (HR ↔ Payroll)

The canonical case for cross-App relations: an HR App owns employee records; a Payroll App references those employees on each payroll run. Without cross-App relations the choice is awkward — either bundle HR and Payroll into one monolithic App (loses modularity), or duplicate employee records in each App (loses single source of truth). Cross-App relations make the modular shape work.

**`hr_app` manifest (the referenced side — no special declarations needed):**

```yaml
content_profile_schema_version: 2
scope: app
package:
  name: hr_app
  version: 1.0.0
  description: People + roles + employment records.
app:
  tracks:
    - key: employees
      name: Employees
      entry_types:
        - key: employee
          name: Employee
          fields:
            - { key: full_name,      name: Full name,        type: text,    required: true,  index: true }
            - { key: email,          name: Email,            type: text,    required: true,  index: true }
            - { key: title,          name: Job title,        type: text }
            - { key: start_date,     name: Start date,       type: date }
            - { key: employment_type, name: Employment type, type: select, enum: [full_time, part_time, contractor] }
      views:
        - { key: feed, name: All employees, view_type: feed, is_default: true }
```

**`payroll_app` manifest (the referencing side — declares dependency + cross-App relation):**

```yaml
content_profile_schema_version: 2
scope: app
package:
  name: payroll_app
  version: 1.0.0
  description: Periodic payroll runs over employees managed in the HR App.
app:
  requires_apps:
    - key: hr_app
      min_version: 1.0.0
      optional: false
      reason: "Payroll runs reference employees from HR. Hard dependency."

  tracks:
    - key: payroll_runs
      name: Payroll Runs
      entry_types:
        - key: payroll_run
          name: Payroll Run
          fields:
            - { key: pay_period_start, name: Period start, type: date, required: true }
            - { key: pay_period_end,   name: Period end,   type: date, required: true }
            - { key: status, name: Status, type: select, enum: [draft, approved, processed, paid], required: true }
            # The cross-App relation:
            - key: employees
              name: Employees
              type: relation
              relation:
                target_app: hr_app
                target_track_types: [employees]
                target_entry_types: [employee]
                allow_cross_app: true
                allow_cross_track: true
                many: true
                label_field: custom_fields.full_name
                on_target_uninstall: block        # don't let HR be uninstalled while payroll references exist
                resolution: workspace             # single HR install assumed; pin if multiple
      views:
        - { key: feed, name: All runs, view_type: feed, is_default: true }
        - key: pipeline
          name: Pipeline
          view_type: kanban
          group_by: custom_fields.status
          kanban_columns:
            - { key: draft,     label: Draft }
            - { key: approved,  label: Approved }
            - { key: processed, label: Processed }
            - { key: paid,      label: Paid }
```

**What happens at install time:**

1. Install Payroll → compiler validates manifest, checks `requires_apps`
2. If HR isn't installed → install pauses, UI prompts "This App requires HR App. Install it first?"
3. If HR is installed but multiple installs exist → install pauses, UI prompts "Which HR App should Payroll reference?"
4. If exactly one HR install → install proceeds; cross-App relation schema materializes
5. When a user creates a Payroll Run, the `employees` field's picker queries the HR App's `employees` track for matching entries
6. Viewing a Payroll Run shows employees by `full_name`; clicking through navigates into the HR App's employee detail (subject to viewer's permissions on HR)

**What happens at uninstall time:**

- Trying to uninstall HR while Payroll Runs reference employees → blocked; error lists referencing Payroll Runs
- User can change Payroll's `on_target_uninstall` to `null` (and resave the manifest version) if they want HR uninstall to nullify the references instead
- Force-uninstall (`?force=true`) bypasses the check; emits prominent ChangeEvent

This is the recommended pattern for any business-domain modularity in integral: separate concerns into separate Apps, declare dependencies explicitly, use cross-App relations for shared entities. A Performance Review App could reference the same HR employees on review entries; a Time Off App could too; all without duplicating employee data.

### App scope — operational layer (skills, agents, settings, seeds, permissions)

App-scoped manifests gain five additional optional sections that turn an App into a fully functional agentive application. The full reference lives in [app_bundles_v1.md](app-bundles-v1.md); this guide covers the authoring workflow that ties them together.

**Authoring sequence for a full App bundle:**

1. **Schema first.** Write `app.tracks[]` with entry types, taxonomy, views, and relations. Verify the App installs cleanly and the schema reads sensibly in the UI before adding operational behavior.
2. **Settings schema** (`app.settings_schema`). Define the JSON Schema describing what's user-configurable post-install (publish cadence, target platforms, brand voice references, etc.). The install-time form is rendered from this schema. See [app_bundles_v1.md §7](app-bundles-v1.md#7-settings-schema).
3. **Skills** (`app.skills[]`). Declare each capability the App ships with. Prefer `kind: declarative` (prompt + tool sequence over Integral's tool manifest) over `kind: custom` (Python handler). Each skill is a `skills/<key>/SKILL.md` JV bundle in the App directory. Skills that call `integral_*` tools **must** declare `extends: action:integral/embedded_integral_action` and `requires-actions: [EmbeddedIntegralAction]` so they inherit the resident propose/stage and identity discipline — see [app-bundles-v1.md §5.2.1](./app-bundles-v1.md#521-extending-the-embedded-integral-base-sop-required-for-integral-tools). Public skills surface in the resident agent's **workspace overlay** after install — [workspace-agent-profile.md](./workspace-agent-profile.md). Full skills reference: [app-bundles-v1.md §5](./app-bundles-v1.md#5-skills-layer).
4. **Agents** (`app.agents[]`). Declare each long-lived AI worker the App registers on install. Each agent has a persona file (under `agents/<name>.yaml`), a bound skill set, scope (`app` or `workspace`), staging policy, and optional default schedules. See [app_bundles_v1.md §6](app-bundles-v1.md#6-agents-layer).
5. **Seeds** (`app.seeds`). Optional initial Entries planted on install. Useful for brand voice starter docs, README entries, sample records.
6. **Permissions** (`app.permissions`). Defaults for install eligibility and post-install member roles.

**Recommended App bundle file layout:**

```
backend/app/profiles/my-app/
├── profile.yaml                 # integral_profile_version: 3; scope: app
├── skills/
│   ├── carousel_drafter/
│   │   └── SKILL.md             # extends + allowed-tools + domain workflow
│   └── example_skill/
│       └── SKILL.md
├── agents/
│   └── drafter.yaml             # agent persona
└── seeds/
    └── brand_voice_starter.md   # seed entry body

# When trust_tier: trusted + Python hooks:
backend/app/profiles/my_app/     # underscored sibling for hyphenated slugs
└── tools/
    ├── __init__.py
    └── example.py
```

The manifest references files by relative path (`prompt_template: skills/carousel_drafter/SKILL.md`, `persona_ref: agents/drafter.yaml`). v3 authoring may use bare skill keys (`skills: [carousel_drafter]`) — the loader expands each to `skills/{key}/SKILL.md`. At install time, files are read from the bundle directory on disk. Use `POST /workspaces/{ws}/apps/install` for full operational registration (skills, agents, hooks) — merge-library alone updates manifest JSON only unless the App is active and sync runs.

For App operational layer (skills, agents, tools, hooks), see [app-bundles-v1.md](./app-bundles-v1.md).

### Optional track initialization (app-level apply only)

When an **app-scoped** library package is applied to a **Workspace** (for example `POST /workspaces/{id}/apps` with `library_content_profile_id`, or `POST /apps/{id}/content-profile/merge-library`), the server may **create real `Track` nodes** from `app.tracks[]`:

- **`app.defaults.provision_prescribed_tracks`**: optional boolean. If omitted and `app.tracks` is non-empty, canonical compilation defaults this to **true**. Set **`false`** to merge the manifest into the App content profile **without** auto-creating Tracks.
- **`provision_on_create`** on each **`app.tracks[]`** entry: optional boolean, default **true**. Set **`false`** to keep that Track type in the manifest for manual creation (e.g. `app_track_type_key` on `POST /tracks`) and runtime resolution, but **not** provision it when the package is applied.
- **`description`** on each **`app.tracks[]`** entry: optional string. When Tracks are auto-provisioned, this is copied to the new `Track.purpose` field so prefab bundles can explain each Track in the UI.
- **`public_share`** on each **`app.tracks[]`** entry: optional block declaring that the Track is *intended* to be publicly shared, and with which permissions:

  ```yaml
  - key: employee_onboarding
    name: Employee Onboarding
    public_share:
      enabled: true
      permissions:
        create_entries: true    # anonymous visitors may submit
        read_entries: false     # ...and read nothing back
        update_entries: false
        read_comments: false
        create_comments: false
  ```

  This is **intent, not activation** — provisioning never mints a share link from it. Share tokens are hash-only and disclosed once at mint, so a token minted server-side during provisioning has no recipient: the plaintext is discarded and the link is unusable, while still counting as an active public share. Instead, the declaration becomes the **default** the owner is offered when they enable sharing (`GET /tracks/{id}/public-share` returns it with `permissions_source: "manifest"`), and that explicit enable mints the real token and returns it once.

  The five permission keys above are the complete set; an unknown key is a **hard compile error** rather than a silent drop, and every value must be boolean. These gate anonymous access, so a typo must not quietly widen the surface. Omitting the block, or setting `enabled: false`, means no declaration.

**Track-level** package merges (`POST /tracks` with `library_content_profile_id`, or track merge-library) **never** run this logic; they only merge tier data into the Track's attached profile.

---

## API reference (creating / updating a profile)

### Track attached profile

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/tracks/{track_id}/content-profile` | Read attached profile (includes `manifest`) |
| PATCH | `/tracks/{track_id}/content-profile` | Update `name`, `description`, `version`, **`manifest`**, **`manifest_yaml`**, **`scope`** |

Example (JSON body fields as query/form style per jvspatial/FastAPI conventions used in this project):

- `manifest_yaml`: full YAML string, or
- `manifest`: already-compiled JSON object,
- `scope`: `track`.

### App attached profile

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/apps/{app_id}/content-profile` | Read App profile |
| PATCH | `/apps/{app_id}/content-profile` | Same as Track; use `scope: app` for App manifests |

---

## Consuming a library package on a Track or App

Library packages are **ContentProfile** nodes with `library_package: true`, listed under the app **ContentProfiles** registry. They are **read** via:

- `GET /content-profiles` — list packages
- `GET /content-profiles/{id}` — package detail + manifest

**Merge** copies manifest-driven **entry types**, **tags/taxonomy**, **views**, and (for App-scoped packages) **skills, agents, settings_schema, seeds, permissions** into the target attached profile (and creates Track-scoped nodes when a Track is provided).

| Action | Endpoint |
|--------|----------|
| Merge library into **Track** profile | `POST /tracks/{track_id}/content-profile/merge-library` with `library_content_profile_id` |
| Merge library into **App** profile | `POST /apps/{app_id}/content-profile/merge-library` with `library_content_profile_id` |
| Merge when **creating** a Track | `POST /tracks` with `library_content_profile_id` (and existing track CP must exist) |
| Merge when **creating** an App | `POST /workspaces/{id}/apps` with `library_content_profile_id` |

Merge logic lives in [content_profile_merge.py](../app/services/content_profile_merge.py). It understands **canonical v2** manifests (`track.*` / `app.*` tiers).

---

## Library-first create flow

Create flows support **optional** library application at creation time.

### Create Track

`POST /tracks` accepts:
- base fields (`title`, `purpose`, `visibility`, …)
- optional `library_content_profile_id`
- optional `app_track_template_content_profile_id` (when creating in an App)

If no library is chosen, the Track remains **base only** (`Post` + `Feed`).

### Create App

`POST /workspaces/{id}/apps` accepts:
- base fields (`name`, `description`, `workspace_id`)
- optional `library_content_profile_id`

When provided, the selected App template is installed onto the newly created App. This triggers the full install lifecycle (see [app_bundles_v1.md §9](app-bundles-v1.md#9-lifecycle)): materialize Tracks, register Skills + Agents, render settings form, plant Seeds.

### Frontend behavior

- Track creation UI exposes **No library (base only)** plus available Track packs
- App creation UI exposes available App suites from the App Catalog
- Choosing no package preserves the minimal baseline

---

## Updating the App catalog (library)

The HTTP API exposes **list/get only** for library packages. **Adding or changing** a cataloged package is a **backend/data** operation today.

Current seeded packages (Phase 31 — decomposed from legacy monolithic CRM+PM):
- `crm` — App-scoped (Contacts, Opportunities, Communications, Company Wiki)
- `projects` — App-scoped (Projects track + anchored project-details / financials / contracts)
- `sales` — App-scoped (Discovery, Scoping, Pricing Rubrics, Project Proposals)
- `portfolio` — App-scoped (Case Studies)
- `Content Calendar` — Track-scoped (single track for editorial planning)
- *Content Factory* — App-scoped (Source Material, Content Pipeline, Agent Runs, Performance with drafting agents) — schema layer shipped (v1.0.0 seeded; operational layer — skills, agents, settings_schema — lands with manifest v2 rollout)

### Starter suite design summary

The consulting lifecycle is split across four standalone apps:

**`crm`** — contacts + opportunities tracks; soft `requires_apps: [projects]` (`optional: true`) so CRM installs alone; won-opportunity → project transform lights up when Projects is present.

**`projects`** — main `projects` track (`project` entry type) plus anchor templates `project-details`, `project-financials`, `contracts-legal`. Cross-app `project.contact` → CRM contacts.

**`sales`** — pre-sales tracks; `proposal_to_project` transform targets the Projects app.

**`portfolio`** — case studies linked to completed (`done`) projects via cross-app relation.

Legacy note: the retired `crm-plus-pm-suite` monolith is replaced by the four bundles above; use `crm-pm-workspace` for a turnkey CRM + Projects workspace install.

### What "library" / "App Catalog" means in the graph

- Registry node: `ContentProfiles` (`CONTENT_PROFILES_REGISTRY_ID`)
- Each App template: a `ContentProfile` with `library_package: true` and `scope: app`
- Each Track-scoped package: a `ContentProfile` with `library_package: true` and `scope: track`
- Membership: `CATALOGS` edge from the registry to the package (see `ensure_catalog_edge` in [app_graph.py](../app/services/app_graph.py))

### Adding a new library package (developer steps)

1. **Author** a manifest (YAML/JSON) with `scope: track` or `scope: app`. For merges into **Tracks**, a **track-scoped** library manifest is the usual choice. For full Apps, use `scope: app` with the operational layer sections (`skills`, `agents`, `settings_schema`, etc.).
2. **Drop bundle in** `backend/app/profiles/<slug>/profile.yaml` (canonical path). Optional skills live under `backend/app/profiles/<slug>/skills/<key>/SKILL.md`.
3. **Bootstrap or rescan**:
   - bootstrap path: `ensure_integral_app_graph()` -> shared disk sync (`content_profile_library_sync.py`)
   - hot-load path: `POST /api/admin/profiles/rescan`
4. The sync layer performs slug-stable upsert (`metadata.slug`) and stale-bundle reconciliation for removed folders.
5. Check diagnostics with `GET /api/admin/profiles` (`issues[]`) before publishing or applying in production.

### Widget contribution guide (view palette)

Integral treats `feed` as baseline (injected when a tier has no feed view) and
all other views as **palette keys** profiles compose via declarative `config`.
See [VIEW_PALETTE.md](../../docs/content-profiles/VIEW_PALETTE.md) for the full
convention. Summary:

1. **Backend contract first**: register/update the contract file under `backend/app/views/contracts/*.json` (key, platforms, palette metadata). Contract rows include `hot_loadable` and should remain `false` for palette-builtins.
2. **Sync frontend artifact** with `cd backend && venv/bin/python scripts/sync_view_contracts.py` (or `npm run sync:view-contracts` in `frontend/`).
3. **Implement web renderer** in `frontend/src/views` (canonical web view-library root).
4. **Register declaratively** by adding a `frontend/src/views/manifests/*.manifest.ts` file that exports a `WidgetRegistration`.
5. **Backend registry behavior** lives in `backend/app/views/content_profile_view_types.py` (config schema + runtime validation rules).
6. **Expose** the capability in a content profile view spec (`view_type`/`type`).
7. **Optional mobile parity**: implement the same key in mobile renderer code; profile manifests do not change.
8. **Merge/apply** the profile so persisted `View` rows are created for Tracks that need it.
9. **Verify** widget appears in the Track UI only when enabled by saved view capabilities.

If a profile references an unregistered capability, the UI renders a clear missing-widget state so developers or AI agents can add it without changing core runtime contracts.

### AI-authoring contract

For agent-authored profile changes, keep outputs deterministic and compiler-friendly:

- Emit canonical manifest v2 shape (`scope`, `track`/`app`, normalized `entry_types`, `views`, `taxonomy`, optional operational sections)
- For relation fields, always specify explicit `relation` constraints (`target_entry_types`, optional `target_track_types`, `allow_cross_track`, `many`). For cross-App relations, add `target_app`, `allow_cross_app: true`, `label_field`, `on_target_uninstall`, and `resolution`; pair with `app.requires_apps[]`
- Use view capability keys that are valid runtime widget types (`feed`, `kanban`, `table`, `calendar`, `gallery`, or newly registered types)
- For App-scoped manifests authoring the operational layer: prefer `kind: declarative` for skills; reference persona files by path; use JSON Schema for settings
- Expect compile-time validation failures for unsupported field/view types or malformed config; treat these errors as authoring feedback loops
- Validate by merging into a test App/Track and asserting materialized entry types/views/skills/agents match manifest intent

### Updating an existing library package

1. Change the `ContentProfile`'s `manifest` / `version` in the database (or via a small admin/maintenance script).
2. If you rely on **merge** into Tracks/Apps, note that merge **skips** duplicate entry types/tags **by name** for that Track; it does not automatically remove old artifacts. For breaking changes, document a **migration** in `migrations` and optionally bump `version`.
3. Teams **re-merge** or re-apply templates as needed (`merge-library` or Track template flows under Apps).

### Bumping versions and migrations

Use the `package` and `migrations` sections in the manifest (see [content_profile_packages.md](content-profile-packages.md)). The compiler validates that each `migrations[]` entry includes `from_version` and `to_version`.

---

## Quick checklist

For a **Track-scoped** profile:

- [ ] Set `content_profile_schema_version: 2` and `scope: track`
- [ ] Define `entry_types` with `fields` (and `relation` where needed)
- [ ] Define `taxonomy.tag_groups` for seeded hierarchical tags
- [ ] Define `views` with `view_type` in `feed`, `kanban`, `table`, `calendar`, `gallery`
- [ ] PATCH attached profile with `manifest_yaml` or `manifest`
- [ ] Optional: set `index: true` on fields for `_cp_index` extraction (see search doc)

For an **App-scoped** profile:

- [ ] Set `content_profile_schema_version: 2` and `scope: app`
- [ ] Define `app.tracks[]` with each track type (entry_types, taxonomy, views)
- [ ] Define `app.relations[]` for cross-track relations between entry types
- [ ] If any field references entries in another App, declare `target_app` + `allow_cross_app: true` on the relation and add the App to `app.requires_apps[]` (see HR/Payroll worked example above)
- [ ] Author `app.skills[]` (prefer `kind: declarative`; reference `prompt_template` files in `skills/<name>/`)
- [ ] Author `app.agents[]` with persona refs, bound skills, scope, schedules, staging policy
- [ ] Define `app.settings_schema` as JSON Schema for post-install configuration
- [ ] Add `app.seeds` for initial Entries (brand voice starters, README entries)
- [ ] Set `app.permissions` defaults
- [ ] PATCH attached App profile with `manifest_yaml` or `manifest`
- [ ] For reusable App templates: register under ContentProfiles registry + install via `POST /workspaces/{id}/apps`
