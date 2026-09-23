# App Bundle Authoring — hands-on guide

**Status:** Contributor reference for first-party and trusted-partner App bundles under `backend/app/packages/{slug}/`.

**Related docs:**

- [app-bundles-v1.md](./app-bundles-v1.md) — architecture, manifest shape, security boundary
- [skill-format-standard.md](./skill-format-standard.md) — `SKILL.md` contract and compliance CI
- [operational-model-authoring-and-library.md](./operational-model-authoring-and-library.md) — library merge and catalog workflow

This guide consolidates the scaffold template, trust-tier rules, and hook/tool wiring so authors can start from a working tree without re-reading the full architecture doc.

---

## Quick start — scaffold a bundle

```bash
python3 backend/scripts/scaffold_bundle.py my-app
python3 backend/scripts/scaffold_bundle.py my-app --trusted   # adds tools/ + sample hook
```

Creates:

```
backend/app/packages/my-app/
├── operational-model.yaml
├── skills/example_skill/SKILL.md
└── agents/example.yaml
```

With `--trusted`, also:

```
backend/app/packages/my_app/tools/   # underscore package for Python imports
├── __init__.py
└── example.py
```

**Invariants enforced at CI:**

| Check | Script |
|-------|--------|
| Manifest ↔ bindings reconciliation | `.ci/tool_manifest_check.sh` |
| Skill compliance (7/7, descriptions) | `.ci/skill_compliance_check.sh` |
| Bundle tools must not import substrate | `.ci/bundle_facade_check.sh` |
| Bundle invariants (I-BUNDLE-01..05) | `backend/tests/test_app_bundles_invariants.py` |

Directory name **must** equal `package.slug` (I-BUNDLE-04). Use hyphens in the slug (`my-app`); Python package uses underscores (`my_app`).

---

## `operational-model.yaml` skeleton (v3)

The scaffold emits this shape — replace placeholders before shipping:

```yaml
integral_operational_model_version: 3
scope: app
package:
  name: My App
  slug: my-app
  version: 0.1.0
  description: One paragraph describing the App for catalog and install UX.
  tags: []
  # trust_tier: trusted   # required when app.tools[] is non-empty

app:
  description: App-level summary shown in workspace UI.
  tracks: []              # see app-bundles-v1.md §4 for track declarations
  skills:
    - example_skill       # bare key → skills/example_skill/SKILL.md
  agents:
    - key: example_agent
      name: Example Agent
      persona_ref: agents/example.yaml
      skills:
        - example_skill
      scope: app
      staging: optional
```

After editing `SKILL.md` `allowed-tools`, sync manifest `tools_required`:

```bash
python3 backend/scripts/sync_bundle_skill_manifests.py --write
```

Regenerate the compliance audit report:

```bash
python3 backend/scripts/audit_skills.py --write-docs
```

---

## Declarative skill template (`SKILL.md`)

Every bundle skill ships with the **7 canonical sections**. Public bundle skills (`private: false`) must pass full compliance — missing sections are **errors**, not warnings.

```markdown
---
name: example_skill
description: >-
  Example App-bundled skill — third-person discovery (what + when to route here).
spec: jv
extends: action:integral/embedded_integral_action
requires-actions:
  - EmbeddedIntegralAction
allowed-tools:
  - integral_query_entries
tags: [example]
---

## When to use

Describe user intents that route here.

## When NOT to use — delegate

Adjacent skills that own neighboring intents.

## Grounding (read before write)

Read tools to call before mutations.

## Procedure

Numbered tool sequence.

## Staging discipline

Propose/bless and batching rules.

## Forbidden patterns

Shortcuts to reject.

## Example

One end-to-end walkthrough.
```

| Frontmatter key | Purpose |
|-----------------|---------|
| `name` | Must match `skills/{key}/` directory |
| `description` | One-line summary; must match `operational-model.yaml` skill entry after sync |
| `extends` | Prepends embedded Integral base SOP at compose time |
| `requires-actions` | Gates skill when `EmbeddedIntegralAction` is disabled |
| `allowed-tools` | MCP tools referenced in the body; synced to manifest `tools_required` |

Authors write **domain workflow only** in the body — inherited base SOP comes from `extends`.

