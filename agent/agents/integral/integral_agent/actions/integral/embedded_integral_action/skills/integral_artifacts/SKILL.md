---
name: integral_artifacts
description: >-
  Maintains session working artifacts — upsert, get, and list harness-agnostic
  blueprints, checklists, and notes keyed on the conversation for multi-turn
  fidelity without Integral UI cards.
spec: jv
allowed-tools:
  - integral_upsert_artifact
  - integral_get_artifact
  - integral_list_artifacts
requires-actions:
  - EmbeddedIntegralAction
extends: action:integral/embedded_integral_action
tags:
  - integral
  - artifacts
  - blueprint
---

# Session artifacts

Working memory for this conversation. Not substrate writes. Not Prompt Sheet
cards. Any harness can call these tools.

## When to use

- Persist or refresh an `app_design_blueprint` (or other outline) the model must
  re-read on a later turn.
- Store a build `checklist` or freeform `note` keyed on the session.
- Recover a prior outline via get/list after an amend or interrupted build.

## When NOT to use

- Substrate creates/updates — those use propose/staging tools, not artifacts.
- Clarifying questions for the user — ask in prose or hand off to a skill that
  owns ask_user.
- Greenfield design gate recording — skill `integral_scaffold` owns the propose
  flow; this skill only holds the durable body.

## Grounding

1. Confirm the conversation session is live (artifacts require a session).
2. Prefer stable keys: `app_design_blueprint` for greenfield outlines.
3. `integral_list_artifacts` before inventing a new key when continuing work.

## Procedure

1. `integral_upsert_artifact` with `key`, `kind`, optional `title`, and `body`
   (markdown). Replaces prior body; bumps `version`.
2. `integral_get_artifact` by `key` when amending or building from a prior
   outline.
3. `integral_list_artifacts` (optional `kind`) to discover keys.

Kinds: `app_design_blueprint`, `checklist`, `note`.

## Staging discipline

Artifacts never mint staged changes. Do not claim apps/tracks exist because an
artifact was upserted. Substrate writes still go through begin_batch / commit
(or other propose tools) under their own skills.

## Forbidden patterns

- Minting Prompt Sheet cards for artifact content.
- Treating artifact upsert as a substrate write.
- Guessing artifact bodies instead of calling get after an amend turn.

## Example

```text
integral_upsert_artifact(
  key="app_design_blueprint",
  kind="app_design_blueprint",
  title="Bookstore Inventory",
  body="## Books\n- Title, Author, Stock…\n## Sales\n…"
)
# later
integral_get_artifact(key="app_design_blueprint")
```
