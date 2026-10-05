# Skill Format Standard — Agent Skills

**Status:** Canonical format for Integral Core skill files and App authoring.

**Normative source:** [Agent Skills specification](https://agentskills.io/specification).

Integral follows the standard without JV Agent extensions. Do not add `spec`,
`extends`, `requires-actions`, `always-active`, `task-lock`, top-level `tags`,
`plan-steps` or other vendor fields. Do not relocate execution extensions into
`metadata` to preserve vendor behavior. Runtime authorization and App tool
bindings belong to Integral's existing policy and manifest contracts.

## Directory and file

A skill is a directory containing `SKILL.md`, optionally accompanied by
`scripts/`, `references/`, `assets/` and other resources.

```markdown
---
name: register-asset
description: Registers an asset after collecting its required fields. Use when the user requests asset registration.
allowed-tools: integral_invoke_app_operation
---

# Register asset

Collect the required fields and invoke the authorized App operation.
```

## Frontmatter

| Field | Requirement |
|---|---|
| `name` | Required, 1–64 lowercase letters/numbers/hyphens; no leading/trailing or consecutive hyphens; matches parent directory |
| `description` | Required non-empty string, at most 1,024 characters; describes purpose and when to use |
| `license` | Optional string naming the license or bundled license reference |
| `compatibility` | Optional string, 1–500 characters, describing environment requirements |
| `metadata` | Optional mapping of string keys to string values |
| `allowed-tools` | Optional space-separated string; standard experimental field, interpreted according to the runtime's supported tool policy |

No snake_case exception applies to disk skill names. Tool IDs and opaque App
operation/graph keys are separate identifiers: existing `integral_*` tool names
and App operation keys remain valid. App manifests reference the actual
hyphenated skill directory through `prompt_template`.

## Instructions and resources

The Markdown body has no required heading structure. Procedures, grounding,
staging guidance, examples and edge cases are useful authoring guidance, not
extra format requirements. Preserve the substance of existing instructions.

Use relative references from the skill root. Instructions are loaded on demand;
resource reads and script execution use the active harness's supported facilities
and Integral's authorization and isolation controls. A skill document does not
register tools, grant permissions, declare Action dependencies or inherit another
skill. Shared runtime instructions belong to the host rather than vendor
frontmatter.

## Discovery, manifests and runtime

`name` and `description` support progressive discovery. The graph/manifest
controls App ownership, access, privacy and available operations. Manifest skill
entries must point to existing files and retain matching discovery descriptions
and tool bindings. Runtime adapters parse standard fields without JV inheritance.
There is no requirement to use a particular model or harness to author a skill.

## Tooling

- `backend/scripts/normalize_skill_frontmatter.py` removes non-standard fields and converts list-form tools; dry-run by default.
- `backend/app/services/skill_compliance.py` enforces standard field types, names and length limits. Body section scores are informational only.
- `backend/scripts/sync_bundle_skill_manifests.py` keeps package manifest references aligned.
- The standard's reference validator is `skills-ref validate <skill-directory>`.

The retired action base SOP is retained as
`agent/.../embedded_integral_action/references/standard-integral-tool-procedure.md`;
it is a reference document, not a skill with an invalid parent/name or inheritance
contract.