Style anchors for rewrites: `integral_filing`, `integral_model`, `integral_scaffold` under the embedded integral action skills tree.

---

## Agent persona (`agents/*.yaml`)

```yaml
name: Example Agent
description: Example resident agent for this App.

engine:
  model: gpt-4o-mini
persona:
  model: gpt-4o-mini
  system_prompt: |
    You are the example agent for this App.
```

Bind in `operational-model.yaml` under `app.agents[]` with `persona_ref`, `skills`, `scope`, and optional `staging`.

---

## Trusted bundle tools and hooks

Use when the App needs **Python side effects** on substrate lifecycle events (precompute, create, connector dedup). Declarative skills alone cannot run arbitrary Python.

### Trust gate

`app.tools[]` requires `package.trust_tier: trusted` or `audited`. CI fails trusted bundles that declare tools without the tier (`test_manifest_tools_hooks.py`, `test_app_bundles_invariants.py`).

Community-tier bundles are **declarative-only** — skills + MCP tools, no `tools/` package.

### Tool handler shape

```python
"""Example bundle tool — async handler for hook dispatch."""

from __future__ import annotations

from typing import Any, Dict

from app.services.hooks.registry import ToolContext


async def run(payload: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    _ = ctx
    return {"ok": True, "echo": payload}
```

**Facade rule:** bundle tools under `profiles/*/tools/*.py` must **not** import `app.services` or `app.models`. Reach substrate only through `ToolContext` (enforced by `.ci/bundle_facade_check.sh`).

`handler_ref` in manifest uses the short form `tools.example:run`; install normalizes to `app.packages.<slug_underscore>.tools.example`.

### Hook wiring

```yaml
package:
  trust_tier: trusted

app:
  tools:
    - key: example_tool
      handler_ref: tools.example:run
      parameters_schema:
        type: object
      output_schema:
        type: object

  hooks:
    - point: entry.precompute
      key: example_precompute
      mode: tool
      tool: example_tool
```

**Frozen hook points:** `entry.transform`, `entry.public_share`, `entry.precompute`, `entry.create`, `entry.update`, `connector.dedup`, `connector.auto_link`.

Reference bundles: `hr_app`, `sales`.

### When to use tools vs declarative skills

| Need | Mechanism |
|------|-----------|
| Agent turn workflow (read → propose → bless) | Declarative `SKILL.md` + `integral_*` MCP tools |
| Automatic side effect on entry save / connector event | `app.tools[]` + `app.hooks[]` |
| Future dedicated agent-invoked Python handler | `kind: custom` skill (registry only in v1 — prefer tools today) |

---

## Authoring checklist

1. `python3 backend/scripts/scaffold_bundle.py <slug> [--trusted]`
2. Flesh out `operational-model.yaml` — tracks, entry types, relations, settings (see [app-bundles-v1.md §4](./app-bundles-v1.md))
3. Author each `skills/*/SKILL.md` to 7/7 compliance
4. Run `sync_bundle_skill_manifests.py --write` after tool list changes
5. Run `pytest backend/tests/test_skill_compliance.py` and `audit_skills.py --write-docs`
6. If trusted: implement `tools/*.py`, declare hooks, verify facade check passes
7. Merge into a clean workspace; exercise each agent skill once

---

## Testing and guards

```bash
# Skill + manifest compliance
pytest backend/tests/test_skill_compliance.py
pytest backend/tests/test_tool_manifest_reconciliation.py
pytest backend/tests/test_manifest_tools_hooks.py
pytest backend/tests/test_app_bundles_invariants.py

# Pre-commit (from repo root, hooks installed)
.ci/skill_compliance_check.sh
.ci/tool_manifest_check.sh
.ci/bundle_facade_check.sh
```

---

## See also

- [app-bundles-v1.md §5.3](./app-bundles-v1.md#53-three-mechanisms--pick-one) — three mechanisms table
- [app-bundles-v1.md §12](./app-bundles-v1.md#12-authoring-patterns) — declarative-only vs consulting deliverable patterns
- [workspace-agent-profile.md](./workspace-agent-profile.md) — runtime overlay and namespacing (`{app_slug}__{skill_key}`)
- [INVARIANTS.md § I-BUNDLE](../INVARIANTS.md) — bundle substrate invariants
