# Workspace Agent Profile — per-tenant resident jvagent skills

**Status:** Shipped (resident cockpit, v1).

**Purpose:** Document how Integral's embedded resident agent (`agent/agents/integral/integral_agent/`) dynamically assumes **workspace-specific** App-bundled skills while preserving a **global base** skill and tool surface for every tenant.

---

## Two-tier model

| Tier | Scope | Contents | When active |
|------|-------|----------|-------------|
| **Base profile** | Global — every user, every workspace | Thirteen `integral_*` action-overlay SOP skills under `agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/` (`integral_identity`, `integral_filing`, `integral_entries`, `integral_attachments`, `integral_workspace`, `integral_models`, `integral_insights`, `integral_scaffold`, `integral_model`, `integral_organize`, `integral_review`, `integral_onboard`, `integral_scheduling`) plus the full Integral tool manifest ([`tool_manifest.yaml`](../../backend/app/agentive/tool_manifest.yaml)) via `EmbeddedIntegralAction` | Always — never filtered by workspace |
| **Workspace overlay** | Per active workspace (`X-Integral-Scope`) + acting user App access | Public declarative skills from installed Apps the user can access (`lifecycle_state=active`); app metadata and settings for future grounding | Merged when the user chats in that workspace |

The overlay does **not** replace base capabilities. Filesystem/base skills win on name collision with overlay skills (jvagent host-provider merge rule).

**User access gate:** Overlay skills are filtered to Apps where `resolve_role(user, "app", app_id)` is non-null. Skills do not carry per-skill RBAC beyond `private` — access flows App → workspace install → user's App role. See [app-bundles-v1.md §5.4](./app-bundles-v1.md#54-skill-ownership-workspace-scope-and-callability).

**Private skills** (`private: true` in manifest) are registered on install but excluded from the resident overlay unless the caller's agent belongs to the same App. Cross-App private denial stacks on top of the user-access gate.

---

## Skill placement invariants (non-negotiable)

Skills are the **coordination layer** over Integral's tools. Where a skill lives is
not a stylistic choice — it determines what the skill is allowed to know and do.

| Layer | Location | Naming | MUST NOT |
|-------|----------|--------|----------|
| **Base skills** | [`agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/`](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/) **only** | `integral_*` prefix | Encode domain logic (CRM/HR/sales workflows); reference bundle-specific tools |
| **App skills** | [`backend/app/packages/<slug>/skills/<key>/SKILL.md`](../../backend/app/packages/) + `app.skills[]` in the bundle manifest | `{app_slug}__{skill_key}` at runtime | Live in agent core; import substrate (`app.services`/`app.models`) directly |
| **Workspace skills** | Editor-authored `Skill` nodes (`origin=workspace`, `body_override`) wired `Workspace —CONTAINS→ Skill` | `workspace__{skill_key}` at runtime | Editable via Settings → Skills (admins/owners) |
| **Bundle tools** | `backend/app/packages/<slug>/tools/*.py` + `app.tools[]` | Bundle-scoped keys | Import `app.services`/`app.models` — reach substrate only via the `ToolContext` facade |
| **Bundle hooks** | `app.hooks[]` (declarative or `mode: tool`) | One of the frozen hook points (I-HOOK-01) | Introduce ad-hoc hook points |

**Why placement is enforced:**

- **Base skills are always discoverable** to the Orchestrator every turn, for every
  tenant. A domain skill in the base tier pollutes every workspace's prompt with
  irrelevant SOP and leaks one customer's domain into another's agent. Domain
  behavior belongs in an **App bundle**, surfaced only where that App is installed.
- **App skills extend, never replace, the base SOP** via
  `extends: action:integral/embedded_integral_action` — write **domain workflow
  only** in the bundle body; identity/scope, propose/stage, and error handling come
  from the base. Filesystem base skills win on name collision (host-provider merge
  rule above).

**Architectural invariants every skill + tool preserves:**

- **Skills are SOP-only.** A `SkillDoc` is prose + `requires_tools` — no executable
  code. Pythonic logic lives in `trust_tier: trusted` bundle **tools** (via
  `ToolContext`) or in substrate, never in a skill body.
- **PC privacy contract.** Identity and scope are dispatch concerns, never tool
  arguments; every substrate mutation a skill coordinates **stages for user bless**.
- **I-GRAPH-01.** Tools a skill calls to wire relationships materialize the edge
  (`REFERENCES` / `ANCHORS`) with `field_key` — a scalar foreign key is never a
  substitute for the edge.
- **Contributed bundles** (third-party skills/tools) require the trust/safeguard
  layer (designed, not yet built — see [BYOA.md](../product/BYOA.md)) **before** any
  marketplace install. First-party bundles only, for now.

> **Placement check (review gate):** zero domain skills in agent core; zero
> `integral_*` skills inside `backend/app/packages/`. A skill that names a
> bundle-specific tool belongs in that bundle, not the base.

---

## Runtime flow

