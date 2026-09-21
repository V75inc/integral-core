# Content Factory — canonical App bundle reference

**Status:** Shipped first-party library package (`content-factory`).

**Purpose:** Multi-stream content production substrate and the reference App-scoped
OperationalModel bundle for downstream phases (orchestration, proactive agents,
marketplace, chat, tiers).

Content Factory exercises the v2 App manifest surface in one self-consistent
template:

- `integral_operational_model_version: 2`, `scope: app`
- Four cross-related tracks
- Three cross-track relation declarations
- Two declarative skills with bundle-resolved `SKILL.md` prompts
- One App-scoped agent with a bundle persona file
- Three-property `settings_schema` (select + multi_select + entry_picker)
- `permissions` (member install role)
- Two idempotent seeds

## File layout

```
backend/app/packages/content-factory/
├── operational-model.yaml
├── skills/
│   ├── carousel_drafter/SKILL.md
│   └── performance_reviewer/SKILL.md
└── agents/
    └── drafter.yaml
```

The library scanner (`operational_model_loader.load_library_operational_models`) discovers
every `backend/app/packages/<slug>/operational-model.yaml` at boot. Bare skill keys in
`app.skills` expand to `prompt_template: skills/<key>/SKILL.md`. Agent
`persona_ref` paths are relative to the bundle root.

At chat runtime, [`WorkspaceAgentProfile`](../workspace-agent-profile.md) composes
callable skills for the active workspace (and caller's App access) into the
resident jvagent overlay — namespaced as `content_factory__carousel_drafter`,
etc.

See also: [app-bundles-v1.md](../app-bundles-v1.md), [operational-model-authoring-and-library.md](../operational-model-authoring-and-library.md).

## Tracks

| Track | Purpose | Entry types |
|-------|---------|-------------|
| `source_material` | Raw inputs (writing, brand voice samples, research, transcripts) | `source_material`, `brand_voice` |
| `content_pipeline` | Drafts in flight, status draft → published | `content_piece` |
| `agent_runs` | One entry per agent invocation (audit thread) | `agent_run` |
| `performance` | Post-publish metrics keyed back to content_piece | `performance_record` |

## Cross-track relations

Declared on entry-type fields and mirrored under `app.relations` for compile-time
graph validation:

- `content_piece.source_materials` → `source_material.source_material[]` (many)
- `content_piece.agent_run` → `agent_runs.agent_run` (single)
- `performance_record.content_piece` → `content_pipeline.content_piece` (single)

All within-App relations. Cross-App legs use `target_app` on the relation field.

## Skills

| Key | Kind | Description |
|-----|------|-------------|
| `carousel_drafter` | declarative | Drafts carousels from queued source_material |
| `performance_reviewer` | declarative | Summarizes recent performance entries per platform |

Both are public (`private: false`). Each `SKILL.md` declares
`extends: action:integral/embedded_integral_action` (inherits resident
propose/stage discipline) and lists `allowed-tools` aligned with manifest
`tools_required`.

## Agent

| Key | Persona | Bound skills | Scope | Schedule |
|-----|---------|--------------|-------|----------|
| `drafter` | `agents/drafter.yaml` | `[carousel_drafter]` | `app` | Weekly (Mon 09:00 UTC) |

`staging: required` — drafts land in pending-review before publishing to user
surfaces. Schedule degrades to `manual` when the native scheduler is unavailable.

## Settings schema

Three properties (two required):

| Property | Type | Widget | Required | Default |
|----------|------|--------|----------|---------|
| `publish_cadence` | string enum | select | yes | `weekly` |
| `target_platforms` | string-enum array | multi_select | yes | `[instagram]` |
| `brand_voice_source_ids` | string array | entry_picker | no | `[]` |

`brand_voice_source_ids` carries `ui:filters` restricting the picker to the
`source_material` track / `brand_voice` entry type / `voice:*` tagged entries.

## Seeds

Two idempotent entries on `source_material` at install:

1. **Brand Voice — Starter Template** (`brand_voice_starter`) — placeholder brand-voice sample.
2. **Welcome to your Content Factory** (`welcome_readme`) — onboarding README.

Reinstall after archive does not duplicate seeds (`seed:{app_id}:source_material:<id>`).

## Authoring a new App bundle

Mirror this layout when adding a first-party or consulting-deliverable App:

1. Scaffold: `python3 backend/scripts/scaffold_bundle.py <slug> [--trusted]`
2. Edit `backend/app/packages/<slug>/operational-model.yaml` — swap `package`, tracks, skills, agents, settings.
3. Add `skills/<key>/SKILL.md` for each declarative skill key.
4. Add `agents/<key>.yaml` (or `.md`) for each agent `persona_ref`.
5. Run `pytest backend/tests/test_app_bundles_invariants.py` — compile + asset gates.
6. Library rows refresh on boot via `operational_model_library_sync`.

Do **not** add Python manifest builders under `app/services/` — YAML bundles are the
single source of truth.

## Acceptance tests

End-to-end install: `backend/tests/domain_apps/test_content_factory_install.py`.

Covers:

- Install via `app_lifecycle.install_app` → four tracks + two skills + one agent + settings + two seeds in one transaction.
- Writing a `content_piece` referencing `source_material` creates the `REFERENCES` edge.
- Archive preserves tracks and flips `lifecycle_state` to `uninstalled`.
- Force-delete hard-cascades.
- `app.installed` ChangeEvent emitted exactly once per lifecycle action.

Invariant gates: `backend/tests/test_app_bundles_invariants.py` (I-APP-01..05, I-BUNDLE-01..05).

## Invariants

This bundle is the structural reference for App Bundles v1 invariants
(`docs/INVARIANTS.md`):

- I-APP-01 — Manifest v2 only.
- I-APP-02 — App lifecycle atomicity.
- I-APP-03 — Cross-App permission propagation.
- I-APP-04 — `requires_apps[]` enforcement.
- I-APP-05 — Same-workspace App scope.
- I-BUNDLE-01..05 — On-disk bundle compile, `SKILL.md`, slug alignment, trust tier.
