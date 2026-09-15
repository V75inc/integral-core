# App Bundles v1 — Architecture

Canonical reference for **Apps** in integral: what they are, how they're packaged as ContentProfile bundles, how Skills and Agents compose with the substrate, and how Apps install, run, update, and uninstall.

This document supersedes the pre-rename treatment of Spaces in [ARCHITECTURE.md](../product/ARCHITECTURE.md) and extends [content_profile_authoring_and_library.md](content-profile-authoring-and-library.md) with the manifest changes that introduce App-level skills, agents, settings, and seeds.

**Status:** v1 — **shipped** (install lifecycle, skill overlay, bundle tools/hooks, library sync).
**Audience:** integral contributors, App authors (community + consulting deliverables), MCP tool implementers.

---

## 1. Purpose

Integral is a **declarative agentive application runtime**. Schema and operational behavior are bundled together as a **ContentProfile**, materialized at install time as either a Track-level customization (light) or an **App** — a coherent operational domain inside a Workspace (full).

This document defines the App bundle shape so that:

- Internal contributors have one canonical reference for the App model.
- Community authors can build Apps that install cleanly across deployments.
- Consulting engagements can deliver client-specific Apps as portable artifacts.
- The AI-native layer (jvagent, MCP, Skills) has a well-defined contract for how App-scoped capabilities surface at runtime.

---

## 2. The model

Integral organizes content and behavior in four nested concepts.

```
Workspace
└── App                      (coherent operational domain; optional)
    └── Track                (table-like collection of entries)
        └── Entry            (record)
```

| Concept | Analogue | Scoping role |
|---|---|---|
| **Workspace** | Operating environment | Top-level container for a user or organization. All access begins here. |
| **App** | Application | Bounded operational domain inside a Workspace. Bundles related Tracks, cross-track relations, App-scoped Skills, App-scoped Agents, and configurable settings. |
| **Track** | Table | Collection of entries of one or more declared types. Can exist directly under a Workspace for light usage, or under an App for bundled usage. |
| **Entry** | Record | Single unit of content, shaped by the EntryType declared in the attached ContentProfile. |

Apps are **optional**. A Workspace can hold loose Tracks for one-off use. Apps are the right primitive when **two or more Tracks share a purpose, relate to each other, or need shared operational behavior** (skills, agents, schedules, settings).

### Renamed from WorkspaceApp → App