```
Chat turn (X-Integral-Scope: workspace_id, authenticated user_id)
  → jvagent_provider sets current_scope_workspace_id
  → materialize_profile_for_turn(workspace_id, user_id)
  → embed.interact_stream(...)
      → Orchestrator discover_skill_docs()
          → filesystem integral_* skills (base)
          → host provider reads turn-scoped overlay SkillDocs
  → clear_turn_workspace_profile() in finally
```

**Workspace switch:** changing the scope header on the next turn recomposes the overlay. No server restart required.

**Empty workspace:** overlay is empty; base skills and tools unchanged.

---

## Key modules

| Module | Role |
|--------|------|
| [`workspace_agent_profile.py`](../../backend/app/agentive/workspace_agent_profile.py) | `WorkspaceAgentProfile` dataclass; `compose_workspace_agent_profile()`; per-(workspace,user) cache; turn ContextVar |
| [`skill_registry.py`](../../backend/app/agentive/services/skill_registry.py) | Persists `Skill` nodes on App install; `get_callable_skills()` enforces private + user App-access gates |
| [`skill_bundle_provider.py`](../../backend/app/agentive/skill_bundle_provider.py) | Registers jvagent host skill provider at embed bootstrap |
| [`jvagent/action/orchestrator/skill_providers.py`](../../../jv/jvagent/jvagent/action/orchestrator/skill_providers.py) | jvagent extension: `register_host_skill_provider()` |
| [`jvagent_provider.py`](../../backend/app/services/chat_providers/jvagent_provider.py) | Materializes profile per chat turn |
| [`app_lifecycle.py`](../../backend/app/services/app_lifecycle.py) | Skill registration on install; cache invalidation on install/uninstall |
| [`operational_model_workspace_init.py`](../../backend/app/services/operational_model_workspace_init.py) | Skill registration during workspace strict-init |

---

## Composition algorithm

1. Find active Apps: `App.find({"workspace_id": ..., "lifecycle_state": "active"})`.
2. Collect callable skills: `get_callable_skills(caller_agent_id, workspace_id, user_id)` — filters to Apps in `accessible_apps_for_scope(user_id, workspace_id)` plus `private` gate.
3. For each skill, resolve SOP body:
   - **`body_override`** on the workspace `Skill` node (editor customization) —
     stores **domain body only**; `extends` merge still applies at compose time.
     See [skill-format-standard.md](./skill-format-standard.md).
   - **Path ref** (`skills/<key>/SKILL.md`) — read from bundle disk via `App.source_operational_model_slug`, `installed_from_library_id` metadata, or `App.metadata.bundle_dir_path`. Inline manifest text is not supported.
4. Namespace overlay skill names: `{app_slug}__{skill_key}` for bundle skills; `workspace__{skill_key}` for workspace-authored skills.
5. Skip skills with `enabled=false`. Include `origin=workspace` skills from `Skill.find({workspace_id, origin: "workspace"})`.
6. Map `Skill.tools_required` (from manifest at install) to `SkillDoc.requires_tools`. Must match `allowed-tools` in the paired `SKILL.md`; no frontmatter fallback at compose time.

---

## Cache and invalidation

In-process cache keyed by `(workspace_id, user_id)` + content hash (`profile_version` from app/skill `updated_at` values).

Invalidated when:

- App install completes ([`app_lifecycle.install_app`](../../backend/app/services/app_lifecycle.py))
- App uninstall removes skills
- Workspace strict-init registers skills ([`init_workspace_from_profile`](../../backend/app/services/operational_model_workspace_init.py))

Settings updates and library re-merge should call `invalidate_workspace_profile(workspace_id)` when those paths gain skill changes (v1.1 hardening).

---

## Authoring App-bundled skills for the overlay

