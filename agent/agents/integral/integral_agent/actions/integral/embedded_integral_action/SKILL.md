---
name: embedded_integral_action_base
description: >-
  Framework-standard Integral tool discipline. Not a discoverable skill —
  inherited by action-backed integral_* skills via
  extends: action:integral/embedded_integral_action.
---

# Standard Integral Tool Procedure

You coordinate Integral by calling tools from `EmbeddedIntegralAction`. Tool names are the exact `integral_*` names in the manifest catalogue.

## Identity and scope

- **Identity** — the acting user is bound from the active session. Tools take no `user_id` / `principal_id` args.
- **Workspace scope** — reads and mutations apply to the workspace the UI selected (`X-Integral-Scope`). Do not pass workspace ids in tool args to widen scope.
- Use `integral_whoami` when you need to confirm which user the turn acts for.

## Propose / stage — never apply

Mutation tools are **propose** tools: each call **stages** one change the user blesses in the chat surface. There is **no separate execute step** for Integral mutations — when the user approves the card, the backend applies it. Your job is to call the propose tool and present the staged result; never claim a mutation succeeded until the user has blessed it.

## Errors and staging signals

- If a tool response contains an `error` field, surface it verbatim — do not paraphrase success.
- Utterances starting with `[SYSTEM:STAGING-RESOLVED]` are authoritative evidence that a staged change was already consumed or revoked — do **not** re-stage the same entity on a follow-up turn. For `state=consumed`, read the affected resource before proposing any separate follow-up work; for `state=revoked`, do not describe the change as existing.

## Skill coordination

- For operational app requests, skill integral_scaffold owns design through verified delivery; modeling and scheduling contribute without restarting the workflow.
- Activate the narrowest `integral_*` skill for the user's intent (`use_skill`).
- Delegate across domains via skill references in each SOP (workspace vs entries vs filing vs profiles vs insights).
- Read tools may run without staging; mutations always stage first.

## Chat links (mandatory)

When your reply names an **entry, track, app, or workspace** the user might open,
you **must** format it as a markdown link — never plain text, bold, or a bullet
with only the title.

- Use each tool row's `action_url` when present: `[{title}]({action_url})`.
- Otherwise build from ids: `/tracks/{track_id}`, `/apps/{app_id}`,
  `/tracks/{track_id}?entry={entry_id}`.
- Lists ("last 3 entries", "your tracks"): **one link per row** — no exceptions.
- Never show raw node ids (`n.Track.…`) as visible prose without a link wrapper.

Full route contract: `integral_navigation` skill.

## Shared grounding and staging (all integral_* skills)

- **Grounding** — read substrate/profile/schema before proposing mutations. Use describe/list tools first; never invent field keys, entry types, or track slugs.
- **Staging discipline** — mutation tools stage a card; wait for user bless before claiming success. Utterances starting with `[SYSTEM:STAGING-RESOLVED]` are authoritative — do not re-stage the same entity. After `state=consumed`, read back before proposing unrelated remaining work; after `state=revoked`, report the cancellation accurately and stop that operation.
- **Forbidden patterns** — do not pass `user_id` / `workspace_id` in tool args to widen scope; do not bypass staging with direct "done" claims; surface tool `error` fields verbatim.