Prior to this document, the App primitive was the `WorkspaceApp` Node class (user-facing label: Space). The rename drops the workspace prefix and aligns the class name with the user-facing concept — Apps are installable, configurable, and (eventually) discoverable in a public catalog. See [§13 Migration from v1 / Space](#13-migration-from-v1--space) for the executed rename shape.

---

## 3. ContentProfile as the bundle format

A **ContentProfile** is the technical artifact that defines an installable customization. It carries a single manifest and applies to either a Track or an App.

| ContentProfile scope | Materializes as | Manifest root key |
|---|---|---|
| `scope: track` | Track-level customization (entry types, taxonomy, views) | `track:` |
| `scope: app` | Full App (one or more Tracks, cross-track relations, App-scoped Skills/Agents/Settings) | `app:` |

The terminology in user-facing surfaces is **App**, not ContentProfile. ContentProfile remains the internal/technical term because the same artifact can materialize as either a Track customization or a full App. Authors and reviewers will encounter both terms; consumers will only see App.

| User-facing term | Technical term |
|---|---|
| App | ContentProfile node with `scope: app` |
| App template (in catalog) | ContentProfile node with `library_package: true` |
| App suite | User-facing label for a ContentProfile library package with `scope: app`; synonym for "App template" in catalog-facing copy |
| Track pack | User-facing label for a ContentProfile library package with `scope: track` |
| Install an App | Merge a library ContentProfile into a Workspace |
| App settings | Settings derived from the manifest's `settings_schema` |

---

## 4. Manifest v2 — full shape

Manifest schema version is **2**. Integral is pre-production; v1 is the historical shape and is no longer supported. All existing manifests are migrated to v2 in place during the App-bundles rollout — no in-memory upgrade shim, no compatibility layer. The compiler accepts v2 only; v1 manifests fail validation.

v2 introduces the operational layer sections (`skills`, `agents`, `settings_schema`, `seeds`, `permissions`) alongside the schema sections (`tracks`, `relations`, `defaults`) that existed in v1.

### 4.1 Top-level keys

```yaml
content_profile_schema_version: 2
scope: app                          # 'app' | 'track'
package:
  name: my-app
  version: 1.0.0
  description: One-paragraph user-facing description.
  publisher: ...
  tags: [...]                       # discovery keywords
  license: MIT                      # optional, for catalog
  homepage: https://...             # optional
app: { ... }                        # required when scope: app
track: { ... }                      # required when scope: track
migrations: [ ... ]                 # optional, schema migrations between package versions
```

### 4.2 App scope — full reference

```yaml
content_profile_schema_version: 2
scope: app

package:
  name: content-factory
  version: 1.0.0
  description: Multi-stream content production substrate.
  tags: [content, production, agents, publishing]

app:
  # ─── Dependency layer (optional) ───────────────────────
  requires_apps:
    # Hard dependency — install blocked without this; uninstall of dep blocked while we're installed
    - key: hr_app
      min_version: 1.0.0
      optional: false
      reason: "Cross-App references to employees managed in HR."
    # Soft dependency — install proceeds; references dangle as 'Restricted (App not installed)' until present
    - key: notifications_app
      optional: true
      reason: "Optional: surface notifications when content is published."

  # ─── Schema layer ──────────────────────────────────────
  defaults:
    provision_prescribed_tracks: true
    default_track: content_pipeline
    default_view: pipeline_board

  tracks:
    - key: source_material
      name: Source Material
      description: Raw inputs drafters pull from.
      provision_on_create: true
      entry_types: [ ... ]
      taxonomy: { tag_groups: [ ... ] }
      views: [ ... ]
    - key: content_pipeline
      # ... etc

  relations:
    - from: { track: content_pipeline, entry_type: content_piece, field: source_materials }
      to:   { track: source_material,  entry_type: source_material }
      many: true

  # ─── Operational layer (NEW in v2) ─────────────────────
  skills:
    - key: carousel_drafter
      name: Carousel Drafter
      kind: declarative              # 'declarative' | 'custom'
      description: |
        Drafts N Spiritual Bytes carousels for IG by pulling source material
        with matching themes and assembling slide texts.
      tools_required:
        - integral_create_entry
        - integral_patch_entry
        - integral_retrieve
      prompt_template: skills/carousel_drafter/SKILL.md
      parameters:
        count: { type: integer, default: 5, minimum: 1, maximum: 20 }
        themes: { type: array, items: { type: string }, default: [] }
      outputs:
        - kind: staged_entries
          track: content_pipeline
          entry_type: content_piece

  agents:
    - key: drafter_agent
      name: Drafter Agent
      persona_ref: agents/drafter.yaml      # persona file inside the bundle
      skills: [carousel_drafter, performance_reviewer]
      scope: app                            # see §10 Scope rules
      default_schedules:
        - name: "Weekly carousel drafting"
          cron: "0 8 * * 1"
          skill: carousel_drafter
          params: { count: 5 }
      staging: required                     # 'required' | 'optional' | 'disabled'

  settings_schema:
    type: object
    properties:
      publish_cadence:
        type: string
        enum: [weekly, twice_weekly, daily]
        default: weekly
        title: Publish cadence
      target_platforms:
        type: array
        items: { type: string, enum: [instagram, youtube, tiktok, linkedin, x, email, blog] }
        default: [instagram]
        title: Target platforms
      brand_voice_source_ids:
        type: array
        items: { type: string }
        title: Brand voice sources
        description: Entry IDs in source_material that define the brand voice for agents.

  permissions:
    install_requires_workspace_role: member
    default_app_role: member

  seeds:
    - track: source_material
      entries:
        - title: "Brand Voice — Starter Template"
          body: |
            Replace this with your brand voice document. Tag it with `voice:<your_brand>`.
          tags: [voice:starter]
          custom_fields:
            source_type: writing
```

### 4.3 Track scope — light usage

Track-scoped manifests retain the same structural shape (entry types, taxonomy, views) as in earlier ContentProfile versions; v2 adds optional `skills:` for Track-scoped operational behavior. See [content_profile_authoring_and_library.md §Canonical manifest shape (v2)](content-profile-authoring-and-library.md#canonical-manifest-shape-v2) for the Track-scope reference.

---

## 5. Skills layer

A **Skill** is a named, declarative operational capability that an Agent can invoke. Skills are the unit of "what the App can do."

### 5.1 Skill kinds

| Kind | Implementation | When to use |
|---|---|---|
| `declarative` | Prompt + tool sequence over integral's MCP toolkit. Pure YAML/Markdown, no Python. | Most domain skills. Drafting, summarizing, categorizing, scheduling, notifying — anything expressible as "compose calls to existing tools." |
| `custom` | Python handler script alongside SKILL.md, executed by jvagent. | Edge cases that genuinely need imperative logic: complex computations, external API calls outside the existing connectors, stateful workflows that can't be expressed as tool sequences. |

**Authoring bias:** prefer `declarative`. The MCP toolkit auto-synthesizes `integral_<verb>_<resource>` tools from every backend endpoint, which is already a rich enough surface that most operational behavior can be expressed declaratively. `custom` is the escape hatch, not the default.

### 5.2 Declarative skill shape

A declarative skill is fully defined in the manifest plus a prompt file:

```yaml
- key: carousel_drafter
  name: Carousel Drafter
  kind: declarative
  description: Drafts N carousels for IG from source material with matching themes.
  tools_required:
    - integral_create_entry
    - integral_patch_entry
    - integral_retrieve
  prompt_template: skills/carousel_drafter/SKILL.md
  parameters:
    count: { type: integer, default: 5, minimum: 1, maximum: 20 }
    themes: { type: array, items: { type: string }, default: [] }
  outputs:
    - kind: staged_entries
      track: content_pipeline
      entry_type: content_piece
```

The `SKILL.md` file (frontmatter + body; see §5.2.1 for `extends`):

```markdown
---
name: carousel_drafter
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_retrieve
  - integral_stage_entry
---

# Carousel Drafter

You are drafting {{count}} Spiritual Bytes carousels for Instagram. Themes: {{themes}}.

1. Open an `agent_run` entry on the `agent_runs` track. Set status=`running`.
2. Query source material via `integral_retrieve` (hybrid mode, scope=this App, filters: source_type=writing|quote, tags include any of the requested themes, used_count < 3).
3. Pull the brand voice document (filter: tag voice:<brand>).
4. For each carousel: assemble 4–6 slides in the Alreadiam brand voice. Each slide one line, sentence case, no terminal punctuation.
5. Stage each carousel via `integral_stage_entry` with run_id=<the agent_run ID>.
6. Close the agent_run entry: completed_at=now, output_count=<actual>, status=succeeded.

Constraints:
- One source_material entry can appear in at most two carousels in a single run.
- Each carousel must reference 1–3 source_material entries via custom_fields.source_materials.
- Reject any draft that turns prescriptive ("you should…", "try to…") — the brand voice is observational, not instructional.
```

#### 5.2.1 Extending the embedded Integral base SOP (required for Integral tools)

> **Canonical format reference:** [skill-format-standard.md](./skill-format-standard.md) — frontmatter, 7-section body bar, editor `domain_body` contract, and CI compliance rules.

App-bundled skills that call Integral manifest tools (`integral_*`) are **action-backed** in the jvagent sense: they coordinate tools furnished by `EmbeddedIntegralAction`. For consistent propose/stage discipline, identity/scope rules, and error handling, **declare SOP inheritance** from the resident action's base procedure (ADR-0020):

```yaml
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
```

| Key | Role |
|-----|------|
| `extends` | Prepends the base markdown from `agent/.../embedded_integral_action/SKILL.md` at overlay discovery time. Authors write **custom workflow only** in the skill body. |
| `requires-actions` | Hard gate — skill is hidden when `EmbeddedIntegralAction` is not enabled on the resident agent (always true for the cockpit). |
| `allowed-tools` | Tools this SOP may surface to the orchestrator for the skill's turn. Should ⊆ manifest `tools_required`. |
| `tools_required` (manifest) | Authoritative tool list validated at install against [`build_tool_catalogue()`](../../backend/app/agentive/tooling/catalogue.py). |

**Do not** duplicate base procedure text (identity/scope, propose/stage, `[SYSTEM:STAGING-RESOLVED]`, error surfacing) in each App skill — inherit it via `extends`.

**Canonical `SKILL.md` shape** (JV skill frontmatter + custom body):

```markdown
---
name: carousel_drafter
description: Drafts carousels from source material.
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_query_entries
  - integral_create_entry
tags: [drafting, content]
---

## Workflow

1. …domain-specific steps only…
```

At runtime, [`workspace_agent_profile.py`](../../backend/app/agentive/workspace_agent_profile.py) composes `extends` when materializing the workspace overlay (same mechanism as resident `integral_*` skills).

**When `extends` is optional:** skills that do not call Integral tools (pure orchestration prose, external MCP-only flows) may omit it. Any skill listing `integral_*` in `tools_required` or `allowed-tools` **should** extend the embedded action.

**Reference:** [content-factory `carousel_drafter`](../../backend/app/profiles/content-factory/skills/carousel_drafter/SKILL.md); resident base SOP at [`embedded_integral_action/SKILL.md`](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/SKILL.md).

### 5.3 Three mechanisms — pick one

App bundles expose operational behavior through three distinct mechanisms. Do not conflate them.

| Mechanism | Declares in YAML | On-disk asset | Runtime surface | Python? |
|-----------|------------------|---------------|-----------------|---------|
| **Declarative skill** | `app.skills[]` (`kind: declarative` or v3 bare key) | `skills/{key}/SKILL.md` | jvagent workspace overlay (`{app_slug}__{skill_key}`) | No — prompt + MCP tools only |
| **Bundle tool** | `app.tools[]` + `app.hooks[]` | `app/profiles/{slug_underscore}/tools/*.py` | Substrate hook dispatch (`entry.create`, `entry.precompute`, …) via `ToolContext` | Yes — async handler |
| **Custom skill** | `app.skills[]` (`kind: custom` + `handler_ref`) | `handler.py` beside skill | Graph registration only in **v1** — overlay execution deferred to v2 | Yes — not invoked by overlay today |

**v1 guidance:** Use **declarative skills** for agent workflows; use **bundle tools + hooks** for entry-save side effects and precompute. Avoid `kind: custom` unless you need catalog metadata ahead of a v2 executor — prefer `tools[]` for Python today.

**v3 on-disk authoring (preferred):** set `integral_profile_version: 3` and declare skills as bare keys; the loader expands each to `{ key, kind: declarative, prompt_template: skills/{key}/SKILL.md }`:

```yaml
integral_profile_version: 3
scope: app
package:
  slug: my-app
  # ...
app:
  skills:
    - carousel_drafter
    - performance_reviewer
```

Bundle directory name **must** equal `package.slug` (I-BUNDLE-04). Manifest file is **`profile.yaml`** under `backend/app/profiles/{slug}/`.

### 5.3.1 Bundle tools and hooks (trusted Python)

When an App needs Python side effects (pricing precompute, leave-balance recalculation, connector dedup), declare **`app.tools[]`** and wire them with **`app.hooks[]`**. Requires `package.trust_tier: trusted` (or `audited`).

```yaml
package:
  slug: hr_app
  trust_tier: trusted

app:
  tools:
    - key: hr_leave_balance
      handler_ref: tools.leave_balance:recalculate
      parameters_schema: { type: object, properties: { entry_id: { type: string } } }
      side_effects: write

  hooks:
    - point: entry.create
      key: hr_leave_on_create
      match: { entry_type: time_off_request }
      mode: tool
      tool: hr_leave_balance
```

- **Handler shape:** `async def handler(payload: dict, ctx: ToolContext) -> dict`
- **Import rule:** bundle tools MUST use `ToolContext` only — no `app.services` / `app.models` imports.
- **Bundle layout:** manifest, skills, and `tools/` live under one directory named for `package.slug` (e.g. `hr_app/profile.yaml` + `hr_app/tools/`). `install_hook._normalize_handler_ref` resolves `tools.*` imports to `app.profiles.<slug>.tools.*`.
- **Hook points (frozen):** `entry.transform`, `entry.public_share`, `entry.precompute`, `entry.create`, `entry.update`, `connector.dedup`, `connector.auto_link`.

Reference: [`hr_app/profile.yaml`](../../backend/app/profiles/hr_app/profile.yaml), [`sales/profile.yaml`](../../backend/app/profiles/sales/profile.yaml).

### 5.3.2 Custom skill shape (v2 execution — registry only in v1)

When a skill will eventually require a dedicated agent-invoked Python handler (distinct from hook tools), the manifest may reference a handler module:

```yaml
- key: instagram_publisher
  name: Instagram Publisher
  kind: custom
  description: Posts approved content_piece entries to the Instagram Graph API.
  handler_ref: skills/instagram_publisher/handler.py
  handler_entry: publish
  tools_required:
    - integral_get_entry
    - integral_patch_entry
  external_apis:
    - host: graph.instagram.com
      reason: Required for publishing to Instagram on user behalf.
  parameters:
    entry_id: { type: string, required: true }
```

Custom skills are registered on install and validated at compile time. **v1 does not execute `kind: custom` handlers in the jvagent overlay** — use bundle `tools[]` + `hooks[]` for Python side effects until v2 ships a dedicated custom-skill executor. Custom skills remain subject to additional review at catalog publish time (see §11).

### 5.4 Skill ownership, workspace scope, and callability

Two distinct properties govern skills:

- **Ownership** — which App declared the skill. Determined by the manifest. Used for install/uninstall lifecycle and dependency tracking.
- **Callability** — which agents may invoke the skill at runtime. Determined by workspace install scope, user App access, and `private` rules.

**Workspace scope:** Skills and bundle tools are installed into the **Workspace** where `install_app` runs. Hook registrations are per-workspace ([`hooks/registry.py`](../../backend/app/services/hooks/registry.py)). Overlay composition uses `X-Integral-Scope` — skills from Apps in other workspaces never surface.

**User access gate:** The resident agent overlay includes a skill only when the acting user has **any effective role** on the owning App (`resolve_role(user, "app", app_id)`). A user without access to the HR App must not see `hr_app__*` skills even when HR is installed in the workspace. Cross-App orchestration among skills the user **can** access remains intentional (e.g., Content Factory + CRM when the user has both Apps).

**Private skills:** an App may mark specific skills as `private: true` to forbid invocation from agents outside the declaring App (in addition to the user-access gate). Use sparingly — most skills benefit from composable workspace overlay among accessible Apps.

### 5.5 Runtime — resident jvagent overlay

At install time, declarative skills are persisted as `Skill` graph nodes ([`skill_registry`](../../backend/app/agentive/services/skill_registry.py)). At **chat runtime**, the embedded resident agent merges **public** skills from active Apps in the scoped workspace that the **acting user can access** into its Orchestrator skill catalog.

Full reference: [workspace-agent-profile.md](./workspace-agent-profile.md).

Summary:

- **Base tier (global):** six `integral_*` action-overlay SOP skills + full tool manifest — always present.
- **Overlay composition:** App `SKILL.md` bodies with `extends: action:integral/embedded_integral_action` inherit the embedded action base SOP automatically.
- **Workspace overlay:** namespaced skills (`{app_slug}__{skill_key}`) derived from installed Apps; recomposed when `X-Integral-Scope` changes.
- **Discovery:** Orchestrator `find_skill` / `use_skill`; lean surfacing (ADR-0018) applies — overlay skills are not all listed turn-1.
- **Invalidation:** overlay cache clears on App install/uninstall; next chat turn sees updated skills without restart.

---

## 6. Agents layer

An **Agent** is a long-lived AI worker that the App declares should be available once installed. Agents have personas (system prompts), bound skill sets, scope, and optional schedules.

### 6.1 Agent declaration shape

```yaml
agents:
  - key: drafter_agent
    name: Drafter Agent
    persona_ref: agents/drafter.yaml
    skills: [carousel_drafter, sleep_story_drafter, performance_reviewer]
    scope: app                  # ownership scope; the agent is registered under the App
    callable_from:              # which surfaces can invoke this agent
      - mission_control
      - scheduled_tasks
      - mcp                     # via /api/agentive/chat for this agent
    default_schedules:
      - name: "Weekly carousel drafting"
        cron: "0 8 * * 1"
        skill: carousel_drafter
        params: { count: 5 }
      - name: "Performance review"
        cron: "0 9 * * 1"
        skill: performance_reviewer
        params: { window: day_7 }
    staging: required           # 'required' | 'optional' | 'disabled'
    history_limit: 10
```

### 6.2 Persona file

The persona file is a YAML manifest matching the existing jvagent persona shape (see [`agent/agents/integral/integral_agent/agent.yaml`](../../agent/agents/integral/integral_agent/agent.yaml) for the integral default agent persona). For App-installed agents the persona declares:

- `engine` / `persona` model bindings
- System prompt (voice, constraints, filing rules, output discipline)
- `history_limit` and other runtime settings

Persona files live inside the App bundle under a conventional `agents/` directory and are materialized into the registered AgentConfig at install time.

**Minimal persona template** — sufficient for most App-bundled agents:

```yaml
# agents/drafter.yaml
name: Drafter
description: Drafts content for the Content Pipeline track based on source material.

# Model bindings. Engine handles tool use + reasoning; persona handles output voice.
engine:
  provider: openai
  model: gpt-5-mini
  reasoning_effort: medium
persona:
  provider: openai
  model: gpt-4o
  temperature: 0.4
  max_tokens: 2000

# Runtime
history_limit: 10        # turns of conversation history visible to engine
default_staging: required

# System prompt — the voice + constraints + output discipline of this agent
system_prompt: |
  You are the Drafter for an Alreadiam-style content production App.

  ## What you do
  - When invoked, you draft content pieces for the Content Pipeline track based on source material and the App's settings.
  - You stage every write (PendingAgentWrite) for human approval. Never commit directly.
  - Every output references the agent_run entry that produced it.

  ## What you don't do
  - You do not delete entries, ever.
  - You do not modify source_material entries — they are read-only to you.
  - You do not bypass staging.
  - You do not invent facts, sources, or quotes not present in the source material you pulled.

  ## Output discipline
  - Match the brand voice declared in the App settings (`{{settings.brand_voice_source_ids}}`).
  - Stay within the format and platform constraints declared in the draft request.
  - When uncertain whether a draft meets quality bar, mark it `needs_review` rather than `approved`.

  ## Tool sequence
  1. Open an agent_run entry on the agent_runs track. status=running.
  2. Query source material via integral_retrieve (App-scoped, theme + freshness filters).
  3. Read brand voice document(s) from settings.
  4. Generate drafts according to skill parameters.
  5. Stage each draft via integral_stage_entry with run_id=<the agent_run ID>.
  6. Close the agent_run entry. completed_at=now, output_count=<actual>, status=succeeded.
```

The minimum a persona file needs: `name`, `engine`, `persona`, `system_prompt`. Everything else (`description`, `history_limit`, `default_staging`) has sensible defaults. Use the template above as a starting point and adjust the system prompt for the specific App's domain.

**Authoring tips:**

- Keep the system prompt tight — 30–60 lines is the sweet spot. Longer prompts dilute attention.
- Lead with "What you do" and "What you don't do" — the constraints matter more than the capabilities.
- Reference settings via `{{settings.<key>}}` so the persona adapts to per-install configuration.
- Defer skill-specific tool sequences to the skill's `prompt_template` file; the persona handles voice and policy, the skill handles workflow.

### 6.3 Agent scope

`agents[].scope` may be `app` (default) or `workspace`. Both register an `AgentConfig` node — they differ in:

| Scope | AgentConfig field | Discovery | Skill visibility |
|---|---|---|---|
| `app` | `app_id` set | Listed under the App in UI | Sees skills from this App + Workspace shared skills |
| `workspace` | `app_id` null, `workspace_id` set | Listed at the Workspace level | Sees skills from every App in the Workspace |

**Most agents should be `scope: app`.** Promote to `workspace` only when the agent is genuinely cross-App by design (e.g., a "Workspace Concierge" that routes user intent to the right App).

### 6.4 Scheduling

Apps may declare default schedules per agent. On install, integral creates the corresponding scheduled-task records (via the same mechanism backing `mcp__scheduled-tasks__create_scheduled_task` for Claude clients, or via integral's native scheduler when present). Schedules can be enabled, disabled, or modified by the user post-install — they're defaults, not locked behavior.

If integral's native scheduler is unavailable in a given deployment, scheduled tasks degrade to "manual invocation only" — the agent and skills work, the cron simply doesn't fire.

### 6.5 Staging

Agent writes are staged by default. `staging: required` means every write the agent makes lands as a `PendingAgentWrite` for human approval; `staging: optional` lets the agent commit directly for some operations (typically reads + status updates) and stage others (creates + deletes); `staging: disabled` is reserved for system-trusted agents (rare; usually only the default integral agent).

App authors should default to `staging: required` for any agent that creates or modifies user-visible content.

---

## 7. Settings schema

Apps declare a JSON Schema describing what's user-configurable post-install. The install flow renders a form derived from the schema; the resulting settings object is persisted on the App node and made available to all agents and skills in the App at runtime.

### 7.1 Schema shape

```yaml
settings_schema:
  type: object
  properties:
    publish_cadence:
      type: string
      enum: [weekly, twice_weekly, daily]
      default: weekly
      title: Publish cadence
      description: How often the drafter should produce new content.
    target_platforms:
      type: array
      items: { type: string, enum: [instagram, youtube, tiktok, linkedin, x] }
      default: [instagram]
      title: Target platforms
    brand_voice_source_ids:
      type: array
      items: { type: string }
      title: Brand voice sources
      description: Entry IDs in source_material that define the brand voice.
      ui:widget: entry_picker
      ui:filters:
        track_key: source_material
        type_key: source_material
        tags_any: [voice:*]
  required: [publish_cadence, target_platforms]
```

### 7.2 Runtime access

Settings are exposed to skills via templating (`{{settings.publish_cadence}}` in prompts) and to custom handlers as a structured dict (`context.app.settings`). Agents can read settings; only the user (via the App's Settings page) can write them, unless a skill is explicitly granted `can_write_settings: true`.

### 7.3 UI hints

JSON Schema's `ui:widget` and `ui:filters` extensions drive the install-time form renderer. Supported widgets in this spec: `text`, `textarea`, `select`, `multi_select`, `boolean`, `entry_picker`, `tag_picker`, `number`, `date`. Unsupported widget keys render as plain text inputs.

---

## 8. Seeds

Apps may include initial Entries that are planted on install. Seeds populate Tracks with starter content — example source material, README entries, sample records — so the App has visible state immediately after install.

```yaml
seeds:
  - track: source_material
    entries:
      - title: "Brand Voice — Starter"
        body: |
          Replace this with your brand voice document.
        tags: [voice:starter]
        custom_fields:
          source_type: writing
      - title: "Welcome to your Content Factory"
        body: |
          This App produces and tracks content across multiple formats and platforms.
          Start by replacing the Brand Voice — Starter entry above with your actual
          brand voice, then run the Drafter Agent to generate your first content drafts.
        tags: []
        custom_fields:
          source_type: writing
```

Seeds run **after** Tracks, EntryTypes, and Taxonomy are materialized. They are skipped on subsequent re-merges (so re-installing an App doesn't duplicate seed entries).

---

## 9. Lifecycle

```
                    ┌──────────────┐
                    │   Cataloged  │  Library ContentProfile, available in App Catalog
                    └──────┬───────┘
                           │ Install
                           ▼
              ┌───────────────────────┐
              │   Installing (atomic) │  Merge manifest, materialize Tracks/Skills/Agents
              └──────────┬────────────┘
                         │ Settings form
                         ▼
                    ┌──────────┐
                    │ Configured│  Settings saved; agents registered; schedules created
                    └────┬─────┘
                         │
                         ▼
                     ┌────────┐
                     │  Active │  Agents run on schedule; user interacts
                     └────┬───┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
        ┌────────┐  ┌───────────┐  ┌─────────────┐
        │ Update │  │  Paused   │  │ Uninstalled │
        └────────┘  └───────────┘  └─────────────┘
```

### 9.1 Install

`POST /api/workspaces/{id}/apps` with `library_content_profile_id`:

1. Validate manifest against canonical v2 compiler (`compile_canonical_manifest()`)
2. Check `app.requires_apps[]` against the Workspace — for each hard dep (`optional: false`), verify a matching App instance exists in the same Workspace and meets `min_version`. If missing, install pauses and prompts the user to install the dependency first (or to confirm proceeding with soft / opt-in handling)
3. For cross-App relations using `resolution: workspace`, verify exactly one installation of each `target_app` exists in the Workspace; if multiple, prompt the user to pin via `resolution: instance:<app_id>`
4. Create the App node (with attached ContentProfile)
5. Materialize Tracks per `app.tracks[]` (those with `provision_on_create: true`)
6. Apply taxonomy and views (including cross-App `REFERENCES` edge definitions — edges aren't created yet, just the relation schema)
7. Register skills (declarative skills stored in graph; at runtime the resident jvagent merges public skills into the per-workspace overlay via `WorkspaceAgentProfile` + jvagent host skill provider)
8. Register agents (AgentConfig nodes; persona files loaded; default schedules registered)
9. Render the settings form; pause until user submits
10. Persist settings on the App node
11. Plant seeds (skipped on re-install)
12. Emit `app.installed` ChangeEvent

All steps run inside a transaction. Failure at any step rolls back.

### 9.2 Configure

After install, the user can:
- Edit settings via the App's Settings page (re-renders the form from `settings_schema`)
- Enable/disable individual schedules
- Reconfigure agent staging policy
- Promote App skills to other Workspace agents (granting cross-App callability)

### 9.3 Update

`POST /api/apps/{id}/update-from-library` re-merges from a newer library ContentProfile version:

- New Tracks, EntryTypes, Tags, Views, Skills, Agents are **added**
- Existing Tracks/Types are **left alone** (per current merge behavior — see [content_profile_authoring_and_library.md §Updating an existing library package](content-profile-authoring-and-library.md))
- Settings schema changes: new properties get their defaults; removed properties are warned-on; existing values preserved
- Migration entries in the manifest's `migrations:` section can run data transformations for breaking changes

### 9.4 Pause / Resume

Pausing an App disables all its scheduled tasks and prevents new agent runs. Existing Entries and graph state are preserved. Resume re-enables schedules.

### 9.5 Uninstall

`DELETE /api/apps/{id}` is destructive and requires confirmation. Lifecycle:

1. **Dependency check** — for every other App in the Workspace whose manifest declares `requires_apps` referencing this App with `optional: false`, uninstall is **blocked** by default. The error response lists the blocking dependents so the user knows what to uninstall (or downgrade to `optional: true`) first.
2. **Cross-App reference check** — for every cross-App relation pointing to Entries in this App, the `on_target_uninstall` policy on the originating relation determines behavior:
   - `block` (default) — uninstall is blocked while any references remain; error lists the referencing Entries
   - `null` — references are silently nulled; a warning is shown to the user before proceeding
   - `archive_self` — referencing Entries are archived; a warning is shown
3. Removes scheduled tasks
4. Unregisters agents (AgentConfig nodes deleted)
5. Unregisters skills
6. Tracks are **archived by default**, not deleted (preserves Entries)
7. User can opt-in to a full purge that also deletes all Entries (still subject to dependency + reference checks)

Force-uninstall is available via `DELETE /api/apps/{id}?force=true` for development/recovery scenarios; this bypasses checks but emits a prominent `app.force_uninstalled` ChangeEvent with details of what was broken.

---

## 10. Scope rules summary

The runtime authority on what's visible to what.

### 10.1 Skill scope matrix

| Skill declared in | Skill ownership | Default callability |
|---|---|---|
| App A (no `private`) | App A | Any agent in the Workspace can invoke (regardless of which App registered the agent) |
| App A with `private: true` | App A | Only agents scoped to App A can invoke |
| Workspace-level skill (rare; reserved) | Workspace | Any agent in the Workspace |

### 10.2 Agent scope matrix

| Agent declared with | AgentConfig | Visible to user under | Sees skills from |
|---|---|---|---|
| `scope: app` | `app_id=<App>` | App's Agents tab | This App's skills + every other App's non-private skills + Workspace skills |
| `scope: workspace` | `app_id=null, workspace_id=<Workspace>` | Workspace's Agents tab | Same as above |

The difference between `app` and `workspace` agent scope is **organizational**, not capability-based. Both can call across the Workspace. App scope is just "which UI surface lists this agent and which install/uninstall cycle owns its registration."

### 10.3 Entry scope

Unchanged from existing model. Entries live on Tracks; Tracks belong to Apps (or directly to Workspaces); Workspace-level access control and per-resource COLLABORATES_ON edges remain authoritative.

### 10.4 Cross-App relations

Cross-App relations let an Entry in one App reference an Entry in a different App within the **same Workspace**. The canonical use case: an HR App holds employee records; a Payroll App references those employees on payroll runs; a Performance Review App references the same employees on review entries; etc. Without cross-App relations, each App would duplicate employee data or live as one giant monolithic App.

Cross-Workspace references are **out of scope** — relations stop at the Workspace boundary.

#### Declaration

A relation field opts into cross-App by setting `target_app` and `allow_cross_app: true`. Example from a Payroll App referencing an HR App's employees:

```yaml
- key: employees
  name: Employees
  type: relation
  relation:
    target_app: hr_app                       # App template key in target
    target_track_types: [employees]          # Track key inside target App
    target_entry_types: [employee]           # Entry type key
    allow_cross_app: true                    # explicit opt-in
    allow_cross_track: true                  # cross-track inside target App if needed
    many: true
    label_field: custom_fields.full_name     # how the reference renders in UI
    on_target_uninstall: block               # 'block' | 'null' | 'archive_self'
    resolution: workspace                    # 'workspace' (any installation of hr_app)
                                             #   | 'instance:<app_id>' (specific install)
```

Key fields:

| Field | Meaning |
|---|---|
| `target_app` | App template `key` to resolve against (matches `package.name` of the target App). Workspace-scoped lookup. |
| `target_track_types` | Track key(s) inside the target App whose entries are eligible. Same semantics as within-App cross-track relations. |
| `target_entry_types` | Entry type key(s) inside the eligible Tracks. |
| `allow_cross_app` | Must be `true` for the runtime to traverse the App boundary. Default `false` keeps the relation App-internal. |
| `label_field` | Optional. Field path on the referenced Entry whose value renders as the reference label in UI surfaces. Defaults to `title`. |
| `on_target_uninstall` | What happens to this relation if the target App is uninstalled. `block` (default; uninstall fails while references exist), `null` (silently nulls the reference), `archive_self` (archives the referencing entry). |
| `resolution` | `workspace` (default — picks any installation of `target_app` in the same Workspace; errors if multiple installs of the same template) or `instance:<app_id>` (pins to a specific App instance). |

#### Resolution semantics

At relation-set time (when a user or agent assigns a value to the field):

1. Validate `allow_cross_app: true` — without it, cross-App targets are rejected
2. Resolve `target_app` to a specific App instance in the Workspace via `resolution`
3. Verify the App instance is `active` (not paused / uninstalled)
4. Verify the target Entry exists, is on a track matching `target_track_types`, and is of an entry type in `target_entry_types`
5. Permission check the caller has read access to the target Entry per the policy engine

The relation is stored as a `REFERENCES` edge in the graph, just like within-App relations, with an additional `target_app_id` attribute so the runtime knows the App boundary was crossed.

#### Read-time permission

When App A displays an Entry that has a cross-App relation to App B:

- If the viewer has read access to the target Entry in App B → full label + click-through resolves to the target Entry in App B
- If the viewer **lacks** read access (App B has stricter permissions or the viewer was not granted membership to App B) → the relation renders as **a stub**: the reference label is shown but the click-through is disabled, and the relation field marks itself "Restricted (access not granted)"

This preserves the policy engine as the single authority. App A cannot launder access to App B's Entries by virtue of holding a reference.

#### Multiple installations of the same App template

A Workspace may install the same App template multiple times (e.g., two CRM Apps for different business lines). In this case:

- `resolution: workspace` errors at install (ambiguous target)
- `resolution: instance:<app_id>` must be used to pin to a specific install
- Install UI surfaces this disambiguation as a settings prompt: "Which CRM should this relation point to?"

#### Self-referential and cyclic

- An App may reference itself (`target_app` equals own key) — this is just a regular cross-track relation; `allow_cross_app: true` is technically redundant but accepted
- Cyclic references between Apps (A → B → A) are allowed — there's no cycle in the *data* (graph is fine with cycles); install order is managed via `requires_apps` (§10.5)

### 10.5 App dependencies (`requires_apps`)

When an App's manifest declares relations that reference other Apps, it should declare those Apps as dependencies so install ordering and uninstall protection work cleanly.

```yaml
app:
  requires_apps:
    - key: hr_app
      min_version: 1.0.0
      optional: false                # 'false' (hard dep) | 'true' (soft dep)
      reason: "Payroll references employees managed in the HR App."
  tracks: [ ... ]
```

Semantics:

| Setting | Install behavior | Uninstall behavior |
|---|---|---|
| `optional: false` (hard) | Install fails if dependency not present. UI offers to install the dependency first. | Uninstalling the dependency is blocked while the dependent App is installed. |
| `optional: true` (soft) | Install proceeds; cross-App relations to the missing target dangle as "Restricted (App not installed)". | No uninstall protection. |

For the HR / Payroll example, Payroll declares `requires_apps: [{key: hr_app, optional: false}]`. The user cannot install Payroll without HR present; cannot uninstall HR while Payroll is installed; both Apps cooperate cleanly.

For looser couplings (e.g., a notes App that *optionally* links to a CRM contact if available), `optional: true` is correct — the App works standalone but lights up extra capability when the dependency is present.

This mechanism also covers skill dependencies in practice: if a skill in App A invokes a skill in App B, declaring `requires_apps: [b]` ensures B is present when A runs.

---

## 11. Marketplace considerations

These apply to App templates contributed to the public App Catalog. They do not bind first-party Apps or consulting deliverables installed in single-tenant deployments — those carry trust intrinsically.

### 11.1 Security boundary

Custom skills (`kind: custom`) run Python on the integral backend. Community-contributed custom skills are a real attack surface. Current spec policy:

- **Community catalog Apps may not include `kind: custom` skills.** Only declarative skills are eligible for public listing.
- Declarative skills compose existing MCP tools and natural-language reasoning. They cannot execute arbitrary code.
- First-party Apps (shipped under `backend/app/profiles/<slug>/`) and trusted-partner Apps may include custom skills, marked with a `trust_tier: trusted` flag and reviewed by integral maintainers.
- Custom-skill Apps installed from outside the catalog (e.g., a consulting deliverable) require the user to acknowledge a capability prompt at install: "This App includes custom code that will run on your integral backend. Source: <publisher>. Approve?"

### 11.2 Capability declarations

Every App declares the MCP tools its skills require (`skills[].tools_required`). At install time the user sees:

- Which Tracks the App will create
- Which existing tools the App will invoke
- Which external APIs custom skills (if any) will call (`skills[].external_apis`)
- What settings will be requested

This is the equivalent of browser-extension permission prompts. The user can approve, reject, or selectively disable optional skills before completing install.

### 11.3 Review pipeline

Catalog admission requires:

1. Manifest validates against the v2 schema with no warnings
2. All referenced files (prompt templates, persona YAML, seed entries) resolve
3. For declarative skills: prompt audit (no instructions that violate integral's safety/privacy boundaries)
4. For custom skills: code review by an integral maintainer
5. Test install on a clean Workspace; verify no errors, all schedules register, all skills callable

### 11.4 Versioning

Manifest `package.version` follows semver. Catalog stores all published versions. Users see the latest stable by default; can pin to a specific version or opt into pre-release channels.

### 11.5 Attribution and licensing

Manifest `package.publisher` and `package.license` are surfaced in the catalog. License defaults to MIT for community-contributed Apps unless otherwise specified. Consulting-deliverable Apps may use proprietary licenses; the manifest carries the license text or reference.

---

## 12. Authoring patterns

Hands-on scaffold, `SKILL.md` template, and trusted-tool wiring: **[app-bundle-authoring.md](./app-bundle-authoring.md)**.

Practical templates for the three primary App authoring contexts.

### 12.1 Pattern: "Declarative-only domain App"

For most domain Apps (CRM, project tracker, content factory, knowledge base, customer support inbox). All skills are declarative. No Python.

**File layout:**
```
content-factory/
├── profile.yaml              # integral_profile_version: 3 preferred
├── skills/
│   ├── carousel_drafter/
│   │   └── SKILL.md          # extends + allowed-tools + custom workflow
│   ├── performance_reviewer/
│   │   └── SKILL.md
│   └── ...
├── agents/
│   └── drafter.yaml
└── seeds/
    └── brand_voice_starter.md
```

**Authoring sequence:**
1. Write `profile.yaml` with schema (tracks, entry types, views, taxonomy, relations)
2. Add `app.skills[]` declaring each capability (v3 bare keys or full dicts)
3. Author each skill's `SKILL.md` describing the tool sequence in natural language
4. Author the agent persona(s) under `agents/`
5. Declare `app.agents[]` binding personas to skills with scope and schedule
6. Define `app.settings_schema` for configurables
7. Author seed entries
8. Test merge into a clean Workspace; verify install completes; run one cycle of each agent

### 12.2 Pattern: "Consulting deliverable App"

For client-specific Apps tailored during AI transformation engagements. May include custom skills (with the client's approval and the `trust_tier: trusted` flag managed via direct deployment, not the public catalog).

Same file layout as 12.1, plus optional `skills/<name>/handler.py` for custom skills.

**Additional considerations:**
- Manifest `package.publisher` carries the consulting firm's name
- Manifest `package.license` may be proprietary (client-owned IP)
- Settings schema captures every client-specific configuration so the App is reusable across similar clients with different parameters
- Maintain a private version registry (your firm's internal catalog) of base templates per industry or function, then per-client manifests that extend them via the `migrations` mechanism

### 12.3 Pattern: "Track-level customization (not a full App)"

For one-off Tracks that need custom entry types but don't justify a full App. Existing `scope: track` flow per [content_profile_authoring_and_library.md](content-profile-authoring-and-library.md). v2 adds optional `track.skills:` for Track-scoped operational behavior (rare — most operational behavior should be App-scoped).

---

## 13. Migration from v1 / Space

Integral is pre-production; v1 manifests and the Space primitive are being removed entirely rather than maintained alongside v2 / App. There is no compatibility shim, no Pydantic alias, no endpoint redirect, no MCP tool aliasing. The cutover is hard.

### 13.1 Manifest migration

All existing v1 manifests in the codebase (seeded packages, tests, fixtures) are rewritten in place to v2 syntax:

- `content_profile_schema_version: 1` → `2`
- Root key `space:` → `app:` where `scope: space`
- `scope: space` → `scope: app`
- Optional new sections (`skills`, `agents`, `settings_schema`, `seeds`, `permissions`) added where the package warrants them; absent otherwise

The compiler accepts v2 only. Any v1 manifest encountered post-cutover is treated as invalid.

### 13.2 WorkspaceApp → App rename (graph + code)

Executed as a single coordinated change (2026-05). Scope:

- `WorkspaceApp` Node class renamed to `App` (drop the workspace prefix) outright — no alias retained
- `/api/spaces` endpoints renamed to `/api/apps` — no redirects retained
- Auto-synthesized MCP tools renamed `integral_<verb>_space` → `integral_<verb>_app` — no legacy tool names retained
- Frontend components, routes, copy migrated in the same change
- Database migration renames the `WorkspaceApp` entity discriminator to `App` in jvspatial's storage

### 13.3 ContentProfile vs App terminology

ContentProfile remains the technical term for the manifest artifact. App is the user-facing term for the installed result. Code, internal docs, and the API may use both; user-facing UI uses only App. The two are not synonyms — a ContentProfile becomes an App only when `scope: app`; track-scoped ContentProfiles are not Apps.

---

## 14. Open questions

Items deferred from this spec, to revisit in a future revision:

1. **App-level permissions.** Workspace members all see all Apps. Per-App access control (App roles separate from Workspace roles) is desirable for multi-collaborator workspaces. Tracked under integral's existing permissions roadmap. (Cross-App relation permission propagation is already handled — see §10.4 read-time permission rules.)
2. **App settings UI.** This spec renders a JSON-Schema-driven form. Richer widgets (entry pickers with live preview, tag pickers, file uploads for brand assets) need a registered widget contribution mechanism similar to view widgets.
3. **Marketplace governance.** Review pipeline shape, dispute resolution, monetization (free vs paid Apps), revenue share — all unresolved. Out of scope for the technical architecture; needed before public catalog opens.
4. **Custom skill sandbox.** First-party custom skills run in the backend Python process. A genuine sandbox (subprocess, container, capability-restricted runtime) is desirable before any non-trusted custom skills are allowed. Major work; out of scope for this spec.
5. **Settings hot-reload.** When settings change, do running agents pick up the new values immediately or on next invocation? This spec specifies next invocation; hot-reload may be added later for specific settings.
6. **Schedule density limits.** A workspace with many Apps installed could accumulate dozens of scheduled tasks. This spec has no per-workspace schedule budget. May need rate limiting.
7. **Cross-Workspace federation.** Relations stop at the Workspace boundary by design. Federated references across Workspaces (e.g., a vendor App in one Workspace referencing customer Apps in others, with explicit grants) is a much larger topic — out of scope for App Bundles v1.

**Resolved in this revision (no longer open):**
- *Cross-App relations* — addressed in §10.4
- *App dependencies* (covers skill dependency cases too) — addressed in §10.5

---

## 15. Glossary

| Term | Definition |
|---|---|
| **Workspace** | Top-level operational environment. Owns Apps, Tracks, Entries, Agents. |
| **App** | Coherent operational domain inside a Workspace. Bundles Tracks, Skills, Agents, Settings. Installed from an App template; configured post-install. (Formerly: Space.) |
| **App template** | A ContentProfile in the catalog with `library_package: true` and `scope: app`. The blueprint. |
| **App instance** | An installed App in a Workspace. The materialized result. |
| **Track** | Table-like collection of entries of one or more declared types. Lives under an App or directly under a Workspace. |
| **Entry** | Single record in a Track. Shaped by the EntryType declared in the attached ContentProfile. |
| **ContentProfile** | Technical term for the manifest artifact. Carries schema, skills, agents, settings, seeds. Applies to either a Track or an App. |
| **Manifest** | The YAML/JSON document inside a ContentProfile. v2 introduces the operational layer sections. |
| **Skill** | Named operational capability invokable by an agent. Either declarative (prompt + tool sequence) or custom (Python handler). |
| **Agent** | Long-lived AI worker registered on App install. Has a persona, bound skills, scope, and optional schedules. |
| **Settings** | User-configurable values defined by the App's `settings_schema`. Available to skills at runtime. |
| **Seed** | Initial Entry planted on App install. Skipped on re-install. |
| **Library package** | A ContentProfile node in the catalog. Synonym: App template (when `scope: app`). |
| **App suite** | User-facing label for a ContentProfile library package with `scope: app`. Synonym of "App template" in catalog-facing copy. |
| **Track pack** | User-facing label for a ContentProfile library package with `scope: track`. |
| **Merge** | The action of applying a library ContentProfile to a target (Workspace or Track). Synonym: Install (when target is a Workspace and scope is app). |
| **Staging** | Mechanism by which agent writes surface as `PendingAgentWrite` for human approval before committing. |
| **MCP tool** | Auto-synthesized tool from a FastAPI endpoint, named `integral_<verb>_<resource>`, callable by agents and external MCP clients. |
| **Cross-App relation** | A relation field on an Entry in one App that points to an Entry in a different App within the same Workspace. Declared with `target_app` + `allow_cross_app: true`. Permission-checked at read time per §10.4. |
| **`requires_apps`** | Manifest section declaring App dependencies. Hard deps (`optional: false`) block install without the dependency and block uninstall of the dependency while present. Soft deps (`optional: true`) proceed gracefully. See §10.5. |
| **`on_target_uninstall`** | Per-relation policy for what happens when the target App of a cross-App relation is uninstalled. `block` (default), `null`, or `archive_self`. See §10.4. |
| **`resolution`** | Per-relation strategy for picking which installation of the target App template to point at when multiple instances exist in the Workspace. `workspace` (default, single-install assumption) or `instance:<app_id>` (pinned). See §10.4. |

---

## 16. References

- [app-bundle-authoring.md](./app-bundle-authoring.md) — scaffold, `SKILL.md` template, trust tier, hooks/tools hands-on guide
- [content_profile_authoring_and_library.md](content-profile-authoring-and-library.md) — manifest v2 authoring guide (Track + App scopes, workflow, library/catalog operations)
- [content_profile_packages.md](content-profile-packages.md) — package metadata and migrations
- [content_profile_search_index.md](content-profile-search-index.md) — `_cp_index` extraction
- [../product/ARCHITECTURE.md](../product/ARCHITECTURE.md) — overall integral architecture
- `../PROJECT.md` (in `.planning/PROJECT.md`, which is gitignored — not available in a fresh clone) — project vision
- [agent/agents/integral/integral_agent/agent.yaml](../../agent/agents/integral/integral_agent/agent.yaml) — default agent persona shape (reference for App-bundled personas)