1. Declare skills in `app.skills[]` in the bundle manifest ([app-bundles-v1.md §5](./app-bundles-v1.md#5-skills-layer)).
2. Author `skills/<key>/SKILL.md` under the bundle directory (JV skill frontmatter + markdown body).
3. **Extend the embedded Integral base SOP** when the skill uses Integral tools — add to frontmatter:
   ```yaml
   extends: action:integral/embedded_integral_action
   requires-actions:
     - EmbeddedIntegralAction
   ```
   The base procedure (identity/scope, propose/stage, staging-resolved signals, error handling) lives at [`embedded_integral_action/SKILL.md`](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/SKILL.md). Write **domain workflow only** in the skill body. See [app-bundles-v1.md §5.2.1](./app-bundles-v1.md#521-extending-the-embedded-integral-base-sop-required-for-integral-tools).
4. Set `allowed-tools` in `SKILL.md` to the subset of manifest `tools_required` the SOP should surface per turn.
5. Set `private: false` (default) for skills the resident should discover workspace-wide.
6. List `tools_required` in the manifest using live catalogue names (`integral_query_entries`). Validated at registration against [`build_tool_catalogue()`](../../backend/app/agentive/tooling/catalogue.py).
7. Point `prompt_template: skills/<key>/SKILL.md` in the manifest (or inline prose for rare cases).
8. Install the App into a workspace (`POST /api/workspaces/{id}/apps/install` or batch install).

At materialization, `compose_workspace_agent_profile()` resolves the on-disk `SKILL.md`, applies `extends` via jvagent `sop_extend`, and namespaces the skill as `{app_slug}__{skill_key}`.

After install, the resident agent exposes overlay skills via Orchestrator `find_skill` / `use_skill` on the next chat turn in that workspace.

**Reference bundle:** [seeded-apps/content-factory.md](./seeded-apps/content-factory.md) — `carousel_drafter` and `performance_reviewer` (both extend the embedded action).

---

## Skill quality standard (SOP depth bar)

> **Canonical format reference:** [skill-format-standard.md](./skill-format-standard.md).

A skill is only as good as its SOP. The bar is [`integral_filing/SKILL.md`](../../agent/agents/integral/integral_agent/actions/integral/embedded_integral_action/skills/integral_filing/SKILL.md)
(~220 lines) — a one-line "draft N carousels" skill is not a skill, it is a tool
alias. Every base **and** app skill body MUST contain these sections:

1. **Purpose / when to use** — the user intents that should route here, in their words.
2. **When NOT to use → delegate** — the adjacent skills that own neighboring intents
   (e.g. filing delegates entity-vs-track decisions to `integral_model`). Prevents
   overlap and keeps each skill a distinct coordination pattern.
3. **Grounding (read before write)** — which read tools to call first to orient
   (`integral_whoami`, `integral_list_apps`, `integral_describe_model`, `integral_get_track_schema`).
   A skill never writes blind.
4. **Procedure** — the numbered tool sequence, naming each tool and the order. For a
   multi-step workflow, open a **batch** so the whole sequence stages as one approval
   (see batch staging). State the decision points and the clarifying questions to ask.
5. **Staging discipline** — every mutation proposes and waits for bless; how to
   present the diff; what "staging-resolved" means before the next step.
6. **Forbidden patterns** — the wrong-but-tempting shortcuts (scalar FK instead of a
   relation edge; widening scope; bypassing a delegate).
7. **Examples** — at least one concrete end-to-end walkthrough.

> **Review gate:** a new skill that lacks *when-NOT-to-use*, *grounding*, or
> *staging discipline* is not mergeable. Match the filing SOP's structure.

### Bundle authoring contract (`tools_required`)

- A bundle skill's `tools_required` (manifest) / `allowed-tools` (frontmatter) spans
  **both** base `integral_*` tools **and** the bundle's own `app.tools[]` keys — the
  whole point of an app skill is to coordinate base primitives *with* domain tools.
- Validated at [`skill_registry.register_skill()`](../../backend/app/agentive/services/skill_registry.py)
  against `build_tool_catalogue()` **and** the bundle's registered tool keys. A
  skill that names an unknown tool fails registration.
- Prefer **declarative** skills (SOP + `integral_*`/bundle tools). Use
  `kind: custom` (a Python handler) only when a computation genuinely cannot be
  expressed as tool coordination — and then the handler is a `trust_tier: trusted`
  bundle tool reached via `ToolContext`, never a substrate import.

---

## jvagent host skill provider (embed hosts)

Embedded hosts register a sync provider at startup:

```python
from app.agentive.skill_bundle_provider import install_skill_provider_into_jvagent
install_skill_provider_into_jvagent()  # main.py after embed.bootstrap
```

The provider reads `get_turn_workspace_profile().overlay_skill_docs` and converts them to jvagent `SkillDoc` entries. See jvagent [`docs/ORCHESTRATOR.md`](../../../jv/jvagent/docs/ORCHESTRATOR.md) § Host skill providers.

Resident agent config stays `skills_source: app` in [`agent.yaml`](../../agent/agents/integral/integral_agent/agent.yaml) — base tier only; overlay merges automatically.

---

## Out of scope (v1)

| Item | Notes |
|------|-------|
| App-bundled persona agents as jvagent graph agents | `sales_resident`, etc. remain `AgentConfig` substrate records |
| Private skills on resident | By design per callability model |
| `kind: custom` handler execution | Metadata persisted; runtime execution deferred |
| `{{settings.*}}` prompt templating | App settings captured on profile; render pass deferred |
| `GET /api/workspaces/{id}/agent-profile` | Debug/introspection API planned v1.1 |

---

## Tests

[`backend/tests/test_workspace_agent_profile.py`](../../backend/tests/test_workspace_agent_profile.py) — empty overlay, post-install public skills, tenant isolation, cache invalidation, host provider integration.

Related: [`test_app_bundled_skills.py`](../../backend/tests/test_app_bundled_skills.py) (registry), [`test_content_factory_install.py`](../../backend/tests/test_content_factory_install.py) (install lifecycle).

---

## See also

- [app-bundles-v1.md](./app-bundles-v1.md) — manifest skills + install lifecycle
- [agent/README.md](../../agent/README.md) — base vs overlay skill authoring contexts
- [ARCHITECTURE.md §10.6](../product/ARCHITECTURE.md#106-agentive-layer-architecture-106) — agentive layer overview
- [BYOA.md](../product/BYOA.md) — external agents use MCP only; resident overlay is in-app
