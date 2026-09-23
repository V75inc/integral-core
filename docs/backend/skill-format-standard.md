# Skill Format Standard — jvagent JV + Anthropic + Integral

**Status:** Canonical cross-repo contract for Integral `SKILL.md` authoring, compliance CI, and the Settings skills editor.

**Upstream standards (normative):**

- jvagent [`jvagent/skills/README.md`](../../../jv/jvagent/jvagent/skills/README.md) — JV skill (`spec: jv`) and Claude skill (`spec: claude`)
- [Anthropic Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) — folder layout, frontmatter discovery
- jvagent ADR-0020 (`extends`), ADR-0023 (placement)

**Related Integral docs:**

- [app-bundle-authoring.md](./app-bundle-authoring.md) — scaffold, trust tier, hooks
- [workspace-agent-profile.md](./workspace-agent-profile.md) — runtime overlay
- [INVARIANTS.md § I-SKILL](../INVARIANTS.md) — enforceable rules

---

## Two layers: discovery vs SOP

| Layer | Where | Audience | Content |
|-------|--------|----------|---------|
| **Discovery** | YAML `description` | Orchestrator before activation (`find_skill`, prompt index) | Third-person: **what** the skill does + **when** to route here (1–3 sentences). Editor field: *"When should the agent use this?"* |
| **SOP body** | Markdown after `---` | Model after `use_skill` | Domain procedure. Integral public skills add the **7-section bar** (below). `## When to use` expands routing with intents/examples — not a copy-paste of `description`. |

This matches Anthropic/jvagent: `description` is injected for skill selection; the body is level-2 disclosure on activation.

---

## File shape (`SKILL.md`)

```yaml
---
name: <key>                    # MUST match skills/<key>/ directory
description: <discovery prose> # third person; what + when
spec: jv                       # explicit; jv (default) | claude
extends: action:integral/embedded_integral_action   # bundle + core integral_*
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_query_entries
tags: [optional]
---

## When to use
…
```

### Field order (convention)

`name` → `description` → `spec` → `extends` → `requires-actions` → `allowed-tools` → (`always-active`) → `tags`

### Name rule exception

Anthropic/jvagent default: lowercase + hyphens. Integral **`integral_*` tools and core skills** use **snake_case** with the `integral_` prefix — namespaced to the MCP catalogue. Bundle keys remain `snake_case` without hyphens.

### Forbidden frontmatter

- `plan-steps` / `plan_steps` — removed; body is the plan
- top-level `version` — use `metadata.version` if semver tracking is adopted

---

## JV skill contract (`spec: jv`)

| Key | Required | Rule |
|-----|----------|------|
| `name` | yes | Matches directory name |
| `description` | yes | Non-empty; third-person discovery |
| `spec` | yes (Integral CI) | `jv` for all Integral disk skills today |
| `allowed-tools` | yes when SOP calls tools | Every catalogue tool backtick-referenced in body MUST be listed |
| `requires-actions` | yes for Integral | `[EmbeddedIntegralAction]` |
| `extends` | core + bundle overlay | `action:integral/embedded_integral_action` — composes base SOP |

**Skill vs tool in prose:** backtick `integral_*` only for MCP tools. Delegate to other skills with explicit wording (*delegate to skill `integral_entries`*), not backticks.

---

## Placement (jvagent ADR-0023)

| Skill kind | Path |
|------------|------|
| Core `integral_*` | `agent/.../embedded_integral_action/skills/integral_*/` |
| App bundle | `backend/app/packages/<slug>/skills/<key>/` |
| Workspace-authored | graph `Skill` node only (no disk file) |
| Base SOP (not discovered) | `embedded_integral_action/SKILL.md` |

---

## Integral 7-section body bar (public skills)

Every **core** and **public bundle** skill body MUST include:

1. **When to use** — intents / example utterances (expands discovery)
2. **When NOT to use — delegate**
3. **Grounding (read before write)**
4. **Procedure**
5. **Staging discipline**
6. **Forbidden patterns**
7. **Example**

Private bundle skills: warnings only. Workspace skills: warned at save.

---

## Three runtime representations

| Layer | Location |
|-------|----------|
| Disk | `SKILL.md` frontmatter + domain body |
| Manifest | `operational-model.yaml` `tools_required`, `description` (synced) |
| Editor / graph | `Skill.description`, `body_override`, `tools_required` |

`Skill.tools_required` wins at runtime. Sync after editing `allowed-tools`:

`python3 backend/scripts/sync_bundle_skill_manifests.py --write`

---

## Tooling

| Task | Command |
|------|---------|
| Compliance tests | `pytest backend/tests/test_skill_compliance.py` |
| Add `spec: jv` / fix core descriptions | `python3 backend/scripts/normalize_skill_frontmatter.py --write` |
| Audit report | `python3 backend/scripts/audit_skills.py --write-docs` |
| jvagent parse check | `jvagent skill validate <path/to/skill>` |

Implementation: [`skill_compliance.py`](../../backend/app/services/skill_compliance.py)
